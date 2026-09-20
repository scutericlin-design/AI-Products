from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from hot_leader_strategy.models import DailyBar, Instrument


TZ = ZoneInfo("Asia/Shanghai")


class HotLeaderStore:
    """SQLite state isolated from stock, ETF and Q-GARP strategy tables/files."""

    def __init__(self, db_path: Path, *, read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        if not self.read_only:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.init_db()

    def init_db(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS hot_leader_instruments (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT, list_date TEXT, delist_date TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_bars (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, pre_close REAL, pct_chg REAL, amount REAL, volume REAL, adj_factor REAL, source TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(symbol, trade_date));
            CREATE INDEX IF NOT EXISTS idx_hot_leader_bars_date ON hot_leader_bars(trade_date, symbol);
            CREATE TABLE IF NOT EXISTS hot_leader_universe (effective_date TEXT NOT NULL, symbol TEXT NOT NULL, score REAL NOT NULL DEFAULT 0, source TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(effective_date, symbol));
            CREATE TABLE IF NOT EXISTS hot_leader_theme_runs (run_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_signals (signal_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, signal TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_paper_accounts (account_id TEXT PRIMARY KEY, cash REAL NOT NULL, initial_cash REAL NOT NULL, realized_pnl REAL NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_paper_positions (account_id TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, industry TEXT, quantity INTEGER NOT NULL, available_quantity INTEGER NOT NULL, avg_cost REAL NOT NULL, last_price REAL NOT NULL, entry_date TEXT NOT NULL, stop_loss REAL, take_profit REAL, trailing_stop_pct REAL, high_watermark REAL, strategy_reason TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(account_id, symbol));
            CREATE TABLE IF NOT EXISTS hot_leader_paper_orders (order_id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, account_id TEXT NOT NULL, side TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, commission REAL NOT NULL, tax REAL NOT NULL, trigger_price REAL, target_weight REAL, stop_loss REAL, take_profit REAL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_signal_executions (signal_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_execution_attempts (attempt_id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, signal_id TEXT NOT NULL, trade_date TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, candidate_count INTEGER NOT NULL, quote_count INTEGER NOT NULL, quote_source TEXT, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_hot_leader_execution_attempts_time ON hot_leader_execution_attempts(created_at DESC);
            CREATE TABLE IF NOT EXISTS hot_leader_intraday_snapshots (snapshot_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, symbol TEXT NOT NULL, price REAL NOT NULL, reference_price REAL, change_pct REAL, source TEXT NOT NULL, recorded_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_hot_leader_intraday_snapshots_symbol_time ON hot_leader_intraday_snapshots(symbol, recorded_at DESC);
            CREATE TABLE IF NOT EXISTS hot_leader_intraday_events (event_id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_hot_leader_intraday_events_symbol_time ON hot_leader_intraday_events(symbol, event_type, created_at DESC);
            CREATE TABLE IF NOT EXISTS hot_leader_live_theme_runs (run_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL, provider_timestamp TEXT, universe_count INTEGER NOT NULL, quote_count INTEGER NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_hot_leader_live_theme_runs_time ON hot_leader_live_theme_runs(observed_at DESC);
            CREATE TABLE IF NOT EXISTS hot_leader_backtests (run_id TEXT PRIMARY KEY, status TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, metrics_json TEXT NOT NULL, assumptions_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hot_leader_quality_checks (check_id TEXT PRIMARY KEY, check_type TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, as_of TEXT, created_at TEXT NOT NULL);
            """)

    def upsert_instruments(self, rows: Iterable[Instrument]) -> int:
        values = [(r.symbol, r.name, r.industry, r.list_date, r.delist_date, _now()) for r in rows if r.symbol]
        with self._connect() as db:
            db.executemany("INSERT INTO hot_leader_instruments VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,industry=excluded.industry,list_date=excluded.list_date,delist_date=excluded.delist_date,updated_at=excluded.updated_at", values)
        return len(values)

    def instruments(self, as_of: str | None = None) -> list[Instrument]:
        sql, args = "SELECT symbol,name,industry,list_date,delist_date FROM hot_leader_instruments", []
        if as_of:
            sql += " WHERE (list_date='' OR list_date<=?) AND (delist_date='' OR delist_date>?)"
            args = [as_of, as_of]
        return [Instrument(**row) for row in self._query(sql, tuple(args))]

    def upsert_bars(self, rows: Iterable[DailyBar]) -> int:
        values = [(r.symbol,r.trade_date,r.open,r.high,r.low,r.close,r.pre_close,r.pct_chg,r.amount,r.volume,r.adj_factor,r.source,_now()) for r in rows if r.symbol and r.trade_date]
        with self._connect() as db:
            db.executemany("""INSERT INTO hot_leader_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,trade_date) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,pre_close=excluded.pre_close,pct_chg=excluded.pct_chg,amount=excluded.amount,volume=excluded.volume,adj_factor=COALESCE(excluded.adj_factor,hot_leader_bars.adj_factor),source=excluded.source,updated_at=excluded.updated_at""", values)
        return len(values)

    def bars(self, symbol: str, as_of: str | None = None, limit: int = 180) -> list[DailyBar]:
        where, args = "symbol=?", [symbol]
        if as_of:
            where += " AND trade_date<=?"; args.append(as_of)
        args.append(limit)
        rows = self._query(f"SELECT symbol,trade_date,open,high,low,close,pre_close,pct_chg,amount,volume,adj_factor,source FROM hot_leader_bars WHERE {where} ORDER BY trade_date DESC LIMIT ?", tuple(args))
        return [DailyBar(**row) for row in reversed(rows)]

    def trade_dates(self, start: str, end: str) -> list[str]:
        return [row["trade_date"] for row in self._query("SELECT DISTINCT trade_date FROM hot_leader_bars WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date", (start,end))]

    def adjustment_coverage(self, start: str, end: str) -> dict[str, int]:
        rows=self._query("SELECT COUNT(*) total,SUM(CASE WHEN adj_factor IS NOT NULL AND adj_factor>0 THEN 1 ELSE 0 END) adjusted FROM hot_leader_bars WHERE trade_date>=? AND trade_date<=?",(start,end))
        return {"total":int(rows[0].get("total") or 0),"adjusted":int(rows[0].get("adjusted") or 0)}

    def upsert_universe(self, effective_date: str, rows: Iterable[tuple[str, float]], source: str) -> int:
        values = [(effective_date, symbol, score, source, _now()) for symbol, score in rows if symbol]
        with self._connect() as db:
            db.executemany("INSERT INTO hot_leader_universe VALUES(?,?,?,?,?) ON CONFLICT(effective_date,symbol) DO UPDATE SET score=excluded.score,source=excluded.source,updated_at=excluded.updated_at", values)
        return len(values)

    def universe_as_of(self, as_of: str) -> set[str]:
        rows = self._query("SELECT symbol FROM hot_leader_universe WHERE effective_date=(SELECT MAX(effective_date) FROM hot_leader_universe WHERE effective_date<=?)", (as_of,))
        return {str(row["symbol"]) for row in rows}

    def save_signal(self, payload: dict[str, Any]) -> str:
        ident = uuid4().hex
        with self._connect() as db: db.execute("INSERT INTO hot_leader_signals VALUES(?,?,?,?,?)", (ident,str(payload.get("as_of") or ""),str(payload.get("signal") or "HOLD"),_dump(payload),_now()))
        return ident

    def latest_signal(self) -> dict[str, Any]:
        rows = self._query("SELECT signal_id,as_of,signal,payload_json,created_at FROM hot_leader_signals ORDER BY created_at DESC LIMIT 1")
        return {**rows[0], "payload":_loads(rows[0]["payload_json"])} if rows else {}

    def signal_executed(self, signal_id: str) -> bool:
        return bool(signal_id and self._query("SELECT 1 FROM hot_leader_signal_executions WHERE signal_id=?", (signal_id,)))

    def mark_signal_execution(self, signal_id: str, trade_date: str, status: str, detail: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO hot_leader_signal_executions VALUES(?,?,?,?,?)", (signal_id,trade_date,status,detail,_now()))

    def record_execution_attempt(self, *, signal_id: str, trade_date: str, stage: str, status: str,
                                 candidate_count: int, quote_count: int, quote_source: str = "",
                                 detail: str = "") -> bool:
        """Persist one deduplicated paper-execution outcome; never changes orders."""
        if not signal_id or not trade_date or not stage or not status:
            return False
        normalized = " ".join(str(detail or "").split())[:500]
        key = ":".join((signal_id, trade_date, stage, status, str(candidate_count), str(quote_count), quote_source, normalized))
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO hot_leader_execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uuid4().hex, key, signal_id, trade_date, stage, status, max(int(candidate_count), 0),
                 max(int(quote_count), 0), quote_source[:80], normalized, _now()),
            )
        return cursor.rowcount == 1

    def recent_execution_attempts(self, limit: int = 30) -> list[dict[str, Any]]:
        try:
            return self._query(
                "SELECT signal_id,trade_date,stage,status,candidate_count,quote_count,quote_source,detail,created_at "
                "FROM hot_leader_execution_attempts ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise

    def save_intraday_snapshots(self, trade_date: str, rows: Iterable[dict[str, Any]]) -> int:
        recorded_at = _now()
        values = [
            (
                uuid4().hex,
                trade_date,
                str(row.get("symbol") or ""),
                float(row.get("price") or 0),
                _number_or_none(row.get("reference_price")),
                _number_or_none(row.get("change_pct")),
                str(row.get("source") or "tushare:realtime_quote"),
                recorded_at,
            )
            for row in rows
            if str(row.get("symbol") or "") and float(row.get("price") or 0) > 0
        ]
        if not values:
            return 0
        with self._connect() as db:
            db.executemany("INSERT INTO hot_leader_intraday_snapshots VALUES(?,?,?,?,?,?,?,?)", values)
        return len(values)

    def record_intraday_event(self, *, dedupe_key: str, trade_date: str, symbol: str, event_type: str, status: str, payload: dict[str, Any]) -> bool:
        if not dedupe_key or not symbol:
            return False
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO hot_leader_intraday_events VALUES(?,?,?,?,?,?,?,?)",
                (uuid4().hex, dedupe_key, trade_date, symbol, event_type, status, _dump(payload), _now()),
            )
        return cursor.rowcount == 1

    def intraday_event_in_cooldown(self, symbol: str, event_type: str, cooldown_minutes: int, now: datetime | None = None) -> bool:
        cutoff = (now or datetime.now(TZ)) - timedelta(minutes=max(cooldown_minutes, 0))
        return bool(
            self._query(
                "SELECT 1 FROM hot_leader_intraday_events WHERE symbol=? AND event_type=? AND status='filled' AND created_at>=? LIMIT 1",
                (symbol, event_type, cutoff.isoformat(timespec="seconds")),
            )
        )

    def latest_intraday_snapshots(self, limit: int = 12) -> list[dict[str, Any]]:
        return self._query(
            """SELECT snapshots.trade_date,snapshots.symbol,snapshots.price,snapshots.reference_price,snapshots.change_pct,snapshots.source,snapshots.recorded_at
            FROM hot_leader_intraday_snapshots AS snapshots
            INNER JOIN (
                SELECT symbol,MAX(recorded_at) AS recorded_at
                FROM hot_leader_intraday_snapshots GROUP BY symbol
            ) AS latest ON snapshots.symbol=latest.symbol AND snapshots.recorded_at=latest.recorded_at
            ORDER BY snapshots.recorded_at DESC LIMIT ?""",
            (limit,),
        )

    def recent_intraday_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {**row, "payload": _loads(row["payload_json"])}
            for row in self._query(
                "SELECT event_id,trade_date,symbol,event_type,status,payload_json,created_at FROM hot_leader_intraday_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def save_live_theme_run(self, *, trade_date: str, status: str, source: str, observed_at: str,
                            provider_timestamp: str | None, universe_count: int, quote_count: int,
                            payload: dict[str, Any]) -> str:
        ident = uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO hot_leader_live_theme_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (ident, trade_date, status, source, observed_at, provider_timestamp, universe_count, quote_count, _dump(payload), _now()))
        return ident

    def latest_live_theme_run(self) -> dict[str, Any]:
        try:
            rows = self._query("SELECT run_id,trade_date,status,source,observed_at,provider_timestamp,universe_count,quote_count,payload_json,created_at FROM hot_leader_live_theme_runs ORDER BY observed_at DESC LIMIT 1")
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise
        return {**rows[0], "payload": _loads(rows[0]["payload_json"])} if rows else {}

    def recent_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        return [{**row,"payload":_loads(row["payload_json"])} for row in self._query("SELECT signal_id,as_of,signal,payload_json,created_at FROM hot_leader_signals ORDER BY created_at DESC LIMIT ?", (limit,))]

    def save_theme_run(self, as_of: str, payload: dict[str, Any]) -> None:
        with self._connect() as db: db.execute("INSERT INTO hot_leader_theme_runs VALUES(?,?,?,?)", (uuid4().hex,as_of,_dump(payload),_now()))

    def load_account(self, account_id: str, initial_cash: float, trade_date: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        accounts = self._query("SELECT * FROM hot_leader_paper_accounts WHERE account_id=?", (account_id,))
        account = accounts[0] if accounts else {"account_id":account_id,"cash":initial_cash,"initial_cash":initial_cash,"realized_pnl":0.0,"updated_at":_now()}
        positions = {row["symbol"]:row for row in self._query("SELECT * FROM hot_leader_paper_positions WHERE account_id=?", (account_id,))}
        for position in positions.values():
            if position["entry_date"] != trade_date: position["available_quantity"] = position["quantity"]
        return account, positions

    def save_account(self, account: dict[str, Any], positions: dict[str,dict[str,Any]], orders: list[dict[str,Any]], status: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO hot_leader_paper_accounts VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET cash=excluded.cash,initial_cash=excluded.initial_cash,realized_pnl=excluded.realized_pnl,updated_at=excluded.updated_at", (account["account_id"],account["cash"],account["initial_cash"],account.get("realized_pnl",0),_now()))
            db.execute("DELETE FROM hot_leader_paper_positions WHERE account_id=?", (account["account_id"],))
            for p in positions.values(): db.execute("INSERT INTO hot_leader_paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (account["account_id"],p["symbol"],p["name"],p.get("industry",""),p["quantity"],p["available_quantity"],p["avg_cost"],p["last_price"],p["entry_date"],p.get("stop_loss"),p.get("take_profit"),p.get("trailing_stop_pct"),p.get("high_watermark"),p.get("strategy_reason",""),_now()))
            for o in orders: db.execute("INSERT OR IGNORE INTO hot_leader_paper_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (o["order_id"],o["dedupe_key"],account["account_id"],o["side"],o["symbol"],o["name"],o["status"],o["quantity"],o["price"],o["commission"],o.get("tax",0),o.get("trigger_price"),o.get("target_weight"),o.get("stop_loss"),o.get("take_profit"),o["reason"],o["created_at"]))

    def has_order(self, dedupe_key: str) -> bool: return bool(self._query("SELECT 1 FROM hot_leader_paper_orders WHERE dedupe_key=?", (dedupe_key,)))
    def paper_snapshot(self, account_id: str, initial_cash: float) -> dict[str,Any]:
        account, positions=self.load_account(account_id,initial_cash,""); mv=sum(float(p["quantity"])*float(p["last_price"]) for p in positions.values()); equity=float(account["cash"])+mv
        return {"account":account,"positions":list(positions.values()),"cash":account["cash"],"market_value":round(mv,2),"equity":round(equity,2),"return_pct":equity/float(account["initial_cash"])-1}
    def recent_orders(self, limit: int=40) -> list[dict[str,Any]]: return self._query("SELECT * FROM hot_leader_paper_orders ORDER BY created_at DESC LIMIT ?", (limit,))
    def log_quality(self, kind:str,status:str,detail:str,as_of:str="") -> None:
        with self._connect() as db: db.execute("INSERT INTO hot_leader_quality_checks VALUES(?,?,?,?,?,?)", (uuid4().hex,kind,status,detail,as_of,_now()))
    def recent_quality(self, limit:int=20) -> list[dict[str,Any]]: return self._query("SELECT check_type,status,detail,as_of,created_at FROM hot_leader_quality_checks ORDER BY created_at DESC LIMIT ?", (limit,))
    def log_backtest(self,status:str,start:str,end:str,metrics:dict[str,Any],assumptions:dict[str,Any],result:dict[str,Any]) -> str:
        ident=uuid4().hex
        with self._connect() as db: db.execute("INSERT INTO hot_leader_backtests VALUES(?,?,?,?,?,?,?,?)", (ident,status,start,end,_dump(metrics),_dump(assumptions),_dump(result),_now()))
        return ident
    def recent_backtests(self,limit:int=10) -> list[dict[str,Any]]: return [{**r,"metrics":_loads(r["metrics_json"]),"assumptions":_loads(r["assumptions_json"])} for r in self._query("SELECT * FROM hot_leader_backtests ORDER BY created_at DESC LIMIT ?", (limit,))]
    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True) if self.read_only else sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db
    def _query(self, sql:str,args:tuple[Any,...]=()) -> list[dict[str,Any]]:
        with self._connect() as db:return [dict(row) for row in db.execute(sql,args).fetchall()]


def _now() -> str: return datetime.now(TZ).isoformat(timespec="seconds")
def _dump(value: Any) -> str: return json.dumps(value,ensure_ascii=False,default=str)
def _loads(value:str) -> dict[str,Any]:
    try: parsed=json.loads(value); return parsed if isinstance(parsed,dict) else {}
    except (TypeError,json.JSONDecodeError): return {}


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None
