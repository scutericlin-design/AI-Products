#!/usr/bin/env python3
"""Build personalized portfolio advice from API/database holdings and latest signals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
ADVICE_PATH = PROJECT_ROOT / "data" / "processed" / "portfolio_advice.csv"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.models import PortfolioPosition, User


def load_portfolio(path: Path) -> pd.DataFrame:
    portfolio = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig")
    required = {"symbol", "name", "weight", "cost_price"}
    missing = required.difference(portfolio.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    portfolio = portfolio.copy()
    portfolio["symbol"] = portfolio["symbol"].astype(str).str.zfill(6)
    portfolio["weight"] = pd.to_numeric(portfolio["weight"], errors="coerce")
    portfolio["cost_price"] = pd.to_numeric(portfolio["cost_price"], errors="coerce")
    if "shares" in portfolio.columns:
        portfolio["shares"] = pd.to_numeric(portfolio["shares"], errors="coerce")
    else:
        portfolio["shares"] = pd.NA
    return portfolio


def load_portfolio_from_db(email: str | None = None) -> pd.DataFrame:
    db = SessionLocal()
    try:
        query = db.query(PortfolioPosition).join(User, PortfolioPosition.user_id == User.id)
        if email:
            query = query.filter(User.email == email.lower())
        positions = query.order_by(PortfolioPosition.user_id, PortfolioPosition.weight.desc()).all()
        rows = [
            {
                "symbol": position.symbol,
                "name": position.name,
                "weight": position.weight,
                "cost_price": position.cost_price,
                "shares": position.shares,
            }
            for position in positions
        ]
        return pd.DataFrame(rows, columns=["symbol", "name", "weight", "cost_price", "shares"])
    finally:
        db.close()


def load_signals(path: Path) -> pd.DataFrame:
    signals = pd.read_csv(path, dtype={"symbol": str})
    signals = signals.copy()
    signals["symbol"] = signals["symbol"].astype(str).str.zfill(6)
    return signals


OPTIONAL_SIGNAL_COLUMNS = [
    "alpha_score",
    "fundamental_quality_score",
    "valuation_sanity_score",
    "liquidity_capacity_score",
    "risk_control_score",
    "crowding_penalty",
    "financial_data_score",
    "data_completeness",
    "raw_institutional_score",
    "score_rank",
    "gate_penalty_score",
    "confidence",
    "model_version",
    "market_regime_key",
    "market_regime_label",
    "market_breadth_20d",
    "market_breadth_60d",
    "market_avg_momentum_20d",
    "market_amount_trend",
    "market_volatility_20d",
    "adaptive_note",
    "effective_buy_score_threshold",
    "score_weight_alpha",
    "score_weight_fundamental",
    "score_weight_valuation",
    "score_weight_liquidity",
    "score_weight_risk",
    "recommendation_tier",
    "signal_source",
    "turnover_rate",
    "stock_profile",
    "stock_profile_label",
    "profile_adjust_note",
]

ACTION_LABELS = {
    "add": "加仓",
    "trial_buy": "试仓买入",
    "hold_core": "核心持有",
    "hold_observe": "持有观察",
    "trim_to_risk_budget": "降至风险仓位",
    "reduce_light": "小幅降仓",
    "large_reduce": "大幅降仓",
    "no_new_buy": "不新增",
    "strategy_clear": "策略性清仓",
    "hard_exit": "清仓/硬风险退出",
    "hold": "持有",
    "hold_watch_no_new": "持有观察",
    "reduce": "减仓",
    "exit_or_strong_reduce": "强减/退出",
}
HARD_EXIT_FLAGS = {
    "delisting_risk",
    "st_or_financial_distress",
    "major_accounting_risk",
    "limit_down_or_hard_to_sell",
    "suspended_or_illiquid",
}
HARD_EXIT_FLAG_LABELS = {
    "delisting_risk": "退市风险",
    "st_or_financial_distress": "ST/财务困境",
    "major_accounting_risk": "重大财务或审计风险",
    "limit_down_or_hard_to_sell": "跌停或流动性导致难以卖出",
    "suspended_or_illiquid": "停牌或严重缺乏流动性",
}
SOFT_RISK_FLAGS = {
    "weak_price_alpha",
    "valuation_expensive_or_invalid",
    "quality_gate_failed",
    "missing_fundamental_data",
    "high_crowding",
    "low_liquidity",
    "wide_intraday_amplitude",
}
RETAIL_POSITION_PROFILES = [
    {
        "key": "small_concentrated",
        "label": "小资金集中",
        "capital_max": 100_000,
        "target_positions": "3-5只",
        "max_single": 0.30,
        "observe_weight": 0.08,
        "risk_per_trade": 0.015,
        "stop_loss": 0.08,
        "ranges": [
            (88, 0.20, 0.30),
            (82, 0.18, 0.25),
            (74, 0.15, 0.25),
            (65, 0.12, 0.20),
            (60, 0.08, 0.15),
            (55, 0.06, 0.10),
            (42, 0.05, 0.08),
            (0, 0.00, 0.00),
        ],
    },
    {
        "key": "standard_retail",
        "label": "标准散户",
        "capital_max": 2_000_000,
        "target_positions": "5-8只",
        "max_single": 0.30,
        "observe_weight": 0.06,
        "risk_per_trade": 0.015,
        "stop_loss": 0.08,
        "ranges": [
            (88, 0.20, 0.30),
            (82, 0.15, 0.25),
            (74, 0.12, 0.20),
            (65, 0.10, 0.16),
            (60, 0.08, 0.12),
            (55, 0.06, 0.10),
            (42, 0.05, 0.08),
            (0, 0.00, 0.00),
        ],
    },
    {
        "key": "steady_retail",
        "label": "稳健散户",
        "capital_max": 5_000_000,
        "target_positions": "6-10只",
        "max_single": 0.25,
        "observe_weight": 0.05,
        "risk_per_trade": 0.012,
        "stop_loss": 0.08,
        "ranges": [
            (82, 0.12, 0.20),
            (74, 0.10, 0.18),
            (65, 0.08, 0.15),
            (60, 0.06, 0.10),
            (55, 0.05, 0.08),
            (42, 0.04, 0.06),
            (0, 0.00, 0.00),
        ],
    },
    {
        "key": "larger_retail",
        "label": "分散型散户",
        "capital_max": float("inf"),
        "target_positions": "8-12只",
        "max_single": 0.20,
        "observe_weight": 0.03,
        "risk_per_trade": 0.010,
        "stop_loss": 0.08,
        "ranges": [
            (82, 0.10, 0.18),
            (74, 0.08, 0.15),
            (65, 0.06, 0.12),
            (60, 0.04, 0.08),
            (55, 0.00, 0.05),
            (42, 0.00, 0.03),
            (0, 0.00, 0.00),
        ],
    },
]
DEFAULT_RETAIL_PROFILE = RETAIL_POSITION_PROFILES[1]


def _record_value(row: pd.Series | dict, key: str):
    if isinstance(row, dict):
        return row.get(key)
    return row.get(key)


def _numeric_value(row: pd.Series | dict, key: str, default: float | None = None) -> float | None:
    value = _record_value(row, key)
    if value is None or pd.isna(value):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if pd.notna(number) else default


def _strategy_value(row: pd.Series | dict, strategy_key: str, key: str):
    scores = _record_value(row, "strategy_scores")
    if not isinstance(scores, dict):
        return None
    strategy = scores.get(strategy_key)
    if not isinstance(strategy, dict):
        return None
    return strategy.get(key)


def _strategy_numeric(row: pd.Series | dict, strategy_key: str, key: str) -> float | None:
    value = _strategy_value(row, strategy_key, key)
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _risk_flag_set(flags: object) -> set[str]:
    raw = "" if flags is None or pd.isna(flags) else str(flags)
    if raw.lower() in {"", "none", "nan", "null", "--"}:
        return set()
    normalized = raw.replace("，", "|").replace(",", "|").replace("；", "|").replace(";", "|")
    return {item.strip() for item in normalized.split("|") if item.strip()}


def _bounded(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _weighted_average(parts: list[tuple[float | None, float]]) -> float | None:
    valid = [(float(value), weight) for value, weight in parts if value is not None and pd.notna(value)]
    if not valid:
        return None
    total_weight = sum(weight for _value, weight in valid)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in valid) / total_weight


def _primary_signal_action(row: pd.Series | dict) -> str:
    return str(_record_value(row, "signal_action") or _record_value(row, "action") or "no_signal")


def _estimated_position_value(record: dict) -> float | None:
    shares = _numeric_value(record, "shares")
    latest_close = _numeric_value(record, "latest_close") or _numeric_value(record, "close")
    if shares is None or latest_close is None or shares <= 0 or latest_close <= 0:
        return None
    return shares * latest_close


def infer_account_value(records: list[dict]) -> float | None:
    implied_values: list[float] = []
    for record in records:
        position_value = _estimated_position_value(record)
        weight = _numeric_value(record, "weight")
        if position_value is not None and weight is not None and weight > 0:
            implied_values.append(position_value / weight)
    if implied_values:
        implied_values.sort()
        mid = len(implied_values) // 2
        if len(implied_values) % 2:
            return implied_values[mid]
        return (implied_values[mid - 1] + implied_values[mid]) / 2

    direct_values = [_estimated_position_value(record) for record in records]
    direct_values = [value for value in direct_values if value is not None]
    if direct_values:
        return sum(direct_values)
    return None


def infer_retail_profile(records: list[dict]) -> dict:
    account_value = infer_account_value(records)
    if account_value is None:
        profile = dict(DEFAULT_RETAIL_PROFILE)
        profile["account_value_estimate"] = None
        profile["capital_source"] = "default_standard_retail"
        return profile

    for profile in RETAIL_POSITION_PROFILES:
        if account_value <= float(profile["capital_max"]):
            selected = dict(profile)
            selected["account_value_estimate"] = round(account_value, 2)
            selected["capital_source"] = "shares_latest_price_weight"
            return selected
    profile = dict(DEFAULT_RETAIL_PROFILE)
    profile["account_value_estimate"] = round(account_value, 2)
    profile["capital_source"] = "fallback_standard_retail"
    return profile


def score_weight_range(holding_score: float, profile: dict) -> tuple[float, float]:
    for threshold, low, high in profile["ranges"]:
        if holding_score >= threshold:
            return float(low), min(float(high), float(profile["max_single"]))
    return 0.0, 0.0


def estimate_holding_score(row: pd.Series | dict) -> float:
    short_score = _numeric_value(row, "short_score")
    if short_score is None:
        short_score = _strategy_numeric(row, "short", "price_factor_score") or _strategy_numeric(row, "short", "score")
    mid_long_score = _numeric_value(row, "mid_long_score")
    if mid_long_score is None:
        mid_long_score = _strategy_numeric(row, "mid_long", "price_factor_score") or _strategy_numeric(row, "mid_long", "score")
    buy_score = _numeric_value(row, "price_factor_score")

    quality = _numeric_value(row, "fundamental_quality_score")
    valuation = _numeric_value(row, "valuation_sanity_score")
    liquidity = _numeric_value(row, "liquidity_capacity_score")
    risk = _numeric_value(row, "risk_control_score")
    completeness = _numeric_value(row, "data_completeness")
    completeness_score = completeness * 100 if completeness is not None and completeness <= 1.2 else completeness

    base = _weighted_average(
        [
            (short_score, 0.30),
            (mid_long_score, 0.36),
            (buy_score, 0.18 if short_score is None and mid_long_score is None else 0.06),
            (quality, 0.10),
            (risk, 0.09),
            (liquidity, 0.06),
            (valuation, 0.04),
            (completeness_score, 0.03),
        ]
    )
    if base is None:
        return 0.0

    signal_action = _primary_signal_action(row)
    if signal_action == "buy":
        base += 2.0
    elif signal_action == "trial_buy":
        base += 1.4
    elif signal_action == "watch":
        base += 0.8
    elif signal_action == "avoid":
        base -= 2.5

    flags = _risk_flag_set(_record_value(row, "risk_flags"))
    gate = _numeric_value(row, "gate_penalty_score", 0.0) or 0.0
    crowding = _numeric_value(row, "crowding_penalty")
    pct_change = _numeric_value(row, "pct_change")
    pnl = _numeric_value(row, "pnl_pct")

    base -= min(max(gate, 0.0) * 0.45, 6.0)
    if flags.intersection(SOFT_RISK_FLAGS):
        base -= min(6.0, 1.7 * len(flags.intersection(SOFT_RISK_FLAGS)))
    if "not_in_current_top_pool" in flags:
        base -= 1.2
    if crowding is not None and crowding >= 8:
        base -= min(5.0, (crowding - 7.5) * 1.2)
    if risk is not None and risk < 45:
        base -= 4.0
    if liquidity is not None and liquidity < 45:
        base -= 3.5
    if completeness is not None and completeness < 0.70:
        base -= 3.0
    if pct_change is not None and pct_change <= -9.7:
        base -= 5.0
    if pnl is not None and pnl <= -0.15 and base < 55:
        base -= 3.0

    return round(_bounded(base), 1)


def hard_exit_triggered(row: pd.Series | dict, holding_score: float) -> bool:
    flags = _risk_flag_set(_record_value(row, "risk_flags"))
    return bool(flags.intersection(HARD_EXIT_FLAGS))


def hard_exit_reason_text(row: pd.Series | dict) -> str:
    flags = _risk_flag_set(_record_value(row, "risk_flags")).intersection(HARD_EXIT_FLAGS)
    if not flags:
        return "硬风险标记"
    return "、".join(HARD_EXIT_FLAG_LABELS.get(flag, flag) for flag in sorted(flags))


def strategy_clear_reasons(row: pd.Series | dict, holding_score: float) -> list[str]:
    current_weight = _numeric_value(row, "weight", 0.0) or 0.0
    if current_weight <= 0:
        return []

    short_score = _numeric_value(row, "short_score")
    if short_score is None:
        short_score = _strategy_numeric(row, "short", "price_factor_score") or _strategy_numeric(row, "short", "score")
    mid_long_score = _numeric_value(row, "mid_long_score")
    if mid_long_score is None:
        mid_long_score = _strategy_numeric(row, "mid_long", "price_factor_score") or _strategy_numeric(row, "mid_long", "score")
    liquidity = _numeric_value(row, "liquidity_capacity_score")
    risk_control = _numeric_value(row, "risk_control_score")
    turnover = _numeric_value(row, "turnover_rate")
    signal_action = _primary_signal_action(row)
    flags = _risk_flag_set(_record_value(row, "risk_flags"))

    reasons: list[str] = []
    if holding_score < 30:
        reasons.append("综合持有评分低于 30")
    if short_score is not None and mid_long_score is not None and short_score < 45 and mid_long_score < 45:
        reasons.append("短期和长期评分同时低于 45")
    if liquidity is not None and liquidity < 35:
        reasons.append("流动性评分低于 35，资金进出效率差")
    if turnover is not None and turnover < 0.8 and liquidity is not None and liquidity < 50:
        reasons.append("换手率和成交承接长期偏弱")
    if signal_action == "avoid" and holding_score < 45:
        reasons.append("新增信号为回避且持有评分低")
    if {"low_liquidity", "weak_price_alpha"}.issubset(flags) and holding_score < 45:
        reasons.append("缺乏活跃度且价格趋势弱")
    if risk_control is not None and risk_control < 35 and holding_score < 45:
        reasons.append("风控评分过低")
    return reasons[:3]


def strategy_clear_triggered(row: pd.Series | dict, holding_score: float) -> bool:
    return bool(strategy_clear_reasons(row, holding_score))


def target_weight_range(
    row: pd.Series | dict,
    holding_score: float,
    max_single: float,
    watch_cap: float,
    profile: dict | None = None,
) -> tuple[float, float]:
    if hard_exit_triggered(row, holding_score):
        return 0.0, 0.0
    if strategy_clear_triggered(row, holding_score):
        return 0.0, 0.0

    profile = profile or DEFAULT_RETAIL_PROFILE
    signal_action = _primary_signal_action(row)
    signal_target = _numeric_value(row, "target_weight")
    if signal_target is None or signal_target <= 0:
        signal_target = None

    low, high = score_weight_range(holding_score, profile)

    if signal_action in {"buy", "trial_buy"}:
        high = max(high, signal_target or 0.0, float(profile["observe_weight"]))
        low = max(low, min(float(profile["observe_weight"]), high))
    elif signal_action in {"watch", "hold_or_reduce"} and holding_score >= 60:
        high = max(high, float(profile["observe_weight"]))
    elif signal_action in {"avoid", "not_in_system_pool"} and holding_score < 55:
        high = min(high, float(profile["observe_weight"]))

    high = min(float(profile["max_single"]), max(0.0, high))
    low = min(high, max(0.0, low))
    return round(low, 4), round(high, 4)


def choose_portfolio_action(row: pd.Series | dict, holding_score: float, low: float, high: float) -> str:
    current_weight = _numeric_value(row, "weight", 0.0) or 0.0
    signal_action = _primary_signal_action(row)
    reduction = max(0.0, current_weight - high)

    if hard_exit_triggered(row, holding_score):
        return "hard_exit"
    if strategy_clear_triggered(row, holding_score):
        return "strategy_clear"
    if current_weight > high + 0.02:
        if holding_score >= 45:
            return "trim_to_risk_budget"
        return "large_reduce" if reduction > 0.05 else "reduce_light"
    if signal_action == "buy" and holding_score >= 74 and current_weight < high - 0.01:
        return "add"
    if signal_action == "trial_buy" and holding_score >= 68 and current_weight < high - 0.01:
        return "add"
    if holding_score >= 78:
        return "hold_core"
    if holding_score >= 60:
        return "hold_observe"
    if holding_score >= 45:
        return "no_new_buy"
    if current_weight > max(high, 0.02) + 0.005:
        return "large_reduce" if reduction > 0.05 else "reduce_light"
    return "no_new_buy"


def choose_target_weight(row: pd.Series | dict, action: str, low: float, high: float) -> float:
    current_weight = _numeric_value(row, "weight", 0.0) or 0.0
    if action in {"hard_exit", "strategy_clear"}:
        return 0.0
    if action in {"trim_to_risk_budget", "reduce_light", "large_reduce"}:
        return high
    if action == "add":
        return min(high, max(low, current_weight + 0.02))
    if current_weight > high + 0.02:
        return high
    return current_weight


def build_upgrade_conditions(row: pd.Series | dict, holding_score: float) -> str:
    conditions: list[str] = []
    flags = _risk_flag_set(_record_value(row, "risk_flags"))
    action = str(_record_value(row, "portfolio_action") or "")
    stock_profile = str(_record_value(row, "stock_profile") or "mid_growth")
    stock_profile_label = str(_record_value(row, "stock_profile_label") or "均衡成长")
    short_score = _numeric_value(row, "short_score") or _strategy_numeric(row, "short", "price_factor_score")
    mid_long_score = _numeric_value(row, "mid_long_score") or _strategy_numeric(row, "mid_long", "price_factor_score")
    liquidity = _numeric_value(row, "liquidity_capacity_score")
    risk = _numeric_value(row, "risk_control_score")
    gate = _numeric_value(row, "gate_penalty_score", 0.0) or 0.0
    turnover = _numeric_value(row, "turnover_rate")
    amount20 = _numeric_value(row, "amount_ma20_yi") or _numeric_value(row, "amount_yi")
    close_position = _numeric_value(row, "intraday_position_pct")
    amplitude = _numeric_value(row, "amplitude_pct_display")
    quality = _numeric_value(row, "fundamental_quality_score")
    valuation = _numeric_value(row, "valuation_sanity_score")

    prefix = "重新纳入条件" if action in {"hard_exit", "strategy_clear"} else "升级条件"

    if stock_profile == "small_elastic":
        if short_score is None or short_score < 72:
            conditions.append("短期趋势评分站回 72 分以上，且不是单日脉冲")
        if turnover is None or turnover < 2.0 or turnover > 14.0:
            conditions.append(f"换手率回到 2%-14% 的健康弹性区间，当前 {turnover:.2f}%" if turnover is not None else "换手率回到 2%-14% 的健康弹性区间")
        if close_position is None or close_position < 58:
            conditions.append("收盘位置稳定在日内 58% 以上，确认资金承接")
        if risk is not None and risk < 58:
            conditions.append("风控评分修复至 58 分以上，避免高波动失控")
    elif stock_profile == "high_vol_theme":
        if risk is None or risk < 66:
            conditions.append("风控评分达到 66 分以上，先证明波动可控")
        if amplitude is None or amplitude > 11:
            conditions.append(f"日内振幅收敛至 11% 以内，当前 {amplitude:.2f}%" if amplitude is not None else "日内振幅收敛至 11% 以内")
        if short_score is None or short_score < 75:
            conditions.append("短期趋势评分达到 75 分以上，避免追高回撤")
        if gate > 0 or flags.intersection(SOFT_RISK_FLAGS):
            conditions.append("门禁扣分和题材拥挤风险明显下降")
    elif stock_profile == "large_quality":
        if mid_long_score is None or mid_long_score < 70:
            conditions.append("长期质量成长评分回到 70 分以上")
        if quality is None or quality < 65:
            conditions.append("基本面质量评分达到 65 分以上")
        if valuation is None or valuation < 50:
            conditions.append("估值合理性评分回到 50 分以上")
        if risk is None or risk < 64:
            conditions.append("风险控制评分达到 64 分以上，确认大票稳定性")
    elif stock_profile == "low_liquidity_watch":
        if liquidity is None or liquidity < 62:
            conditions.append("流动性评分达到 62 分以上，先解决进出效率")
        if amount20 is None or amount20 < 5:
            conditions.append(f"20日成交额提升至 5 亿以上，当前 {amount20:.2f} 亿" if amount20 is not None else "20日成交额提升至 5 亿以上")
        if turnover is None or turnover < 1.2:
            conditions.append("换手率回到 1.2% 以上，证明有活跃资金")
    elif stock_profile == "data_limited_growth":
        if short_score is None or short_score < 72:
            conditions.append("短期趋势评分达到 72 分以上，用价格强度弥补数据不足")
        if liquidity is None or liquidity < 58:
            conditions.append("流动性评分达到 58 分以上")
        if gate > 0:
            conditions.append("数据/门禁扣分下降，财务或行情覆盖进一步补齐")
        if risk is None or risk < 60:
            conditions.append("风控评分达到 60 分以上")
    else:
        if holding_score < 65:
            conditions.append("综合持有评分回到 65 分以上")
        if short_score is not None and short_score < 70:
            conditions.append("短期趋势评分修复至 70 分以上")
        if mid_long_score is not None and mid_long_score < 68:
            conditions.append("长期质量/估值评分修复至 68 分以上")

    if "not_in_current_top_pool" in flags:
        conditions.append("重新进入系统 Top30 或行业强度明显改善")
    if (gate > 0 or flags.intersection(SOFT_RISK_FLAGS)) and stock_profile not in {"high_vol_theme", "data_limited_growth"}:
        conditions.append("门禁扣分和风险标记消除")
    if liquidity is not None and liquidity < 60 and stock_profile not in {"low_liquidity_watch", "data_limited_growth"}:
        conditions.append("成交承接改善，流动性评分达到 60 分以上")
    if risk is not None and risk < 60 and stock_profile not in {"small_elastic", "high_vol_theme", "large_quality", "data_limited_growth"}:
        conditions.append("波动风险下降，风控评分达到 60 分以上")
    if not conditions:
        conditions.append(f"按{stock_profile_label}画像继续保持趋势、成交承接和基本面质量，且不突破单票仓位上限")
    return f"{prefix}（{stock_profile_label}）： " + "；".join(conditions[:4])


def build_action_rationale(row: pd.Series | dict, action: str, low: float, high: float, holding_score: float) -> str:
    label = ACTION_LABELS.get(action, action)
    current_weight = _numeric_value(row, "weight", 0.0) or 0.0
    signal_action = _primary_signal_action(row)
    if action == "add":
        return f"{label}：综合持有评分 {holding_score:.1f}，新增信号较强，可在 {low:.0%}-{high:.0%} 风险预算内分批。"
    if action == "hold_core":
        return f"{label}：评分和风险预算匹配，仓位维持在 {low:.0%}-{high:.0%} 区间内。"
    if action == "hold_observe":
        return f"{label}：当前可继续持有，但未达到积极新增标准，重点观察升级条件。"
    if action == "trim_to_risk_budget":
        return f"{label}：股票本身未触发硬退出，但当前 {current_weight:.1%} 超过 {high:.0%} 风险预算。"
    if action == "reduce_light":
        return f"{label}：综合评分偏弱或仓位略超预算，先把风险暴露降到 {high:.0%} 以内。"
    if action == "large_reduce":
        return f"{label}：综合评分偏弱且当前 {current_weight:.1%} 明显高于 {high:.0%} 风险预算，需要分批把仓位降下来。"
    if action == "strategy_clear":
        reasons = "、".join(strategy_clear_reasons(row, holding_score)) or "综合评分和活跃度不满足继续持有要求"
        return f"{label}：{reasons}，目标仓位为 0%，不建议继续占用资金。"
    if action == "hard_exit":
        return f"{label}：触发{hard_exit_reason_text(row)}，目标仓位为 0%，需要优先制定退出计划。"
    if action == "no_new_buy":
        return f"{label}：{signal_action} 未达到新增标准，已有仓位以观察和风控为主。"
    return f"{label}：按综合持有评分和风险预算执行。"


def build_trade_plan(row: pd.Series | dict, action: str, low: float, high: float, holding_score: float, profile: dict) -> dict:
    current_weight = _numeric_value(row, "weight", 0.0) or 0.0
    buy_score = _numeric_value(row, "price_factor_score")
    stop_loss = float(profile["stop_loss"])
    risk_per_trade = float(profile["risk_per_trade"])
    observe = float(profile["observe_weight"])

    if action in {"hard_exit", "strategy_clear"}:
        initial_weight = 0.0
        add_weight = 0.0
    elif buy_score is not None and buy_score >= 78 and holding_score >= 70:
        initial_weight = min(high, max(low, observe * 2))
        add_weight = high
    elif holding_score >= 65:
        initial_weight = min(high, max(low, observe))
        add_weight = high
    elif holding_score >= 55:
        initial_weight = min(high, observe)
        add_weight = high
    else:
        initial_weight = 0.0
        add_weight = 0.0

    if current_weight > 0 and action not in {"hard_exit", "strategy_clear"}:
        initial_weight = min(high, max(initial_weight, min(current_weight, high)))
        add_weight = max(add_weight, min(high, current_weight))

    clear_reasons = strategy_clear_reasons(row, holding_score)
    clear_rule = (
        f"触发{hard_exit_reason_text(row)}时清仓"
        if action == "hard_exit"
        else "；".join(clear_reasons)
        if clear_reasons
        else "综合持有评分低于 35，或短期/长期评分同时低于 45 且流动性继续走弱"
    )

    return {
        "profile_key": profile["key"],
        "profile_label": profile["label"],
        "target_positions": profile["target_positions"],
        "account_value_estimate": profile.get("account_value_estimate"),
        "capital_source": profile.get("capital_source"),
        "profile_max_single": round(float(profile["max_single"]), 4),
        "initial_weight": round(initial_weight, 4),
        "add_weight": round(add_weight, 4),
        "max_weight": round(high, 4),
        "observe_weight": round(observe, 4),
        "stop_loss_pct": round(stop_loss, 4),
        "risk_per_trade_pct": round(risk_per_trade, 4),
        "buy_rule": "新增买入评分达到 78 且短期/长期至少一侧确认；否则只进入观察仓或等待。",
        "add_rule": "买入后评分维持 70 以上、成交承接不恶化、价格回踩不破关键趋势位，再分批加到上限。",
        "reduce_rule": "跌破趋势位、持有评分跌破 55、或仓位超过风险预算时先降仓。",
        "clear_rule": clear_rule,
        "review_rule": "每次打开页面刷新评分；持仓股至少每周复评一次，财报/公告/异动当日必须复评。",
    }


def finalize_advice_record(record: dict, max_single: float, watch_cap: float, profile: dict | None = None) -> dict:
    updated = dict(record)
    profile = profile or DEFAULT_RETAIL_PROFILE
    holding_score = estimate_holding_score(updated)
    low, high = target_weight_range(updated, holding_score, max_single, watch_cap, profile)
    action = choose_portfolio_action(updated, holding_score, low, high)
    target = choose_target_weight(updated, action, low, high)
    current_weight = _numeric_value(updated, "weight", 0.0) or 0.0
    trade_plan = build_trade_plan(updated, action, low, high, holding_score, profile)

    updated["holding_score"] = holding_score
    updated["target_weight_low"] = low
    updated["target_weight_high"] = high
    updated["suggested_target_weight"] = round(target, 4)
    updated["weight_delta"] = round(target - current_weight, 4)
    updated["portfolio_action"] = action
    updated["advice_type"] = "position_management"
    updated["position_role"] = (
        "核心持有"
        if action == "hold_core"
        else "观察持有"
        if action in {"hold_observe", "no_new_buy"}
        else "风险预算管理"
        if action in {"trim_to_risk_budget", "reduce_light", "large_reduce"}
        else "退出候选"
        if action in {"hard_exit", "strategy_clear"}
        else "加仓候选"
    )
    updated["position_profile_key"] = profile["key"]
    updated["position_profile_label"] = profile["label"]
    updated["target_position_count"] = profile["target_positions"]
    updated["account_value_estimate"] = profile.get("account_value_estimate")
    updated["profile_max_single"] = round(float(profile["max_single"]), 4)
    updated["retail_position_note"] = f"{profile['label']}模式：建议组合控制在{profile['target_positions']}，普通持仓不再使用过度分散的个位数仓位。"
    updated["buy_initial_weight"] = trade_plan["initial_weight"]
    updated["buy_add_weight"] = trade_plan["add_weight"]
    updated["buy_max_weight"] = trade_plan["max_weight"]
    updated["stop_loss_pct"] = trade_plan["stop_loss_pct"]
    updated["risk_per_trade_pct"] = trade_plan["risk_per_trade_pct"]
    updated["trade_plan"] = trade_plan
    updated["upgrade_conditions"] = build_upgrade_conditions(updated, holding_score)
    updated["action_rationale"] = build_action_rationale(updated, action, low, high, holding_score)
    updated["advice_reason"] = build_reason(pd.Series(updated), max_single, watch_cap)
    return updated


def finalize_advice_records(records: list[dict], max_single: float, watch_cap: float) -> list[dict]:
    profile = infer_retail_profile(records)
    return [finalize_advice_record(record, max_single, watch_cap, profile) for record in records]


def target_for_signal(
    signal_action: str,
    current_weight: float,
    max_single: float,
    watch_cap: float,
    signal_target_weight: float | None = None,
) -> float:
    if signal_action in {"buy", "trial_buy"}:
        if signal_target_weight is not None and pd.notna(signal_target_weight) and signal_target_weight > 0:
            return min(max_single, float(signal_target_weight))
        return min(max_single, max(0.05, min(0.08, max_single)))
    if signal_action == "watch":
        return min(current_weight, watch_cap)
    if signal_action == "hold_or_reduce":
        return min(current_weight, watch_cap)
    if signal_action == "avoid":
        return 0.0
    if signal_action == "not_in_system_pool":
        return min(current_weight, watch_cap)
    return min(current_weight, watch_cap)


def advice_action(signal_action: str, current_weight: float, target_weight: float) -> str:
    if signal_action == "avoid":
        return "exit_or_strong_reduce"
    if signal_action == "not_in_system_pool" and current_weight > target_weight + 0.02:
        return "reduce"
    if current_weight > target_weight + 0.02:
        return "reduce"
    if signal_action in {"buy", "trial_buy"} and current_weight < target_weight - 0.01:
        return "add"
    if signal_action == "watch":
        return "hold_watch_no_new"
    return "hold"


def _factor_value(row: pd.Series, column: str) -> float | None:
    value = row.get(column)
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_factor_summary(row: pd.Series) -> str:
    strengths: list[str] = []
    risks: list[str] = []
    alpha = _factor_value(row, "alpha_score")
    quality = _factor_value(row, "fundamental_quality_score")
    valuation = _factor_value(row, "valuation_sanity_score")
    liquidity = _factor_value(row, "liquidity_capacity_score")
    risk = _factor_value(row, "risk_control_score")
    completeness = _factor_value(row, "data_completeness")
    crowding = _factor_value(row, "crowding_penalty")
    gate = _factor_value(row, "gate_penalty_score")

    if alpha is not None and alpha >= 70:
        strengths.append("趋势/Alpha强")
    if quality is not None and quality >= 65:
        strengths.append("基本面较好")
    if valuation is not None and valuation >= 60:
        strengths.append("估值相对合理")
    if liquidity is not None and liquidity >= 70:
        strengths.append("成交承接好")
    if risk is not None and risk >= 65:
        strengths.append("波动风险可控")

    if gate is not None and gate > 0:
        risks.append(f"门禁扣 {gate:.1f}")
    if crowding is not None and crowding >= 8:
        risks.append("拥挤/追高风险")
    if completeness is not None and completeness < 0.75:
        risks.append("数据覆盖待补齐")
    if risk is not None and risk < 55:
        risks.append("风控分偏低")
    if liquidity is not None and liquidity < 55:
        risks.append("流动性不足")

    strength_text = "、".join(strengths[:3]) if strengths else "暂无突出优势"
    risk_text = "、".join(risks[:3]) if risks else "硬性风险暂未突出"
    return f"优势：{strength_text}；风险：{risk_text}"


def build_reason(row: pd.Series, max_single: float, watch_cap: float) -> str:
    parts: list[str] = []
    parts.append(f"当前仓位 {row['weight']:.2%}")
    if pd.notna(row.get("holding_score")):
        parts.append(f"综合持有评分 {row['holding_score']:.1f}")
    if pd.notna(row.get("target_weight_high")):
        parts.append(f"风险预算 {row.get('target_weight_low', 0):.0%}-{row['target_weight_high']:.0%}")

    if pd.notna(row.get("pnl_pct")):
        parts.append(f"浮盈亏 {row['pnl_pct']:.2%}")

    price_factor_score = _factor_value(row, "price_factor_score")
    if price_factor_score is not None:
        parts.append(f"新增买入评分 {price_factor_score:.1f}")
    turnover_rate_value = _factor_value(row, "turnover_rate")
    if turnover_rate_value is not None:
        turnover_rate = float(turnover_rate_value)
        parts.append(f"换手率 {turnover_rate:.2f}%")

    parts.append(build_factor_summary(row))

    signal_action = row.get("signal_action", "no_signal")
    parts.append(f"新增信号 {signal_action}")

    if row.get("model_version"):
        parts.append(f"模型版本 {row['model_version']}")

    profile_max_single = _factor_value(row, "profile_max_single") or max_single
    if row["weight"] > profile_max_single:
        parts.append(f"超过散户单票上限 {profile_max_single:.0%}")

    if signal_action == "not_in_system_pool":
        parts.append("未进入当前系统股票池，表示新增优先级不足，不等同于必须卖出")

    if row.get("action_rationale"):
        parts.append(row["action_rationale"])

    if row.get("upgrade_conditions"):
        parts.append(f"升级条件: {row['upgrade_conditions']}")

    risk_flags = row.get("risk_flags")
    if isinstance(risk_flags, str) and risk_flags:
        parts.append(f"风险标记: {risk_flags}")

    return "；".join(parts)


def build_advice(portfolio: pd.DataFrame, signals: pd.DataFrame, max_single: float, watch_cap: float) -> pd.DataFrame:
    signals = signals.copy()
    for column in OPTIONAL_SIGNAL_COLUMNS:
        if column not in signals.columns:
            signals[column] = pd.NA

    merged = portfolio.merge(
        signals,
        how="left",
        on="symbol",
        suffixes=("_portfolio", "_signal"),
    )

    merged["name"] = merged["name_portfolio"].fillna(merged.get("name_signal"))
    merged["signal_action"] = merged["action"].fillna("not_in_system_pool")
    merged["model_version"] = merged["model_version"].fillna("institutional_score_v7_profile_adaptive_tushare")
    merged["confidence"] = merged["confidence"].fillna("low")
    merged["signal_source"] = merged["signal_source"].fillna("no_system_signal")
    merged["latest_close"] = merged["close"]
    merged["pnl_pct"] = merged["latest_close"] / merged["cost_price"] - 1
    merged.loc[merged["latest_close"].isna() | merged["cost_price"].isna(), "pnl_pct"] = pd.NA

    merged = pd.DataFrame(finalize_advice_records(merged.to_dict(orient="records"), max_single, watch_cap))
    merged["advice_reason"] = merged.apply(lambda row: build_reason(row, max_single, watch_cap), axis=1)

    output_columns = [
        "symbol",
        "name",
        "weight",
        "suggested_target_weight",
        "target_weight_low",
        "target_weight_high",
        "weight_delta",
        "portfolio_action",
        "signal_action",
        "holding_score",
        "position_role",
        "position_profile_key",
        "position_profile_label",
        "target_position_count",
        "account_value_estimate",
        "profile_max_single",
        "retail_position_note",
        "buy_initial_weight",
        "buy_add_weight",
        "buy_max_weight",
        "stop_loss_pct",
        "risk_per_trade_pct",
        "trade_plan",
        "advice_type",
        "action_rationale",
        "upgrade_conditions",
        "price_factor_score",
        "latest_close",
        "cost_price",
        "pnl_pct",
        "shares",
        "trade_date",
        "risk_flags",
        "stock_profile",
        "stock_profile_label",
        "profile_adjust_note",
        "turnover_rate",
        "alpha_score",
        "fundamental_quality_score",
        "valuation_sanity_score",
        "liquidity_capacity_score",
        "risk_control_score",
        "crowding_penalty",
        "financial_data_score",
        "data_completeness",
        "raw_institutional_score",
        "score_rank",
        "gate_penalty_score",
        "confidence",
        "model_version",
        "market_regime_key",
        "market_regime_label",
        "market_breadth_20d",
        "market_breadth_60d",
        "market_avg_momentum_20d",
        "market_amount_trend",
        "market_volatility_20d",
        "adaptive_note",
        "effective_buy_score_threshold",
        "score_weight_alpha",
        "score_weight_fundamental",
        "score_weight_valuation",
        "score_weight_liquidity",
        "score_weight_risk",
        "recommendation_tier",
        "signal_source",
        "advice_reason",
    ]
    return merged[output_columns].sort_values(
        ["holding_score", "price_factor_score", "weight"],
        ascending=[False, False, False],
        na_position="last",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build personalized advice from server-side portfolio positions.")
    parser.add_argument("--email", help="Optional account email; defaults to all stored portfolio positions.")
    parser.add_argument("--portfolio-csv", type=Path, help="Legacy explicit CSV import for development only.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_LATEST_PATH)
    parser.add_argument("--max-single", type=float, default=0.12, help="Maximum target weight for one stock.")
    parser.add_argument("--watch-cap", type=float, default=0.04, help="Target cap for watch or degraded stocks.")
    args = parser.parse_args()

    portfolio = load_portfolio(args.portfolio_csv) if args.portfolio_csv else load_portfolio_from_db(args.email)
    if portfolio.empty:
        raise SystemExit("No portfolio positions found. Add holdings in the web page first.")
    signals = load_signals(args.signals)
    advice = build_advice(portfolio, signals, args.max_single, args.watch_cap)

    ADVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    advice.to_csv(ADVICE_PATH, index=False, encoding="utf-8-sig")

    print(f"wrote {ADVICE_PATH.relative_to(PROJECT_ROOT)} rows={len(advice)}")
    print("\nportfolio advice")
    print(
        advice[
            [
                "symbol",
                "name",
                "weight",
                "suggested_target_weight",
                "target_weight_high",
                "portfolio_action",
                "holding_score",
                "signal_action",
                "price_factor_score",
                "pnl_pct",
            ]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
