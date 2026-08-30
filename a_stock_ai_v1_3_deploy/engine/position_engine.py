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
            cap = settings.multi_strategy_max_exposure if updated.get("is_multi_strategy") else settings.max_position_weight
            try:
                position = float(updated.get("position"))
            except (TypeError, ValueError):
                position = 0.0
            updated["position"] = round(max(0.0, min(position, cap)), 4)
        return updated
