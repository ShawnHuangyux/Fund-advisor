"""DeepSeek 客户端（OpenAI 兼容 SDK）。

只实现最小需要：JSON 强制输出 + 重试 + 基本 token 计费记录（内存态；
持久化到 SQLite 留到后续阶段）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

try:
    from openai import APIConnectionError, APIError, OpenAI, RateLimitError
except ImportError as e:  # pragma: no cover
    raise ImportError("openai 未安装；请先 uv sync") from e


DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
MODEL_LIGHT = "deepseek-chat"
MODEL_DEEP = "deepseek-reasoner"


@dataclass
class UsageRecord:
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0
    provider: str = "deepseek"


@dataclass
class DeepSeekClient:
    """DeepSeek 聊天/推理客户端。"""

    api_key: str | None = None
    base_url: str = DEEPSEEK_BASE_URL
    usage_log: list[UsageRecord] = field(default_factory=list)

    def __post_init__(self):
        self.api_key = self.api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "未找到 DEEPSEEK_API_KEY；请在 .env 中配置或显式传入。"
            )
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=2, max=20),
        retry=retry_if_exception_type(
            (APIConnectionError, RateLimitError, APIError)
        ),
    )
    def chat_json(
        self,
        *,
        system: str,
        user: str,
        mode: str = "deep",
        max_tokens: int | None = 2048,
        temperature: float = 0.4,
        kind: str = "diagnosis",
    ) -> tuple[dict[str, Any], UsageRecord]:
        """调用 DeepSeek 并强制 JSON 输出。

        mode="deep" 用 reasoner（规格里要求的默认），"light" 用 chat。
        kind: "diagnosis" 组合诊断 / "candidate" 候选分析，用于账单分类。
        """
        model = MODEL_DEEP if mode == "deep" else MODEL_LIGHT
        logger.info("DeepSeek chat_json model={} temp={}", model, temperature)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        # reasoner 目前不支持 response_format；chat 支持。
        if model == MODEL_LIGHT:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)

        content = resp.choices[0].message.content or ""
        parsed = _extract_json(content)
        if parsed is None:
            raise ValueError(f"LLM 返回的不是合法 JSON：{content[:500]}")

        usage = resp.usage
        record = UsageRecord(
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            reasoning_tokens=getattr(
                getattr(usage, "completion_tokens_details", None),
                "reasoning_tokens",
                0,
            ) or 0,
        )
        self.usage_log.append(record)
        logger.info(
            "DeepSeek 返回：input={} output={} reasoning={}",
            record.prompt_tokens, record.completion_tokens, record.reasoning_tokens,
        )
        # 写入 SQLite 账单；失败只记日志不影响主流程
        try:
            from ..data.usage_db import record_usage

            cost = record_usage(record, kind=kind, provider="deepseek")
            logger.info("本次成本 ¥{:.4f}（kind={}）", float(cost), kind)
        except Exception as e:  # noqa: BLE001
            logger.warning("写入 LLM 账单失败：{}", e)
        return parsed, record


def _extract_json(text: str) -> dict[str, Any] | None:
    """容错提取 JSON：优先直接 json.loads，再退到从 ``{``/``}`` 截取。"""
    text = text.strip()
    if text.startswith("```"):
        # 剥掉 markdown code fence
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    # 启发式：截取第一个 { 到最后一个 }
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i : j + 1])
        except Exception:  # noqa: BLE001
            return None
    return None
