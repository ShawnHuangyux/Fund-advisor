"""APScheduler 常驻入口：每个交易日 16:30 Asia/Shanghai 自动跑诊断。

启动方式::

    uv run fund-advisor-scheduler               # 常驻，按 cron 触发
    uv run fund-advisor-scheduler --run-now     # 启动时立刻跑一次再进调度
    uv run fund-advisor-scheduler --run-once    # 只跑一次就退出（给系统 cron 用）

cron 表达式从 ``config/settings.yaml`` 的 ``scheduler`` 小节读取；启动时会
把一次完整的 loguru 日志 sink 到 ``logs/scheduler.log`` (每日轮转、保留 14 天)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from ..data import load_settings
from .daily_job import run_daily_job


def _project_root() -> Path:
    # src/fund_advisor/scheduler/runner.py → parents[3] = 项目根
    return Path(__file__).resolve().parents[3]


def _resolve_paths() -> tuple[Path, Path, Path, Path]:
    root = _project_root()
    settings_path = root / "config" / "settings.yaml"
    portfolio_path = root / "config" / "portfolio.yaml"
    logs_dir = root / "logs"
    return root, settings_path, portfolio_path, logs_dir


def _setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        logs_dir / "scheduler.log",
        rotation="00:00",
        retention="14 days",
        level="INFO",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fund advisor 每日定时诊断")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="启动时立即跑一次再进入调度循环",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="只跑一次就退出（不启动调度循环，用于系统 cron）",
    )
    args = parser.parse_args(argv)

    root, settings_path, portfolio_path, logs_dir = _resolve_paths()
    _setup_logging(logs_dir)

    settings = load_settings(settings_path)
    sc = settings.scheduler
    reports_dir = root / sc.reports_dir

    logger.info(
        "fund-advisor-scheduler 启动：项目根 {}，报告目录 {}，cron {:02d}:{:02d} ({}) tz={}",
        root,
        reports_dir,
        sc.cron_hour,
        sc.cron_minute,
        sc.day_of_week,
        sc.timezone,
    )

    if args.run_once:
        run_daily_job(settings_path, portfolio_path, reports_dir, llm_mode=settings.llm.mode)
        return 0

    if args.run_now:
        logger.info("--run-now：立即执行一次")
        run_daily_job(settings_path, portfolio_path, reports_dir, llm_mode=settings.llm.mode)

    if not sc.enabled:
        logger.warning("scheduler.enabled = False，退出（未启动调度循环）")
        return 0

    scheduler = BlockingScheduler(timezone=sc.timezone)
    trigger = CronTrigger(
        day_of_week=sc.day_of_week,
        hour=sc.cron_hour,
        minute=sc.cron_minute,
        timezone=sc.timezone,
    )
    scheduler.add_job(
        run_daily_job,
        trigger=trigger,
        args=[settings_path, portfolio_path, reports_dir],
        kwargs={"llm_mode": settings.llm.mode},
        id="daily_diagnosis",
        name="每日定时诊断",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    try:
        logger.info("进入 BlockingScheduler 循环，Ctrl+C 退出")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，停止调度")
        scheduler.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
