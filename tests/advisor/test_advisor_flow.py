"""advisor.run_diagnosis 端到端测试（mock akshare，不依赖 LLM）。"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal
from unittest.mock import patch

from fund_advisor.advisor import advisor as advisor_mod
from fund_advisor.models import Action, FundType

from ..conftest import make_holding, make_portfolio


def _fake_index_valuation(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "as_of": _date(2026, 4, 20),
        "pe": Decimal("12.0"),
        "pb": Decimal("1.3"),
        "pe_percentile": Decimal("0.50"),
    }


def _fake_enrich(holding, **kwargs):
    """给 holding 直接填好 name/fund_type/latest_nav，避免真联网。"""
    if not holding.name:
        holding.name = f"mocked-{holding.code}"
    if holding.fund_type is None:
        holding.fund_type = FundType.EQUITY
    holding.latest_nav = Decimal("1.10")
    from datetime import date
    holding.latest_nav_date = date.today()
    return {"code": holding.code, "mocked": True}


def test_fallback_when_no_llm(default_settings):
    """无 LLM 客户端时走纯规则兜底，依然产出今日建议卡片。"""
    h = make_holding(code="017513", name="广发北证50", shares="2000", cost_price="1.25")
    p = make_portfolio(holdings=[h])

    with patch(
        "fund_advisor.advisor.advisor.enrich_holding_inplace",
        side_effect=_fake_enrich,
    ), patch(
        "fund_advisor.diagnostics.valuation.get_index_valuation",
        side_effect=_fake_index_valuation,
    ):
        report = advisor_mod.run_diagnosis(
            p, default_settings, llm_client=None, resolve=True
        )

    assert report.llm_synthesis is not None
    assert report.llm_synthesis.today_headline  # 有一段话
    # 纯规则兜底里，过度集中的基金应得到 PAUSE_DCA 建议
    over_items = [
        it for it in report.concentration_diagnosis.items if it.over_limit
    ]
    if over_items:
        assert any(a.action == Action.PAUSE_DCA for a in report.action_items)


def test_resolve_populates_market_value(default_settings):
    """补齐后 market_value 应基于 latest_nav 而非 cost_price。"""
    h = make_holding(shares="1000", cost_price="1.00", name="原名")
    p = make_portfolio(holdings=[h])

    with patch(
        "fund_advisor.advisor.advisor.enrich_holding_inplace",
        side_effect=_fake_enrich,
    ):
        advisor_mod.resolve_portfolio(p)

    assert p.holdings[0].latest_nav == Decimal("1.10")
    # market_value = 1000 * 1.10 = 1100
    assert p.holdings[0].market_value == Decimal("1100.00")
