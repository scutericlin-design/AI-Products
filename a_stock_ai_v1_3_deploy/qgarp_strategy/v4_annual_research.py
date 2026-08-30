"""Run bounded, annual, research-only tests of a supplied v4 planner."""

from __future__ import annotations

from statistics import mean
from typing import Any

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.v3_backtest import run_v3_historical_backtest


def run_annual_style_research(store: QGARPStore, settings: QGARPSettings, planner: Any, start_year: int = 2019, end_year: int = 2026, final_end_date: str = "20260731") -> dict[str, Any]:
    """Evaluate a fixed planner independently by calendar year.

    The results retain the v3 point-in-time universe, adjusted-price coverage,
    next-open execution, T+1, tradability checks and transaction costs.  This
    function has no paper, push, notification or live-execution dependency.
    """
    # Freeze historical factor portfolios once. At each later plan date the
    # planner is allowed to read only formations strictly before that date.
    if hasattr(planner, "prepare"):
        planner.prepare(store, settings, final_end_date)
    rows = []
    for year in range(start_year, end_year + 1):
        end = min(f"{year}1231", final_end_date) if year == end_year else f"{year}1231"
        result = run_v3_historical_backtest(
            store, settings, f"{year}0101", end, planner=planner,
            strategy_version=planner.settings.version,
            parameter_snapshot=planner.settings.snapshot(),
        )
        rows.append({"year": year, "status": result.get("status"), "metrics": result.get("metrics") or {}})
    valid = [row["metrics"] for row in rows if row["status"] == "ok"]
    excess = [float(row.get("excess_return_pct") or 0.0) for row in valid]
    return {
        "strategy_version": planner.settings.version,
        "execution_mode": "research_only_no_paper_no_push",
        "annual_runs": rows,
        "summary": {
            "positive_excess_years": sum(value > 0 for value in excess),
            "total_years": len(valid),
            "average_excess_return_pct": round(mean(excess), 3) if excess else None,
            "worst_max_drawdown_pct": min((float(row.get("max_drawdown_pct") or 0.0) for row in valid), default=None),
        },
    }
