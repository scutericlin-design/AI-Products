from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from realtime.intraday_loop import IntradayLoop
from scheduler.trading_calendar import current_trading_window
from storage.logger import get_latest_cycle_payload, init_db, log_push


logger = logging.getLogger(__name__)


def run_scheduled_cycle(loop: IntradayLoop) -> str | None:
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip scheduled cycle: phase=%s reason=%s", window.phase, window.reason)
        return None
    return loop.run_once(trigger_source="scheduler")


def run_etf_minute_cycle() -> dict[str, object] | None:
    """Run the independent ETF minute paper engine inside the shared scheduler."""
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip ETF minute cycle: phase=%s reason=%s", window.phase, window.reason)
        return None

    from etf_strategy.config import load_settings as load_etf_settings
    from etf_strategy.live_runner import ETFMinutePaperRunner
    from notify.feishu import send_feishu_etf_event_report

    etf_settings = load_etf_settings()
    if not etf_settings.minute_paper_trading_enabled:
        return {"status": "skipped_etf_paper_trading_disabled", "no_real_orders": True}
    result = ETFMinutePaperRunner(settings=etf_settings).run_once()
    push = send_feishu_etf_event_report(result)
    result["push"] = push
    logger.info(
        "ETF minute cycle finished: status=%s filled=%s push=%s",
        result.get("status"),
        len(result.get("orders") or []),
        push.get("status"),
    )
    return result


def run_etf_source_context_refresh() -> dict[str, object] | None:
    """Prepare prior-close data for the source-file ETF strategy at 09:00."""
    from etf_strategy.config import load_settings as load_etf_settings
    from etf_strategy.live_runner import ETFMinutePaperRunner

    etf_settings = load_etf_settings()
    if not etf_settings.minute_paper_trading_enabled or etf_settings.minute_strategy_profile != "wufu_v7_static":
        return None
    result = ETFMinutePaperRunner(settings=etf_settings).refresh_source_context()
    logger.info("ETF source-file context refresh: status=%s as_of=%s", result.get("status"), result.get("daily_as_of"))
    return result


def run_hot_leader_close_plan() -> dict[str, object] | None:
    """Create a standalone close plan; it never submits broker orders."""
    from hot_leader_strategy.config import load_settings as load_hot_leader_settings
    from hot_leader_strategy.runner import HotLeaderRunner

    settings = load_hot_leader_settings()
    if not settings.auto_enabled:
        return {"status": "skipped_hot_leader_automation_disabled", "no_real_orders": True}
    result = HotLeaderRunner(settings=settings).close_plan_once()
    logger.info("hot-leader close plan finished: status=%s", result.get("status"))
    return result


def run_hot_leader_open_execution() -> dict[str, object] | None:
    """Execute only a previous-session plan in the independent paper account."""
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip hot-leader open execution: phase=%s reason=%s", window.phase, window.reason)
        return None
    from hot_leader_strategy.config import load_settings as load_hot_leader_settings
    from hot_leader_strategy.runner import HotLeaderRunner

    settings = load_hot_leader_settings()
    if not settings.auto_enabled or not settings.paper_enabled:
        return {"status": "skipped_hot_leader_paper_disabled", "no_real_orders": True}
    result = HotLeaderRunner(settings=settings).execute_previous_plan_once()
    logger.info("hot-leader open paper execution finished: status=%s filled=%s push=%s", result.get("status"), len(result.get("orders") or []), (result.get("push") or {}).get("status"))
    return result


def run_hot_leader_intraday_cycle() -> dict[str, object] | None:
    """Refresh only hot-leader plan/position quotes and act on new paper events."""
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip hot-leader intraday cycle: phase=%s reason=%s", window.phase, window.reason)
        return None
    from hot_leader_strategy.config import load_settings as load_hot_leader_settings
    from hot_leader_strategy.runner import HotLeaderRunner

    settings = load_hot_leader_settings()
    if not settings.auto_enabled or not settings.intraday_enabled:
        return {"status": "skipped_hot_leader_intraday_disabled", "no_real_orders": True}
    result = HotLeaderRunner(settings=settings).intraday_once()
    logger.info(
        "hot-leader intraday cycle finished: status=%s quotes=%s filled=%s push=%s",
        result.get("status"),
        result.get("quote_count", 0),
        len(result.get("orders") or []),
        (result.get("push") or {}).get("status"),
    )
    return result


def run_hot_leader_live_theme_cycle() -> dict[str, object] | None:
    """Persist a whole-market observation only; it cannot create an order."""
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip hot-leader live theme cycle: phase=%s reason=%s", window.phase, window.reason)
        return None
    from hot_leader_strategy.config import load_settings as load_hot_leader_settings
    from hot_leader_strategy.runner import HotLeaderRunner

    settings = load_hot_leader_settings()
    if not settings.auto_enabled or not settings.live_theme_enabled:
        return {"status": "skipped_hot_leader_live_theme_disabled", "no_real_orders": True}
    result = HotLeaderRunner(settings=settings).live_theme_once()
    logger.info("hot-leader live theme cycle finished: status=%s quotes=%s themes=%s", result.get("status"), result.get("quote_count", 0), result.get("hot_theme_count", 0))
    return result


def run_market_status_push() -> dict[str, object] | None:
    """Send a scheduled market brief from the latest completed stock cycle."""
    window = current_trading_window()
    if not window.is_open:
        logger.info("skip market status push: phase=%s reason=%s", window.phase, window.reason)
        return None
    row = get_latest_cycle_payload()
    if not row:
        return {"status": "skipped_no_market_cycle"}
    try:
        payload = json.loads(str(row.get("payload_json") or "{}"))
    except json.JSONDecodeError:
        return {"status": "skipped_invalid_market_cycle_payload"}
    if not isinstance(payload, dict) or not payload.get("final_signal"):
        return {"status": "skipped_incomplete_market_cycle"}

    from notify.feishu import send_feishu_market_status

    push = send_feishu_market_status(payload)
    push_id = f"market-status-{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d%H%M')}"
    log_push(push_id, push)
    logger.info("market status push finished: status=%s", push.get("status"))
    return push


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
    from etf_strategy.config import load_settings as load_etf_settings

    etf_settings = load_etf_settings()
    if etf_settings.minute_enabled:
        if etf_settings.minute_strategy_profile == "wufu_v7_static":
            scheduler.add_job(
                run_etf_source_context_refresh,
                trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=0, second=5, timezone="Asia/Shanghai"),
                id="a_stock_ai_etf_source_context",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
        # Cron ranges cannot express the lunch break in one trigger. Register
        # only the executable A-share minutes so closed periods stay silent.
        etf_minute_windows = (
            ("morning_0930", {"hour": 9, "minute": "30-59"}),
            ("morning_10", {"hour": 10, "minute": "*"}),
            ("morning_11", {"hour": 11, "minute": "0-29"}),
            ("afternoon", {"hour": "13-14", "minute": "*"}),
        )
        for suffix, fields in etf_minute_windows:
            scheduler.add_job(
                run_etf_minute_cycle,
                trigger=CronTrigger(day_of_week="mon-fri", second=5, timezone="Asia/Shanghai", **fields),
                id=f"a_stock_ai_etf_minute_paper_{suffix}",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
    from hot_leader_strategy.config import load_settings as load_hot_leader_settings

    hot_leader_settings = load_hot_leader_settings()
    if hot_leader_settings.auto_enabled:
        scheduler.add_job(
            run_hot_leader_close_plan,
            trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=8, second=15, timezone="Asia/Shanghai"),
            id="a_stock_ai_hot_leader_close_plan",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            run_hot_leader_open_execution,
            trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=31, second=20, timezone="Asia/Shanghai"),
            id="a_stock_ai_hot_leader_open_paper",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        if hot_leader_settings.intraday_enabled:
            interval = hot_leader_settings.intraday_interval_minutes
            hot_leader_windows = (
                ("morning_0932", {"hour": 9, "minute": _minute_range(32, 58, interval)}),
                ("morning_10", {"hour": 10, "minute": _minute_range(0, 58, interval)}),
                ("morning_11", {"hour": 11, "minute": _minute_range(0, 28, interval)}),
                ("afternoon_13", {"hour": 13, "minute": _minute_range(0, 58, interval)}),
                ("afternoon_14", {"hour": 14, "minute": _minute_range(0, 58, interval)}),
            )
            for suffix, fields in hot_leader_windows:
                scheduler.add_job(
                    run_hot_leader_intraday_cycle,
                    trigger=CronTrigger(day_of_week="mon-fri", second=35, timezone="Asia/Shanghai", **fields),
                    id=f"a_stock_ai_hot_leader_intraday_{suffix}",
                    max_instances=1,
                    coalesce=True,
                    replace_existing=True,
                )
        if hot_leader_settings.live_theme_enabled:
            interval = hot_leader_settings.live_theme_interval_minutes
            hot_leader_theme_windows = (
                ("morning_0935", {"hour": 9, "minute": _minute_range(35, 55, interval)}),
                ("morning_10", {"hour": 10, "minute": _minute_range(0, 55, interval)}),
                ("morning_11", {"hour": 11, "minute": _minute_range(0, 25, interval)}),
                ("afternoon_13", {"hour": 13, "minute": _minute_range(0, 55, interval)}),
                ("afternoon_14", {"hour": 14, "minute": _minute_range(0, 55, interval)}),
            )
            for suffix, fields in hot_leader_theme_windows:
                scheduler.add_job(
                    run_hot_leader_live_theme_cycle,
                    trigger=CronTrigger(day_of_week="mon-fri", second=50, timezone="Asia/Shanghai", **fields),
                    id=f"a_stock_ai_hot_leader_live_theme_{suffix}",
                    max_instances=1,
                    coalesce=True,
                    replace_existing=True,
                )
    if settings.market_status_push_enabled:
        # Run after the latest two-minute stock cycle has completed. The lunch
        # break is intentionally excluded so off-market periods stay silent.
        market_status_windows = (
            ("morning_0932", {"hour": 9, "minute": "32"}),
            ("morning_10", {"hour": 10, "minute": "2,32"}),
            ("morning_11", {"hour": 11, "minute": "2"}),
            ("afternoon_13", {"hour": 13, "minute": "2,32"}),
            ("afternoon_14", {"hour": 14, "minute": "2,32"}),
        )
        for suffix, fields in market_status_windows:
            scheduler.add_job(
                run_market_status_push,
                trigger=CronTrigger(day_of_week="mon-fri", second=40, timezone="Asia/Shanghai", **fields),
                id=f"a_stock_ai_market_status_{suffix}",
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
        "scheduler started with stock_interval=%ss etf_minute=%s market_status_30m=%s daily_review=%s self_learning=%s",
        settings.loop_seconds,
        "enabled" if etf_settings.minute_enabled else "disabled",
        "enabled" if settings.market_status_push_enabled else "disabled",
        f"{settings.review_hour:02d}:{settings.review_minute:02d}" if settings.review_enabled else "disabled",
        (
            f"{settings.self_learning_hour:02d}:{settings.self_learning_minute:02d}"
            if settings.self_learning_enabled
            else "disabled"
        ),
    )
    scheduler.start()


def _minute_range(start: int, end: int, interval: int) -> str:
    """Return an APScheduler cron expression without generating closed-period jobs."""
    return f"{start}-{end}" if interval <= 1 else f"{start}-{end}/{interval}"
