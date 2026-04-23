"""risk 诊断模块的单元测试。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from fund_advisor.diagnostics import risk
from fund_advisor.models.settings import StressScenario

from ..conftest import make_holding, make_portfolio


def _make_nav_df(dates: list[date], navs: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"净值日期": dates, "单位净值": navs, "日增长率": [0.0] * len(dates)}
    )


def test_max_drawdown_basic():
    """[1.0, 1.2, 0.9, 1.1] → 峰值 1.2 → 谷底 0.9 → 回撤 25%。"""
    s = pd.Series([1.0, 1.2, 0.9, 1.1])
    assert risk.compute_max_drawdown(s) == Decimal("0.25")


def test_max_drawdown_monotonic_up_returns_zero():
    s = pd.Series([1.0, 1.01, 1.02, 1.05])
    result = risk.compute_max_drawdown(s)
    assert result == Decimal("0.00") or result == Decimal("0")


def test_annualized_volatility_constant_returns_none_or_zero():
    """恒定净值 → 日收益全 0 → 方差 0。"""
    s = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    vol = risk.compute_annualized_volatility(s)
    # 可能是 None（全 0 会被认为无意义）或接近 0
    assert vol is None or vol <= Decimal("0.01")


def test_stress_test_loss_clamped_to_window():
    """给 2022 跨年数据，窗口 2022-01-01 ~ 2022-04-30 应返回区间跌幅。"""
    # 造一条 2022-01-01 净值 1.0，2022-03-15 跌到 0.8，2022-04-30 回升到 0.85
    dates = [date(2022, 1, 1), date(2022, 2, 1), date(2022, 3, 15),
             date(2022, 4, 30), date(2022, 6, 1)]
    navs = [1.0, 0.95, 0.8, 0.85, 0.95]
    df = _make_nav_df(dates, navs)

    loss = risk.stress_test_loss(df, date(2022, 1, 1), date(2022, 4, 30))
    assert loss is not None
    # 从峰值 1.0 → 谷底 0.8 = 20%
    assert abs(float(loss) - 0.20) < 0.001


def test_stress_test_loss_returns_none_when_no_data_in_window():
    df = _make_nav_df([date(2023, 1, 1), date(2023, 2, 1)], [1.0, 1.1])
    assert risk.stress_test_loss(df, date(2022, 1, 1), date(2022, 4, 30)) is None


def test_diagnose_breach_tolerance_emits_high_signal(default_settings):
    """组合最大回撤容忍度 0.15，但压力场景算出 22% 跌幅 → RISK_EXCEEDS_TOLERANCE (high)。"""
    h = make_holding(code="017513", shares="1000", cost_price="1.0")
    # 让 market_value / total 占比最大化（关掉 cash 影响）
    p = make_portfolio(cash="0", holdings=[h], max_drawdown_tolerance="0.15")

    # 伪造一段 2022 历史：从 1.0 跌到 0.78
    dates = [date(2022, 1, 1), date(2022, 2, 1), date(2022, 3, 15), date(2022, 4, 30)]
    navs = [1.0, 0.90, 0.78, 0.82]
    nav_histories = {"017513": _make_nav_df(dates, navs)}

    # 强制 settings 只含 2022 场景
    settings = default_settings.model_copy(
        update={
            "stress_scenarios": [
                StressScenario(
                    name="2022 测试场景",
                    start=date(2022, 1, 1),
                    end=date(2022, 4, 30),
                )
            ]
        }
    )

    diag = risk.diagnose(p, settings, nav_histories=nav_histories)

    assert len(diag.stress_tests) == 1
    st = diag.stress_tests[0]
    # 跌幅 (1.0 - 0.78)/1.0 = 22%
    assert abs(float(st.portfolio_loss) - 0.22) < 0.005
    assert st.breach_tolerance is True
    codes = [s.code for s in diag.signals]
    assert "RISK_EXCEEDS_TOLERANCE" in codes
    breach_sig = next(s for s in diag.signals if s.code == "RISK_EXCEEDS_TOLERANCE")
    assert breach_sig.severity.value == "high"


def test_diagnose_missing_history_graceful(default_settings):
    """nav_histories 为空 → 不崩，signals 为空或只含低优 info。"""
    h = make_holding(code="017513", shares="1000", cost_price="1.0")
    p = make_portfolio(cash="0", holdings=[h])

    settings = default_settings.model_copy(
        update={
            "stress_scenarios": [
                StressScenario(
                    name="2022",
                    start=date(2022, 1, 1),
                    end=date(2022, 4, 30),
                )
            ]
        }
    )
    diag = risk.diagnose(p, settings, nav_histories={})  # 空
    # 没有任何数据 → 每只基金 data_caveat 非空、没有 HIGH 信号
    assert diag.fund_metrics[0].data_caveat is not None
    assert diag.stress_tests[0].portfolio_loss == Decimal("0.0000")
    assert diag.stress_tests[0].breach_tolerance is False
    assert "017513" in diag.stress_tests[0].missing_funds
    assert not any(s.severity.value == "high" for s in diag.signals)
