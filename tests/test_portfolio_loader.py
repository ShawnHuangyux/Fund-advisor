"""portfolio_loader 读写 + 备份测试。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from fund_advisor.data import load_portfolio, save_portfolio
from fund_advisor.models import Portfolio

SAMPLE_YAML = """
capital:
  available_cash: 50000
  emergency_reserve: 30000
  monthly_expense: 8000
  target_portfolio_budget: 200000
dca_plans:
  - code: "017513"
    amount_rmb: 100
    frequency: daily
    start_date: "2026-04-25"
    enabled: true
holdings:
  - code: "017513"
    name: "广发北证50成份指数C"
    fund_type: equity_fund
    shares: 3000
    average_cost: 1.25
"""

BAD_ALLOCATION_YAML = """
cash: 0
principal_total: 100000
emergency_reserve: 10000
risk_tolerance: moderate
max_drawdown_tolerance: 0.20
target_allocation:
  equity_fund: 0.50
  bond_fund: 0.20
  money_fund: 0.10   # 三项之和 0.8，应失败
holdings: []
"""

LEGACY_YAML = """
cash: 50000
principal_total: 200000
emergency_reserve: 30000
holdings:
  - code: "017513"
    name: "广发北证50成份指数C"
    fund_type: equity_fund
    shares: 3000
    cost_price: 1.25
    purchase_date: "2025-08-15"
    target_allocation: 0.10
    strategy: DCA
"""


def test_load_valid_portfolio(tmp_path: Path):
    p = tmp_path / "portfolio.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")

    portfolio = load_portfolio(p)
    assert portfolio.cash == Decimal("50000")
    assert len(portfolio.holdings) == 1
    assert len(portfolio.dca_plans) == 1
    assert portfolio.holdings[0].code == "017513"
    assert portfolio.holdings[0].average_cost == Decimal("1.25")


def test_allocation_sum_validation_fails(tmp_path: Path):
    p = tmp_path / "portfolio.yaml"
    p.write_text(BAD_ALLOCATION_YAML, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_portfolio(p)


def test_save_creates_backup_and_roundtrip(tmp_path: Path):
    p = tmp_path / "portfolio.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")

    portfolio = load_portfolio(p)
    # 修改 cash 后写回
    updated = portfolio.model_copy(
        update={
            "capital": portfolio.capital.model_copy(
                update={"available_cash": Decimal("60000")}
            )
        }
    )
    save_portfolio(updated, p)

    # 应存在 backup-* 文件
    backups = list(tmp_path.glob("portfolio.yaml.backup-*"))
    assert len(backups) == 1

    # 往返读取结果一致
    reloaded = load_portfolio(p)
    assert reloaded.cash == Decimal("60000")
    assert len(reloaded.holdings) == 1


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_portfolio(tmp_path / "nope.yaml")


def test_empty_name_becomes_none(tmp_path: Path):
    """YAML 里 name 是空字符串时，Pydantic 应归一为 None，供 akshare 后续补齐。"""
    yaml_text = """
capital:
  available_cash: 0
  emergency_reserve: 10000
  target_portfolio_budget: 100000
holdings:
  - code: "017811"
    name: ""
    shares: 100
    average_cost: 1.0
"""
    p = tmp_path / "portfolio.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    portfolio = load_portfolio(p)
    assert portfolio.holdings[0].name is None
    assert portfolio.holdings[0].fund_type is None
    assert portfolio.holdings[0].average_cost == Decimal("1.0")


def test_code_is_zero_padded():
    """4 位代码应被补齐为 6 位。"""
    portfolio = Portfolio.model_validate(
        {
            "capital": {
                "available_cash": 0,
                "emergency_reserve": 10000,
                "target_portfolio_budget": 100000,
            },
            "holdings": [
                {
                    "code": "1234",
                    "name": "测试",
                    "fund_type": "equity_fund",
                    "shares": 100,
                    "average_cost": 1.0,
                }
            ],
        }
    )
    assert portfolio.holdings[0].code == "001234"


def test_duplicate_holding_codes_fail_validation():
    with pytest.raises(ValidationError, match="基金代码不能重复"):
        Portfolio.model_validate(
            {
                "capital": {
                    "available_cash": 0,
                    "emergency_reserve": 10000,
                    "target_portfolio_budget": 100000,
                },
                "holdings": [
                    {
                        "code": "1234",
                        "name": "测试A",
                        "fund_type": "equity_fund",
                        "shares": 100,
                        "average_cost": 1.0,
                    },
                    {
                        "code": "001234",
                        "name": "测试B",
                        "fund_type": "equity_fund",
                        "shares": 200,
                        "average_cost": 1.1,
                    },
                ],
            }
        )


def test_risk_tolerance_defaults_to_moderate_when_missing():
    portfolio = Portfolio.model_validate(
        {
            "capital": {
                "available_cash": 0,
                "emergency_reserve": 10000,
                "target_portfolio_budget": 100000,
            },
            "holdings": [],
        }
    )

    assert portfolio.risk_tolerance.value == "moderate"
    assert portfolio.max_drawdown_tolerance == Decimal("0.15")
    assert portfolio.target_allocation.equity_fund == Decimal("0.50")
    assert portfolio.target_allocation.bond_fund == Decimal("0.30")
    assert portfolio.target_allocation.money_fund == Decimal("0.20")


def test_load_legacy_portfolio_schema(tmp_path: Path):
    p = tmp_path / "portfolio.yaml"
    p.write_text(LEGACY_YAML, encoding="utf-8")

    portfolio = load_portfolio(p)

    assert portfolio.capital.available_cash == Decimal("50000")
    assert portfolio.capital.target_portfolio_budget == Decimal("200000")
    assert portfolio.holdings[0].average_cost == Decimal("1.25")
    assert portfolio.holdings[0].purchase_date is not None
    assert portfolio.holdings[0].strategy.value == "DCA"
