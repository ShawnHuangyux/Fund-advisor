"""scheduler.daily_job.run_daily_job I/O 闭环测试。

只测文件落盘 + 异常吞掉行为，不测 APScheduler 本身。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fund_advisor.models import (
    CapitalDiagnosis,
    ConcentrationDiagnosis,
    DiagnosisReport,
    PortfolioSummary,
)
from fund_advisor.scheduler.daily_job import run_daily_job

from ..conftest import make_portfolio


def _minimal_report() -> DiagnosisReport:
    """构造一个 schema 合法的最小 DiagnosisReport。"""
    return DiagnosisReport(
        generated_at=datetime(2026, 4, 24, 16, 30, 0),
        portfolio_summary=PortfolioSummary(
            total_assets=Decimal("100000"),
            cash=Decimal("50000"),
            invested_value=Decimal("50000"),
            invested_cost=Decimal("45000"),
            total_pnl=Decimal("5000"),
            principal_total=Decimal("200000"),
            emergency_reserve=Decimal("30000"),
            holdings_count=0,
            holdings=[],
        ),
        concentration_diagnosis=ConcentrationDiagnosis(items=[], signals=[]),
        capital_diagnosis=CapitalDiagnosis(
            invested_value=Decimal("50000"),
            investable_principal=Decimal("170000"),
            capital_utilization=Decimal("0.29"),
            emergency_reserve=Decimal("30000"),
            monthly_expense=Decimal("8000"),
            emergency_adequacy_months=Decimal("3.75"),
            dca_budget_per_month=Decimal("10000"),
            signals=[],
        ),
    )


def test_run_daily_job_writes_json(tmp_path: Path, default_settings):
    """成功路径：run_diagnosis 被 mock 后，报告应按日期落盘且能反序列化。"""
    reports_dir = tmp_path / "reports"
    settings_path = tmp_path / "settings.yaml"
    portfolio_path = tmp_path / "portfolio.yaml"

    with patch(
        "fund_advisor.scheduler.daily_job.load_settings",
        return_value=default_settings,
    ), patch(
        "fund_advisor.scheduler.daily_job.load_portfolio",
        return_value=make_portfolio(),
    ), patch(
        "fund_advisor.scheduler.daily_job.build_deepseek_client",
        return_value=None,
    ), patch(
        "fund_advisor.scheduler.daily_job.advisor_mod.run_diagnosis",
        return_value=_minimal_report(),
    ):
        out = run_daily_job(settings_path, portfolio_path, reports_dir)

    assert out is not None
    assert out.exists()
    assert out.name == "2026-04-24.json"

    # 能往返反序列化
    restored = DiagnosisReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert restored.generated_at.date().isoformat() == "2026-04-24"
    assert restored.portfolio_summary.total_assets == Decimal("100000")


def test_run_daily_job_swallows_exception(tmp_path: Path, default_settings):
    """失败路径：run_diagnosis 抛异常时，函数不向上抛、返回 None、不落盘。"""
    reports_dir = tmp_path / "reports"
    settings_path = tmp_path / "settings.yaml"
    portfolio_path = tmp_path / "portfolio.yaml"

    with patch(
        "fund_advisor.scheduler.daily_job.load_settings",
        return_value=default_settings,
    ), patch(
        "fund_advisor.scheduler.daily_job.load_portfolio",
        return_value=object(),
    ), patch(
        "fund_advisor.scheduler.daily_job.build_deepseek_client",
        return_value=None,
    ), patch(
        "fund_advisor.scheduler.daily_job.advisor_mod.run_diagnosis",
        side_effect=RuntimeError("boom"),
    ):
        out = run_daily_job(settings_path, portfolio_path, reports_dir)

    assert out is None
    # 目录可能已创建（reports_dir.mkdir 在写盘之前是否执行取决于实现位置），
    # 但绝不能有 JSON 文件
    assert not list(reports_dir.glob("*.json")) if reports_dir.exists() else True
