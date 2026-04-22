"""基金静态元数据。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .portfolio import FundType


class FundRiskClass(StrEnum):
    """集中度诊断内部使用的风险分类。"""

    HIGH_VOLATILITY = "high_volatility"  # 上限 15%
    BROAD_INDEX = "broad_index"          # 上限 30%
    BOND = "bond"                        # 上限 40%
    MONEY = "money"                      # 不限
    UNKNOWN = "unknown"                  # 走保守 15%


class FundBasicInfo(BaseModel):
    """akshare 拉回来的基金元信息（精简后的稳定字段）。"""

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    fund_type_raw: str = Field(description="原始基金类型字符串，如 '股票型-标准指数'")
    fund_type: FundType = Field(description="归一化后的大类")
    inception_date: date | None = None
    fund_company: str | None = None
    fund_manager: str | None = None
    latest_scale: str | None = Field(default=None, description="最新规模（原始字符串如 '4.09亿'）")


def normalize_fund_type(raw: str) -> FundType:
    """把 akshare 返回的中文基金类型映射到内部枚举。

    典型值：
    - '股票型-标准指数' / '指数型-股票' / '股票型' → EQUITY
    - '债券型-中短债' / '债券型-混合二级' → BOND
    - '货币型' / '理财型' → MONEY
    - '混合型-灵活' / '混合型-偏股' → HYBRID
    - 'QDII' / '国际(QDII)-股票' → QDII
    """
    if not raw:
        return FundType.UNKNOWN
    s = raw.strip()
    if "QDII" in s or "国际" in s:
        return FundType.QDII
    if "货币" in s or "理财" in s:
        return FundType.MONEY
    if "债券" in s:
        return FundType.BOND
    if "混合" in s:
        return FundType.HYBRID
    if "股票" in s or "指数" in s:
        return FundType.EQUITY
    return FundType.UNKNOWN
