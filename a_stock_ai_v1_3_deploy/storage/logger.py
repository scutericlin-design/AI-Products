from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from scheduler.trading_calendar import BEIJING_TZ, current_trading_window


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _json(value: Any) -> str:
    def default(item: Any) -> Any:
        if is_dataclass(item):
            return asdict(item)
        return str(item)

    return json.dumps(value, ensure_ascii=False, default=default)


def init_db() -> None:
    with _connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cycle_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL UNIQUE,
                trigger_source TEXT NOT NULL,
                status TEXT NOT NULL,
                market_phase TEXT,
                quote_count INTEGER DEFAULT 0,
                leader_count INTEGER DEFAULT 0,
                decision_count INTEGER DEFAULT 0,
                push_status TEXT,
                error_text TEXT,
                payload_json TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS decision_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                action TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                target_weight REAL DEFAULT 0,
                risk_level TEXT,
                risk_flags TEXT,
                reason TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS push_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                response_code INTEGER,
                response_text TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL UNIQUE,
                review_date TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                article_markdown TEXT,
                summary_json TEXT,
                push_status TEXT,
                response_code INTEGER,
                response_text TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS push_dedupe_state (
                scope TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                summary_json TEXT,
                sent_count INTEGER DEFAULT 1,
                first_sent_at TEXT NOT NULL,
                last_sent_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                trigger_source TEXT NOT NULL,
                status TEXT NOT NULL,
                sample_count INTEGER DEFAULT 0,
                evaluated_count INTEGER DEFAULT 0,
                metrics_json TEXT,
                proposal_json TEXT,
                applied_json TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_param_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                params_json TEXT NOT NULL,
                changes_json TEXT,
                metrics_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL UNIQUE,
                cycle_id TEXT,
                status TEXT NOT NULL,
                cash REAL DEFAULT 0,
                equity REAL DEFAULT 0,
                market_value REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                positions_json TEXT,
                orders_json TEXT,
                trades_json TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL UNIQUE,
                cycle_id TEXT,
                symbol TEXT NOT NULL,
                name TEXT,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                quantity INTEGER DEFAULT 0,
                price REAL DEFAULT 0,
                reason TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL UNIQUE,
                order_id TEXT,
                cycle_id TEXT,
                symbol TEXT NOT NULL,
                name TEXT,
                side TEXT NOT NULL,
                quantity INTEGER DEFAULT 0,
                price REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                fee REAL DEFAULT 0,
                tax REAL DEFAULT 0,
                slippage REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                strategy_name TEXT NOT NULL,
                status TEXT NOT NULL,
                lookback_days INTEGER DEFAULT 0,
                holding_days INTEGER DEFAULT 0,
                trade_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                total_return_pct REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                metrics_json TEXT,
                trades_json TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def log(data: Any) -> None:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY,
            data TEXT
        )
        """
    )
    c.execute("INSERT INTO logs (data) VALUES (?)", (str(data),))
    conn.commit()
    conn.close()


def start_cycle(cycle_id: str, trigger_source: str) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT INTO cycle_logs (cycle_id, trigger_source, status, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (cycle_id, trigger_source, "running", _now()),
        )


def finish_cycle(
    cycle_id: str,
    status: str,
    market_phase: str | None = None,
    quote_count: int = 0,
    leader_count: int = 0,
    decision_count: int = 0,
    push_status: str | None = None,
    error_text: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    with _connect() as db:
        db.execute(
            """
            UPDATE cycle_logs
            SET status = ?,
                market_phase = ?,
                quote_count = ?,
                leader_count = ?,
                decision_count = ?,
                push_status = ?,
                error_text = ?,
                payload_json = ?,
                finished_at = ?
            WHERE cycle_id = ?
            """,
            (
                status,
                market_phase,
                quote_count,
                leader_count,
                decision_count,
                push_status,
                error_text,
                _json(payload or {}),
                _now(),
                cycle_id,
            ),
        )


def log_decisions(cycle_id: str, signals: Any) -> None:
    if isinstance(signals, dict) and isinstance(signals.get("recommendations"), list):
        recommendations = signals.get("recommendations") or []
        if recommendations:
            items = [
                {
                    **item,
                    "signal": item.get("action", signals.get("signal", "HOLD")),
                    "risk_level": signals.get("risk_level"),
                    "overall_signal": signals.get("signal"),
                    "reasoning": item.get("reasoning"),
                }
                for item in recommendations
            ]
        else:
            items = [signals]
    elif isinstance(signals, dict) or is_dataclass(signals):
        items = [signals]
    else:
        items = list(signals or [])

    with _connect() as db:
        for signal in items:
            payload = asdict(signal) if is_dataclass(signal) else dict(signal)
            leader = payload.get("leader") if isinstance(payload.get("leader"), dict) else {}
            symbol = payload.get("symbol") or payload.get("stock") or leader.get("stock") or "NO_LEADER"
            name = payload.get("name") or leader.get("name")
            action = payload.get("action") or payload.get("signal") or "HOLD"
            confidence = payload.get("confidence")
            target_weight = payload.get("target_weight", payload.get("position", 0.0))
            reason = payload.get("reason") or payload.get("reasoning")
            db.execute(
                """
                INSERT INTO decision_logs (
                    cycle_id, symbol, name, action, confidence, target_weight,
                    risk_level, risk_flags, reason, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    symbol,
                    name,
                    action,
                    confidence,
                    target_weight,
                    payload.get("risk_level"),
                    ",".join(payload.get("risk_flags") or []),
                    reason,
                    _json(payload),
                    _now(),
                ),
            )


def log_push(cycle_id: str, push_result: dict[str, Any]) -> None:
    with _connect() as db:
        db.execute(
            """
            INSERT INTO push_logs (
                cycle_id, channel, status, response_code, response_text, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                "feishu",
                str(push_result.get("status") or "unknown"),
                push_result.get("response_code"),
                push_result.get("response_text"),
                _json(push_result.get("payload") or {}),
                _now(),
            ),
        )


def get_push_dedupe_state(scope: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM push_dedupe_state WHERE scope = ?",
            (scope,),
        ).fetchone()
        return dict(row) if row else None


def upsert_push_dedupe_state(scope: str, fingerprint: str, summary: dict[str, Any]) -> None:
    init_db()
    now = _now()
    previous = get_push_dedupe_state(scope)
    if previous and previous.get("fingerprint") == fingerprint:
        first_sent_at = previous.get("first_sent_at") or now
        sent_count = int(previous.get("sent_count") or 0) + 1
    else:
        first_sent_at = now
        sent_count = 1

    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO push_dedupe_state (
                scope, fingerprint, summary_json, sent_count,
                first_sent_at, last_sent_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                fingerprint,
                _json(summary),
                sent_count,
                first_sent_at,
                now,
                now,
            ),
        )


def latest_cycle() -> sqlite3.Row | None:
    init_db()
    with _connect() as db:
        return db.execute("SELECT * FROM cycle_logs ORDER BY started_at DESC LIMIT 1").fetchone()


def get_cycle(cycle_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as db:
        row = db.execute("SELECT * FROM cycle_logs WHERE cycle_id = ?", (cycle_id,)).fetchone()
    return dict(row) if row else None


def get_latest_cycle_payload() -> dict[str, Any] | None:
    row = latest_cycle()
    if not row:
        return None
    return dict(row)


def fetch_daily_review_inputs(review_date: date) -> dict[str, Any]:
    init_db()
    start, end = _local_day_bounds(review_date)
    with _connect() as db:
        cycles = db.execute(
            """
            SELECT * FROM cycle_logs
            WHERE started_at >= ? AND started_at < ?
            ORDER BY started_at ASC
            """,
            (start, end),
        ).fetchall()
        decisions = db.execute(
            """
            SELECT * FROM decision_logs
            WHERE created_at >= ? AND created_at < ?
            ORDER BY confidence DESC, created_at ASC
            """,
            (start, end),
        ).fetchall()
        pushes = db.execute(
            """
            SELECT * FROM push_logs
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (start, end),
        ).fetchall()
    return {
        "review_date": review_date.isoformat(),
        "cycles": [dict(row) for row in cycles],
        "decisions": [dict(row) for row in decisions],
        "pushes": [dict(row) for row in pushes],
    }


def fetch_buy_decisions(lookback_days: int) -> list[dict[str, Any]]:
    init_db()
    start = (datetime.now(BEIJING_TZ) - timedelta(days=lookback_days)).isoformat(timespec="seconds")
    with _connect() as db:
        rows = db.execute(
            """
            SELECT *
            FROM decision_logs
            WHERE created_at >= ?
              AND UPPER(action) = 'BUY'
              AND symbol != 'NO_LEADER'
            ORDER BY created_at ASC
            """,
            (start,),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_backtest_decisions(lookback_days: int) -> list[dict[str, Any]]:
    init_db()
    start = (datetime.now(BEIJING_TZ) - timedelta(days=lookback_days)).isoformat(timespec="seconds")
    with _connect() as db:
        rows = db.execute(
            """
            SELECT *
            FROM decision_logs
            WHERE created_at >= ?
              AND UPPER(action) = 'BUY'
              AND symbol != 'NO_LEADER'
            ORDER BY created_at ASC, confidence DESC
            """,
            (start,),
        ).fetchall()
    return [dict(row) for row in rows]


def log_review(
    review_id: str,
    review_date: str,
    status: str,
    title: str,
    article_markdown: str,
    summary: dict[str, Any],
    push_result: dict[str, Any] | None = None,
    error_text: str | None = None,
) -> None:
    init_db()
    push_result = push_result or {}
    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO review_logs (
                review_id, review_date, status, title, article_markdown,
                summary_json, push_status, response_code, response_text,
                error_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                review_date,
                status,
                title,
                article_markdown,
                _json(summary),
                push_result.get("status"),
                push_result.get("response_code"),
                push_result.get("response_text"),
                error_text,
                _now(),
            ),
        )


def log_learning_run(
    run_id: str,
    trigger_source: str,
    status: str,
    sample_count: int,
    evaluated_count: int,
    metrics: dict[str, Any],
    proposal: dict[str, Any],
    applied: dict[str, Any],
    error_text: str | None = None,
) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO learning_runs (
                run_id, trigger_source, status, sample_count, evaluated_count,
                metrics_json, proposal_json, applied_json, error_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trigger_source,
                status,
                sample_count,
                evaluated_count,
                _json(metrics),
                _json(proposal),
                _json(applied),
                error_text,
                _now(),
            ),
        )


def log_strategy_param_version(
    version: int,
    status: str,
    reason: str,
    params: dict[str, Any],
    changes: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT INTO strategy_param_versions (
                version, status, reason, params_json, changes_json, metrics_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version,
                status,
                reason,
                _json(params),
                _json(changes),
                _json(metrics),
                _now(),
            ),
        )


def log_paper_account_snapshot(
    snapshot_id: str,
    cycle_id: str | None,
    status: str,
    account: dict[str, Any],
    orders: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO paper_account_snapshots (
                snapshot_id, cycle_id, status, cash, equity, market_value,
                realized_pnl, unrealized_pnl, positions_json, orders_json,
                trades_json, summary_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                cycle_id,
                status,
                account.get("cash", 0.0),
                account.get("equity", 0.0),
                account.get("market_value", 0.0),
                account.get("realized_pnl", 0.0),
                account.get("unrealized_pnl", 0.0),
                _json(account.get("positions") or {}),
                _json(orders),
                _json(trades),
                _json(summary),
                _now(),
            ),
        )


def log_paper_order(order: dict[str, Any]) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO paper_orders (
                order_id, cycle_id, symbol, name, side, status, quantity,
                price, reason, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.get("order_id"),
                order.get("cycle_id"),
                order.get("symbol"),
                order.get("name"),
                order.get("side"),
                order.get("status"),
                order.get("quantity", 0),
                order.get("price", 0.0),
                order.get("reason"),
                _json(order),
                _now(),
            ),
        )


def log_paper_trade(trade: dict[str, Any]) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO paper_trades (
                trade_id, order_id, cycle_id, symbol, name, side, quantity,
                price, amount, fee, tax, slippage, realized_pnl, payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.get("trade_id"),
                trade.get("order_id"),
                trade.get("cycle_id"),
                trade.get("symbol"),
                trade.get("name"),
                trade.get("side"),
                trade.get("quantity", 0),
                trade.get("price", 0.0),
                trade.get("amount", 0.0),
                trade.get("fee", 0.0),
                trade.get("tax", 0.0),
                trade.get("slippage", 0.0),
                trade.get("realized_pnl", 0.0),
                _json(trade),
                _now(),
            ),
        )


def log_backtest_run(
    run_id: str,
    strategy_name: str,
    status: str,
    lookback_days: int,
    holding_days: int,
    metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    error_text: str | None = None,
) -> None:
    init_db()
    with _connect() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO backtest_runs (
                run_id, strategy_name, status, lookback_days, holding_days,
                trade_count, win_rate, total_return_pct, max_drawdown_pct,
                metrics_json, trades_json, error_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                strategy_name,
                status,
                lookback_days,
                holding_days,
                int(metrics.get("trade_count") or 0),
                float(metrics.get("win_rate") or 0.0),
                float(metrics.get("total_return_pct") or 0.0),
                float(metrics.get("max_drawdown_pct") or 0.0),
                _json(metrics),
                _json(trades),
                error_text,
                _now(),
            ),
        )


def healthcheck(max_stale_seconds: int | None = None) -> tuple[bool, str]:
    window = current_trading_window()
    if not window.is_open:
        return True, f"ok market_closed phase={window.phase} reason={window.reason}"

    row = latest_cycle()
    if row is None:
        return False, "no cycle logs"
    if row["status"] == "failed":
        return False, f"latest cycle failed: {row['cycle_id']}"
    stale_seconds = max_stale_seconds or max(settings.loop_seconds * 3 + 60, 300)
    started_at = datetime.fromisoformat(row["started_at"])
    age = (datetime.now(timezone.utc) - started_at).total_seconds()
    if age > stale_seconds:
        return False, f"latest cycle stale: {age:.0f}s"
    return True, f"ok cycle_id={row['cycle_id']} status={row['status']}"


def database_path() -> Path:
    return settings.db_path


def _local_day_bounds(value: date) -> tuple[str, str]:
    start_local = datetime.combine(value, time.min, tzinfo=BEIJING_TZ)
    end_local = datetime.combine(value + timedelta(days=1), time.min, tzinfo=BEIJING_TZ)
    return start_local.isoformat(timespec="seconds"), end_local.isoformat(timespec="seconds")
