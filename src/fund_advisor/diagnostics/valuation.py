"""估值诊断（规格文档 §4，简化版）。

阶段 2 简化实现：
- 仅对能通过"基金名称关键词 → 宽基指数"映射成功的持仓做 PE 3 年分位判定
- 主动/主题基金无法匹配时，降级为 ``ValuationStatus.UNAVAILABLE`` 并记 note
- 温度阈值：≤30%=low、30-70%=normal、70-80%=high、≥80%=overheated
- 非股票类（债/货）直接标 UNAVAILABLE
- 任何网络失败都降级为 UNAVAILABLE 并记 note，不阻断诊断
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from loguru import logger

from ..data.akshare_client import FundDataError, get_index_valuation, match_index_symbol
from ..models import (
    FundType,
    Portfolio,
    Settings,
    Severity,
    Signal,
    ValuationDiagnosis,
    ValuationItem,
    ValuationStatus,
)

_LOW = Decimal("0.30")
_NORMAL_HIGH = Decimal("0.70")
_HIGH = Decimal("0.80")

ValuationFetcher = Callable[[str], dict]


def _status_of(pe_percentile: Decimal | None) -> ValuationStatus:
    if pe_percentile is None:
        return ValuationStatus.UNAVAILABLE
    if pe_percentile <= _LOW:
        return ValuationStatus.LOW
    if pe_percentile < _NORMAL_HIGH:
        return ValuationStatus.NORMAL
    if pe_percentile < _HIGH:
        return ValuationStatus.HIGH
    return ValuationStatus.OVERHEATED


def diagnose(
    portfolio: Portfolio,
    settings: Settings,
    *,
    fetch_index: ValuationFetcher | None = None,
) -> ValuationDiagnosis:
    """执行估值诊断。

    ``fetch_index`` 允许注入 mock（测试时避免真联网）；默认走 akshare。
    """
    fetch = fetch_index or get_index_valuation
    items: list[ValuationItem] = []
    signals: list[Signal] = []

    for h in portfolio.holdings:
        ft = h.fund_type or FundType.UNKNOWN
        # 非股票类型直接跳过
        if ft in (FundType.BOND, FundType.MONEY):
            items.append(
                ValuationItem(
                    fund_code=h.code,
                    fund_name=h.name or h.code,
                    status=ValuationStatus.UNAVAILABLE,
                    note=f"{ft.value} 不做估值温度判定",
                )
            )
            continue

        symbol = match_index_symbol(h.name or "")
        if symbol is None:
            items.append(
                ValuationItem(
                    fund_code=h.code,
                    fund_name=h.name or h.code,
                    status=ValuationStatus.UNAVAILABLE,
                    note="主动/主题基金未匹配到宽基指数，暂不输出估值温度",
                )
            )
            continue

        try:
            valuation = fetch(symbol)
        except FundDataError as e:
            logger.warning("指数 {} 估值拉取失败：{}", symbol, e)
            items.append(
                ValuationItem(
                    fund_code=h.code,
                    fund_name=h.name or h.code,
                    index_symbol=symbol,
                    status=ValuationStatus.UNAVAILABLE,
                    note=f"指数数据暂不可用：{e}",
                )
            )
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception("指数 {} 估值异常：{}", symbol, e)
            items.append(
                ValuationItem(
                    fund_code=h.code,
                    fund_name=h.name or h.code,
                    index_symbol=symbol,
                    status=ValuationStatus.UNAVAILABLE,
                    note=f"数据异常：{type(e).__name__}",
                )
            )
            continue

        pe_pct = valuation.get("pe_percentile")
        status = _status_of(pe_pct)
        items.append(
            ValuationItem(
                fund_code=h.code,
                fund_name=h.name or h.code,
                index_symbol=symbol,
                pe=valuation.get("pe"),
                pb=valuation.get("pb"),
                pe_percentile=pe_pct,
                status=status,
                as_of=valuation.get("as_of"),
                note=f"对应指数：{symbol}",
            )
        )

        if status == ValuationStatus.LOW:
            signals.append(
                Signal(
                    code="VALUATION_LOW",
                    severity=Severity.INFO,
                    fund_code=h.code,
                    message=(
                        f"{h.name or h.code} 对应指数 {symbol} PE 处于近 3 年 "
                        f"{float(pe_pct) * 100:.1f}% 分位（低估区间），可考虑加大定投。"
                    ),
                    detail={"index": symbol, "pe_percentile": str(pe_pct)},
                )
            )
        elif status == ValuationStatus.OVERHEATED:
            signals.append(
                Signal(
                    code="VALUATION_OVERHEATED",
                    severity=Severity.WARN,
                    fund_code=h.code,
                    message=(
                        f"{h.name or h.code} 对应指数 {symbol} PE 处于近 3 年 "
                        f"{float(pe_pct) * 100:.1f}% 分位（过热），考虑部分止盈或停投。"
                    ),
                    detail={"index": symbol, "pe_percentile": str(pe_pct)},
                )
            )

    return ValuationDiagnosis(items=items, signals=signals)
