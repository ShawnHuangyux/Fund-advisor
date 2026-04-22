"""portfolio_loader 读写 + 备份测试。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from fund_advisor.data import load_portfolio, save_portfolio
from fund_advisor.models import Portfolio

SAMPLE_YAML = """
cash: 50000
principal_total: 200000
emergency_reserve: 30000
risk_tolerance: moderate
max_drawdown_tolerance: 0.20
target_allocation:
  equity_fund: 0.50
  bond_fund: 0.30
  money_fund: 0.20
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


def test_load_valid_portfolio(tmp_path: Path):
    p = tmp_path / "portfolio.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")

    portfolio = load_portfolio(p)
    assert portfolio.cash == Decimal("50000")
    assert len(portfolio.holdings) == 1
    assert portfolio.holdings[0].code == "017513"


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
    updated = portfolio.model_copy(update={"cash": Decimal("60000")})
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
cash: 0
principal_total: 100000
emergency_reserve: 10000
risk_tolerance: moderate
max_drawdown_tolerance: 0.20
target_allocation:
  equity_fund: 0.50
  bond_fund: 0.30
  money_fund: 0.20
holdings:
  - code: "017811"
    name: ""
    shares: 100
    cost_price: 1.0
    purchase_date: "2024-01-01"
"""
    p = tmp_path / "portfolio.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    portfolio = load_portfolio(p)
    assert portfolio.holdings[0].name is None
    assert portfolio.holdings[0].fund_type is None
    # target_allocation 未填 → 默认 0.10
    assert portfolio.holdings[0].target_allocation == Decimal("0.10")


def test_code_is_zero_padded():
    """4 位代码应被补齐为 6 位。"""
    portfolio = Portfolio.model_validate(
        {
            "cash": 0,
            "principal_total": 100000,
            "emergency_reserve": 10000,
            "risk_tolerance": "moderate",
            "max_drawdown_tolerance": 0.20,
            "target_allocation": {
                "equity_fund": 0.50,
                "bond_fund": 0.30,
                "money_fund": 0.20,
            },
            "holdings": [
                {
                    "code": "1234",
                    "name": "测试",
                    "fund_type": "equity_fund",
                    "shares": 100,
                    "cost_price": 1.0,
                    "purchase_date": "2024-01-01",
                    "target_allocation": 0.1,
                }
            ],
        }
    )
    assert portfolio.holdings[0].code == "001234"
