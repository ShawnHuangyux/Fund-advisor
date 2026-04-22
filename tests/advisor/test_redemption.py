"""T+N 赎回估算测试。"""

from __future__ import annotations

from datetime import date

from fund_advisor.advisor.redemption import estimate_settlement
from fund_advisor.models import FundType


def test_equity_midweek_skips_weekend():
    """周三赎回股基：T+1 周四确认、T+2~T+4 周五~下周二到账。"""
    s = estimate_settlement(FundType.EQUITY, date(2026, 4, 22))  # 周三
    assert s.trade_date == date(2026, 4, 22)
    assert s.confirm_date == date(2026, 4, 23)   # T+1 周四
    assert s.available_earliest == date(2026, 4, 24)  # T+2 周五
    # T+4 的工作日 = 周一(4/27)? 4/22 周三 → 周四/周五/周一/周二 = 4/28 (周二)
    assert s.available_latest == date(2026, 4, 28)


def test_weekend_trade_bumps_to_monday():
    """周六下单应以下周一作为有效下单日。"""
    s = estimate_settlement(FundType.EQUITY, date(2026, 4, 25))  # 周六
    assert s.trade_date == date(2026, 4, 27)  # 下周一
    assert s.confirm_date == date(2026, 4, 28)


def test_money_fund_same_day_next_workday():
    """货基：T+1 确认+到账。"""
    s = estimate_settlement(FundType.MONEY, date(2026, 4, 22))
    assert s.confirm_date == date(2026, 4, 23)
    assert s.available_earliest == s.available_latest == date(2026, 4, 23)


def test_bond_fund_t2():
    """债基：T+1 确认、T+2~T+3 到账。"""
    s = estimate_settlement(FundType.BOND, date(2026, 4, 22))
    assert s.confirm_date == date(2026, 4, 23)
    assert s.available_earliest == date(2026, 4, 24)


def test_qdii_fund_longest():
    """QDII：T+2 确认、T+4~T+8 到账。"""
    s = estimate_settlement(FundType.QDII, date(2026, 4, 22))
    # 4/22 周三 → T+2 = 周五 4/24
    assert s.confirm_date == date(2026, 4, 24)
    # T+4 = 下周二 4/28
    assert s.available_earliest == date(2026, 4, 28)
    # T+8 = 下下周一 5/4 (跳周末)
    assert s.available_latest == date(2026, 5, 4)


def test_note_mentions_tplus_warning():
    s = estimate_settlement(FundType.EQUITY, date(2026, 4, 22))
    assert "确认净值" in s.note
    assert "当日收益" in s.note or "确认日净值" in s.note
