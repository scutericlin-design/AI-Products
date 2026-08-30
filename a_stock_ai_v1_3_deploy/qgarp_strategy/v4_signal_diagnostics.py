"""Point-in-time diagnostics for independent Q-GARP v4.9 signal hypotheses.

This module is deliberately a *measurement* stage, not an optimiser.  Each
signal is formed using data available at a month-end, then compared with the
next 20 trading-day return.  It is safe to use future prices only in that
subsequent evaluation step.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.models import DailyBar
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.strategy import _adjusted_closes
from qgarp_strategy.v3_research import QGARPv3Research, QGARPv3Settings, _correlation


SIGNAL_KEYS = ("industry_strength_60", "residual_momentum_60", "short_reversal_5", "residual_momentum_plus_reversal")


def run_v49_signal_diagnostics(
    store: QGARPStore,
    settings: QGARPSettings,
    start_date: str = "20190101",
    end_date: str = "20260731",
    horizon_days: int = 20,
) -> dict[str, Any]:
    """Measure the proposed signals, preserving PIT membership/fundamentals."""
    dates = store.trade_dates(start_date, end_date)
    month_ends = [date for index, date in enumerate(dates[:-horizon_days - 1]) if date[:6] != dates[index + 1][:6]]
    selector = QGARPv3Research(QGARPv3Settings(
        version="v4.9_signal_diagnostic_research_only",
        quality_weight=0.25, growth_weight=0.20, value_weight=0.20,
        momentum_weight=0.30, low_vol_weight=0.05,
        min_confirmation_count=1,
    ))
    instruments = store.load_instruments()
    observations: list[dict[str, Any]] = []
    for date in month_ends:
        future_date = dates[dates.index(date) + horizon_days]
        members = store.membership_symbols_as_of(settings.backtest_universe_id, date)
        active = [item for item in instruments if item.symbol in members]
        histories = {item.symbol: store.bars(item.symbol, date) for item in active}
        fundamentals = {
            instrument.symbol: snapshot
            for instrument in active
            if (snapshot := store.fundamental_as_of(instrument.symbol, date)) is not None
        }
        ranked = selector.rank(as_of=date, instruments=active, histories=histories, fundamentals=fundamentals)
        benchmark_bars = store.bars(settings.benchmark_symbol, date)
        bench_now = _price(benchmark_bars)
        bench_future = _price(store.bars(settings.benchmark_symbol, future_date, 1))
        benchmark_closes = _adjusted_closes(benchmark_bars)
        if bench_now <= 0 or bench_future <= 0 or len(benchmark_closes) < 61 or benchmark_closes[-61] <= 0:
            continue
        benchmark_return = bench_future / bench_now - 1
        benchmark_r60 = benchmark_closes[-1] / benchmark_closes[-61] - 1
        feature_rows = _features(ranked, benchmark_r60)
        evaluated = []
        for row in feature_rows:
            entry = _price(row["bars"])
            future = _price(store.bars(row["instrument"].symbol, future_date, 1))
            if entry > 0 and future > 0:
                evaluated.append({**row, "forward_excess_return": future / entry - 1 - benchmark_return})
        if len(evaluated) >= 20:
            observations.append({"date": date, "year": int(date[:4]), "rows": evaluated})
    return {
        "status": "ok" if observations else "blocked",
        "strategy_version": "v4.9_signal_diagnostic_research_only",
        "research_only": True,
        "period": [start_date, end_date],
        "horizon_days": horizon_days,
        "sample_months": len(observations),
        "signals": _summarize(observations),
        "by_year": {year: _summarize([item for item in observations if item["year"] == year]) for year in sorted({item["year"] for item in observations})},
        "acceptance_rule": "仅当训练期每个入选信号的平均 Rank-IC 和分层超额收益同号为正，且 2026 留出期组合信号仍为正，才进入年度组合回测。",
    }


def _features(rows: list[dict[str, Any]], benchmark_r60: float) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[dict[str, Any], float, float]]] = defaultdict(list)
    for row in rows:
        closes = _adjusted_closes(row["bars"])
        if len(closes) < 61 or closes[-61] <= 0 or closes[-6] <= 0:
            continue
        r60 = closes[-1] / closes[-61] - 1
        r5 = closes[-1] / closes[-6] - 1
        groups[row["instrument"].industry or "未分类"].append((row, r60, r5))
    prepared: list[dict[str, Any]] = []
    all_r60 = [item[1] for group in groups.values() for item in group]
    fallback_mean = mean(all_r60) if all_r60 else 0.0
    for group in groups.values():
        industry_r60 = mean(item[1] for item in group) if len(group) >= 5 else fallback_mean
        for row, r60, r5 in group:
            prepared.append({**row, "r60": r60, "r5": r5, "industry_r60": industry_r60, "industry_strength_60": industry_r60 - benchmark_r60, "residual_momentum_60": r60 - industry_r60, "short_reversal_5": -r5})
    for key in ("industry_strength_60", "residual_momentum_60", "short_reversal_5"):
        values = [row[key] for row in prepared]
        for row in prepared:
            row[key + "_rank"] = _percentile(values, row[key])
    for row in prepared:
        row["residual_momentum_plus_reversal"] = 0.70 * row["residual_momentum_60_rank"] + 0.30 * row["short_reversal_5_rank"]
    return prepared


def _summarize(observations: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in SIGNAL_KEYS:
        ics, spreads = [], []
        for observation in observations:
            values = [row[key] for row in observation["rows"]]
            returns = [row["forward_excess_return"] for row in observation["rows"]]
            ics.append(_correlation(values, returns))
            ordered = sorted(zip(values, returns), key=lambda item: item[0])
            bucket = max(1, len(ordered) // 10)
            spreads.append(mean(value[1] for value in ordered[-bucket:]) - mean(value[1] for value in ordered[:bucket]))
        result[key] = {
            "mean_rank_ic": round(mean(ics), 5) if ics else None,
            "positive_ic_ratio": round(sum(value > 0 for value in ics) / len(ics), 4) if ics else None,
            "mean_top_minus_bottom_excess_pct": round(mean(spreads) * 100, 3) if spreads else None,
            "positive_spread_ratio": round(sum(value > 0 for value in spreads) / len(spreads), 4) if spreads else None,
        }
    return result


def _price(bars: list[DailyBar]) -> float:
    closes = _adjusted_closes(bars)
    return closes[-1] if closes else 0.0


def _percentile(values: list[float], value: float) -> float:
    return 1.0 if len(values) <= 1 else sum(item <= value for item in values) / len(values)
