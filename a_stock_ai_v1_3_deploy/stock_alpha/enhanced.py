"""Frozen growth-aware candidate model and a bounded, evidence-backed AI sleeve."""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from stock_alpha.model import ModelConfig, _capped_inverse_vol


VERSION = "stock_alpha_growth_events_v1"


def enhanced_plan(baseline: dict, holdings: dict[str, float], industries: dict[str, str], bars: pd.DataFrame,
                  financials: pd.DataFrame | None = None) -> dict:
    if baseline.get("status") != "ok":
        return {**baseline, "version": VERSION}
    ranked = pd.DataFrame(baseline["scores"]).copy()
    ranked["industry"] = ranked.ts_code.map(industries).fillna("unknown")

    def rank(field, ascending=True):
        global_rank = ranked[field].rank(pct=True, ascending=ascending)
        grouped = ranked.groupby("industry")[field]
        local = grouped.rank(pct=True, ascending=ascending)
        return local.where((grouped.transform("count") >= 5) & (ranked.industry != "unknown"), global_rank)

    ranked["quality_enhanced"] = (rank("roe") + (np.sign(ranked.ocfps) + 1) / 2 + rank("debt_to_assets", False)) / 3
    ranked["growth"] = rank("or_yoy")
    quality_coverage = profit_coverage = 0.0
    if financials is not None and {"roic", "ocf_to_or", "netprofit_yoy"}.issubset(financials.columns):
        prior = financials[(financials.ann_date < baseline["as_of"]) & (financials.end_date <= financials.ann_date)]
        prior = prior.sort_values(["end_date", "ann_date"]).drop_duplicates("ts_code", keep="last").set_index("ts_code")
        for name in ("roic", "ocf_to_or", "netprofit_yoy"):
            ranked[name] = pd.to_numeric(ranked.ts_code.map(prior[name]), errors="coerce").replace([np.inf, -np.inf], np.nan)
        quality_valid = ranked[["roic", "ocf_to_or"]].notna().all(axis=1)
        quality_coverage = float(quality_valid.mean())
        profit_coverage = float(ranked.netprofit_yoy.notna().mean())
        if quality_coverage >= 0.8:
            rich_quality = (rank("roe") + rank("debt_to_assets", False)
                            + rank("roic").fillna(0) + rank("ocf_to_or").fillna(0)) / 4
            ranked["quality_enhanced"] = rich_quality
        if profit_coverage >= 0.8:
            ranked["growth"] = (rank("or_yoy") + rank("netprofit_yoy").fillna(0)) / 2
    ranked["value_enhanced"] = (rank("earnings_yield") + rank("book_yield")) / 2
    ranked["score"] = 100 * (0.35 * ranked.quality_enhanced + 0.25 * ranked.growth
                              + 0.20 * ranked.value_enhanced + 0.20 * ranked.momentum_rank)
    ranked = ranked.sort_values(["score", "ts_code"], ascending=[False, True]).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    retained = ranked[(ranked.ts_code.isin(holdings)) & (ranked["rank"] <= 24)]
    priority = pd.concat([retained, ranked[~ranked.ts_code.isin(retained.ts_code)]])
    history = bars[bars.trade_date <= baseline["as_of"]].copy()
    history["adjusted"] = history.close * history.adj_factor
    prices = history.pivot(index="trade_date", columns="ts_code", values="adjusted").tail(61)
    returns = prices.pct_change(fill_method=None).tail(60)
    selected, selected_indices = [], []
    for index, row in priority.iterrows():
        if len(selected) >= 3:
            corr = returns[selected].corrwith(returns[row.ts_code])
            if corr.isna().any() or (corr > 0.75).sum() >= 3:
                continue
        selected.append(row.ts_code)
        selected_indices.append(index)
        if len(selected) == 12:
            break
    targets = ranked.loc[selected_indices].copy()
    targets["weight"] = _capped_inverse_vol(targets.volatility.to_numpy(), 0.80, 0.08, 0.05)
    return {"status": "ok", "as_of": baseline["as_of"], "version": VERSION,
            "weights": dict(zip(targets.ts_code, targets.weight)), "targets": json.loads(targets.to_json(orient="records", double_precision=15)),
            "scores": json.loads(ranked.to_json(orient="records", double_precision=15)), "diagnostics": {
                "experimental_not_optimized": True, "growth_weight": 0.25,
                "lowvol_in_score": False, "inverse_vol_position_sizing": True,
                "industry_local_rank_min_members": 5, "not_industry_neutral": True,
                "financial_quality_still_limited_proxy": True,
                "roic_cash_conversion_coverage": quality_coverage,
                "profit_growth_coverage": profit_coverage,
                "rich_quality_active": quality_coverage >= 0.8,
            }}


def apply_opportunities(plan: dict, opportunities: list[dict], events: list[dict], *, today: str,
                        bars: pd.DataFrame | None = None) -> dict:
    """Move up to 20% NAV within the original budget; no new leverage or names outside eligibility."""
    if plan.get("status") != "ok":
        return plan
    known_events = {event["id"]: event for event in events}
    eligible = {row["ts_code"]: row for row in plan["scores"]}
    chosen, evidence = [], []
    for item in opportunities[:3]:
        symbol = item.get("ts_code")
        ids = item.get("event_ids")
        if not isinstance(symbol, str) or symbol not in eligible or symbol in chosen or not isinstance(ids, list) or not ids:
            continue
        matched = [known_events.get(value) for value in ids if isinstance(value, str)]
        if len(matched) != len(ids) or any(not e or e["ts_code"] != symbol or not e.get("positive_numeric_evidence")
                                           or not e["ann_date"] <= today <= e["expires"] for e in matched):
            continue
        if not isinstance(item.get("thesis"), str) or not item["thesis"].strip():
            continue
        chosen.append(symbol)
        evidence.append({"ts_code": symbol, "event_ids": ids, "thesis": item["thesis"][:600],
                         "counter_evidence": str(item.get("counter_evidence", ""))[:600]})
    if not chosen:
        return {**plan, "ai_effect": "no_valid_opportunity", "ai_positive_active_weight": 0.0}
    original = {symbol: float(weight) for symbol, weight in plan["weights"].items()}
    weights = original.copy()
    # Preserve the total budget. Free space from the weakest non-opportunity names only.
    score = {symbol: float(row["score"]) for symbol, row in eligible.items()}
    remaining = 0.20
    for symbol in chosen:
        if symbol not in weights and len(weights) >= 12:
            replaceable = sorted((s for s in weights if s not in chosen), key=lambda s: (score.get(s, 0), s))
            if not replaceable:
                continue
            removed = replaceable[0]
            released = weights.pop(removed)
            weights[symbol] = min(released, 0.08, remaining)
            remaining -= weights[symbol]
            # Any unused remainder is cash, not silently allocated to another idea.
        else:
            needed = min(max(0, 0.08 - weights.get(symbol, 0)), remaining)
            for donor in sorted((s for s in weights if s not in chosen), key=lambda s: (score.get(s, 0), s)):
                transfer = min(needed, weights[donor])
                weights[donor] -= transfer
                weights[symbol] = weights.get(symbol, 0) + transfer
                needed -= transfer
                remaining -= transfer
                if needed < 1e-10:
                    break
        weights = {s: w for s, w in weights.items() if w > 1e-10}
    positive = sum(max(0, w - original.get(s, 0)) for s, w in weights.items())
    if positive > 0.20000001 or sum(weights.values()) > 0.80000001 or len(weights) > 12 or any(w > 0.08000001 for w in weights.values()):
        raise ValueError("AI sleeve exceeded frozen limits")
    if bars is not None:
        history = bars[bars.trade_date <= plan["as_of"]].copy()
        history["adjusted"] = history.close * history.adj_factor
        returns = history.pivot(index="trade_date", columns="ts_code", values="adjusted").tail(61).pct_change(fill_method=None).tail(60)
        selected = []
        for symbol in weights:
            if len(selected) >= 3:
                corr = returns[selected].corrwith(returns[symbol])
                if corr.isna().any() or (corr > 0.75).sum() >= 3:
                    return {**plan, "ai_effect": "correlation_constraint_rejected_overlay", "ai_positive_active_weight": 0.0}
            selected.append(symbol)
    return {**plan, "weights": weights, "targets": [{**eligible[symbol], "weight": weight} for symbol, weight in weights.items()],
            "ai_effect": "bounded_opportunity_selection",
            "ai_positive_active_weight": positive, "ai_evidence": evidence}


def plan_identity(plan: dict, account: str, signal_date: str) -> str:
    payload = {"account": account, "signal_date": signal_date, "version": plan.get("version", "baseline"),
               "weights": plan.get("weights", {}), "ai_mode": plan.get("ai_mode", "rules_only")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]
