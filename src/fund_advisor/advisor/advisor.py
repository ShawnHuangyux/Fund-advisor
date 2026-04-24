"""决策层：联网补齐 → 规则诊断 → LLM 综合。

流程：
1. 对每只持仓调 akshare 补齐 name/fund_type/latest_nav（失败降级为本地数据）
2. 跑 concentration + capital 两个规则模块
3. 如果提供了 DeepSeekClient，调 LLM 生成 today_headline 与 action_items
4. 否则走纯规则降级：给一段简短的总结和保守建议
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Callable

from loguru import logger

import pandas as pd

from ..data.akshare_client import FundDataError, enrich_holding_inplace, get_nav_history
from ..diagnostics import capital, concentration, cost, position, risk, valuation
from ..models import (
    Action,
    ActionItem,
    BUY_ACTIONS,
    DiagnosisReport,
    FundType,
    HoldingSnapshot,
    LLMSynthesis,
    Portfolio,
    PortfolioSummary,
    Settings,
    Severity,
    Signal,
)

try:
    from ..llm import DeepSeekClient, synthesize_diagnosis
except Exception:  # pragma: no cover - openai 未装时兜底
    DeepSeekClient = None  # type: ignore
    synthesize_diagnosis = None  # type: ignore

try:
    from ..data.usage_db import budget_state, current_month_cost
except Exception:  # pragma: no cover - sqlite 理论上一定存在
    budget_state = None  # type: ignore
    current_month_cost = None  # type: ignore


def resolve_portfolio(portfolio: Portfolio, *, progress: Callable[[str], None] | None = None) -> list[dict]:
    """就地联网补齐 name/fund_type 与 latest_nav。返回每只基金的补齐记录。"""
    reports = []
    for h in portfolio.holdings:
        if progress:
            progress(f"查询 {h.code}…")
        reports.append(enrich_holding_inplace(h))
    return reports


def build_summary(portfolio: Portfolio) -> PortfolioSummary:
    today = date.today()
    snapshots = [
        HoldingSnapshot(
            code=h.code,
            name=h.name or h.code,
            fund_type=(h.fund_type or FundType.UNKNOWN).value,
            shares=h.shares,
            cost_price=h.cost_price,
            latest_nav=h.latest_nav,
            latest_nav_date=h.latest_nav_date,
            market_value=h.market_value,
            pnl=h.pnl,
            pnl_pct=h.pnl_pct,
            held_days=(today - h.purchase_date).days,
        )
        for h in portfolio.holdings
    ]
    return PortfolioSummary(
        total_assets=portfolio.total_assets,
        cash=portfolio.cash,
        invested_value=portfolio.invested_value,
        invested_cost=portfolio.invested_cost,
        total_pnl=portfolio.total_pnl,
        principal_total=portfolio.principal_total,
        emergency_reserve=portfolio.emergency_reserve,
        holdings_count=len(portfolio.holdings),
        holdings=snapshots,
    )


def _fallback_synthesis(report: DiagnosisReport) -> tuple[LLMSynthesis, list[ActionItem]]:
    """没有 LLM 时的纯规则兜底。"""
    signals = report.signals
    high_sigs = [s for s in signals if s.severity.value == "high"]
    has_over = any(s.code == "OVER_CONCENTRATED" for s in signals)
    has_emergency = any(s.code == "EMERGENCY_RESERVE_LOW" for s in signals)
    has_under = any(s.code == "CAPITAL_UNDERUTILIZED" for s in signals)

    if has_emergency:
        headline = "应急金不足 3 个月，今日暂停所有加仓类操作，先补应急金。"
    elif has_over:
        headline = "有品种集中度超标，建议暂停对超标品种的加仓，其余持有观察。"
    elif has_under:
        headline = "本金利用率偏低；建议维持定投节奏，不要急于一次性大额投入。"
    else:
        headline = "今日无强信号，建议持有观察。"

    synth = LLMSynthesis(
        today_headline=headline,
        overall_assessment=(
            "纯规则兜底输出：未配置 DeepSeek API Key 或调用失败，"
            "仅基于集中度与资金效率给出结论。"
        ),
        risk_warnings=[s.message for s in high_sigs],
        data_caveats=["本条结论未经 LLM 综合；未结合最新估值温度与压力测试。"],
        alternative_view="如果你对后续市场方向有明确判断，本兜底结论可能过于保守。",
    )

    items: list[ActionItem] = []
    for it in report.concentration_diagnosis.items:
        if it.over_limit:
            items.append(
                ActionItem(
                    fund_code=it.fund_code,
                    fund_name=it.fund_name,
                    action=Action.PAUSE_DCA,
                    amount_rmb=None,
                    priority="medium",
                    rationale=f"集中度超标：{float(it.actual_ratio)*100:.2f}% > {float(it.cap_ratio)*100:.0f}%",
                    alternative_view="如果你是长期看好该品种，也可维持定投但严控金额。",
                )
            )
    # 阶段 2：赎回费阶梯即将降档时强制提醒
    if report.cost_diagnosis:
        for ci in report.cost_diagnosis.items:
            if (
                ci.is_c_class
                and ci.next_tier_days_away is not None
                and ci.next_tier_days_away <= 3
            ):
                items.append(
                    ActionItem(
                        fund_code=ci.fund_code,
                        fund_name=ci.fund_name,
                        action=Action.HOLD_OBSERVE,
                        amount_rmb=None,
                        priority="high",
                        rationale=(
                            f"C 类赎回费阶梯：再等 {ci.next_tier_days_away} 天即可降档"
                            f"（当前费率 {float(ci.current_redemption_fee_rate or 0) * 100:.2f}%）。"
                        ),
                        alternative_view="若急需资金，赎回费仍可承担。",
                    )
                )
    return synth, items


def run_diagnosis(
    portfolio: Portfolio,
    settings: Settings,
    *,
    llm_client: "DeepSeekClient | None" = None,
    llm_mode: str = "deep",
    resolve: bool = True,
    progress: Callable[[str], None] | None = None,
) -> DiagnosisReport:
    """端到端诊断。

    - ``resolve=True``：联网补齐 name/fund_type/latest_nav（默认开）。
    - ``llm_client``：传入则调 LLM 生成今日建议，否则走纯规则兜底。
    """
    if resolve:
        if progress:
            progress("联网补齐基金信息…")
        resolve_portfolio(portfolio, progress=progress)

    if progress:
        progress("跑规则引擎…")
    conc = concentration.diagnose(portfolio, settings)
    cap = capital.diagnose(portfolio, settings)
    pos = position.diagnose(portfolio, settings)
    cst = cost.diagnose(portfolio, settings)
    if progress:
        progress("拉取指数估值…")
    val = valuation.diagnose(portfolio, settings)

    if progress:
        progress("拉取历史净值（用于风险/压力测试）…")
    nav_histories: dict[str, pd.DataFrame] = {}
    for h in portfolio.holdings:
        if h.fund_type == FundType.MONEY:
            nav_histories[h.code] = pd.DataFrame()
            continue
        try:
            rows = get_nav_history(h.code, years=3)
            nav_histories[h.code] = pd.DataFrame(
                [
                    {
                        "净值日期": r["date"],
                        "单位净值": r["nav"],
                        "日增长率": r["daily_change_pct"],
                    }
                    for r in rows
                ]
            )
        except FundDataError as e:
            logger.warning("get_nav_history({}) 失败：{}", h.code, e)
            nav_histories[h.code] = pd.DataFrame()
    rsk = risk.diagnose(portfolio, settings, nav_histories=nav_histories)

    all_signals: list[Signal] = [
        *conc.signals,
        *cap.signals,
        *pos.signals,
        *cst.signals,
        *val.signals,
        *rsk.signals,
    ]

    report = DiagnosisReport(
        generated_at=datetime.now(),
        portfolio_summary=build_summary(portfolio),
        concentration_diagnosis=conc,
        capital_diagnosis=cap,
        position_diagnosis=pos,
        cost_diagnosis=cst,
        valuation_diagnosis=val,
        risk_diagnosis=rsk,
        signals=all_signals,
    )

    # Phase 4：月度预算门槛。block 时 deep 模式强制降级为规则兜底。
    if (
        llm_client is not None
        and budget_state is not None
        and current_month_cost is not None
    ):
        try:
            state = budget_state(settings.llm)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 LLM 账单失败：{}", e)
            state = "ok"
        if state == "block" and llm_mode == "deep":
            cost_now = current_month_cost()
            logger.warning(
                "本月 LLM 成本 ¥{:.2f} 已达阻断线，深度模式禁用，降级为规则兜底",
                float(cost_now),
            )
            report.signals.append(
                Signal(
                    code="LLM_BUDGET_BLOCKED",
                    severity=Severity.WARN,
                    message=(
                        f"本月 LLM 成本 ¥{float(cost_now):.2f} ≥ "
                        f"阻断阈值 ¥{float(settings.llm.monthly_budget_block):.0f}；"
                        "深度模式已禁用，本次走纯规则兜底。"
                    ),
                )
            )
            llm_client = None
        elif state == "warn":
            logger.warning("本月 LLM 成本已过警告线（仅提示，不阻断）")

    if llm_client is not None and synthesize_diagnosis is not None:
        try:
            if progress:
                progress("调用 LLM 综合…")
            synth, items = synthesize_diagnosis(
                portfolio, report, llm_client, mode=llm_mode
            )
            report.llm_synthesis = synth
            report.action_items = items
            return report
        except Exception as e:  # noqa: BLE001
            logger.exception("LLM 综合失败，降级为纯规则：{}", e)

    synth, items = _fallback_synthesis(report)
    report.llm_synthesis = synth
    report.action_items = items
    return report


# 兼容旧入口（阶段 1 测试曾用）
def run_stage1_diagnosis(portfolio: Portfolio, settings: Settings) -> DiagnosisReport:
    return run_diagnosis(portfolio, settings, llm_client=None, resolve=False)
