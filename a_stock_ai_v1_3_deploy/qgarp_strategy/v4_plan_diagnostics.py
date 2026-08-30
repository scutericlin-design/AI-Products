"""Read-only monthly attribution for Q-GARP v4 research planners."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from qgarp_strategy.backtest import _month_end
from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.storage import QGARPStore


def diagnose_monthly_plans(store: QGARPStore, settings: QGARPSettings, planner: Any, start_date: str = "20190101", end_date: str = "20260731") -> dict[str, Any]:
    """Measure selection and exposure decisions without generating orders."""
    dates = store.trade_dates(start_date, end_date)
    instruments = store.load_instruments()
    rows: list[dict[str, Any]] = []
    for index, date in enumerate(dates[:-1]):
        if not _month_end(dates, index):
            continue
        members = store.membership_symbols_as_of(settings.backtest_universe_id, date)
        active = [item for item in instruments if item.symbol in members]
        histories = {item.symbol: store.bars(item.symbol, date) for item in active}
        fundamentals = {item.symbol: snapshot for item in active if (snapshot := store.fundamental_as_of(item.symbol, date)) is not None}
        plan = planner.plan(
            as_of=date, instruments=active, histories=histories, fundamentals=fundamentals,
            benchmark=store.bars(settings.benchmark_symbol, date), current_symbols=set(),
        )
        state = plan.get("factor_state") or {}
        rows.append({
            "date": date,
            "year": int(date[:4]),
            "regime": plan.get("market_regime"),
            "eligible_count": plan.get("candidate_count", 0),
            "selected_count": len(plan.get("recommendations") or []),
            "target_exposure": plan.get("target_exposure", 0.0),
            "positive_factor_count": state.get("positive_factor_count"),
            "momentum_mode": state.get("momentum_mode"),
            "leaders": state.get("leaders") or [],
        })
    annual: dict[int, dict[str, Any]] = {}
    for year in sorted({row["year"] for row in rows}):
        group = [row for row in rows if row["year"] == year]
        annual[year] = _summarize(group)
    return {"status": "ok", "research_only": True, "strategy_version": planner.settings.version, "months": rows, "annual": annual, "overall": _summarize(rows)}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    regimes = Counter(row["regime"] for row in rows)
    modes = Counter(row["momentum_mode"] for row in rows if row["momentum_mode"])
    leaders = Counter(leader for row in rows for leader in row["leaders"])
    return {
        "rebalance_months": len(rows),
        "average_eligible_count": round(mean(row["eligible_count"] for row in rows), 2) if rows else 0,
        "average_selected_count": round(mean(row["selected_count"] for row in rows), 2) if rows else 0,
        "average_target_exposure_pct": round(mean(row["target_exposure"] for row in rows) * 100, 2) if rows else 0,
        "regimes": dict(regimes),
        "momentum_modes": dict(modes),
        "factor_leader_months": dict(leaders),
        "average_positive_factor_count": round(mean(row["positive_factor_count"] for row in rows if row["positive_factor_count"] is not None), 2) if rows else None,
    }
