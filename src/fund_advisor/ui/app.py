"""Streamlit 前端。

⚠️ 本系统仅为信息聚合与辅助分析工具，不构成投资建议。
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from fund_advisor.advisor import (
    advisor as advisor_mod,
)
from fund_advisor.advisor.redemption import estimate_settlement
from fund_advisor.data import load_portfolio, load_settings, save_portfolio
from fund_advisor.data.akshare_client import (
    FundDataError,
    clear_cache,
    get_basic_info,
    get_index_valuation,
    get_latest_nav,
    get_nav_history,
    match_index_symbol,
)
from fund_advisor.models import (
    Action,
    CandidateRequest,
    FundType,
    Holding,
    Portfolio,
    RiskTolerance,
    Settings,
    Strategy,
    TargetAllocation,
    normalize_fund_type,
)

try:
    from fund_advisor.llm import DeepSeekClient, analyze_candidate
except Exception:  # noqa: BLE001
    DeepSeekClient = None  # type: ignore
    analyze_candidate = None  # type: ignore

load_dotenv()

DISCLAIMER = (
    "⚠️ 本系统仅为信息聚合与辅助分析工具，不构成投资建议；"
    "所有决策与风险由使用者自行承担。"
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PORTFOLIO_PATH = PROJECT_ROOT / "config" / "portfolio.yaml"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

ACTION_LABEL = {
    Action.START_DCA: "🟢 启动定投",
    Action.CONTINUE_DCA: "🟢 继续定投",
    Action.INCREASE_DCA: "🟢 加大定投",
    Action.DECREASE_DCA: "🟡 降低定投",
    Action.PAUSE_DCA: "⏸️ 暂停定投",
    Action.LUMP_SUM_ADD: "🟢 一次性加仓",
    Action.HOLD_OBSERVE: "⚪ 持有观察",
    Action.PARTIAL_TAKE_PROFIT: "🔴 部分止盈",
    Action.FULL_REDEEM: "🔴 全部赎回",
    Action.SKIP: "⏭️ 不建议买入",
}

PRIORITY_ICON = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}


# ------------------- 加载器 -------------------
@st.cache_data(show_spinner=False)
def _load_settings_cached(mtime: float) -> Settings:  # noqa: ARG001
    return load_settings(SETTINGS_PATH)


def _get_settings() -> Settings:
    mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else 0.0
    return _load_settings_cached(mtime)


def _get_portfolio() -> Portfolio | None:
    try:
        return load_portfolio(PORTFOLIO_PATH)
    except FileNotFoundError:
        st.error(f"找不到持仓配置：{PORTFOLIO_PATH}")
    except ValidationError as e:
        st.error("持仓配置校验失败：")
        st.code(str(e))
    except Exception as e:  # noqa: BLE001
        st.error(f"加载失败：{type(e).__name__}: {e}")
    return None


def _get_llm_client() -> "DeepSeekClient | None":
    if DeepSeekClient is None:
        return None
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    try:
        client = DeepSeekClient(api_key=key)
    except Exception as e:  # noqa: BLE001
        st.warning(f"DeepSeek 客户端初始化失败：{e}")
        return None
    return client


# ------------------- Tab 1：今日建议 -------------------
def render_today(portfolio: Portfolio, settings: Settings) -> None:
    st.subheader("📣 今日建议")
    st.caption(
        "点击「生成今日建议」：系统会联网补齐基金信息与最新净值，"
        "结合你的现金/应急金/持仓集中度，调用 DeepSeek 给出一段可执行建议。"
    )

    col_a, col_b, col_c = st.columns([1, 1, 2])
    use_llm = col_a.checkbox("使用 LLM 综合", value=True)
    mode = col_b.selectbox(
        "模式",
        ["deep (reasoner)", "light (chat)"],
        index=0,
    )
    mode_key = "deep" if mode.startswith("deep") else "light"

    with col_c:
        if st.button("🔍 生成今日建议", type="primary", use_container_width=True):
            client = _get_llm_client() if use_llm else None
            if use_llm and client is None:
                st.warning(
                    "未检测到 `DEEPSEEK_API_KEY` 或客户端初始化失败，"
                    "将降级为纯规则兜底输出。请在 `.env` 中配置后重试。"
                )

            with st.status("生成中…", expanded=True) as status:
                def _p(msg: str) -> None:
                    status.update(label=msg)

                report = advisor_mod.run_diagnosis(
                    portfolio,
                    settings,
                    llm_client=client,
                    llm_mode=mode_key,
                    resolve=True,
                    progress=_p,
                )
                status.update(label="完成 ✅", state="complete")

            st.session_state["report"] = report
            # 写回 YAML（把 akshare 补齐的 name/fund_type 落盘）
            try:
                save_portfolio(portfolio, PORTFOLIO_PATH)
            except Exception as e:  # noqa: BLE001
                st.warning(f"基金信息补齐未能写回 YAML：{e}")

    report = st.session_state.get("report")
    if report is None:
        st.info("点击上方按钮开始。")
        return

    # ---- 今日结论卡片 ----
    synth = report.llm_synthesis
    if synth:
        st.success(f"### {synth.today_headline}")
        if synth.overall_assessment:
            st.markdown(f"> {synth.overall_assessment}")
        if synth.alternative_view:
            with st.expander("🔁 反面观点（必看）"):
                st.write(synth.alternative_view)

    # ---- 风险 / 数据说明 ----
    if synth and (synth.risk_warnings or synth.data_caveats):
        c1, c2 = st.columns(2)
        with c1:
            if synth.risk_warnings:
                st.markdown("**⚠️ 风险提示**")
                for w in synth.risk_warnings:
                    st.markdown(f"- {w}")
        with c2:
            if synth.data_caveats:
                st.markdown("**📌 数据说明**")
                for d in synth.data_caveats:
                    st.markdown(f"- {d}")

    # ---- 逐只基金建议 ----
    st.markdown("---")
    st.markdown("### 🎯 每只基金的操作建议")
    if not report.action_items:
        st.info("本次没有生成具体动作（可能处于持有观察阶段）。")
    for item in report.action_items:
        label = ACTION_LABEL.get(item.action, item.action.value)
        icon = PRIORITY_ICON.get(item.priority, "•")
        header = f"{icon} {label} · **{item.fund_name or item.fund_code}** ({item.fund_code})"
        if item.amount_rmb:
            header += f" · ¥{item.amount_rmb:,.2f}"
        with st.expander(header, expanded=item.priority == "high"):
            st.markdown(f"**理由**：{item.rationale}")
            if item.llm_comment:
                st.caption(item.llm_comment)
            if item.alternative_view:
                st.markdown(f"**反面观点**：{item.alternative_view}")
            if item.confidence is not None:
                st.progress(item.confidence, text=f"LLM 置信度 {item.confidence:.0%}")
            if item.settlement:
                st.info(f"🗓️ {item.settlement.note}")

    # ---- 规则信号（可折叠） ----
    with st.expander("🔧 规则引擎原始信号 & 数据"):
        if report.signals:
            for s in report.signals:
                icon = {"info": "ℹ️", "warn": "⚠️", "high": "🚨"}.get(s.severity.value, "•")
                st.markdown(
                    f"{icon} **[{s.severity.value.upper()}] {s.code}"
                    + (f" · {s.fund_code}" if s.fund_code else "")
                    + f"** {s.message}"
                )
        else:
            st.success("无规则信号触发。")

        st.markdown("**集中度**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "代码": it.fund_code,
                        "名称": it.fund_name,
                        "识别类别": it.risk_class,
                        "实际占比": f"{float(it.actual_ratio)*100:.2f}%",
                        "上限": f"{float(it.cap_ratio)*100:.2f}%",
                        "超标": "❗" if it.over_limit else "",
                    }
                    for it in report.concentration_diagnosis.items
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

        c = report.capital_diagnosis
        st.markdown(
            f"**资金效率** 利用率 {float(c.capital_utilization)*100:.2f}%、"
            f"应急金 {float(c.emergency_adequacy_months):.2f} 月、"
            f"建议月度定投 ¥{float(c.dca_budget_per_month):,.2f}"
        )

        if report.position_diagnosis:
            st.markdown("**大类仓位 (target 已按 invested/total 等效缩放)**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "类别": b.category,
                            "目标": f"{float(b.target_ratio) * 100:.2f}%",
                            "实际": f"{float(b.actual_ratio) * 100:.2f}%",
                            "偏离": f"{float(b.deviation) * 100:+.2f}%",
                            "金额": float(b.actual_value),
                        }
                        for b in report.position_diagnosis.buckets
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        if report.cost_diagnosis:
            st.markdown("**成本 & 赎回费阶梯**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "代码": it.fund_code,
                            "名称": it.fund_name,
                            "持有天数": it.held_days,
                            "浮盈亏": float(it.pnl),
                            "收益率": f"{float(it.pnl_pct) * 100:+.2f}%",
                            "年化(近似)": (
                                f"{float(it.annualized_return) * 100:+.2f}%"
                                if it.annualized_return is not None
                                else "—"
                            ),
                            "C 类": "✅" if it.is_c_class else "",
                            "当前赎回费": (
                                f"{float(it.current_redemption_fee_rate) * 100:.2f}%"
                                if it.current_redemption_fee_rate is not None
                                else "—"
                            ),
                            "距下一档": (
                                f"{it.next_tier_days_away} 天"
                                if it.next_tier_days_away is not None
                                else "—"
                            ),
                        }
                        for it in report.cost_diagnosis.items
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        if report.valuation_diagnosis:
            st.markdown("**估值温度（近 3 年 PE 分位）**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "代码": it.fund_code,
                            "名称": it.fund_name,
                            "对应指数": it.index_symbol or "—",
                            "PE": float(it.pe) if it.pe is not None else None,
                            "3 年分位": (
                                f"{float(it.pe_percentile) * 100:.1f}%"
                                if it.pe_percentile is not None
                                else "—"
                            ),
                            "温度": it.status.value,
                            "截至": it.as_of.isoformat() if it.as_of else "—",
                            "备注": it.note,
                        }
                        for it in report.valuation_diagnosis.items
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

    with st.expander("📄 完整诊断 JSON（调试用）"):
        st.code(report.model_dump_json(indent=2), language="json")


# ------------------- Tab 2：组合总览 -------------------
def render_overview(portfolio: Portfolio) -> None:
    st.subheader("组合总览")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总资产", f"¥{portfolio.total_assets:,.2f}")
    col2.metric(
        "持仓市值",
        f"¥{portfolio.invested_value:,.2f}",
        f"{portfolio.total_pnl:+,.2f}",
    )
    col3.metric("可用现金", f"¥{portfolio.cash:,.2f}")
    col4.metric("应急储备", f"¥{portfolio.emergency_reserve:,.2f}")

    if not portfolio.holdings:
        st.info("当前没有持仓。可在『持仓管理』Tab 录入。")
        return

    rows = [
        {
            "代码": h.code,
            "名称": h.name or "(待联网补齐)",
            "类型": (h.fund_type.value if h.fund_type else "(待联网补齐)"),
            "份额": float(h.shares),
            "成本价": float(h.cost_price),
            "最新净值": (float(h.latest_nav) if h.latest_nav else None),
            "净值日期": (
                h.latest_nav_date.isoformat() if h.latest_nav_date else ""
            ),
            "市值": float(h.market_value),
            "浮盈亏": float(h.pnl),
            "收益率": f"{float(h.pnl_pct)*100:+.2f}%",
        }
        for h in portfolio.holdings
    ]
    df = pd.DataFrame(rows)

    left, right = st.columns([1, 1])
    with left:
        st.markdown("**资产构成**")
        pie = pd.DataFrame(
            [{"项目": "现金", "金额": float(portfolio.cash)}]
            + [
                {"项目": h.name or h.code, "金额": float(h.market_value)}
                for h in portfolio.holdings
            ]
        )
        fig = px.pie(pie, values="金额", names="项目", hole=0.4)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**持仓明细**")
        st.dataframe(df, use_container_width=True, hide_index=True)


# ------------------- Tab 3：持仓管理（极简录入） -------------------
def _holdings_entry_df(portfolio: Portfolio) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": h.code,
                "shares": float(h.shares),
                "cost_price": float(h.cost_price),
                "purchase_date": h.purchase_date,
                "strategy": h.strategy.value,
                "target_allocation": float(h.target_allocation),
                "notes": h.notes or "",
                "(自动)名称": h.name or "",
                "(自动)类型": (h.fund_type.value if h.fund_type else ""),
            }
            for h in portfolio.holdings
        ]
    )


def render_manage(portfolio: Portfolio) -> None:
    st.subheader("持仓管理")
    st.caption(
        "📝 录入规则：**只需填 code / 份额 / 成本价 / 建仓日 / 策略**。"
        "基金名称和类型由系统联网自动识别；目标占比上限不填默认 10%。"
        "提交后会先备份原 YAML（`config/portfolio.yaml.backup-<时间戳>`）再原子写回。"
    )

    with st.form("portfolio_form"):
        c1, c2, c3 = st.columns(3)
        cash = c1.number_input(
            "可用现金 (¥)", min_value=0.0, value=float(portfolio.cash), step=100.0
        )
        principal = c2.number_input(
            "计划总本金 (¥)", min_value=0.0,
            value=float(portfolio.principal_total), step=1000.0,
        )
        reserve = c3.number_input(
            "应急储备 (¥)", min_value=0.0,
            value=float(portfolio.emergency_reserve), step=500.0,
        )

        c4, c5 = st.columns(2)
        risk = c4.selectbox(
            "风险承受度",
            options=[r.value for r in RiskTolerance],
            index=[r.value for r in RiskTolerance].index(portfolio.risk_tolerance.value),
        )
        drawdown = c5.number_input(
            "最大可承受回撤 (0-1)", 0.0, 1.0,
            float(portfolio.max_drawdown_tolerance), 0.01,
        )

        st.markdown("**目标配置（三项之和 ≈ 1.0）**")
        a1, a2, a3 = st.columns(3)
        eq = a1.number_input("股基", 0.0, 1.0, float(portfolio.target_allocation.equity_fund), 0.05)
        bd = a2.number_input("债基", 0.0, 1.0, float(portfolio.target_allocation.bond_fund), 0.05)
        mm = a3.number_input("货基", 0.0, 1.0, float(portfolio.target_allocation.money_fund), 0.05)

        st.markdown("**持仓**（下表可直接增删改行；**(自动)** 列不用填）")
        edited = st.data_editor(
            _holdings_entry_df(portfolio),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "code": st.column_config.TextColumn("代码*", required=True, help="6 位基金代码，如 017513"),
                "shares": st.column_config.NumberColumn("份额*", min_value=0.0, format="%.4f", required=True),
                "cost_price": st.column_config.NumberColumn("成本价*", min_value=0.0001, format="%.4f", required=True),
                "purchase_date": st.column_config.DateColumn("建仓日*", required=True),
                "strategy": st.column_config.SelectboxColumn("策略", options=[s.value for s in Strategy], default=Strategy.DCA.value),
                "target_allocation": st.column_config.NumberColumn("上限占比", min_value=0.0, max_value=1.0, format="%.4f", default=0.10, help="不填默认 10%"),
                "notes": st.column_config.TextColumn("备注"),
                "(自动)名称": st.column_config.TextColumn("(自动)名称", disabled=True),
                "(自动)类型": st.column_config.TextColumn("(自动)类型", disabled=True),
            },
            disabled=["(自动)名称", "(自动)类型"],
        )

        submitted = st.form_submit_button("💾 保存（不联网）")
        resolve_on_save = st.form_submit_button("🌐 保存并联网补全基金名称/类型")

    if not (submitted or resolve_on_save):
        return

    try:
        new_holdings = []
        for _, row in edited.iterrows():
            if pd.isna(row.get("code")) or str(row["code"]).strip() == "":
                continue
            new_holdings.append(
                Holding(
                    code=str(row["code"]),
                    name=(str(row["(自动)名称"]).strip() or None) if row.get("(自动)名称") is not None else None,
                    fund_type=(FundType(row["(自动)类型"]) if row.get("(自动)类型") else None),
                    shares=Decimal(str(row["shares"])),
                    cost_price=Decimal(str(row["cost_price"])),
                    purchase_date=row["purchase_date"],
                    strategy=Strategy(row.get("strategy") or Strategy.DCA.value),
                    target_allocation=Decimal(str(row.get("target_allocation") or 0.10)),
                    notes=(None if pd.isna(row.get("notes")) or str(row["notes"]).strip() == "" else str(row["notes"])),
                )
            )

        new_portfolio = Portfolio(
            cash=Decimal(str(cash)),
            principal_total=Decimal(str(principal)),
            emergency_reserve=Decimal(str(reserve)),
            risk_tolerance=RiskTolerance(risk),
            max_drawdown_tolerance=Decimal(str(drawdown)),
            target_allocation=TargetAllocation(
                equity_fund=Decimal(str(eq)),
                bond_fund=Decimal(str(bd)),
                money_fund=Decimal(str(mm)),
            ),
            holdings=new_holdings,
        )
    except ValidationError as e:
        st.error("校验失败，未写入：")
        st.code(str(e))
        return
    except Exception as e:  # noqa: BLE001
        st.error(f"转换失败：{type(e).__name__}: {e}")
        return

    if resolve_on_save:
        with st.spinner("联网补全基金名称与类型…"):
            advisor_mod.resolve_portfolio(new_portfolio)

    save_portfolio(new_portfolio, PORTFOLIO_PATH)
    st.success("已写回 config/portfolio.yaml。")
    st.rerun()


# ------------------- Tab 4：候选基金分析 -------------------
def render_candidate(portfolio: Portfolio, settings: Settings) -> None:
    st.subheader("候选基金临时分析")
    st.caption(
        "输入你**正在考虑买入**的基金代码 + 金额，系统会联网查基本信息与最新净值，"
        "结合你当前的现金、应急金、持仓结构，由 LLM 给出「要不要买、怎么买」的建议。"
        "这里**不会**修改你的持仓。"
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    code_input = col1.text_input("基金代码", placeholder="例如 007889")
    amount_input = col2.number_input("意向金额 (¥)", min_value=0.0, value=2000.0, step=500.0)
    mode_input = col3.selectbox("意向方式", ["DCA", "lump_sum"], index=0)

    if st.button("🔍 分析", type="primary"):
        if not code_input.strip():
            st.warning("请输入基金代码。")
            return

        client = _get_llm_client()
        if client is None or analyze_candidate is None:
            st.error("需要配置 DEEPSEEK_API_KEY 才能使用本功能。")
            return

        code = code_input.strip().zfill(6)
        try:
            with st.spinner(f"查询 {code} 基本信息…"):
                basic = get_basic_info(code)
                basic["fund_type_normalized"] = normalize_fund_type(
                    basic.get("fund_type_raw", "")
                ).value
            with st.spinner(f"查询 {code} 最新净值…"):
                try:
                    nav = get_latest_nav(code)
                except FundDataError:
                    nav = None
        except FundDataError as e:
            st.error(f"查询失败：{e}")
            return

        # 资金效率需要给 LLM
        settings_ = settings
        emergency_months = (
            portfolio.emergency_reserve / settings_.capital.monthly_expense_default
            if settings_.capital.monthly_expense_default > 0
            else Decimal("0")
        ).quantize(Decimal("0.01"))

        req = CandidateRequest(
            code=code,
            intended_amount_rmb=Decimal(str(amount_input)),
            intended_mode=mode_input,
        )

        with st.spinner("调用 DeepSeek 分析…"):
            result = analyze_candidate(
                portfolio, req, basic, nav, client,
                emergency_months=emergency_months,
                mode="deep",
            )
        st.session_state["candidate_result"] = result

    result = st.session_state.get("candidate_result")
    if result is None:
        return

    # 展示
    st.markdown("---")
    st.markdown(f"### {result.code} · {result.fund_name}")
    st.caption(
        f"类型：{result.fund_type}｜最新净值：{result.latest_nav} "
        f"({result.latest_nav_date or '—'})"
    )

    if result.should_buy:
        st.success(f"💡 {result.headline}")
    else:
        st.warning(f"💡 {result.headline}")

    label = ACTION_LABEL.get(result.suggested_action, result.suggested_action.value)
    amount_str = (
        f"¥{float(result.suggested_amount_rmb):,.2f}"
        if result.suggested_amount_rmb
        else "—"
    )
    st.markdown(f"**建议动作**：{label}　**金额**：{amount_str}")
    st.markdown(f"**理由**：{result.reasoning}")
    st.markdown(f"**🔁 反面观点**：{result.alternative_view}")

    if result.risk_warnings:
        st.markdown("**⚠️ 风险提示**")
        for w in result.risk_warnings:
            st.markdown(f"- {w}")

    # 如果建议是 LUMP_SUM_ADD 之类，顺手把 T+N 申购提示也给出（买入方向也是 T+1 确认净值）
    try:
        ft = normalize_fund_type(result.fund_type)
        s = estimate_settlement(ft)
        st.info(
            "📅 基金申购同样遵循 T+N 规则："
            f"{s.trade_date.isoformat()} 下单，{s.confirm_date.isoformat()} 按当日净值确认份额。"
        )
    except Exception:  # noqa: BLE001
        pass


# ------------------- Tab：基金明细（净值曲线 + 指数估值） -------------------
@st.cache_data(show_spinner=False, ttl=3600)
def _cached_nav_history(code: str, years: int) -> list[dict]:
    rows = get_nav_history(code, years=years)
    # Decimal 不能直接进 plotly，转 float
    return [
        {"date": r["date"], "nav": float(r["nav"]), "daily_change_pct": float(r["daily_change_pct"])}
        for r in rows
    ]


@st.cache_data(show_spinner=False, ttl=3600)
def _cached_index_valuation(symbol: str) -> dict:
    v = get_index_valuation(symbol)
    return {
        "symbol": v["symbol"],
        "as_of": v["as_of"].isoformat() if v.get("as_of") else None,
        "pe": float(v["pe"]) if v.get("pe") is not None else None,
        "pb": float(v["pb"]) if v.get("pb") is not None else None,
        "pe_percentile": float(v["pe_percentile"]) if v.get("pe_percentile") is not None else None,
    }


def render_fund_detail(portfolio: Portfolio) -> None:
    st.subheader("基金明细")
    st.caption("选择一只持仓，查看近 3 年净值曲线与对应指数估值分位（若能匹配）。")

    if not portfolio.holdings:
        st.info("当前没有持仓。")
        return

    code_options = [f"{h.code} · {h.name or '(待补全)'}" for h in portfolio.holdings]
    selected_label = st.selectbox("选择基金", code_options)
    selected_code = selected_label.split(" · ")[0]
    selected = next((h for h in portfolio.holdings if h.code == selected_code), None)
    if selected is None:
        return

    years = st.slider("净值曲线年限", 1, 5, 3)

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "最新净值",
        f"{selected.latest_nav}" if selected.latest_nav else "—",
        f"截至 {selected.latest_nav_date}" if selected.latest_nav_date else None,
    )
    c2.metric("成本价", f"{selected.cost_price}")
    c3.metric("持有天数", f"{(pd.Timestamp.today().date() - selected.purchase_date).days}")

    # ---- 净值曲线 ----
    try:
        with st.spinner(f"加载 {selected_code} 近 {years} 年净值…"):
            rows = _cached_nav_history(selected_code, years)
    except FundDataError as e:
        st.warning(f"净值曲线不可用：{e}")
        rows = []

    if rows:
        df_nav = pd.DataFrame(rows)
        fig = px.line(df_nav, x="date", y="nav", title=f"{selected_code} 近 {years} 年单位净值")
        fig.add_hline(
            y=float(selected.cost_price),
            line_dash="dash",
            annotation_text=f"成本价 {selected.cost_price}",
            annotation_position="bottom right",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ---- 对应指数估值 ----
    st.markdown("---")
    st.markdown("### 对应指数估值（近 3 年 PE 分位）")
    symbol = match_index_symbol(selected.name or "")
    if symbol is None:
        st.info("该基金名称未匹配到宽基指数，暂不展示估值分位。")
        return

    try:
        with st.spinner(f"加载指数 {symbol} 估值…"):
            val = _cached_index_valuation(symbol)
    except FundDataError as e:
        st.warning(f"指数估值不可用：{e}")
        return

    pe_pct = val.get("pe_percentile")
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("对应指数", symbol)
    cc2.metric("PE", f"{val['pe']}" if val.get("pe") is not None else "—")
    cc3.metric("PB", f"{val['pb']}" if val.get("pb") is not None else "—")
    cc4.metric(
        "3 年分位",
        f"{pe_pct * 100:.1f}%" if pe_pct is not None else "—",
        _temperature_label(pe_pct),
    )
    if val.get("as_of"):
        st.caption(f"数据截至 {val['as_of']}")


def _temperature_label(pe_pct: float | None) -> str | None:
    if pe_pct is None:
        return None
    if pe_pct <= 0.30:
        return "🟢 低估区间"
    if pe_pct < 0.70:
        return "⚪ 正常区间"
    if pe_pct < 0.80:
        return "🟡 偏高"
    return "🔴 过热"


# ------------------- Tab 5：用量与成本 -------------------
def render_usage() -> None:
    st.subheader("用量与成本（本次会话）")
    st.caption(
        "持久化到 SQLite 的计费表将在后续阶段实现；这里只展示**当前 Streamlit 会话内**的累计。"
    )

    client = _get_llm_client()
    if client is None or not client.usage_log:
        st.info("本会话尚未发生 LLM 调用。")
        return

    rows = [
        {
            "#": i + 1,
            "模型": r.model,
            "输入 tokens": r.prompt_tokens,
            "输出 tokens": r.completion_tokens,
            "reasoning tokens": r.reasoning_tokens,
        }
        for i, r in enumerate(client.usage_log)
    ]
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    total_in = sum(r.prompt_tokens for r in client.usage_log)
    total_out = sum(r.completion_tokens for r in client.usage_log)
    c1, c2 = st.columns(2)
    c1.metric("累计输入 tokens", f"{total_in:,}")
    c2.metric("累计输出 tokens", f"{total_out:,}")


# ------------------- 主入口 -------------------
def main() -> None:
    st.set_page_config(
        page_title="基金投资决策助手（个人）", page_icon="📊", layout="wide"
    )
    st.warning(DISCLAIMER)

    settings = _get_settings()
    portfolio = _get_portfolio()

    with st.sidebar:
        st.header("数据与状态")
        st.code(str(PORTFOLIO_PATH.relative_to(PROJECT_ROOT)))
        if portfolio:
            st.metric("总资产", f"¥{portfolio.total_assets:,.2f}")
            st.metric("浮盈亏", f"¥{portfolio.total_pnl:+,.2f}")

        key_ok = bool(os.getenv("DEEPSEEK_API_KEY"))
        st.markdown(f"DeepSeek Key：{'✅ 已配置' if key_ok else '❌ 未配置'}")

        if st.button("🗑️ 清空 akshare 缓存"):
            n = clear_cache()
            st.success(f"已清理 {n} 个缓存文件。")

    tabs = st.tabs(
        ["📣 今日建议", "组合总览", "基金明细", "持仓管理", "候选基金分析", "用量与成本"]
    )
    with tabs[0]:
        if portfolio:
            render_today(portfolio, settings)
    with tabs[1]:
        if portfolio:
            render_overview(portfolio)
    with tabs[2]:
        if portfolio:
            render_fund_detail(portfolio)
    with tabs[3]:
        if portfolio:
            render_manage(portfolio)
    with tabs[4]:
        if portfolio:
            render_candidate(portfolio, settings)
    with tabs[5]:
        render_usage()


if __name__ == "__main__":
    main()
