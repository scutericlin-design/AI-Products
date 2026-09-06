"""Read-only projection of the isolated Stock Alpha ledger; never imports a worker."""

from contextlib import closing, contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
from zoneinfo import ZoneInfo


ACCOUNTS = ("C_ai", "B_enhanced", "A_baseline")
TZ = ZoneInfo("Asia/Shanghai")


@contextmanager
def ledger_snapshot(root: Path):
    # The worker takes this same lock for every ledger write. Never ignore a WAL.
    with tempfile.TemporaryDirectory(prefix="stock-alpha-read-") as folder:
        target = Path(folder) / "paper.sqlite"
        with (root / "worker.lock").open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            try:
                for suffix in ("", "-wal"):
                    source = root / ("paper.sqlite" + suffix)
                    if suffix and not source.exists():
                        continue
                    if source.stat().st_size > 128 * 1024 * 1024:
                        raise ValueError("Snapshot exceeds read budget")
                    shutil.copyfile(source, Path(str(target) + suffix))
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            yield db


def build_stock_alpha_payload(directory: Path | None = None) -> dict:
    root = directory or Path(os.getenv("STOCK_ALPHA_DASHBOARD_HOME", "/app/stock_alpha_runtime"))
    result = {"strategy": "stock_alpha", "read_only": True, "paper_only": True,
              "official_account": "C_ai", "status": "unavailable", "accounts": {},
              "fetched_at": datetime.now(TZ).isoformat()}
    path = root / "paper.sqlite"
    if not path.is_file():
        return {**result, "message": "独立模拟账本暂不可用，未创建或重置账户。"}
    try:
        with ledger_snapshot(root) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            stored = {r["key"]: json.loads(r["payload"]) for r in db.execute(
                "SELECT key,payload FROM state WHERE key IN ('plans','last_ai','snapshot_as_of')")}
            accounts = {}
            for account in ACCOUNTS:
                row = db.execute("SELECT id,initial_cash,cash FROM accounts WHERE id=?", (account,)).fetchone()
                if row is None or row["initial_cash"] <= 0:
                    raise ValueError("missing account")
                info = dict(row)
                positions = [dict(r) for r in db.execute("SELECT symbol,name,qty,cost,mark,mark_at,entry_date FROM positions WHERE account=? ORDER BY symbol", (account,))]
                info["positions"] = positions
                info["equity"] = info["cash"] + sum(p["qty"] * p["mark"] for p in positions)
                info["return_pct"] = (info["equity"] / info["initial_cash"] - 1) * 100
                totals = db.execute("SELECT COUNT(*) AS trades,COALESCE(SUM(fee),0) AS fees,COALESCE(SUM(realized),0) AS realized_pnl FROM trades WHERE account=?", (account,)).fetchone()
                info.update(dict(totals))
                info["recent_trades"] = [dict(r) for r in db.execute("SELECT id,symbol,name,side,qty,price,fee,realized,at,ai_mode,reason FROM trades WHERE account=? ORDER BY id DESC LIMIT 100", (account,))]
                # Daily final snapshots bound the chart response without inventing an intraday curve.
                info["curve"] = [dict(r) for r in db.execute("SELECT at,nav FROM cycles WHERE account=? AND at IN (SELECT MAX(at) FROM cycles WHERE account=? GROUP BY substr(at,1,10)) ORDER BY at", (account, account))]
                peak, drawdown = info["initial_cash"], 0
                for point in db.execute("SELECT nav FROM cycles WHERE account=? ORDER BY at", (account,)):
                    peak = max(peak, point["nav"])
                    drawdown = max(drawdown, 1 - point["nav"] / peak)
                info["max_drawdown_pct"] = drawdown * 100
                latest = db.execute("SELECT at,payload FROM cycles WHERE account=? ORDER BY at DESC LIMIT 1", (account,)).fetchone()
                info["updated_at"] = latest["at"] if latest else None
                info["stale_marks"] = json.loads(latest["payload"]).get("stale_marks", []) if latest else []
                info["recent_blocks"] = [dict(r) for r in db.execute("SELECT cycle,symbol,side,status,reason FROM attempts WHERE account=? ORDER BY rowid DESC LIMIT 30", (account,))]
                plan = stored.get("plans", {}).get(account, {})
                info["plan"] = {key: plan.get(key) for key in ("signal_date", "prepared_at", "ai_mode", "ai_effect", "ai_positive_active_weight")}
                info["plan"]["targets"] = [{"symbol": s, "weight": w} for s, w in plan.get("weights", {}).items()]
                info["plan"]["evidence"] = [{k: item.get(k) for k in ("ts_code", "thesis", "counter_evidence", "event_ids")} for item in plan.get("ai_evidence", [])]
                accounts[account] = info
            ai = stored.get("last_ai", {})
            result.update(status="ok", accounts=accounts, snapshot_as_of=stored.get("snapshot_as_of"),
                          ai={k: ai.get(k) for k in ("mode", "model")})
        for name in ("health", "heartbeat"):
            try:
                raw = json.loads((root / f"{name}.json").read_text())
                result[name] = {k: raw[k] for k in ("at", "status", "quote_count", "fresh_quote_count", "filled", "corporate_action_pending") if k in raw}
            except (OSError, ValueError, TypeError):
                result[name] = {}
        heartbeat = result["heartbeat"].get("at")
        age = (datetime.now(TZ) - datetime.fromisoformat(heartbeat)).total_seconds() if heartbeat else None
        result["worker_stale"] = age is None or not 0 <= age <= 900
        return result
    except (sqlite3.Error, ValueError, TypeError, KeyError, OSError):
        return {**result, "status": "unavailable", "accounts": {}, "message": "独立账本暂不可读，请检查服务或挂载；其他策略不受此接口影响。"}
