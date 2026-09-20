from __future__ import annotations

import argparse
import logging
from pathlib import Path

from app.config import settings
from app.database import init_db
from app.services.timezone import BEIJING_TZ
from app.trading.engine import TradingEngine


def run_once(trigger_source: str = "manual") -> str:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return TradingEngine().run_cycle(trigger_source=trigger_source)


def start_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    engine = TradingEngine()

    def run_if_market_open() -> None:
        Path("data/personal_plan_scheduler.heartbeat").touch()
        phase = engine.market_state_engine.current_phase()
        if phase not in {"morning_session", "afternoon_session"}:
            logging.getLogger(__name__).info("Skip personal-plan cycle outside continuous trading: %s", phase)
            return
        engine.run_cycle(trigger_source="scheduler")

    scheduler = BlockingScheduler(timezone=BEIJING_TZ)
    scheduler.add_job(
        run_if_market_open,
        trigger=IntervalTrigger(seconds=max(settings.trading_loop_seconds, 5)),
        id="a_share_intraday_decision_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    if settings.trading_run_on_start:
        run_if_market_open()
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="A-share realtime trading engine v1.3")
    parser.add_argument("--once", action="store_true", help="Run one decision cycle and exit.")
    args = parser.parse_args()

    if args.once:
        cycle_id = run_once()
        print(f"cycle_id={cycle_id}")
        return
    start_scheduler()
