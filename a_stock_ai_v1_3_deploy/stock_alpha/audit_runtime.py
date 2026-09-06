"""Read-only stock runtime audit. Excludes auth, API configuration and other accounts."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def audit(path: Path, start: str) -> dict:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        cycles = db.execute("SELECT started_at,status,payload_json FROM cycle_logs WHERE started_at>=? ORDER BY id", (start,)).fetchall()
        trades = [dict(row) for row in db.execute(
            "SELECT account_id,symbol,name,side,quantity,price,amount,fee,tax,realized_pnl,created_at FROM paper_trades WHERE created_at>=? ORDER BY id", (start,))]
        order_states = [dict(row) for row in db.execute(
            "SELECT side,status,reason,COUNT(*) AS count FROM paper_orders WHERE created_at>=? GROUP BY side,status,reason ORDER BY count DESC", (start,))]
        snapshots = [dict(row) for row in db.execute(
            "SELECT account_id,status,equity,cash,realized_pnl,created_at FROM paper_account_snapshots WHERE created_at>=? ORDER BY id", (start,))]
    states, ai_states, flags = Counter(), Counter(), Counter()
    false_downtrends, exits = [], Counter()
    for row in cycles:
        payload = json.loads(row["payload_json"] or "{}")
        final = payload.get("final_signal") or {}
        state = (final.get("state") or {}).get("state", "unknown")
        states[(state, final.get("signal", "unknown"))] += 1
        ai_states["degraded" if final.get("ai_degraded") else "normal"] += 1
        flags.update(final.get("risk_flags") or [])
        if "forced_downtrend_sell" in (final.get("risk_flags") or []) and state != "DOWNTREND":
            false_downtrends.append(row["started_at"])
        plan = ((payload.get("paper_simulation") or {}).get("summary") or {}).get("execution_policy") or {}
        exits.update((plan.get("sell_reasons") or {}).values())
    sessions = {}
    for row in snapshots:
        key = row["account_id"] or "legacy_unknown"
        entry = sessions.setdefault(key, {"first": row, "last": row, "resets": []})
        entry["last"] = row
        if row["status"] == "reset":
            entry["resets"].append(row)
    return {
        "from": start, "through": cycles[-1]["started_at"] if cycles else None,
        "cycles": len(cycles), "status": dict(Counter(row["status"] for row in cycles)),
        "state_signal_counts": [{"state": key[0], "signal": key[1], "count": value} for key, value in states.items()],
        "ai": dict(ai_states), "risk_flags": dict(flags), "false_downtrend_cycles": false_downtrends,
        "exit_reasons": dict(exits), "orders": order_states, "trades": trades, "sessions": sessions,
        "costs": sum(row["fee"] + row["tax"] for row in trades),
        "realized_pnl": sum(row["realized_pnl"] for row in trades),
        "note": "Read-only stock records; a partial first-day snapshot is not initial account capital",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--start", default="2026-08-31")
    args = parser.parse_args()
    print(json.dumps(audit(args.database, args.start), ensure_ascii=False, indent=2))
