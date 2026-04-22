"""诊断报告相关的 Pydantic 模型。

面向"给金融小白的每日操作卡片"：
- ``DiagnosisReport.today_recommendation`` 是 UI 要高亮的那段话（来自 LLM）。
- ``ActionItem`` 是可执行建议，若涉及赎回自带 T+N 结算信息。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    HIGH = "high"


class Action(StrEnum):
    """基金专用 action space（规格文档 §Action Space）。"""

    START_DCA = "START_DCA"
    CONTINUE_DCA = "CONTINUE_DCA"
    INCREASE_DCA = "INCREASE_DCA"
    DECREASE_DCA = "DECREASE_DCA"
    PAUSE_DCA = "PAUSE_DCA"
    LUMP_SUM_ADD = "LUMP_SUM_ADD"
    HOLD_OBSERVE = "HOLD_OBSERVE"
    PARTIAL_TAKE_PROFIT = "PARTIAL_TAKE_PROFIT"
    FULL_REDEEM = "FULL_REDEEM"
    SKIP = "SKIP"  # 用于候选基金：不建议买入


# 加仓/买入类动作（用于 emergency reserve 阻断）
BUY_ACTIONS = {
    Action.START_DCA,
    Action.CONTINUE_DCA,
    Action.INCREASE_DCA,
    Action.LUMP_SUM_ADD,
}

# 赎回类动作（必带 settlement 信息）
REDEEM_ACTIONS = {Action.PARTIAL_TAKE_PROFIT, Action.FULL_REDEEM}


class Settlement(BaseModel):
    """T+N 结算信息。"""

    model_config = ConfigDict(extra="forbid")

    trade_date: date = Field(description="下单日（T）")
    confirm_date: date = Field(description="净值确认日（T+1 一般，15:00 前下单取当日）")
    available_earliest: date = Field(description="资金最早到账日")
    available_latest: date = Field(description="资金最晚到账日")
    note: str = Field(description="人类可读的结算提示")


class Signal(BaseModel):
    """规则引擎触发的单条信号。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    fund_code: str | None = None
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ActionItem(BaseModel):
    """最终建议的可执行动作。"""

    model_config = ConfigDict(extra="forbid")

    fund_code: str = Field(description="目标基金代码；组合层动作填 'PORTFOLIO'")
    fund_name: str | None = None
    action: Action
    amount_rmb: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    priority: Literal["high", "medium", "low"]
    rationale: str = Field(description="规则依据")
    llm_comment: str | None = None
    alternative_view: str | None = Field(
        default=None, description="LLM 强制给出的反面意见"
    )
    confidence: float | None = Field(default=None, ge=0, le=1)
    settlement: Settlement | None = Field(
        default=None, description="赎回类动作附带的 T+N 结算信息"
    )


class ConcentrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: str
    fund_name: str
    risk_class: str
    cap_ratio: Decimal
    actual_ratio: Decimal
    over_limit: bool


class ConcentrationDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConcentrationItem] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)


class CapitalDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invested_value: Decimal
    investable_principal: Decimal
    capital_utilization: Decimal
    emergency_reserve: Decimal
    monthly_expense: Decimal
    emergency_adequacy_months: Decimal
    dca_budget_per_month: Decimal
    signals: list[Signal] = Field(default_factory=list)


# ---- 仓位（大类配置）诊断 ----
class AllocationBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(description="equity_fund / bond_fund / money_fund / cash / other")
    target_ratio: Decimal
    actual_ratio: Decimal
    deviation: Decimal = Field(description="actual - target（正=超配，负=欠配）")
    actual_value: Decimal


class PositionDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_assets: Decimal
    tolerance: Decimal = Field(description="偏离容忍带（±）")
    buckets: list[AllocationBucket] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)


# ---- 成本诊断 ----
class CostItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: str
    fund_name: str
    fund_type: str
    shares: Decimal
    cost_price: Decimal
    latest_nav: Decimal | None = None
    market_value: Decimal
    cost_value: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    held_days: int
    annualized_return: Decimal | None = Field(
        default=None, description="简单年化收益率（近似 IRR）"
    )
    is_c_class: bool = Field(description="是否 C 类份额（名称末尾含 'C'）")
    current_redemption_fee_rate: Decimal | None = Field(
        default=None, description="当前赎回费率（按持有天数落在哪个阶梯）"
    )
    next_tier_days_away: int | None = Field(
        default=None, description="距离下一个更优赎回费阶梯还需持有的天数（None 表示已最优）"
    )


class CostDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CostItem] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)


# ---- 估值诊断 ----
class ValuationStatus(StrEnum):
    LOW = "low"              # ≤30 分位
    NORMAL = "normal"        # 30-70
    HIGH = "high"            # 70-80
    OVERHEATED = "overheated"  # ≥80
    UNAVAILABLE = "unavailable"  # 没有可用数据


class ValuationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: str
    fund_name: str
    index_symbol: str | None = Field(default=None, description="映射到的指数名（如 '沪深300'）")
    pe: Decimal | None = None
    pb: Decimal | None = None
    pe_percentile: Decimal | None = Field(default=None, description="0-1 之间，近 3 年分位")
    status: ValuationStatus = ValuationStatus.UNAVAILABLE
    as_of: date | None = None
    note: str = ""


class ValuationDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ValuationItem] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)


class HoldingSnapshot(BaseModel):
    """给 UI/LLM 展示用的单只基金快照。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    fund_type: str
    shares: Decimal
    cost_price: Decimal
    latest_nav: Decimal | None = None
    latest_nav_date: date | None = None
    market_value: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    held_days: int


class PortfolioSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_assets: Decimal
    cash: Decimal
    invested_value: Decimal
    invested_cost: Decimal
    total_pnl: Decimal
    principal_total: Decimal
    emergency_reserve: Decimal
    holdings_count: int
    holdings: list[HoldingSnapshot] = Field(default_factory=list)


class LLMSynthesis(BaseModel):
    """LLM 综合意见。"""

    model_config = ConfigDict(extra="forbid")

    today_headline: str = Field(description="一句话：今天组合整体怎么办")
    overall_assessment: str = Field(description="100 字以内的整体评估")
    risk_warnings: list[str] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)
    alternative_view: str = Field(description="强制的反面意见")
    model_used: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None


class DiagnosisReport(BaseModel):
    """完整诊断报告。"""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    portfolio_summary: PortfolioSummary
    concentration_diagnosis: ConcentrationDiagnosis
    capital_diagnosis: CapitalDiagnosis
    position_diagnosis: PositionDiagnosis | None = None
    cost_diagnosis: CostDiagnosis | None = None
    valuation_diagnosis: ValuationDiagnosis | None = None
    signals: list[Signal] = Field(default_factory=list)
    llm_synthesis: LLMSynthesis | None = None
    action_items: list[ActionItem] = Field(default_factory=list)


# ---- 候选基金分析（"未持有基金"的临时诊断）----
class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    intended_amount_rmb: Decimal = Field(ge=0, decimal_places=2)
    intended_mode: Literal["lump_sum", "DCA"] = "DCA"


class CandidateAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    fund_name: str
    fund_type: str
    latest_nav: Decimal | None = None
    latest_nav_date: date | None = None
    headline: str
    should_buy: bool
    suggested_action: Action
    suggested_amount_rmb: Decimal | None = None
    reasoning: str
    alternative_view: str
    risk_warnings: list[str] = Field(default_factory=list)
