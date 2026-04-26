"""LLM synthesizer 容错单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fund_advisor.llm.synthesizer import analyze_candidate
from fund_advisor.models import CandidateRequest

from ..conftest import make_portfolio


@dataclass
class _FakeUsageRecord:
    model: str = "deepseek-chat"
    prompt_tokens: int = 0
    completion_tokens: int = 0


class _FakeClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def chat_json(self, **kwargs):
        return self.payload, _FakeUsageRecord()


def test_analyze_candidate_parses_string_false_as_false():
    portfolio = make_portfolio()
    req = CandidateRequest(
        code="007889",
        intended_amount_rmb=Decimal("2000"),
        intended_mode="DCA",
    )
    client = _FakeClient(
        {
            "headline": "先别买",
            "should_buy": "false",
            "suggested_action": "SKIP",
            "suggested_amount_rmb": None,
            "reasoning": "现金优先",
            "alternative_view": "若只是小仓位试错，也可观察后分批进。",
            "risk_warnings": [],
        }
    )

    result = analyze_candidate(
        portfolio,
        req,
        basic_info={
            "name": "测试基金",
            "fund_type_raw": "股票型",
            "fund_type_normalized": "equity_fund",
        },
        nav_info={"nav": Decimal("1.23"), "nav_date": date(2026, 4, 25)},
        client=client,
        emergency_months=Decimal("3.75"),
        mode="light",
    )

    assert result.should_buy is False
