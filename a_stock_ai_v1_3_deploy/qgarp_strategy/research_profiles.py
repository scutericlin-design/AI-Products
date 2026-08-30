"""Small, pre-registered Q-GARP research profiles.

These are competing economic hypotheses, not an unconstrained parameter search.
They are for backtests only and never change the production/default settings.
"""

from __future__ import annotations

from dataclasses import replace

from qgarp_strategy.config import QGARPSettings


RESEARCH_PROFILES: dict[str, dict[str, object]] = {
    "baseline_qvm": {
        "strategy_profile": "quality_value_momentum",
        "strategy_version": "v2.1_baseline_qvm_research",
    },
    "quality_defensive": {
        "strategy_profile": "quality_value_defensive",
        "strategy_version": "v2.1_quality_defensive_research",
        "factor_quality_weight": 0.35,
        "factor_growth_weight": 0.10,
        "factor_value_weight": 0.30,
        "factor_momentum_weight": 0.10,
        "factor_low_vol_weight": 0.15,
        "selection_top_pct": 0.12,
        "min_roe": 8.0,
        "max_debt_to_assets": 65.0,
        "max_names": 12,
        "uptrend_exposure": 0.85,
        "range_exposure": 0.60,
        "risk_off_exposure": 0.20,
    },
    "quality_growth_trend": {
        "strategy_profile": "quality_momentum",
        "strategy_version": "v2.1_quality_growth_trend_research",
        "factor_quality_weight": 0.30,
        "factor_growth_weight": 0.25,
        "factor_value_weight": 0.05,
        "factor_momentum_weight": 0.25,
        "factor_low_vol_weight": 0.15,
        "selection_top_pct": 0.12,
        "min_roe": 7.0,
        "max_debt_to_assets": 70.0,
        "max_names": 12,
        "momentum_short_days": 40,
        "momentum_long_days": 100,
        "momentum_skip_days": 10,
    },
    "quality_value_low_turnover": {
        "strategy_profile": "quality_value_defensive",
        "strategy_version": "v2.1_quality_value_low_turnover_research",
        "factor_quality_weight": 0.40,
        "factor_growth_weight": 0.10,
        "factor_value_weight": 0.30,
        "factor_momentum_weight": 0.05,
        "factor_low_vol_weight": 0.15,
        "selection_top_pct": 0.15,
        "min_roe": 7.0,
        "max_debt_to_assets": 65.0,
        "rebalance_months": 2,
        "max_names": 15,
        "uptrend_exposure": 0.85,
        "range_exposure": 0.65,
        "risk_off_exposure": 0.25,
    },
    # Second-round hypotheses: the first round may be under-invested because
    # a narrow monthly screen cannot fill its intended risk budget.  These
    # profiles deliberately broaden diversification and reduce turnover;
    # they do not relax the point-in-time financial or valuation safeguards.
    "broad_quality_core": {
        "strategy_profile": "quality_value_defensive",
        "strategy_version": "v2.2_broad_quality_core_exploratory",
        "factor_quality_weight": 0.35,
        "factor_growth_weight": 0.15,
        "factor_value_weight": 0.25,
        "factor_momentum_weight": 0.10,
        "factor_low_vol_weight": 0.15,
        "selection_top_pct": 0.30,
        "min_roe": 6.0,
        "max_debt_to_assets": 70.0,
        "rebalance_months": 2,
        "max_names": 20,
        "max_industry_weight": 0.32,
        "uptrend_exposure": 0.95,
        "range_exposure": 0.80,
        "risk_off_exposure": 0.30,
    },
    "broad_quality_trend": {
        "strategy_profile": "quality_momentum",
        "strategy_version": "v2.2_broad_quality_trend_exploratory",
        "factor_quality_weight": 0.25,
        "factor_growth_weight": 0.20,
        "factor_value_weight": 0.10,
        "factor_momentum_weight": 0.30,
        "factor_low_vol_weight": 0.15,
        "selection_top_pct": 0.30,
        "min_roe": 6.0,
        "max_debt_to_assets": 70.0,
        "rebalance_months": 2,
        "max_names": 20,
        "max_industry_weight": 0.32,
        "uptrend_exposure": 0.95,
        "range_exposure": 0.75,
        "risk_off_exposure": 0.25,
    },
    # Factor IC evidence through 2026-07: quality, valuation and low-volatility
    # were positive; momentum was negative and growth close to neutral.  This
    # is a structural hypothesis, not merely a reweighting of a weak factor.
    "evidence_quality_value_lowvol": {
        "strategy_profile": "quality_value_defensive",
        "strategy_version": "v2.3_evidence_quality_value_lowvol_research",
        "factor_quality_weight": 0.25,
        "factor_growth_weight": 0.0,
        "factor_value_weight": 0.35,
        "factor_momentum_weight": 0.0,
        "factor_low_vol_weight": 0.40,
        "selection_top_pct": 0.18,
        "factor_confirmation_count": 2,
        "min_roe": 6.0,
        "max_debt_to_assets": 70.0,
        "rebalance_months": 2,
        "max_names": 15,
        "uptrend_exposure": 0.90,
        "range_exposure": 0.70,
        "risk_off_exposure": 0.25,
    },
    "evidence_lowvol_regime_defensive": {
        "strategy_profile": "quality_value_defensive",
        "strategy_version": "v2.4_evidence_lowvol_regime_defensive_research",
        "factor_quality_weight": 0.25,
        "factor_growth_weight": 0.0,
        "factor_value_weight": 0.35,
        "factor_momentum_weight": 0.0,
        "factor_low_vol_weight": 0.40,
        "selection_top_pct": 0.18,
        "factor_confirmation_count": 2,
        "min_roe": 6.0,
        "max_debt_to_assets": 70.0,
        "rebalance_months": 2,
        "max_names": 15,
        "uptrend_exposure": 0.90,
        "range_exposure": 0.45,
        "risk_off_exposure": 0.0,
    },
}


def apply_research_profile(settings: QGARPSettings, name: str) -> QGARPSettings:
    try:
        overrides = RESEARCH_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Q-GARP research profile: {name}") from exc
    return replace(settings, **overrides)
