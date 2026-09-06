"""Reproducible, year-by-year robustness research for daily strategy profiles.

This runner deliberately compares a small set of explainable candidates.  It
does not perform an unrestricted parameter search across the same years that
will later be used to judge the result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any

from app.config import settings
from backtest.historical_daily import (
    _build_historical_universe_context,
    _build_index_regimes,
    _load_daily_bars,
    _load_index_daily_bars,
    _run_portfolio_backtest,
    _strategy_profile,
)


@dataclass(frozen=True)
class ResearchCandidate:
    key: str
    profile_name: str
    overrides: dict[str, Any]
    rationale: str


# These are intentionally sparse, structural variations of the same approach.
# They are designed before reading the annual outcomes, not fitted per year.
DEFAULT_CANDIDATES = (
    ResearchCandidate(
        key="hybrid_alpha_baseline",
        profile_name="hybrid_alpha",
        overrides={},
        rationale="Current hybrid tactical profile: breakout plus controlled pullback entries.",
    ),
    ResearchCandidate(
        key="hybrid_alpha_guarded",
        profile_name="hybrid_alpha",
        overrides={
            "max_total_exposure": 0.78,
            "max_names": 5,
            "per_trade_weight": 0.10,
            "regime_min_score": 60.0,
            "regime_exit_score": 47.0,
            "min_breadth20": 0.48,
            "narrow_regime_enabled": False,
            "breakout_transition_weight_scale": 0.34,
            "breakout_transition_max_names": 1,
            "pullback_rank_threshold": 83.0,
            "pullback_min_momentum20": 4.0,
            "pullback_entry_weight": 0.045,
            "drawdown_cooldown_pct": 0.045,
            "drawdown_cooldown_days": 14,
        },
        rationale="Reduce exposure and countertrend pullbacks unless breadth is broad; prioritize bear-market defense.",
    ),
    ResearchCandidate(
        key="hybrid_alpha_trend",
        profile_name="hybrid_alpha",
        overrides={
            "max_total_exposure": 0.90,
            "max_names": 6,
            "per_trade_weight": 0.115,
            "regime_min_score": 57.0,
            "regime_exit_score": 44.0,
            "min_breadth20": 0.46,
            "narrow_regime_min_score": 55.0,
            "narrow_rank_boost": 7.0,
            "breakout_entry_weight": 0.15,
            "breakout_transition_weight_scale": 0.42,
            "pullback_rank_threshold": 81.0,
            "pullback_min_momentum20": 3.5,
            "pullback_entry_weight": 0.045,
        },
        rationale="Keep a diversified trend sleeve while reducing weak-regime and short pullback exposure.",
    ),
    ResearchCandidate(
        key="hybrid_alpha_disciplined_hybrid",
        profile_name="hybrid_alpha",
        overrides={
            "max_total_exposure": 0.72,
            "max_names": 4,
            "per_trade_weight": 0.12,
            "holding_days": 8,
            "stop_loss_pct": 0.065,
            "take_profit_pct": 0.30,
            "trailing_stop_pct": 0.085,
            "trail_activation_pct": 0.12,
            "market_exit_enabled": False,
            "allow_sideways": False,
            "regime_min_score": 58.0,
            "regime_exit_score": 43.0,
            "min_breadth20": 0.47,
            "min_breadth60": 0.40,
            "min_regime_momentum20": 0.0,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 55.0,
            "narrow_min_momentum5": 0.5,
            "narrow_min_sentiment_score": 60.0,
            "narrow_exposure_scale": 0.30,
            "min_sentiment_score": 56.0,
            "panic_threshold": 64.0,
            "entry_guard_enabled": True,
            "entry_min_open_gap_pct": -0.8,
            "entry_max_open_gap_pct": 3.5,
            "pullback_enabled": True,
            "pullback_rank_threshold": 84.0,
            "pullback_min_momentum20": 4.0,
            "pullback_entry_weight": 0.045,
            "pullback_holding_days": 5,
            "breakout_entry_weight": 0.12,
            "breakout_min_close_60d_high_ratio": 0.97,
            "breakout_max_distance_ma20_pct": 12.0,
            "breakout_requires_broad_pass": True,
        },
        rationale="Position-aware version: reduce exposure, require material entries and let qualified holdings develop before exit.",
    ),
    ResearchCandidate(
        key="hybrid_alpha_disciplined_trend",
        profile_name="hybrid_alpha",
        overrides={
            "max_total_exposure": 0.72,
            "max_names": 4,
            "per_trade_weight": 0.12,
            "holding_days": 9,
            "stop_loss_pct": 0.065,
            "take_profit_pct": 0.32,
            "trailing_stop_pct": 0.085,
            "trail_activation_pct": 0.12,
            "market_exit_enabled": False,
            "allow_sideways": False,
            "regime_min_score": 59.0,
            "regime_exit_score": 43.0,
            "min_breadth20": 0.48,
            "min_breadth60": 0.40,
            "min_regime_momentum20": 0.0,
            "narrow_regime_enabled": False,
            "min_sentiment_score": 57.0,
            "panic_threshold": 63.0,
            "entry_guard_enabled": True,
            "entry_min_open_gap_pct": -0.8,
            "entry_max_open_gap_pct": 3.0,
            "pullback_enabled": False,
            "breakout_entry_weight": 0.12,
            "breakout_min_close_60d_high_ratio": 0.975,
            "breakout_max_distance_ma20_pct": 11.0,
            "breakout_requires_broad_pass": True,
            "breakout_min_breadth20": 0.48,
            "breakout_min_regime_score": 59.0,
            "breakout_min_sentiment_score": 58.0,
        },
        rationale="Pure confirmed-trend sleeve: reject countertrend pullbacks and trade only broad, liquid momentum states.",
    ),
    ResearchCandidate(
        key="quality_t_reference",
        profile_name="quality_t",
        overrides={},
        rationale="Existing quality/T profile used as a price-liquidity quality reference, not a fundamental-data proxy.",
    ),
    ResearchCandidate(
        key="regime_adaptive_reference",
        profile_name="regime_adaptive",
        overrides={},
        rationale="Existing low-exposure regime filter used as a defensive reference.",
    ),
    ResearchCandidate(
        key="hybrid_confirmed_breakout",
        profile_name="hybrid_alpha",
        overrides={
            "max_total_exposure": 0.85,
            "max_names": 5,
            "per_trade_weight": 0.12,
            "holding_days": 7,
            "stop_loss_pct": 0.055,
            "take_profit_pct": 0.24,
            "trailing_stop_pct": 0.085,
            "trail_activation_pct": 0.11,
            "allow_sideways": False,
            "regime_min_score": 58.0,
            "regime_exit_score": 45.0,
            "min_breadth20": 0.48,
            "min_regime_momentum20": 0.0,
            "narrow_regime_enabled": False,
            "min_sentiment_score": 56.0,
            "entry_guard_enabled": True,
            "entry_min_open_gap_pct": -1.0,
            "entry_max_open_gap_pct": 3.5,
            "pullback_enabled": False,
            "breakout_entry_weight": 0.12,
            "breakout_min_close_60d_high_ratio": 0.98,
            "breakout_max_distance_ma20_pct": 12.0,
            "breakout_index_min_above120": 3,
            "breakout_index_min_momentum20": 1.0,
            "breakout_index_min_momentum60": 0.0,
            "breakout_transition_weight_scale": 0.30,
            "breakout_transition_max_names": 1,
        },
        rationale="Trend-following only: disable pullbacks, require broad confirmation and avoid opening gaps.",
    ),
    ResearchCandidate(
        key="hybrid_trend_runner",
        profile_name="hybrid_alpha",
        overrides={
            "max_total_exposure": 0.90,
            "max_names": 5,
            "per_trade_weight": 0.13,
            "holding_days": 8,
            "stop_loss_pct": 0.06,
            "take_profit_pct": 0.28,
            "trailing_stop_pct": 0.09,
            "trail_activation_pct": 0.12,
            "regime_min_score": 59.0,
            "regime_exit_score": 45.0,
            "min_breadth20": 0.47,
            "min_regime_momentum20": 0.0,
            "narrow_regime_enabled": False,
            "min_sentiment_score": 55.0,
            "pullback_enabled": False,
            "breakout_entry_weight": 0.13,
            "breakout_min_close_60d_high_ratio": 0.96,
            "breakout_max_distance_ma20_pct": 14.0,
            "runner_rank_threshold": 86.0,
            "runner_momentum10_min": 5.0,
            "runner_momentum20_min": 7.0,
            "runner_take_profit_pct": 0.42,
            "runner_trailing_stop_pct": 0.10,
            "runner_trail_activation_pct": 0.13,
            "runner_holding_days": 12,
        },
        rationale="Trend-following with a longer runner sleeve, but no countertrend pullback entries.",
    ),
    ResearchCandidate(
        key="regime_adaptive_balanced",
        profile_name="regime_adaptive",
        overrides={
            "max_total_exposure": 0.55,
            "max_names": 3,
            "per_trade_weight": 0.12,
            "holding_days": 5,
            "regime_min_score": 57.0,
            "regime_exit_score": 45.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.46,
            "min_breadth60": 0.40,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 54.0,
            "narrow_rank_boost": 8.0,
            "narrow_exposure_scale": 0.30,
            "min_sentiment_score": 54.0,
            "panic_threshold": 64.0,
            "rank_threshold": 86.0,
            "min_pct_chg": 1.2,
            "min_momentum5": 1.6,
            "min_momentum10": 1.0,
            "min_momentum20": 0.0,
            "max_volatility10": 9.5,
            "min_close_high_ratio": 0.94,
        },
        rationale="A moderately invested, breadth-gated trend sleeve between the defensive and tactical references.",
    ),
)


# A final, deliberately small refinement set.  The core candidate is retained
# so every refinement is evaluated against the exact same sample and costs.
BALANCED_REFINEMENT_CANDIDATES = (
    ResearchCandidate(
        key="regime_adaptive_balanced_core",
        profile_name="regime_adaptive",
        overrides={
            "max_total_exposure": 0.55,
            "max_names": 3,
            "per_trade_weight": 0.12,
            "holding_days": 5,
            "regime_min_score": 57.0,
            "regime_exit_score": 45.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.46,
            "min_breadth60": 0.40,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 54.0,
            "narrow_rank_boost": 8.0,
            "narrow_exposure_scale": 0.30,
            "min_sentiment_score": 54.0,
            "panic_threshold": 64.0,
            "rank_threshold": 86.0,
            "min_pct_chg": 1.2,
            "min_momentum5": 1.6,
            "min_momentum10": 1.0,
            "min_momentum20": 0.0,
            "max_volatility10": 9.5,
            "min_close_high_ratio": 0.94,
        },
        rationale="Second-round balanced regime candidate retained as the control.",
    ),
    ResearchCandidate(
        key="regime_adaptive_balanced_quality",
        profile_name="regime_adaptive",
        overrides={
            "max_total_exposure": 0.58,
            "max_names": 3,
            "per_trade_weight": 0.12,
            "holding_days": 5,
            "regime_min_score": 58.0,
            "regime_exit_score": 45.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.47,
            "min_breadth60": 0.40,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 55.0,
            "narrow_rank_boost": 9.0,
            "narrow_exposure_scale": 0.28,
            "min_sentiment_score": 55.0,
            "panic_threshold": 63.0,
            "rank_threshold": 89.0,
            "min_amount_yi": 3.0,
            "min_pct_chg": 1.4,
            "min_momentum5": 1.8,
            "min_momentum10": 1.2,
            "min_momentum20": 0.2,
            "max_volatility10": 9.0,
            "min_close_high_ratio": 0.95,
            "entry_guard_enabled": True,
            "entry_min_open_gap_pct": -0.8,
            "entry_max_open_gap_pct": 3.5,
        },
        rationale="Favor liquid, high-close-confirmation entries and cut marginal signals.",
    ),
    ResearchCandidate(
        key="regime_adaptive_balanced_plus",
        profile_name="regime_adaptive",
        overrides={
            "max_total_exposure": 0.68,
            "max_names": 4,
            "per_trade_weight": 0.14,
            "holding_days": 6,
            "stop_loss_pct": 0.04,
            "take_profit_pct": 0.15,
            "trailing_stop_pct": 0.065,
            "trail_activation_pct": 0.08,
            "regime_min_score": 58.0,
            "regime_exit_score": 45.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.47,
            "min_breadth60": 0.40,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 55.0,
            "narrow_rank_boost": 9.0,
            "narrow_exposure_scale": 0.30,
            "min_sentiment_score": 55.0,
            "panic_threshold": 64.0,
            "rank_threshold": 87.0,
            "min_pct_chg": 1.3,
            "min_momentum5": 1.7,
            "min_momentum10": 1.1,
            "min_momentum20": 0.0,
            "max_volatility10": 9.5,
            "min_close_high_ratio": 0.945,
        },
        rationale="Use more of confirmed strong regimes while retaining the same breadth and panic gates.",
    ),
    ResearchCandidate(
        key="regime_adaptive_balanced_trend",
        profile_name="regime_adaptive",
        overrides={
            "max_total_exposure": 0.62,
            "max_names": 3,
            "per_trade_weight": 0.14,
            "holding_days": 7,
            "stop_loss_pct": 0.045,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.075,
            "trail_activation_pct": 0.095,
            "regime_min_score": 58.0,
            "regime_exit_score": 45.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.47,
            "min_breadth60": 0.40,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 55.0,
            "narrow_rank_boost": 9.0,
            "narrow_exposure_scale": 0.28,
            "min_sentiment_score": 55.0,
            "panic_threshold": 63.0,
            "rank_threshold": 88.0,
            "min_pct_chg": 1.3,
            "min_momentum5": 1.8,
            "min_momentum10": 1.2,
            "min_momentum20": 0.2,
            "max_volatility10": 9.2,
            "min_close_high_ratio": 0.95,
        },
        rationale="A longer, moderately concentrated trend sleeve with unchanged regime protection.",
    ),
)


def run_annual_research(
    years: list[int],
    *,
    universe_profile: str = "blended",
    universe_limit: int = 120,
    candidates: tuple[ResearchCandidate, ...] = DEFAULT_CANDIDATES,
) -> dict[str, Any]:
    annual_results: list[dict[str, Any]] = []
    for year in years:
        start_date = f"{year}0101"
        end_date = _end_date_for_year(year)
        warm_start = f"{year - 1}0401"
        universe, tags = _build_historical_universe_context(
            profile_name=universe_profile,
            as_of_date=start_date,
            limit=universe_limit,
        )
        bars_by_symbol = _load_daily_bars(universe, warm_start, end_date)
        index_bars = _load_index_daily_bars(
            warm_start,
            end_date,
            ["000300.SH", "000905.SH", "000852.SH", "399006.SZ"],
        )
        index_regimes = _build_index_regimes(index_bars)
        for candidate in candidates:
            profile = _strategy_profile(candidate.profile_name, overrides=candidate.overrides)
            profile["symbol_pool_tags"] = tags
            if profile.get("index_regime_enabled"):
                profile["index_regime_by_date"] = index_regimes
                profile["index_regime_dates"] = sorted(index_regimes)
            result = _run_portfolio_backtest(
                bars_by_symbol=bars_by_symbol,
                visible_start=start_date,
                end_date=end_date,
                profile=profile,
            )
            annual_results.append(
                {
                    "year": year,
                    "candidate": candidate.key,
                    "profile_name": candidate.profile_name,
                    "rationale": candidate.rationale,
                    "universe_count": len(universe),
                    "symbols_with_data": len(bars_by_symbol),
                    "metrics": result["metrics"],
                }
            )

    summary = _summarize_candidates(annual_results, candidates)
    output = {
        "research_type": "limited_structural_candidate_comparison",
        "universe_profile": universe_profile,
        "universe_limit": universe_limit,
        "years": years,
        "annual_results": annual_results,
        "candidate_summary": summary,
        "assumptions": [
            "Universe is constructed from the first available daily_basic snapshot on or before each year start.",
            "Signals use T close and enter at the next trading day open with configured slippage, commission, stamp duty and A-share lot sizing.",
            "This daily-bar model cannot fully guarantee fills during suspensions or price-limit queues; live and paper execution need further validation.",
            "quality_t_reference scores price/liquidity characteristics only; it is not a point-in-time financial-statement backtest.",
        ],
    }
    output_path = Path(settings.storage_dir) / "annual_strategy_research.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    output["result_path"] = str(output_path)
    return output


def _end_date_for_year(year: int) -> str:
    today = date.today()
    if year >= today.year:
        return today.strftime("%Y%m%d")
    return f"{year + 1}0101"


def _summarize_candidates(
    annual_results: list[dict[str, Any]],
    candidates: tuple[ResearchCandidate, ...],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    validation_years = {row["year"] for row in annual_results if row["year"] >= 2023}
    for candidate in candidates:
        rows = [row for row in annual_results if row["candidate"] == candidate.key]
        returns = [float(row["metrics"].get("total_return_pct") or 0.0) for row in rows]
        drawdowns = [float(row["metrics"].get("max_drawdown_pct") or 0.0) for row in rows]
        trades = [int(row["metrics"].get("trade_count") or 0) for row in rows]
        validation = [row for row in rows if row["year"] in validation_years]
        validation_returns = [float(row["metrics"].get("total_return_pct") or 0.0) for row in validation]
        compounded = 1.0
        for annual_return in returns:
            compounded *= 1 + annual_return / 100
        validation_compounded = 1.0
        for annual_return in validation_returns:
            validation_compounded *= 1 + annual_return / 100
        summaries.append(
            {
                "candidate": candidate.key,
                "profile_name": candidate.profile_name,
                "rationale": candidate.rationale,
                "years": len(rows),
                "mean_return_pct": round(mean(returns), 4) if returns else 0.0,
                "median_return_pct": round(median(returns), 4) if returns else 0.0,
                "worst_year_return_pct": round(min(returns), 4) if returns else 0.0,
                "negative_years": sum(value < 0 for value in returns),
                "compound_return_pct": round((compounded - 1) * 100, 4),
                "mean_max_drawdown_pct": round(mean(drawdowns), 4) if drawdowns else 0.0,
                "worst_max_drawdown_pct": round(max(drawdowns), 4) if drawdowns else 0.0,
                "mean_trade_count": round(mean(trades), 2) if trades else 0.0,
                "validation_years": len(validation),
                "validation_mean_return_pct": round(mean(validation_returns), 4) if validation_returns else 0.0,
                "validation_compound_return_pct": round((validation_compounded - 1) * 100, 4),
                "validation_negative_years": sum(value < 0 for value in validation_returns),
            }
        )
    return summaries
