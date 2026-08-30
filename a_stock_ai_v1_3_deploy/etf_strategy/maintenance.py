from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def minute_storage_report(db_path: Path) -> dict[str, Any]:
    """Read-only capacity report for the isolated ETF research database.

    Historical minute bars are deliberately retained because the ETF 1-minute
    backtester depends on them. This report makes growth visible without
    deleting research data or changing the live ETF strategy.
    """
    path = Path(db_path).resolve()
    if not path.exists():
        return {
            "exists": False,
            "db_path": str(path),
            "message": "ETF策略数据库不存在。",
        }

    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0] or 0)
        page_count = int(db.execute("PRAGMA page_count").fetchone()[0] or 0)
        daily_row = db.execute("SELECT MAX(trade_date) AS latest_daily_date FROM etf_daily").fetchone()
        minute_row = db.execute(
            """
            SELECT trade_time AS latest_minute_time, source AS latest_minute_source
            FROM etf_minute
            ORDER BY trade_time DESC
            LIMIT 1
            """
        ).fetchone()
        cached_windows = db.execute("SELECT COUNT(*) FROM etf_minute_fetch_window").fetchone()[0]

    return {
        "exists": True,
        "db_path": str(path),
        "size_bytes": path.stat().st_size,
        "allocated_bytes": page_size * page_count,
        "page_size": page_size,
        "page_count": page_count,
        "cached_minute_windows": int(cached_windows or 0),
        "latest_daily_date": daily_row["latest_daily_date"] if daily_row else None,
        "latest_minute_time": minute_row["latest_minute_time"] if minute_row else None,
        "latest_minute_source": minute_row["latest_minute_source"] if minute_row else None,
        "retention_policy": "保留历史分钟数据；归档必须先验证回测可从归档读取。",
    }
