"""策略参数与阈值配置模型（读取 config/settings.yaml）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ConcentrationCaps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_volatility: Decimal = Field(default=Decimal("0.15"))
    broad_index: Decimal = Field(default=Decimal("0.30"))
    bond: Decimal = Field(default=Decimal("0.40"))
    money: Decimal = Field(default=Decimal("1.00"), description="货基不限，用 1.0 表达")
    unknown: Decimal = Field(default=Decimal("0.15"), description="未匹配走保守 15%")


class ConcentrationKeywords(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_volatility: list[str] = Field(default_factory=list)
    broad_index: list[str] = Field(default_factory=list)


class AllocationBands(BaseModel):
    """大类配置偏离容忍带（阶段 2 仓位诊断用）。"""

    model_config = ConfigDict(extra="forbid")

    tolerance: Decimal = Field(default=Decimal("0.05"), description="±5%")


class CapitalThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_expense_default: Decimal = Field(
        default=Decimal("8000"), description="每月生活支出默认值"
    )
    emergency_min_months: Decimal = Field(
        default=Decimal("3"), description="应急金最低月数"
    )
    default_dca_months: Decimal = Field(
        default=Decimal("12"), description="计划定投月数"
    )
    capital_under_utilization_threshold: Decimal = Field(
        default=Decimal("0.50"), description="本金利用率低于此值发 info"
    )


class CClassFeeTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_days: int
    max_days: int | None = Field(default=None, description="None 表示无上限")
    rate: Decimal


class RedemptionFees(BaseModel):
    """C 类基金赎回费阶梯（阶段 2 成本诊断用；阶段 1 先加载进来保证 schema 稳定）。"""

    model_config = ConfigDict(extra="forbid")

    default_c_class: list[CClassFeeTier] = Field(default_factory=list)
    overrides: dict[str, list[CClassFeeTier]] = Field(
        default_factory=dict, description="按基金代码覆盖的赎回费阶梯"
    )


class StressScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start: date
    end: date


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="deepseek")
    mode: str = Field(default="deep")
    monthly_budget_warn: Decimal = Field(default=Decimal("80"))
    monthly_budget_block: Decimal = Field(default=Decimal("100"))


class SchedulerSettings(BaseModel):
    """阶段 5：APScheduler 每日定时任务配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timezone: str = Field(default="Asia/Shanghai")
    cron_hour: int = Field(default=16, ge=0, le=23)
    cron_minute: int = Field(default=30, ge=0, le=59)
    day_of_week: str = Field(
        default="mon-fri", description="APScheduler cron day_of_week 表达式"
    )
    reports_dir: str = Field(default="reports", description="相对项目根目录")


class Settings(BaseModel):
    """全局策略参数根对象。"""

    model_config = ConfigDict(extra="forbid")

    concentration_caps: ConcentrationCaps = Field(default_factory=ConcentrationCaps)
    concentration_keywords: ConcentrationKeywords = Field(
        default_factory=ConcentrationKeywords
    )
    allocation_bands: AllocationBands = Field(default_factory=AllocationBands)
    capital: CapitalThresholds = Field(default_factory=CapitalThresholds)
    redemption_fees: RedemptionFees = Field(default_factory=RedemptionFees)
    stress_scenarios: list[StressScenario] = Field(default_factory=list)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
