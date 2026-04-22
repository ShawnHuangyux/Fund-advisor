"""基金赎回 T+N 估算。

基金与股票的根本区别：**当日下单 → T+1 确认净值 → T+N 资金到账**，用户不能
当日"卖出即到账"。这里按基金大类给一个估算区间，足以在建议文本里标注。

说明：15:00 前下单按 T 日净值确认，15:00 后按 T+1；这里简化按"当日下单 = 确认 T+1"。
实际到账天数由基金公司和托管行决定，以下数值是行业常见值。
"""

from __future__ import annotations

from datetime import date, timedelta

from ..models import FundType, Settlement

# (确认 T+n, 最早到账 T+n, 最晚到账 T+n)
_DEFAULTS: dict[FundType, tuple[int, int, int]] = {
    FundType.EQUITY: (1, 2, 4),
    FundType.HYBRID: (1, 2, 4),
    FundType.BOND: (1, 2, 3),
    FundType.MONEY: (1, 1, 1),
    FundType.QDII: (2, 4, 8),
    FundType.UNKNOWN: (1, 2, 4),
}


def _is_workday(d: date) -> bool:
    return d.weekday() < 5  # 周一=0, 周六=5；不考虑法定节假日


def _add_workdays(start: date, n: int) -> date:
    """在 start 基础上前进 n 个交易日（跳过周末）。"""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if _is_workday(d):
            added += 1
    return d


def estimate_settlement(fund_type: FundType, trade_date: date | None = None) -> Settlement:
    """返回赎回 T+N 估算。默认以今日为下单日。"""
    if trade_date is None:
        trade_date = date.today()
    # 若下单日本身不是工作日，用下一个工作日当作实际下单日（口语化处理）
    effective_trade = trade_date if _is_workday(trade_date) else _add_workdays(trade_date, 1)

    t_confirm, t_earliest, t_latest = _DEFAULTS.get(fund_type, _DEFAULTS[FundType.UNKNOWN])

    confirm_date = _add_workdays(effective_trade, t_confirm)
    avail_earliest = _add_workdays(effective_trade, t_earliest)
    avail_latest = _add_workdays(effective_trade, t_latest)

    type_label = {
        FundType.EQUITY: "股票型",
        FundType.HYBRID: "混合型",
        FundType.BOND: "债券型",
        FundType.MONEY: "货币型",
        FundType.QDII: "QDII",
        FundType.UNKNOWN: "未知类型",
    }[fund_type]

    if t_earliest == t_latest:
        avail_phrase = f"资金预计 {avail_earliest.isoformat()} 到账"
    else:
        avail_phrase = (
            f"资金预计 {avail_earliest.isoformat()} ~ {avail_latest.isoformat()} 到账"
        )

    note = (
        f"{type_label}基金：{effective_trade.isoformat()} 下单，"
        f"{confirm_date.isoformat()} 确认净值，{avail_phrase}。"
        f"当日收益与赎回金额按确认日净值计算，不是你看见的当前净值。"
    )

    return Settlement(
        trade_date=effective_trade,
        confirm_date=confirm_date,
        available_earliest=avail_earliest,
        available_latest=avail_latest,
        note=note,
    )
