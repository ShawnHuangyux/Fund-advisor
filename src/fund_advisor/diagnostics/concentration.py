"""集中度诊断。

规则（规格文档 §2 集中度诊断）：
- 北交所/行业主题基金（波动极大）：上限 15%
- 宽基指数（沪深 300 / 中证 500 等）：上限 30%
- 债基：40%
- 货基：不限
- 未匹配 → 走保守 15%
"""

from __future__ import annotations

from decimal import Decimal

from ..models import (
    ConcentrationDiagnosis,
    ConcentrationItem,
    FundRiskClass,
    FundType,
    Holding,
    Portfolio,
    Settings,
    Severity,
    Signal,
)

_DEC_ZERO = Decimal("0")


def classify_risk(holding: Holding, settings: Settings) -> FundRiskClass:
    """基于 fund_type + 名称关键词打标签。"""
    name = holding.name or ""
    keywords = settings.concentration_keywords

    # 高波动关键词优先于 broad_index（"北证50" 同时命中 "北证" 与 "50"，以高波动为准）
    if any(kw in name for kw in keywords.high_volatility):
        return FundRiskClass.HIGH_VOLATILITY
    if any(kw in name for kw in keywords.broad_index):
        return FundRiskClass.BROAD_INDEX

    match holding.fund_type:
        case FundType.BOND:
            return FundRiskClass.BOND
        case FundType.MONEY:
            return FundRiskClass.MONEY
        case FundType.QDII:
            # QDII 视为高波动
            return FundRiskClass.HIGH_VOLATILITY
        case FundType.EQUITY | FundType.HYBRID:
            # 已经过关键词筛选仍未归类 → 保守
            return FundRiskClass.UNKNOWN
        case _:
            return FundRiskClass.UNKNOWN


def cap_for(risk_class: FundRiskClass, settings: Settings) -> Decimal:
    caps = settings.concentration_caps
    return {
        FundRiskClass.HIGH_VOLATILITY: caps.high_volatility,
        FundRiskClass.BROAD_INDEX: caps.broad_index,
        FundRiskClass.BOND: caps.bond,
        FundRiskClass.MONEY: caps.money,
        FundRiskClass.UNKNOWN: caps.unknown,
    }[risk_class]


def diagnose(portfolio: Portfolio, settings: Settings) -> ConcentrationDiagnosis:
    """计算每只持仓占总资产比例，对比上限并产出信号。"""
    total = portfolio.total_assets
    items: list[ConcentrationItem] = []
    signals: list[Signal] = []

    if total <= _DEC_ZERO:
        return ConcentrationDiagnosis(items=[], signals=[])

    for h in portfolio.holdings:
        risk_class = classify_risk(h, settings)
        cap = cap_for(risk_class, settings)
        # 优先用 market_value（实时净值），没有就回退到成本价
        actual = (h.market_value / total).quantize(Decimal("0.0001"))
        over = actual > cap

        items.append(
            ConcentrationItem(
                fund_code=h.code,
                fund_name=h.name or h.code,
                risk_class=risk_class.value,
                cap_ratio=cap,
                actual_ratio=actual,
                over_limit=over,
            )
        )

        if over:
            signals.append(
                Signal(
                    code="OVER_CONCENTRATED",
                    severity=Severity.WARN,
                    fund_code=h.code,
                    message=(
                        f"{h.name or h.code}({h.code}) 当前占比 "
                        f"{actual * 100:.2f}% 超过 {risk_class.value} 品种上限 "
                        f"{cap * 100:.0f}%，建议暂停加仓或考虑部分止盈。"
                    ),
                    detail={
                        "actual_ratio": str(actual),
                        "cap_ratio": str(cap),
                        "risk_class": risk_class.value,
                    },
                )
            )

    return ConcentrationDiagnosis(items=items, signals=signals)
