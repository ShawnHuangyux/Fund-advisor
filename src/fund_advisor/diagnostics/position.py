"""大类仓位诊断（规格文档 §1）。

把组合拆成：equity_fund / bond_fund / money_fund / cash / other 五桶，
对比 portfolio.target_allocation（不含 cash），偏离超过 ±tolerance 触发信号：
- 股基超配 + 现金充足 → OVER_ALLOCATED_EQUITY（建议暂停/降低股基加仓）
- 股基欠配 + 现金充足 → UNDER_ALLOCATED_EQUITY（建议加仓）

注意：spec 里的 target_allocation 是"投资组合里"三类之和=1 的比例；这里的
actual_ratio 分母取 total_assets（含 cash），所以比较时要把 target 做等效换算。
我们采用一种简化口径：把 cash 视作"待分配"，三类的 target 合起来占 invested_value
而不是 total_assets。因此：
    effective_target[bucket] = target_allocation[bucket] * (invested_value / total_assets)
此口径下，若投入已达计划本金，数值与原口径一致；现金未投完时信号会更温和。
"""

from __future__ import annotations

from decimal import Decimal

from ..models import (
    AllocationBucket,
    FundType,
    Portfolio,
    PositionDiagnosis,
    Settings,
    Severity,
    Signal,
)

_DEC_ZERO = Decimal("0")
_CATEGORIES = ("equity_fund", "bond_fund", "money_fund")


def _bucket_of(fund_type: FundType | None) -> str:
    if fund_type == FundType.BOND:
        return "bond_fund"
    if fund_type == FundType.MONEY:
        return "money_fund"
    if fund_type in (FundType.EQUITY, FundType.HYBRID, FundType.QDII):
        return "equity_fund"
    return "other"


def diagnose(portfolio: Portfolio, settings: Settings) -> PositionDiagnosis:
    total = portfolio.total_assets
    tolerance = settings.allocation_bands.tolerance

    values: dict[str, Decimal] = {
        "equity_fund": _DEC_ZERO,
        "bond_fund": _DEC_ZERO,
        "money_fund": _DEC_ZERO,
        "cash": portfolio.cash,
        "other": _DEC_ZERO,
    }
    for h in portfolio.holdings:
        bucket = _bucket_of(h.fund_type)
        values[bucket] = values[bucket] + h.market_value

    buckets: list[AllocationBucket] = []
    signals: list[Signal] = []

    if total <= _DEC_ZERO:
        return PositionDiagnosis(
            total_assets=total, tolerance=tolerance, buckets=[], signals=[]
        )

    # 等效目标：把 cash 视作待分配 → target 三类按 invested_value/total_assets 缩放
    invested = portfolio.invested_value
    scale = (invested / total).quantize(Decimal("0.0001")) if total > 0 else _DEC_ZERO

    target_map = {
        "equity_fund": portfolio.target_allocation.equity_fund * scale,
        "bond_fund": portfolio.target_allocation.bond_fund * scale,
        "money_fund": portfolio.target_allocation.money_fund * scale,
        "cash": Decimal("1.0") - scale,
        "other": _DEC_ZERO,
    }

    cash_abundant = portfolio.cash >= portfolio.emergency_reserve  # 宽松近似

    for cat in ("equity_fund", "bond_fund", "money_fund", "cash", "other"):
        actual_ratio = (values[cat] / total).quantize(Decimal("0.0001"))
        target_ratio = Decimal(target_map[cat]).quantize(Decimal("0.0001"))
        deviation = (actual_ratio - target_ratio).quantize(Decimal("0.0001"))
        buckets.append(
            AllocationBucket(
                category=cat,
                target_ratio=target_ratio,
                actual_ratio=actual_ratio,
                deviation=deviation,
                actual_value=values[cat].quantize(Decimal("0.01")),
            )
        )

        # 信号仅对三大基金类发出
        if cat not in _CATEGORIES:
            continue
        if deviation > tolerance:
            signals.append(
                Signal(
                    code=f"OVER_ALLOCATED_{cat.upper()}",
                    severity=Severity.WARN,
                    fund_code=None,
                    message=(
                        f"{cat} 实际占比 {float(actual_ratio) * 100:.2f}%，"
                        f"超过目标 {float(target_ratio) * 100:.2f}% 超过容忍带 "
                        f"{float(tolerance) * 100:.0f}%；建议暂停该类加仓。"
                    ),
                    detail={
                        "actual_ratio": str(actual_ratio),
                        "target_ratio": str(target_ratio),
                        "deviation": str(deviation),
                    },
                )
            )
        elif deviation < -tolerance and cash_abundant:
            signals.append(
                Signal(
                    code=f"UNDER_ALLOCATED_{cat.upper()}",
                    severity=Severity.INFO,
                    fund_code=None,
                    message=(
                        f"{cat} 实际占比 {float(actual_ratio) * 100:.2f}%，"
                        f"低于目标 {float(target_ratio) * 100:.2f}% 超过容忍带 "
                        f"{float(tolerance) * 100:.0f}%；若估值合理可考虑加仓。"
                    ),
                    detail={
                        "actual_ratio": str(actual_ratio),
                        "target_ratio": str(target_ratio),
                        "deviation": str(deviation),
                    },
                )
            )

    return PositionDiagnosis(
        total_assets=total,
        tolerance=tolerance,
        buckets=buckets,
        signals=signals,
    )
