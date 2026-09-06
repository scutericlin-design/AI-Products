"""Position-aware paper execution rules for the stock strategy only."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

from app.config import settings
from portfolio.account import (
    PaperAccount,
    PaperPosition,
    _commission,
    _lot_floor,
    _max_affordable_quantity,
    mark_to_market,
    reentry_blocked,
)
from scheduler.trading_calendar import BEIJING_TZ


def build_execution_plan(
    account: PaperAccount,
    final_signal: dict[str, Any],
    prices: dict[str, float],
    *,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Convert a market signal into position-level paper orders.

    A broad intraday SELL is a risk alert, not an instruction to liquidate every
    holding. Immediate exits remain available for hard loss and trailing-stop
    events; a market exit needs a strong risk confirmation and a mature lot.
    """
    today = as_of_date or datetime.now(BEIJING_TZ).date()
    if not settings.paper_disciplined_execution_enabled:
        plan = _legacy_plan(final_signal)
        plan["buy_recommendations"], plan["buy_skips"] = _buy_candidates(account, final_signal, prices, today)
        return plan

    exits: dict[str, str] = {}
    holds: list[dict[str, Any]] = []
    for symbol, position in account.positions.items():
        price = _positive_float(prices.get(symbol), 0.0)
        decision = _exit_decision(position, price, final_signal, today)
        if decision:
            exits[symbol] = decision
        else:
            holds.append(
                {
                    "symbol": symbol,
                    "held_trading_days": _held_trading_days(position.entry_date, today),
                    "reason": "未触发逐仓位退出条件，保持持仓" if price > 0 else "缺少新鲜有效行情，不执行退出",
                }
            )

    buys, buy_skips = _buy_candidates(account, final_signal, prices, today, excluded_symbols=set(exits))
    return {
        "version": "v2_position_aware",
        "enabled": True,
        "buy_recommendations": buys,
        "sell_symbols": sorted(exits),
        "sell_reasons": exits,
        "hold_decisions": holds,
        "buy_skips": buy_skips,
        "policy_flags": [
            "position_aware_exit",
            "minimum_holding_period",
            "exposure_budget",
            "rebalance_threshold",
            "same_day_reentry_cooldown",
            "fresh_execution_prices",
            "cash_reservation",
        ],
    }


def _legacy_plan(final_signal: dict[str, Any]) -> dict[str, Any]:
    signal = str(final_signal.get("signal") or "HOLD").upper()
    return {
        "version": "legacy",
        "enabled": False,
        "buy_recommendations": list(final_signal.get("recommendations") or []) if signal == "BUY" else [],
        "sell_symbols": None if signal == "SELL" else [],
        "sell_reasons": {},
        "hold_decisions": [],
        "buy_skips": [],
        "policy_flags": ["legacy_account_wide_sell"],
    }


def _exit_decision(
    position: PaperPosition,
    price: float,
    signal: dict[str, Any],
    today: date,
) -> str | None:
    cost = _positive_float(position.avg_cost, 0.0)
    if _positive_float(price, 0.0) <= 0 or cost <= 0 or position.available_quantity <= 0:
        return None
    return_pct = price / cost - 1
    if return_pct <= -settings.paper_policy_hard_stop_loss_pct:
        return "硬止损触发：单票跌幅达到风险上限"

    high_watermark = max(
        _positive_float(position.high_watermark, 0.0), _positive_float(position.last_price, 0.0), cost
    )
    peak_return = high_watermark / cost - 1
    if (
        peak_return >= settings.paper_policy_trail_activation_pct
        and price <= high_watermark * (1 - settings.paper_policy_trailing_stop_pct)
    ):
        return "移动止盈触发：强势股回撤超过锁利阈值"

    if _held_trading_days(position.entry_date, today) < settings.paper_policy_min_holding_days:
        return None
    if _confirmed_market_exit(signal):
        return "市场风险确认：持有期已满足，执行防守退出"
    return None


def _confirmed_market_exit(signal: dict[str, Any]) -> bool:
    state = signal.get("state") if isinstance(signal.get("state"), dict) else {}
    sentiment = signal.get("market_sentiment") if isinstance(signal.get("market_sentiment"), dict) else {}
    market_state = str(state.get("state") or "").upper()
    permission = str(sentiment.get("trade_permission") or "").upper()
    panic = _positive_float(sentiment.get("panic_score"), 0.0)
    return (
        str(signal.get("signal") or "").upper() == "SELL"
        and market_state == "DOWNTREND"
        and permission == "NO_BUY"
        and panic >= settings.paper_policy_exit_panic_score
    )


def _buy_candidates(
    account: PaperAccount,
    signal: dict[str, Any],
    prices: dict[str, float],
    today: date,
    *,
    excluded_symbols: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if str(signal.get("signal") or "HOLD").upper() != "BUY":
        return [], []

    account = deepcopy(account)
    summary = mark_to_market(account, prices)
    equity = max(_positive_float(summary.get("equity"), 0.0), 0.0)
    if equity <= 0:
        return [], [{"reason": "账户权益无效，跳过新增仓位"}]
    invested = max(_positive_float(summary.get("market_value"), 0.0), 0.0)
    cash_remaining = _positive_float(account.cash, 0.0)
    disciplined = settings.paper_disciplined_execution_enabled
    exposure_limit = settings.paper_policy_max_total_exposure if disciplined else 1.0
    capacity = max(0.0, equity * exposure_limit - invested)
    slots = max(settings.paper_policy_max_names - len(account.positions), 0)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()

    candidates = sorted(
        (dict(item) for item in signal.get("recommendations") or [] if isinstance(item, dict)),
        key=lambda item: _positive_float(item.get("rank_score"), 0.0),
        reverse=True,
    )
    for item in candidates:
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol in seen:
            skipped.append({"symbol": symbol, "reason": "同一周期重复标的"})
            continue
        seen.add(symbol)
        if symbol in (excluded_symbols or set()) or reentry_blocked(account, symbol, today):
            skipped.append({"symbol": symbol, "reason": "退出当日不重新买入"})
            continue
        price = _positive_float(prices.get(symbol), 0.0)
        if not symbol or price <= 0:
            skipped.append({"symbol": symbol, "reason": "缺少有效执行价格"})
            continue
        if not item.get("can_buy", True) or item.get("is_limit_up") or item.get("near_limit_up"):
            skipped.append({"symbol": symbol, "reason": "标的不可买入"})
            continue
        fill_price = round(price * (1 + settings.paper_slippage_pct), 4)
        max_buy_price = _positive_float(item.get("max_buy_price"), 0.0)
        if (
            not isfinite(fill_price) or fill_price <= 0
            or ("max_buy_price" in item and (max_buy_price <= 0 or fill_price > max_buy_price))
        ):
            skipped.append({"symbol": symbol, "reason": "执行价无效或含滑点价格超过最高追价"})
            continue
        position = account.positions.get(symbol)
        if disciplined and position and position.last_trade_date == today.isoformat():
            skipped.append({"symbol": symbol, "reason": "同一交易日不重复加仓"})
            continue
        if disciplined and not position and slots <= 0:
            skipped.append({"symbol": symbol, "reason": "组合持仓数量已达上限"})
            continue
        if position and (_positive_float(position.avg_cost, 0.0) <= 0 or position.quantity <= 0):
            skipped.append({"symbol": symbol, "reason": "现有持仓成本或数量无效"})
            continue
        current_value = position.quantity * price if position else 0.0
        requested_weight = min(
            _positive_float(item.get("position", item.get("target_weight")), 0.0),
            settings.paper_policy_max_position_pct if disciplined else settings.paper_max_position_pct,
            settings.paper_max_position_pct,
            settings.max_position_weight,
        )
        target_value = equity * requested_weight
        desired_add = max(target_value - current_value, 0.0)
        affordable_quantity = _max_affordable_quantity(cash_remaining, fill_price)
        allowed_add = min(desired_add, capacity, affordable_quantity * fill_price)
        if allowed_add <= 0 or (disciplined and allowed_add / equity < settings.paper_policy_min_rebalance_delta_pct):
            skipped.append({"symbol": symbol, "reason": "调仓差额低于组合再平衡阈值"})
            continue
        quantity = min(_lot_floor(allowed_add / fill_price), affordable_quantity)
        if quantity <= 0:
            skipped.append({"symbol": symbol, "reason": "可用预算不足一手"})
            continue
        amount = round(quantity * fill_price, 4)
        reserved_cash = amount + _commission(amount)
        adjusted = dict(item)
        adjusted["symbol"] = symbol
        adjusted["current_price"] = price
        adjusted["position"] = (current_value + allowed_add) / equity
        if "target_weight" in adjusted:
            adjusted["target_weight"] = adjusted["position"]
        adjusted["execution_policy"] = "v2_position_aware" if disciplined else "legacy"
        adjusted["execution_quantity_cap"] = quantity
        adjusted["execution_reserved_cash"] = reserved_cash
        selected.append(adjusted)
        capacity -= allowed_add
        cash_remaining = max(0.0, cash_remaining - reserved_cash)
        if not position:
            slots -= 1
        if capacity <= 0:
            break
    return selected, skipped


def _held_trading_days(entry_date: str, today: date) -> int:
    try:
        start = date.fromisoformat(str(entry_date)[:10])
    except ValueError:
        return 0
    if start >= today:
        return 0
    count = 0
    current = start + timedelta(days=1)
    while current <= today:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def _positive_float(value: Any, default: float) -> float:
    try:
        number = float(value)
        return number if isfinite(number) and number > 0 else default
    except (TypeError, ValueError, OverflowError):
        return default
