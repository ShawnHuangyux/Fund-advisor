"""仓位诊断单元测试。"""

from __future__ import annotations

from decimal import Decimal

from fund_advisor.diagnostics import position
from fund_advisor.models import FundType

from ..conftest import make_holding, make_portfolio


def test_equity_overallocated_triggers_warn(default_settings):
    """股基实际占比显著高于等效目标 → OVER_ALLOCATED_EQUITY。"""
    # invested = 60000 (equity), cash = 10000, total = 70000
    # invested/total = 85.7%，等效目标股基 = 0.5 * 0.857 ≈ 42.9%
    # 实际股基 = 60000/70000 ≈ 85.7% → 偏离 ≈ +42.8% > 5%
    eq = make_holding(
        code="110020",
        name="易方达沪深300",
        fund_type=FundType.EQUITY,
        shares="60000",
        cost_price="1.00",
    )
    p = make_portfolio(
        cash="10000", principal_total="200000",
        emergency_reserve="20000", holdings=[eq],
    )
    result = position.diagnose(p, default_settings)
    codes = [s.code for s in result.signals]
    assert "OVER_ALLOCATED_EQUITY_FUND" in codes


def test_balanced_portfolio_no_signal(default_settings):
    """目标 50/30/20，实际近似命中 → 无信号。"""
    # cash 20000, 股基 50000, 债基 30000 → invested = 80000, total = 100000
    # invested/total = 80% → 股基目标等效 0.5*0.8=40%, 实际股基 50%，偏离 +10% > 5%
    # 这里反而会超配股基，我们故意安排正好：
    # 让 invested/total=1.0 就对齐 target；把 cash 设成 0 就行。
    eq = make_holding(
        code="000001", name="股基",
        fund_type=FundType.EQUITY, shares="50000", cost_price="1.0",
    )
    bd = make_holding(
        code="000002", name="债基",
        fund_type=FundType.BOND, shares="30000", cost_price="1.0",
    )
    mm = make_holding(
        code="000003", name="货基",
        fund_type=FundType.MONEY, shares="20000", cost_price="1.0",
    )
    p = make_portfolio(
        cash="0", principal_total="150000",
        emergency_reserve="20000", holdings=[eq, bd, mm],
    )
    result = position.diagnose(p, default_settings)
    assert result.signals == []


def test_under_allocated_info_when_cash_abundant(default_settings):
    """股基欠配且 cash >= emergency → UNDER_ALLOCATED_EQUITY_FUND info。"""
    # 没有股基，全现金 + 债基
    bd = make_holding(
        code="000914", name="中加纯债",
        fund_type=FundType.BOND, shares="30000", cost_price="1.0",
    )
    p = make_portfolio(
        cash="100000", principal_total="200000",
        emergency_reserve="20000", holdings=[bd],
    )
    # total = 130000, invested = 30000, invested/total ≈ 23.1%
    # equity 目标等效 = 0.5 * 0.231 = 11.5%，实际 0%，偏离 -11.5% < -5%
    # cash_abundant: 100000 >= 20000 → True
    result = position.diagnose(p, default_settings)
    codes = [s.code for s in result.signals]
    assert "UNDER_ALLOCATED_EQUITY_FUND" in codes
    sig = next(s for s in result.signals if s.code == "UNDER_ALLOCATED_EQUITY_FUND")
    assert sig.severity.value == "info"


def test_empty_portfolio_returns_empty(default_settings):
    p = make_portfolio(cash="0", principal_total="100000", emergency_reserve="10000")
    result = position.diagnose(p, default_settings)
    # 仍然生成 5 个桶（即便 value=0）但 total=0 返回空
    assert result.total_assets == Decimal("0")
    assert result.buckets == []
