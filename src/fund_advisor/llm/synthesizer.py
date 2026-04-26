"""把规则诊断 + 行情 + 持仓打包喂给 LLM，拿回今日建议。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from loguru import logger

from ..advisor.redemption import estimate_settlement
from ..models import (
    Action,
    ActionItem,
    BUY_ACTIONS,
    CandidateAnalysis,
    CandidateRequest,
    DiagnosisReport,
    FundType,
    LLMSynthesis,
    Portfolio,
    REDEEM_ACTIONS,
    RiskTolerance,
)
from .client import DeepSeekClient
from .prompts import (
    SYSTEM_CANDIDATE,
    SYSTEM_DIAGNOSIS,
    USER_CANDIDATE_TMPL,
    USER_DIAGNOSIS_TMPL,
)


def _fmt(d: Decimal | None, places: int = 2) -> str:
    if d is None:
        return "—"
    return f"{d:.{places}f}"


def _holdings_block(portfolio: Portfolio) -> str:
    lines = []
    today = date.today()
    for h in portfolio.holdings:
        held_days = None if h.purchase_date is None else (today - h.purchase_date).days
        nav_str = (
            f"¥{h.latest_nav} ({h.latest_nav_date})"
            if h.latest_nav
            else "无实时净值，按成本价估算"
        )
        pnl_sign = "+" if h.pnl >= 0 else ""
        held_days_str = f"持有 {held_days} 天" if held_days is not None else "持有天数未知"
        lines.append(
            f"- {h.code} {h.name or '(待查)'} [{(h.fund_type or FundType.UNKNOWN).value}] "
            f"份额 {h.shares}, 成本 ¥{h.cost_price}, 最新 {nav_str}, "
            f"市值 ¥{h.market_value}, 浮盈亏 {pnl_sign}¥{h.pnl} ({pnl_sign}{h.pnl_pct * 100:.2f}%), "
            f"{held_days_str}"
        )
    return "\n".join(lines) if lines else "（当前无持仓）"


def _dca_plans_block(portfolio: Portfolio) -> str:
    if not portfolio.dca_plans:
        return "（当前无定投计划）"
    lines = []
    weekday_labels = {
        0: "周一",
        1: "周二",
        2: "周三",
        3: "周四",
        4: "周五",
        5: "周六",
        6: "周日",
    }
    for plan in portfolio.dca_plans:
        schedule = {
            "daily": "每天",
            "weekly": f"每周 {weekday_labels.get(plan.start_date.weekday(), '—')}",
            "monthly": f"每月 {plan.start_date.day} 日",
        }.get(plan.frequency.value, plan.frequency.value)
        status = "启用中" if plan.enabled else "已停用"
        lines.append(
            f"- {plan.code} {plan.name or '(待查)'}: 每期 ¥{plan.amount_rmb}, {schedule}, "
            f"{status}, 开始于 {plan.start_date}"
        )
    return "\n".join(lines)


def _concentration_block(report: DiagnosisReport) -> str:
    items = report.concentration_diagnosis.items
    if not items:
        return "  （无持仓）"
    lines = []
    for it in items:
        flag = "❗超标" if it.over_limit else "OK"
        lines.append(
            f"  - {it.fund_code} {it.fund_name}: 实际 {float(it.actual_ratio)*100:.2f}%, "
            f"上限 {float(it.cap_ratio)*100:.0f}% [{it.risk_class}] {flag}"
        )
    return "\n".join(lines)


def _signals_block(report: DiagnosisReport) -> str:
    if not report.signals:
        return "（无规则信号触发）"
    return "\n".join(
        f"- [{s.severity.value.upper()}] {s.code}"
        + (f" ({s.fund_code})" if s.fund_code else "")
        + f": {s.message}"
        for s in report.signals
    )


def _position_block(report: DiagnosisReport) -> str:
    pos = report.position_diagnosis
    if pos is None or not pos.buckets:
        return "（无仓位数据）"
    lines = []
    for b in pos.buckets:
        if b.category == "other" and b.actual_value <= 0:
            continue
        flag = ""
        if b.deviation > pos.tolerance:
            flag = " ❗超配"
        elif b.deviation < -pos.tolerance:
            flag = " ↘️ 欠配"
        lines.append(
            f"  - {b.category}: 实际 {float(b.actual_ratio) * 100:.2f}%, "
            f"目标 {float(b.target_ratio) * 100:.2f}%"
            f"（偏离 {float(b.deviation) * 100:+.2f}%）{flag}"
        )
    return "\n".join(lines)


def _cost_block(report: DiagnosisReport) -> str:
    cst = report.cost_diagnosis
    if cst is None or not cst.items:
        return "（无成本数据）"
    lines = []
    for it in cst.items:
        held_days = f"持 {it.held_days} 天" if it.held_days is not None else "持有天数未知"
        ann = (
            f"年化 {float(it.annualized_return) * 100:+.2f}%"
            if it.annualized_return is not None
            else "年化 n/a"
        )
        cclass = ""
        if it.is_c_class:
            cur = f"{float(it.current_redemption_fee_rate or 0) * 100:.2f}%"
            if it.next_tier_days_away is not None:
                cclass = f"，C 类当前赎回费 {cur}，再 {it.next_tier_days_away} 天降档"
            else:
                cclass = f"，C 类当前赎回费 {cur}（已最优）"
        lines.append(
            f"  - {it.fund_code} {it.fund_name}: {held_days}, "
            f"浮盈亏 ¥{it.pnl} ({float(it.pnl_pct) * 100:+.2f}%), {ann}{cclass}"
        )
    return "\n".join(lines)


def _risk_block(report: DiagnosisReport) -> str:
    rd = report.risk_diagnosis
    if rd is None:
        return "  （本次未计算风险指标）"
    lines: list[str] = []
    wmdd = rd.weighted_max_drawdown_1y
    wvol = rd.weighted_annualized_volatility
    lines.append(
        f"  - 加权最大回撤 (1Y): {float(wmdd) * 100:.2f}%"
        if wmdd is not None else "  - 加权最大回撤 (1Y): —"
    )
    lines.append(
        f"  - 加权年化波动率: {float(wvol) * 100:.2f}%"
        if wvol is not None else "  - 加权年化波动率: —"
    )
    for m in rd.fund_metrics:
        mdd_s = (
            f"{float(m.max_drawdown_1y) * 100:.2f}%"
            if m.max_drawdown_1y is not None else "—"
        )
        vol_s = (
            f"{float(m.annualized_volatility) * 100:.2f}%"
            if m.annualized_volatility is not None else "—"
        )
        caveat = f" (⚠️ {m.data_caveat})" if m.data_caveat else ""
        lines.append(
            f"  - {m.fund_code} {m.fund_name}: 回撤 {mdd_s}, 波动 {vol_s}{caveat}"
        )
    for t in rd.stress_tests:
        flag = "❗超阈" if t.breach_tolerance else "ok"
        miss = f"（缺失 {', '.join(t.missing_funds)}）" if t.missing_funds else ""
        lines.append(
            f"  - 场景「{t.scenario_name}」 {t.start}→{t.end}: "
            f"组合假设损失 {float(t.portfolio_loss) * 100:.2f}% [{flag}]{miss}"
        )
    return "\n".join(lines)


def _valuation_block(report: DiagnosisReport) -> str:
    val = report.valuation_diagnosis
    if val is None or not val.items:
        return "（无估值数据）"
    lines = []
    for it in val.items:
        if it.status.value == "unavailable":
            lines.append(f"  - {it.fund_code} {it.fund_name}: 无估值数据 ({it.note})")
            continue
        pe_p = f"{float(it.pe_percentile or 0) * 100:.1f}%" if it.pe_percentile is not None else "—"
        lines.append(
            f"  - {it.fund_code} {it.fund_name} (对应 {it.index_symbol}): "
            f"PE {it.pe}, 3年分位 {pe_p} → {it.status.value} (截至 {it.as_of})"
        )
    return "\n".join(lines)


def _settlement_block(portfolio: Portfolio) -> str:
    lines = []
    for h in portfolio.holdings:
        ft = h.fund_type or FundType.UNKNOWN
        s = estimate_settlement(ft)
        lines.append(f"- {h.code} {h.name or ''}: {s.note}")
    return "\n".join(lines) if lines else "（无持仓）"


def synthesize_diagnosis(
    portfolio: Portfolio,
    report: DiagnosisReport,
    client: DeepSeekClient,
    *,
    mode: str = "deep",
) -> tuple[LLMSynthesis, list[ActionItem]]:
    """调用 LLM 生成今日建议 + ActionItem 列表。"""
    summary = report.portfolio_summary
    cap = report.capital_diagnosis

    user_msg = USER_DIAGNOSIS_TMPL.format(
        cash=_fmt(portfolio.cash),
        invested_cost=_fmt(summary.invested_cost),
        invested_value=_fmt(summary.invested_value),
        total_pnl=_fmt(summary.total_pnl),
        principal_total=_fmt(portfolio.principal_total),
        emergency_reserve=_fmt(portfolio.emergency_reserve),
        risk_tolerance=RiskTolerance.MODERATE.value,
        max_drawdown_tolerance=f"{float(portfolio.max_drawdown_tolerance)*100:.0f}%",
        target_eq=f"{float(portfolio.target_allocation.equity_fund)*100:.0f}%",
        target_bd=f"{float(portfolio.target_allocation.bond_fund)*100:.0f}%",
        target_mm=f"{float(portfolio.target_allocation.money_fund)*100:.0f}%",
        holdings_block=_holdings_block(portfolio),
        dca_plans_block=_dca_plans_block(portfolio),
        utilization=f"{float(cap.capital_utilization)*100:.2f}%",
        util_threshold="50%",
        emergency_months=_fmt(cap.emergency_adequacy_months),
        emergency_min="3",
        dca_budget=_fmt(cap.dca_budget_per_month),
        concentration_block=_concentration_block(report),
        position_block=_position_block(report),
        cost_block=_cost_block(report),
        valuation_block=_valuation_block(report),
        risk_block=_risk_block(report),
        signals_block=_signals_block(report),
        settlement_block=_settlement_block(portfolio),
    )

    parsed, record = client.chat_json(
        system=SYSTEM_DIAGNOSIS, user=user_msg, mode=mode, kind="diagnosis"
    )

    synth = LLMSynthesis(
        today_headline=str(parsed.get("today_headline", ""))[:200],
        overall_assessment=str(parsed.get("overall_assessment", ""))[:500],
        risk_warnings=[str(x) for x in parsed.get("risk_warnings", [])][:10],
        data_caveats=[str(x) for x in parsed.get("data_caveats", [])][:10],
        alternative_view=str(parsed.get("alternative_view", "")),
        model_used=record.model,
        tokens_input=record.prompt_tokens,
        tokens_output=record.completion_tokens,
    )

    # ---- 转 ActionItem，套上硬约束 ----
    emergency_low = any(s.code == "EMERGENCY_RESERVE_LOW" for s in report.signals)
    name_lookup = {h.code: (h.name or h.code, h.fund_type or FundType.UNKNOWN)
                   for h in portfolio.holdings}

    actions: list[ActionItem] = []
    for raw in parsed.get("fund_actions", []):
        try:
            code = str(raw["fund_code"]).strip().zfill(6)
            action = Action(raw["action"])
        except (KeyError, ValueError) as e:
            logger.warning("LLM 返回的 fund_action 无法解析：{} ({})", raw, e)
            continue

        name, ftype = name_lookup.get(code, (code, FundType.UNKNOWN))
        amount = raw.get("amount_rmb")
        amount_dec = None if amount in (None, "", "null") else Decimal(str(amount))

        # 硬约束：应急金告急 → 把加仓类动作强制转为 PAUSE_DCA
        if emergency_low and action in BUY_ACTIONS:
            logger.warning(
                "应急金告急，强制把 {} 的 {} 降级为 PAUSE_DCA", code, action.value
            )
            action = Action.PAUSE_DCA
            amount_dec = None

        settlement = None
        if action in REDEEM_ACTIONS:
            settlement = estimate_settlement(ftype)

        actions.append(
            ActionItem(
                fund_code=code,
                fund_name=name,
                action=action,
                amount_rmb=amount_dec,
                priority=raw.get("priority", "medium"),
                rationale=str(raw.get("reasoning", ""))[:300],
                alternative_view=str(raw.get("alternative_view", ""))[:300],
                confidence=_safe_float(raw.get("confidence")),
                llm_comment=None,
                settlement=settlement,
            )
        )

    return synth, actions


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return None


def _safe_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return bool(v)
    if isinstance(v, str):
        normalized = v.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n", ""}:
            return False
    return None


def analyze_candidate(
    portfolio: Portfolio,
    req: CandidateRequest,
    basic_info: dict,
    nav_info: dict | None,
    client: DeepSeekClient,
    emergency_months: Decimal,
    *,
    mode: str = "deep",
) -> CandidateAnalysis:
    """分析一只用户**尚未持有**的基金：值不值得买、怎么买。"""
    user_msg = USER_CANDIDATE_TMPL.format(
        cash=_fmt(portfolio.cash),
        emergency_reserve=_fmt(portfolio.emergency_reserve),
        emergency_months=_fmt(emergency_months),
        holdings_codes=", ".join(h.code for h in portfolio.holdings) or "无",
        holdings_count=len(portfolio.holdings),
        invested_value=_fmt(portfolio.invested_value),
        code=req.code,
        name=basic_info.get("name", ""),
        fund_type_raw=basic_info.get("fund_type_raw", "未知"),
        fund_type=basic_info.get("fund_type_normalized", "unknown"),
        latest_nav=(str(nav_info["nav"]) if nav_info else "未获取到"),
        latest_nav_date=(nav_info["nav_date"].isoformat() if nav_info else "—"),
        intended_mode=req.intended_mode,
        intended_amount=_fmt(req.intended_amount_rmb),
    )

    parsed, record = client.chat_json(
        system=SYSTEM_CANDIDATE, user=user_msg, mode=mode, kind="candidate"
    )

    try:
        action = Action(parsed.get("suggested_action", "HOLD_OBSERVE"))
    except ValueError:
        action = Action.HOLD_OBSERVE

    amount = parsed.get("suggested_amount_rmb")
    amount_dec = None if amount in (None, "", "null") else Decimal(str(amount))
    should_buy = _safe_bool(parsed.get("should_buy"))

    return CandidateAnalysis(
        code=req.code,
        fund_name=basic_info.get("name", req.code),
        fund_type=basic_info.get("fund_type_raw", "未知"),
        latest_nav=(nav_info["nav"] if nav_info else None),
        latest_nav_date=(nav_info["nav_date"] if nav_info else None),
        headline=str(parsed.get("headline", ""))[:200],
        should_buy=False if should_buy is None else should_buy,
        suggested_action=action,
        suggested_amount_rmb=amount_dec,
        reasoning=str(parsed.get("reasoning", ""))[:500],
        alternative_view=str(parsed.get("alternative_view", "")),
        risk_warnings=[str(x) for x in parsed.get("risk_warnings", [])][:10],
    )
