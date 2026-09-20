"""Research-only A-share factor hypotheses for Stock Alpha.

This module never feeds the live worker. It compares predeclared, explainable
cross-sectional hypotheses using the existing next-open research proxy.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

import stock_alpha.backtest as backtest
from stock_alpha.data import load_dataset, write_json
from stock_alpha.model import ModelConfig, _capped_inverse_vol, build_targets, prepare_model_data


VARIANTS = ("quality_growth_reversal", "quality_growth_trend_volume", "quality_growth_blend")


def _rank(values: pd.Series, *, ascending: bool = True) -> pd.Series:
    return values.rank(method="average", pct=True, ascending=ascending).fillna(0.0)


def _history_features(bars: pd.DataFrame, as_of: str, symbols: set[str]) -> pd.DataFrame:
    history = bars.loc[(bars.trade_date.astype(str) <= as_of) & bars.ts_code.isin(symbols)].copy()
    history["adjusted"] = pd.to_numeric(history.close, errors="coerce") * pd.to_numeric(history.adj_factor, errors="coerce")
    history["amount"] = pd.to_numeric(history.amount, errors="coerce")
    rows = []
    for symbol, group in history.groupby("ts_code", sort=False):
        group = group.sort_values("trade_date", kind="stable").tail(61)
        price = group.adjusted.dropna()
        amount = group.amount.dropna()
        if len(price) < 61 or len(amount) < 60 or (price <= 0).any():
            continue
        rows.append({
            "ts_code": symbol,
            "short_return": float(price.iloc[-1] / price.iloc[-6] - 1),
            "ma60_gap": float(price.iloc[-1] / price.tail(60).mean() - 1),
            "volume_acceleration": float(amount.tail(20).mean() / amount.tail(60).mean()),
        })
    return pd.DataFrame(rows)


def build_research_plan(variant: str, bars: pd.DataFrame, fundamentals: pd.DataFrame, memberships: pd.DataFrame,
                        as_of: str, current_weights: dict[str, float], config: ModelConfig, *, prepared=None) -> dict:
    baseline = build_targets(bars, fundamentals, memberships, as_of, current_weights, config, prepared=prepared)
    if baseline.get("status") != "ok":
        return baseline
    ranked = pd.DataFrame(baseline["scores"]).copy()
    features = _history_features(bars, as_of, set(ranked.ts_code))
    ranked = ranked.merge(features, on="ts_code", how="left", validate="one_to_one")
    if ranked[["short_return", "ma60_gap", "volume_acceleration"]].isna().any().any():
        return {**baseline, "status": "blocked", "diagnostics": {**baseline["diagnostics"], "reason": "missing_research_features"}}
    growth = _rank(ranked.or_yoy)
    reversal = _rank(ranked.short_return, ascending=True)
    volume = _rank(ranked.volume_acceleration)
    if variant == "quality_growth_reversal":
        ranked["research_score"] = (0.34 * ranked.quality + 0.26 * growth + 0.18 * ranked.value
                                      + 0.12 * ranked.lowvol + 0.10 * reversal)
        ranked = ranked.loc[(ranked.ma60_gap >= -0.02) & (ranked.momentum >= -0.05)].copy()
    elif variant == "quality_growth_trend_volume":
        ranked["research_score"] = (0.32 * ranked.quality + 0.25 * growth + 0.15 * ranked.value
                                      + 0.18 * ranked.momentum_rank + 0.10 * volume)
        ranked = ranked.loc[(ranked.ma60_gap >= -0.02) & (ranked.momentum >= -0.05)].copy()
    elif variant == "quality_growth_blend":
        ranked["research_score"] = (0.33 * ranked.quality + 0.25 * growth + 0.16 * ranked.value
                                      + 0.10 * ranked.lowvol + 0.08 * ranked.momentum_rank + 0.08 * reversal)
        ranked = ranked.loc[ranked.ma60_gap >= -0.04].copy()
    else:
        raise ValueError(f"unknown research variant: {variant}")
    if len(ranked) < config.min_candidates:
        return {**baseline, "status": "blocked", "diagnostics": {**baseline["diagnostics"], "reason": "research_gate_too_narrow"}}
    ranked = ranked.sort_values(["research_score", "ts_code"], ascending=[False, True], kind="stable").reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    retained = ranked.loc[(ranked.ts_code.map(current_weights).fillna(0) > 0) & (ranked["rank"] <= config.retention_rank)]
    priority = pd.concat([retained, ranked.loc[~ranked.ts_code.isin(retained.ts_code)]], ignore_index=True)
    price_history = bars.loc[(bars.trade_date.astype(str) <= as_of) & bars.ts_code.isin(ranked.ts_code)].copy()
    price_history["adjusted"] = pd.to_numeric(price_history.close, errors="coerce") * pd.to_numeric(price_history.adj_factor, errors="coerce")
    returns = price_history.pivot(index="trade_date", columns="ts_code", values="adjusted").tail(61).pct_change(fill_method=None).tail(60)
    selected = []
    for row in priority.itertuples(index=False):
        symbol = row.ts_code
        if len(selected) >= config.correlation_cluster_size:
            correlations = returns[selected].corrwith(returns[symbol])
            if correlations.isna().any() or int((correlations > config.correlation_threshold).sum()) >= config.correlation_cluster_size:
                continue
        selected.append(symbol)
        if len(selected) == config.max_names:
            break
    targets = ranked.set_index("ts_code").loc[selected].reset_index().copy()
    if len(targets) < config.min_candidates:
        return {**baseline, "status": "blocked", "diagnostics": {**baseline["diagnostics"], "reason": "research_correlation_gate_too_narrow"}}
    targets["weight"] = _capped_inverse_vol(targets.volatility.to_numpy(dtype=float), config.total_weight, config.max_weight, config.volatility_floor)
    return {**baseline, "status": "ok", "targets": targets.to_dict("records"),
            "weights": dict(zip(targets.ts_code, targets.weight)), "scores": ranked.to_dict("records"),
            "diagnostics": {**baseline["diagnostics"], "research_only": True, "research_variant": variant,
                            "research_factor_weights": {"quality": 0.34, "growth": 0.26, "value": 0.18,
                                                        "lowvol": 0.12, "reversal": 0.10} if variant == "quality_growth_reversal" else None,
                            "research_feature_gate": "price_near_or_above_60d_mean and medium_trend_not_deeply_negative"}}


def run(data: Path, output: Path) -> dict:
    bars, fundamentals, memberships, calendar, manifest = load_dataset(data)
    output.mkdir(parents=True, exist_ok=True)
    config = ModelConfig()
    sessions = [day for day in calendar if manifest["start"] <= day <= manifest["end"]]
    signal_dates = sorted(set(sessions[::config.rebalance_sessions] + [sessions[-1]]))
    prepared = prepare_model_data(bars, fundamentals, memberships, signal_dates, config, end=manifest["end"])
    result = {"research_only": True, "promoted": False, "data_end": manifest["end"], "configuration": asdict(config), "runs": []}
    original = backtest.build_targets
    try:
        for variant in VARIANTS:
            def selector(*args, _variant=variant, **kwargs):
                return build_research_plan(_variant, *args, **kwargs)
            backtest.build_targets = selector
            for cost_multiplier in (1.0, 2.0):
                report = backtest.run_backtest(bars, fundamentals, memberships, sessions, manifest["start"], manifest["end"], config,
                                               cost_multiplier=cost_multiplier, prepared=prepared)
                summary = {key: report[key] for key in ("status", "variant", "metrics", "annual", "diagnostics")}
                summary["research_variant"] = variant
                summary["cost_multiplier"] = cost_multiplier
                result["runs"].append(summary)
                write_json(output / f"{variant}_cost{cost_multiplier:g}.json", report)
    finally:
        backtest.build_targets = original
    write_json(output / "comparison.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.data, args.output)
    print(json.dumps({"stage": "complete", "runs": len(result["runs"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
