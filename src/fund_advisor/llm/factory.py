"""LLM 客户端工厂：UI 与 Scheduler 共用的构造逻辑。"""

from __future__ import annotations

import os

from loguru import logger

from .client import DeepSeekClient


def build_deepseek_client() -> DeepSeekClient | None:
    """根据环境变量 DEEPSEEK_API_KEY 构造客户端。

    未配置 key 或初始化失败时返回 None，调用方自行降级到规则兜底。
    """
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        logger.info("DEEPSEEK_API_KEY 未设置，跳过 LLM 客户端构造")
        return None
    try:
        return DeepSeekClient(api_key=key)
    except Exception as e:  # noqa: BLE001
        logger.warning("DeepSeek 客户端初始化失败：{}", e)
        return None
