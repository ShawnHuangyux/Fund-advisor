"""LLM 账单持久化 (data/usage_db.py) 单元测试。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from fund_advisor.data import usage_db
from fund_advisor.models.settings import LLMSettings


@dataclass
class _FakeRecord:
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0


@pytest.fixture
def _tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "usage.db"
    monkeypatch.setenv("FUND_ADVISOR_USAGE_DB", str(db_file))
    return db_file


def test_compute_cost_reasoner():
    """reasoner: input 0.004, output 0.016 per 1K，reasoning 计入 output。

    1000 in + 500 out + 500 reasoning
      = 0.004 * 1 + 0.016 * 1.0 = 0.020
    """
    cost = usage_db.compute_cost("deepseek-reasoner", 1000, 500, 500)
    assert cost == Decimal("0.0200")


def test_compute_cost_chat():
    cost = usage_db.compute_cost("deepseek-chat", 2000, 1000, 0)
    # 0.001*2 + 0.002*1 = 0.004
    assert cost == Decimal("0.0040")


def test_compute_cost_unknown_model_zero():
    assert usage_db.compute_cost("unknown-model", 10000, 10000, 0) == Decimal("0.0000")


def test_record_usage_and_current_month(_tmp_db):
    rec = _FakeRecord("deepseek-reasoner", 1000, 500, 500)
    cost = usage_db.record_usage(rec, kind="diagnosis")
    assert cost == Decimal("0.0200")

    total = usage_db.current_month_cost()
    assert total == Decimal("0.0200")

    # 再写一条
    usage_db.record_usage(
        _FakeRecord("deepseek-chat", 2000, 1000, 0), kind="candidate"
    )
    total2 = usage_db.current_month_cost()
    assert total2 == Decimal("0.0240")


def test_current_month_cost_excludes_prior_months(_tmp_db, monkeypatch):
    # 直接往库里插一条"上月"的记录
    conn = sqlite3.connect(str(_tmp_db))
    conn.executescript(usage_db._SCHEMA)
    last_month = (datetime.now().replace(day=1) - timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    conn.execute(
        "INSERT INTO llm_usage(ts, provider, model, input_tokens, output_tokens, "
        "reasoning_tokens, cost_rmb, kind) VALUES(?,?,?,?,?,?,?,?)",
        (last_month, "deepseek", "deepseek-reasoner", 1000, 1000, 0, 90.0, "diagnosis"),
    )
    conn.commit()
    conn.close()

    # 当月成本不应该把上月 90 元算进来
    assert usage_db.current_month_cost() == Decimal("0.0000")

    # 当月再写一条小额
    usage_db.record_usage(
        _FakeRecord("deepseek-chat", 1000, 500, 0), kind="diagnosis"
    )
    assert usage_db.current_month_cost() == Decimal("0.0020")


def test_budget_state_transitions(_tmp_db):
    settings = LLMSettings(
        provider="deepseek",
        mode="deep",
        monthly_budget_warn=Decimal("80"),
        monthly_budget_block=Decimal("100"),
    )

    # 没记录 → ok
    assert usage_db.budget_state(settings) == "ok"

    # 插入 50 元 → ok
    conn = sqlite3.connect(str(_tmp_db))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO llm_usage(ts, provider, model, input_tokens, output_tokens, "
        "reasoning_tokens, cost_rmb, kind) VALUES(?,?,?,?,?,?,?,?)",
        (now, "deepseek", "deepseek-reasoner", 0, 0, 0, 50.0, "diagnosis"),
    )
    conn.commit()
    conn.close()
    assert usage_db.budget_state(settings) == "ok"

    # 再加 35 元 → warn
    conn = sqlite3.connect(str(_tmp_db))
    conn.execute(
        "INSERT INTO llm_usage(ts, provider, model, input_tokens, output_tokens, "
        "reasoning_tokens, cost_rmb, kind) VALUES(?,?,?,?,?,?,?,?)",
        (now, "deepseek", "deepseek-reasoner", 0, 0, 0, 35.0, "diagnosis"),
    )
    conn.commit()
    conn.close()
    assert usage_db.budget_state(settings) == "warn"

    # 再加 20 元（累计 105）→ block
    conn = sqlite3.connect(str(_tmp_db))
    conn.execute(
        "INSERT INTO llm_usage(ts, provider, model, input_tokens, output_tokens, "
        "reasoning_tokens, cost_rmb, kind) VALUES(?,?,?,?,?,?,?,?)",
        (now, "deepseek", "deepseek-reasoner", 0, 0, 0, 20.0, "diagnosis"),
    )
    conn.commit()
    conn.close()
    assert usage_db.budget_state(settings) == "block"


def test_recent_usage_ordering(_tmp_db):
    usage_db.record_usage(
        _FakeRecord("deepseek-chat", 100, 50, 0), kind="diagnosis"
    )
    usage_db.record_usage(
        _FakeRecord("deepseek-reasoner", 200, 100, 100), kind="candidate"
    )
    rows = usage_db.recent_usage(limit=10)
    assert len(rows) == 2
    # 按 id DESC
    assert rows[0]["kind"] == "candidate"
    assert rows[1]["kind"] == "diagnosis"
    assert rows[0]["model"] == "deepseek-reasoner"
