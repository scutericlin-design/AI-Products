"""Bounded, auditable optimizer for Q-GARP v4 research only."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.v3_backtest import run_v4_annual_backtests


# Economically distinct attack hypotheses; this is deliberately not a grid.
ATTACK_PROFILES: dict[str, dict[str, str]] = {
    "attack_balanced": {"QGARP_V4_VERSION": "v4.1_attack_balanced", "QGARP_V4_MAX_NAMES": "15", "QGARP_V4_ENTRY_TOP_PCT": "0.22", "QGARP_V4_HOLD_TOP_PCT": "0.40", "QGARP_V4_BREADTH_OFFENSIVE_FLOOR": "0.52", "QGARP_V4_OFFENSIVE_VOLUME_RATIO": "0.75", "QGARP_V4_OFFENSIVE_EXPOSURE": "0.95"},
    "attack_broad": {"QGARP_V4_VERSION": "v4.1_attack_broad", "QGARP_V4_MAX_NAMES": "16", "QGARP_V4_ENTRY_TOP_PCT": "0.28", "QGARP_V4_HOLD_TOP_PCT": "0.45", "QGARP_V4_BREADTH_OFFENSIVE_FLOOR": "0.50", "QGARP_V4_OFFENSIVE_VOLUME_RATIO": "0.65", "QGARP_V4_OFFENSIVE_EXPOSURE": "0.95"},
    "attack_selective": {"QGARP_V4_VERSION": "v4.1_attack_selective", "QGARP_V4_MAX_NAMES": "12", "QGARP_V4_ENTRY_TOP_PCT": "0.18", "QGARP_V4_HOLD_TOP_PCT": "0.35", "QGARP_V4_BREADTH_OFFENSIVE_FLOOR": "0.55", "QGARP_V4_OFFENSIVE_VOLUME_RATIO": "0.85", "QGARP_V4_OFFENSIVE_EXPOSURE": "0.95"},
    "attack_fast": {"QGARP_V4_VERSION": "v4.1_attack_fast", "QGARP_V4_MAX_NAMES": "15", "QGARP_V4_ENTRY_TOP_PCT": "0.25", "QGARP_V4_HOLD_TOP_PCT": "0.45", "QGARP_V4_BREADTH_OFFENSIVE_FLOOR": "0.48", "QGARP_V4_OFFENSIVE_VOLUME_RATIO": "0.60", "QGARP_V4_TRANSITION_EXPOSURE": "0.45", "QGARP_V4_OFFENSIVE_EXPOSURE": "0.95"},
}


def _score(report: dict[str, Any]) -> float:
    rows = {row["year"]: row.get("metrics") or {} for row in report["annual_runs"] if row.get("status") == "ok"}
    attack = [float(rows.get(year, {}).get("excess_return_pct") or -100) for year in (2019, 2020, 2024, 2025)]
    defense = [float(rows.get(year, {}).get("excess_return_pct") or -100) for year in (2021, 2022, 2023)]
    drawdown = min(float(metrics.get("max_drawdown_pct") or -100) for metrics in rows.values()) if rows else -100
    positive = sum(value > 0 for value in (attack + defense))
    # Reward multiple distinct regimes; penalize an unacceptable annual drawdown.
    return round(sum(attack) * 0.45 + sum(defense) * 0.55 + positive * 4 + min(drawdown + 18, 0) * 1.5, 3)


def run_v4_bounded_optimization(settings: QGARPSettings, store: QGARPStore, hours: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(hours, 0.1) * 3600
    original = {key: os.environ.get(key) for key in set().union(*[set(p) for p in ATTACK_PROFILES.values()])}
    reports: dict[str, Any] = {}
    try:
        for name, overrides in ATTACK_PROFILES.items():
            if time.monotonic() >= deadline:
                break
            os.environ.update(overrides)
            report = run_v4_annual_backtests(store, settings, 2019, 2026, "20260731")
            reports[name] = {"overrides": overrides, "report": report, "score": _score(report)}
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    selected = max(reports, key=lambda name: reports[name]["score"]) if reports else None
    result = {"status": "ok", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"), "time_limit_hours": hours, "selection_rule": "annual attack+defense excess, positive regime count, annual drawdown penalty", "profiles": reports, "selected": selected}
    path = Path(settings.reports_dir) / f"qgarp_v4_annual_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["report_path"] = str(path)
    return result
