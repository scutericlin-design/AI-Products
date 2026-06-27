from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from realtime.intraday_loop import IntradayLoop
from scheduler.trading_calendar import current_trading_window
from storage.logger import init_db


logger = logging.getLogger(__name__)


def run_scheduled_cycle(loop: IntradayLoop) -> str | None:
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip scheduled cycle: phase=%s reason=%s", window.phase, window.reason)
        return None
    return loop.run_once(trigger_source="scheduler")


def start_scheduler() -> None:
    init_db()
    loop = IntradayLoop()
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        lambda: run_scheduled_cycle(loop),
        trigger=IntervalTrigger(seconds=settings.loop_seconds),
        id="a_stock_ai_intraday_loop",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    if settings.review_enabled:
        from review.daily_review import run_daily_review

        scheduler.add_job(
            lambda: run_daily_review(trigger_source="scheduler"),
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=settings.review_hour,
                minute=settings.review_minute,
                timezone="Asia/Shanghai",
            ),
            id="a_stock_ai_daily_review",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    if settings.self_learning_enabled:
        from learning.self_learning import run_self_learning

        scheduler.add_job(
            lambda: run_self_learning(trigger_source="scheduler"),
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=settings.self_learning_hour,
                minute=settings.self_learning_minute,
                timezone="Asia/Shanghai",
            ),
            id="a_stock_ai_self_learning",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    if settings.run_on_start:
        window = current_trading_window()
        if window.is_open:
            loop.run_once(trigger_source="startup")
        else:
            logger.info("skip startup cycle: phase=%s reason=%s", window.phase, window.reason)
    logger.info(
        "scheduler started with interval=%ss daily_review=%s self_learning=%s",
        settings.loop_seconds,
        f"{settings.review_hour:02d}:{settings.review_minute:02d}" if settings.review_enabled else "disabled",
        (
            f"{settings.self_learning_hour:02d}:{settings.self_learning_minute:02d}"
            if settings.self_learning_enabled
            else "disabled"
        ),
    )
    scheduler.start()
