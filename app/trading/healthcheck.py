from __future__ import annotations

import sys

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal, init_db
from app.models import TradingEngineRun
from app.trading.utils import utc_now_naive


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        latest = db.scalar(select(TradingEngineRun).order_by(TradingEngineRun.started_at.desc()))
        if latest is None:
            print("no trading engine run yet")
            return 1
        max_stale_seconds = max(settings.trading_health_max_stale_seconds, settings.trading_loop_seconds * 3 + 60)
        age_seconds = (utc_now_naive() - latest.started_at).total_seconds()
        if age_seconds > max_stale_seconds:
            print(f"stale trading engine run age={age_seconds:.0f}s")
            return 1
        if latest.status == "failed":
            print(f"latest trading engine run failed cycle_id={latest.cycle_id}")
            return 1
        print(f"ok cycle_id={latest.cycle_id} status={latest.status}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
