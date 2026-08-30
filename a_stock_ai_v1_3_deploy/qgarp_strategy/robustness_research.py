"""Walk-forward robustness runner for Q-GARP's existing point-in-time cache."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from qgarp_strategy.backtest import run_historical_backtest
from qgarp_strategy.config import QGARPSettings, load_settings
from qgarp_strategy.research_profiles import RESEARCH_PROFILES, apply_research_profile
from qgarp_strategy.storage import QGARPStore


# Profiles are selected using the first two periods only.  The last two are
# untouched holdouts; a profile must pass both to be described as robust.
CALIBRATION_PERIODS = (("20190101", "20201231"), ("20210101", "20221231"))
HOLDOUT_PERIODS = (("20230101", "20241231"), ("20250101", "20260731"))


def run_walk_forward_research(
    settings: QGARPSettings | None = None,
    store: QGARPStore | None = None,
    names: list[str] | None = None,
) -> dict[str, Any]:
    base = settings or load_settings()
    data = store or QGARPStore(base.db_path)
    requested = names or list(RESEARCH_PROFILES)
    report: dict[str, Any] = {
        "status": "ok",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selection_rule": "calibration score selects one profile; both holdout periods are untouched",
        "acceptance_rule": "holdout excess return > 0 in both periods, average holdout Sharpe > 0.35, worst holdout max drawdown >= -18%, and no period is blocked",
        "profiles": {},
    }
    for name in requested:
        candidate = apply_research_profile(base, name)
        rows = []
        for start, end in (*CALIBRATION_PERIODS, *HOLDOUT_PERIODS):
            result = run_historical_backtest(data, candidate, start, end)
            rows.append({"period": [start, end], "status": result.get("status"), "metrics": result.get("metrics") or {}, "run_id": result.get("run_id")})
        report["profiles"][name] = {"parameters": candidate.parameter_snapshot(), "runs": rows}
    calibration = {name: _score(item["runs"][: len(CALIBRATION_PERIODS)]) for name, item in report["profiles"].items()}
    selected = max(calibration, key=calibration.get) if calibration else None
    holdouts = report["profiles"].get(selected, {}).get("runs", [])[len(CALIBRATION_PERIODS) :]
    report["calibration_scores"] = calibration
    report["selected_by_calibration"] = selected
    report["holdout_verdict"] = _holdout_verdict(holdouts)
    output = Path(base.reports_dir) / f"qgarp_walk_forward_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(output)
    return report


def _score(runs: list[dict[str, Any]]) -> float:
    if not runs or any(item.get("status") != "ok" for item in runs):
        return -1e9
    values = [item.get("metrics") or {} for item in runs]
    return sum(float(row.get("excess_return_pct") or -100) + float(row.get("sharpe") or -5) * 4 + float(row.get("max_drawdown_pct") or -100) * 0.20 - float(row.get("turnover_multiple") or 0) * 0.25 for row in values) / len(values)


def _holdout_verdict(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(runs) != len(HOLDOUT_PERIODS) or any(item.get("status") != "ok" for item in runs):
        return {"passed": False, "reason": "holdout missing or blocked"}
    values = [item.get("metrics") or {} for item in runs]
    excess = [float(row.get("excess_return_pct") or -100) for row in values]
    sharpes = [float(row.get("sharpe") or -5) for row in values]
    drawdowns = [float(row.get("max_drawdown_pct") or -100) for row in values]
    passed = min(excess) > 0 and sum(sharpes) / len(sharpes) > 0.35 and min(drawdowns) >= -18.0
    return {"passed": passed, "holdout_excess_returns": excess, "average_holdout_sharpe": round(sum(sharpes) / len(sharpes), 3), "worst_holdout_drawdown": min(drawdowns)}
