"""风险诊断（阶段 3）。

指标：
- 单只基金最近 1 年（252 交易日）最大回撤
- 单只基金年化波动率（日收益 std × sqrt(252)）
- 组合按市值加权的 1 年最大回撤 / 年化波动率
- 对 ``settings.stress_scenarios`` 里的每段历史窗口，按持仓权重计算"组合假设损失"

信号：
- ``RISK_EXCEEDS_TOLERANCE`` (high)：任一压力场景组合损失 > ``max_drawdown_tolerance``
- ``DRAWDOWN_LIMIT_APPROACHING`` (warn)：加权 1 年回撤 > 0.8 × tolerance
- ``HIGH_VOLATILITY_TILT`` (info)：加权年化波动率 > 0.30
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from ..models import (
    FundRiskMetric,
    Portfolio,
    RiskDiagnosis,
    Settings,
    Severity,
    Signal,
    StressTestResult,
)

_DEC_ZERO = Decimal("0")
_TRADING_DAYS_1Y = 252
_HIGH_VOL_THRESHOLD = Decimal("0.30")
_APPROACH_FACTOR = Decimal("0.8")


# ---- 纯计算：净值序列 → 指标 ----
def compute_max_drawdown(nav_series: pd.Series) -> Decimal | None:
    """最大回撤（正值 Decimal，如 0.2345 表示 23.45%）。

    序列少于 2 个点返回 None。
    """
    s = pd.to_numeric(nav_series, errors="coerce").dropna()
    if len(s) < 2:
        return None
    peak = s.cummax()
    drawdown = (s - peak) / peak  # 全 <= 0
    mdd = float(-drawdown.min())
    if mdd < 0:  # 数值噪声兜底
        mdd = 0.0
    return Decimal(str(round(mdd, 4)))


def compute_annualized_volatility(nav_series: pd.Series) -> Decimal | None:
    """年化波动率 = 日收益 std × sqrt(252)。不足 5 个点返回 None。"""
    s = pd.to_numeric(nav_series, errors="coerce").dropna()
    if len(s) < 5:
        return None
    returns = s.pct_change().dropna()
    if len(returns) < 2:
        return None
    vol = float(returns.std(ddof=1) * math.sqrt(_TRADING_DAYS_1Y))
    if math.isnan(vol) or vol < 0:
        return None
    return Decimal(str(round(vol, 4)))


def stress_test_loss(
    nav_history: pd.DataFrame, start: date, end: date
) -> Decimal | None:
    """给定历史 NAV（含列 ``净值日期`` 和 ``单位净值``）与窗口，
    返回区间内峰值 → 谷底最大跌幅（正值）。

    若窗口内有效数据 < 2 条，返回 None（代表该基金在当时还未成立/停牌）。
    """
    if nav_history is None or nav_history.empty:
        return None
    if "净值日期" not in nav_history.columns or "单位净值" not in nav_history.columns:
        return None

    df = nav_history.copy()
    # 日期列可能是 datetime.date 或字符串，统一成 date
    df["净值日期"] = df["净值日期"].map(
        lambda d: d if isinstance(d, date) else pd.to_datetime(d).date()
    )
    mask = (df["净值日期"] >= start) & (df["净值日期"] <= end)
    seg = df.loc[mask].sort_values("净值日期")
    if len(seg) < 2:
        return None

    navs = pd.to_numeric(seg["单位净值"], errors="coerce").dropna()
    if len(navs) < 2:
        return None

    peak = navs.cummax()
    drawdown = (navs - peak) / peak
    loss = float(-drawdown.min())
    if loss < 0:
        loss = 0.0
    return Decimal(str(round(loss, 4)))


# ---- 组合层 ----
def _weight_of(market_value: Decimal, total: Decimal) -> Decimal:
    if total <= _DEC_ZERO:
        return _DEC_ZERO
    return (market_value / total).quantize(Decimal("0.0001"))


def _weighted_average(
    pairs: list[tuple[Decimal, Decimal | None]],
) -> Decimal | None:
    """按权重对 (weight, value) 求加权平均；忽略 None。"""
    num = Decimal("0")
    denom = Decimal("0")
    for w, v in pairs:
        if v is None:
            continue
        num += w * v
        denom += w
    if denom <= _DEC_ZERO:
        return None
    return (num / denom).quantize(Decimal("0.0001"))


def diagnose(
    portfolio: Portfolio,
    settings: Settings,
    *,
    nav_histories: dict[str, pd.DataFrame],
) -> RiskDiagnosis:
    """主入口。``nav_histories`` 键为基金代码，值为整段历史 DataFrame。

    找不到的基金不会崩溃——其 metric 与 stress loss 记为 None，并在 data_caveat 说明。
    """
    total = portfolio.total_assets
    fund_metrics: list[FundRiskMetric] = []
    weights: dict[str, Decimal] = {}

    # 取最近 1 年数据用于 max_drawdown / volatility
    for h in portfolio.holdings:
        weights[h.code] = _weight_of(h.market_value, total)
        history = nav_histories.get(h.code)
        caveat: str | None = None
        mdd: Decimal | None = None
        vol: Decimal | None = None

        if history is None or history.empty:
            caveat = "无历史净值数据，跳过风险指标计算"
        else:
            df = history.copy()
            df["净值日期"] = df["净值日期"].map(
                lambda d: d if isinstance(d, date) else pd.to_datetime(d).date()
            )
            df = df.sort_values("净值日期").tail(_TRADING_DAYS_1Y)
            if len(df) < 20:
                caveat = "成立/数据不足，指标仅供参考"
            mdd = compute_max_drawdown(df["单位净值"])
            vol = compute_annualized_volatility(df["单位净值"])

        fund_metrics.append(
            FundRiskMetric(
                fund_code=h.code,
                fund_name=h.name or h.code,
                max_drawdown_1y=mdd,
                annualized_volatility=vol,
                data_caveat=caveat,
            )
        )

    # 组合加权
    weighted_mdd = _weighted_average(
        [(weights[m.fund_code], m.max_drawdown_1y) for m in fund_metrics]
    )
    weighted_vol = _weighted_average(
        [(weights[m.fund_code], m.annualized_volatility) for m in fund_metrics]
    )

    # 压力测试
    stress_results: list[StressTestResult] = []
    tolerance = portfolio.max_drawdown_tolerance
    any_breach = False
    for sc in settings.stress_scenarios:
        fund_losses: dict[str, Decimal | None] = {}
        missing: list[str] = []
        weighted_loss = Decimal("0")
        covered_weight = Decimal("0")

        for h in portfolio.holdings:
            loss = stress_test_loss(nav_histories.get(h.code, pd.DataFrame()), sc.start, sc.end)
            fund_losses[h.code] = loss
            if loss is None:
                missing.append(h.code)
                continue
            w = weights[h.code]
            weighted_loss += w * loss
            covered_weight += w

        # 覆盖权重偏低（比如所有基金都比场景晚成立），只能据实报告
        portfolio_loss = weighted_loss.quantize(Decimal("0.0001"))
        breach = portfolio_loss > tolerance
        if breach:
            any_breach = True
        stress_results.append(
            StressTestResult(
                scenario_name=sc.name,
                start=sc.start,
                end=sc.end,
                portfolio_loss=portfolio_loss,
                breach_tolerance=breach,
                fund_losses=fund_losses,
                missing_funds=missing,
            )
        )

    # 信号
    signals: list[Signal] = []
    if any_breach:
        breach_names = [t.scenario_name for t in stress_results if t.breach_tolerance]
        worst = max(
            (t for t in stress_results if t.breach_tolerance),
            key=lambda t: t.portfolio_loss,
        )
        signals.append(
            Signal(
                code="RISK_EXCEEDS_TOLERANCE",
                severity=Severity.HIGH,
                message=(
                    f"压力测试显示组合在 {'/'.join(breach_names)} 场景下的最大"
                    f"假设损失为 {float(worst.portfolio_loss) * 100:.2f}%，"
                    f"超过你设定的可承受回撤 {float(tolerance) * 100:.0f}%。"
                    "建议立即降低高波动仓位或暂停加仓。"
                ),
                detail={
                    "worst_scenario": worst.scenario_name,
                    "worst_loss": str(worst.portfolio_loss),
                    "tolerance": str(tolerance),
                },
            )
        )

    if weighted_mdd is not None and weighted_mdd > (tolerance * _APPROACH_FACTOR):
        signals.append(
            Signal(
                code="DRAWDOWN_LIMIT_APPROACHING",
                severity=Severity.WARN,
                message=(
                    f"组合近 1 年加权最大回撤 {float(weighted_mdd) * 100:.2f}%，"
                    f"已逼近你的回撤承受阈值 {float(tolerance) * 100:.0f}%。"
                ),
                detail={
                    "weighted_mdd": str(weighted_mdd),
                    "tolerance": str(tolerance),
                },
            )
        )

    if weighted_vol is not None and weighted_vol > _HIGH_VOL_THRESHOLD:
        signals.append(
            Signal(
                code="HIGH_VOLATILITY_TILT",
                severity=Severity.INFO,
                message=(
                    f"组合加权年化波动率 {float(weighted_vol) * 100:.2f}%，"
                    "波动偏高；若对短期波动敏感，可考虑增加债基/货基配置。"
                ),
                detail={"weighted_vol": str(weighted_vol)},
            )
        )

    return RiskDiagnosis(
        fund_metrics=fund_metrics,
        weighted_max_drawdown_1y=weighted_mdd,
        weighted_annualized_volatility=weighted_vol,
        stress_tests=stress_results,
        signals=signals,
    )


__all__: list[str] = [
    "compute_max_drawdown",
    "compute_annualized_volatility",
    "stress_test_loss",
    "diagnose",
]


# 用于类型检查器但不想强制的 Signal.detail 泛型
_ = Any
