from __future__ import annotations

from datetime import datetime
from math import floor
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.storage import QGARPStore


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
ACCOUNT_ID = "qgarp_alpha_paper"


class QGARPPaperRunner:
    """Local-only daily paper broker. No broker SDK is imported or callable here."""

    def __init__(self, settings: QGARPSettings, store: QGARPStore):
        self.settings = settings
        self.store = store

    def run(self, plan: dict[str, Any], trade_date: str | None = None) -> dict[str, Any]:
        date = trade_date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        compact = date.replace("-", "")
        account, positions = self.store.load_account(ACCOUNT_ID, self.settings.paper_initial_cash, date)
        orders: list[dict[str, Any]] = []
        self._mark_positions(positions, plan)

        # Hard exits are evaluated before any new entry. Same-day purchases remain unavailable to sell (T+1).
        for symbol, position in list(positions.items()):
            price = float(position.get("last_price") or 0)
            stop = float(position.get("stop_loss") or 0)
            take = float(position.get("take_profit") or 0)
            side = "SELL" if price > 0 and ((stop > 0 and price <= stop) or (take > 0 and price >= take)) else ""
            if side:
                reason = "Q-GARP硬止损" if price <= stop else "Q-GARP止盈"
                order = self._sell(account, positions, position, price, date, f"{compact}:{side}:{symbol}:{reason}", reason)
                if order:
                    orders.append(order)

        existing = set(positions)
        for candidate in list(plan.get("recommendations") or []):
            if str(candidate.get("action") or "").upper() != "BUY":
                continue
            symbol = str(candidate.get("symbol") or "")
            if not symbol or symbol in existing:
                continue
            price = float(candidate.get("trigger_price") or candidate.get("current_price") or 0)
            if price <= 0:
                continue
            order = self._buy(account, positions, candidate, price, date, f"{compact}:BUY:{symbol}")
            if order:
                orders.append(order)
                existing.add(symbol)

        self.store.save_account_state(account, positions, status="filled" if orders else "no_fill")
        equity = _equity(account, positions)
        return {"status": "ok", "mode": "qgarp_local_paper_only", "no_real_orders": True, "orders": orders, "account": {"cash": account["cash"], "equity": equity, "positions": list(positions.values())}, "plan": plan}

    def _mark_positions(self, positions: dict[str, dict[str, Any]], plan: dict[str, Any]) -> None:
        prices = {str(item.get("symbol")): float(item.get("current_price") or item.get("trigger_price") or 0) for item in list(plan.get("recommendations") or [])}
        for symbol, position in positions.items():
            price = prices.get(symbol, 0)
            if price <= 0:
                bars = self.store.bars(symbol, str(plan.get("as_of") or "") or None, 1)
                price = bars[-1].close if bars else 0
            if price > 0:
                position["last_price"] = price

    def _buy(self, account: dict[str, Any], positions: dict[str, dict[str, Any]], candidate: dict[str, Any], price: float, date: str, dedupe_key: str) -> dict[str, Any] | None:
        if self.store.has_order(dedupe_key):
            return None
        execution = price * (1 + self.settings.paper_slippage_pct)
        equity = _equity(account, positions)
        target_weight = min(float(candidate.get("target_weight") or 0), self.settings.paper_max_position_pct)
        budget = min(account["cash"], equity * target_weight)
        quantity = _affordable_quantity(account["cash"], execution, budget, self.settings)
        if quantity <= 0:
            return None
        amount = quantity * execution
        commission = _commission(amount, self.settings)
        account["cash"] = round(account["cash"] - amount - commission, 4)
        symbol = str(candidate["symbol"])
        positions[symbol] = {"symbol": symbol, "name": str(candidate.get("name") or symbol), "industry": str(candidate.get("industry") or "未分类"), "quantity": quantity, "available_quantity": 0, "avg_cost": round((amount + commission) / quantity, 6), "last_price": price, "last_trade_date": date, "stop_loss": float(candidate.get("stop_loss") or 0), "take_profit": float(candidate.get("take_profit") or 0), "trailing_stop": float(candidate.get("trailing_stop_pct") or 0), "strategy_reason": str(candidate.get("reasoning") or "")}
        order = _order("BUY", candidate, quantity, execution, commission, 0.0, dedupe_key, "Q-GARP综合评分触发")
        trade = _trade(order, amount, commission, 0.0, 0.0)
        self.store.save_account_state(account, positions, order, trade, status="filled")
        return order

    def _sell(self, account: dict[str, Any], positions: dict[str, dict[str, Any]], position: dict[str, Any], price: float, date: str, dedupe_key: str, reason: str) -> dict[str, Any] | None:
        if self.store.has_order(dedupe_key):
            return None
        quantity = min(int(position.get("quantity") or 0), int(position.get("available_quantity") or 0))
        if quantity <= 0:
            return None
        execution = price * (1 - self.settings.paper_slippage_pct)
        amount = quantity * execution
        commission = _commission(amount, self.settings)
        tax = amount * self.settings.paper_stamp_duty_rate
        account["cash"] = round(account["cash"] + amount - commission - tax, 4)
        pnl = amount - commission - tax - quantity * float(position.get("avg_cost") or 0)
        account["realized_pnl"] = round(float(account.get("realized_pnl") or 0) + pnl, 4)
        symbol = str(position["symbol"])
        del positions[symbol]
        candidate = {"symbol": symbol, "name": position.get("name"), "trigger_price": price, "target_weight": 0.0, "stop_loss": position.get("stop_loss"), "take_profit": position.get("take_profit"), "reasoning": position.get("strategy_reason", "")}
        order = _order("SELL", candidate, quantity, execution, commission, tax, dedupe_key, reason)
        trade = _trade(order, amount, commission, tax, pnl)
        self.store.save_account_state(account, positions, order, trade, status="filled")
        return order


def _affordable_quantity(cash: float, price: float, budget: float, settings: QGARPSettings) -> int:
    if price <= 0 or budget <= 0:
        return 0
    quantity = floor(budget / price / settings.paper_lot_size) * settings.paper_lot_size
    while quantity > 0:
        amount = quantity * price
        if amount + _commission(amount, settings) <= cash:
            return quantity
        quantity -= settings.paper_lot_size
    return 0


def _commission(amount: float, settings: QGARPSettings) -> float:
    return round(max(amount * settings.paper_commission_rate, settings.paper_min_commission), 4)


def _equity(account: dict[str, Any], positions: dict[str, dict[str, Any]]) -> float:
    return round(float(account.get("cash") or 0) + sum(float(item.get("quantity") or 0) * float(item.get("last_price") or 0) for item in positions.values()), 4)


def _order(side: str, candidate: dict[str, Any], quantity: int, price: float, commission: float, tax: float, dedupe_key: str, reason: str) -> dict[str, Any]:
    return {"order_id": uuid4().hex, "dedupe_key": dedupe_key, "side": side, "symbol": str(candidate.get("symbol") or ""), "name": str(candidate.get("name") or candidate.get("symbol") or ""), "status": "filled", "quantity": quantity, "price": round(price, 4), "commission": commission, "tax": round(tax, 4), "trigger_price": candidate.get("trigger_price"), "target_weight": candidate.get("target_weight"), "stop_loss": candidate.get("stop_loss"), "take_profit": candidate.get("take_profit"), "reason": f"{reason}；{candidate.get('reasoning') or ''}", "created_at": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"), "strategy_id": "qgarp_alpha"}


def _trade(order: dict[str, Any], amount: float, fee: float, tax: float, pnl: float) -> dict[str, Any]:
    return {"trade_id": uuid4().hex, "order_id": order["order_id"], "side": order["side"], "symbol": order["symbol"], "quantity": order["quantity"], "price": order["price"], "amount": amount, "fee": fee, "tax": tax, "realized_pnl": pnl, "created_at": order["created_at"]}
