"""LLM 月度账单持久化（阶段 4）。

- SQLite 表 ``llm_usage`` 存每次调用的 token / 成本
- 每月聚合成本用于预算门槛（¥80 warn / ¥100 block 深度推理）
- 路径默认 ``data/usage.db``，可通过环境变量 ``FUND_ADVISOR_USAGE_DB`` 覆盖，便于测试
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal

from loguru import logger

_ENV_VAR = "FUND_ADVISOR_USAGE_DB"
DEFAULT_DB_PATH = Path("data/usage.db")

# 定价表：(input ¥/1K tokens, output ¥/1K tokens)
# reasoning tokens 计入 output（与 DeepSeek 官方口径一致）
PRICE_TABLE: dict[str, tuple[Decimal, Decimal]] = {
    "deepseek-chat": (Decimal("0.001"), Decimal("0.002")),
    "deepseek-reasoner": (Decimal("0.004"), Decimal("0.016")),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cost_rmb REAL NOT NULL,
    kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts);
"""


def _get_db_path() -> Path:
    env = os.getenv(_ENV_VAR)
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def _connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or _get_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    return conn


def compute_cost(
    model: str, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0
) -> Decimal:
    """根据模型 + token 数算成本（人民币）。未知模型返回 0。"""
    price_in, price_out = PRICE_TABLE.get(model, (Decimal("0"), Decimal("0")))
    total_out = Decimal(output_tokens) + Decimal(reasoning_tokens)
    cost = (price_in * Decimal(input_tokens) + price_out * total_out) / Decimal("1000")
    return cost.quantize(Decimal("0.0001"))


def record_usage(
    record: Any,
    kind: str,
    *,
    db_path: Path | None = None,
    provider: str = "deepseek",
) -> Decimal:
    """把一条 UsageRecord 写入数据库并返回本次成本。

    ``record`` 使用鸭子类型，需含 ``model`` / ``prompt_tokens`` /
    ``completion_tokens`` / ``reasoning_tokens`` 四个属性（DeepSeekClient
    里的 ``UsageRecord`` 即满足）。
    """
    model = getattr(record, "model", "")
    in_tok = int(getattr(record, "prompt_tokens", 0) or 0)
    out_tok = int(getattr(record, "completion_tokens", 0) or 0)
    rea_tok = int(getattr(record, "reasoning_tokens", 0) or 0)
    cost = compute_cost(model, in_tok, out_tok, rea_tok)
    ts = datetime.now().astimezone().isoformat(timespec="seconds")

    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_usage(ts, provider, model, input_tokens, output_tokens, "
            "reasoning_tokens, cost_rmb, kind) VALUES(?,?,?,?,?,?,?,?)",
            (ts, provider, model, in_tok, out_tok, rea_tok, float(cost), kind),
        )
    return cost


def current_month_cost(*, db_path: Path | None = None) -> Decimal:
    """返回本月（本地时区）累计 LLM 成本。"""
    now = datetime.now().astimezone()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cutoff = month_start.isoformat(timespec="seconds")
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "SELECT COALESCE(SUM(cost_rmb), 0) FROM llm_usage WHERE ts >= ?",
                (cutoff,),
            )
            total = cur.fetchone()[0]
    except sqlite3.DatabaseError as e:
        logger.warning("读取 usage.db 失败：{}", e)
        return Decimal("0")
    return Decimal(str(total)).quantize(Decimal("0.0001"))


def budget_state(llm_settings, *, db_path: Path | None = None) -> Literal["ok", "warn", "block"]:
    """比对本月成本与 llm_settings.monthly_budget_warn/block 阈值。"""
    cost = current_month_cost(db_path=db_path)
    warn = Decimal(str(llm_settings.monthly_budget_warn))
    block = Decimal(str(llm_settings.monthly_budget_block))
    if cost >= block:
        return "block"
    if cost >= warn:
        return "warn"
    return "ok"


def recent_usage(
    *, limit: int = 30, db_path: Path | None = None
) -> list[dict[str, Any]]:
    """返回最近 N 条记录，按时间倒序。"""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "SELECT ts, provider, model, input_tokens, output_tokens, "
            "reasoning_tokens, cost_rmb, kind FROM llm_usage "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


__all__: Iterable[str] = (
    "PRICE_TABLE",
    "budget_state",
    "compute_cost",
    "current_month_cost",
    "recent_usage",
    "record_usage",
)
