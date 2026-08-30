from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any
from uuid import uuid4

from app.config import settings
from scheduler.trading_calendar import BEIJING_TZ
from storage.logger import fetch_backtest_decisions, fetch_multi_strategy_backtest_decisions, log_backtest_run


logger = logging.getLogger(__name__)


def run_signal_replay_backtest(
    lookback_days: int | None = None,
    holding_days: int | None = None,
    multi_strategy: bool = False,
) -> dict[str, Any]:
    run_id = uuid4().hex
    days = lookback_days or settings.backtest_default_days
    hold_days = holding_days or settings.backtest_holding_days
    decisions = fetch_multi_strategy_backtest_decisions(days) if multi_strategy else fetch_backtest_decisions(days)
    strategy_name = "v1_9_multi_strategy_signal_replay" if multi_strategy else "v1_6_signal_replay"
    cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    try:
        client = None
        for row in decisions:
            if client is None:
                from data.tushare_client import TushareClient

                client = TushareClient()
            payload = _loads(row.get("payload_json"))
            symbol = str(row.get("symbol") or payload.get("symbol") or "")
            entry_price = _float(payload.get("current_price") or payload.get("price"), 0.0)
            created_at = str(row.get("created_at") or "")
            if not symbol or entry_price <= 0 or not created_at:
                skipped.append({"symbol": symbol, "reason": "missing_symbol_price_or_time"})
                continue

            start_date, end_date, signal_date = _bar_window(created_at, hold_days)
            key = (symbol, start_date, end_date)
            if key not in cache:
                try:
                    cache[key] = client.fetch_daily_bars(symbol, start_date, end_date)
                except Exception as exc:
                    logger.warning("backtest daily bar fetch failed for %s: %s", symbol, exc)
                    cache[key] = []
            trade = _simulate_trade(row, payload, cache[key], entry_price, signal_date, hold_days)
            if trade:
                trades.append(trade)
            else:
                skipped.append({"symbol": symbol, "reason": "no_mature_future_bar", "signal_date": signal_date})

        metrics = _build_metrics(trades, len(decisions), len(skipped))
        status = "ok" if trades else "skipped_no_trades"
        log_backtest_run(
            run_id=run_id,
            strategy_name=strategy_name,
            status=status,
            lookback_days=days,
            holding_days=hold_days,
            metrics=metrics,
            trades=trades,
        )
        return {
            "run_id": run_id,
            "status": status,
            "lookback_days": days,
            "holding_days": hold_days,
            "multi_strategy": multi_strategy,
            "metrics": metrics,
            "trades": trades,
            "skipped": skipped[:50],
            "no_real_orders": True,
        }
    except Exception as exc:
        logger.exception("signal replay backtest failed")
        metrics = {
            "sample_count": len(decisions),
            "trade_count": len(trades),
            "skipped_count": len(skipped),
        }
        log_backtest_run(
            run_id=run_id,
            strategy_name=strategy_name,
            status="failed",
            lookback_days=days,
            holding_days=hold_days,
            metrics=metrics,
            trades=trades,
            error_text=str(exc),
        )
        return {
            "run_id": run_id,
            "status": "failed",
            "error": str(exc),
            "metrics": metrics,
            "multi_strategy": multi_strategy,
            "no_real_orders": True,
        }


def _simulate_trade(
    row: dict[str, Any],
    payload: dict[str, Any],
    bars: list[dict[str, Any]],
    raw_entry_price: float,
    signal_date: str,
    holding_days: int,
) -> dict[str, Any] | None:
    future_bars = [
        bar
        for bar in sorted(bars, key=lambda item: str(item.get("trade_date") or ""))
        if str(bar.get("trade_date") or "") > signal_date and _float(bar.get("close"), 0.0) > 0
    ][:holding_days]
    if not future_bars:
        return None

    entry_price = raw_entry_price * (1 + settings.paper_slippage_pct)
    stop_loss = _float(payload.get("stop_loss"), 0.0)
    exit_bar = future_bars[-1]
    exit_price = _float(exit_bar.get("close"), 0.0)
    exit_reason = "time_exit"
    for bar in future_bars:
        low = _float(bar.get("low"), 0.0)
        if stop_loss > 0 and low > 0 and low <= stop_loss:
            exit_bar = bar
            exit_price = stop_loss
            exit_reason = "stop_loss"
            break

    exit_price = exit_price * (1 - settings.paper_slippage_pct)
    gross_return = exit_price / entry_price - 1
    cost_drag = settings.paper_commission_rate * 2 + settings.paper_stamp_duty_rate
    net_return = gross_return - cost_drag
    allocation = min(
        max(_float(payload.get("position") or payload.get("target_weight"), settings.paper_max_position_pct), 0.0),
        settings.paper_max_position_pct,
        settings.max_position_weight,
    )
    capital_return = net_return * allocation

    return {
        "cycle_id": row.get("cycle_id"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "signal_time": row.get("created_at"),
        "signal_date": signal_date,
        "entry_price": round(entry_price, 4),
        "raw_entry_price": round(raw_entry_price, 4),
        "exit_trade_date": exit_bar.get("trade_date"),
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "holding_bars": len(future_bars),
        "stop_loss": round(stop_loss, 4),
        "net_return_pct": round(net_return * 100, 4),
        "gross_return_pct": round(gross_return * 100, 4),
        "capital_return_pct": round(capital_return * 100, 4),
        "allocation": round(allocation, 4),
        "win": net_return > 0,
        "rank_score": _float(payload.get("rank_score"), 0.0),
        "confidence": _float(payload.get("confidence"), 0.0),
        "sentiment_score": _float(payload.get("sentiment_score"), 0.0),
        "leader_strength": _float(payload.get("leader_strength"), 0.0),
        "quality_score": _float(payload.get("quality_score"), 0.0),
        "tradability_score": _float(payload.get("tradability_score"), 0.0),
    }


def _build_metrics(
    trades: list[dict[str, Any]],
    sample_count: int,
    skipped_count: int,
) -> dict[str, Any]:
    if not trades:
        return {
            "sample_count": sample_count,
            "trade_count": 0,
            "skipped_count": skipped_count,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "median_return_pct": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "stop_rate": 0.0,
            "profit_factor": 0.0,
        }

    returns = [float(item["net_return_pct"]) for item in trades]
    capital_returns = [float(item["capital_return_pct"]) / 100 for item in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    equity_curve = _equity_curve(capital_returns)
    max_drawdown = _max_drawdown(equity_curve)
    total_return = (equity_curve[-1] - 1) * 100
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "sample_count": sample_count,
        "trade_count": len(trades),
        "skipped_count": skipped_count,
        "win_rate": round(len(wins) / len(trades), 4),
        "avg_return_pct": round(mean(returns), 4),
        "median_return_pct": round(median(returns), 4),
        "best_return_pct": round(max(returns), 4),
        "worst_return_pct": round(min(returns), 4),
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "stop_rate": round(
            sum(1 for item in trades if item.get("exit_reason") == "stop_loss") / len(trades),
            4,
        ),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else 0.0,
    }


def _bar_window(created_at: str, holding_days: int) -> tuple[str, str, str]:
    created = datetime.fromisoformat(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=BEIJING_TZ)
    local_created = created.astimezone(BEIJING_TZ)
    start = local_created - timedelta(days=1)
    end = min(datetime.now(BEIJING_TZ), local_created + timedelta(days=max(holding_days * 3, holding_days + 10)))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), local_created.strftime("%Y%m%d")


def _equity_curve(capital_returns: list[float]) -> list[float]:
    equity = 1.0
    curve = [equity]
    for value in capital_returns:
        equity *= 1 + value
        curve.append(equity)
    return curve


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0] if curve else 1.0
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
