"""持仓、资金与定投计划相关的数据模型。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class FundType(StrEnum):
    EQUITY = "equity_fund"
    BOND = "bond_fund"
    MONEY = "money_fund"
    HYBRID = "hybrid_fund"
    QDII = "qdii_fund"
    UNKNOWN = "unknown"


class Strategy(StrEnum):
    DCA = "DCA"
    LUMP_SUM = "lump_sum"
    HOLD = "hold"


class RiskTolerance(StrEnum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class PlanFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class TransactionType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"


Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Ratio = Annotated[Decimal, Field(ge=0, le=1, decimal_places=4)]


DEFAULT_TARGET_ALLOCATION = Decimal("0.10")
DEFAULT_MAX_DRAWDOWN_TOLERANCE = Decimal("0.15")


def default_target_allocation() -> "TargetAllocation":
    return TargetAllocation(
        equity_fund=Decimal("0.50"),
        bond_fund=Decimal("0.30"),
        money_fund=Decimal("0.20"),
    )


class CapitalState(BaseModel):
    """组合层的资金状态。"""

    model_config = ConfigDict(extra="forbid")

    available_cash: Money = Field(description="当前还能用于申购基金的现金")
    emergency_reserve: Money = Field(description="明确预留、不参与投资的资金")
    monthly_expense: Money | None = Field(
        default=None,
        description="月度必要支出；不填时回退到 settings.capital.monthly_expense_default",
    )
    target_portfolio_budget: Money | None = Field(
        default=None,
        description="这套组合最终计划投入的规模；可选",
    )

    @model_validator(mode="after")
    def _check_budget(self) -> "CapitalState":
        if (
            self.target_portfolio_budget is not None
            and self.emergency_reserve > self.target_portfolio_budget
        ):
            raise ValueError("emergency_reserve 不应超过 target_portfolio_budget")
        return self


class Holding(BaseModel):
    """当前持仓快照。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(description="基金代码（6 位数字）")
    name: str | None = Field(
        default=None, description="基金简称；留空时由 akshare 自动联网补全"
    )
    fund_type: FundType | None = Field(
        default=None, description="基金大类；留空时由 akshare 自动识别"
    )
    shares: Decimal = Field(ge=0, decimal_places=4, description="当前持有份额")
    average_cost: Decimal = Field(
        gt=0,
        decimal_places=4,
        validation_alias=AliasChoices("average_cost", "cost_price"),
        description="当前加权平均持仓成本",
    )
    notes: str | None = Field(default=None, description="备注")

    # 旧版 schema 兼容字段：继续接受输入，但不再视为新的核心数据模型
    opened_on: date | None = Field(
        default=None,
        validation_alias=AliasChoices("opened_on", "purchase_date"),
        exclude=True,
        description="兼容旧版的首次建仓日",
    )
    legacy_strategy: Strategy | None = Field(
        default=None,
        validation_alias=AliasChoices("strategy"),
        exclude=True,
        description="兼容旧版的 strategy 字段",
    )
    legacy_target_allocation: Ratio = Field(
        default=DEFAULT_TARGET_ALLOCATION,
        validation_alias=AliasChoices("target_allocation"),
        exclude=True,
        description="兼容旧版的单持仓上限占比",
    )

    # 运行时填充（不写入 YAML）
    latest_nav: Decimal | None = Field(
        default=None, exclude=True, description="最新单位净值（运行时由 akshare 填充）"
    )
    latest_nav_date: date | None = Field(
        default=None, exclude=True, description="最新净值对应日期"
    )

    @field_validator("code")
    @classmethod
    def _pad_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) > 6:
            raise ValueError(f"基金代码应为最多 6 位数字，收到 {v!r}")
        return v.zfill(6)

    @field_validator("name", "notes", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def cost_price(self) -> Decimal:
        return self.average_cost

    @property
    def purchase_date(self) -> date | None:
        return self.opened_on

    @property
    def strategy(self) -> Strategy:
        return self.legacy_strategy or Strategy.HOLD

    @property
    def target_allocation(self) -> Decimal:
        return self.legacy_target_allocation

    @property
    def cost_value(self) -> Decimal:
        return (self.shares * self.average_cost).quantize(Decimal("0.01"))

    @property
    def market_value(self) -> Decimal:
        if self.latest_nav is not None and self.latest_nav > 0:
            return (self.shares * self.latest_nav).quantize(Decimal("0.01"))
        return self.cost_value

    @property
    def pnl(self) -> Decimal:
        return (self.market_value - self.cost_value).quantize(Decimal("0.01"))

    @property
    def pnl_pct(self) -> Decimal:
        base = self.cost_value
        if base <= 0:
            return Decimal("0")
        return ((self.market_value - base) / base).quantize(Decimal("0.0001"))


class DCAPlan(BaseModel):
    """当前启用中的定投计划。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(description="基金代码（6 位数字）")
    name: str | None = Field(default=None, description="基金简称；可选")
    fund_type: FundType | None = Field(default=None, description="基金大类；可选")
    amount_rmb: Decimal = Field(gt=0, decimal_places=2, description="每期定投金额")
    frequency: PlanFrequency = Field(default=PlanFrequency.DAILY)
    start_date: date
    enabled: bool = True
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    notes: str | None = Field(default=None)

    @field_validator("code")
    @classmethod
    def _pad_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) > 6:
            raise ValueError(f"基金代码应为最多 6 位数字，收到 {v!r}")
        return v.zfill(6)

    @field_validator("name", "notes", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @model_validator(mode="after")
    def _check_schedule(self) -> "DCAPlan":
        if self.frequency == PlanFrequency.WEEKLY and self.day_of_week is None:
            raise ValueError("weekly 定投计划需要 day_of_week")
        if self.frequency == PlanFrequency.MONTHLY and self.day_of_month is None:
            raise ValueError("monthly 定投计划需要 day_of_month")
        if self.frequency != PlanFrequency.WEEKLY:
            self.day_of_week = None
        if self.frequency != PlanFrequency.MONTHLY:
            self.day_of_month = None
        return self


class TransactionRecord(BaseModel):
    """可选的交易流水（后续高级模式使用）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str
    date: date
    type: TransactionType
    shares: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    amount_rmb: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    nav: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    fee: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    notes: str | None = None

    @field_validator("code")
    @classmethod
    def _pad_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) > 6:
            raise ValueError(f"基金代码应为最多 6 位数字，收到 {v!r}")
        return v.zfill(6)


class TargetAllocation(BaseModel):
    """内部仍保留的大类配置默认值。"""

    model_config = ConfigDict(extra="forbid")

    equity_fund: Ratio = Field(description="股基目标占比（占投资部分）")
    bond_fund: Ratio = Field(description="债基目标占比（占投资部分）")
    money_fund: Ratio = Field(description="货基目标占比（占投资部分）")

    @model_validator(mode="after")
    def _sum_to_one(self) -> "TargetAllocation":
        total = self.equity_fund + self.bond_fund + self.money_fund
        if abs(total - Decimal("1.0")) > Decimal("0.01"):
            raise ValueError(f"target_allocation 三项之和必须 ≈ 1.0（当前 {total}）")
        return self


class Portfolio(BaseModel):
    """用户完整的投资组合配置。"""

    model_config = ConfigDict(extra="forbid")

    capital: CapitalState
    holdings: list[Holding] = Field(default_factory=list)
    dca_plans: list[DCAPlan] = Field(default_factory=list)
    transactions: list[TransactionRecord] = Field(default_factory=list)

    # 当前版本仍保留诊断默认值，但不再要求用户手动维护
    risk_tolerance: RiskTolerance = Field(
        default=RiskTolerance.MODERATE,
        exclude=True,
        description="风险承受度；当前项目默认固定为 moderate",
    )
    max_drawdown_tolerance: Ratio = Field(
        default=DEFAULT_MAX_DRAWDOWN_TOLERANCE,
        exclude=True,
        description="最大可承受回撤（0-1）；当前项目默认固定为 0.15",
    )
    target_allocation: TargetAllocation = Field(
        default_factory=default_target_allocation,
        exclude=True,
        description="目标配置比例；当前项目默认固定为股/债/货 = 50/30/20",
    )

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_schema(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        raw = dict(data)
        capital = dict(raw.get("capital") or {})

        if "available_cash" not in capital and "cash" in raw:
            capital["available_cash"] = raw.pop("cash")
        else:
            raw.pop("cash", None)

        if "emergency_reserve" not in capital and "emergency_reserve" in raw:
            capital["emergency_reserve"] = raw["emergency_reserve"]
        raw.pop("emergency_reserve", None)

        if "target_portfolio_budget" not in capital and "principal_total" in raw:
            capital["target_portfolio_budget"] = raw["principal_total"]
        raw.pop("principal_total", None)

        if "monthly_expense" not in capital and "monthly_expense" in raw:
            capital["monthly_expense"] = raw["monthly_expense"]
        raw.pop("monthly_expense", None)

        raw["capital"] = capital
        raw.setdefault("dca_plans", [])
        raw.setdefault("transactions", [])
        return raw

    @model_validator(mode="after")
    def _check_duplicates(self) -> "Portfolio":
        holding_seen: set[str] = set()
        holding_duplicates: list[str] = []
        for holding in self.holdings:
            if holding.code in holding_seen and holding.code not in holding_duplicates:
                holding_duplicates.append(holding.code)
            holding_seen.add(holding.code)
        if holding_duplicates:
            dup_list = ", ".join(holding_duplicates)
            raise ValueError(f"holdings 中基金代码不能重复：{dup_list}")

        plan_seen: set[str] = set()
        plan_duplicates: list[str] = []
        for plan in self.dca_plans:
            if plan.code in plan_seen and plan.code not in plan_duplicates:
                plan_duplicates.append(plan.code)
            plan_seen.add(plan.code)
        if plan_duplicates:
            dup_list = ", ".join(plan_duplicates)
            raise ValueError(f"dca_plans 中基金代码不能重复：{dup_list}")
        return self

    @property
    def cash(self) -> Decimal:
        return self.capital.available_cash

    @property
    def emergency_reserve(self) -> Decimal:
        return self.capital.emergency_reserve

    @property
    def principal_total(self) -> Decimal:
        if self.capital.target_portfolio_budget is not None:
            return self.capital.target_portfolio_budget
        return (
            self.invested_cost + self.capital.available_cash + self.capital.emergency_reserve
        ).quantize(Decimal("0.01"))

    @property
    def target_portfolio_budget(self) -> Decimal | None:
        return self.capital.target_portfolio_budget

    @property
    def monthly_expense(self) -> Decimal | None:
        return self.capital.monthly_expense

    @property
    def invested_value(self) -> Decimal:
        total = sum((h.market_value for h in self.holdings), start=Decimal("0"))
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def invested_cost(self) -> Decimal:
        total = sum((h.cost_value for h in self.holdings), start=Decimal("0"))
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def total_assets(self) -> Decimal:
        return (self.capital.available_cash + self.invested_value).quantize(Decimal("0.01"))

    @property
    def total_pnl(self) -> Decimal:
        return (self.invested_value - self.invested_cost).quantize(Decimal("0.01"))
