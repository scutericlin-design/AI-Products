"""Point-in-time, frozen factor-portfolio ledger for Q-GARP research."""

from __future__ import annotations

from statistics import mean
from typing import Any

from qgarp_strategy.models import DailyBar
from qgarp_strategy.strategy import _adjusted_closes
from qgarp_strategy.v3_research import FACTOR_KEYS


class FrozenFactorPortfolioLedger:
    """Freeze factor quintiles at month-end; never rank today's names backwards.

    A state read at date ``t`` uses only portfolios formed strictly before
    ``t``.  Their return is measured from the formation close to the close at
    ``t`` using current price data.  This avoids the mechanical relationship
    between a current momentum rank and its own historical return.
    """

    def __init__(self, lookback_months: int = 3):
        self.lookback_months = max(1, lookback_months)
        self.portfolios: dict[str, dict[str, dict[str, list[tuple[str, float]]]]] = {}

    def add(self, as_of: str, rows: list[dict[str, Any]]) -> None:
        snapshot: dict[str, dict[str, list[tuple[str, float]]]] = {}
        for key in FACTOR_KEYS:
            ordered = sorted(rows, key=lambda row: row["ranks"][key])
            bucket = max(1, len(ordered) // 5)
            snapshot[key] = {
                "low": self._leg(ordered[:bucket]),
                "high": self._leg(ordered[-bucket:]),
            }
        self.portfolios[as_of] = snapshot

    def state(self, as_of: str, current_prices: dict[str, float]) -> dict[str, Any]:
        formation_dates = [date for date in sorted(self.portfolios) if date < as_of][-self.lookback_months:]
        if not formation_dates:
            return self._neutral("insufficient_frozen_history")
        spreads: dict[str, float] = {}
        coverage: dict[str, int] = {}
        for key in FACTOR_KEYS:
            values = []
            used = 0
            for formation in formation_dates:
                high = self._return(self.portfolios[formation][key]["high"], current_prices)
                low = self._return(self.portfolios[formation][key]["low"], current_prices)
                if high is None or low is None:
                    continue
                values.append(high - low)
                used += 1
            spreads[key] = mean(values) if values else 0.0
            coverage[key] = used
        ranks = {key: _percentile(list(spreads.values()), value) for key, value in spreads.items()}
        raw_weights = {key: 0.10 + ranks[key] for key in FACTOR_KEYS}
        total = sum(raw_weights.values()) or 1.0
        weights = {key: raw_weights[key] / total for key in FACTOR_KEYS}
        leaders = [key for key in FACTOR_KEYS if spreads[key] > 0 and ranks[key] >= 0.60]
        return {
            "source": "frozen_factor_portfolios",
            "formation_dates": formation_dates,
            "spreads": {key: round(value, 6) for key, value in spreads.items()},
            "weights": {key: round(value, 4) for key, value in weights.items()},
            "leaders": leaders,
            "positive_factor_count": sum(value > 0 for value in spreads.values()),
            "momentum_mode": "trend" if spreads["momentum"] > 0 else "reversal",
            "coverage": coverage,
        }

    def _leg(self, rows: list[dict[str, Any]]) -> list[tuple[str, float]]:
        leg = []
        for row in rows:
            prices = _adjusted_closes(row["bars"])
            if prices and prices[-1] > 0:
                leg.append((row["instrument"].symbol, prices[-1]))
        return leg

    @staticmethod
    def _return(leg: list[tuple[str, float]], prices: dict[str, float]) -> float | None:
        values = [prices[symbol] / entry - 1 for symbol, entry in leg if entry > 0 and prices.get(symbol, 0.0) > 0]
        return mean(values) if len(values) >= max(3, len(leg) // 2) else None

    @staticmethod
    def _neutral(reason: str) -> dict[str, Any]:
        weight = round(1 / len(FACTOR_KEYS), 4)
        return {"source": reason, "formation_dates": [], "spreads": {key: 0.0 for key in FACTOR_KEYS}, "weights": {key: weight for key in FACTOR_KEYS}, "leaders": [], "positive_factor_count": 0, "momentum_mode": "neutral", "coverage": {key: 0 for key in FACTOR_KEYS}}


def _percentile(values: list[float], value: float) -> float:
    return 1.0 if len(values) <= 1 else sum(item <= value for item in values) / len(values)
