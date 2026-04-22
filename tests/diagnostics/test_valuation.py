"""估值诊断单元测试（mock 指数查询，不走网络）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fund_advisor.diagnostics import valuation
from fund_advisor.models import FundType, ValuationStatus

from ..conftest import make_holding, make_portfolio


def _fake_fetch_low(symbol: str) -> dict:
    """15% 分位 → LOW。"""
    return {
        "symbol": symbol,
        "as_of": date(2026, 4, 20),
        "pe": Decimal("11.20"),
        "pb": Decimal("1.35"),
        "pe_percentile": Decimal("0.15"),
    }


def _fake_fetch_overheated(symbol: str) -> dict:
    """92% 分位 → OVERHEATED。"""
    return {
        "symbol": symbol,
        "as_of": date(2026, 4, 20),
        "pe": Decimal("28.0"),
        "pb": Decimal("3.2"),
        "pe_percentile": Decimal("0.92"),
    }


def _fake_fetch_fail(symbol: str) -> dict:
    from fund_advisor.data.akshare_client import FundDataError

    raise FundDataError("mock failure")


def test_low_valuation_emits_info_signal(default_settings):
    h = make_holding(
        code="110020", name="易方达沪深300ETF联接A",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
    )
    p = make_portfolio(holdings=[h])
    result = valuation.diagnose(p, default_settings, fetch_index=_fake_fetch_low)
    item = result.items[0]
    assert item.status == ValuationStatus.LOW
    assert item.index_symbol == "沪深300"
    assert any(s.code == "VALUATION_LOW" for s in result.signals)


def test_overheated_emits_warn_signal(default_settings):
    h = make_holding(
        code="161725", name="招商中证白酒",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
    )
    # 招商中证白酒里不含匹配关键词，我们用创业板来制造命中：
    h.name = "华安创业板50指数"
    p = make_portfolio(holdings=[h])
    result = valuation.diagnose(p, default_settings, fetch_index=_fake_fetch_overheated)
    item = result.items[0]
    assert item.status == ValuationStatus.OVERHEATED
    assert any(s.code == "VALUATION_OVERHEATED" for s in result.signals)


def test_unmatched_active_fund_is_unavailable(default_settings):
    """主动/主题基金无法匹配指数 → UNAVAILABLE。"""
    h = make_holding(
        code="017812", name="东方人工智能主题混合C",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
    )
    p = make_portfolio(holdings=[h])
    result = valuation.diagnose(p, default_settings, fetch_index=_fake_fetch_low)
    assert result.items[0].status == ValuationStatus.UNAVAILABLE
    assert result.items[0].index_symbol is None


def test_bond_and_money_skipped(default_settings):
    bd = make_holding(
        code="000914", name="中加纯债",
        fund_type=FundType.BOND, shares="1000", cost_price="1.0",
    )
    mm = make_holding(
        code="000307", name="华夏现金增利",
        fund_type=FundType.MONEY, shares="1000", cost_price="1.0",
    )
    p = make_portfolio(holdings=[bd, mm])
    result = valuation.diagnose(p, default_settings, fetch_index=_fake_fetch_low)
    assert all(it.status == ValuationStatus.UNAVAILABLE for it in result.items)


def test_fetch_failure_graceful(default_settings):
    """网络失败时降级为 UNAVAILABLE，不抛异常。"""
    h = make_holding(
        code="110020", name="易方达沪深300ETF联接A",
        fund_type=FundType.EQUITY, shares="1000", cost_price="1.0",
    )
    p = make_portfolio(holdings=[h])
    result = valuation.diagnose(p, default_settings, fetch_index=_fake_fetch_fail)
    item = result.items[0]
    assert item.status == ValuationStatus.UNAVAILABLE
    assert "mock failure" in item.note
