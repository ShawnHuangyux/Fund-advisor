"""DataQualityReport 生成逻辑单元测试（Fix 2）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fund_advisor.advisor.advisor import build_data_quality_report
from fund_advisor.models import (
    CapitalDiagnosis,
    ConcentrationDiagnosis,
    DiagnosisReport,
    FundRiskMetric,
    FundType,
    Holding,
    PortfolioSummary,
    RiskDiagnosis,
    StressTestResult,
    Strategy,
)

from ..conftest import make_holding, make_portfolio


def _minimal_report() -> DiagnosisReport:
    """构造一份 schema 合法的最小诊断报告，供 build_data_quality_report 使用。"""
    return DiagnosisReport(
        generated_at=datetime(2026, 4, 24),
        portfolio_summary=PortfolioSummary(
            total_assets=Decimal("100000"),
            cash=Decimal("0"),
            invested_value=Decimal("100000"),
            invested_cost=Decimal("100000"),
            total_pnl=Decimal("0"),
            principal_total=Decimal("100000"),
            emergency_reserve=Decimal("0"),
            holdings_count=0,
            holdings=[],
        ),
        concentration_diagnosis=ConcentrationDiagnosis(items=[], signals=[]),
        capital_diagnosis=CapitalDiagnosis(
            invested_value=Decimal("100000"),
            investable_principal=Decimal("100000"),
            capital_utilization=Decimal("1.0"),
            emergency_reserve=Decimal("0"),
            monthly_expense=Decimal("8000"),
            emergency_adequacy_months=Decimal("0"),
            dca_budget_per_month=Decimal("0"),
            signals=[],
        ),
    )


def test_data_quality_all_complete():
    """所有基金信息齐备时 overall_complete=True，warnings 为空。"""
    h = make_holding(
        code="110022",
        name="易方达消费",
        fund_type=FundType.EQUITY,
    )
    h.latest_nav = Decimal("3.5")
    h.latest_nav_date = date(2026, 4, 23)
    p = make_portfolio(holdings=[h])
    report = _minimal_report()

    dq = build_data_quality_report(p, report)

    assert dq.overall_complete is True
    assert dq.missing_name == []
    assert dq.missing_fund_type == []
    assert dq.missing_latest_nav == []
    assert dq.missing_nav_history == []
    assert dq.warnings == []


def test_data_quality_missing_name_and_nav():
    """基金缺 name 和 latest_nav，应分别进入对应列表，且 overall_complete=False。"""
    h = Holding(
        code="999999",
        name=None,
        fund_type=FundType.EQUITY,
        shares=Decimal("1000"),
        cost_price=Decimal("1.0"),
        target_allocation=Decimal("0.1"),
        strategy=Strategy.DCA,
        purchase_date=date(2024, 1, 1),
    )
    # latest_nav 没有设置（None），且非货基
    p = make_portfolio(holdings=[h])
    report = _minimal_report()

    dq = build_data_quality_report(p, report)

    assert dq.overall_complete is False
    assert dq.missing_name == ["999999"]
    assert dq.missing_latest_nav == ["999999"]
    assert any("基金名称" in w for w in dq.warnings)
    assert any("最新净值" in w for w in dq.warnings)


def test_data_quality_money_fund_not_flagged_for_missing_nav():
    """货基 latest_nav 即使为 None，也不算缺失（货基 nav 恒为 1.0）。"""
    h = Holding(
        code="000001",
        name="华夏现金增利",
        fund_type=FundType.MONEY,
        shares=Decimal("1000"),
        cost_price=Decimal("1.0"),
        target_allocation=Decimal("0.1"),
        strategy=Strategy.DCA,
        purchase_date=date(2024, 1, 1),
    )
    # 故意不设 latest_nav
    p = make_portfolio(holdings=[h])
    report = _minimal_report()

    dq = build_data_quality_report(p, report)

    assert dq.missing_latest_nav == []


def test_data_quality_unknown_fund_type_flagged():
    """fund_type 为 UNKNOWN（或 None）都应被计入 missing_fund_type。"""
    h1 = Holding(
        code="100001",
        name="未知一号",
        fund_type=None,
        shares=Decimal("100"),
        cost_price=Decimal("1.0"),
        latest_nav=Decimal("1.0"),
        target_allocation=Decimal("0.1"),
        strategy=Strategy.DCA,
        purchase_date=date(2024, 1, 1),
    )
    h2 = Holding(
        code="100002",
        name="未知二号",
        fund_type=FundType.UNKNOWN,
        shares=Decimal("100"),
        cost_price=Decimal("1.0"),
        latest_nav=Decimal("1.0"),
        target_allocation=Decimal("0.1"),
        strategy=Strategy.DCA,
        purchase_date=date(2024, 1, 1),
    )
    p = make_portfolio(holdings=[h1, h2])
    report = _minimal_report()

    dq = build_data_quality_report(p, report)

    assert set(dq.missing_fund_type) == {"100001", "100002"}
    assert any("equity_fund 桶" in w for w in dq.warnings)


def test_data_quality_missing_nav_history_from_stress_tests():
    """missing_nav_history 应从 RiskDiagnosis.stress_tests.missing_funds 去重汇总。"""
    report = _minimal_report()
    report.risk_diagnosis = RiskDiagnosis(
        fund_metrics=[],
        stress_tests=[
            StressTestResult(
                scenario_name="2022 熊市",
                start=date(2022, 1, 1),
                end=date(2022, 10, 31),
                portfolio_loss=Decimal("0.2"),
                breach_tolerance=False,
                fund_losses={},
                missing_funds=["017513", "017812"],
            ),
            StressTestResult(
                scenario_name="2024 回撤",
                start=date(2024, 1, 1),
                end=date(2024, 2, 5),
                portfolio_loss=Decimal("0.1"),
                breach_tolerance=False,
                fund_losses={},
                missing_funds=["017513"],  # 与上面重复，应去重
            ),
        ],
    )
    p = make_portfolio(holdings=[])

    dq = build_data_quality_report(p, report)

    assert dq.missing_nav_history == ["017513", "017812"]
    assert dq.overall_complete is False
