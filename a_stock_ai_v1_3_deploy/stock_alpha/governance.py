"""Read-only promotion governance for the isolated Stock Alpha paper experiment."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from stock_alpha.paper import ACCOUNTS, TZ


VERSION = "stock_alpha_governance_v1"
MIN_CLOSED_SESSIONS = 60
MIN_EXCESS_RETURN_PCT = 2.0
MAX_DRAWDOWN_WORSENING_PCT = 2.0


def evaluate(rows: Iterable[dict], now: datetime | None = None) -> dict:
    """Summarize daily paper evidence without changing a strategy or account."""
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        account, at = str(row.get("account") or ""), str(row.get("at") or "")
        if account not in ACCOUNTS or len(at) < 10:
            continue
        key = (account, at[:10])
        if key not in latest or at > str(latest[key].get("at") or ""):
            latest[key] = row
    curves: dict[str, list[dict]] = defaultdict(list)
    for (account, _), row in sorted(latest.items(), key=lambda item: (item[0][0], item[0][1])):
        try:
            nav = float(row["nav"])
        except (KeyError, TypeError, ValueError):
            continue
        if nav > 0:
            curves[account].append({"at": row["at"], "nav": nav})
    accounts = {account: _metrics(curves.get(account, [])) for account in ACCOUNTS}
    baseline, challenger = accounts["A_baseline"], accounts["B_enhanced"]
    sufficient = min(baseline["closed_sessions"], challenger["closed_sessions"]) >= MIN_CLOSED_SESSIONS
    return_gap = challenger["return_pct"] - baseline["return_pct"]
    drawdown_gap = challenger["max_drawdown_pct"] - baseline["max_drawdown_pct"]
    evidence_passed = sufficient and return_gap >= MIN_EXCESS_RETURN_PCT and drawdown_gap <= MAX_DRAWDOWN_WORSENING_PCT
    return {
        "version": VERSION,
        "updated_at": (now or datetime.now(TZ)).isoformat(),
        "automatic_promotion": False,
        "status": "review_ready" if evidence_passed else "collecting_evidence",
        "policy": {
            "minimum_closed_sessions": MIN_CLOSED_SESSIONS,
            "minimum_enhanced_excess_return_pct": MIN_EXCESS_RETURN_PCT,
            "maximum_drawdown_worsening_pct": MAX_DRAWDOWN_WORSENING_PCT,
            "requires_human_review": True,
            "parameter_changes": "disabled",
        },
        "accounts": accounts,
        "comparison": {
            "challenger": "B_enhanced",
            "comparator": "A_baseline",
            "return_gap_pct": return_gap,
            "drawdown_gap_pct": drawdown_gap,
            "evidence_passed": evidence_passed,
            "recommendation": "human_review_required" if evidence_passed else "keep_current_configuration",
        },
    }


def _metrics(points: list[dict]) -> dict:
    if not points:
        return {"closed_sessions": 0, "return_pct": 0.0, "max_drawdown_pct": 0.0, "last_at": None}
    initial, peak = float(points[0]["nav"]), float(points[0]["nav"])
    drawdown = 0.0
    for point in points:
        peak = max(peak, float(point["nav"]))
        drawdown = max(drawdown, 1 - float(point["nav"]) / peak)
    return {"closed_sessions": len(points), "return_pct": (float(points[-1]["nav"]) / initial - 1) * 100,
            "max_drawdown_pct": drawdown * 100, "last_at": points[-1]["at"]}
