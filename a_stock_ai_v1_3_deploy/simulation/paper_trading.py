from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from portfolio.account import (
    PaperAccount,
    account_summary,
    load_account,
    mark_to_market,
    new_account,
    save_account,
    simulate_buy,
    simulate_sell_all,
)
from app.config import settings
from storage.logger import (
    get_cycle,
    get_latest_cycle_payload,
    log_paper_account_snapshot,
    log_paper_order,
    log_paper_trade,
    paper_account_session_metrics,
    reconcile_paper_account_session,
)


logger = logging.getLogger(__name__)


def run_paper_simulation_once(
    cycle_id: str | None = None,
    final_signal: dict[str, Any] | None = None,
    primary_strategy: dict[str, Any] | None = None,
    market_prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Runs one paper-trading cycle from a stored payload or the current in-memory signal."""
    row: dict[str, Any] | None = None
    multi_strategy: dict[str, Any] = {}
    if final_signal is None:
        row = get_cycle(cycle_id) if cycle_id else get_latest_cycle_payload()
        if not row:
            return {
                "status": "skipped_no_cycle",
                "reason": "没有可用于模拟盘的盘中信号日志",
                "no_real_orders": True,
            }
        payload = _loads(row.get("payload_json"))
        final_signal = payload.get("final_signal") if isinstance(payload.get("final_signal"), dict) else {}
        multi_strategy = payload.get("multi_strategy") if isinstance(payload.get("multi_strategy"), dict) else {}
        primary_strategy = payload.get("primary_strategy") if isinstance(payload.get("primary_strategy"), dict) else {}
    else:
        final_signal = dict(final_signal)
        primary_strategy = dict(primary_strategy or {})

    multi_portfolio = multi_strategy.get("portfolio_signal") if isinstance(multi_strategy.get("portfolio_signal"), dict) else {}
    simulation_source = "legacy_signal"
    if primary_strategy and primary_strategy.get("strategy_id"):
        simulation_source = f"primary_strategy:{primary_strategy['strategy_id']}"
    elif settings.multi_strategy_paper_enabled and multi_strategy.get("mode") in {"shadow", "active"} and multi_portfolio:
        final_signal = multi_portfolio
        simulation_source = "multi_strategy_portfolio"
    if not final_signal:
        return {
            "status": "skipped_no_signal",
            "cycle_id": cycle_id or (row or {}).get("cycle_id"),
            "reason": "最新周期缺少最终信号",
            "no_real_orders": True,
        }

    account = _prepare_account_session(load_account())
    recommendations = list(final_signal.get("recommendations") or [])
    watchlist = list(final_signal.get("watchlist") or [])
    prices = (
        _latest_price_map(
            recommendations,
            watchlist,
            market_prices=market_prices,
            required_symbols=list(account.positions),
        )
        if account.positions or recommendations or watchlist
        else {}
    )
    mark_to_market(account, prices)

    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    skipped_orders: list[dict[str, Any]] = []
    signal = str(final_signal.get("signal") or "HOLD").upper()
    source_cycle_id = str(cycle_id or (row or {}).get("cycle_id") or "")

    if signal == "SELL":
        sell_orders, sell_trades = simulate_sell_all(
            account,
            cycle_id=source_cycle_id,
            prices=prices,
            reason=str(final_signal.get("no_recommendation_reason") or "系统防守信号，模拟卖出可卖持仓"),
        )
        skipped_orders.extend(order for order in sell_orders if order.get("status") == "skipped")
        orders.extend(order for order in sell_orders if order.get("status") != "skipped")
        trades.extend(sell_trades)
    elif signal == "BUY":
        for recommendation in recommendations:
            order, trade = simulate_buy(account, recommendation, cycle_id=source_cycle_id)
            if order.get("status") == "skipped":
                skipped_orders.append(order)
            else:
                orders.append(order)
            if trade:
                trades.append(trade)
    else:
        logger.info("paper simulation keeps account unchanged for signal=%s", signal)

    execution_context = _ai_execution_context(final_signal)
    for event in orders + skipped_orders + trades:
        event["account_id"] = account.account_id
    if execution_context:
        for order in orders + skipped_orders:
            order.update(execution_context)
        for trade in trades:
            trade.update(execution_context)

    mark_to_market(account, prices)
    save_account(account)
    for order in orders:
        log_paper_order(order)
    for trade in trades:
        log_paper_trade(trade)

    summary = {
        **account_summary(account),
        "signal": signal,
        "filled_orders": sum(1 for order in orders if order.get("status") == "filled"),
        "rejected_orders": sum(1 for order in orders if order.get("status") == "rejected"),
        "skipped_orders": len(skipped_orders),
        "skipped_order_reasons": _skip_reason_summary(skipped_orders),
        "trade_count": len(trades),
        "paper_account_id": account.account_id,
        "paper_session_started_at": account.session_started_at,
        "session_metrics": paper_account_session_metrics(account.account_id),
        "simulation_source": simulation_source,
        "no_real_orders": True,
        **execution_context,
    }
    snapshot_id = uuid4().hex
    log_paper_account_snapshot(
        snapshot_id=snapshot_id,
        cycle_id=source_cycle_id,
        status="ok",
        account=account.to_dict(),
        orders=orders,
        trades=trades,
        summary=summary,
    )
    return {
        "status": "ok",
        "snapshot_id": snapshot_id,
        "cycle_id": source_cycle_id,
        "summary": summary,
        "orders": orders,
        "skipped_orders": skipped_orders,
        "trades": trades,
        "account": account.to_dict(),
        "execution_context": execution_context,
    }


def reset_paper_account() -> dict[str, Any]:
    account = new_account()
    save_account(account)
    summary = {
        **account_summary(account),
        "signal": "RESET",
        "filled_orders": 0,
        "rejected_orders": 0,
        "skipped_orders": 0,
        "trade_count": 0,
        "paper_account_id": account.account_id,
        "paper_session_started_at": account.session_started_at,
        "no_real_orders": True,
    }
    snapshot_id = uuid4().hex
    log_paper_account_snapshot(
        snapshot_id=snapshot_id,
        cycle_id=None,
        status="reset",
        account=account.to_dict(),
        orders=[],
        trades=[],
        summary=summary,
    )
    return {
        "status": "reset",
        "snapshot_id": snapshot_id,
        "summary": summary,
        "account": account.to_dict(),
    }


def current_paper_account() -> dict[str, Any]:
    # The dashboard mounts stock storage read-only. Session reconciliation is
    # therefore performed by the writable simulation worker, never by readers.
    account = load_account()
    payload = account.to_dict()
    payload["session_metrics"] = paper_account_session_metrics(account.account_id)
    return payload


def reconcile_current_paper_account_session() -> dict[str, Any]:
    """Run the one-time stock-session migration from a writable engine process."""
    account = _prepare_account_session(load_account())
    save_account(account)
    payload = account.to_dict()
    payload["session_metrics"] = paper_account_session_metrics(account.account_id)
    return payload


def _prepare_account_session(account: PaperAccount) -> PaperAccount:
    """Reconcile stock-only legacy logs after a reset without touching ETF state."""
    effective_start = reconcile_paper_account_session(account.account_id, account.session_started_at)
    if effective_start and effective_start != account.session_started_at:
        account.session_started_at = effective_start
        save_account(account)
    return account


def _skip_reason_summary(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], int] = {}
    for order in orders:
        key = (str(order.get("skip_kind") or "other"), str(order.get("reason") or "未执行"))
        grouped[key] = grouped.get(key, 0) + 1
    return [
        {"kind": kind, "reason": reason, "count": count}
        for (kind, reason), count in sorted(grouped.items())
    ]


def _ai_execution_context(final_signal: dict[str, Any]) -> dict[str, Any]:
    if not bool(final_signal.get("ai_degraded")):
        return {}
    return {
        "ai_degraded": True,
        "ai_execution_mode": str(final_signal.get("ai_execution_mode") or "hybrid_alpha_rules_only"),
        "ai_execution_chain": str(
            final_signal.get("ai_execution_chain")
            or "deepseek_then_minimax_then_stepfun_then_hybrid_alpha_rules"
        ),
        "ai_provider": str(final_signal.get("ai_provider") or "unavailable"),
        "ai_error": str(final_signal.get("ai_error") or ""),
    }


def _latest_price_map(
    recommendations: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
    market_prices: dict[str, float] | None = None,
    required_symbols: list[str] | None = None,
) -> dict[str, float]:
    prices: dict[str, float] = {}
    for item in recommendations + watchlist:
        symbol = str(item.get("symbol") or "")
        price = _float(item.get("current_price") or item.get("price"), 0.0)
        if symbol and price > 0:
            prices[symbol] = price
    for symbol, price in (market_prices or {}).items():
        value = _float(price, 0.0)
        if symbol and value > 0:
            prices[str(symbol)] = value
    try:
        from realtime.market_stream import MarketStream

        missing = [
            str(symbol)
            for symbol in (required_symbols or [])
            if str(symbol) and str(symbol) not in prices
        ]
        if market_prices is not None:
            quotes = MarketStream().quotes_for_symbols(missing) if missing else []
        else:
            quotes = MarketStream().latest_quotes()
        for quote in quotes:
            if quote.price > 0:
                prices[quote.symbol] = quote.price
    except Exception as exc:
        logger.warning("paper simulation realtime mark-to-market failed: %s", exc)
    return prices


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
