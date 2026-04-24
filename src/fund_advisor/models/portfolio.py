"""持仓与组合相关的 Pydantic 数据模型。

设计目标：用户只填 code / shares / cost_price / purchase_date，其它元数据（name/
fund_type）由系统联网补齐，目标占比上限没填就给保守默认 10%。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Ratio = Annotated[Decimal, Field(ge=0, le=1, decimal_places=4)]


DEFAULT_TARGET_ALLOCATION = Decimal("0.10")


class Holding(BaseModel):
    """单只基金持仓。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(description="基金代码（6 位数字）")
    name: str | None = Field(
        default=None, description="基金简称；留空时由 akshare 自动联网补全"
    )
    fund_type: FundType | None = Field(
        default=None, description="基金大类；留空时由 akshare 自动识别"
    )
    shares: Decimal = Field(ge=0, decimal_places=4, description="当前持有份额")
    cost_price: Decimal = Field(gt=0, decimal_places=4, description="加权持有成本价")
    purchase_date: date = Field(description="首次建仓日期（用于 C 类赎回费阶梯）")
    target_allocation: Ratio = Field(
        default=DEFAULT_TARGET_ALLOCATION,
        description="在组合中的目标占比上限，留空默认 0.10",
    )
    strategy: Strategy = Field(default=Strategy.DCA, description="策略：定投/一次性/持有")
    notes: str | None = Field(default=None, description="备注")

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

    @field_validator("name", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def cost_value(self) -> Decimal:
        """按成本价估算的持仓市值（兜底，当没有实时净值时使用）。"""
        return (self.shares * self.cost_price).quantize(Decimal("0.01"))

    @property
    def market_value(self) -> Decimal:
        """当前市值：优先用 latest_nav，没有时回退到成本价。"""
        if self.latest_nav is not None and self.latest_nav > 0:
            return (self.shares * self.latest_nav).quantize(Decimal("0.01"))
        return self.cost_value

    @property
    def pnl(self) -> Decimal:
        """浮动盈亏 = 市值 - 成本。"""
        return (self.market_value - self.cost_value).quantize(Decimal("0.01"))

    @property
    def pnl_pct(self) -> Decimal:
        base = self.cost_value
        if base <= 0:
            return Decimal("0")
        return ((self.market_value - base) / base).quantize(Decimal("0.0001"))


class TargetAllocation(BaseModel):
    """目标配置比例（三大类）。

    ⚠️ 这三项表示**投资部分**（invested_value，不含现金）的目标比例，
    三项之和 ≈ 1.0。``diagnostics/position.py`` 会按当前
    ``invested_value / total_assets`` 自动缩放成"含现金口径"的等效目标，
    避免因定投未到位（现金未投完）就触发欠配告警。

    UI 持仓管理 tab 展示的也是这个"投资部分占比"口径；诊断报告的仓位
    表格则同时展示原始目标和缩放后的等效目标。
    """

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

    cash: Money = Field(description="可用现金")
    principal_total: Money = Field(description="计划投入的总本金")
    emergency_reserve: Money = Field(description="应急储备金，不参与投资")
    risk_tolerance: RiskTolerance = Field(description="风险承受度")
    max_drawdown_tolerance: Ratio = Field(description="最大可承受回撤（0-1）")
    target_allocation: TargetAllocation = Field(description="目标配置比例")
    holdings: list[Holding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_reserve(self) -> "Portfolio":
        if self.emergency_reserve > self.principal_total:
            raise ValueError("emergency_reserve 不应超过 principal_total")
        return self

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
        return (self.cash + self.invested_value).quantize(Decimal("0.01"))

    @property
    def total_pnl(self) -> Decimal:
        return (self.invested_value - self.invested_cost).quantize(Decimal("0.01"))
