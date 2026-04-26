"""UI helper 逻辑单元测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from fund_advisor.data.akshare_client import FundDataError
from fund_advisor.models import FundType, PlanFrequency, Strategy
from fund_advisor.ui.app import (
    _build_holdings_from_editor,
    _build_dca_plans_from_editor,
    _dca_plan_entry_df,
    _fund_detail_latest_nav,
    _fund_type_label,
    _holdings_entry_df,
    _refresh_portfolio_latest_navs,
)

from .conftest import make_holding


def test_build_holdings_preserves_auto_metadata_when_code_unchanged():
    original = [
        make_holding(code="017513", name="广发北证50成份指数C", fund_type=FundType.EQUITY)
    ]
    edited = pd.DataFrame(
        [
            {
                "code": "017513",
                "shares": 1000.0,
                "average_cost": 1.2,
                "notes": "原备注",
                "(自动)名称": "广发北证50成份指数C",
                "(自动)类型": "股票基金",
            }
        ]
    )

    holdings = _build_holdings_from_editor(edited, original)

    assert holdings[0].name == "广发北证50成份指数C"
    assert holdings[0].fund_type == FundType.EQUITY


def test_build_holdings_clears_auto_metadata_when_code_changes():
    original = [
        make_holding(code="017513", name="广发北证50成份指数C", fund_type=FundType.EQUITY)
    ]
    edited = pd.DataFrame(
        [
            {
                "code": "017812",
                "shares": 1000.0,
                "average_cost": 1.2,
                "notes": "",
                "(自动)名称": "广发北证50成份指数C",
                "(自动)类型": "股票基金",
            }
        ]
    )

    holdings = _build_holdings_from_editor(edited, original)

    assert holdings[0].code == "017812"
    assert holdings[0].name is None
    assert holdings[0].fund_type is None


def test_holdings_entry_df_keeps_editor_columns_when_portfolio_empty():
    from .conftest import make_portfolio

    portfolio = make_portfolio(holdings=[])

    df = _holdings_entry_df(portfolio)

    assert df.empty
    assert list(df.columns) == [
        "code",
        "shares",
        "average_cost",
        "notes",
        "(自动)名称",
        "(自动)类型",
    ]


def test_build_holdings_from_editor_supports_first_row_for_empty_portfolio():
    edited = pd.DataFrame(
        [
            {
                "code": "017513",
                "shares": 1000.0,
                "average_cost": 1.2,
                "notes": "新建第一只",
                "(自动)名称": "",
                "(自动)类型": "",
            }
        ]
    )

    holdings = _build_holdings_from_editor(edited, [])

    assert len(holdings) == 1
    assert holdings[0].code == "017513"
    assert holdings[0].name is None
    assert holdings[0].fund_type is None
    assert holdings[0].strategy == Strategy.HOLD


def test_build_holdings_preserves_existing_strategy_when_ui_no_longer_edits_it():
    original = [make_holding(strategy=Strategy.LUMP_SUM)]
    edited = pd.DataFrame(
        [
            {
                "code": "000001",
                "shares": 1000.0,
                "average_cost": 1.2,
                "notes": "",
                "(自动)名称": "测试基金",
                "(自动)类型": "股票基金",
            }
        ]
    )

    holdings = _build_holdings_from_editor(edited, original)

    assert holdings[0].strategy == Strategy.LUMP_SUM


def test_fund_type_label_uses_chinese_text():
    assert _fund_type_label(FundType.EQUITY) == "股票基金"
    assert _fund_type_label(FundType.BOND) == "债券基金"


def test_dca_plan_entry_df_keeps_editor_columns_when_portfolio_empty():
    from .conftest import make_portfolio

    portfolio = make_portfolio(holdings=[])

    df = _dca_plan_entry_df(portfolio)

    assert df.empty
    assert list(df.columns) == [
        "code",
        "amount_rmb",
        "frequency",
        "start_date",
        "enabled",
        "notes",
        "(自动)名称",
        "(自动)类型",
    ]


def test_build_dca_plans_from_editor_supports_first_row():
    edited = pd.DataFrame(
        [
            {
                "code": "017513",
                "amount_rmb": 50.0,
                "frequency": "daily",
                "start_date": date(2026, 4, 25),
                "enabled": True,
                "notes": "每日定投",
                "(自动)名称": "",
                "(自动)类型": "",
            }
        ]
    )

    plans = _build_dca_plans_from_editor(edited, [])

    assert len(plans) == 1
    assert plans[0].code == "017513"
    assert plans[0].frequency == PlanFrequency.DAILY
    assert plans[0].enabled is True


def test_build_dca_plans_from_editor_infers_weekly_schedule():
    edited = pd.DataFrame(
        [
            {
                "code": "017513",
                "amount_rmb": 100.0,
                "frequency": "weekly",
                "start_date": date(2026, 4, 27),
                "enabled": True,
                "notes": "",
                "(自动)名称": "广发北证50成份指数C",
                "(自动)类型": "股票基金",
            }
        ]
    )

    plans = _build_dca_plans_from_editor(edited, [])

    assert plans[0].day_of_week == 0
    assert plans[0].day_of_month is None


def test_fund_detail_latest_nav_prefers_nav_history_last_point():
    holding = make_holding()
    holding.latest_nav = Decimal("1.10")
    holding.latest_nav_date = date(2026, 4, 20)

    latest_nav, latest_nav_date = _fund_detail_latest_nav(
        holding,
        [
            {"date": date(2026, 4, 21), "nav": 1.23},
            {"date": date(2026, 4, 22), "nav": 1.25},
        ],
    )

    assert latest_nav == 1.25
    assert latest_nav_date == date(2026, 4, 22)


def test_fund_detail_latest_nav_falls_back_to_selected_runtime_value():
    holding = make_holding()
    holding.latest_nav = Decimal("1.10")
    holding.latest_nav_date = date(2026, 4, 20)

    latest_nav, latest_nav_date = _fund_detail_latest_nav(holding, [])

    assert latest_nav == Decimal("1.10")
    assert latest_nav_date == date(2026, 4, 20)


def test_refresh_portfolio_latest_navs_updates_equity_and_money_funds():
    equity = make_holding(code="017513", fund_type=FundType.EQUITY)
    money = make_holding(code="000198", fund_type=FundType.MONEY)

    def fake_fetch(code: str) -> dict:
        assert code == "017513"
        return {"nav": Decimal("1.25"), "nav_date": date(2026, 4, 25)}

    from .conftest import make_portfolio

    portfolio = make_portfolio(holdings=[equity, money])
    _refresh_portfolio_latest_navs(portfolio, fetch_latest_nav=fake_fetch)

    assert equity.latest_nav == Decimal("1.25")
    assert equity.latest_nav_date == date(2026, 4, 25)
    assert money.latest_nav == Decimal("1.0")
    assert money.latest_nav_date == date.today()


def test_refresh_portfolio_latest_navs_keeps_existing_value_when_fetch_fails():
    holding = make_holding(code="017513", fund_type=FundType.EQUITY)
    holding.latest_nav = Decimal("1.10")
    holding.latest_nav_date = date(2026, 4, 20)

    def fake_fetch(code: str) -> dict:
        raise FundDataError("boom")

    from .conftest import make_portfolio

    portfolio = make_portfolio(holdings=[holding])
    _refresh_portfolio_latest_navs(portfolio, fetch_latest_nav=fake_fetch)

    assert holding.latest_nav == Decimal("1.10")
    assert holding.latest_nav_date == date(2026, 4, 20)
