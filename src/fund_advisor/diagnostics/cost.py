"""成本诊断（规格文档 §3）。

对每只持仓输出：浮盈亏、收益率、持有天数、简化年化收益（近似 IRR）以及
**C 类份额赎回费阶梯提醒**（持有 < 7 天 / 7-30 天 / ≥30 天 三档）。

简化说明（阶段 2）：
- IRR 严格实现需要依赖 transactions.yaml 里的多次买入流水。此处给出"单次建仓等
  效年化"作为近似：r = (market/cost)^(365/held_days) - 1。后续阶段 5 再补真 IRR。
- C 类识别：基金名称以 "C" 结尾或含 "C类"；其它按规格走默认费率表。
- 关键信号：距离满 30 天还有 1~3 天时触发 REDEMPTION_FEE_TIER_IMMINENT（高优，
  让用户知道再等两天能免 0.5% 赎回费，对 C 类定投者非常值钱）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..models import (
    CostDiagnosis,
    CostItem,
    FundType,
    Portfolio,
    Settings,
    Severity,
    Signal,
)
from ..models.settings import CClassFeeTier

_DEC_ZERO = Decimal("0")
_ONE = Decimal("1")


def _is_c_class(name: str) -> bool:
    if not name:
        return False
    s = name.strip()
    # 末尾带 C 或名称含 "C类"
    return s.endswith("C") or "C类" in s or s.endswith("C)")


def _resolve_fee_tiers(
    code: str, settings: Settings
) -> list[CClassFeeTier]:
    """优先取 overrides[code]，否则走 default_c_class。"""
    overrides = settings.redemption_fees.overrides.get(code)
    if overrides:
        return overrides
    return settings.redemption_fees.default_c_class


def _current_tier(tiers: list[CClassFeeTier], held_days: int) -> CClassFeeTier | None:
    for t in tiers:
        lo = t.min_days
        hi = t.max_days if t.max_days is not None else 10**9
        if lo <= held_days <= hi:
            return t
    return None


def _next_better_tier_start(
    tiers: list[CClassFeeTier], held_days: int
) -> int | None:
    """返回下一个更低费率阶梯的起始天数；若已处于最低（0）或无更优，返回 None。"""
    current = _current_tier(tiers, held_days)
    if current is None or current.rate <= _DEC_ZERO:
        return None
    # 找 min_days > held_days 且 rate < current.rate 的最小阶梯
    better = [t for t in tiers if t.min_days > held_days and t.rate < current.rate]
    if not better:
        return None
    better.sort(key=lambda t: t.min_days)
    return better[0].min_days


def _annualized_return(
    cost_value: Decimal, market_value: Decimal, held_days: int
) -> Decimal | None:
    """(market/cost)^(365/d) - 1；d≤0 或 cost≤0 返回 None。"""
    if held_days <= 0 or cost_value <= 0:
        return None
    try:
        ratio = float(market_value) / float(cost_value)
        if ratio <= 0:
            return None
        ann = ratio ** (365.0 / held_days) - 1.0
        return Decimal(str(ann)).quantize(Decimal("0.0001"))
    except (OverflowError, ValueError, ZeroDivisionError):
        return None


def diagnose(portfolio: Portfolio, settings: Settings) -> CostDiagnosis:
    today = date.today()
    items: list[CostItem] = []
    signals: list[Signal] = []

    for h in portfolio.holdings:
        held_days = None if h.purchase_date is None else max(0, (today - h.purchase_date).days)
        name = h.name or h.code
        is_c = _is_c_class(name)

        current_rate: Decimal | None = None
        next_tier_days_away: int | None = None

        if is_c and held_days is not None:
            tiers = _resolve_fee_tiers(h.code, settings)
            current = _current_tier(tiers, held_days)
            current_rate = current.rate if current else None
            next_start = _next_better_tier_start(tiers, held_days)
            if next_start is not None:
                next_tier_days_away = max(0, next_start - held_days)

                # 信号：还差 ≤3 天即可降档 → 高优提醒
                if next_tier_days_away <= 3:
                    signals.append(
                        Signal(
                            code="REDEMPTION_FEE_TIER_IMMINENT",
                            severity=Severity.HIGH,
                            fund_code=h.code,
                            message=(
                                f"{name} ({h.code}) 再持有 {next_tier_days_away} 天即可进入"
                                f"更低赎回费阶梯（当前 {float(current_rate or 0) * 100:.2f}%，"
                                f"满 {next_start} 天后降至 "
                                f"{float(_lower_rate(tiers, next_start)) * 100:.2f}%）。"
                                f"若有赎回计划，强烈建议再等几天。"
                            ),
                            detail={
                                "held_days": held_days,
                                "next_tier_days_away": next_tier_days_away,
                                "current_rate": str(current_rate or 0),
                            },
                        )
                    )
                elif next_tier_days_away <= 14:
                    signals.append(
                        Signal(
                            code="REDEMPTION_FEE_TIER_NEAR",
                            severity=Severity.INFO,
                            fund_code=h.code,
                            message=(
                                f"{name} ({h.code}) 再持有 {next_tier_days_away} 天可进入更低赎回费阶梯。"
                            ),
                            detail={
                                "held_days": held_days,
                                "next_tier_days_away": next_tier_days_away,
                            },
                        )
                    )

        ann = (
            _annualized_return(h.cost_value, h.market_value, held_days)
            if held_days is not None
            else None
        )

        items.append(
            CostItem(
                fund_code=h.code,
                fund_name=name,
                fund_type=(h.fund_type or FundType.UNKNOWN).value,
                shares=h.shares,
                cost_price=h.cost_price,
                latest_nav=h.latest_nav,
                market_value=h.market_value,
                cost_value=h.cost_value,
                pnl=h.pnl,
                pnl_pct=h.pnl_pct,
                held_days=held_days,
                annualized_return=ann,
                is_c_class=is_c,
                current_redemption_fee_rate=current_rate,
                next_tier_days_away=next_tier_days_away,
            )
        )

    return CostDiagnosis(items=items, signals=signals)


def _lower_rate(tiers: list[CClassFeeTier], start_day: int) -> Decimal:
    """辅助：取 min_days==start_day 的那档费率；不存在返回 0。"""
    for t in tiers:
        if t.min_days == start_day:
            return t.rate
    return _DEC_ZERO
