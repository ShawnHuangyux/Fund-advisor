"""集中度诊断单元测试。"""

from __future__ import annotations

from decimal import Decimal

from fund_advisor.diagnostics import concentration
from fund_advisor.models import FundType

from ..conftest import make_holding, make_portfolio


def test_beijing_fund_over_limit_triggers_signal(default_settings):
    """北证类基金占比 22% 应触发 OVER_CONCENTRATED（上限 15%）。"""
    # 总资产 = cash + 持仓 = 10000 + 28000 = 38000；持仓占比 ≈ 73.7%
    h = make_holding(
        code="017513",
        name="广发北证50成份指数C",
        fund_type=FundType.EQUITY,
        shares="20000",
        cost_price="1.40",  # 市值 28000
    )
    p = make_portfolio(
        cash="10000",
        principal_total="100000",
        emergency_reserve="20000",
        holdings=[h],
    )
    result = concentration.diagnose(p, default_settings)

    item = result.items[0]
    assert item.risk_class == "high_volatility"
    assert item.cap_ratio == Decimal("0.15")
    assert item.over_limit is True
    codes = [s.code for s in result.signals]
    assert "OVER_CONCENTRATED" in codes


def test_broad_index_within_30pct_no_signal(default_settings):
    """沪深300 占比 25% 不应触发（上限 30%）。"""
    # 持仓 50000 / 总资产 (150000+50000=200000) = 25%
    h = make_holding(
        code="110020",
        name="易方达沪深300ETF联接A",
        fund_type=FundType.EQUITY,
        shares="25000",
        cost_price="2.00",  # 市值 50000
    )
    p = make_portfolio(
        cash="150000",
        principal_total="300000",
        emergency_reserve="30000",
        holdings=[h],
    )
    result = concentration.diagnose(p, default_settings)

    assert result.items[0].risk_class == "broad_index"
    assert result.items[0].over_limit is False
    assert all(s.code != "OVER_CONCENTRATED" for s in result.signals)


def test_unknown_fund_uses_conservative_15pct(default_settings):
    """未匹配关键词的主动股基占比 16% 应按 15% 保守上限触发。"""
    # 持仓 16000 / 总资产 (84000+16000=100000) = 16%
    h = make_holding(
        code="000123",
        name="某神秘主动权益基金",  # 不命中任何关键词
        fund_type=FundType.EQUITY,
        shares="16000",
        cost_price="1.00",
    )
    p = make_portfolio(
        cash="84000",
        principal_total="200000",
        emergency_reserve="30000",
        holdings=[h],
    )
    result = concentration.diagnose(p, default_settings)

    assert result.items[0].risk_class == "unknown"
    assert result.items[0].cap_ratio == Decimal("0.15")
    assert result.items[0].over_limit is True
    assert any(s.code == "OVER_CONCENTRATED" for s in result.signals)


def test_money_fund_never_over_limit(default_settings):
    """货基上限 100%，永远不报 OVER_CONCENTRATED。"""
    h = make_holding(
        code="003003",
        name="华夏现金增利货币A",
        fund_type=FundType.MONEY,
        shares="90000",
        cost_price="1.00",  # 市值 90000
    )
    p = make_portfolio(
        cash="10000",
        principal_total="150000",
        emergency_reserve="30000",
        holdings=[h],
    )
    result = concentration.diagnose(p, default_settings)
    assert result.items[0].risk_class == "money"
    assert result.items[0].over_limit is False


def test_empty_portfolio_returns_empty(default_settings):
    p = make_portfolio(cash="0", principal_total="100000", emergency_reserve="10000")
    result = concentration.diagnose(p, default_settings)
    assert result.items == []
    assert result.signals == []
