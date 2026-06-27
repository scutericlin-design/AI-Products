from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.models import PortfolioPosition, WatchlistItem


SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
SCORED_UNIVERSE_PATH = PROJECT_ROOT / "data" / "processed" / "scored_universe_latest.csv"
NO_FALLBACK_PATH = PROJECT_ROOT / "data" / "processed" / "__missing_strategy_fallback__.csv"
STRATEGY_SIGNAL_CONFIGS = {
    "short": {
        "strategy_type": "short_elastic_2_8w",
        "label": "短期高弹性 2-8周",
        "pool_path": PROJECT_ROOT / "data" / "processed" / "recommended_pool_short.csv",
        "scored_universe_path": PROJECT_ROOT / "data" / "processed" / "scored_universe_short.csv",
    },
    "mid_long": {
        "strategy_type": "mid_long_quality_3_12m",
        "label": "中长期质量成长 3-12月",
        "pool_path": PROJECT_ROOT / "data" / "processed" / "recommended_pool_midlong.csv",
        "scored_universe_path": PROJECT_ROOT / "data" / "processed" / "scored_universe_midlong.csv",
    },
}
STRATEGY_SCORE_FIELDS = [
    "price_factor_score",
    "action",
    "target_weight",
    "trade_date",
    "risk_flags",
    "reason",
    "stock_profile",
    "stock_profile_label",
    "profile_adjust_note",
    "close",
    "pct_change",
    "amount_yi",
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
]


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    return value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in record.items()}


def _portfolio_engine():
    from scripts.build_portfolio_advice import build_advice, finalize_advice_records, load_signals

    return build_advice, finalize_advice_records, load_signals


def _score_for_sort(record: dict[str, Any]) -> float:
    for key in ["holding_score", "price_factor_score", "short_score", "mid_long_score"]:
        value = record.get(key)
        try:
            if value is not None and pd.notna(value):
                return float(value)
        except (TypeError, ValueError):
            continue
    return -1.0


def _sort_records_by_score(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            _score_for_sort(record),
            float(record.get("weight") or 0),
            str(record.get("symbol") or ""),
        ),
        reverse=True,
    )


def positions_to_frame(positions: list[PortfolioPosition]) -> pd.DataFrame:
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


def _empty_signal_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "name",
            "close",
            "price_factor_score",
            "action",
            "target_weight",
            "trade_date",
            "risk_flags",
            "reason",
            "stock_profile",
            "stock_profile_label",
            "profile_adjust_note",
            "pct_change",
            "amount_yi",
            "turnover_rate",
            "intraday_position_pct",
            "amplitude_pct_display",
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
        ]
    )


def load_personal_signal_view(
    pool_path: Path = RECOMMENDED_POOL_PATH,
    scored_universe_path: Path = SCORED_UNIVERSE_PATH,
    fallback_path: Path = SIGNAL_LATEST_PATH,
    held_symbols: set[str] | None = None,
) -> pd.DataFrame:
    _build_advice, _finalize_advice_records, load_signals = _portfolio_engine()
    frames: list[pd.DataFrame] = []
    held_symbols = {str(symbol).zfill(6) for symbol in (held_symbols or set())}
    if pool_path.exists():
        pool = load_signals(pool_path)
        pool["signal_source"] = "system_pool"
        frames.append(pool)

    used_symbols = set(frames[0]["symbol"]) if frames else set()
    if scored_universe_path.exists() and held_symbols:
        universe = load_signals(scored_universe_path)
        missing_held_symbols = held_symbols.difference(used_symbols)
        universe = universe[universe["symbol"].isin(missing_held_symbols)].copy()
        if not universe.empty:
            universe["signal_source"] = "portfolio_realtime_score"
            universe["risk_flags"] = universe["risk_flags"].fillna("").astype(str)
            universe["risk_flags"] = universe["risk_flags"].apply(
                lambda value: "|".join([item for item in [value, "not_in_current_top_pool"] if item])
            )
            universe["reason"] = "未进入当前Top30系统股票池；" + universe["reason"].fillna("").astype(str)
            universe.loc[universe["action"] == "buy", "action"] = "watch"
            universe["target_weight"] = 0.0
            frames.append(universe)
            used_symbols.update(set(universe["symbol"]))

    if fallback_path.exists():
        fallback = load_signals(fallback_path)
        fallback = fallback[~fallback["symbol"].isin(used_symbols)].copy()
        if held_symbols:
            fallback = fallback[fallback["symbol"].isin(held_symbols)].copy()
        fallback["action"] = "not_in_system_pool"
        fallback["price_factor_score"] = pd.NA
        fallback["target_weight"] = 0.0
        fallback["risk_flags"] = "not_in_current_top_pool"
        fallback["reason"] = "未进入当前自适应系统股票池；仅使用本地行情估算浮盈亏。"
        fallback["model_version"] = "institutional_score_v7_profile_adaptive_tushare"
        fallback["confidence"] = "low"
        fallback["signal_source"] = "fallback_price_only"
        frames.append(fallback)

    if not frames:
        return _empty_signal_frame()

    signals = pd.concat(frames, ignore_index=True, sort=False)
    for column in _empty_signal_frame().columns:
        if column not in signals.columns:
            signals[column] = pd.NA
    return signals[_empty_signal_frame().columns].drop_duplicates("symbol", keep="first")


def _path_strategy_type(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, dtype={"symbol": str}, nrows=1, encoding="utf-8-sig")
    except Exception:
        return None
    if frame.empty or "strategy_type" not in frame.columns:
        return None
    value = frame.iloc[0].get("strategy_type")
    return str(value) if pd.notna(value) and str(value) else None


def _strategy_paths_for_read(strategy_key: str) -> tuple[Path, Path]:
    config = STRATEGY_SIGNAL_CONFIGS[strategy_key]
    pool_path = config["pool_path"]
    scored_universe_path = config["scored_universe_path"]
    if pool_path.exists() or scored_universe_path.exists():
        return pool_path, scored_universe_path

    strategy_type = str(config["strategy_type"])
    if _path_strategy_type(RECOMMENDED_POOL_PATH) == strategy_type or _path_strategy_type(SCORED_UNIVERSE_PATH) == strategy_type:
        return RECOMMENDED_POOL_PATH, SCORED_UNIVERSE_PATH
    return pool_path, scored_universe_path


def load_strategy_signal_view(strategy_key: str, held_symbols: set[str] | None = None) -> pd.DataFrame:
    pool_path, scored_universe_path = _strategy_paths_for_read(strategy_key)
    return load_personal_signal_view(
        pool_path=pool_path,
        scored_universe_path=scored_universe_path,
        fallback_path=NO_FALLBACK_PATH,
        held_symbols=held_symbols,
    )


def _strategy_payload(strategy_key: str, signal: dict[str, Any] | None) -> dict[str, Any]:
    config = STRATEGY_SIGNAL_CONFIGS[strategy_key]
    payload: dict[str, Any] = {
        "strategy_key": strategy_key,
        "strategy_type": config["strategy_type"],
        "strategy_label": config["label"],
        "status": "missing",
        "score": None,
        "price_factor_score": None,
        "action": "no_signal",
        "confidence": "low",
        "trade_date": None,
        "model_version": None,
        "signal_source": "strategy_cache_missing",
        "reason": "该策略评分缓存尚未生成，请在系统股票池切换到该策略后点击更新。",
    }
    if not signal:
        return payload

    payload["status"] = "available"
    for field in STRATEGY_SCORE_FIELDS:
        payload[field] = signal.get(field)
    payload["score"] = signal.get("price_factor_score")
    payload["latest_close"] = signal.get("close")
    payload["action"] = signal.get("action") or "no_signal"
    payload["confidence"] = signal.get("confidence") or "low"
    payload["signal_source"] = signal.get("signal_source") or "strategy_signal"
    payload["reason"] = signal.get("reason") or payload["reason"]
    return _clean_record(payload)


def build_strategy_score_bundle(symbols: set[str]) -> dict[str, dict[str, dict[str, Any]]]:
    normalized_symbols = {str(symbol).zfill(6) for symbol in symbols}
    bundle: dict[str, dict[str, dict[str, Any]]] = {
        symbol: {
            key: _strategy_payload(key, None)
            for key in STRATEGY_SIGNAL_CONFIGS
        }
        for symbol in normalized_symbols
    }
    if not normalized_symbols:
        return bundle

    for strategy_key in STRATEGY_SIGNAL_CONFIGS:
        signals = load_strategy_signal_view(strategy_key, held_symbols=normalized_symbols)
        if signals.empty:
            continue
        signals = signals[signals["symbol"].isin(normalized_symbols)].drop_duplicates("symbol", keep="first")
        for record in signals.to_dict(orient="records"):
            symbol = str(record.get("symbol") or "").zfill(6)
            if symbol in bundle:
                bundle[symbol][strategy_key] = _strategy_payload(strategy_key, _clean_record(record))
    return bundle


def _attach_strategy_scores(records: list[dict[str, Any]], symbols: set[str]) -> list[dict[str, Any]]:
    score_bundle = build_strategy_score_bundle(symbols)
    for record in records:
        symbol = str(record.get("symbol") or "").zfill(6)
        strategy_scores = score_bundle.get(symbol) or {
            key: _strategy_payload(key, None)
            for key in STRATEGY_SIGNAL_CONFIGS
        }
        short_score = strategy_scores["short"]
        mid_long_score = strategy_scores["mid_long"]
        record["strategy_scores"] = strategy_scores
        record["short_score"] = short_score.get("price_factor_score")
        record["short_action"] = short_score.get("action")
        record["short_confidence"] = short_score.get("confidence")
        record["short_trade_date"] = short_score.get("trade_date")
        record["short_model_version"] = short_score.get("model_version")
        record["mid_long_score"] = mid_long_score.get("price_factor_score")
        record["mid_long_action"] = mid_long_score.get("action")
        record["mid_long_confidence"] = mid_long_score.get("confidence")
        record["mid_long_trade_date"] = mid_long_score.get("trade_date")
        record["mid_long_model_version"] = mid_long_score.get("model_version")
    return [_clean_record(record) for record in records]


def build_user_advice(
    positions: list[PortfolioPosition],
    signals_path: Path = RECOMMENDED_POOL_PATH,
    max_single: float = 0.12,
    watch_cap: float = 0.04,
) -> list[dict[str, Any]]:
    if not positions:
        return []
    portfolio = positions_to_frame(positions)
    if signals_path == RECOMMENDED_POOL_PATH:
        signals = load_personal_signal_view(pool_path=signals_path, held_symbols=set(portfolio["symbol"]))
    else:
        _build_advice, _finalize_advice_records, load_signals = _portfolio_engine()
        signals = load_signals(signals_path)
    build_advice, finalize_advice_records, _load_signals = _portfolio_engine()
    advice = build_advice(portfolio, signals, max_single=max_single, watch_cap=watch_cap)
    records = [_clean_record(record) for record in advice.to_dict(orient="records")]
    records = _attach_strategy_scores(records, set(portfolio["symbol"]))
    records = finalize_advice_records(records, max_single=max_single, watch_cap=watch_cap)
    return _sort_records_by_score([_clean_record(record) for record in records])


def build_watchlist_scores(items: list[WatchlistItem]) -> list[dict[str, Any]]:
    if not items:
        return []

    symbols = {str(item.symbol).zfill(6) for item in items}
    signals = load_personal_signal_view(held_symbols=symbols)
    if not signals.empty:
        signals = signals[signals["symbol"].isin(symbols)].drop_duplicates("symbol", keep="first")
    signals_by_symbol = {
        str(record.get("symbol") or "").zfill(6): _clean_record(record)
        for record in signals.to_dict(orient="records")
    }

    rows: list[dict[str, Any]] = []
    for item in items:
        symbol = str(item.symbol).zfill(6)
        signal = signals_by_symbol.get(symbol, {})
        action = signal.get("action") or "not_in_system_pool"
        score = signal.get("price_factor_score")
        reason = signal.get("reason") or "暂未进入当前评分文件；建议先等待下一轮数据刷新后再判断。"
        if action == "not_in_system_pool":
            reason = "未进入当前Top30系统股票池；" + reason
        rows.append(
            _clean_record(
                {
                    "id": item.id,
                    "symbol": symbol,
                    "name": item.name or signal.get("name") or symbol,
                    "note": item.note,
                    "action": action,
                    "signal_action": action,
                    "price_factor_score": score,
                    "latest_close": signal.get("close"),
                    "trade_date": signal.get("trade_date"),
                    "risk_flags": signal.get("risk_flags"),
                    "reason": reason,
                    "stock_profile": signal.get("stock_profile"),
                    "stock_profile_label": signal.get("stock_profile_label"),
                    "profile_adjust_note": signal.get("profile_adjust_note"),
                    "pct_change": signal.get("pct_change"),
                    "amount_yi": signal.get("amount_yi"),
                    "turnover_rate": signal.get("turnover_rate"),
                    "intraday_position_pct": signal.get("intraday_position_pct"),
                    "amplitude_pct_display": signal.get("amplitude_pct_display"),
                    "alpha_score": signal.get("alpha_score"),
                    "fundamental_quality_score": signal.get("fundamental_quality_score"),
                    "valuation_sanity_score": signal.get("valuation_sanity_score"),
                    "liquidity_capacity_score": signal.get("liquidity_capacity_score"),
                    "risk_control_score": signal.get("risk_control_score"),
                    "crowding_penalty": signal.get("crowding_penalty"),
                    "financial_data_score": signal.get("financial_data_score"),
                    "data_completeness": signal.get("data_completeness"),
                    "raw_institutional_score": signal.get("raw_institutional_score"),
                    "score_rank": signal.get("score_rank"),
                    "gate_penalty_score": signal.get("gate_penalty_score"),
                    "confidence": signal.get("confidence") or "low",
                    "model_version": signal.get("model_version"),
                    "market_regime_key": signal.get("market_regime_key"),
                    "market_regime_label": signal.get("market_regime_label"),
                    "market_breadth_20d": signal.get("market_breadth_20d"),
                    "market_breadth_60d": signal.get("market_breadth_60d"),
                    "market_avg_momentum_20d": signal.get("market_avg_momentum_20d"),
                    "market_amount_trend": signal.get("market_amount_trend"),
                    "market_volatility_20d": signal.get("market_volatility_20d"),
                    "adaptive_note": signal.get("adaptive_note"),
                    "effective_buy_score_threshold": signal.get("effective_buy_score_threshold"),
                    "score_weight_alpha": signal.get("score_weight_alpha"),
                    "score_weight_fundamental": signal.get("score_weight_fundamental"),
                    "score_weight_valuation": signal.get("score_weight_valuation"),
                    "score_weight_liquidity": signal.get("score_weight_liquidity"),
                    "score_weight_risk": signal.get("score_weight_risk"),
                    "recommendation_tier": signal.get("recommendation_tier"),
                    "signal_source": signal.get("signal_source") or "watchlist_refresh",
                }
            )
        )
    return _attach_strategy_scores(rows, symbols)


EXIT_ACTIONS = {"hard_exit", "strategy_clear", "exit_or_strong_reduce"}
REDUCE_ACTIONS = {"reduce", "reduce_light", "large_reduce", "trim_to_risk_budget"}
BUY_ACTIONS = {"add"}
OPTIMAL_TRADE_PLAN_VERSION = "retail_optimal_trade_plan_v1_core30"
TRADE_PLAN_BUY_ACTIONS = {"buy", "trial_buy", "add"}
TRADE_PLAN_KEEP_ACTIONS = {"hold", "watch", "hold_core", "hold_observe", "hold_watch_no_new", "no_new_buy"}
TRADE_PLAN_EXIT_ACTIONS = EXIT_ACTIONS.union({"avoid"})
TRADE_PLAN_SOFT_RISK_FLAGS = {
    "weak_price_alpha",
    "valuation_expensive_or_invalid",
    "quality_gate_failed",
    "missing_fundamental_data",
    "high_crowding",
    "low_liquidity",
    "wide_intraday_amplitude",
    "not_in_current_top_pool",
}
TRADE_PLAN_HARD_RISK_FLAGS = {
    "delisting_risk",
    "st_or_financial_distress",
    "major_accounting_risk",
    "limit_down_or_hard_to_sell",
    "suspended_or_illiquid",
}


def _manual_trade_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _round_down_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0 or lot_size <= 0:
        return 0
    return int(quantity // lot_size * lot_size)


def _manual_trade_intent(row: dict[str, Any], delta: float) -> str:
    action = str(row.get("portfolio_action") or "")
    if action in EXIT_ACTIONS:
        return "sell"
    if action in REDUCE_ACTIONS or delta < -0.005:
        return "sell"
    if action in BUY_ACTIONS and delta > 0.005:
        return "buy"
    return "hold"


def _manual_trade_priority(intent: str, action: str, score: float, delta: float) -> tuple[int, float, float]:
    if intent == "sell" and action in EXIT_ACTIONS:
        return (0, -abs(delta), -score)
    if intent == "sell":
        return (1, -abs(delta), -score)
    if intent == "buy":
        return (2, -score, -delta)
    return (3, -score, -abs(delta))


def _manual_trade_note(row: dict[str, Any], intent: str, quantity: int, estimated_amount: float, min_trade_amount: float) -> str:
    action = str(row.get("portfolio_action") or "")
    score = _manual_trade_number(row.get("holding_score"), -1)
    if intent == "sell" and action in EXIT_ACTIONS:
        return "退出类信号，优先人工核对盘口、跌停和公告后处理。"
    if intent == "sell":
        return "降风险信号，建议先把仓位降到风险预算附近。"
    if intent == "buy":
        if quantity <= 0 or estimated_amount < min_trade_amount:
            return "达到加仓方向，但金额不足一手或低于最小交易额，先观察。"
        return "加仓信号，建议分批限价，不追涨，成交后继续观察承接。"
    if score >= 65:
        return "维持观察或持有，不需要因为单日波动频繁交易。"
    return "暂不产生手动委托，等待评分或流动性改善。"


def _trade_plan_flag_set(flags: Any) -> set[str]:
    if flags is None:
        return set()
    text = str(flags).strip()
    if not text or text.lower() in {"none", "nan", "null", "--"}:
        return set()
    return {item.strip() for item in text.replace("，", "|").replace(",", "|").replace("；", "|").replace(";", "|").split("|") if item.strip()}


def _trade_plan_score(row: dict[str, Any]) -> float:
    holding = _manual_trade_number(row.get("holding_score"), math.nan)
    short = _manual_trade_number(row.get("short_score"), math.nan)
    mid_long = _manual_trade_number(row.get("mid_long_score"), math.nan)
    price = _manual_trade_number(row.get("price_factor_score"), math.nan)
    quality = _manual_trade_number(row.get("fundamental_quality_score"), math.nan)
    valuation = _manual_trade_number(row.get("valuation_sanity_score"), math.nan)
    liquidity = _manual_trade_number(row.get("liquidity_capacity_score"), math.nan)
    risk = _manual_trade_number(row.get("risk_control_score"), math.nan)
    completeness = _manual_trade_number(row.get("data_completeness"), math.nan)
    if math.isfinite(completeness) and completeness <= 1.2:
        completeness *= 100

    weighted_parts: list[tuple[float, float]] = []
    for value, weight in [
        (holding, 0.28),
        (short, 0.18),
        (mid_long, 0.22),
        (price, 0.14),
        (quality, 0.08),
        (risk, 0.05),
        (liquidity, 0.03),
        (valuation, 0.01),
        (completeness, 0.01),
    ]:
        if math.isfinite(value):
            weighted_parts.append((value, weight))
    if not weighted_parts:
        return 0.0
    total_weight = sum(weight for _value, weight in weighted_parts)
    score = sum(value * weight for value, weight in weighted_parts) / total_weight

    action = str(row.get("portfolio_action") or row.get("signal_action") or row.get("action") or "")
    if action in {"buy", "add"}:
        score += 2.5
    elif action == "trial_buy":
        score += 1.2
    elif action in {"avoid", "hard_exit", "strategy_clear"}:
        score -= 8.0

    flags = _trade_plan_flag_set(row.get("risk_flags"))
    score -= min(10.0, len(flags.intersection(TRADE_PLAN_SOFT_RISK_FLAGS)) * 1.8)
    if flags.intersection(TRADE_PLAN_HARD_RISK_FLAGS):
        score -= 30.0
    gate = _manual_trade_number(row.get("gate_penalty_score"), 0.0)
    score -= min(7.0, max(gate, 0.0) * 0.45)
    return round(max(0.0, min(100.0, score)), 1)


def _is_hard_exit_candidate(row: dict[str, Any], score: float) -> bool:
    action = str(row.get("portfolio_action") or row.get("signal_action") or row.get("action") or "")
    flags = _trade_plan_flag_set(row.get("risk_flags"))
    return action in TRADE_PLAN_EXIT_ACTIONS or bool(flags.intersection(TRADE_PLAN_HARD_RISK_FLAGS)) or score < 30


def _is_super_core_candidate(row: dict[str, Any], score: float) -> bool:
    profile = str(row.get("stock_profile") or "")
    flags = _trade_plan_flag_set(row.get("risk_flags"))
    short = _manual_trade_number(row.get("short_score"), math.nan)
    mid_long = _manual_trade_number(row.get("mid_long_score"), math.nan)
    liquidity = _manual_trade_number(row.get("liquidity_capacity_score"), math.nan)
    risk = _manual_trade_number(row.get("risk_control_score"), math.nan)
    amount = _manual_trade_number(row.get("amount_yi"), math.nan)
    soft_flags = flags.intersection(TRADE_PLAN_SOFT_RISK_FLAGS.difference({"not_in_current_top_pool"}))
    if profile in {"high_vol_theme", "low_liquidity_watch", "data_limited_growth"}:
        return False
    return (
        score >= 88
        and (not math.isfinite(mid_long) or mid_long >= 85)
        and (not math.isfinite(short) or short >= 78)
        and (not math.isfinite(liquidity) or liquidity >= 68)
        and (not math.isfinite(risk) or risk >= 68)
        and (not math.isfinite(amount) or amount >= 5)
        and not soft_flags
    )


def _dynamic_position_cap(row: dict[str, Any], score: float, held: bool) -> float:
    if _is_hard_exit_candidate(row, score):
        return 0.0
    profile = str(row.get("stock_profile") or "mid_growth")
    flags = _trade_plan_flag_set(row.get("risk_flags"))
    liquidity = _manual_trade_number(row.get("liquidity_capacity_score"), math.nan)
    risk = _manual_trade_number(row.get("risk_control_score"), math.nan)
    amount = _manual_trade_number(row.get("amount_yi"), math.nan)

    if _is_super_core_candidate(row, score):
        cap = 0.30
    elif profile == "large_quality":
        cap = 0.25 if score >= 82 else 0.20
    elif profile in {"mid_growth", "balanced_growth", ""}:
        cap = 0.25 if score >= 86 else 0.20 if score >= 78 else 0.15
    elif profile == "small_elastic":
        cap = 0.18 if score >= 84 else 0.15 if score >= 76 else 0.10
    elif profile == "high_vol_theme":
        cap = 0.12 if score >= 82 else 0.08
    elif profile == "low_liquidity_watch":
        cap = 0.05 if held else 0.0
    elif profile == "data_limited_growth":
        cap = 0.12 if score >= 80 else 0.08
    else:
        cap = 0.20 if score >= 82 else 0.15 if score >= 72 else 0.08

    if flags.intersection({"low_liquidity", "wide_intraday_amplitude", "high_crowding"}):
        cap = min(cap, 0.12)
    if math.isfinite(liquidity) and liquidity < 45:
        cap = min(cap, 0.05 if held else 0.0)
    elif math.isfinite(liquidity) and liquidity < 60:
        cap = min(cap, 0.10)
    if math.isfinite(risk) and risk < 50:
        cap = min(cap, 0.08)
    if math.isfinite(amount) and amount < 2:
        cap = min(cap, 0.06 if held else 0.0)
    return round(max(0.0, min(0.30, cap)), 4)


def _position_role(row: dict[str, Any], score: float, cap: float) -> str:
    if cap <= 0:
        return "退出/不配置"
    if _is_super_core_candidate(row, score) and cap >= 0.30:
        return "超核心"
    if cap >= 0.25:
        return "核心"
    if cap >= 0.15:
        return "标准持仓"
    if cap >= 0.08:
        return "试探仓"
    return "观察仓"


def _desired_weight(row: dict[str, Any], score: float, cap: float, held: bool) -> float:
    if cap <= 0:
        return 0.0
    current_weight = _manual_trade_number(row.get("weight"))
    action = str(row.get("portfolio_action") or row.get("signal_action") or row.get("action") or "")
    if score >= 90:
        desired = 0.25
    elif score >= 86:
        desired = 0.20
    elif score >= 82:
        desired = 0.16
    elif score >= 78:
        desired = 0.12
    elif score >= 70:
        desired = 0.08
    elif held and score >= 60:
        desired = min(max(current_weight, 0.06), 0.08)
    elif held and score >= 48:
        desired = min(max(current_weight * 0.5, 0.05), 0.06)
    else:
        desired = 0.0

    if action in {"buy", "add"} and score >= 82:
        desired = max(desired, 0.15)
    if action == "trial_buy" and score >= 76:
        desired = max(desired, 0.08)
    if not held:
        first_buy_cap = 0.15 if cap >= 0.25 else 0.12 if cap >= 0.15 else 0.08
        desired = min(desired, first_buy_cap)
    return round(min(cap, max(0.0, desired)), 4)


def _candidate_priority(row: dict[str, Any]) -> float:
    score = _manual_trade_number(row.get("optimization_score"))
    held_bonus = 1.5 if row.get("candidate_source") == "current_holding" else 0.0
    action = str(row.get("portfolio_action") or row.get("signal_action") or row.get("action") or "")
    action_bonus = 2.0 if action in {"buy", "add"} else 0.8 if action == "trial_buy" else 0.0
    cap_bonus = _manual_trade_number(row.get("position_cap")) * 10
    return score + held_bonus + action_bonus + cap_bonus


def _target_count_for_account(account_value: float) -> int:
    if account_value <= 100_000:
        return 4
    if account_value <= 500_000:
        return 5
    if account_value <= 2_000_000:
        return 6
    return 8


def _cash_floor_for_plan(rows: list[dict[str, Any]]) -> tuple[float, str]:
    labels = [str(row.get("market_regime_label") or row.get("market_regime_key") or "") for row in rows]
    joined = " ".join(labels)
    if any(keyword in joined for keyword in ["缩量", "防御", "bear", "defensive"]):
        return 0.16, "市场偏防御，保留更高现金缓冲。"
    if any(keyword in joined for keyword in ["高波动", "修复", "volatile", "recovery"]):
        return 0.12, "市场波动偏高，买入采用分批确认。"
    return 0.08, "市场未触发高防御现金约束，保留基础现金缓冲。"


def _load_system_trade_candidates(held_symbols: set[str], limit_per_strategy: int = 30) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for strategy_key in STRATEGY_SIGNAL_CONFIGS:
        signals = load_strategy_signal_view(strategy_key)
        if signals.empty:
            continue
        signals = signals.head(limit_per_strategy)
        for raw in signals.to_dict(orient="records"):
            record = _clean_record(raw)
            symbol = str(record.get("symbol") or "").zfill(6)
            if not symbol or symbol in held_symbols:
                continue
            action = str(record.get("action") or "")
            score = _manual_trade_number(record.get("price_factor_score"), -1)
            if action not in {"buy", "trial_buy", "watch", "hold"} and score < 82:
                continue
            record["symbol"] = symbol
            record["weight"] = 0.0
            record["shares"] = 0.0
            record["latest_close"] = record.get("close")
            record["signal_action"] = action or "watch"
            record["portfolio_action"] = "add" if action in {"buy", "trial_buy"} else "no_new_buy"
            record["candidate_source"] = "system_pool"
            record["source_strategy_keys"] = [strategy_key]
            existing = candidates.get(symbol)
            if existing is None or score > _manual_trade_number(existing.get("price_factor_score"), -1):
                if existing and existing.get("source_strategy_keys"):
                    record["source_strategy_keys"] = sorted(set(existing["source_strategy_keys"]).union({strategy_key}))
                candidates[symbol] = record
            elif existing:
                existing["source_strategy_keys"] = sorted(set(existing.get("source_strategy_keys") or []).union({strategy_key}))
    rows = list(candidates.values())
    if rows:
        rows = _attach_strategy_scores(rows, {str(row.get("symbol") or "").zfill(6) for row in rows})
    return rows


def _build_trade_plan_candidates(advice_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    held_symbols = {str(row.get("symbol") or "").zfill(6) for row in advice_rows}
    rows: list[dict[str, Any]] = []
    for row in advice_rows:
        item = dict(row)
        item["candidate_source"] = "current_holding"
        item["source_strategy_keys"] = ["holding"]
        rows.append(item)
    rows.extend(_load_system_trade_candidates(held_symbols))

    for row in rows:
        held = row.get("candidate_source") == "current_holding"
        score = _trade_plan_score(row)
        cap = _dynamic_position_cap(row, score, held)
        row["optimization_score"] = score
        row["position_cap"] = cap
        row["position_role"] = _position_role(row, score, cap)
        row["desired_weight"] = _desired_weight(row, score, cap, held)
        row["priority_score"] = round(_candidate_priority(row), 2)
    return [_clean_record(row) for row in rows]


def _allocate_target_weights(candidates: list[dict[str, Any]], account_value: float) -> tuple[dict[str, float], dict[str, Any]]:
    cash_floor, cash_note = _cash_floor_for_plan(candidates)
    target_exposure = max(0.65, min(0.92, 1.0 - cash_floor))
    max_count = _target_count_for_account(account_value)
    positive = [
        row
        for row in candidates
        if _manual_trade_number(row.get("desired_weight")) > 0
        and _manual_trade_number(row.get("position_cap")) > 0
        and not _is_hard_exit_candidate(row, _manual_trade_number(row.get("optimization_score")))
    ]
    positive.sort(key=lambda row: _candidate_priority(row), reverse=True)
    selected = positive[:max_count]

    targets = {str(row.get("symbol") or "").zfill(6): 0.0 for row in candidates}
    raw_total = sum(_manual_trade_number(row.get("desired_weight")) for row in selected)
    scale = min(1.0, target_exposure / raw_total) if raw_total > 0 else 1.0
    for row in selected:
        symbol = str(row.get("symbol") or "").zfill(6)
        cap = _manual_trade_number(row.get("position_cap"))
        target = min(cap, _manual_trade_number(row.get("desired_weight")) * scale)
        if row.get("candidate_source") != "current_holding" and target < 0.055:
            target = 0.0
        targets[symbol] = round(target, 4)

    # Existing positions that are not hard-risk exits should not be forced to zero
    # merely because a fresh candidate ranks higher. Keep a small, realistic risk
    # budget for acceptable holdings and let the trade plan reduce gradually.
    for row in candidates:
        symbol = str(row.get("symbol") or "").zfill(6)
        if row.get("candidate_source") != "current_holding" or targets.get(symbol, 0.0) > 0:
            continue
        score = _manual_trade_number(row.get("optimization_score"))
        if _is_hard_exit_candidate(row, score) or score < 55:
            continue
        cap = _manual_trade_number(row.get("position_cap"))
        current_weight = _manual_trade_number(row.get("weight"))
        desired = _manual_trade_number(row.get("desired_weight"))
        keep_weight = min(cap, desired if desired > 0 else 0.06, max(current_weight, 0.06))
        if keep_weight >= 0.045:
            targets[symbol] = round(keep_weight, 4)

    ranked_symbols = sorted(targets, key=lambda symbol: targets[symbol], reverse=True)
    if ranked_symbols and targets[ranked_symbols[0]] > 0.30:
        targets[ranked_symbols[0]] = 0.30
    ranked_symbols = sorted(targets, key=lambda symbol: targets[symbol], reverse=True)
    if len(ranked_symbols) >= 2 and targets[ranked_symbols[0]] + targets[ranked_symbols[1]] > 0.50:
        targets[ranked_symbols[1]] = max(0.0, 0.50 - targets[ranked_symbols[0]])
    ranked_symbols = sorted(targets, key=lambda symbol: targets[symbol], reverse=True)
    top3_sum = sum(targets[symbol] for symbol in ranked_symbols[:3])
    if len(ranked_symbols) >= 3 and top3_sum > 0.65:
        excess = top3_sum - 0.65
        third = ranked_symbols[2]
        targets[third] = max(0.0, targets[third] - excess)

    selected_symbols = {symbol for symbol, target in targets.items() if target > 0}
    summary = {
        "target_exposure": round(sum(targets.values()), 4),
        "cash_floor": round(cash_floor, 4),
        "cash_note": cash_note,
        "target_position_count": len(selected_symbols),
        "max_position_count": max_count,
        "selected_symbols": sorted(selected_symbols),
    }
    return targets, summary


def _trade_plan_reason(row: dict[str, Any], target_weight: float) -> str:
    score = _manual_trade_number(row.get("optimization_score"))
    cap = _manual_trade_number(row.get("position_cap"))
    role = row.get("position_role") or "观察"
    action = str(row.get("portfolio_action") or row.get("signal_action") or row.get("action") or "")
    if target_weight <= 0:
        if _is_hard_exit_candidate(row, score):
            return "触发退出或低分规则，不再占用组合资金。"
        return "未进入本轮最优组合，优先把资金留给更高确定性标的。"
    if role == "超核心":
        return f"超核心候选，评分 {score:.1f}，单票上限允许到 {cap:.0%}，但仍需分批执行。"
    if action in {"buy", "add"}:
        return f"进入最优组合，评分 {score:.1f}，按{role}配置，目标仓位 {target_weight:.0%}。"
    return f"保留在目标组合内，评分 {score:.1f}，按{role}仓位预算管理。"


def _execution_steps(row: dict[str, Any], intent: str, target_weight: float) -> str:
    role = str(row.get("position_role") or "")
    if intent == "buy":
        if role == "超核心":
            return "先买计划量约50%-60%，回踩不破关键位且评分维持高位，再考虑第二笔。"
        return "按计划量分1-2笔限价买入，不追涨，成交后继续观察承接。"
    if intent == "sell":
        if target_weight <= 0:
            return "优先处理退出仓位；若跌停/停牌则改为持续跟踪可成交窗口。"
        return "先降到目标仓位附近，避免一次性卖出造成执行偏差。"
    return "暂不下单，等待评分、盘口或价格触发条件变化。"


def _entry_timing(row: dict[str, Any], intent: str) -> dict[str, Any]:
    score = _manual_trade_number(row.get("optimization_score"), _manual_trade_number(row.get("holding_score"), 0.0))
    alpha = _manual_trade_number(row.get("alpha_score"), score)
    liquidity = _manual_trade_number(row.get("liquidity_capacity_score"), 60.0)
    risk = _manual_trade_number(row.get("risk_control_score"), 60.0)
    close_pos = _manual_trade_number(row.get("intraday_position_pct"), 55.0)
    pct_change = _manual_trade_number(row.get("pct_change"), 0.0)
    turnover = _manual_trade_number(row.get("turnover_rate"), 0.0)
    amplitude = _manual_trade_number(row.get("amplitude_pct_display"), 0.0)
    flags = _trade_plan_flag_set(row.get("risk_flags"))

    timing = score * 0.38 + alpha * 0.20 + liquidity * 0.14 + risk * 0.12 + close_pos * 0.16
    if 1.0 <= pct_change <= 6.5:
        timing += 4.0
    elif pct_change > 8.8:
        timing -= 9.0
    elif pct_change < -3.0:
        timing -= 5.0
    if 1.2 <= turnover <= 12.0:
        timing += 3.5
    elif turnover > 18.0:
        timing -= 4.0
    elif turnover and turnover < 0.8:
        timing -= 5.0
    if amplitude > 12.0:
        timing -= 7.0
    elif 3.0 <= amplitude <= 9.5:
        timing += 2.0
    if flags.intersection({"limit_up_or_hard_to_buy", "wide_intraday_amplitude", "high_crowding"}):
        timing -= 8.0
    if intent != "buy":
        timing = min(timing, score)
    timing = round(max(0.0, min(100.0, timing)), 1)

    if timing >= 82:
        label = "可执行买点"
        rule = "允许按计划首笔限价执行，避免追高超过计划价。"
    elif timing >= 72:
        label = "等待确认"
        rule = "等放量承接、回踩不破或行业强度确认后再执行。"
    elif timing >= 60:
        label = "低吸观察"
        rule = "只接受靠近支撑位的小仓试探，不追涨。"
    else:
        label = "暂不入场"
        rule = "买点质量不足，先观察，不占用现金。"
    return {"score": timing, "label": label, "rule": rule}


def _reward_risk(row: dict[str, Any], target_weight: float) -> dict[str, Any]:
    score = _manual_trade_number(row.get("optimization_score"), _manual_trade_number(row.get("holding_score"), 0.0))
    role = str(row.get("position_role") or "")
    risk_score = _manual_trade_number(row.get("risk_control_score"), 60.0)
    amplitude = _manual_trade_number(row.get("amplitude_pct_display"), 6.0)
    stop_loss = _manual_trade_number(row.get("stop_loss_pct"), 0.0)
    if stop_loss <= 0:
        stop_loss = 0.065 if risk_score >= 70 else 0.08 if risk_score >= 55 else 0.10
    if amplitude > 10:
        stop_loss = max(stop_loss, 0.09)
    if role == "超核心":
        upside = 0.32 if score >= 90 else 0.26
    elif role == "核心":
        upside = 0.24 if score >= 84 else 0.20
    elif role == "标准持仓":
        upside = 0.18 if score >= 78 else 0.14
    elif role == "试探仓":
        upside = 0.12
    else:
        upside = 0.08
    if target_weight <= 0:
        upside = 0.0
    ratio = round(upside / stop_loss, 2) if stop_loss > 0 else None
    if ratio is None or ratio < 1.3:
        label = "赔率不足"
    elif ratio < 2.0:
        label = "赔率一般"
    elif ratio < 3.0:
        label = "赔率较好"
    else:
        label = "高赔率"
    return {
        "upside_pct": round(upside, 4),
        "downside_pct": round(stop_loss, 4),
        "odds_ratio": ratio,
        "label": label,
        "rule": "低于1.5倍赔率不主动新增；2倍以上才值得占用主要资金。",
    }


def _sell_layer(row: dict[str, Any], intent: str, target_weight: float) -> dict[str, Any]:
    action = str(row.get("portfolio_action") or "")
    score = _manual_trade_number(row.get("optimization_score"), _manual_trade_number(row.get("holding_score"), 0.0))
    pnl = _manual_trade_number(row.get("pnl_pct"), 0.0)
    if action in {"hard_exit"}:
        return {"type": "硬风险退出", "rule": "优先卖出，不等待技术修复；若无法成交则每日跟踪可卖窗口。"}
    if action in {"strategy_clear"} or (target_weight <= 0 and intent == "sell"):
        return {"type": "策略性清仓", "rule": "评分、活跃度或组合竞争力不足，资金让位给更强候选。"}
    if pnl >= 0.22 and score < 72:
        return {"type": "盈利保护", "rule": "上涨后评分转弱，优先保护利润，可分批落袋。"}
    if pnl <= -0.08 and score < 60:
        return {"type": "时间/亏损止损", "rule": "亏损且评分未修复，不补仓，先降风险。"}
    if intent == "sell":
        return {"type": "机会替换", "rule": "已有更强候选进入目标组合，降至目标仓位释放现金。"}
    return {"type": "持有跟踪", "rule": "未触发卖出分层，按复盘日期重新评估。"}


def _battle_plan(row: dict[str, Any], target_weight: float, intent: str, ideal_quantity: int) -> dict[str, Any]:
    role = str(row.get("position_role") or "观察")
    entry = _entry_timing(row, intent)
    odds = _reward_risk(row, target_weight)
    initial_weight = 0.0
    if intent == "buy" and target_weight > 0:
        initial_weight = min(target_weight, 0.10 if role in {"核心", "超核心"} else 0.08 if role == "标准持仓" else 0.05)
    add_weight = max(0.0, target_weight - initial_weight)
    return {
        "role": role,
        "first_weight": round(initial_weight, 4),
        "add_weight": round(add_weight, 4),
        "max_weight": round(_manual_trade_number(row.get("position_cap")), 4),
        "ideal_quantity": ideal_quantity,
        "entry_rule": entry["rule"],
        "add_rule": "盈利后加仓：买入后评分维持75以上、回撤不破关键位、成交承接不恶化，才考虑第二笔。",
        "no_average_down_rule": "亏损状态不默认补仓；只有评分重新转强且风险标记消失，才允许重新评估。",
        "review_rule": "买入后5个交易日做第一次复盘，10/20个交易日跟踪是否跑赢系统候选。",
        "stop_rule": f"计划下行风险约 {odds['downside_pct']:.1%}，跌破失效条件先降仓。",
    }


def _scenario_plan(row: dict[str, Any], target_weight: float, intent: str) -> dict[str, Any]:
    role = str(row.get("position_role") or "观察")
    cap = _manual_trade_number(row.get("position_cap"))
    return {
        "bull": {
            "label": "乐观",
            "action": f"若放量突破且评分维持高位，目标可向 {min(cap, max(target_weight, target_weight + 0.05)):.0%} 靠近。",
        },
        "base": {
            "label": "中性",
            "action": f"维持 {target_weight:.0%} 目标仓位，按{role}节奏复盘。",
        },
        "bear": {
            "label": "悲观",
            "action": "若跌破关键趋势位、评分跌破60或风险标记新增，降到观察仓或退出。",
        },
        "current_bias": "进攻" if intent == "buy" else "防守" if intent == "sell" else "等待",
    }


def _market_style_radar(rows: list[dict[str, Any]], optimization: dict[str, Any]) -> dict[str, Any]:
    breadth20 = [_manual_trade_number(row.get("market_breadth_20d"), math.nan) for row in rows]
    breadth60 = [_manual_trade_number(row.get("market_breadth_60d"), math.nan) for row in rows]
    volatility = [_manual_trade_number(row.get("market_volatility_20d"), math.nan) for row in rows]
    amount_trend = [_manual_trade_number(row.get("market_amount_trend"), math.nan) for row in rows]

    def avg(values: list[float]) -> float | None:
        clean = [value for value in values if math.isfinite(value)]
        return round(sum(clean) / len(clean), 4) if clean else None

    b20 = avg(breadth20)
    b60 = avg(breadth60)
    vol = avg(volatility)
    amt = avg(amount_trend)
    if b20 is not None and b20 < 0.42:
        regime = "缩量防御"
        adjustment = "降低追涨和弹性股权重，提高现金底线和流动性门槛。"
    elif vol is not None and vol > 0.035:
        regime = "高波动修复"
        adjustment = "买入分批确认，降低单票首仓，优先风险收益比高的候选。"
    elif b20 is not None and b20 > 0.58 and (amt is None or amt >= 0):
        regime = "放量进攻"
        adjustment = "允许优质候选加快入场，但仍限制追高和拥挤题材。"
    else:
        regime = "震荡轮动"
        adjustment = "强调行业相对强度、回踩买点和持仓竞争。"
    return {
        "regime": regime,
        "breadth_20d": b20,
        "breadth_60d": b60,
        "volatility_20d": vol,
        "amount_trend": amt,
        "cash_floor": optimization.get("cash_floor"),
        "adjustment": adjustment,
    }


def build_manual_trade_plan(
    positions: list[PortfolioPosition],
    account_value: float,
    available_cash: float = 0.0,
    min_trade_amount: float = 3000.0,
    lot_size: int = 100,
) -> dict[str, Any]:
    advice_rows = build_user_advice(positions)
    account_value = max(_manual_trade_number(account_value), 0.0)
    available_cash = max(_manual_trade_number(available_cash), 0.0)
    min_trade_amount = max(_manual_trade_number(min_trade_amount), 0.0)
    lot_size = max(int(lot_size or 100), 1)

    candidates = _build_trade_plan_candidates(advice_rows)
    targets, optimization = _allocate_target_weights(candidates, account_value)
    replacement_candidates = sorted(
        [
            row
            for row in candidates
            if row.get("candidate_source") == "system_pool"
            and _manual_trade_number(targets.get(str(row.get("symbol") or "").zfill(6))) > 0
        ],
        key=lambda row: _candidate_priority(row),
        reverse=True,
    )
    best_replacement = replacement_candidates[0] if replacement_candidates else None
    planned_rows: list[dict[str, Any]] = []
    planned_sell_cash = 0.0
    planned_buy_cash = 0.0
    buy_candidates: list[dict[str, Any]] = []

    sorted_rows = sorted(candidates, key=lambda row: _candidate_priority(row), reverse=True)

    for row in sorted_rows:
        symbol = str(row.get("symbol") or "").zfill(6)
        target_weight = _manual_trade_number(targets.get(symbol))
        latest_price = _manual_trade_number(row.get("latest_close") or row.get("close"))
        current_shares = _manual_trade_number(row.get("shares"))
        current_weight_input = _manual_trade_number(row.get("weight"))
        current_market_value = current_shares * latest_price if current_shares > 0 and latest_price > 0 else 0.0
        current_weight = current_market_value / account_value if current_market_value > 0 and account_value > 0 else current_weight_input
        delta = target_weight - current_weight
        score = _manual_trade_number(row.get("optimization_score"), _manual_trade_number(row.get("holding_score"), -1))
        hard_exit = _is_hard_exit_candidate(row, score)
        intent = "hold"
        if current_weight > 0 and (target_weight <= 0 or hard_exit or delta < -0.018):
            intent = "sell"
        elif target_weight > 0 and delta > 0.025:
            intent = "buy"

        estimated_current_value = account_value * current_weight
        shares_estimated = False
        if current_shares <= 0 and latest_price > 0 and estimated_current_value > 0:
            current_shares = estimated_current_value / latest_price
            shares_estimated = True

        target_amount = account_value * target_weight
        current_amount = current_market_value if current_market_value > 0 else estimated_current_value
        raw_amount = abs(target_amount - current_amount)
        quantity = 0
        ideal_quantity = 0
        ideal_amount = 0.0
        estimated_amount = 0.0
        order_price_hint = "手动核对盘口后限价委托"
        if intent == "sell" and latest_price > 0:
            if hard_exit or target_weight <= 0:
                quantity = int(current_shares)
                order_price_hint = "退出类卖出：优先确认是否跌停/停牌，可分批限价"
            else:
                quantity = _round_down_lot(min(current_shares, raw_amount / latest_price), lot_size)
                order_price_hint = "降仓卖出：按卖一/买一附近限价，避免一次性砸盘"
            estimated_amount = quantity * latest_price
            if estimated_amount >= min_trade_amount or target_weight <= 0:
                planned_sell_cash += estimated_amount
            else:
                quantity = 0
                estimated_amount = 0.0
        elif intent == "buy" and latest_price > 0:
            buy_candidates.append(
                {
                    "row": row,
                    "target_weight": target_weight,
                    "current_weight": current_weight,
                    "delta": delta,
                    "latest_price": latest_price,
                    "current_shares": current_shares,
                    "shares_estimated": shares_estimated,
                    "raw_amount": raw_amount,
                    "target_amount": target_amount,
                    "current_amount": current_amount,
                }
            )
            continue
        elif intent == "buy":
            order_price_hint = "缺少有效最新价，暂不换算委托数量"

        manual_action = "不操作"
        if intent == "sell" and quantity > 0:
            manual_action = "手动卖出"
        elif intent != "hold":
            manual_action = "人工观察"

        entry_timing = _entry_timing(row, intent)
        reward_risk = _reward_risk(row, target_weight)
        sell_layer = _sell_layer(row, intent, target_weight)
        battle_plan = _battle_plan(row, target_weight, intent, ideal_quantity)
        scenario_plan = _scenario_plan(row, target_weight, intent)
        competition_note = "进入或保留目标组合"
        if row.get("candidate_source") == "current_holding" and target_weight <= 0 and best_replacement:
            competition_note = f"被更强候选 {best_replacement.get('name')}({best_replacement.get('symbol')}) 竞争替换"
        elif row.get("candidate_source") == "current_holding" and intent == "sell":
            competition_note = "旧仓降至目标风险预算，释放现金给更高确定性机会"
        elif row.get("candidate_source") == "system_pool" and target_weight > 0:
            competition_note = "系统候选进入目标组合，等待买点和资金确认"

        planned_rows.append(
            _clean_record(
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "intent": intent,
                    "manual_action": manual_action,
                    "portfolio_action": row.get("portfolio_action"),
                    "candidate_source": row.get("candidate_source"),
                    "source_strategy_keys": row.get("source_strategy_keys"),
                    "current_weight": round(current_weight, 4),
                    "input_weight": round(current_weight_input, 4),
                    "target_weight": round(target_weight, 4),
                    "weight_delta": round(delta, 4),
                    "position_cap": row.get("position_cap"),
                    "position_role": row.get("position_role"),
                    "optimization_score": row.get("optimization_score"),
                    "priority_score": row.get("priority_score"),
                    "latest_price": latest_price or None,
                    "quantity": quantity,
                    "ideal_quantity": ideal_quantity,
                    "estimated_amount": round(estimated_amount, 2),
                    "ideal_amount": round(ideal_amount, 2),
                    "raw_target_amount": round(raw_amount, 2),
                    "target_amount": round(target_amount, 2),
                    "current_amount": round(current_amount, 2),
                    "current_shares": round(current_shares, 2) if current_shares else None,
                    "shares_estimated": shares_estimated,
                    "holding_score": row.get("holding_score"),
                    "short_score": row.get("short_score"),
                    "mid_long_score": row.get("mid_long_score"),
                    "pnl_pct": row.get("pnl_pct"),
                    "risk_flags": row.get("risk_flags"),
                    "order_price_hint": order_price_hint,
                    "optimization_reason": _trade_plan_reason(row, target_weight),
                    "execution_steps": _execution_steps(row, intent, target_weight),
                    "entry_timing": entry_timing,
                    "reward_risk": reward_risk,
                    "odds_ratio": reward_risk.get("odds_ratio"),
                    "sell_layer": sell_layer,
                    "battle_plan": battle_plan,
                    "scenario_plan": scenario_plan,
                    "competition_note": competition_note,
                    "note": _manual_trade_note(row, intent, quantity, estimated_amount, min_trade_amount),
                }
            )
        )

    buy_cash_available = available_cash + planned_sell_cash
    buy_candidates.sort(key=lambda item: _candidate_priority(item["row"]), reverse=True)
    for item in buy_candidates:
        row = item["row"]
        target_weight = _manual_trade_number(item["target_weight"])
        current_weight = _manual_trade_number(item["current_weight"])
        latest_price = _manual_trade_number(item["latest_price"])
        raw_amount = _manual_trade_number(item["raw_amount"])
        ideal_quantity = _round_down_lot(raw_amount / latest_price, lot_size) if latest_price > 0 else 0
        ideal_amount = ideal_quantity * latest_price
        spendable = min(raw_amount, buy_cash_available)
        quantity = _round_down_lot(spendable / latest_price, lot_size) if latest_price > 0 else 0
        estimated_amount = quantity * latest_price
        order_price_hint = "新增买入：限价不高于计划价，放量承接确认后再下单"
        if estimated_amount >= min_trade_amount:
            planned_buy_cash += estimated_amount
            buy_cash_available -= estimated_amount
            manual_action = "手动买入"
            execution_steps = _execution_steps(row, "buy", target_weight)
        else:
            quantity = 0
            estimated_amount = 0.0
            manual_action = "待买入观察" if ideal_quantity > 0 else "继续观察"
            order_price_hint = (
                "目标进入组合，但当前可用资金、优先级或最小交易金额限制导致暂不执行"
                if ideal_quantity > 0
                else "目标仓位对应金额不足一手，暂不生成买入委托"
            )
            execution_steps = (
                "当前不下单；若释放资金或评分优先级提升，再按目标数量分批限价。"
                if ideal_quantity > 0
                else "目标仓位对应数量不足一手，先继续观察。"
            )

        entry_timing = _entry_timing(row, "buy")
        reward_risk = _reward_risk(row, target_weight)
        sell_layer = _sell_layer(row, "buy", target_weight)
        battle_plan = _battle_plan(row, target_weight, "buy", ideal_quantity)
        scenario_plan = _scenario_plan(row, target_weight, "buy")
        competition_note = "系统候选进入目标组合，等待买点和资金确认"

        planned_rows.append(
            _clean_record(
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "intent": "buy",
                    "manual_action": manual_action,
                    "portfolio_action": row.get("portfolio_action"),
                    "candidate_source": row.get("candidate_source"),
                    "source_strategy_keys": row.get("source_strategy_keys"),
                    "current_weight": round(current_weight, 4),
                    "input_weight": round(_manual_trade_number(row.get("weight")), 4),
                    "target_weight": round(target_weight, 4),
                    "weight_delta": round(target_weight - current_weight, 4),
                    "position_cap": row.get("position_cap"),
                    "position_role": row.get("position_role"),
                    "optimization_score": row.get("optimization_score"),
                    "priority_score": row.get("priority_score"),
                    "latest_price": latest_price or None,
                    "quantity": quantity,
                    "ideal_quantity": ideal_quantity,
                    "estimated_amount": round(estimated_amount, 2),
                    "ideal_amount": round(ideal_amount, 2),
                    "raw_target_amount": round(raw_amount, 2),
                    "target_amount": round(_manual_trade_number(item["target_amount"]), 2),
                    "current_amount": round(_manual_trade_number(item["current_amount"]), 2),
                    "current_shares": round(_manual_trade_number(item["current_shares"]), 2) if item.get("current_shares") else None,
                    "shares_estimated": item.get("shares_estimated"),
                    "holding_score": row.get("holding_score"),
                    "short_score": row.get("short_score"),
                    "mid_long_score": row.get("mid_long_score"),
                    "pnl_pct": row.get("pnl_pct"),
                    "risk_flags": row.get("risk_flags"),
                    "order_price_hint": order_price_hint,
                    "optimization_reason": _trade_plan_reason(row, target_weight),
                    "execution_steps": execution_steps,
                    "entry_timing": entry_timing,
                    "reward_risk": reward_risk,
                    "odds_ratio": reward_risk.get("odds_ratio"),
                    "sell_layer": sell_layer,
                    "battle_plan": battle_plan,
                    "scenario_plan": scenario_plan,
                    "competition_note": competition_note,
                    "note": _manual_trade_note(row, "buy", quantity, estimated_amount, min_trade_amount),
                }
            )
        )

    planned_rows.sort(
        key=lambda row: _manual_trade_priority(
            str(row.get("intent") or "hold"),
            str(row.get("portfolio_action") or ""),
            _manual_trade_number(row.get("optimization_score"), -1),
            _manual_trade_number(row.get("weight_delta")),
        )
    )
    executable = [row for row in planned_rows if row["manual_action"] in {"手动买入", "手动卖出"}]
    pending_buys = [row for row in planned_rows if row["manual_action"] == "待买入观察"]
    shadow_portfolio = [
        {
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "status": "真实持仓跟踪"
            if row.get("candidate_source") == "current_holding" and _manual_trade_number(row.get("target_weight")) > 0
            else "计划买入"
            if row.get("manual_action") == "手动买入"
            else "影子待买"
            if row.get("manual_action") == "待买入观察"
            else "影子跟踪",
            "target_weight": row.get("target_weight"),
            "position_role": row.get("position_role"),
            "optimization_score": row.get("optimization_score"),
            "entry_timing_score": (row.get("entry_timing") or {}).get("score"),
            "odds_ratio": row.get("odds_ratio"),
            "tracking_rule": "5/10/20个交易日跟踪是否跑赢当前持仓和系统股票池；未执行也记录机会成本。",
        }
        for row in planned_rows
        if _manual_trade_number(row.get("target_weight")) > 0
    ]
    shadow_portfolio = sorted(
        shadow_portfolio,
        key=lambda row: (_manual_trade_number(row.get("target_weight")), _manual_trade_number(row.get("optimization_score"))),
        reverse=True,
    )
    known_position_market_value = sum(
        _manual_trade_number(row.get("latest_price")) * _manual_trade_number(row.get("current_shares"))
        for row in planned_rows
        if row.get("candidate_source") == "current_holding"
        and row.get("current_shares") is not None
        and not row.get("shares_estimated")
    )
    input_position_value_estimate = account_value * sum(
        _manual_trade_number(row.get("input_weight"))
        for row in planned_rows
        if row.get("candidate_source") == "current_holding"
    )
    notes = [
        "这是组合优化后的手动下单计划，不会连接券商，也不会自动委托。",
        "核心股票最高允许到 30%，但仅限超核心候选；新增买入默认分批执行，不一次性打满。",
        "买入前必须人工确认公告、涨跌停、停牌、盘口流动性和个人风险承受能力。",
        "A 股通常按 100 股一手估算；清仓或零股卖出请以券商客户端实际规则为准。",
        optimization.get("cash_note") or "",
    ]
    notes = [note for note in notes if note]
    if known_position_market_value > 0 and input_position_value_estimate > 0:
        diff_ratio = abs(known_position_market_value - input_position_value_estimate) / max(
            known_position_market_value,
            input_position_value_estimate,
        )
        if diff_ratio > 0.15:
            notes.append("持股股数折算市值与输入总资产/仓位偏差较大，请先核对总资产、仓位和股数。")
    if planned_buy_cash > available_cash + planned_sell_cash:
        notes.append("计划买入金额超过可用资金和预计卖出回款，请先减少买入或提高现金。")

    return {
        "ok": True,
        "mode": "optimized_manual_trade_plan",
        "algorithm_version": OPTIMAL_TRADE_PLAN_VERSION,
        "account_value": round(account_value, 2),
        "available_cash": round(available_cash, 2),
        "planned_buy_cash": round(planned_buy_cash, 2),
        "planned_sell_cash": round(planned_sell_cash, 2),
        "cash_after_plan_estimate": round(available_cash + planned_sell_cash - planned_buy_cash, 2),
        "known_position_market_value": round(known_position_market_value, 2),
        "input_position_value_estimate": round(input_position_value_estimate, 2),
        "position_count": len(advice_rows),
        "candidate_count": len(candidates),
        "target_position_count": optimization.get("target_position_count", 0),
        "target_exposure": optimization.get("target_exposure", 0),
        "cash_floor": optimization.get("cash_floor", 0),
        "max_position_count": optimization.get("max_position_count", 0),
        "executable_count": len(executable),
        "pending_buy_count": len(pending_buys),
        "market_style_radar": _market_style_radar(candidates, optimization),
        "shadow_portfolio": shadow_portfolio[:12],
        "playbook": {
            "focus": "买得更少但更准；盈利加仓，亏损不补仓；弱持仓每天与系统候选竞争。",
            "entry_gate": "买入需要同时通过综合评分、买点评分、赔率和资金优先级。",
            "review_loop": "所有手动买入、待买入观察和跳过机会都可记录到复盘表，统计5/10/20日表现。",
        },
        "rows": planned_rows,
        "notes": notes,
    }


def summarize_risk(advice_rows: list[dict[str, Any]], max_single: float = 0.12) -> dict[str, Any]:
    total_weight = sum(float(row.get("weight") or 0) for row in advice_rows)
    max_weight = max((float(row.get("weight") or 0) for row in advice_rows), default=0.0)
    profile_caps = [
        float(row.get("profile_max_single") or 0)
        for row in advice_rows
        if row.get("profile_max_single") is not None
    ]
    profile_cap = max(profile_caps, default=max_single)
    profile_labels = [str(row.get("position_profile_label")) for row in advice_rows if row.get("position_profile_label")]
    target_counts = [str(row.get("target_position_count")) for row in advice_rows if row.get("target_position_count")]
    reduce_actions = {"reduce", "trim_to_risk_budget", "reduce_light", "large_reduce", "strategy_clear", "hard_exit", "exit_or_strong_reduce"}
    reduce_count = sum(1 for row in advice_rows if str(row.get("portfolio_action") or "") in reduce_actions)
    exit_count = sum(1 for row in advice_rows if str(row.get("portfolio_action")) in {"hard_exit", "exit_or_strong_reduce"})
    strategy_clear_count = sum(1 for row in advice_rows if str(row.get("portfolio_action")) == "strategy_clear")
    pnl_values = [float(row["pnl_pct"]) for row in advice_rows if row.get("pnl_pct") is not None]
    average_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else None

    notes: list[str] = []
    if profile_labels and target_counts:
        notes.append(f"账户按{profile_labels[0]}仓位体系评估，建议组合约 {target_counts[0]}。")
    if total_weight > 0.95:
        notes.append("总仓位接近满仓，建议保留现金缓冲以应对高波动。")
    if max_weight > profile_cap:
        notes.append(f"存在单票仓位超过 {profile_cap:.0%} 散户档位上限，需要优先降集中度。")
    if exit_count:
        notes.append(f"有 {exit_count} 只持仓触发硬风险退出。")
    if strategy_clear_count:
        notes.append(f"有 {strategy_clear_count} 只持仓触发策略性清仓。")
    if reduce_count:
        notes.append(f"有 {reduce_count} 只持仓需要降至风险预算。")
    if average_pnl is not None and average_pnl < -0.08:
        notes.append("组合平均浮亏超过 8%，需要检查止损纪律。")
    if not notes:
        notes.append("组合未触发硬性风险阈值，继续按信号变化观察。")

    if exit_count or strategy_clear_count or max_weight > profile_cap * 1.25 or total_weight > 1.1:
        risk_level = "high"
    elif reduce_count or max_weight > profile_cap or total_weight > 0.95:
        risk_level = "medium"
    else:
        risk_level = "controlled"

    return {
        "total_weight": total_weight,
        "position_count": len(advice_rows),
        "max_single_weight": max_weight,
        "reduce_count": reduce_count,
        "exit_count": exit_count,
        "strategy_clear_count": strategy_clear_count,
        "average_pnl_pct": average_pnl,
        "risk_level": risk_level,
        "notes": notes,
    }
