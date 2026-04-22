"""资金效率诊断。

规格文档 §6：
- 本金利用率 = 已投入市值 / (principal_total - emergency_reserve)
- 应急金充足度 = emergency_reserve / monthly_expense
- 定投预算建议 = 剩余可投资金 / 计划定投月数
- 应急金不足 (<3 月) → 高优信号，阻断所有加仓建议（由 advisor 层落地）
- 利用率 <50% → info（阶段 1 没有估值信号，暂只发 info）
"""

from __future__ import annotations

from decimal import Decimal

from ..models import CapitalDiagnosis, Portfolio, Settings, Severity, Signal

_DEC_ZERO = Decimal("0")


def _safe_div(num: Decimal, den: Decimal, quantize: str = "0.0001") -> Decimal:
    if den <= _DEC_ZERO:
        return _DEC_ZERO
    return (num / den).quantize(Decimal(quantize))


def diagnose(portfolio: Portfolio, settings: Settings) -> CapitalDiagnosis:
    cap_cfg = settings.capital
    invested = portfolio.invested_value
    investable = (portfolio.principal_total - portfolio.emergency_reserve).quantize(
        Decimal("0.01")
    )
    utilization = _safe_div(invested, investable)

    monthly_expense = cap_cfg.monthly_expense_default
    adequacy_months = _safe_div(
        portfolio.emergency_reserve, monthly_expense, quantize="0.01"
    )

    remaining_to_invest = max(
        _DEC_ZERO, (investable - invested).quantize(Decimal("0.01"))
    )
    dca_months = cap_cfg.default_dca_months
    dca_budget = _safe_div(
        remaining_to_invest, dca_months, quantize="0.01"
    ) if dca_months > _DEC_ZERO else _DEC_ZERO

    signals: list[Signal] = []

    if adequacy_months < cap_cfg.emergency_min_months:
        signals.append(
            Signal(
                code="EMERGENCY_RESERVE_LOW",
                severity=Severity.HIGH,
                fund_code=None,
                message=(
                    f"应急储备金仅可覆盖 {adequacy_months} 个月（"
                    f"月支出按 ¥{monthly_expense} 计），低于最低 "
                    f"{cap_cfg.emergency_min_months} 个月要求，"
                    f"本次诊断将阻断一切加仓类建议。"
                ),
                detail={
                    "adequacy_months": str(adequacy_months),
                    "min_months": str(cap_cfg.emergency_min_months),
                    "emergency_reserve": str(portfolio.emergency_reserve),
                    "monthly_expense": str(monthly_expense),
                },
            )
        )

    if utilization < cap_cfg.capital_under_utilization_threshold:
        signals.append(
            Signal(
                code="CAPITAL_UNDERUTILIZED",
                severity=Severity.INFO,
                fund_code=None,
                message=(
                    f"本金利用率 {utilization * 100:.2f}%，低于 "
                    f"{cap_cfg.capital_under_utilization_threshold * 100:.0f}%；"
                    f"若后续估值转低可考虑加速投入。建议月度定投预算 "
                    f"¥{dca_budget}（按剩余可投资金 ¥{remaining_to_invest} / "
                    f"{dca_months} 个月摊分）。"
                ),
                detail={
                    "utilization": str(utilization),
                    "remaining_to_invest": str(remaining_to_invest),
                    "dca_budget_per_month": str(dca_budget),
                },
            )
        )

    return CapitalDiagnosis(
        invested_value=invested,
        investable_principal=investable,
        capital_utilization=utilization,
        emergency_reserve=portfolio.emergency_reserve,
        monthly_expense=monthly_expense,
        emergency_adequacy_months=adequacy_months,
        dca_budget_per_month=dca_budget,
        signals=signals,
    )
