from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from portfolio.account import (
    account_summary,
    load_account,
    mark_to_market,
    new_account,
    save_account,
    simulate_buy,
    simulate_sell_all,
)
from storage.logger import (
    get_cycle,
    get_latest_cycle_payload,
    log_paper_account_snapshot,
    log_paper_order,
    log_paper_trade,
)


logger = logging.getLogger(__name__)


def run_paper_simulation_once(cycle_id: str | None = None) -> dict[str, Any]:
    row = get_cycle(cycle_id) if cycle_id else get_latest_cycle_payload()
    if not row:
        return {
            "status": "skipped_no_cycle",
            "reason": "没有可用于模拟盘的盘中信号日志",
            "no_real_orders": True,
        }

    payload = _loads(row.get("payload_json"))
    final_signal = payload.get("final_signal") if isinstance(payload.get("final_signal"), dict) else {}
    if not final_signal:
        return {
            "status": "skipped_no_signal",
            "cycle_id": row.get("cycle_id"),
            "reason": "最新周期缺少最终信号",
            "no_real_orders": True,
        }

    account = load_account()
    recommendations = list(final_signal.get("recommendations") or [])
    watchlist = list(final_signal.get("watchlist") or [])
    prices = _latest_price_map(recommendations, watchlist) if account.positions or recommendations or watchlist else {}
    mark_to_market(account, prices)

    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signal = str(final_signal.get("signal") or "HOLD").upper()
    source_cycle_id = str(row.get("cycle_id") or "")

    if signal == "SELL":
        sell_orders, sell_trades = simulate_sell_all(
            account,
            cycle_id=source_cycle_id,
            prices=prices,
            reason=str(final_signal.get("no_recommendation_reason") or "系统防守信号，模拟卖出可卖持仓"),
        )
        orders.extend(sell_orders)
        trades.extend(sell_trades)
    elif signal == "BUY":
        for recommendation in recommendations:
            order, trade = simulate_buy(account, recommendation, cycle_id=source_cycle_id)
            orders.append(order)
            if trade:
                trades.append(trade)
    else:
        logger.info("paper simulation keeps account unchanged for signal=%s", signal)

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
        "trade_count": len(trades),
        "no_real_orders": True,
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
        "trades": trades,
        "account": account.to_dict(),
    }


def reset_paper_account() -> dict[str, Any]:
    account = new_account()
    save_account(account)
    summary = {
        **account_summary(account),
        "signal": "RESET",
        "filled_orders": 0,
        "rejected_orders": 0,
        "trade_count": 0,
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
    account = load_account()
    return account.to_dict()


def _latest_price_map(
    recommendations: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
) -> dict[str, float]:
    prices: dict[str, float] = {}
    for item in recommendations + watchlist:
        symbol = str(item.get("symbol") or "")
        price = _float(item.get("current_price") or item.get("price"), 0.0)
        if symbol and price > 0:
            prices[symbol] = price
    try:
        from realtime.market_stream import MarketStream

        for quote in MarketStream().latest_quotes():
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
