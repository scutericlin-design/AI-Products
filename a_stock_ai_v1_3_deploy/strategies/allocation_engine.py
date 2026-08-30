from __future__ import annotations

from typing import Any

from app.config import settings
from strategies.config import ALLOCATION_PROFILES


class AllocationEngine:
    def allocate(self, regime: dict[str, Any]) -> dict[str, Any]:
        regime_name = str(regime.get("regime") or "range")
        profile = dict(ALLOCATION_PROFILES.get(regime_name, ALLOCATION_PROFILES["range"]))
        target_exposure = sum(value for key, value in profile.items() if key != "cash")
        exposure_cap = settings.multi_strategy_max_exposure
        if target_exposure > exposure_cap:
            scale = exposure_cap / target_exposure
            for key in ("robust_hybrid", "quality_growth", "hot_leader"):
                profile[key] = round(profile[key] * scale, 4)
            profile["cash"] = round(1 - sum(profile[key] for key in profile if key != "cash"), 4)

        return {
            "version": "v1.9",
            "regime": regime_name,
            "strategy_budgets": {key: value for key, value in profile.items() if key != "cash"},
            "cash_target": profile["cash"],
            "target_exposure": round(sum(value for key, value in profile.items() if key != "cash"), 4),
            "reason": regime.get("reason"),
        }
