from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from etf_strategy.models import ETFBar, ETFInstrument


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _order_execution_context(order: dict[str, Any]) -> dict[str, Any]:
    return {
        key: order[key]
        for key in (
            "ai_degraded",
            "ai_execution_mode",
            "ai_execution_chain",
            "ai_provider",
            "ai_error",
        )
        if key in order
    }


class ETFLocalStore:
    """Local-only SQLite persistence for ETF research data and decisions."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self) -> None:
        with self._connect() as db:
            # The engine, dashboard and scheduled source snapshot can access
            # this independent ETF database concurrently. WAL lets dashboard
            # reads proceed while a short paper-account write is committed.
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS etf_universe (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    index_code TEXT,
                    index_name TEXT,
                    exchange TEXT,
                    etf_type TEXT,
                    list_date TEXT,
                    delist_date TEXT,
                    list_status TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS etf_daily (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    pre_close REAL,
                    pct_chg REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_etf_daily_date ON etf_daily(trade_date);
                CREATE TABLE IF NOT EXISTS etf_minute (
                    symbol TEXT NOT NULL,
                    trade_time TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, trade_time)
                );
                CREATE INDEX IF NOT EXISTS idx_etf_minute_time_symbol ON etf_minute(trade_time, symbol);
                CREATE TABLE IF NOT EXISTS etf_minute_fetch_window (
                    symbol TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, start_time, end_time)
                );
                CREATE TABLE IF NOT EXISTS etf_index_daily (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    pre_close REAL,
                    pct_chg REAL,
                    amount REAL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, trade_date)
                );
                CREATE TABLE IF NOT EXISTS etf_nav (
                    symbol TEXT NOT NULL,
                    nav_date TEXT NOT NULL,
                    unit_nav REAL,
                    accum_nav REAL,
                    ann_date TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, nav_date)
                );
                CREATE TABLE IF NOT EXISTS etf_decisions (
                    decision_id TEXT PRIMARY KEY,
                    as_of TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    target_symbol TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS etf_wufu_v7_sessions (
                    trade_date TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS etf_paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    cash REAL NOT NULL,
                    initial_cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS etf_paper_positions (
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    available_quantity INTEGER NOT NULL,
                    avg_cost REAL NOT NULL,
                    last_price REAL NOT NULL,
                    last_trade_date TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS etf_paper_orders (
                    order_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    reason TEXT NOT NULL,
                    execution_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_etf_paper_orders_created
                    ON etf_paper_orders(created_at);
                """
            )
            self._ensure_column(db, "etf_paper_orders", "execution_json", "TEXT NOT NULL DEFAULT '{}'")

    def upsert_universe(self, instruments: Iterable[ETFInstrument]) -> int:
        rows = [
            (
                item.symbol,
                item.name,
                item.index_code,
                item.index_name,
                item.exchange,
                item.etf_type,
                item.list_date,
                item.delist_date,
                item.list_status,
                _now(),
            )
            for item in instruments
        ]
        if not rows:
            return 0
        with self._connect() as db:
            db.executemany(
                """
                INSERT INTO etf_universe (
                    symbol, name, index_code, index_name, exchange, etf_type,
                    list_date, delist_date, list_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=excluded.name, index_code=excluded.index_code,
                    index_name=excluded.index_name, exchange=excluded.exchange,
                    etf_type=excluded.etf_type, list_date=excluded.list_date,
                    delist_date=excluded.delist_date, list_status=excluded.list_status,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def upsert_daily_bars(self, bars: Iterable[ETFBar]) -> int:
        rows = [
            (
                item.symbol,
                item.trade_date,
                item.open,
                item.high,
                item.low,
                item.close,
                item.pre_close,
                item.pct_chg,
                item.volume,
                item.amount,
                item.source,
                _now(),
            )
            for item in bars
            if item.symbol and item.trade_date
        ]
        if not rows:
            return 0
        with self._connect() as db:
            db.executemany(
                """
                INSERT INTO etf_daily (
                    symbol, trade_date, open, high, low, close, pre_close, pct_chg,
                    volume, amount, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, pre_close=excluded.pre_close,
                    pct_chg=excluded.pct_chg, volume=excluded.volume,
                    amount=excluded.amount, source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def upsert_minute_bars(self, symbol: str, bars: pd.DataFrame, source: str = "tushare:etf_mins") -> int:
        if bars.empty:
            return 0
        normalized_symbol = str(symbol or "").strip().upper()
        records: list[tuple[Any, ...]] = []
        for _, row in bars.iterrows():
            timestamp = pd.to_datetime(row.get("trade_time"), errors="coerce")
            close = _float(row.get("close"))
            if pd.isna(timestamp) or close <= 0 or not normalized_symbol:
                continue
            records.append(
                (
                    normalized_symbol,
                    timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    _float(row.get("open")),
                    _float(row.get("high")),
                    _float(row.get("low")),
                    close,
                    _float(row.get("vol", row.get("volume"))),
                    _float(row.get("amount")),
                    source,
                    _now(),
                )
            )
        if not records:
            return 0
        with self._connect() as db:
            db.executemany(
                """
                INSERT INTO etf_minute (
                    symbol, trade_time, open, high, low, close, volume, amount, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_time) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                    volume=excluded.volume, amount=excluded.amount, source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                records,
            )
        return len(records)

    def upsert_index_bars(self, rows: Iterable[dict[str, Any]]) -> int:
        records = [
            (
                str(row.get("symbol") or ""),
                str(row.get("trade_date") or ""),
                _float(row.get("open")),
                _float(row.get("high")),
                _float(row.get("low")),
                _float(row.get("close")),
                _float(row.get("pre_close")),
                _float(row.get("pct_chg")),
                _float(row.get("amount")),
                _now(),
            )
            for row in rows
            if row.get("symbol") and row.get("trade_date")
        ]
        if not records:
            return 0
        with self._connect() as db:
            db.executemany(
                """
                INSERT INTO etf_index_daily (
                    symbol, trade_date, open, high, low, close, pre_close,
                    pct_chg, amount, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, pre_close=excluded.pre_close,
                    pct_chg=excluded.pct_chg, amount=excluded.amount,
                    updated_at=excluded.updated_at
                """,
                records,
            )
        return len(records)

    def upsert_nav_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        records = [
            (
                str(row.get("symbol") or ""),
                str(row.get("nav_date") or ""),
                _float(row.get("unit_nav")),
                _float(row.get("accum_nav")),
                str(row.get("ann_date") or ""),
                _now(),
            )
            for row in rows
            if row.get("symbol") and row.get("nav_date")
        ]
        if not records:
            return 0
        with self._connect() as db:
            db.executemany(
                """
                INSERT INTO etf_nav (symbol, nav_date, unit_nav, accum_nav, ann_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, nav_date) DO UPDATE SET
                    unit_nav=excluded.unit_nav, accum_nav=excluded.accum_nav,
                    ann_date=excluded.ann_date, updated_at=excluded.updated_at
                """,
                records,
            )
        return len(records)

    def load_universe(self, as_of: str) -> pd.DataFrame:
        query = """
            SELECT * FROM etf_universe
            WHERE (list_date = '' OR list_date <= ?)
              AND (delist_date = '' OR delist_date > ?)
              AND list_status != 'P'
            ORDER BY symbol
        """
        return self._read_frame(query, (as_of, as_of))

    def load_etf_daily(
        self,
        as_of: str,
        start_date: str | None = None,
        symbols: set[str] | frozenset[str] | None = None,
    ) -> pd.DataFrame:
        query = "SELECT * FROM etf_daily WHERE trade_date <= ?"
        params: list[str] = [as_of]
        if start_date:
            query += " AND trade_date >= ?"
            params.append(start_date)
        if symbols:
            ordered_symbols = sorted(symbols)
            query += " AND symbol IN (" + ",".join("?" for _ in ordered_symbols) + ")"
            params.extend(ordered_symbols)
        query += " ORDER BY symbol, trade_date"
        return self._read_frame(query, tuple(params))

    def load_minute_bars(self, start_time: str, end_time: str, symbols: set[str] | frozenset[str]) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()
        ordered_symbols = sorted(str(item).upper() for item in symbols)
        placeholders = ",".join("?" for _ in ordered_symbols)
        query = (
            "SELECT symbol, trade_time, open, high, low, close, volume, amount "
            "FROM etf_minute WHERE trade_time >= ? AND trade_time <= ? "
            f"AND symbol IN ({placeholders}) ORDER BY trade_time, symbol"
        )
        result = self._read_frame(query, (start_time, end_time, *ordered_symbols))
        if not result.empty:
            result["trade_time"] = pd.to_datetime(result["trade_time"], errors="coerce")
            result = result.dropna(subset=["trade_time"])
        return result

    def minute_row_count(self, symbol: str, start_time: str, end_time: str) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS count FROM etf_minute
                WHERE symbol = ? AND trade_time >= ? AND trade_time <= ?
                """,
                (str(symbol).upper(), start_time, end_time),
            ).fetchone()
        return int(row["count"] if row else 0)

    def minute_window_is_cached(self, symbol: str, start_time: str, end_time: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT 1 FROM etf_minute_fetch_window
                WHERE symbol = ? AND start_time = ? AND end_time = ?
                """,
                (str(symbol).upper(), start_time, end_time),
            ).fetchone()
        return row is not None

    def mark_minute_window_cached(self, symbol: str, start_time: str, end_time: str, row_count: int) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO etf_minute_fetch_window (symbol, start_time, end_time, row_count, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, start_time, end_time) DO UPDATE SET
                    row_count=excluded.row_count, fetched_at=excluded.fetched_at
                """,
                (str(symbol).upper(), start_time, end_time, int(row_count), _now()),
            )

    def load_index_daily(self, as_of: str, start_date: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM etf_index_daily WHERE trade_date <= ?"
        params: list[str] = [as_of]
        if start_date:
            query += " AND trade_date >= ?"
            params.append(start_date)
        query += " ORDER BY symbol, trade_date"
        return self._read_frame(query, tuple(params))

    def latest_trade_date(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT MAX(trade_date) AS trade_date FROM etf_daily").fetchone()
        return str(row["trade_date"]) if row and row["trade_date"] else None

    def has_daily_snapshot(self, trade_date: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM etf_daily WHERE trade_date = ?",
                (trade_date,),
            ).fetchone()
        return bool(row and int(row["count"]) > 0)

    def load_latest_nav(self, symbol: str, as_of: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM etf_nav
                WHERE symbol = ? AND nav_date <= ?
                ORDER BY nav_date DESC LIMIT 1
                """,
                (symbol, as_of),
            ).fetchone()
        return dict(row) if row else None

    def save_decision(self, payload: dict[str, Any]) -> str:
        created_at = _now()
        decision_id = f"etf-{uuid4().hex}"
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO etf_decisions (
                    decision_id, as_of, strategy_id, signal, target_symbol,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    str(payload.get("as_of") or ""),
                    str(payload.get("strategy_id") or ""),
                    str(payload.get("signal") or "HOLD"),
                    str(target.get("symbol") or ""),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
        return decision_id

    def load_wufu_v7_session(self, trade_date: str) -> dict[str, Any]:
        """Return source-strategy intraday state for one trading day.

        The source file relies on its platform scheduler retaining the 13:08
        ranking until the later sell/buy callbacks. This table only replaces
        that platform state; it does not add a trading rule.
        """
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json FROM etf_wufu_v7_sessions WHERE trade_date = ?",
                (str(trade_date),),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def save_wufu_v7_session(self, trade_date: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO etf_wufu_v7_sessions (trade_date, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (str(trade_date), json.dumps(payload, ensure_ascii=False, sort_keys=True), _now()),
            )

    def load_paper_account(self, account_id: str, initial_cash: float, trade_date: str) -> dict[str, Any]:
        """Load the independent ETF paper account and roll T+1 availability forward."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM etf_paper_accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                db.execute(
                    """
                    INSERT INTO etf_paper_accounts (account_id, cash, initial_cash, realized_pnl, updated_at)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (account_id, float(initial_cash), float(initial_cash), _now()),
                )
                account = {
                    "account_id": account_id,
                    "cash": float(initial_cash),
                    "initial_cash": float(initial_cash),
                    "realized_pnl": 0.0,
                }
            else:
                account = dict(row)

            db.execute(
                """
                UPDATE etf_paper_positions
                SET available_quantity = quantity, updated_at = ?
                WHERE account_id = ? AND last_trade_date < ?
                """,
                (_now(), account_id, trade_date),
            )
            rows = db.execute(
                "SELECT * FROM etf_paper_positions WHERE account_id = ? ORDER BY symbol", (account_id,)
            ).fetchall()
        return {"account": account, "positions": {str(row["symbol"]): dict(row) for row in rows}}

    def has_paper_order(self, dedupe_key: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM etf_paper_orders WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
        return row is not None

    def save_paper_account_state(
        self,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        order: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically persist ETF paper-account state and a deduplicated order."""
        account_id = str(account["account_id"])
        now = _now()
        with self._connect() as db:
            try:
                if order:
                    db.execute(
                        """
                        INSERT INTO etf_paper_orders (
                            order_id, dedupe_key, account_id, side, symbol, name, status,
                            quantity, price, commission, reason, execution_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(order["order_id"]),
                            str(order["dedupe_key"]),
                            account_id,
                            str(order["side"]),
                            str(order["symbol"]),
                            str(order["name"]),
                            str(order["status"]),
                            int(order["quantity"]),
                            float(order["price"]),
                            float(order["commission"]),
                            str(order["reason"]),
                            json.dumps(_order_execution_context(order), ensure_ascii=False, sort_keys=True),
                            str(order["created_at"]),
                        ),
                    )
            except sqlite3.IntegrityError:
                return False

            db.execute(
                """
                INSERT INTO etf_paper_accounts (account_id, cash, initial_cash, realized_pnl, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    cash=excluded.cash, initial_cash=excluded.initial_cash,
                    realized_pnl=excluded.realized_pnl, updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    float(account["cash"]),
                    float(account["initial_cash"]),
                    float(account.get("realized_pnl") or 0.0),
                    now,
                ),
            )
            db.execute("DELETE FROM etf_paper_positions WHERE account_id = ?", (account_id,))
            db.executemany(
                """
                INSERT INTO etf_paper_positions (
                    account_id, symbol, name, quantity, available_quantity, avg_cost,
                    last_price, last_trade_date, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_id,
                        symbol,
                        str(position["name"]),
                        int(position["quantity"]),
                        int(position["available_quantity"]),
                        float(position["avg_cost"]),
                        float(position["last_price"]),
                        str(position["last_trade_date"]),
                        now,
                    )
                    for symbol, position in positions.items()
                    if int(position.get("quantity") or 0) > 0
                ],
            )
        return True

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30.0)
        db.execute("PRAGMA busy_timeout=30000")
        db.row_factory = sqlite3.Row
        return db

    def _read_frame(self, query: str, params: tuple[Any, ...]) -> pd.DataFrame:
        with self._connect() as db:
            return pd.read_sql_query(query, db, params=params)


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def _float(value: Any) -> float:
    try:
        return float(value) if value not in {None, ""} else 0.0
    except (TypeError, ValueError):
        return 0.0
