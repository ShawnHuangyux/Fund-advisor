"""pytest 公共夹具。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from fund_advisor.models import (
    FundType,
    Holding,
    Portfolio,
    RiskTolerance,
    Settings,
    Strategy,
    TargetAllocation,
)


@pytest.fixture
def default_settings() -> Settings:
    """使用内置默认值 + 关键词表的 Settings。"""
    return Settings.model_validate(
        {
            "concentration_keywords": {
                "high_volatility": ["北证", "北交所", "科创", "行业", "主题"],
                "broad_index": ["沪深300", "中证500", "创业板"],
            },
        }
    )


def make_holding(
    *,
    code: str = "000001",
    name: str = "测试基金",
    fund_type: FundType = FundType.EQUITY,
    shares: str = "1000",
    cost_price: str = "1.0",
    target_allocation: str = "0.20",
    strategy: Strategy = Strategy.DCA,
    purchase_date: date = date(2024, 1, 1),
) -> Holding:
    return Holding(
        code=code,
        name=name,
        fund_type=fund_type,
        shares=Decimal(shares),
        cost_price=Decimal(cost_price),
        target_allocation=Decimal(target_allocation),
        strategy=strategy,
        purchase_date=purchase_date,
    )


def make_portfolio(
    *,
    cash: str = "50000",
    principal_total: str = "200000",
    emergency_reserve: str = "30000",
    holdings: list[Holding] | None = None,
    max_drawdown_tolerance: str = "0.20",
) -> Portfolio:
    return Portfolio(
        cash=Decimal(cash),
        principal_total=Decimal(principal_total),
        emergency_reserve=Decimal(emergency_reserve),
        risk_tolerance=RiskTolerance.MODERATE,
        max_drawdown_tolerance=Decimal(max_drawdown_tolerance),
        target_allocation=TargetAllocation(
            equity_fund=Decimal("0.50"),
            bond_fund=Decimal("0.30"),
            money_fund=Decimal("0.20"),
        ),
        holdings=holdings or [],
    )
