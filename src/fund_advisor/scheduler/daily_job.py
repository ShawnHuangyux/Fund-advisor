"""每日定时诊断任务：跑 run_diagnosis 并把 DiagnosisReport 落盘为 JSON。

设计取舍：
- 单次任务内部异常一律捕获并 ``logger.exception``，不向上抛——避免 APScheduler
  因单次失败把 job 标记成 ``MaxInstancesReachedError`` 或杀掉。
- 同一天多次运行会覆盖当天文件（``YYYY-MM-DD.json``），最新一次为准。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..advisor import advisor as advisor_mod
from ..data import load_portfolio, load_settings
from ..llm import build_deepseek_client
from ..models import DiagnosisReport, Portfolio, Settings


def run_daily_job(
    settings_path: Path,
    portfolio_path: Path,
    reports_dir: Path,
    *,
    llm_mode: str = "deep",
) -> Path | None:
    """跑一次完整诊断、落盘 JSON、返回文件路径（失败返回 None）。

    参数全部是 Path，而不是已加载好的 Settings / Portfolio——这样每次调度都会
    重读磁盘，支持用户改完 YAML 立刻生效而不用重启 scheduler 进程。
    """
    try:
        logger.info("开始每日定时诊断")
        settings: Settings = load_settings(settings_path)
        portfolio: Portfolio = load_portfolio(portfolio_path)
        logger.info(
            "加载配置完成：{} 只持仓，LLM 模式 {}",
            len(portfolio.holdings),
            llm_mode,
        )

        llm_client = build_deepseek_client()
        report: DiagnosisReport = advisor_mod.run_diagnosis(
            portfolio,
            settings,
            llm_client=llm_client,
            llm_mode=llm_mode,
            resolve=True,
            progress=lambda m: logger.info("  · {}", m),
        )

        reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{report.generated_at.date().isoformat()}.json"
        out_path = reports_dir / filename
        out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            "诊断完成并落盘：{}（signals={}, action_items={}）",
            out_path,
            len(report.signals),
            len(report.action_items),
        )
        return out_path
    except Exception:  # noqa: BLE001
        logger.exception("每日定时诊断失败")
        return None
