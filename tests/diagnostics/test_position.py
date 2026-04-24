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


def test_unknown_fund_type_falls_into_equity_bucket(default_settings):
    """Fix 3：fund_type=None 的基金应被归入 equity_fund 桶，参与超配信号。

    场景：
    - 一只 fund_type=None 的基金占总资产 40%
    - 一只普通股基占 20%
    - cash 40%
    - invested_value = 60%，等效股基目标 = 0.5 * 0.6 = 30%
    - 实际股基占比（含未知桶）= 60%
    - 偏离 = +30% > tolerance 5% → OVER_ALLOCATED_EQUITY_FUND 触发

    旧实现里未知桶归入 "other"（target=0，信号逻辑被跳过），
    股基实际仅 20% < 等效目标 30%，反而会触发 UNDER（方向相反）。
    """
    unknown = make_holding(
        code="100001",
        name=None,
        shares="40000",
        cost_price="1.0",
    )
    unknown.fund_type = None  # 显式设置 None 覆盖 make_holding 的默认 EQUITY
    eq = make_holding(
        code="110020",
        name="易方达沪深300",
        fund_type=FundType.EQUITY,
        shares="20000",
        cost_price="1.0",
    )
    p = make_portfolio(
        cash="40000",
        principal_total="200000",
        emergency_reserve="20000",
        holdings=[unknown, eq],
    )

    result = position.diagnose(p, default_settings)
    codes = [s.code for s in result.signals]

    assert "OVER_ALLOCATED_EQUITY_FUND" in codes
    # 未知桶不应触发 OVER_ALLOCATED_OTHER（"other" 桶不参与信号）
    assert "OVER_ALLOCATED_OTHER" not in codes
    # "other" 桶里不应再有这只未知基金
    other_bucket = next(b for b in result.buckets if b.category == "other")
    assert other_bucket.actual_value == Decimal("0.00")


def test_empty_portfolio_returns_empty(default_settings):
    p = make_portfolio(cash="0", principal_total="100000", emergency_reserve="10000")
    result = position.diagnose(p, default_settings)
    # 仍然生成 5 个桶（即便 value=0）但 total=0 返回空
    assert result.total_assets == Decimal("0")
    assert result.buckets == []
