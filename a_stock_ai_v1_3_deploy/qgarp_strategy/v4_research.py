"""Q-GARP v4.8: frozen factor-state and conditional-momentum research.

This is an isolated research planner.  It cannot submit paper orders, push
notifications, interact with hybrid_alpha, ETF, Dashboard or a broker.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Any

from qgarp_strategy.models import DailyBar, FundamentalSnapshot, Instrument
from qgarp_strategy.strategy import _adjusted_closes
from qgarp_strategy.v3_research import FACTOR_KEYS, QGARPv3Research, QGARPv3Settings
from qgarp_strategy.v4_factor_state_ledger import FrozenFactorPortfolioLedger


@dataclass(frozen=True)
class QGARPv4Settings:
    version: str = "v4.8_frozen_factor_state_research_only"
    offensive_exposure: float = 0.90
    transition_exposure: float = 0.50
    max_names: int = 14
    entry_top_pct: float = 0.22
    hold_top_pct: float = 0.38
    breadth_offensive_floor: float = 0.48
    breadth_cash_ceiling: float = 0.42
    min_confirmation_count: int = 2
    factor_state_months: int = 3
    stock_momentum_weight: float = 0.25
    max_short_return: float = 0.15
    max_industry_names: int = 3

    def snapshot(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def load_v4_settings() -> QGARPv4Settings:
    return QGARPv4Settings(
        version=os.getenv("QGARP_V4_VERSION", "v4.8_frozen_factor_state_research_only"),
        offensive_exposure=min(max(_env_float("QGARP_V4_OFFENSIVE_EXPOSURE", 0.90), 0.10), 1.0),
        transition_exposure=min(max(_env_float("QGARP_V4_TRANSITION_EXPOSURE", 0.50), 0.10), 0.70),
        max_names=min(max(_env_int("QGARP_V4_MAX_NAMES", 14), 8), 20),
        entry_top_pct=min(max(_env_float("QGARP_V4_ENTRY_TOP_PCT", 0.22), 0.05), 0.50),
        hold_top_pct=min(max(_env_float("QGARP_V4_HOLD_TOP_PCT", 0.38), 0.10), 0.70),
        breadth_offensive_floor=min(max(_env_float("QGARP_V4_BREADTH_OFFENSIVE_FLOOR", 0.48), 0.30), 0.80),
        breadth_cash_ceiling=min(max(_env_float("QGARP_V4_BREADTH_CASH_CEILING", 0.42), 0.15), 0.70),
        min_confirmation_count=min(max(_env_int("QGARP_V4_MIN_CONFIRMATIONS", 2), 1), 3),
        factor_state_months=min(max(_env_int("QGARP_V4_FACTOR_STATE_MONTHS", 3), 1), 6),
        stock_momentum_weight=min(max(_env_float("QGARP_V4_STOCK_MOMENTUM_WEIGHT", 0.25), 0.0), 0.50),
        max_short_return=min(max(_env_float("QGARP_V4_MAX_SHORT_RETURN", 0.15), 0.05), 0.35),
        max_industry_names=min(max(_env_int("QGARP_V4_MAX_INDUSTRY_NAMES", 3), 1), 8),
    )


class QGARPv4Research:
    """Factor-state weighting with frozen, point-in-time factor portfolios."""

    def __init__(self, settings: QGARPv4Settings | None = None):
        self.settings = settings or load_v4_settings()
        self.selector = QGARPv3Research(QGARPv3Settings(
            version=self.settings.version,
            quality_weight=0.25, growth_weight=0.20, value_weight=0.20,
            momentum_weight=0.30, low_vol_weight=0.05,
            entry_top_pct=self.settings.entry_top_pct, hold_top_pct=self.settings.hold_top_pct,
            min_confirmation_count=self.settings.min_confirmation_count, max_names=self.settings.max_names,
        ))
        self.ledger = FrozenFactorPortfolioLedger(self.settings.factor_state_months)

    def prepare(self, store: Any, base: Any, end_date: str) -> None:
        """Pre-freeze historical factor portfolios; reads no future state at plan time."""
        dates = store.trade_dates("20000101", end_date)
        instruments = store.load_instruments()
        for index, date in enumerate(dates[:-1]):
            if date[:6] == dates[index + 1][:6]:
                continue
            members = store.membership_symbols_as_of(base.backtest_universe_id, date)
            active = [item for item in instruments if item.symbol in members]
            histories = {item.symbol: store.bars(item.symbol, date) for item in active}
            fundamentals = {item.symbol: value for item in active if (value := store.fundamental_as_of(item.symbol, date)) is not None}
            self.ledger.add(date, self.selector.rank(as_of=date, instruments=active, histories=histories, fundamentals=fundamentals))

    def plan(self, *, as_of: str, instruments: list[Instrument], histories: dict[str, list[DailyBar]], fundamentals: dict[str, FundamentalSnapshot], benchmark: list[DailyBar], current_symbols: set[str] | None = None) -> dict[str, Any]:
        rows = self.selector.rank(as_of=as_of, instruments=instruments, histories=histories, fundamentals=fundamentals)
        prices = {symbol: _adjusted_closes(bars)[-1] for symbol, bars in histories.items() if bars and _adjusted_closes(bars)[-1] > 0}
        factor_state = self.ledger.state(as_of, prices)
        regime, exposure, breadth = self._regime(benchmark, rows)
        if exposure <= 0:
            return self._empty(as_of, regime, breadth, factor_state, "指数趋势与广度同步走弱，现金防守")
        ranked = self._rank(rows, benchmark, factor_state)
        selected = self._select(ranked, current_symbols or set())
        if not selected:
            return self._empty(as_of, regime, breadth, factor_state, "硬风控后没有可交易标的")
        target_weight = exposure / len(selected)
        recommendations = [self._recommendation(row, current_symbols or set(), target_weight, regime, factor_state) for row in selected]
        return {
            "strategy_id": "qgarp_v4_frozen_factor_state_research", "strategy_version": self.settings.version,
            "execution_mode": "research_only_no_paper_no_push", "as_of": as_of, "market_regime": regime,
            "breadth": round(breadth, 4), "target_exposure": round(sum(item["target_weight"] for item in recommendations), 4),
            "candidate_count": len(rows), "recommendations": recommendations, "factor_state": factor_state,
            "parameters": self.settings.snapshot(),
        }

    def _regime(self, benchmark: list[DailyBar], rows: list[dict[str, Any]]) -> tuple[str, float, float]:
        if len(benchmark) < 121:
            return "transition", self.settings.transition_exposure, 0.0
        prices = _adjusted_closes(benchmark)
        breadth = mean(1.0 if _adjusted_closes(row["bars"])[-1] >= mean(_adjusted_closes(row["bars"])[-20:]) else 0.0 for row in rows) if rows else 0.0
        ma20, ma60, ma120 = mean(prices[-20:]), mean(prices[-60:]), mean(prices[-120:])
        if prices[-1] >= ma20 >= ma60 and breadth >= self.settings.breadth_offensive_floor:
            return "offensive", self.settings.offensive_exposure, breadth
        if prices[-1] < ma20 < ma60 and prices[-1] < ma120 and breadth <= self.settings.breadth_cash_ceiling:
            return "cash_defense", 0.0, breadth
        return "transition", self.settings.transition_exposure, breadth

    def _rank(self, rows: list[dict[str, Any]], benchmark: list[DailyBar], state: dict[str, Any]) -> list[dict[str, Any]]:
        bench = _adjusted_closes(benchmark)
        if len(bench) < 61:
            return []
        benchmark_60 = bench[-1] / bench[-61] - 1
        prepared = []
        for row in rows:
            prices = _adjusted_closes(row["bars"])
            if len(prices) < 61 or prices[-61] <= 0:
                continue
            r5, r60 = prices[-1] / prices[-6] - 1, prices[-1] / prices[-61] - 1
            dynamic = sum(self._oriented_rank(row, key, state) * state["weights"][key] for key in FACTOR_KEYS)
            prepared.append({**row, "r5": r5, "relative_momentum": r60 - benchmark_60, "dynamic_factor_score": dynamic, "above_ma60": prices[-1] >= mean(prices[-60:])})
        relative_values = [row["relative_momentum"] for row in prepared]
        for row in prepared:
            stock_rank = _percentile(relative_values, row["relative_momentum"])
            if state["momentum_mode"] == "reversal":
                stock_rank = 1.0 - stock_rank
            row["score_components"] = {f"factor_{key}": self._oriented_rank(row, key, state) * state["weights"][key] for key in FACTOR_KEYS}
            row["score_components"]["stock_relative_momentum"] = stock_rank
            row["factor_momentum_score"] = 100 * ((1 - self.settings.stock_momentum_weight) * row["dynamic_factor_score"] + self.settings.stock_momentum_weight * stock_rank)
            row["hard_eligible"] = row["above_ma60"] and row["r5"] <= self.settings.max_short_return
            row["preferred"] = row["hard_eligible"] and (row["relative_momentum"] <= 0.08 if state["momentum_mode"] == "reversal" else row["relative_momentum"] >= -0.08)
        return sorted(prepared, key=lambda row: row["factor_momentum_score"], reverse=True)

    @staticmethod
    def _oriented_rank(row: dict[str, Any], key: str, state: dict[str, Any]) -> float:
        rank = row["ranks"][key]
        return 1.0 - rank if key == "momentum" and state["momentum_mode"] == "reversal" else rank

    def _select(self, rows: list[dict[str, Any]], current_symbols: set[str]) -> list[dict[str, Any]]:
        entry_count = max(self.settings.max_names, int(len(rows) * self.settings.entry_top_pct))
        hold_count = max(entry_count, int(len(rows) * self.settings.hold_top_pct))
        chosen: list[dict[str, Any]] = []
        industries: dict[str, int] = defaultdict(int)
        # Preferred names have directional confirmation.  Hard-eligible
        # fallbacks fill the diversified, executable book rather than leaving
        # an accidental cash position in an otherwise allowed regime.
        for prefer_only, industry_cap in ((True, self.settings.max_industry_names), (False, self.settings.max_industry_names), (False, self.settings.max_names)):
            for index, row in enumerate(rows):
                symbol = row["instrument"].symbol
                holding = symbol in current_symbols and index < hold_count
                if index >= entry_count and not holding:
                    continue
                if not row["hard_eligible"] or (prefer_only and not row["preferred"] and not holding):
                    continue
                industry = row["instrument"].industry or "未分类"
                if industries[industry] >= industry_cap or row in chosen:
                    continue
                chosen.append(row)
                industries[industry] += 1
                if len(chosen) >= self.settings.max_names:
                    return chosen
        return chosen

    def _recommendation(self, row: dict[str, Any], current_symbols: set[str], weight: float, regime: str, state: dict[str, Any]) -> dict[str, Any]:
        instrument, bar = row["instrument"], row["bars"][-1]
        return {
            "symbol": instrument.symbol, "name": instrument.name, "industry": instrument.industry,
            "action": "HOLD" if instrument.symbol in current_symbols else "BUY", "trigger_price": round(bar.close, 2),
            "target_weight": round(weight, 4), "stop_loss": round(bar.close * 0.88, 2), "take_profit": None,
            "strategy_score": round(row["factor_momentum_score"], 2),
            "factor_scores": {key: round(value * 100, 2) for key, value in row["score_components"].items()},
            "reasoning": f"v4.8 {regime}：冻结因子组合状态，动量模式 {state['momentum_mode']}，综合分 {row['factor_momentum_score']:.0f}。",
            "strategy_id": "qgarp_v4_frozen_factor_state_research", "strategy_version": self.settings.version,
        }

    def _empty(self, as_of: str, regime: str, breadth: float, state: dict[str, Any], reason: str) -> dict[str, Any]:
        return {"strategy_id": "qgarp_v4_frozen_factor_state_research", "strategy_version": self.settings.version, "execution_mode": "research_only_no_paper_no_push", "as_of": as_of, "market_regime": regime, "breadth": round(breadth, 4), "target_exposure": 0.0, "candidate_count": 0, "recommendations": [], "factor_state": state, "parameters": self.settings.snapshot(), "reason": reason}


def _percentile(values: list[float], value: float) -> float:
    return 1.0 if len(values) <= 1 else sum(item <= value for item in values) / len(values)
