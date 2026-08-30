from __future__ import annotations

import json
import logging
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import floor, sqrt
from pathlib import Path
from statistics import mean, median
from typing import Any
from uuid import uuid4

from app.config import settings
from scheduler.trading_calendar import BEIJING_TZ
from storage.logger import log_backtest_run


logger = logging.getLogger(__name__)
_REGIME_CACHE: dict[tuple[int, str], dict[str, Any]] = {}


@dataclass
class DailyPosition:
    symbol: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit: float
    trailing_stop_pct: float
    trail_activation_pct: float
    highest_price: float
    max_holding_days: int
    runner: bool = False
    partial_take_profit: float = 0.0
    partial_fraction: float = 0.0
    partial_taken: bool = False
    quality_tier: str = "B"
    quality_score: float = 0.0
    max_position_weight: float = 0.0
    add_count: int = 0
    held_bars: int = 0
    entry_mode: str = ""


def _strategy_profile(
    name: str,
    holding_days: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "conservative",
        "max_total_exposure": min(settings.max_position_weight, max(settings.paper_max_position_pct, 0.01)),
        "max_names": settings.max_push_stocks,
        "per_trade_weight": min(
            min(settings.max_position_weight, max(settings.paper_max_position_pct, 0.01))
            / max(settings.max_push_stocks, 1),
            settings.paper_max_position_pct,
        ),
        "holding_days": holding_days or settings.backtest_holding_days,
        "stop_loss_pct": settings.stop_loss_pct,
        "take_profit_pct": 0.16,
        "trailing_stop_pct": 0.0,
        "trail_activation_pct": 0.08,
        "runner_enabled": False,
        "runner_rank_threshold": 90.0,
        "runner_momentum10_min": 8.0,
        "runner_momentum20_min": 12.0,
        "runner_take_profit_pct": 0.45,
        "runner_trailing_stop_pct": 0.10,
        "runner_trail_activation_pct": 0.12,
        "runner_holding_days": holding_days or 10,
        "runner_partial_enabled": False,
        "runner_partial_take_profit_pct": 0.18,
        "runner_partial_fraction": 0.5,
        "quality_t_enabled": False,
        "tier_a_min_quality": 78.0,
        "tier_b_min_quality": 64.0,
        "min_quality_score": 0.0,
        "tier_a_entry_weight": 0.08,
        "tier_b_entry_weight": 0.055,
        "tier_c_entry_weight": 0.035,
        "tier_a_max_weight": 0.18,
        "tier_b_max_weight": 0.10,
        "tier_c_max_weight": 0.05,
        "tier_a_stop_loss_pct": 0.095,
        "tier_b_stop_loss_pct": 0.065,
        "tier_c_stop_loss_pct": 0.045,
        "tier_a_take_profit_pct": 0.45,
        "tier_b_take_profit_pct": 0.22,
        "tier_c_take_profit_pct": 0.14,
        "tier_a_trailing_stop_pct": 0.12,
        "tier_b_trailing_stop_pct": 0.08,
        "tier_c_trailing_stop_pct": 0.0,
        "tier_a_holding_days": 18,
        "tier_b_holding_days": 8,
        "tier_c_holding_days": 4,
        "t_trade_enabled": False,
        "t_trade_dip_pct": 0.035,
        "t_trade_rebound_pct": 0.025,
        "t_trade_fraction": 0.35,
        "add_on_enabled": False,
        "add_on_trigger_pct": 0.055,
        "add_on_fraction": 0.45,
        "max_add_count": 1,
        "allow_sideways": False,
        "regime_filter_enabled": False,
        "market_exit_enabled": False,
        "regime_min_score": 55.0,
        "regime_exit_score": 42.0,
        "regime_full_score": 70.0,
        "min_breadth20": 0.0,
        "min_breadth60": 0.0,
        "min_regime_momentum20": -100.0,
        "min_regime_momentum60": -100.0,
        "narrow_regime_enabled": False,
        "narrow_regime_min_score": 52.0,
        "narrow_min_momentum5": 0.0,
        "narrow_min_sentiment_score": 58.0,
        "narrow_rank_boost": 6.0,
        "narrow_exposure_scale": 0.35,
        "stop_loss_cooldown_enabled": False,
        "stop_loss_cooldown_count": 2,
        "stop_loss_cooldown_window": 8,
        "stop_loss_cooldown_days": 10,
        "drawdown_cooldown_enabled": False,
        "drawdown_cooldown_pct": 0.04,
        "drawdown_cooldown_days": 15,
        "bear_market_block_enabled": False,
        "bear_market_score_max": 55.0,
        "bear_market_momentum60_max": -4.0,
        "bear_market_breadth60_max": 0.43,
        "entry_guard_enabled": False,
        "entry_min_open_gap_pct": -1.2,
        "entry_max_open_gap_pct": 6.5,
        "pullback_entry_min_open_gap_pct": -2.5,
        "index_regime_enabled": False,
        "index_bear_momentum60_max": 2.5,
        "index_bear_above60_max": 1,
        "index_symbols": ["000300.SH", "000905.SH", "000852.SH", "399006.SZ"],
        "pool_switch_enabled": False,
        "symbol_pool_tags": {},
        "hybrid_alpha_enabled": False,
        "breakout_entry_weight": 0.0,
        "breakout_index_gate_enabled": False,
        "breakout_index_min_above120": 2,
        "breakout_index_min_momentum20": 0.0,
        "breakout_index_min_momentum60": 0.0,
        "breakout_index_relaxed_above60": 3,
        "breakout_index_relaxed_momentum20": 4.0,
        "breakout_confirmed_momentum20": 10.0,
        "breakout_confirmed_momentum60": 5.0,
        "breakout_confirmed_min_above120_for_momentum60": 0,
        "breakout_transition_weight_scale": 0.65,
        "breakout_transition_max_names": 3,
        "breakout_strong_weight_scale": 1.0,
        "breakout_overheat_enabled": False,
        "breakout_overheat_momentum5_min": 4.0,
        "breakout_overheat_momentum20_min": 10.0,
        "breakout_overheat_momentum60_max": 15.0,
        "breakout_overheat_weight_scale": 0.65,
        "breakout_overheat_max_names": 3,
        "breakout_stale_enabled": False,
        "breakout_stale_momentum5_max": 2.0,
        "breakout_stale_momentum20_max": 8.0,
        "breakout_stale_weight_scale": 0.55,
        "breakout_stale_max_names": 2,
        "breakout_min_close_60d_high_ratio": 0.0,
        "breakout_max_distance_ma20_pct": 0.0,
        "breakout_min_regime_score": 0.0,
        "breakout_min_breadth20": 0.0,
        "breakout_min_regime_momentum20": -100.0,
        "breakout_min_sentiment_score": 0.0,
        "breakout_requires_broad_pass": False,
        "pullback_enabled": False,
        "pullback_entry_weight": 0.0,
        "pullback_min_pct": -3.5,
        "pullback_max_pct": 1.2,
        "pullback_min_momentum20": 2.0,
        "pullback_ma20_floor_pct": -2.5,
        "pullback_ma20_ceiling_pct": 8.0,
        "pullback_rank_threshold": 70.0,
        "pullback_stop_loss_pct": 0.032,
        "pullback_take_profit_pct": 0.075,
        "pullback_trailing_stop_pct": 0.045,
        "pullback_holding_days": 3,
        "min_sentiment_score": settings.sentiment_min_buy_score,
        "panic_threshold": settings.sentiment_panic_threshold,
        "rank_threshold": max(settings.confidence_threshold * 100, 70.0),
        "min_history_bars": 20,
        "min_amount_yi": settings.min_turnover_yi,
        "min_pct_chg": 0.0,
        "max_pct_chg": settings.max_recommend_pct_change,
        "min_momentum5": 0.0,
        "min_momentum10": 0.0,
        "min_momentum20": -100.0,
        "max_volatility10": 12.0,
        "require_ma_stack": True,
        "require_ma60_stack": False,
        "max_distance_ma20_pct": 0.0,
        "min_amount_vs_ma20": 0.0,
        "min_close_high_ratio": 0.0,
        "pct_sweet_spot": 5.0,
        "volatility_penalty_start": 3.5,
        "momentum5_weight": 4.0,
        "momentum10_weight": 2.0,
        "momentum20_weight": 0.0,
        "sentiment_weight": 0.26,
        "trend_weight": 0.32,
        "liquidity_weight": 0.22,
        "pct_weight": 0.20,
        "max_signal_gap_days": 5,
        "post_gap_breakout_throttle_enabled": False,
        "post_gap_cooldown_trading_days": 3,
        "post_gap_breakout_weight_scale": 0.35,
        "post_gap_breakout_max_names": 1,
    }
    presets: dict[str, dict[str, Any]] = {
        "conservative": {},
        "aggressive": {
            "name": "aggressive",
            "max_total_exposure": 1.0,
            "max_names": 5,
            "per_trade_weight": 0.18,
            "holding_days": holding_days or 5,
            "stop_loss_pct": 0.055,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.075,
            "trail_activation_pct": 0.09,
            "min_sentiment_score": 50.0,
            "rank_threshold": 72.0,
            "min_pct_chg": 0.8,
            "max_pct_chg": 9.35,
            "min_momentum5": 1.5,
            "min_momentum10": 1.0,
            "max_volatility10": 10.0,
            "pct_sweet_spot": 6.5,
            "momentum5_weight": 4.5,
            "momentum10_weight": 2.4,
            "momentum20_weight": 0.5,
            "sentiment_weight": 0.18,
            "trend_weight": 0.44,
            "liquidity_weight": 0.18,
            "pct_weight": 0.20,
        },
        "breakout": {
            "name": "breakout",
            "max_total_exposure": 0.95,
            "max_names": 4,
            "per_trade_weight": 0.25,
            "holding_days": holding_days or 6,
            "stop_loss_pct": 0.07,
            "take_profit_pct": 0.24,
            "trailing_stop_pct": 0.09,
            "trail_activation_pct": 0.12,
            "min_sentiment_score": 48.0,
            "rank_threshold": 76.0,
            "min_amount_yi": max(settings.min_turnover_yi, 2.0),
            "min_pct_chg": 2.0,
            "max_pct_chg": 9.55,
            "min_momentum5": 3.0,
            "min_momentum10": 4.0,
            "max_volatility10": 13.5,
            "pct_sweet_spot": 7.5,
            "momentum5_weight": 5.0,
            "momentum10_weight": 3.0,
            "momentum20_weight": 1.0,
            "sentiment_weight": 0.14,
            "trend_weight": 0.52,
            "liquidity_weight": 0.12,
            "pct_weight": 0.22,
        },
        "optimized": {
            "name": "optimized",
            "max_total_exposure": 1.0,
            "max_names": 5,
            "per_trade_weight": 0.24,
            "holding_days": holding_days or 5,
            "stop_loss_pct": 0.055,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.075,
            "trail_activation_pct": 0.09,
            "min_sentiment_score": 50.0,
            "rank_threshold": 72.0,
            "min_pct_chg": 0.8,
            "max_pct_chg": 9.35,
            "min_momentum5": 1.5,
            "min_momentum10": 1.0,
            "max_volatility10": 10.0,
            "pct_sweet_spot": 6.5,
            "momentum5_weight": 4.5,
            "momentum10_weight": 2.4,
            "momentum20_weight": 0.5,
            "sentiment_weight": 0.18,
            "trend_weight": 0.44,
            "liquidity_weight": 0.18,
            "pct_weight": 0.20,
        },
        "runner": {
            "name": "runner",
            "max_total_exposure": 1.0,
            "max_names": 5,
            "per_trade_weight": 0.24,
            "holding_days": holding_days or 5,
            "stop_loss_pct": 0.055,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.075,
            "trail_activation_pct": 0.09,
            "runner_enabled": True,
            "runner_rank_threshold": 88.0,
            "runner_momentum10_min": 7.0,
            "runner_momentum20_min": 9.0,
            "runner_take_profit_pct": 0.45,
            "runner_trailing_stop_pct": 0.10,
            "runner_trail_activation_pct": 0.12,
            "runner_holding_days": 10,
            "runner_partial_enabled": True,
            "runner_partial_take_profit_pct": 0.18,
            "runner_partial_fraction": 0.5,
            "min_sentiment_score": 50.0,
            "rank_threshold": 72.0,
            "min_pct_chg": 0.8,
            "max_pct_chg": 9.35,
            "min_momentum5": 1.5,
            "min_momentum10": 1.0,
            "max_volatility10": 10.0,
            "pct_sweet_spot": 6.5,
            "momentum5_weight": 4.5,
            "momentum10_weight": 2.4,
            "momentum20_weight": 0.5,
            "sentiment_weight": 0.18,
            "trend_weight": 0.44,
            "liquidity_weight": 0.18,
            "pct_weight": 0.20,
        },
        "quality_t": {
            "name": "quality_t",
            "quality_t_enabled": True,
            "max_total_exposure": 1.0,
            "max_names": 6,
            "per_trade_weight": 0.06,
            "holding_days": holding_days or 6,
            "stop_loss_pct": 0.06,
            "take_profit_pct": 0.20,
            "trailing_stop_pct": 0.08,
            "trail_activation_pct": 0.10,
            "min_sentiment_score": 48.0,
            "rank_threshold": 72.0,
            "min_amount_yi": max(settings.min_turnover_yi, 2.0),
            "min_pct_chg": 0.8,
            "max_pct_chg": 9.35,
            "min_momentum5": 1.5,
            "min_momentum10": 1.0,
            "max_volatility10": 9.5,
            "pct_sweet_spot": 5.8,
            "momentum5_weight": 3.2,
            "momentum10_weight": 2.0,
            "momentum20_weight": 1.0,
            "sentiment_weight": 0.16,
            "trend_weight": 0.34,
            "liquidity_weight": 0.24,
            "pct_weight": 0.26,
            "tier_a_min_quality": 90.0,
            "tier_b_min_quality": 64.0,
            "min_quality_score": 64.0,
            "tier_a_entry_weight": 0.16,
            "tier_b_entry_weight": 0.16,
            "tier_c_entry_weight": 0.0,
            "tier_a_max_weight": 0.24,
            "tier_b_max_weight": 0.16,
            "tier_c_max_weight": 0.0,
            "tier_a_stop_loss_pct": 0.075,
            "tier_b_stop_loss_pct": 0.055,
            "tier_c_stop_loss_pct": 0.045,
            "tier_a_take_profit_pct": 0.34,
            "tier_b_take_profit_pct": 0.18,
            "tier_c_take_profit_pct": 0.14,
            "tier_a_trailing_stop_pct": 0.085,
            "tier_b_trailing_stop_pct": 0.075,
            "tier_c_trailing_stop_pct": 0.0,
            "tier_a_holding_days": 8,
            "tier_b_holding_days": 5,
            "tier_c_holding_days": 4,
            "runner_partial_enabled": True,
            "runner_partial_take_profit_pct": 0.18,
            "runner_partial_fraction": 0.35,
            "t_trade_enabled": True,
            "t_trade_dip_pct": 0.04,
            "t_trade_rebound_pct": 0.025,
            "t_trade_fraction": 0.30,
            "add_on_enabled": True,
            "add_on_trigger_pct": 0.07,
            "add_on_fraction": 0.25,
            "max_add_count": 1,
        },
        "regime_adaptive": {
            "name": "regime_adaptive",
            "max_total_exposure": 0.35,
            "max_names": 2,
            "per_trade_weight": 0.10,
            "holding_days": holding_days or 3,
            "stop_loss_pct": 0.035,
            "take_profit_pct": 0.12,
            "trailing_stop_pct": 0.055,
            "trail_activation_pct": 0.07,
            "allow_sideways": False,
            "regime_filter_enabled": True,
            "market_exit_enabled": True,
            "regime_min_score": 58.0,
            "regime_exit_score": 47.0,
            "regime_full_score": 72.0,
            "min_breadth20": 0.48,
            "min_breadth60": 0.44,
            "min_regime_momentum20": 0.0,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 51.0,
            "narrow_min_momentum5": 0.6,
            "narrow_min_sentiment_score": 60.0,
            "narrow_rank_boost": 10.0,
            "narrow_exposure_scale": 0.25,
            "stop_loss_cooldown_enabled": True,
            "stop_loss_cooldown_count": 2,
            "stop_loss_cooldown_window": 10,
            "stop_loss_cooldown_days": 16,
            "min_sentiment_score": 56.0,
            "panic_threshold": 62.0,
            "rank_threshold": 92.0,
            "min_history_bars": 20,
            "min_amount_yi": max(settings.min_turnover_yi, 2.5),
            "min_pct_chg": 1.0,
            "max_pct_chg": 8.4,
            "min_momentum5": 1.8,
            "min_momentum10": 1.2,
            "min_momentum20": -1.0,
            "max_volatility10": 8.8,
            "require_ma_stack": True,
            "require_ma60_stack": False,
            "max_distance_ma20_pct": 0.0,
            "min_amount_vs_ma20": 0.0,
            "min_close_high_ratio": 0.95,
            "pct_sweet_spot": 4.5,
            "volatility_penalty_start": 3.0,
            "momentum5_weight": 3.0,
            "momentum10_weight": 2.8,
            "momentum20_weight": 1.6,
            "sentiment_weight": 0.16,
            "trend_weight": 0.46,
            "liquidity_weight": 0.20,
            "pct_weight": 0.18,
        },
        "regime_adaptive_balanced_trend": {
            "name": "regime_adaptive_balanced_trend",
            "max_total_exposure": 0.62,
            "max_names": 3,
            "per_trade_weight": 0.14,
            "holding_days": holding_days or 7,
            "stop_loss_pct": 0.045,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.075,
            "trail_activation_pct": 0.095,
            "allow_sideways": False,
            "regime_filter_enabled": True,
            "market_exit_enabled": True,
            "regime_min_score": 58.0,
            "regime_exit_score": 45.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.47,
            "min_breadth60": 0.40,
            "min_regime_momentum20": 0.0,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 55.0,
            "narrow_min_momentum5": 0.6,
            "narrow_min_sentiment_score": 60.0,
            "narrow_rank_boost": 9.0,
            "narrow_exposure_scale": 0.28,
            "stop_loss_cooldown_enabled": True,
            "stop_loss_cooldown_count": 2,
            "stop_loss_cooldown_window": 10,
            "stop_loss_cooldown_days": 16,
            "min_sentiment_score": 55.0,
            "panic_threshold": 63.0,
            "rank_threshold": 88.0,
            "min_history_bars": 20,
            "min_amount_yi": max(settings.min_turnover_yi, 2.5),
            "min_pct_chg": 1.3,
            "max_pct_chg": 8.4,
            "min_momentum5": 1.8,
            "min_momentum10": 1.2,
            "min_momentum20": 0.2,
            "max_volatility10": 9.2,
            "require_ma_stack": True,
            "require_ma60_stack": False,
            "min_close_high_ratio": 0.95,
            "pct_sweet_spot": 4.5,
            "volatility_penalty_start": 3.0,
            "momentum5_weight": 3.0,
            "momentum10_weight": 2.8,
            "momentum20_weight": 1.6,
            "sentiment_weight": 0.16,
            "trend_weight": 0.46,
            "liquidity_weight": 0.20,
            "pct_weight": 0.18,
        },
        "hybrid_alpha": {
            "name": "hybrid_alpha",
            "max_total_exposure": 1.0,
            "max_names": 6,
            "per_trade_weight": 0.12,
            "holding_days": holding_days or 5,
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.075,
            "trail_activation_pct": 0.09,
            "runner_enabled": True,
            "runner_rank_threshold": 88.0,
            "runner_momentum10_min": 7.0,
            "runner_momentum20_min": 9.0,
            "runner_take_profit_pct": 0.38,
            "runner_trailing_stop_pct": 0.10,
            "runner_trail_activation_pct": 0.12,
            "runner_holding_days": 10,
            "runner_partial_enabled": True,
            "runner_partial_take_profit_pct": 0.16,
            "runner_partial_fraction": 0.35,
            "allow_sideways": True,
            "regime_filter_enabled": True,
            "market_exit_enabled": True,
            "regime_min_score": 56.0,
            "regime_exit_score": 42.0,
            "regime_full_score": 70.0,
            "min_breadth20": 0.44,
            "min_breadth60": 0.0,
            "min_regime_momentum20": -0.5,
            "min_regime_momentum60": -100.0,
            "narrow_regime_enabled": True,
            "narrow_regime_min_score": 52.0,
            "narrow_min_momentum5": -0.8,
            "narrow_min_sentiment_score": 56.0,
            "narrow_rank_boost": 5.0,
            "narrow_exposure_scale": 0.45,
            "stop_loss_cooldown_enabled": True,
            "stop_loss_cooldown_count": 2,
            "stop_loss_cooldown_window": 10,
            "stop_loss_cooldown_days": 8,
            "drawdown_cooldown_enabled": True,
            "drawdown_cooldown_pct": 0.055,
            "drawdown_cooldown_days": 10,
            "bear_market_block_enabled": True,
            "bear_market_score_max": 55.0,
            "bear_market_momentum60_max": -4.0,
            "bear_market_breadth60_max": 0.43,
            "entry_guard_enabled": False,
            "entry_min_open_gap_pct": -0.8,
            "entry_max_open_gap_pct": 6.0,
            "pullback_entry_min_open_gap_pct": -2.2,
            "index_regime_enabled": True,
            "index_bear_momentum60_max": 2.5,
            "index_bear_above60_max": 1,
            "pool_switch_enabled": True,
            "hybrid_alpha_enabled": True,
            "breakout_entry_weight": 0.18,
            "breakout_index_gate_enabled": True,
            "breakout_index_min_above120": 2,
            "breakout_index_min_momentum20": 0.0,
            "breakout_index_min_momentum60": 0.0,
            "breakout_index_relaxed_above60": 4,
            "breakout_index_relaxed_momentum20": 12.0,
            "breakout_confirmed_momentum20": 14.0,
            "breakout_confirmed_momentum60": 8.0,
            "breakout_confirmed_min_above120_for_momentum60": 4,
            "breakout_transition_weight_scale": 0.45,
            "breakout_transition_max_names": 2,
            "breakout_strong_weight_scale": 1.18,
            "breakout_overheat_enabled": True,
            "breakout_overheat_momentum5_min": 4.0,
            "breakout_overheat_momentum20_min": 10.0,
            "breakout_overheat_momentum60_max": 15.0,
            "breakout_overheat_weight_scale": 0.65,
            "breakout_overheat_max_names": 3,
            "breakout_stale_enabled": True,
            "breakout_stale_momentum5_max": 2.0,
            "breakout_stale_momentum20_max": 8.0,
            "breakout_stale_weight_scale": 0.50,
            "breakout_stale_max_names": 2,
            "post_gap_breakout_throttle_enabled": True,
            "post_gap_cooldown_trading_days": 3,
            "post_gap_breakout_weight_scale": 0.35,
            "post_gap_breakout_max_names": 1,
            "breakout_min_close_60d_high_ratio": 0.96,
            "breakout_max_distance_ma20_pct": 14.5,
            "breakout_min_regime_score": 0.0,
            "breakout_min_breadth20": 0.0,
            "breakout_min_regime_momentum20": -100.0,
            "breakout_min_sentiment_score": 60.0,
            "breakout_requires_broad_pass": True,
            "pullback_enabled": True,
            "pullback_entry_weight": 0.055,
            "pullback_min_pct": -3.8,
            "pullback_max_pct": 1.4,
            "pullback_min_momentum20": 2.0,
            "pullback_ma20_floor_pct": -2.2,
            "pullback_ma20_ceiling_pct": 9.0,
            "pullback_rank_threshold": 79.0,
            "pullback_stop_loss_pct": 0.032,
            "pullback_take_profit_pct": 0.105,
            "pullback_trailing_stop_pct": 0.055,
            "pullback_holding_days": 4,
            "min_sentiment_score": 52.0,
            "panic_threshold": 72.0,
            "rank_threshold": 76.0,
            "min_history_bars": 20,
            "min_amount_yi": max(settings.min_turnover_yi, 2.0),
            "min_pct_chg": 0.6,
            "max_pct_chg": 9.25,
            "min_momentum5": 0.0,
            "min_momentum10": -1.0,
            "min_momentum20": -4.0,
            "max_volatility10": 11.5,
            "require_ma_stack": False,
            "require_ma60_stack": False,
            "max_distance_ma20_pct": 24.0,
            "min_amount_vs_ma20": 0.0,
            "min_close_high_ratio": 0.90,
            "pct_sweet_spot": 5.8,
            "volatility_penalty_start": 5.0,
            "momentum5_weight": 3.4,
            "momentum10_weight": 2.5,
            "momentum20_weight": 1.2,
            "sentiment_weight": 0.20,
            "trend_weight": 0.42,
            "liquidity_weight": 0.18,
            "pct_weight": 0.20,
        },
    }
    selected = {**base, **presets.get(name, presets["conservative"])}
    if holding_days is not None:
        selected["holding_days"] = holding_days
    if overrides:
        selected.update({key: value for key, value in overrides.items() if value is not None})
    total_weight = (
        float(selected["sentiment_weight"])
        + float(selected["trend_weight"])
        + float(selected["liquidity_weight"])
        + float(selected["pct_weight"])
    )
    if total_weight <= 0:
        selected["trend_weight"] = 1.0
        total_weight = 1.0
    for key in ("sentiment_weight", "trend_weight", "liquidity_weight", "pct_weight"):
        selected[key] = float(selected[key]) / total_weight
    selected["max_total_exposure"] = min(max(float(selected["max_total_exposure"]), 0.01), 1.0)
    selected["max_names"] = max(int(selected["max_names"]), 1)
    selected["per_trade_weight"] = min(max(float(selected["per_trade_weight"]), 0.01), selected["max_total_exposure"])
    selected["holding_days"] = max(int(selected["holding_days"]), 1)
    return selected


def run_historical_daily_backtest(
    lookback_days: int = 180,
    holding_days: int | None = None,
    symbols: list[str] | None = None,
    profile_name: str = "conservative",
    profile_overrides: dict[str, Any] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    universe_profile: str = "watchlist",
    universe_limit: int = 300,
    universe_asof: str | None = None,
) -> dict[str, Any]:
    run_id = uuid4().hex
    profile = _strategy_profile(profile_name, holding_days, profile_overrides)
    hold_days = int(profile["holding_days"])
    if start_date and end_date:
        start_dt = datetime.strptime(start_date, "%Y%m%d").date()
        end_dt = datetime.strptime(end_date, "%Y%m%d").date()
    else:
        end_dt = datetime.now(BEIJING_TZ).date() - timedelta(days=1)
        start_dt = end_dt - timedelta(days=lookback_days)
    if end_dt < start_dt:
        raise ValueError("end_date must be greater than or equal to start_date")
    warmup_days = 260 if bool(profile.get("index_regime_enabled")) else 120 if bool(profile.get("regime_filter_enabled")) else 40
    warm_start_dt = start_dt - timedelta(days=warmup_days)
    start_date = start_dt.strftime("%Y%m%d")
    warm_start_date = warm_start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")
    universe_tags: dict[str, list[str]] = {}
    if symbols:
        universe = symbols
    else:
        universe, universe_tags = _build_historical_universe_context(
            profile_name=universe_profile,
            as_of_date=universe_asof or end_date,
            limit=universe_limit,
        )
    if universe_tags:
        profile["symbol_pool_tags"] = universe_tags

    try:
        bars_by_symbol = _load_daily_bars(universe, warm_start_date, end_date)
        if bool(profile.get("index_regime_enabled")):
            index_bars = _load_index_daily_bars(warm_start_date, end_date, profile.get("index_symbols") or [])
            index_regimes = _build_index_regimes(index_bars)
            profile["index_regime_by_date"] = index_regimes
            profile["index_regime_dates"] = sorted(index_regimes)
        result = _run_portfolio_backtest(
            bars_by_symbol=bars_by_symbol,
            visible_start=start_date,
            end_date=end_date,
            profile=profile,
        )
        profile_for_result = {**profile}
        if isinstance(profile_for_result.get("post_gap_signal_dates"), set):
            profile_for_result["post_gap_signal_dates"] = sorted(profile_for_result["post_gap_signal_dates"])
        result.update(
            {
                "run_id": run_id,
                "status": "ok" if result["metrics"]["trade_count"] else "skipped_no_trades",
                "lookback_days": lookback_days,
                "holding_days": hold_days,
                "start_date": start_date,
                "end_date": end_date,
                "universe_count": len(universe),
                "universe_profile": universe_profile,
                "universe_limit": universe_limit,
                "universe_asof": universe_asof or end_date,
                "universe_tags_count": len(universe_tags),
                "symbols_with_data": len(bars_by_symbol),
                "profile": profile_for_result,
                "no_real_orders": True,
            }
        )
        chart_path = _write_curve_svg(result)
        json_path = _write_result_json(result, chart_path)
        result["chart_path"] = str(chart_path)
        result["result_path"] = str(json_path)
        log_backtest_run(
            run_id=run_id,
            strategy_name=f"v1_6_historical_daily_{profile['name']}",
            status=result["status"],
            lookback_days=lookback_days,
            holding_days=hold_days,
            metrics={**result["metrics"], "chart_path": str(chart_path), "result_path": str(json_path)},
            trades=result["trades"][:1000],
        )
        return result
    except Exception as exc:
        logger.exception("historical daily backtest failed")
        log_backtest_run(
            run_id=run_id,
            strategy_name="v1_6_historical_daily",
            status="failed",
            lookback_days=lookback_days,
            holding_days=hold_days,
            metrics={},
            trades=[],
            error_text=str(exc),
        )
        return {
            "run_id": run_id,
            "status": "failed",
            "error": str(exc),
            "lookback_days": lookback_days,
            "holding_days": hold_days,
            "no_real_orders": True,
        }


def run_historical_parameter_search(
    lookback_days: int = 180,
    profile_name: str = "aggressive",
    top_n: int = 20,
) -> dict[str, Any]:
    run_id = uuid4().hex
    end_dt = datetime.now(BEIJING_TZ).date() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=lookback_days)
    warm_start_dt = start_dt - timedelta(days=40)
    start_date = start_dt.strftime("%Y%m%d")
    warm_start_date = warm_start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")
    universe = settings.watch_symbols
    if not universe:
        from data.tushare_client import TushareClient

        universe = TushareClient()._default_symbols()

    bars_by_symbol = _load_daily_bars(universe, warm_start_date, end_date)
    base_profile = _strategy_profile(profile_name, None, None)
    candidates: list[dict[str, Any]] = []
    total = 0
    for exposure in (0.85, 0.95, 1.0):
        for max_names in (4, 5, 6):
            for per_trade_weight in (0.16, 0.20, 0.24, 0.28):
                for holding_days in (4, 5, 7, 9):
                    for stop_loss_pct in (0.04, 0.05, 0.06, 0.075):
                        for take_profit_pct in (0.12, 0.16, 0.22, 0.30):
                            for trailing_stop_pct in (0.0, 0.06, 0.08):
                                for rank_threshold in (68.0, 70.0, 72.0, 74.0):
                                    for min_pct_chg in (0.0, 0.6, 1.2):
                                        total += 1
                                        profile = {
                                            **base_profile,
                                            "name": "optimized_search",
                                            "max_total_exposure": exposure,
                                            "max_names": max_names,
                                            "per_trade_weight": per_trade_weight,
                                            "holding_days": holding_days,
                                            "stop_loss_pct": stop_loss_pct,
                                            "take_profit_pct": take_profit_pct,
                                            "trailing_stop_pct": trailing_stop_pct,
                                            "rank_threshold": rank_threshold,
                                            "min_pct_chg": min_pct_chg,
                                        }
                                        result = _run_portfolio_backtest(
                                            bars_by_symbol=bars_by_symbol,
                                            visible_start=start_date,
                                            end_date=end_date,
                                            profile=profile,
                                        )
                                        metrics = result["metrics"]
                                        trade_count = int(metrics.get("trade_count") or 0)
                                        if trade_count < 20:
                                            continue
                                        return_pct = float(metrics.get("total_return_pct") or 0.0)
                                        drawdown_pct = float(metrics.get("max_drawdown_pct") or 0.0)
                                        win_rate = float(metrics.get("win_rate") or 0.0)
                                        score = return_pct - drawdown_pct * 0.75 + win_rate * 3
                                        candidates.append(
                                            {
                                                "score": round(score, 4),
                                                "return_pct": round(return_pct, 4),
                                                "max_drawdown_pct": round(drawdown_pct, 4),
                                                "trade_count": trade_count,
                                                "win_rate": round(win_rate, 4),
                                                "profit_factor": metrics.get("profit_factor"),
                                                "stop_rate": metrics.get("stop_rate"),
                                                "sharpe_proxy": metrics.get("sharpe_proxy"),
                                                "profile": {
                                                    key: profile[key]
                                                    for key in (
                                                        "max_total_exposure",
                                                        "max_names",
                                                        "per_trade_weight",
                                                        "holding_days",
                                                        "stop_loss_pct",
                                                        "take_profit_pct",
                                                        "trailing_stop_pct",
                                                        "rank_threshold",
                                                        "min_pct_chg",
                                                    )
                                                },
                                            }
                                        )
    candidates.sort(key=lambda item: (item["score"], item["return_pct"]), reverse=True)
    output = {
        "run_id": run_id,
        "status": "ok" if candidates else "skipped_no_candidates",
        "lookback_days": lookback_days,
        "start_date": start_date,
        "end_date": end_date,
        "profile_base": profile_name,
        "universe_count": len(universe),
        "symbols_with_data": len(bars_by_symbol),
        "searched_count": total,
        "candidate_count": len(candidates),
        "top": candidates[:top_n],
        "no_real_orders": True,
    }
    path = settings.storage_dir / f"historical_optimization_{run_id}.json"
    output["result_path"] = str(path)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _build_historical_universe(profile_name: str, as_of_date: str, limit: int = 300) -> list[str]:
    universe, _tags = _build_historical_universe_context(profile_name, as_of_date, limit)
    return universe


def _build_historical_universe_context(
    profile_name: str,
    as_of_date: str,
    limit: int = 300,
) -> tuple[list[str], dict[str, list[str]]]:
    from data.stock_universe import build_stock_universe
    from data.tushare_client import TushareClient

    client = TushareClient()
    normalized_profile = str(profile_name or "watchlist").strip().lower()
    if normalized_profile in {"watchlist", "settings", "current"}:
        universe = client.core_selection_symbols()
        return universe, {symbol: ["core"] for symbol in universe}
    if normalized_profile == "default":
        universe = client._default_symbols()
        return universe, {symbol: ["core"] for symbol in universe}

    try:
        frame = _fetch_daily_basic_snapshot(as_of_date)
        return build_stock_universe(
            frame,
            profile_name=normalized_profile,
            limit=limit,
            core_symbols=client.core_selection_symbols(),
            normalize_symbol=client.normalize_symbol,
        )
    except Exception as exc:
        logger.warning("daily_basic universe failed; falling back to watchlist: %s", exc)
        universe = client.core_selection_symbols()
        return universe, {symbol: ["core"] for symbol in universe}


def _fetch_daily_basic_snapshot(as_of_date: str) -> Any:
    import tushare as ts

    if settings.tushare_token:
        ts.set_token(settings.tushare_token)
    pro = ts.pro_api()
    if settings.tushare_token:
        setattr(pro, "_DataApi__token", settings.tushare_token)
        setattr(pro, "_DataApi_token", settings.tushare_token)
    if settings.tushare_base_url:
        setattr(pro, "_DataApi__http_url", settings.tushare_base_url.rstrip("/"))
    as_of = datetime.strptime(as_of_date, "%Y%m%d").date()
    fields = "ts_code,turnover_rate,volume_ratio,total_mv,circ_mv"
    for offset in range(0, 15):
        trade_date = (as_of - timedelta(days=offset)).strftime("%Y%m%d")
        frame = pro.daily_basic(trade_date=trade_date, fields=fields)
        if frame is not None and not frame.empty:
            if offset:
                logger.info("daily_basic snapshot rolled back from %s to %s", as_of_date, trade_date)
            return frame
    return pro.daily_basic(trade_date=as_of_date, fields=fields)


def _load_index_daily_bars(start_date: str, end_date: str, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    import tushare as ts

    if settings.tushare_token:
        ts.set_token(settings.tushare_token)
    pro = ts.pro_api()
    if settings.tushare_token:
        setattr(pro, "_DataApi__token", settings.tushare_token)
        setattr(pro, "_DataApi_token", settings.tushare_token)
    if settings.tushare_base_url:
        setattr(pro, "_DataApi__http_url", settings.tushare_base_url.rstrip("/"))

    output: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        try:
            frame = pro.index_daily(ts_code=symbol, start_date=start_date, end_date=end_date)
        except Exception as exc:
            logger.warning("index daily failed for %s: %s", symbol, exc)
            continue
        if frame is None or frame.empty:
            continue
        rows = []
        columns = {str(column).strip().lower(): column for column in frame.columns}
        for _, row in frame.iterrows():
            close_col = columns.get("close")
            pct_col = columns.get("pct_chg")
            rows.append(
                {
                    "ts_code": str(row.get(columns.get("ts_code"), symbol)),
                    "trade_date": str(row.get(columns.get("trade_date"), "")),
                    "close": _float(row.get(close_col) if close_col is not None else None, 0.0),
                    "pct_chg": _float(row.get(pct_col) if pct_col is not None else None, 0.0),
                }
            )
        output[symbol] = sorted(
            [item for item in rows if item["trade_date"] and item["close"] > 0],
            key=lambda item: item["trade_date"],
        )
    return output


def _build_index_regimes(index_bars: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    all_dates = sorted({bar["trade_date"] for bars in index_bars.values() for bar in bars})
    regimes: dict[str, dict[str, Any]] = {}
    for trade_date in all_dates:
        momentum5_values: list[float] = []
        momentum20_values: list[float] = []
        momentum60_values: list[float] = []
        above60 = 0
        above120 = 0
        count = 0
        for bars in index_bars.values():
            index = _bar_index(bars, trade_date)
            if index is None or index < 120:
                continue
            window = bars[: index + 1]
            closes = [_float(item.get("close"), 0.0) for item in window if _float(item.get("close"), 0.0) > 0]
            if len(closes) < 121:
                continue
            close = closes[-1]
            ma60 = mean(closes[-60:])
            ma120 = mean(closes[-120:])
            momentum5 = (close / closes[-6] - 1) * 100 if closes[-6] else 0.0
            momentum20 = (close / closes[-21] - 1) * 100 if closes[-21] else 0.0
            momentum60 = (close / closes[-61] - 1) * 100 if closes[-61] else 0.0
            momentum5_values.append(momentum5)
            momentum20_values.append(momentum20)
            momentum60_values.append(momentum60)
            above60 += 1 if close >= ma60 else 0
            above120 += 1 if close >= ma120 else 0
            count += 1
        regimes[trade_date] = {
            "avg_momentum5": round(mean(momentum5_values), 4) if momentum5_values else 0.0,
            "avg_momentum20": round(mean(momentum20_values), 4) if momentum20_values else 0.0,
            "avg_momentum60": round(mean(momentum60_values), 4) if momentum60_values else 0.0,
            "above60_count": above60,
            "above120_count": above120,
            "index_count": count,
        }
    return regimes


def _dedupe_symbols(symbols: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = str(symbol).strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _load_daily_bars(symbols: list[str], start_date: str, end_date: str) -> dict[str, list[dict[str, Any]]]:
    from data.tushare_client import TushareClient

    client = TushareClient()
    output: dict[str, list[dict[str, Any]]] = {}
    for index, symbol in enumerate(symbols, start=1):
        try:
            bars = client.fetch_daily_bars(symbol, start_date, end_date)
        except Exception as exc:
            logger.warning("daily bars failed for %s: %s", symbol, exc)
            bars = []
        usable = [
            _normalize_bar(symbol, bar)
            for bar in bars
            if _float(bar.get("close"), 0.0) > 0 and str(bar.get("trade_date") or "")
        ]
        if usable:
            output[usable[0]["symbol"]] = sorted(usable, key=lambda item: item["trade_date"])
        if index % 20 == 0:
            logger.info("historical bars loaded %s/%s", index, len(symbols))
    return output


def _run_portfolio_backtest(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    visible_start: str,
    end_date: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    bars_by_date = _bars_by_date(bars_by_symbol)
    all_dates = sorted(date for date in bars_by_date if visible_start <= date <= end_date)
    if len(all_dates) < 5:
        return _empty_result("not_enough_trading_days")

    signal_by_entry_date: dict[str, list[dict[str, Any]]] = {}
    max_signal_gap_days = max(int(profile.get("max_signal_gap_days") or 5), 1)
    post_gap_signal_dates: set[str] = set()
    if bool(profile.get("post_gap_breakout_throttle_enabled")):
        cooldown_days = max(int(profile.get("post_gap_cooldown_trading_days") or 0), 0)
        for date_index in range(1, len(all_dates)):
            previous_dt = datetime.strptime(all_dates[date_index - 1], "%Y%m%d").date()
            current_dt = datetime.strptime(all_dates[date_index], "%Y%m%d").date()
            if (current_dt - previous_dt).days > max_signal_gap_days:
                for cooldown_index in range(date_index, min(date_index + cooldown_days, len(all_dates))):
                    post_gap_signal_dates.add(all_dates[cooldown_index])
    profile["post_gap_signal_dates"] = post_gap_signal_dates
    for index, signal_date in enumerate(all_dates[:-1]):
        entry_date = all_dates[index + 1]
        signal_dt = datetime.strptime(signal_date, "%Y%m%d").date()
        entry_dt = datetime.strptime(entry_date, "%Y%m%d").date()
        if (entry_dt - signal_dt).days > max_signal_gap_days:
            signal_by_entry_date[entry_date] = []
            continue
        signal_by_entry_date[entry_date] = _select_candidates(signal_date, bars_by_symbol, bars_by_date, profile)

    cash = settings.paper_initial_cash
    initial_cash = settings.paper_initial_cash
    positions: list[DailyPosition] = []
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    max_total_exposure = min(max(float(profile["max_total_exposure"]), 0.01), 1.0)
    max_names = max(int(profile["max_names"]), 1)
    per_trade_weight = min(float(profile["per_trade_weight"]), max_total_exposure)
    stop_loss_indices: list[int] = []
    pause_until_index = -1
    risk_peak_equity = initial_cash

    for date_index, trade_date in enumerate(all_dates):
        cash, positions, exit_trades = _process_exits(cash, positions, bars_by_date, trade_date, profile)
        trades.extend(exit_trades)
        current_stop_losses = sum(1 for trade in exit_trades if trade.get("exit_reason") == "stop_loss")
        if bool(profile.get("stop_loss_cooldown_enabled")) and current_stop_losses:
            stop_loss_indices.extend([date_index] * current_stop_losses)
            window = max(int(profile["stop_loss_cooldown_window"]), 1)
            stop_loss_indices = [index for index in stop_loss_indices if date_index - index < window]
            if len(stop_loss_indices) >= int(profile["stop_loss_cooldown_count"]):
                pause_until_index = max(
                    pause_until_index,
                    date_index + max(int(profile["stop_loss_cooldown_days"]), 1),
                )
                stop_loss_indices.clear()
        if bool(profile.get("market_exit_enabled")) and positions:
            regime = _market_regime(trade_date, bars_by_symbol, bars_by_date)
            if float(regime.get("score") or 0.0) < float(profile["regime_exit_score"]) or _is_bear_market_block(
                regime,
                profile,
            ) or _is_index_bear_block(
                trade_date,
                profile,
            ):
                cash, positions, market_exit_trades = _market_exit_positions(
                    cash,
                    positions,
                    bars_by_date,
                    trade_date,
                    "market_risk_off",
                )
                trades.extend(market_exit_trades)

        if bool(profile.get("quality_t_enabled")):
            cash, positions, management_trades = _process_quality_t_management(
                cash,
                positions,
                bars_by_date,
                trade_date,
                profile,
            )
            trades.extend(management_trades)

        equity_before_entries = _equity(cash, positions, bars_by_date, trade_date)
        risk_peak_equity = max(risk_peak_equity, equity_before_entries)
        if bool(profile.get("drawdown_cooldown_enabled")) and risk_peak_equity > 0:
            current_drawdown = 1 - equity_before_entries / risk_peak_equity
            if current_drawdown >= float(profile["drawdown_cooldown_pct"]):
                pause_until_index = max(
                    pause_until_index,
                    date_index + max(int(profile["drawdown_cooldown_days"]), 1),
                )
                risk_peak_equity = equity_before_entries
        if date_index < pause_until_index:
            curve.append({"date": trade_date, "equity": round(equity_before_entries, 4)})
            continue
        current_exposure = _market_value(positions, bars_by_date, trade_date)
        max_exposure_value = equity_before_entries * max_total_exposure
        available_exposure = max(0.0, max_exposure_value - current_exposure)
        for candidate in signal_by_entry_date.get(trade_date, []):
            if any(position.symbol == candidate["symbol"] for position in positions):
                continue
            if available_exposure <= 0:
                break
            bar = bars_by_date.get(trade_date, {}).get(candidate["symbol"])
            if not bar:
                continue
            entry_raw = _float(bar.get("open"), 0.0) or _float(bar.get("close"), 0.0)
            if entry_raw <= 0:
                continue
            if bool(profile.get("entry_guard_enabled")):
                pre_close = _float(bar.get("pre_close"), 0.0)
                if pre_close <= 0:
                    previous = _previous_bar(bars_by_date, candidate["symbol"], trade_date)
                    pre_close = _float(previous.get("close") if previous else 0.0, 0.0)
                if pre_close > 0:
                    open_gap_pct = (entry_raw / pre_close - 1) * 100
                    min_gap = (
                        float(profile["pullback_entry_min_open_gap_pct"])
                        if candidate.get("entry_mode") == "pullback"
                        else float(profile["entry_min_open_gap_pct"])
                    )
                    if open_gap_pct < min_gap or open_gap_pct > float(profile["entry_max_open_gap_pct"]):
                        continue
            entry_price = entry_raw * (1 + settings.paper_slippage_pct)
            quality_tier = str(candidate.get("quality_tier") or "B")
            candidate_weight = (
                _tier_value(profile, quality_tier, "entry_weight")
                if bool(profile.get("quality_t_enabled"))
                else per_trade_weight
            )
            candidate_weight = float(candidate.get("entry_weight") or candidate_weight)
            candidate_weight *= float(candidate.get("exposure_scale") or 1.0)
            target_value = min(equity_before_entries * candidate_weight, available_exposure, cash)
            quantity = _lot_floor(target_value / entry_price)
            amount = quantity * entry_price
            fee = _commission(amount)
            if quantity <= 0 or amount + fee > cash:
                continue
            cash -= amount + fee
            available_exposure -= amount
            is_runner = _is_runner_candidate(candidate, profile)
            if bool(profile.get("quality_t_enabled")):
                stop_loss_pct = _tier_value(profile, quality_tier, "stop_loss_pct")
                take_profit_pct = _tier_value(profile, quality_tier, "take_profit_pct")
                trailing_stop_pct = _tier_value(profile, quality_tier, "trailing_stop_pct")
                trail_activation_pct = 0.12 if quality_tier == "A" else float(profile["trail_activation_pct"])
                max_holding_days = _tier_int(profile, quality_tier, "holding_days")
                max_position_weight = _tier_value(profile, quality_tier, "max_weight")
            else:
                stop_loss_pct = float(profile["stop_loss_pct"])
                take_profit_pct = (
                    float(profile["runner_take_profit_pct"]) if is_runner else float(profile["take_profit_pct"])
                )
                trailing_stop_pct = (
                    float(profile["runner_trailing_stop_pct"]) if is_runner else float(profile["trailing_stop_pct"])
                )
                trail_activation_pct = (
                    float(profile["runner_trail_activation_pct"]) if is_runner else float(profile["trail_activation_pct"])
                )
                max_holding_days = int(profile["runner_holding_days"]) if is_runner else int(profile["holding_days"])
                max_position_weight = candidate_weight
            stop_loss_pct = float(candidate.get("stop_loss_pct") or stop_loss_pct)
            take_profit_pct = float(candidate.get("take_profit_pct") or take_profit_pct)
            trailing_stop_pct = float(candidate.get("trailing_stop_pct") or trailing_stop_pct)
            trail_activation_pct = float(candidate.get("trail_activation_pct") or trail_activation_pct)
            max_holding_days = int(candidate.get("holding_days") or max_holding_days)
            if is_runner and str(candidate.get("entry_mode") or "") == "breakout":
                take_profit_pct = float(profile["runner_take_profit_pct"])
                trailing_stop_pct = float(profile["runner_trailing_stop_pct"])
                trail_activation_pct = float(profile["runner_trail_activation_pct"])
                max_holding_days = int(profile["runner_holding_days"])
            partial_fraction = (
                float(profile["runner_partial_fraction"])
                if is_runner and bool(profile.get("runner_partial_enabled"))
                else 0.0
            )
            partial_take_profit = (
                entry_price * (1 + float(profile["runner_partial_take_profit_pct"]))
                if partial_fraction > 0
                else 0.0
            )
            stop_loss = entry_price * (1 - stop_loss_pct)
            take_profit = entry_price * (1 + take_profit_pct)
            positions.append(
                DailyPosition(
                    symbol=candidate["symbol"],
                    entry_date=trade_date,
                    entry_price=entry_price,
                    quantity=quantity,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    trailing_stop_pct=trailing_stop_pct,
                    trail_activation_pct=trail_activation_pct,
                    highest_price=entry_price,
                    max_holding_days=max_holding_days,
                    runner=is_runner,
                    partial_take_profit=partial_take_profit,
                    partial_fraction=partial_fraction,
                    quality_tier=quality_tier,
                    quality_score=float(candidate.get("quality_score") or 0.0),
                    max_position_weight=max_position_weight,
                    entry_mode=str(candidate.get("entry_mode") or ""),
                )
            )
            trades.append(
                {
                    "symbol": candidate["symbol"],
                    "side": "BUY",
                    "trade_date": trade_date,
                    "price": round(entry_price, 4),
                    "quantity": quantity,
                    "amount": round(amount, 4),
                    "fee": round(fee, 4),
                    "rank_score": candidate["rank_score"],
                    "sentiment_score": candidate["sentiment_score"],
                    "runner": is_runner,
                    "quality_tier": quality_tier,
                    "quality_score": candidate.get("quality_score"),
                    "entry_mode": candidate.get("entry_mode"),
                    "take_profit_pct": round(take_profit_pct, 4),
                    "max_holding_days": max_holding_days,
                    "partial_fraction": round(partial_fraction, 4),
                }
            )

        equity = _equity(cash, positions, bars_by_date, trade_date)
        curve.append({"date": trade_date, "equity": round(equity, 4)})

    if positions and all_dates:
        final_date = all_dates[-1]
        cash, positions, forced_exits = _force_close_positions(cash, positions, bars_by_date, final_date)
        trades.extend(forced_exits)
        curve[-1]["equity"] = round(cash, 4)

    curve = _attach_drawdown(curve)
    closed_trades = _pair_trades(trades)
    metrics = _metrics(curve, closed_trades, initial_cash, bars_by_symbol, all_dates, trades)
    return {
        "metrics": metrics,
        "curve": curve,
        "trades": trades,
        "closed_trades": closed_trades,
        "selection_note": (
            f"Daily approximation of the production intraday strategy with profile={profile['name']}: "
            "use day T close data to rank liquid, "
            "positive-trend candidates; buy at T+1 open; enforce total exposure cap, lot size, slippage, "
            "commission, stamp duty, stop loss, take profit, trailing stop, and fixed holding window."
        ),
    }


def _select_candidates(
    signal_date: str,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    day_bars = list(bars_by_date.get(signal_date, {}).values())
    sentiment = _daily_sentiment(day_bars)
    regime = _market_regime(signal_date, bars_by_symbol, bars_by_date)
    hybrid = bool(profile.get("hybrid_alpha_enabled"))
    if sentiment["state"] != "UPTREND" and not bool(profile["allow_sideways"]):
        return []
    if sentiment["sentiment_score"] < float(profile["min_sentiment_score"]):
        return []
    if sentiment["panic_score"] >= float(profile["panic_threshold"]):
        return []
    if _is_bear_market_block(regime, profile):
        return []
    if _is_index_bear_block(signal_date, profile):
        return []
    narrow_mode = False
    if bool(profile.get("regime_filter_enabled")):
        broad_pass = (
            float(regime.get("score") or 0.0) >= float(profile["regime_min_score"])
            and float(regime.get("breadth20") or 0.0) >= float(profile["min_breadth20"])
            and float(regime.get("breadth60") or 0.0) >= float(profile["min_breadth60"])
            and float(regime.get("momentum20") or 0.0) >= float(profile["min_regime_momentum20"])
            and float(regime.get("momentum60") or 0.0) >= float(profile["min_regime_momentum60"])
        )
        narrow_mode = (
            bool(profile.get("narrow_regime_enabled"))
            and not broad_pass
            and float(regime.get("score") or 0.0) >= float(profile["narrow_regime_min_score"])
            and float(regime.get("momentum20") or 0.0) >= float(profile["min_regime_momentum20"])
            and float(regime.get("momentum60") or 0.0) >= float(profile["min_regime_momentum60"])
            and float(regime.get("momentum5") or 0.0) >= float(profile["narrow_min_momentum5"])
            and float(sentiment.get("sentiment_score") or 0.0) >= float(profile["narrow_min_sentiment_score"])
        )
        if not broad_pass and not narrow_mode:
            return []

    ranked: list[dict[str, Any]] = []
    threshold = float(profile["rank_threshold"])
    exposure_scale = 1.0
    breakout_index_allowed = _is_breakout_index_allowed(signal_date, profile)
    breakout_control = _breakout_regime_control(signal_date, profile)
    if bool(profile.get("regime_filter_enabled")):
        regime_score = float(regime.get("score") or 0.0)
        min_score = float(profile["regime_min_score"])
        full_score = max(float(profile["regime_full_score"]), min_score + 0.1)
        exposure_scale = _clamp((regime_score - min_score) / (full_score - min_score), 0.35, 1.0)
        threshold += max(full_score - regime_score, 0.0) * 0.12
        if narrow_mode:
            threshold += float(profile["narrow_rank_boost"])
            exposure_scale = min(exposure_scale, float(profile["narrow_exposure_scale"]))
    for symbol, bars in bars_by_symbol.items():
        candidate_exposure_scale = exposure_scale
        index = _bar_index(bars, signal_date)
        if index is None or index < int(profile["min_history_bars"]):
            continue
        window = bars[: index + 1]
        bar = window[-1]
        close = _float(bar.get("close"), 0.0)
        pct = _float(bar.get("pct_chg"), 0.0)
        amount_yi = _amount_yi(bar)
        if close <= 0 or amount_yi < float(profile["min_amount_yi"]):
            continue
        if not hybrid and pct < float(profile["min_pct_chg"]):
            continue
        if pct >= float(profile["max_pct_chg"]):
            continue
        closes = [_float(item.get("close"), 0.0) for item in window if _float(item.get("close"), 0.0) > 0]
        if len(closes) < int(profile["min_history_bars"]):
            continue
        ma5 = mean(closes[-5:])
        ma10 = mean(closes[-10:])
        ma20 = mean(closes[-20:]) if len(closes) >= 20 else ma10
        ma60 = mean(closes[-60:]) if len(closes) >= 60 else ma20
        momentum5 = (close / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else 0.0
        momentum10 = (close / closes[-11] - 1) * 100 if len(closes) >= 11 and closes[-11] else 0.0
        momentum20 = (close / closes[-21] - 1) * 100 if len(closes) >= 21 and closes[-21] else 0.0
        if not hybrid and bool(profile["require_ma_stack"]) and (close < ma5 or ma5 < ma10 or ma10 < ma20):
            continue
        if not hybrid and bool(profile.get("require_ma60_stack")) and (len(closes) < 60 or ma20 < ma60 or close < ma20):
            continue
        if not hybrid and (
            momentum5 < float(profile["min_momentum5"])
            or momentum10 < float(profile["min_momentum10"])
            or momentum20 < float(profile["min_momentum20"])
        ):
            continue
        distance_ma20 = (close / ma20 - 1) * 100 if ma20 else 0.0
        if float(profile.get("max_distance_ma20_pct") or 0.0) > 0 and distance_ma20 > float(
            profile["max_distance_ma20_pct"]
        ):
            continue
        recent_high60 = max(closes[-60:]) if len(closes) >= 60 else max(closes)
        close_60d_high_ratio = close / recent_high60 if recent_high60 > 0 else 0.0
        amount_ma20 = mean(_amount_yi(item) for item in window[-20:]) if len(window) >= 20 else amount_yi
        if float(profile.get("min_amount_vs_ma20") or 0.0) > 0 and amount_yi < amount_ma20 * float(
            profile["min_amount_vs_ma20"]
        ):
            continue
        high = _float(bar.get("high"), 0.0)
        if high > 0 and float(profile.get("min_close_high_ratio") or 0.0) > 0:
            if close / high < float(profile["min_close_high_ratio"]):
                continue
        volatility = _volatility([_float(item.get("pct_chg"), 0.0) for item in window[-10:]])
        if volatility > float(profile["max_volatility10"]):
            continue
        quality_score = _historical_quality_score(
            close=close,
            amount_yi=amount_yi,
            volatility=volatility,
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            momentum5=momentum5,
            momentum10=momentum10,
            momentum20=momentum20,
            pct=pct,
        )
        quality_tier = _quality_tier(quality_score, profile)
        if bool(profile.get("quality_t_enabled")) and quality_score < float(profile.get("min_quality_score") or 0.0):
            continue
        trend_score = _clamp(
            52
            + momentum5 * float(profile["momentum5_weight"])
            + momentum10 * float(profile["momentum10_weight"])
            + momentum20 * float(profile["momentum20_weight"])
            + (close / ma10 - 1) * 100,
            0,
            100,
        )
        liquidity_score = _clamp(45 + min(amount_yi, 50) * 1.5, 0, 100)
        pct_score = _clamp(48 + pct * 7 - max(pct - float(profile["pct_sweet_spot"]), 0) * 7, 0, 100)
        risk_penalty = max(volatility - float(profile["volatility_penalty_start"]), 0) * 4
        rank_score = _clamp(
            sentiment["sentiment_score"] * float(profile["sentiment_weight"])
            + trend_score * float(profile["trend_weight"])
            + liquidity_score * float(profile["liquidity_weight"])
            + pct_score * float(profile["pct_weight"])
            - risk_penalty,
            0,
            100,
        )
        if bool(profile.get("quality_t_enabled")):
            rank_score = _clamp(rank_score * 0.78 + quality_score * 0.22, 0, 100)
        if bool(profile.get("pool_switch_enabled")):
            pool_allowed, rank_score, pool_exposure_scale = _pool_switch_adjustment(
                symbol=symbol,
                profile=profile,
                regime=regime,
                sentiment=sentiment,
                rank_score=rank_score,
                exposure_scale=candidate_exposure_scale,
            )
            if not pool_allowed:
                continue
            candidate_exposure_scale = pool_exposure_scale

        entry_mode = "breakout"
        entry_weight = 0.0
        stop_loss_pct = 0.0
        take_profit_pct = 0.0
        trailing_stop_pct = 0.0
        trail_activation_pct = 0.0
        holding_days = 0
        candidate_threshold = threshold
        if hybrid:
            breakout_ok = (
                sentiment["state"] == "UPTREND"
                and breakout_index_allowed
                and (not bool(profile.get("breakout_requires_broad_pass")) or not narrow_mode)
                and float(regime.get("score") or 0.0) >= float(profile["breakout_min_regime_score"])
                and float(regime.get("breadth20") or 0.0) >= float(profile["breakout_min_breadth20"])
                and float(regime.get("momentum20") or 0.0) >= float(profile["breakout_min_regime_momentum20"])
                and float(sentiment.get("sentiment_score") or 0.0) >= float(profile["breakout_min_sentiment_score"])
                and pct >= float(profile["min_pct_chg"])
                and close >= ma5 >= ma10 >= ma20
                and momentum5 >= float(profile["min_momentum5"])
                and momentum10 >= float(profile["min_momentum10"])
                and (
                    float(profile.get("breakout_min_close_60d_high_ratio") or 0.0) <= 0
                    or close_60d_high_ratio >= float(profile["breakout_min_close_60d_high_ratio"])
                )
                and (
                    float(profile.get("breakout_max_distance_ma20_pct") or 0.0) <= 0
                    or distance_ma20 <= float(profile["breakout_max_distance_ma20_pct"])
                )
            )
            pullback_ok = (
                bool(profile.get("pullback_enabled"))
                and float(profile.get("pullback_min_pct")) <= pct <= float(profile.get("pullback_max_pct"))
                and momentum20 >= float(profile["pullback_min_momentum20"])
                and float(profile["pullback_ma20_floor_pct"]) <= distance_ma20 <= float(
                    profile["pullback_ma20_ceiling_pct"]
                )
                and close >= ma20 * (1 + float(profile["pullback_ma20_floor_pct"]) / 100)
                and ma10 >= ma20 * 0.985
            )
            breakout_rank = rank_score
            pullback_rank = _clamp(
                quality_score * 0.24
                + sentiment["sentiment_score"] * 0.18
                + liquidity_score * 0.14
                + _clamp(66 + momentum20 * 1.2 + max(-pct, 0) * 3.0 - abs(distance_ma20) * 2.2, 0, 100) * 0.36
                + _clamp(100 - volatility * 8.0, 0, 100) * 0.08,
                0,
                100,
            )
            if pullback_ok and pullback_rank >= float(profile["pullback_rank_threshold"]) and (
                not breakout_ok or pullback_rank >= breakout_rank + 1.5
            ):
                entry_mode = "pullback"
                rank_score = pullback_rank
                candidate_threshold = float(profile["pullback_rank_threshold"])
                entry_weight = float(profile["pullback_entry_weight"])
                stop_loss_pct = float(profile["pullback_stop_loss_pct"])
                take_profit_pct = float(profile["pullback_take_profit_pct"])
                trailing_stop_pct = float(profile["pullback_trailing_stop_pct"])
                trail_activation_pct = max(take_profit_pct * 0.55, 0.04)
                holding_days = int(profile["pullback_holding_days"])
            elif breakout_ok:
                entry_mode = "breakout"
                rank_score = breakout_rank
                entry_weight = float(profile["breakout_entry_weight"] or profile["per_trade_weight"]) * float(
                    breakout_control.get("weight_scale") or 1.0
                )
                stop_loss_pct = float(profile["stop_loss_pct"])
                take_profit_pct = float(profile["take_profit_pct"])
                trailing_stop_pct = float(profile["trailing_stop_pct"])
                trail_activation_pct = float(profile["trail_activation_pct"])
                holding_days = int(profile["holding_days"])
            else:
                continue

        if rank_score < candidate_threshold:
            continue
        ranked.append(
            {
                "symbol": symbol,
                "rank_score": round(rank_score, 4),
                "sentiment_score": sentiment["sentiment_score"],
                "pct_chg": round(pct, 4),
                "amount_yi": round(amount_yi, 4),
                "momentum5": round(momentum5, 4),
                "momentum10": round(momentum10, 4),
                "momentum20": round(momentum20, 4),
                "close_60d_high_ratio": round(close_60d_high_ratio, 4),
                "volatility10": round(volatility, 4),
                "quality_score": round(quality_score, 4),
                "quality_tier": quality_tier,
                "market_regime_score": regime.get("score"),
                "market_breadth20": regime.get("breadth20"),
                "exposure_scale": round(candidate_exposure_scale, 4),
                "entry_mode": entry_mode,
                "entry_weight": round(entry_weight, 4) if entry_weight else None,
                "stop_loss_pct": round(stop_loss_pct, 4) if stop_loss_pct else None,
                "take_profit_pct": round(take_profit_pct, 4) if take_profit_pct else None,
                "trailing_stop_pct": round(trailing_stop_pct, 4) if trailing_stop_pct else None,
                "trail_activation_pct": round(trail_activation_pct, 4) if trail_activation_pct else None,
                "holding_days": holding_days or None,
            }
        )
    ranked.sort(key=lambda item: item["rank_score"], reverse=True)
    if hybrid:
        return _apply_hybrid_candidate_caps(ranked, profile, breakout_control)
    return ranked[: int(profile["max_names"])]


def _apply_hybrid_candidate_caps(
    ranked: list[dict[str, Any]],
    profile: dict[str, Any],
    breakout_control: dict[str, Any],
) -> list[dict[str, Any]]:
    max_names = int(profile["max_names"])
    max_breakout_names = max(int(breakout_control.get("max_breakout_names") or max_names), 0)
    output: list[dict[str, Any]] = []
    breakout_count = 0
    for candidate in ranked:
        if candidate.get("entry_mode") == "breakout":
            if breakout_count >= max_breakout_names:
                continue
            breakout_count += 1
        output.append(candidate)
        if len(output) >= max_names:
            break
    return output


def _daily_sentiment(day_bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not day_bars:
        return {"state": "NO_DATA", "sentiment_score": 0.0, "panic_score": 100.0}
    pct_values = [_float(item.get("pct_chg"), 0.0) for item in day_bars]
    up_ratio = sum(1 for value in pct_values if value > 0) / len(pct_values)
    down_ratio = sum(1 for value in pct_values if value < 0) / len(pct_values)
    strong_up_ratio = sum(1 for value in pct_values if value >= 3) / len(pct_values)
    strong_down_ratio = sum(1 for value in pct_values if value <= -3) / len(pct_values)
    avg_pct = mean(pct_values)
    sentiment_score = _clamp(
        50 + (up_ratio - 0.5) * 70 + avg_pct * 5 + strong_up_ratio * 18 - strong_down_ratio * 22,
        0,
        100,
    )
    panic_score = _clamp(25 + max(down_ratio - 0.5, 0) * 90 + strong_down_ratio * 80 - strong_up_ratio * 20, 0, 100)
    if up_ratio >= 0.56 and avg_pct > 0.15 and panic_score < settings.sentiment_panic_threshold:
        state = "UPTREND"
    elif up_ratio <= 0.42 and avg_pct < -0.25:
        state = "DOWNTREND"
    else:
        state = "SIDEWAYS"
    return {
        "state": state,
        "sentiment_score": round(sentiment_score, 4),
        "panic_score": round(panic_score, 4),
        "up_ratio": round(up_ratio, 4),
        "down_ratio": round(down_ratio, 4),
        "avg_pct": round(avg_pct, 4),
    }


def _market_regime(
    signal_date: str,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    cache_key = (id(bars_by_symbol), signal_date)
    cached = _REGIME_CACHE.get(cache_key)
    if cached is not None:
        return cached
    dates = sorted(date for date in bars_by_date if date <= signal_date)
    if not dates:
        output = {
            "state": "NO_DATA",
            "score": 0.0,
            "breadth20": 0.0,
            "breadth60": 0.0,
            "momentum5": 0.0,
            "momentum20": 0.0,
            "momentum60": 0.0,
        }
        _REGIME_CACHE[cache_key] = output
        return output

    history = dates[-60:]
    avg_returns: list[float] = []
    for date in history:
        pct_values = [_float(item.get("pct_chg"), 0.0) for item in bars_by_date.get(date, {}).values()]
        if pct_values:
            avg_returns.append(mean(pct_values))

    momentum5 = sum(avg_returns[-5:]) if len(avg_returns) >= 5 else sum(avg_returns)
    momentum20 = sum(avg_returns[-20:]) if len(avg_returns) >= 20 else sum(avg_returns)
    momentum60 = sum(avg_returns[-60:]) if len(avg_returns) >= 60 else sum(avg_returns)

    breadth20_hits = 0
    breadth20_count = 0
    breadth60_hits = 0
    breadth60_count = 0
    for bars in bars_by_symbol.values():
        index = _bar_index(bars, signal_date)
        if index is None:
            continue
        window = bars[: index + 1]
        closes = [_float(item.get("close"), 0.0) for item in window if _float(item.get("close"), 0.0) > 0]
        if not closes:
            continue
        close = closes[-1]
        if len(closes) >= 20:
            breadth20_count += 1
            if close >= mean(closes[-20:]):
                breadth20_hits += 1
        if len(closes) >= 60:
            breadth60_count += 1
            if close >= mean(closes[-60:]):
                breadth60_hits += 1

    breadth20 = breadth20_hits / breadth20_count if breadth20_count else 0.0
    breadth60 = breadth60_hits / breadth60_count if breadth60_count else breadth20
    sentiment = _daily_sentiment(list(bars_by_date.get(signal_date, {}).values()))
    panic = float(sentiment.get("panic_score") or 0.0)
    sentiment_score = float(sentiment.get("sentiment_score") or 0.0)

    score = _clamp(
        50
        + momentum5 * 1.6
        + momentum20 * 0.9
        + momentum60 * 0.28
        + (breadth20 - 0.5) * 55
        + (breadth60 - 0.5) * 38
        + (sentiment_score - 50) * 0.24
        - max(panic - 55, 0) * 0.45,
        0,
        100,
    )
    if score >= 68 and breadth20 >= 0.58 and momentum20 > 1.5:
        state = "BULL"
    elif score >= 58 and breadth20 >= 0.50 and momentum20 > 0:
        state = "TRADEABLE"
    elif score <= 42 or momentum20 < -3.0:
        state = "BEAR"
    else:
        state = "SIDEWAYS"
    output = {
        "state": state,
        "score": round(score, 4),
        "breadth20": round(breadth20, 4),
        "breadth60": round(breadth60, 4),
        "momentum5": round(momentum5, 4),
        "momentum20": round(momentum20, 4),
        "momentum60": round(momentum60, 4),
        "sentiment_score": sentiment.get("sentiment_score"),
        "panic_score": sentiment.get("panic_score"),
    }
    _REGIME_CACHE[cache_key] = output
    return output


def _pool_switch_adjustment(
    symbol: str,
    profile: dict[str, Any],
    regime: dict[str, Any],
    sentiment: dict[str, Any],
    rank_score: float,
    exposure_scale: float,
) -> tuple[bool, float, float]:
    tags_by_symbol = profile.get("symbol_pool_tags") or {}
    raw_tags = tags_by_symbol.get(symbol) or tags_by_symbol.get(str(symbol).upper()) or []
    tags = {str(tag).lower() for tag in raw_tags}
    if not tags:
        return True, rank_score, exposure_scale

    regime_score = float(regime.get("score") or 0.0)
    momentum5 = float(regime.get("momentum5") or 0.0)
    momentum20 = float(regime.get("momentum20") or 0.0)
    breadth20 = float(regime.get("breadth20") or 0.0)
    sentiment_score = float(sentiment.get("sentiment_score") or 0.0)
    panic_score = float(sentiment.get("panic_score") or 0.0)

    allowed = False
    boost = 0.0
    exposure_multiplier = 1.0

    if "core" in tags:
        allowed = True
        if 48 <= regime_score <= 68:
            boost += 1.0
        if panic_score >= 68:
            exposure_multiplier *= 0.75

    if "large" in tags:
        large_ok = regime_score >= 52 or momentum20 >= -1.2 or sentiment_score >= 57
        if large_ok:
            allowed = True
            boost += 1.2 if momentum20 >= -0.5 else -0.4
            if regime_score < 56:
                exposure_multiplier *= 0.85

    if "active" in tags:
        active_ok = (
            panic_score < 70
            and sentiment_score >= 54
            and breadth20 >= 0.40
            and (
                regime_score >= 60
                or (regime_score >= 56 and momentum5 >= 0.8)
                or (momentum20 >= 1.2 and sentiment_score >= 58)
            )
        )
        if active_ok:
            allowed = True
            boost += 2.2 if regime_score >= 64 else 0.6
            exposure_multiplier *= 1.08 if regime_score >= 66 else 0.92
        elif tags == {"active"}:
            return False, rank_score, exposure_scale
        else:
            boost -= 1.5
            exposure_multiplier *= 0.85

    adjusted_rank = _clamp(rank_score + boost, 0, 100)
    adjusted_exposure = _clamp(exposure_scale * exposure_multiplier, 0.2, 1.15)
    return allowed, adjusted_rank, adjusted_exposure


def _is_bear_market_block(regime: dict[str, Any], profile: dict[str, Any]) -> bool:
    if not bool(profile.get("bear_market_block_enabled")):
        return False
    return (
        float(regime.get("score") or 0.0) <= float(profile["bear_market_score_max"])
        and float(regime.get("momentum60") or 0.0) <= float(profile["bear_market_momentum60_max"])
        and float(regime.get("breadth60") or 0.0) <= float(profile["bear_market_breadth60_max"])
    )


def _index_regime_at(trade_date: str, profile: dict[str, Any]) -> dict[str, Any]:
    dates = profile.get("index_regime_dates") or []
    regimes = profile.get("index_regime_by_date") or {}
    if not dates:
        return {}
    index = bisect_right(dates, trade_date) - 1
    if index < 0:
        return {}
    return regimes.get(dates[index]) or {}


def _is_index_bear_block(trade_date: str, profile: dict[str, Any]) -> bool:
    if not bool(profile.get("index_regime_enabled")):
        return False
    regime = _index_regime_at(trade_date, profile)
    if int(regime.get("index_count") or 0) < 2:
        return False
    return (
        float(regime.get("avg_momentum60") or 0.0) <= float(profile["index_bear_momentum60_max"])
        and int(regime.get("above60_count") or 0) <= int(profile["index_bear_above60_max"])
    )


def _is_breakout_index_allowed(trade_date: str, profile: dict[str, Any]) -> bool:
    if not bool(profile.get("breakout_index_gate_enabled")):
        return True
    regime = _index_regime_at(trade_date, profile)
    if int(regime.get("index_count") or 0) < 2:
        return True

    above120 = int(regime.get("above120_count") or 0)
    above60 = int(regime.get("above60_count") or 0)
    momentum20 = float(regime.get("avg_momentum20") or 0.0)
    momentum60 = float(regime.get("avg_momentum60") or 0.0)
    structural_pass = (
        above120 >= int(profile["breakout_index_min_above120"])
        and momentum20 >= float(profile["breakout_index_min_momentum20"])
        and momentum60 >= float(profile["breakout_index_min_momentum60"])
    )
    early_bull_pass = (
        above60 >= int(profile["breakout_index_relaxed_above60"])
        and momentum20 >= float(profile["breakout_index_relaxed_momentum20"])
    )
    return structural_pass or early_bull_pass


def _breakout_regime_control(trade_date: str, profile: dict[str, Any]) -> dict[str, Any]:
    max_names = int(profile["max_names"])
    post_gap_signal_dates = profile.get("post_gap_signal_dates") or set()
    if bool(profile.get("post_gap_breakout_throttle_enabled")) and trade_date in post_gap_signal_dates:
        return {
            "weight_scale": float(profile["post_gap_breakout_weight_scale"]),
            "max_breakout_names": min(max(int(profile["post_gap_breakout_max_names"]), 0), max_names),
            "stage": "post_gap",
        }
    if not bool(profile.get("breakout_index_gate_enabled")):
        return {"weight_scale": 1.0, "max_breakout_names": max_names, "stage": "ungated"}
    regime = _index_regime_at(trade_date, profile)
    if int(regime.get("index_count") or 0) < 2:
        return {"weight_scale": 1.0, "max_breakout_names": max_names, "stage": "unknown"}

    above120 = int(regime.get("above120_count") or 0)
    momentum5 = float(regime.get("avg_momentum5") or 0.0)
    momentum20 = float(regime.get("avg_momentum20") or 0.0)
    momentum60 = float(regime.get("avg_momentum60") or 0.0)
    momentum20_confirmed = momentum20 >= float(profile["breakout_confirmed_momentum20"])
    momentum60_confirmed = (
        momentum60 >= float(profile["breakout_confirmed_momentum60"])
        and above120 >= int(profile["breakout_confirmed_min_above120_for_momentum60"])
    )
    confirmed = momentum20_confirmed or momentum60_confirmed
    if confirmed:
        stale = (
            bool(profile.get("breakout_stale_enabled"))
            and momentum60_confirmed
            and not momentum20_confirmed
            and momentum5 <= float(profile["breakout_stale_momentum5_max"])
            and momentum20 <= float(profile["breakout_stale_momentum20_max"])
        )
        if stale:
            return {
                "weight_scale": float(profile["breakout_stale_weight_scale"]),
                "max_breakout_names": min(max(int(profile["breakout_stale_max_names"]), 0), max_names),
                "stage": "stale",
            }
        overheat = (
            bool(profile.get("breakout_overheat_enabled"))
            and momentum5 >= float(profile["breakout_overheat_momentum5_min"])
            and momentum20 >= float(profile["breakout_overheat_momentum20_min"])
            and momentum60 <= float(profile["breakout_overheat_momentum60_max"])
        )
        if overheat:
            return {
                "weight_scale": float(profile["breakout_overheat_weight_scale"]),
                "max_breakout_names": min(max(int(profile["breakout_overheat_max_names"]), 0), max_names),
                "stage": "overheat",
            }
        return {
            "weight_scale": float(profile["breakout_strong_weight_scale"]),
            "max_breakout_names": max_names,
            "stage": "confirmed",
        }
    return {
        "weight_scale": float(profile["breakout_transition_weight_scale"]),
        "max_breakout_names": min(max(int(profile["breakout_transition_max_names"]), 0), max_names),
        "stage": "transition",
    }


def _historical_quality_score(
    close: float,
    amount_yi: float,
    volatility: float,
    ma5: float,
    ma10: float,
    ma20: float,
    momentum5: float,
    momentum10: float,
    momentum20: float,
    pct: float,
) -> float:
    liquidity = _clamp(45 + min(amount_yi, 35) * 1.45, 0, 100)
    stability = _clamp(100 - volatility * 8.5, 0, 100)
    trend_stack = 60.0
    if close >= ma5 >= ma10 >= ma20:
        trend_stack = 92.0
    elif close >= ma10 >= ma20:
        trend_stack = 80.0
    elif close >= ma20:
        trend_stack = 68.0
    momentum = _clamp(55 + momentum5 * 2.5 + momentum10 * 1.5 + momentum20 * 0.7 - max(pct - 7, 0) * 4, 0, 100)
    price_sanity = 86.0 if 4 <= close <= 220 else 70.0
    return _clamp(
        liquidity * 0.28
        + stability * 0.24
        + trend_stack * 0.22
        + momentum * 0.18
        + price_sanity * 0.08,
        0,
        100,
    )


def _quality_tier(quality_score: float, profile: dict[str, Any]) -> str:
    if quality_score >= float(profile["tier_a_min_quality"]):
        return "A"
    if quality_score >= float(profile["tier_b_min_quality"]):
        return "B"
    return "C"


def _tier_value(profile: dict[str, Any], tier: str, suffix: str) -> float:
    key = f"tier_{str(tier).lower()}_{suffix}"
    return float(profile.get(key, profile.get(f"tier_b_{suffix}", 0.0)))


def _tier_int(profile: dict[str, Any], tier: str, suffix: str) -> int:
    return max(int(_tier_value(profile, tier, suffix)), 1)


def _is_runner_candidate(candidate: dict[str, Any], profile: dict[str, Any]) -> bool:
    if not bool(profile.get("runner_enabled")):
        return False
    return (
        float(candidate.get("rank_score") or 0.0) >= float(profile["runner_rank_threshold"])
        and float(candidate.get("momentum10") or 0.0) >= float(profile["runner_momentum10_min"])
        and float(candidate.get("momentum20") or 0.0) >= float(profile["runner_momentum20_min"])
    )


def _process_exits(
    cash: float,
    positions: list[DailyPosition],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
    profile: dict[str, Any],
) -> tuple[float, list[DailyPosition], list[dict[str, Any]]]:
    remaining: list[DailyPosition] = []
    trades: list[dict[str, Any]] = []
    for position in positions:
        bar = bars_by_date.get(trade_date, {}).get(position.symbol)
        if not bar:
            remaining.append(position)
            continue
        position.held_bars += 1
        low = _float(bar.get("low"), 0.0)
        high = _float(bar.get("high"), 0.0)
        close = _float(bar.get("close"), 0.0)
        if high > 0:
            position.highest_price = max(position.highest_price, high)
        exit_price = 0.0
        reason = ""
        if low > 0 and low <= position.stop_loss:
            exit_price = position.stop_loss * (1 - settings.paper_slippage_pct)
            reason = "stop_loss"
        elif (
            position.runner
            and not position.partial_taken
            and position.partial_fraction > 0
            and high > 0
            and position.partial_take_profit > 0
            and high >= position.partial_take_profit
        ):
            partial_quantity = _lot_floor(position.quantity * position.partial_fraction)
            if partial_quantity > 0 and partial_quantity < position.quantity:
                partial_price = position.partial_take_profit * (1 - settings.paper_slippage_pct)
                cash, partial_trade = _sell_position(
                    cash,
                    position,
                    trade_date,
                    partial_price,
                    "partial_take_profit",
                    quantity=partial_quantity,
                )
                trades.append(partial_trade)
                position.quantity -= partial_quantity
                position.partial_taken = True
                remaining.append(position)
                continue
        elif high > 0 and high >= position.take_profit:
            exit_price = position.take_profit * (1 - settings.paper_slippage_pct)
            reason = "take_profit"
        elif (
            low > 0
            and position.trailing_stop_pct > 0
            and position.highest_price >= position.entry_price * (1 + position.trail_activation_pct)
            and low <= position.highest_price * (1 - position.trailing_stop_pct)
        ):
            exit_price = position.highest_price * (1 - position.trailing_stop_pct) * (1 - settings.paper_slippage_pct)
            reason = "trailing_stop"
        elif position.held_bars >= position.max_holding_days:
            exit_price = close * (1 - settings.paper_slippage_pct)
            reason = "time_exit"
        if exit_price <= 0:
            remaining.append(position)
            continue
        cash, trade = _sell_position(cash, position, trade_date, exit_price, reason)
        trades.append(trade)
    return cash, remaining, trades


def _process_quality_t_management(
    cash: float,
    positions: list[DailyPosition],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
    profile: dict[str, Any],
) -> tuple[float, list[DailyPosition], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    equity = _equity(cash, positions, bars_by_date, trade_date)
    for position in positions:
        bar = bars_by_date.get(trade_date, {}).get(position.symbol)
        previous = _previous_bar(bars_by_date, position.symbol, trade_date)
        if not bar or not previous or position.quality_tier != "A":
            continue
        low = _float(bar.get("low"), 0.0)
        close = _float(bar.get("close"), 0.0)
        prev_close = _float(previous.get("close"), 0.0)
        if low <= 0 or close <= 0 or prev_close <= 0:
            continue

        if bool(profile.get("t_trade_enabled")) and position.held_bars >= 1:
            t_quantity = _lot_floor(position.quantity * float(profile["t_trade_fraction"]))
            trigger_price = prev_close * (1 - float(profile["t_trade_dip_pct"]))
            rebound_price = trigger_price * (1 + float(profile["t_trade_rebound_pct"]))
            if t_quantity > 0 and low <= trigger_price and close >= rebound_price:
                buy_price = trigger_price * (1 + settings.paper_slippage_pct)
                sell_price = rebound_price * (1 - settings.paper_slippage_pct)
                buy_amount = t_quantity * buy_price
                buy_fee = _commission(buy_amount)
                if cash >= buy_amount + buy_fee:
                    sell_amount = t_quantity * sell_price
                    sell_fee = _commission(sell_amount)
                    sell_tax = sell_amount * settings.paper_stamp_duty_rate
                    t_pnl = sell_amount - sell_fee - sell_tax - buy_amount - buy_fee
                    cash += t_pnl
                    trades.append(
                        {
                            "symbol": position.symbol,
                            "side": "BUY_T",
                            "trade_date": trade_date,
                            "price": round(buy_price, 4),
                            "quantity": t_quantity,
                            "amount": round(buy_amount, 4),
                            "fee": round(buy_fee, 4),
                            "quality_tier": position.quality_tier,
                            "reason": "base_position_t_buy",
                        }
                    )
                    trades.append(
                        {
                            "symbol": position.symbol,
                            "side": "SELL_T",
                            "trade_date": trade_date,
                            "price": round(sell_price, 4),
                            "quantity": t_quantity,
                            "amount": round(sell_amount, 4),
                            "fee": round(sell_fee, 4),
                            "tax": round(sell_tax, 4),
                            "realized_pnl": round(t_pnl, 4),
                            "quality_tier": position.quality_tier,
                            "reason": "sell_old_lot_after_t_rebound",
                        }
                    )

        if bool(profile.get("add_on_enabled")) and position.add_count < int(profile["max_add_count"]):
            current_value = position.quantity * close
            max_value = equity * max(position.max_position_weight, 0.01)
            if current_value < max_value and close >= position.entry_price * (1 + float(profile["add_on_trigger_pct"])):
                add_value = min(
                    max_value - current_value,
                    current_value * float(profile["add_on_fraction"]),
                    cash,
                )
                add_price = close * (1 + settings.paper_slippage_pct)
                add_quantity = _lot_floor(add_value / add_price)
                add_amount = add_quantity * add_price
                add_fee = _commission(add_amount)
                if add_quantity > 0 and cash >= add_amount + add_fee:
                    old_cost = position.entry_price * position.quantity
                    new_quantity = position.quantity + add_quantity
                    position.entry_price = (old_cost + add_amount + add_fee) / new_quantity
                    position.quantity = new_quantity
                    position.add_count += 1
                    position.stop_loss = max(
                        position.stop_loss,
                        position.entry_price * (1 - _tier_value(profile, position.quality_tier, "stop_loss_pct")),
                    )
                    position.take_profit = position.entry_price * (
                        1 + _tier_value(profile, position.quality_tier, "take_profit_pct")
                    )
                    cash -= add_amount + add_fee
                    trades.append(
                        {
                            "symbol": position.symbol,
                            "side": "BUY",
                            "trade_date": trade_date,
                            "price": round(add_price, 4),
                            "quantity": add_quantity,
                            "amount": round(add_amount, 4),
                            "fee": round(add_fee, 4),
                            "quality_tier": position.quality_tier,
                            "quality_score": round(position.quality_score, 4),
                            "reason": "quality_t_add_on",
                        }
                    )
    return cash, positions, trades


def _market_exit_positions(
    cash: float,
    positions: list[DailyPosition],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
    reason: str,
) -> tuple[float, list[DailyPosition], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    remaining: list[DailyPosition] = []
    for position in positions:
        bar = bars_by_date.get(trade_date, {}).get(position.symbol)
        if not bar:
            remaining.append(position)
            continue
        close = _float(bar.get("close"), 0.0)
        if close <= 0:
            remaining.append(position)
            continue
        cash, trade = _sell_position(
            cash,
            position,
            trade_date,
            close * (1 - settings.paper_slippage_pct),
            reason,
        )
        trades.append(trade)
    return cash, remaining, trades


def _force_close_positions(
    cash: float,
    positions: list[DailyPosition],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
) -> tuple[float, list[DailyPosition], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    for position in positions:
        bar = bars_by_date.get(trade_date, {}).get(position.symbol)
        close = _float(bar.get("close") if bar else 0.0, position.entry_price)
        cash, trade = _sell_position(
            cash,
            position,
            trade_date,
            close * (1 - settings.paper_slippage_pct),
            "force_close",
        )
        trades.append(trade)
    return cash, [], trades


def _sell_position(
    cash: float,
    position: DailyPosition,
    trade_date: str,
    exit_price: float,
    reason: str,
    quantity: int | None = None,
) -> tuple[float, dict[str, Any]]:
    sell_quantity = int(quantity or position.quantity)
    amount = sell_quantity * exit_price
    fee = _commission(amount)
    tax = amount * settings.paper_stamp_duty_rate
    pnl = amount - fee - tax - sell_quantity * position.entry_price
    cash += amount - fee - tax
    return cash, {
        "symbol": position.symbol,
        "side": "SELL",
        "trade_date": trade_date,
        "price": round(exit_price, 4),
        "quantity": sell_quantity,
        "amount": round(amount, 4),
        "fee": round(fee, 4),
        "tax": round(tax, 4),
        "realized_pnl": round(pnl, 4),
        "return_pct": round((exit_price / position.entry_price - 1) * 100, 4),
        "exit_reason": reason,
        "entry_date": position.entry_date,
        "entry_price": round(position.entry_price, 4),
        "runner": position.runner,
        "quality_tier": position.quality_tier,
        "quality_score": round(position.quality_score, 4),
        "entry_mode": position.entry_mode,
    }


def _metrics(
    curve: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
    initial_cash: float,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    trading_dates: list[str],
    trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not curve:
        return {"trade_count": 0}
    final_equity = curve[-1]["equity"]
    total_return_pct = (final_equity / initial_cash - 1) * 100
    max_dd, dd_start, dd_trough = _max_drawdown(curve)
    returns = [item["return_pct"] for item in closed_trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    daily_returns = [
        curve[index]["equity"] / curve[index - 1]["equity"] - 1
        for index in range(1, len(curve))
        if curve[index - 1]["equity"] > 0
    ]
    annual_return = (final_equity / initial_cash) ** (252 / max(len(curve), 1)) - 1 if initial_cash else 0.0
    volatility = _volatility([value * 100 for value in daily_returns]) / 100 * sqrt(252)
    sharpe = annual_return / volatility if volatility > 0 else 0.0
    entry_mode_counts: dict[str, int] = {}
    entry_mode_pnl: dict[str, float] = {}
    exit_reason_counts: dict[str, int] = {}
    for item in closed_trades:
        mode = str(item.get("entry_mode") or "unknown")
        reason = str(item.get("exit_reason") or "unknown")
        entry_mode_counts[mode] = entry_mode_counts.get(mode, 0) + 1
        entry_mode_pnl[mode] = entry_mode_pnl.get(mode, 0.0) + _float(item.get("realized_pnl"), 0.0)
        exit_reason_counts[reason] = exit_reason_counts.get(reason, 0) + 1
    return {
        "trade_count": len(closed_trades),
        "buy_order_count": sum(1 for item in closed_trades),
        "t_trade_count": sum(1 for item in (trades or []) if item.get("side") == "SELL_T"),
        "add_on_count": sum(1 for item in (trades or []) if item.get("reason") == "quality_t_add_on"),
        "tier_a_trade_count": sum(1 for item in closed_trades if item.get("quality_tier") == "A"),
        "trading_days": len(trading_dates),
        "initial_cash": round(initial_cash, 4),
        "final_equity": round(final_equity, 4),
        "total_return_pct": round(total_return_pct, 4),
        "annualized_return_pct": round(annual_return * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "max_drawdown_start": dd_start,
        "max_drawdown_trough": dd_trough,
        "win_rate": round(len(wins) / len(returns), 4) if returns else 0.0,
        "avg_trade_return_pct": round(mean(returns), 4) if returns else 0.0,
        "median_trade_return_pct": round(median(returns), 4) if returns else 0.0,
        "best_trade_return_pct": round(max(returns), 4) if returns else 0.0,
        "worst_trade_return_pct": round(min(returns), 4) if returns else 0.0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else 0.0,
        "entry_mode_counts": dict(sorted(entry_mode_counts.items())),
        "entry_mode_pnl": {key: round(value, 4) for key, value in sorted(entry_mode_pnl.items())},
        "exit_reason_counts": dict(sorted(exit_reason_counts.items())),
        "stop_rate": round(
            sum(1 for item in closed_trades if item.get("exit_reason") == "stop_loss") / len(closed_trades),
            4,
        )
        if closed_trades
        else 0.0,
        "sharpe_proxy": round(sharpe, 4),
        "symbol_count_with_data": len(bars_by_symbol),
    }


def _pair_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buys: dict[str, list[dict[str, Any]]] = {}
    paired: list[dict[str, Any]] = []
    for trade in trades:
        symbol = str(trade.get("symbol") or "")
        if trade.get("side") == "BUY":
            buys.setdefault(symbol, []).append({**trade, "remaining_quantity": int(trade.get("quantity") or 0)})
        elif trade.get("side") == "SELL":
            sell_quantity = int(trade.get("quantity") or 0)
            while sell_quantity > 0 and buys.get(symbol):
                buy = buys[symbol][0]
                matched_quantity = min(int(buy.get("remaining_quantity") or 0), sell_quantity)
                if matched_quantity <= 0:
                    buys[symbol].pop(0)
                    continue
                entry_price = _float(buy.get("price"), _float(trade.get("entry_price"), 0.0))
                exit_price = _float(trade.get("price"), 0.0)
                return_pct = (exit_price / entry_price - 1) * 100 if entry_price else 0.0
                paired.append(
                    {
                        "symbol": symbol,
                        "entry_date": buy.get("trade_date") or trade.get("entry_date"),
                        "exit_date": trade.get("trade_date"),
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(exit_price, 4),
                        "quantity": matched_quantity,
                        "return_pct": round(return_pct, 4),
                        "realized_pnl": round(_float(trade.get("realized_pnl"), 0.0) * matched_quantity / max(int(trade.get("quantity") or matched_quantity), 1), 4),
                        "exit_reason": trade.get("exit_reason"),
                        "runner": trade.get("runner"),
                        "quality_tier": buy.get("quality_tier") or trade.get("quality_tier"),
                        "quality_score": buy.get("quality_score") or trade.get("quality_score"),
                        "entry_mode": buy.get("entry_mode") or trade.get("entry_mode"),
                    }
                )
                buy["remaining_quantity"] = int(buy.get("remaining_quantity") or 0) - matched_quantity
                sell_quantity -= matched_quantity
                if int(buy.get("remaining_quantity") or 0) <= 0:
                    buys[symbol].pop(0)
    return paired


def _attach_drawdown(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak = curve[0]["equity"] if curve else 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        drawdown = point["equity"] / peak - 1 if peak else 0.0
        point["drawdown_pct"] = round(drawdown * 100, 4)
    return curve


def _max_drawdown(curve: list[dict[str, Any]]) -> tuple[float, str, str]:
    peak = curve[0]["equity"] if curve else 1.0
    peak_date = curve[0]["date"] if curve else ""
    max_dd = 0.0
    dd_start = peak_date
    dd_trough = peak_date
    for point in curve:
        if point["equity"] > peak:
            peak = point["equity"]
            peak_date = point["date"]
        drawdown = (peak - point["equity"]) / peak if peak else 0.0
        if drawdown > max_dd:
            max_dd = drawdown
            dd_start = peak_date
            dd_trough = point["date"]
    return max_dd, dd_start, dd_trough


def _write_curve_svg(result: dict[str, Any]) -> Path:
    curve = result.get("curve") or []
    path = settings.storage_dir / f"historical_backtest_{result['run_id']}.svg"
    width = 1200
    height = 720
    margin = 70
    gap = 60
    panel_h = (height - margin * 2 - gap) / 2
    equity_values = [point["equity"] for point in curve] or [1.0]
    dd_values = [point["drawdown_pct"] for point in curve] or [0.0]
    eq_min, eq_max = min(equity_values), max(equity_values)
    if eq_min == eq_max:
        eq_min *= 0.99
        eq_max *= 1.01
    dd_min, dd_max = min(dd_values), 0.0
    if dd_min == dd_max:
        dd_min = -1.0

    eq_points = _polyline_points(curve, "equity", width, margin, margin, panel_h, eq_min, eq_max)
    dd_points = _polyline_points(curve, "drawdown_pct", width, margin, margin + panel_h + gap, panel_h, dd_min, dd_max)
    metrics = result.get("metrics") or {}
    title = f"Historical Daily Backtest {result.get('start_date')} - {result.get('end_date')}"
    subtitle = (
        f"Return {metrics.get('total_return_pct', 0)}% | Max DD {metrics.get('max_drawdown_pct', 0)}% | "
        f"Trades {metrics.get('trade_count', 0)} | Win {round(float(metrics.get('win_rate', 0))*100, 2)}%"
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{margin}" y="34" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#172033">{_escape(title)}</text>
  <text x="{margin}" y="58" font-family="Arial, sans-serif" font-size="14" fill="#526070">{_escape(subtitle)}</text>
  {_axis_svg(margin, margin, width - margin * 2, panel_h, "Equity", eq_min, eq_max)}
  <polyline points="{eq_points}" fill="none" stroke="#1f77b4" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
  {_axis_svg(margin, margin + panel_h + gap, width - margin * 2, panel_h, "Drawdown %", dd_min, dd_max)}
  <polyline points="{dd_points}" fill="none" stroke="#d62728" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
  {_date_labels(curve, width, height, margin)}
</svg>
"""
    path.write_text(svg, encoding="utf-8")
    return path


def _write_result_json(result: dict[str, Any], chart_path: Path) -> Path:
    path = settings.storage_dir / f"historical_backtest_{result['run_id']}.json"
    payload = {**result, "chart_path": str(chart_path)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _polyline_points(
    curve: list[dict[str, Any]],
    key: str,
    width: int,
    margin: int,
    top: float,
    panel_h: float,
    value_min: float,
    value_max: float,
) -> str:
    if not curve:
        return ""
    span = value_max - value_min or 1.0
    points: list[str] = []
    drawable_w = width - margin * 2
    for index, point in enumerate(curve):
        x = margin + drawable_w * index / max(len(curve) - 1, 1)
        value = float(point.get(key) or 0.0)
        y = top + panel_h - (value - value_min) / span * panel_h
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def _axis_svg(x: float, y: float, width: float, height: float, label: str, value_min: float, value_max: float) -> str:
    mid = (value_min + value_max) / 2
    return f"""
  <rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#f8fafc" stroke="#d8dee8"/>
  <line x1="{x}" y1="{y + height}" x2="{x + width}" y2="{y + height}" stroke="#9aa4b2"/>
  <line x1="{x}" y1="{y}" x2="{x}" y2="{y + height}" stroke="#9aa4b2"/>
  <text x="{x - 48}" y="{y + 5}" font-family="Arial, sans-serif" font-size="12" fill="#526070">{value_max:.2f}</text>
  <text x="{x - 48}" y="{y + height / 2 + 4}" font-family="Arial, sans-serif" font-size="12" fill="#526070">{mid:.2f}</text>
  <text x="{x - 48}" y="{y + height}" font-family="Arial, sans-serif" font-size="12" fill="#526070">{value_min:.2f}</text>
  <text x="{x}" y="{y - 10}" font-family="Arial, sans-serif" font-size="13" font-weight="700" fill="#172033">{_escape(label)}</text>
"""


def _date_labels(curve: list[dict[str, Any]], width: int, height: int, margin: int) -> str:
    if not curve:
        return ""
    first = curve[0]["date"]
    last = curve[-1]["date"]
    middle = curve[len(curve) // 2]["date"]
    return f"""
  <text x="{margin}" y="{height - 22}" font-family="Arial, sans-serif" font-size="12" fill="#526070">{first}</text>
  <text x="{width / 2 - 34}" y="{height - 22}" font-family="Arial, sans-serif" font-size="12" fill="#526070">{middle}</text>
  <text x="{width - margin - 60}" y="{height - 22}" font-family="Arial, sans-serif" font-size="12" fill="#526070">{last}</text>
"""


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "metrics": {"trade_count": 0, "reason": reason},
        "curve": [],
        "trades": [],
        "closed_trades": [],
        "selection_note": "",
    }


def _bars_by_date(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for symbol, bars in bars_by_symbol.items():
        for bar in bars:
            output.setdefault(bar["trade_date"], {})[symbol] = bar
    return output


def _bar_index(bars: list[dict[str, Any]], trade_date: str) -> int | None:
    for index, bar in enumerate(bars):
        if bar["trade_date"] == trade_date:
            return index
    return None


def _previous_bar(
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    symbol: str,
    trade_date: str,
) -> dict[str, Any] | None:
    previous_dates = [date for date in bars_by_date if date < trade_date and symbol in bars_by_date[date]]
    if not previous_dates:
        return None
    return bars_by_date[max(previous_dates)][symbol]


def _normalize_bar(symbol: str, bar: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(bar.get("ts_code") or symbol),
        "trade_date": str(bar.get("trade_date") or ""),
        "open": _float(bar.get("open"), 0.0),
        "high": _float(bar.get("high"), 0.0),
        "low": _float(bar.get("low"), 0.0),
        "close": _float(bar.get("close"), 0.0),
        "pct_chg": _float(bar.get("pct_chg"), 0.0),
        "amount": _float(bar.get("amount"), 0.0),
    }


def _market_value(
    positions: list[DailyPosition],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
) -> float:
    total = 0.0
    for position in positions:
        bar = bars_by_date.get(trade_date, {}).get(position.symbol)
        close = _float(bar.get("close") if bar else 0.0, position.entry_price)
        total += position.quantity * close
    return total


def _equity(
    cash: float,
    positions: list[DailyPosition],
    bars_by_date: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
) -> float:
    return cash + _market_value(positions, bars_by_date, trade_date)


def _amount_yi(bar: dict[str, Any]) -> float:
    amount = _float(bar.get("amount"), 0.0)
    return amount / 100000 if amount > 1000 else amount


def _commission(amount: float) -> float:
    if amount <= 0:
        return 0.0
    return max(amount * settings.paper_commission_rate, settings.paper_min_commission)


def _lot_floor(quantity: float) -> int:
    lot = max(settings.paper_lot_size, 1)
    return int(floor(max(quantity, 0.0) / lot) * lot)


def _volatility(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
