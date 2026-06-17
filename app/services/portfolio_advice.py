from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.models import PortfolioPosition, WatchlistItem
from scripts.build_portfolio_advice import build_advice, load_signals


SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
SCORED_UNIVERSE_PATH = PROJECT_ROOT / "data" / "processed" / "scored_universe_latest.csv"


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    return value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in record.items()}


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
            "pct_change",
            "amount_yi",
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
        fallback["reason"] = "未进入当前 institutional_score_v4_tushare 系统股票池；仅使用本地行情估算浮盈亏。"
        fallback["model_version"] = "institutional_score_v4_tushare"
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
        signals = load_signals(signals_path)
    advice = build_advice(portfolio, signals, max_single=max_single, watch_cap=watch_cap)
    return [_clean_record(record) for record in advice.to_dict(orient="records")]


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
                    "pct_change": signal.get("pct_change"),
                    "amount_yi": signal.get("amount_yi"),
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
                    "recommendation_tier": signal.get("recommendation_tier"),
                    "signal_source": signal.get("signal_source") or "watchlist_refresh",
                }
            )
        )
    return rows


def summarize_risk(advice_rows: list[dict[str, Any]], max_single: float = 0.12) -> dict[str, Any]:
    total_weight = sum(float(row.get("weight") or 0) for row in advice_rows)
    max_weight = max((float(row.get("weight") or 0) for row in advice_rows), default=0.0)
    reduce_count = sum(1 for row in advice_rows if "reduce" in str(row.get("portfolio_action") or ""))
    exit_count = sum(1 for row in advice_rows if str(row.get("portfolio_action")) == "exit_or_strong_reduce")
    pnl_values = [float(row["pnl_pct"]) for row in advice_rows if row.get("pnl_pct") is not None]
    average_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else None

    notes: list[str] = []
    if total_weight > 0.95:
        notes.append("总仓位接近满仓，建议保留现金缓冲以应对高波动。")
    if max_weight > max_single:
        notes.append(f"存在单票仓位超过 {max_single:.0%} 上限，需要优先降集中度。")
    if exit_count:
        notes.append(f"有 {exit_count} 只持仓被模型标记为强减/退出。")
    if reduce_count:
        notes.append(f"有 {reduce_count} 只持仓需要降低风险暴露。")
    if average_pnl is not None and average_pnl < -0.08:
        notes.append("组合平均浮亏超过 8%，需要检查止损纪律。")
    if not notes:
        notes.append("组合未触发硬性风险阈值，继续按信号变化观察。")

    if exit_count or max_weight > max_single * 1.8 or total_weight > 1.1:
        risk_level = "high"
    elif reduce_count or max_weight > max_single or total_weight > 0.95:
        risk_level = "medium"
    else:
        risk_level = "controlled"

    return {
        "total_weight": total_weight,
        "position_count": len(advice_rows),
        "max_single_weight": max_weight,
        "reduce_count": reduce_count,
        "exit_count": exit_count,
        "average_pnl_pct": average_pnl,
        "risk_level": risk_level,
        "notes": notes,
    }
