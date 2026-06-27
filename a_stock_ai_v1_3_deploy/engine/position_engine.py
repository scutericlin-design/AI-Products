from __future__ import annotations

from typing import Any

from app.config import settings


def clamp_position(position: Any) -> float:
    try:
        value = float(position)
    except (TypeError, ValueError):
        value = 0.0
    return round(max(0.0, min(value, settings.max_position_weight)), 4)


class PositionEngine:
    def apply_position_limits(self, signal: dict[str, Any]) -> dict[str, Any]:
        updated = dict(signal)
        if str(updated.get("signal", "HOLD")).upper() in {"SELL", "HOLD"}:
            updated["position"] = 0.0
        else:
            updated["position"] = clamp_position(updated.get("position"))
        return updated
