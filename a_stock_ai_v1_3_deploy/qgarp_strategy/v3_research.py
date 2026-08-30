"""Q-GARP v3 research-only model and factor diagnostics.

This module deliberately has no paper-trading or notification dependency.  It
shares only Q-GARP's isolated market-data store, and its output is a research
report until it has passed the configured walk-forward gates.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from statistics import mean
from typing import Any

from qgarp_strategy.models import DailyBar, FundamentalSnapshot, Instrument
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.strategy import _adjusted_closes, _days_between


V3_VERSION = "v3.0_research_only"
FACTOR_KEYS = ("quality", "growth", "value", "momentum", "low_vol")


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


@dataclass(frozen=True)
class QGARPv3Settings:
    """Parameters are deliberately namespaced, so v3 cannot alter v2."""

    version: str = V3_VERSION
    quality_weight: float = 0.30
    growth_weight: float = 0.25
    value_weight: float = 0.20
    momentum_weight: float = 0.15
    low_vol_weight: float = 0.10
    entry_top_pct: float = 0.12
    hold_top_pct: float = 0.25
    min_confirmation_count: int = 3
    max_names: int = 15
    max_single_weight: float = 0.08
    max_industry_weight: float = 0.25
    min_listing_days: int = 250
    min_average_amount_yuan: float = 80_000_000.0
    momentum_short_days: int = 60
    momentum_long_days: int = 120
    momentum_skip_days: int = 20
    adjustment_coverage_floor: float = 0.98

    @property
    def weights(self) -> dict[str, float]:
        raw = {
            "quality": max(self.quality_weight, 0.0),
            "growth": max(self.growth_weight, 0.0),
            "value": max(self.value_weight, 0.0),
            "momentum": max(self.momentum_weight, 0.0),
            "low_vol": max(self.low_vol_weight, 0.0),
        }
        total = sum(raw.values())
        return {key: value / total for key, value in raw.items()} if total else raw

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "weights": self.weights,
            "entry_top_pct": self.entry_top_pct,
            "hold_top_pct": self.hold_top_pct,
            "min_confirmation_count": self.min_confirmation_count,
            "max_names": self.max_names,
            "max_single_weight": self.max_single_weight,
            "max_industry_weight": self.max_industry_weight,
            "momentum": {"short_days": self.momentum_short_days, "long_days": self.momentum_long_days, "skip_days": self.momentum_skip_days},
        }


def load_v3_settings() -> QGARPv3Settings:
    """Load only QGARP_V3_* environment variables; v2 remains immutable."""
    entry = min(max(_env_float("QGARP_V3_ENTRY_TOP_PCT", 0.12), 0.03), 0.30)
    hold = min(max(_env_float("QGARP_V3_HOLD_TOP_PCT", 0.25), entry), 0.50)
    return QGARPv3Settings(
        quality_weight=max(_env_float("QGARP_V3_QUALITY_WEIGHT", 0.30), 0.0),
        growth_weight=max(_env_float("QGARP_V3_GROWTH_WEIGHT", 0.25), 0.0),
        value_weight=max(_env_float("QGARP_V3_VALUE_WEIGHT", 0.20), 0.0),
        momentum_weight=max(_env_float("QGARP_V3_MOMENTUM_WEIGHT", 0.15), 0.0),
        low_vol_weight=max(_env_float("QGARP_V3_LOW_VOL_WEIGHT", 0.10), 0.0),
        entry_top_pct=entry,
        hold_top_pct=hold,
        min_confirmation_count=min(max(_env_int("QGARP_V3_MIN_CONFIRMATION_COUNT", 3), 1), len(FACTOR_KEYS)),
        max_names=min(max(_env_int("QGARP_V3_MAX_NAMES", 15), 5), 40),
        max_single_weight=min(max(_env_float("QGARP_V3_MAX_SINGLE_WEIGHT", 0.08), 0.01), 0.20),
        max_industry_weight=min(max(_env_float("QGARP_V3_MAX_INDUSTRY_WEIGHT", 0.25), 0.05), 0.60),
    )


class QGARPv3Research:
    """Industry-relative quality-growth-value ranking with explicit hysteresis."""

    def __init__(self, settings: QGARPv3Settings | None = None):
        self.settings = settings or load_v3_settings()

    def rank(self, *, as_of: str, instruments: list[Instrument], histories: dict[str, list[DailyBar]], fundamentals: dict[str, FundamentalSnapshot]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for instrument in instruments:
            bars, fundamental = histories.get(instrument.symbol) or [], fundamentals.get(instrument.symbol)
            reason = self._rejection(instrument, bars, fundamental, as_of)
            if reason:
                continue
            rows.append({"instrument": instrument, "bars": bars, "fundamental": fundamental, "raw": self._raw_factors(bars, fundamental)})
        self._score_by_industry(rows)
        return sorted(rows, key=lambda row: row["score"], reverse=True)

    def plan(self, *, as_of: str, instruments: list[Instrument], histories: dict[str, list[DailyBar]], fundamentals: dict[str, FundamentalSnapshot], benchmark: list[DailyBar], current_symbols: set[str] | None = None) -> dict[str, Any]:
        ranked = self.rank(as_of=as_of, instruments=instruments, histories=histories, fundamentals=fundamentals)
        exposure, regime, regime_reason = self._regime(benchmark, ranked)
        current_symbols = current_symbols or set()
        total = max(len(ranked), 1)
        entry_count = max(1, int(total * self.settings.entry_top_pct))
        hold_count = max(entry_count, int(total * self.settings.hold_top_pct))
        selected: list[dict[str, Any]] = []
        industry_weight: dict[str, float] = defaultdict(float)
        for index, row in enumerate(ranked):
            symbol = row["instrument"].symbol
            entering = index < entry_count
            holding = symbol in current_symbols and index < hold_count
            if not entering and not holding:
                continue
            if row["confirmation_count"] < self.settings.min_confirmation_count:
                continue
            industry = row["instrument"].industry or "未分类"
            if industry_weight[industry] + self.settings.max_single_weight > self.settings.max_industry_weight + 1e-9:
                continue
            selected.append(row)
            industry_weight[industry] += self.settings.max_single_weight
            if len(selected) >= self.settings.max_names:
                break
        allocation = min(exposure, self.settings.max_single_weight * len(selected))
        recommendations = []
        for row in selected:
            bar, instrument = row["bars"][-1], row["instrument"]
            weight = allocation / len(selected) if selected else 0.0
            recommendations.append({
                "symbol": instrument.symbol,
                "name": instrument.name,
                "industry": instrument.industry,
                "action": "HOLD" if instrument.symbol in current_symbols else "BUY",
                "trigger_price": round(bar.close, 2),
                "target_weight": round(weight, 4),
                "stop_loss": round(bar.close * 0.88, 2),
                "take_profit": None,
                "strategy_score": round(row["score"], 2),
                "factor_scores": {key: round(row["ranks"][key] * 100, 2) for key in FACTOR_KEYS},
                "reasoning": f"v3研究：行业内质量/成长/估值/动量/低波综合{row['score']:.0f}，{row['confirmation_count']}项因子确认；{regime}。",
                "strategy_id": "qgarp_v3_research",
                "strategy_version": self.settings.version,
            })
        return {
            "strategy_id": "qgarp_v3_research",
            "strategy_version": self.settings.version,
            "execution_mode": "research_only_no_paper_no_push",
            "as_of": as_of,
            "market_regime": regime,
            "market_regime_reason": regime_reason,
            "target_exposure": round(sum(row["target_weight"] for row in recommendations), 4),
            "candidate_count": len(ranked),
            "recommendations": recommendations,
            "parameters": self.settings.snapshot(),
        }

    def _rejection(self, instrument: Instrument, bars: list[DailyBar], fundamental: FundamentalSnapshot | None, as_of: str) -> str | None:
        if "ST" in instrument.name.upper() or "退" in instrument.name:
            return "st_or_delisting"
        if instrument.list_date and _days_between(instrument.list_date, as_of) < self.settings.min_listing_days:
            return "listing_age"
        required = self.settings.momentum_long_days + self.settings.momentum_skip_days + 1
        if len(bars) < required or fundamental is None or fundamental.available_at > as_of:
            return "missing_history_or_point_in_time_financial"
        if any(value is None for value in (
            fundamental.roe,
            fundamental.revenue_yoy,
            fundamental.profit_yoy,
            fundamental.operating_cashflow_per_share,
            fundamental.debt_to_assets,
        )):
            return "incomplete_financial_fields"
        if any(bar.adj_factor is None or bar.adj_factor <= 0 for bar in bars[-required:]):
            return "missing_adjustment_factor"
        if mean(max(bar.amount, 0.0) for bar in bars[-20:]) < self.settings.min_average_amount_yuan:
            return "illiquid"
        current = bars[-1]
        if current.close <= 0 or current.pct_chg >= 9.5 or current.pe_ttm is None or current.pe_ttm <= 0 or current.pb is None or current.pb <= 0:
            return "untradable_or_nonpositive_valuation"
        return None

    def _raw_factors(self, bars: list[DailyBar], fundamental: FundamentalSnapshot) -> dict[str, float]:
        closes = _adjusted_closes(bars)
        anchor = len(closes) - 1 - self.settings.momentum_skip_days
        r_short = closes[anchor] / closes[anchor - self.settings.momentum_short_days] - 1
        r_long = closes[anchor] / closes[anchor - self.settings.momentum_long_days] - 1
        returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
        current = bars[-1]
        return {
            "quality": _value(fundamental.roe) * 0.55 + _value(fundamental.operating_cashflow_per_share) * 8 - _value(fundamental.debt_to_assets) * 0.18,
            "growth": _value(fundamental.revenue_yoy) * 0.35 + _value(fundamental.profit_yoy) * 0.65,
            "value": -_value(current.pe_ttm) * 0.65 - _value(current.pb) * 3.5,
            "momentum": r_short * 0.45 + r_long * 0.55,
            "low_vol": -sqrt(mean(value * value for value in returns[-20:])) if returns else 0.0,
        }

    def _score_by_industry(self, rows: list[dict[str, Any]]) -> None:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[row["instrument"].industry or "未分类"].append(row)
        for group in groups.values():
            peer = group if len(group) >= 5 else rows
            ranks = {key: _percentile(peer, key) for key in FACTOR_KEYS}
            for row in group:
                row["ranks"] = {key: ranks[key][id(row)] for key in FACTOR_KEYS}
                row["confirmation_count"] = sum(value >= 0.60 for value in row["ranks"].values())
                row["score"] = sum(row["ranks"][key] * self.settings.weights[key] * 100 for key in FACTOR_KEYS)

    def _regime(self, benchmark: list[DailyBar], ranked: list[dict[str, Any]]) -> tuple[float, str, str]:
        if len(benchmark) < 60:
            return 0.60, "range", "基准历史不足，按中性研究仓位"
        closes = [bar.close for bar in benchmark]
        ma20, ma60 = mean(closes[-20:]), mean(closes[-60:])
        breadth = mean(1.0 if row["bars"][-1].close >= mean([bar.close for bar in row["bars"][-20:]]) else 0.0 for row in ranked) if ranked else 0.0
        if closes[-1] >= ma20 >= ma60 and breadth >= 0.55:
            return 0.90, "uptrend", "指数趋势向上且候选股广度确认"
        if closes[-1] < ma20 < ma60 and breadth < 0.45:
            return 0.35, "risk_off", "指数趋势走弱且候选股广度不足"
        return 0.60, "range", "趋势或广度未同时确认，保持中性仓位"


def run_v3_factor_diagnostics(store: QGARPStore, start_date: str, end_date: str, horizon_days: int = 20, settings: QGARPv3Settings | None = None) -> dict[str, Any]:
    """Measure factor rank IC and top-minus-bottom spread without using future data in signals."""
    settings = settings or load_v3_settings()
    dates = store.trade_dates(start_date, end_date)
    if len(dates) <= horizon_days + 1:
        return {"status": "blocked", "reason": "研究区间交易日不足"}
    coverage = store.adjustment_factor_coverage(start_date, end_date)
    ratio = coverage["adjusted"] / coverage["total"] if coverage["total"] else 0.0
    if ratio < settings.adjustment_coverage_floor:
        return {"status": "blocked", "reason": f"复权覆盖率 {ratio:.2%} 低于 {settings.adjustment_coverage_floor:.0%}", "coverage": coverage}
    engine = QGARPv3Research(settings)
    instruments = store.load_instruments()
    month_ends = [date for index, date in enumerate(dates[:-horizon_days - 1]) if date[:6] != dates[index + 1][:6]]
    measurements: dict[str, list[float]] = defaultdict(list)
    spreads: dict[str, list[float]] = defaultdict(list)
    samples = 0
    for date in month_ends:
        future_date = dates[dates.index(date) + horizon_days]
        symbols = store.membership_symbols_as_of("QGARP_DAILY_BASIC_PIT", date)
        members = [item for item in instruments if item.symbol in symbols]
        histories = {item.symbol: store.bars(item.symbol, date) for item in members}
        fundamentals = {item.symbol: value for item in members if (value := store.fundamental_as_of(item.symbol, date)) is not None}
        ranked = engine.rank(as_of=date, instruments=members, histories=histories, fundamentals=fundamentals)
        rows = []
        for row in ranked:
            symbol = row["instrument"].symbol
            future = _adjusted_bar_price(store, symbol, future_date)
            current_bar = row["bars"][-1]
            current = current_bar.close * float(current_bar.adj_factor or 0.0)
            if future > 0 and current > 0:
                rows.append((row, future / current - 1))
        if len(rows) < 20:
            continue
        samples += 1
        for key in (*FACTOR_KEYS, "total"):
            values = [row[0]["score"] / 100 if key == "total" else row[0]["ranks"][key] for row in rows]
            returns = [row[1] for row in rows]
            measurements[key].append(_correlation(values, returns))
            ordered = sorted(zip(values, returns), key=lambda item: item[0])
            bucket = max(1, len(ordered) // 10)
            spreads[key].append(mean(value[1] for value in ordered[-bucket:]) - mean(value[1] for value in ordered[:bucket]))
    if not samples:
        return {"status": "blocked", "reason": "没有足够的点时点候选样本", "coverage": coverage}
    factors = {
        key: {
            "mean_rank_ic": round(mean(values), 5),
            "positive_ic_ratio": round(sum(value > 0 for value in values) / len(values), 4),
            "mean_top_minus_bottom_return_pct": round(mean(spreads[key]) * 100, 3),
        }
        for key, values in measurements.items()
    }
    return {
        "status": "ok",
        "strategy_version": settings.version,
        "research_only": True,
        "period": [start_date, end_date],
        "forward_horizon_days": horizon_days,
        "sample_months": samples,
        "coverage": coverage,
        "parameters": settings.snapshot(),
        "factors": factors,
        "interpretation": "仅当总分及保留因子在多个市场阶段呈现稳定正向 IC 与分层收益时，才允许进入后续组合回测。",
    }


def build_v3_plan_from_store(store: QGARPStore, as_of: str, current_symbols: set[str] | None = None, settings: QGARPv3Settings | None = None) -> dict[str, Any]:
    """Build a read-only v3 research plan from Q-GARP's own isolated store."""
    settings = settings or load_v3_settings()
    universe_id = os.getenv("QGARP_V3_UNIVERSE_ID", "QGARP_DAILY_BASIC_PIT")
    symbols = store.membership_symbols_as_of(universe_id, as_of)
    if not symbols:
        return {"status": "blocked", "reason": f"{as_of} 缺少 {universe_id} 点时点股票池", "execution_mode": "research_only_no_paper_no_push"}
    instruments = [item for item in store.load_instruments(as_of) if item.symbol in symbols]
    histories = {item.symbol: store.bars(item.symbol, as_of) for item in instruments}
    fundamentals = {item.symbol: value for item in instruments if (value := store.fundamental_as_of(item.symbol, as_of)) is not None}
    benchmark_symbol = os.getenv("QGARP_V3_BENCHMARK_SYMBOL", "000906.SH").upper()
    return QGARPv3Research(settings).plan(
        as_of=as_of,
        instruments=instruments,
        histories=histories,
        fundamentals=fundamentals,
        benchmark=store.bars(benchmark_symbol, as_of),
        current_symbols=current_symbols,
    )


def _percentile(rows: list[dict[str, Any]], key: str) -> dict[int, float]:
    ordered = sorted(rows, key=lambda row: row["raw"][key], reverse=True)
    denominator = max(len(ordered) - 1, 1)
    return {id(row): 1 - index / denominator for index, row in enumerate(ordered)}


def _adjusted_bar_price(store: QGARPStore, symbol: str, trade_date: str) -> float:
    rows = store.bars(symbol, trade_date, 1)
    if not rows or rows[-1].trade_date != trade_date or not rows[-1].adj_factor:
        return 0.0
    return rows[-1].close * float(rows[-1].adj_factor)


def _correlation(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = sqrt(sum((y - right_mean) ** 2 for y in right))
    return numerator / (left_scale * right_scale) if left_scale and right_scale else 0.0


def _value(value: float | None) -> float:
    return float(value) if value is not None else 0.0
