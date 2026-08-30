from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from qgarp_strategy.models import DailyBar, FundamentalSnapshot, Instrument


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class QGARPStore:
    """Local SQLite store. It intentionally shares no tables or files with stock/ETF strategies."""

    def __init__(self, db_path: Path, *, read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        if not self.read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.init_db()

    def init_db(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS qgarp_instruments (
                    symbol TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT,
                    list_date TEXT, delist_date TEXT, list_status TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qgarp_daily_bars (
                    symbol TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL, low REAL,
                    close REAL, pre_close REAL, pct_chg REAL, volume REAL, amount REAL,
                    pe_ttm REAL, pb REAL, adj_factor REAL, source TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_qgarp_daily_date ON qgarp_daily_bars(trade_date, symbol);
                CREATE TABLE IF NOT EXISTS qgarp_financials (
                    symbol TEXT NOT NULL, report_end_date TEXT NOT NULL, available_at TEXT NOT NULL,
                    roe REAL, revenue_yoy REAL, profit_yoy REAL, operating_cashflow_per_share REAL,
                    debt_to_assets REAL, source TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, report_end_date, available_at)
                );
                CREATE INDEX IF NOT EXISTS idx_qgarp_financials_asof ON qgarp_financials(symbol, available_at);
                CREATE TABLE IF NOT EXISTS qgarp_universe_memberships (
                    index_code TEXT NOT NULL, effective_date TEXT NOT NULL, symbol TEXT NOT NULL,
                    weight REAL, source TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(index_code, effective_date, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_qgarp_membership_asof ON qgarp_universe_memberships(index_code, effective_date);
                CREATE TABLE IF NOT EXISTS qgarp_factor_runs (
                    run_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qgarp_signals (
                    signal_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, signal TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qgarp_signals_created ON qgarp_signals(created_at);
                CREATE TABLE IF NOT EXISTS qgarp_paper_accounts (
                    account_id TEXT PRIMARY KEY, cash REAL NOT NULL, initial_cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qgarp_paper_positions (
                    account_id TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, industry TEXT,
                    quantity INTEGER NOT NULL, available_quantity INTEGER NOT NULL, avg_cost REAL NOT NULL,
                    last_price REAL NOT NULL, last_trade_date TEXT NOT NULL, stop_loss REAL, take_profit REAL,
                    trailing_stop REAL, strategy_reason TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS qgarp_paper_orders (
                    order_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
                    side TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
                    quantity INTEGER NOT NULL, price REAL NOT NULL, commission REAL NOT NULL, tax REAL NOT NULL,
                    trigger_price REAL, target_weight REAL, stop_loss REAL, take_profit REAL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qgarp_paper_orders_created ON qgarp_paper_orders(created_at);
                CREATE TABLE IF NOT EXISTS qgarp_paper_trades (
                    trade_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, side TEXT NOT NULL, symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL, price REAL NOT NULL, amount REAL NOT NULL, fee REAL NOT NULL,
                    tax REAL NOT NULL, realized_pnl REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qgarp_account_snapshots (
                    snapshot_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, cash REAL NOT NULL, equity REAL NOT NULL,
                    market_value REAL NOT NULL, realized_pnl REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qgarp_backtest_runs (
                    run_id TEXT PRIMARY KEY, status TEXT NOT NULL, start_date TEXT, end_date TEXT,
                    metrics_json TEXT NOT NULL, assumptions_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qgarp_data_quality_checks (
                    check_id TEXT PRIMARY KEY, check_type TEXT NOT NULL, status TEXT NOT NULL,
                    detail TEXT NOT NULL, as_of TEXT, created_at TEXT NOT NULL
                );
                """
            )
            # Existing independent Q-GARP caches predate adjusted-price support.
            # SQLite has no ADD COLUMN IF NOT EXISTS, so inspect before migrating.
            columns = {row[1] for row in db.execute("PRAGMA table_info(qgarp_daily_bars)")}
            if "adj_factor" not in columns:
                db.execute("ALTER TABLE qgarp_daily_bars ADD COLUMN adj_factor REAL")

    def upsert_instruments(self, rows: Iterable[Instrument]) -> int:
        values = [(x.symbol, x.name, x.industry, x.list_date, x.delist_date, x.list_status, _now()) for x in rows if x.symbol]
        if not values:
            return 0
        with self._connect() as db:
            db.executemany(
                """INSERT INTO qgarp_instruments(symbol,name,industry,list_date,delist_date,list_status,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,industry=excluded.industry,
                list_date=excluded.list_date,delist_date=excluded.delist_date,list_status=excluded.list_status,updated_at=excluded.updated_at""",
                values,
            )
        return len(values)

    def load_instruments(self, as_of: str | None = None, limit: int | None = None) -> list[Instrument]:
        query = "SELECT symbol,name,industry,list_date,delist_date,list_status FROM qgarp_instruments WHERE 1=1"
        params: list[Any] = []
        if as_of:
            query += " AND (list_date='' OR list_date<=?) AND (delist_date='' OR delist_date>?)"
            params.extend([as_of, as_of])
        query += " ORDER BY symbol"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return [Instrument(**row) for row in self._query(query, tuple(params))]

    def upsert_daily_bars(self, rows: Iterable[DailyBar]) -> int:
        values = [(
            x.symbol, x.trade_date, x.open, x.high, x.low, x.close, x.pre_close, x.pct_chg, x.volume,
            x.amount, x.pe_ttm, x.pb, x.adj_factor, x.source, _now(),
        ) for x in rows if x.symbol and x.trade_date]
        if not values:
            return 0
        with self._connect() as db:
            db.executemany(
                """INSERT INTO qgarp_daily_bars(symbol,trade_date,open,high,low,close,pre_close,pct_chg,volume,amount,pe_ttm,pb,adj_factor,source,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
                open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,pre_close=excluded.pre_close,
                pct_chg=excluded.pct_chg,volume=excluded.volume,amount=excluded.amount,pe_ttm=excluded.pe_ttm,pb=excluded.pb,
                adj_factor=COALESCE(excluded.adj_factor,qgarp_daily_bars.adj_factor),
                source=excluded.source,updated_at=excluded.updated_at""",
                values,
            )
        return len(values)

    def bars(self, symbol: str, as_of: str | None = None, limit: int = 180) -> list[DailyBar]:
        where = "symbol=?"
        params: list[Any] = [symbol]
        if as_of:
            where += " AND trade_date<=?"
            params.append(as_of)
        params.append(limit)
        rows = self._query(
            f"SELECT symbol,trade_date,open,high,low,close,pre_close,pct_chg,volume,amount,pe_ttm,pb,adj_factor,source FROM qgarp_daily_bars WHERE {where} ORDER BY trade_date DESC LIMIT ?",
            tuple(params),
        )
        return [DailyBar(**row) for row in reversed(rows)]

    def upsert_adjustment_factors(self, symbol: str, factors: dict[str, float | None]) -> int:
        values = [(value, symbol, trade_date) for trade_date, value in factors.items() if value is not None and value > 0]
        if not values:
            return 0
        with self._connect() as db:
            cursor = db.executemany("UPDATE qgarp_daily_bars SET adj_factor=?, updated_at=? WHERE symbol=? AND trade_date=?", [(value, _now(), item_symbol, trade_date) for value, item_symbol, trade_date in values])
        return cursor.rowcount if cursor.rowcount >= 0 else len(values)

    def adjustment_factor_coverage(self, start_date: str, end_date: str) -> dict[str, int]:
        rows = self._query("""SELECT COUNT(*) AS total, SUM(CASE WHEN adj_factor IS NOT NULL AND adj_factor>0 THEN 1 ELSE 0 END) AS adjusted
            FROM qgarp_daily_bars WHERE trade_date>=? AND trade_date<=? AND symbol<>?""", (start_date, end_date, "000906.SH"))
        row = rows[0] if rows else {}
        return {"total": int(row.get("total") or 0), "adjusted": int(row.get("adjusted") or 0)}

    def adjustment_dates_needing_enrichment(self, start_date: str, end_date: str, limit: int) -> list[str]:
        rows = self._query("""SELECT trade_date FROM qgarp_daily_bars
            WHERE trade_date>=? AND trade_date<=? AND symbol<>?
            GROUP BY trade_date
            HAVING SUM(CASE WHEN adj_factor IS NOT NULL AND adj_factor>0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) < 0.98
            ORDER BY trade_date LIMIT ?""", (start_date, end_date, "000906.SH", limit))
        return [str(row["trade_date"]) for row in rows]

    def upsert_adjustment_factors_for_date(self, trade_date: str, factors: dict[str, float | None]) -> int:
        values = [(value, _now(), symbol, trade_date) for symbol, value in factors.items() if value is not None and value > 0]
        if not values:
            return 0
        with self._connect() as db:
            cursor = db.executemany("UPDATE qgarp_daily_bars SET adj_factor=?, updated_at=? WHERE symbol=? AND trade_date=?", values)
        return cursor.rowcount if cursor.rowcount >= 0 else len(values)

    def latest_trade_date(self) -> str | None:
        rows = self._query("SELECT MAX(trade_date) AS trade_date FROM qgarp_daily_bars")
        return str(rows[0].get("trade_date") or "") or None

    def trade_dates(self, start_date: str, end_date: str) -> list[str]:
        rows = self._query("SELECT DISTINCT trade_date FROM qgarp_daily_bars WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date", (start_date, end_date))
        return [str(row["trade_date"]) for row in rows]

    def upsert_memberships(self, index_code: str, rows: Iterable[tuple[str, str, float, str]]) -> int:
        values = [(index_code, date, symbol, weight, source, _now()) for date, symbol, weight, source in rows if date and symbol]
        if not values:
            return 0
        with self._connect() as db:
            db.executemany("""INSERT INTO qgarp_universe_memberships(index_code,effective_date,symbol,weight,source,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(index_code,effective_date,symbol) DO UPDATE SET weight=excluded.weight,source=excluded.source,updated_at=excluded.updated_at""", values)
        return len(values)

    def membership_symbols_as_of(self, index_code: str, as_of: str) -> set[str]:
        rows = self._query("""SELECT symbol FROM qgarp_universe_memberships WHERE index_code=? AND effective_date=(
            SELECT MAX(effective_date) FROM qgarp_universe_memberships WHERE index_code=? AND effective_date<=?)""", (index_code, index_code, as_of))
        return {str(row["symbol"]) for row in rows}

    def upsert_financials(self, rows: Iterable[FundamentalSnapshot]) -> int:
        values = [(
            x.symbol, x.report_end_date, x.available_at, x.roe, x.revenue_yoy, x.profit_yoy,
            x.operating_cashflow_per_share, x.debt_to_assets, x.source, _now(),
        ) for x in rows if x.symbol and x.report_end_date and x.available_at]
        if not values:
            return 0
        with self._connect() as db:
            db.executemany(
                """INSERT INTO qgarp_financials(symbol,report_end_date,available_at,roe,revenue_yoy,profit_yoy,
                operating_cashflow_per_share,debt_to_assets,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,report_end_date,available_at) DO UPDATE SET roe=excluded.roe,revenue_yoy=excluded.revenue_yoy,
                profit_yoy=excluded.profit_yoy,operating_cashflow_per_share=excluded.operating_cashflow_per_share,
                debt_to_assets=excluded.debt_to_assets,source=excluded.source,updated_at=excluded.updated_at""",
                values,
            )
        return len(values)

    def fundamental_as_of(self, symbol: str, as_of: str) -> FundamentalSnapshot | None:
        rows = self._query(
            """SELECT symbol,report_end_date,available_at,roe,revenue_yoy,profit_yoy,operating_cashflow_per_share,
            debt_to_assets,source FROM qgarp_financials WHERE symbol=? AND available_at<=?
            ORDER BY available_at DESC,report_end_date DESC LIMIT 1""",
            (symbol, as_of),
        )
        return FundamentalSnapshot(**rows[0]) if rows else None

    def save_signal(self, payload: dict[str, Any]) -> str:
        signal_id = uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO qgarp_signals(signal_id,as_of,signal,payload_json,created_at) VALUES(?,?,?,?,?)",
                (signal_id, str(payload.get("as_of") or ""), str(payload.get("signal") or "HOLD"), _dump(payload), _now()),
            )
        return signal_id

    def save_factor_run(self, as_of: str, payload: dict[str, Any]) -> str:
        run_id = uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO qgarp_factor_runs(run_id,as_of,payload_json,created_at) VALUES(?,?,?,?)", (run_id, as_of, _dump(payload), _now()))
        return run_id

    def latest_signal(self) -> dict[str, Any]:
        rows = self._query("SELECT signal_id,as_of,signal,payload_json,created_at FROM qgarp_signals ORDER BY created_at DESC LIMIT 1")
        if not rows:
            return {}
        row = rows[0]
        return {**row, "payload": _loads(row.pop("payload_json", "{}"))}

    def recent_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query("SELECT signal_id,as_of,signal,payload_json,created_at FROM qgarp_signals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [{**row, "payload": _loads(row.pop("payload_json", "{}"))} for row in rows]

    def load_account(self, account_id: str, initial_cash: float, trade_date: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        rows = self._query("SELECT account_id,cash,initial_cash,realized_pnl,updated_at FROM qgarp_paper_accounts WHERE account_id=?", (account_id,))
        if rows:
            account = rows[0]
        else:
            account = {"account_id": account_id, "cash": initial_cash, "initial_cash": initial_cash, "realized_pnl": 0.0, "updated_at": _now()}
        positions = {row["symbol"]: row for row in self._query("SELECT * FROM qgarp_paper_positions WHERE account_id=?", (account_id,))}
        for position in positions.values():
            if str(position.get("last_trade_date")) != trade_date:
                position["available_quantity"] = int(position.get("quantity") or 0)
        return account, positions

    def save_account_state(self, account: dict[str, Any], positions: dict[str, dict[str, Any]], order: dict[str, Any] | None = None, trade: dict[str, Any] | None = None, status: str = "ok") -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO qgarp_paper_accounts(account_id,cash,initial_cash,realized_pnl,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET cash=excluded.cash,initial_cash=excluded.initial_cash,realized_pnl=excluded.realized_pnl,updated_at=excluded.updated_at""",
                (account["account_id"], account["cash"], account["initial_cash"], account.get("realized_pnl", 0.0), _now()),
            )
            db.execute("DELETE FROM qgarp_paper_positions WHERE account_id=?", (account["account_id"],))
            for position in positions.values():
                db.execute(
                    """INSERT INTO qgarp_paper_positions(account_id,symbol,name,industry,quantity,available_quantity,avg_cost,last_price,last_trade_date,stop_loss,take_profit,trailing_stop,strategy_reason,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (account["account_id"], position["symbol"], position["name"], position.get("industry", ""), position["quantity"], position["available_quantity"], position["avg_cost"], position["last_price"], position["last_trade_date"], position.get("stop_loss"), position.get("take_profit"), position.get("trailing_stop"), position.get("strategy_reason", ""), _now()),
                )
            if order:
                db.execute(
                    """INSERT OR IGNORE INTO qgarp_paper_orders(order_id,dedupe_key,account_id,side,symbol,name,status,quantity,price,commission,tax,trigger_price,target_weight,stop_loss,take_profit,reason,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (order["order_id"], order["dedupe_key"], account["account_id"], order["side"], order["symbol"], order["name"], order["status"], order["quantity"], order["price"], order["commission"], order.get("tax", 0.0), order.get("trigger_price"), order.get("target_weight"), order.get("stop_loss"), order.get("take_profit"), order["reason"], order["created_at"]),
                )
            if trade:
                db.execute("INSERT INTO qgarp_paper_trades(trade_id,order_id,side,symbol,quantity,price,amount,fee,tax,realized_pnl,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (trade["trade_id"], trade["order_id"], trade["side"], trade["symbol"], trade["quantity"], trade["price"], trade["amount"], trade["fee"], trade["tax"], trade.get("realized_pnl", 0.0), trade["created_at"]))
            equity, market_value = _equity(account, positions)
            db.execute("INSERT INTO qgarp_account_snapshots(snapshot_id,account_id,cash,equity,market_value,realized_pnl,status,created_at) VALUES(?,?,?,?,?,?,?,?)", (uuid4().hex, account["account_id"], account["cash"], equity, market_value, account.get("realized_pnl", 0.0), status, _now()))

    def has_order(self, dedupe_key: str) -> bool:
        return bool(self._query("SELECT 1 AS found FROM qgarp_paper_orders WHERE dedupe_key=? LIMIT 1", (dedupe_key,)))

    def paper_snapshot(self, account_id: str, initial_cash: float) -> dict[str, Any]:
        account, positions = self.load_account(account_id, initial_cash, "")
        equity, market_value = _equity(account, positions)
        return {"account": account, "positions": list(positions.values()), "cash": account["cash"], "equity": equity, "market_value": market_value, "return_pct": (equity / account["initial_cash"] - 1) if account["initial_cash"] else 0.0}

    def recent_orders(self, limit: int = 40) -> list[dict[str, Any]]:
        return self._query("SELECT order_id,side,symbol,name,status,quantity,price,commission,tax,trigger_price,target_weight,stop_loss,take_profit,reason,created_at FROM qgarp_paper_orders ORDER BY created_at DESC LIMIT ?", (limit,))

    def equity_curve(self, limit: int = 180) -> list[dict[str, Any]]:
        return list(reversed(self._query("SELECT created_at,cash,equity,market_value,realized_pnl,status FROM qgarp_account_snapshots ORDER BY created_at DESC LIMIT ?", (limit,))))

    def log_quality_check(self, check_type: str, status: str, detail: str, as_of: str | None = None) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO qgarp_data_quality_checks(check_id,check_type,status,detail,as_of,created_at) VALUES(?,?,?,?,?,?)", (uuid4().hex, check_type, status, detail, as_of, _now()))

    def recent_quality_checks(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._query("SELECT check_type,status,detail,as_of,created_at FROM qgarp_data_quality_checks ORDER BY created_at DESC LIMIT ?", (limit,))

    def log_backtest(self, status: str, start_date: str, end_date: str, metrics: dict[str, Any], assumptions: dict[str, Any], result: dict[str, Any]) -> str:
        run_id = uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO qgarp_backtest_runs(run_id,status,start_date,end_date,metrics_json,assumptions_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (run_id, status, start_date, end_date, _dump(metrics), _dump(assumptions), _dump(result), _now()))
        return run_id

    def recent_backtests(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query("SELECT run_id,status,start_date,end_date,metrics_json,assumptions_json,created_at FROM qgarp_backtest_runs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [{**row, "metrics": _loads(row.pop("metrics_json", "{}")), "assumptions": _loads(row.pop("assumptions_json", "{}"))} for row in rows]

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            db = sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)
        else:
            db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db


def _equity(account: dict[str, Any], positions: dict[str, dict[str, Any]]) -> tuple[float, float]:
    market_value = sum(float(item.get("quantity") or 0) * float(item.get("last_price") or 0) for item in positions.values())
    return round(float(account.get("cash") or 0) + market_value, 4), round(market_value, 4)


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}
