"""成本诊断单元测试。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fund_advisor.diagnostics import cost
from fund_advisor.models import FundType

from ..conftest import make_holding, make_portfolio


def test_c_class_tier_imminent_high_signal(default_settings_with_fees):
    """C 类持有 28 天，距满 30 天还有 2 天 → HIGH 信号。"""
    h = make_holding(
        code="017513",
        name="广发北证50成份指数C",
        fund_type=FundType.EQUITY,
        shares="1000",
        cost_price="1.0",
        purchase_date=date.today() - timedelta(days=28),
    )
    p = make_portfolio(holdings=[h])
    result = cost.diagnose(p, default_settings_with_fees)

    item = result.items[0]
    assert item.is_c_class is True
    assert item.held_days == 28
    assert item.next_tier_days_away == 2
    assert item.current_redemption_fee_rate == Decimal("0.005")

    high_signals = [s for s in result.signals if s.severity.value == "high"]
    assert any(s.code == "REDEMPTION_FEE_TIER_IMMINENT" for s in high_signals)


def test_c_class_over_30_days_fully_optimal(default_settings_with_fees):
    """C 类持有 100 天 → 0 费率，next_tier_days_away=None，无信号。"""
    h = make_holding(
        code="017513", name="广发北证50成份指数C",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
        purchase_date=date.today() - timedelta(days=100),
    )
    p = make_portfolio(holdings=[h])
    result = cost.diagnose(p, default_settings_with_fees)
    item = result.items[0]
    assert item.current_redemption_fee_rate == Decimal("0")
    assert item.next_tier_days_away is None
    assert not any(
        s.code in ("REDEMPTION_FEE_TIER_IMMINENT", "REDEMPTION_FEE_TIER_NEAR")
        for s in result.signals
    )


def test_non_c_class_no_redemption_fields(default_settings_with_fees):
    """非 C 类 (A 类 / 普通) 不判定赎回费。"""
    h = make_holding(
        code="110020", name="易方达沪深300ETF联接A",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
        purchase_date=date.today() - timedelta(days=5),
    )
    p = make_portfolio(holdings=[h])
    result = cost.diagnose(p, default_settings_with_fees)
    item = result.items[0]
    assert item.is_c_class is False
    assert item.current_redemption_fee_rate is None
    assert item.next_tier_days_away is None


def test_annualized_return_positive(default_settings_with_fees):
    """市值涨 10%，持有 365 天 → 年化 ≈ 10%。"""
    h = make_holding(
        code="000001", name="测试A",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
        purchase_date=date.today() - timedelta(days=365),
    )
    h.latest_nav = Decimal("1.10")
    p = make_portfolio(holdings=[h])
    result = cost.diagnose(p, default_settings_with_fees)
    ann = result.items[0].annualized_return
    assert ann is not None
    assert Decimal("0.09") <= ann <= Decimal("0.11")


def test_zero_held_days_annualized_none(default_settings_with_fees):
    """当日建仓 held_days=0 → annualized_return=None，不崩。"""
    h = make_holding(
        code="000001", name="测试",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
        purchase_date=date.today(),
    )
    p = make_portfolio(holdings=[h])
    result = cost.diagnose(p, default_settings_with_fees)
    assert result.items[0].annualized_return is None
    assert result.items[0].held_days == 0
