"""资金效率诊断单元测试。"""

from __future__ import annotations

from decimal import Decimal

from fund_advisor.diagnostics import capital

from ..conftest import make_holding, make_portfolio


def test_emergency_reserve_low_triggers_high_signal(default_settings):
    """应急金 < 3 个月 (8000 × 3 = 24000) → 高优信号。"""
    p = make_portfolio(
        cash="50000",
        principal_total="200000",
        emergency_reserve="10000",  # 10000 / 8000 = 1.25 月
    )
    result = capital.diagnose(p, default_settings)

    codes = [s.code for s in result.signals]
    assert "EMERGENCY_RESERVE_LOW" in codes
    sig = next(s for s in result.signals if s.code == "EMERGENCY_RESERVE_LOW")
    assert sig.severity.value == "high"
    assert result.emergency_adequacy_months == Decimal("1.25")


def test_capital_underutilized_info_when_below_50pct(default_settings):
    """利用率 < 50% 应触发 CAPITAL_UNDERUTILIZED info。"""
    # 投入 50000，可投 (200000 - 30000) = 170000 → 利用率 ≈ 29.4%
    h = make_holding(shares="50000", cost_price="1.00")
    p = make_portfolio(
        cash="50000",
        principal_total="200000",
        emergency_reserve="30000",
        holdings=[h],
    )
    result = capital.diagnose(p, default_settings)

    assert result.capital_utilization < Decimal("0.5")
    assert any(s.code == "CAPITAL_UNDERUTILIZED" for s in result.signals)
    # 应急金 30000 / 8000 = 3.75 月，不触发 EMERGENCY_RESERVE_LOW
    assert not any(s.code == "EMERGENCY_RESERVE_LOW" for s in result.signals)


def test_dca_budget_nonnegative_when_overinvested(default_settings):
    """已投入超出可投本金时，建议月度定投应为 0 而不是负数。"""
    # 投入 200000 > 可投 170000
    h = make_holding(shares="200000", cost_price="1.00")
    p = make_portfolio(
        cash="10000",
        principal_total="200000",
        emergency_reserve="30000",
        holdings=[h],
    )
    result = capital.diagnose(p, default_settings)

    assert result.dca_budget_per_month == Decimal("0")


def test_zero_investable_principal_safe(default_settings):
    """principal_total == emergency_reserve 时不应除零崩溃。"""
    p = make_portfolio(
        cash="10000", principal_total="30000", emergency_reserve="30000"
    )
    result = capital.diagnose(p, default_settings)
    assert result.investable_principal == Decimal("0.00")
    assert result.capital_utilization == Decimal("0")
