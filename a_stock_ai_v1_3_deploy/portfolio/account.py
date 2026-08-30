from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import floor
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from scheduler.trading_calendar import BEIJING_TZ


logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    symbol: str
    name: str
    quantity: int
    available_quantity: int
    avg_cost: float
    last_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    last_trade_date: str = ""
    strategy_id: str = "legacy"


@dataclass
class PaperAccount:
    account_id: str
    cash: float
    initial_cash: float
    realized_pnl: float = 0.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    session_started_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        positions = {symbol: asdict(position) for symbol, position in self.positions.items()}
        summary = account_summary(self)
        return {
            "account_id": self.account_id,
            "cash": round(self.cash, 4),
            "initial_cash": round(self.initial_cash, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "unrealized_pnl": summary["unrealized_pnl"],
            "market_value": summary["market_value"],
            "equity": summary["equity"],
            "return_pct": summary["return_pct"],
            "strategy_exposure": summary["strategy_exposure"],
            "positions": positions,
            "session_started_at": self.session_started_at,
            "updated_at": self.updated_at,
        }


def load_account(path: Path | None = None) -> PaperAccount:
    account_path = path or settings.paper_account_path
    if not account_path.exists():
        return new_account()
    try:
        raw = json.loads(account_path.read_text(encoding="utf-8"))
        positions: dict[str, PaperPosition] = {}
        for symbol, payload in (raw.get("positions") or {}).items():
            positions[symbol] = PaperPosition(
                symbol=str(payload.get("symbol") or symbol),
                name=str(payload.get("name") or symbol),
                quantity=int(payload.get("quantity") or 0),
                available_quantity=int(payload.get("available_quantity") or 0),
                avg_cost=_float(payload.get("avg_cost"), 0.0),
                last_price=_float(payload.get("last_price"), 0.0),
                market_value=_float(payload.get("market_value"), 0.0),
                unrealized_pnl=_float(payload.get("unrealized_pnl"), 0.0),
                last_trade_date=str(payload.get("last_trade_date") or ""),
                strategy_id=str(payload.get("strategy_id") or "legacy"),
            )
        account = PaperAccount(
            account_id=str(raw.get("account_id") or uuid4().hex),
            cash=_float(raw.get("cash"), settings.paper_initial_cash),
            initial_cash=_float(raw.get("initial_cash"), settings.paper_initial_cash),
            realized_pnl=_float(raw.get("realized_pnl"), 0.0),
            positions=positions,
            session_started_at=str(raw.get("session_started_at") or raw.get("updated_at") or _now()),
            updated_at=str(raw.get("updated_at") or ""),
        )
        rollover_t1(account)
        return account
    except Exception as exc:
        logger.warning("paper account load failed, creating a fresh account: %s", exc)
        return new_account()


def save_account(account: PaperAccount, path: Path | None = None) -> None:
    account.updated_at = _now()
    account_path = path or settings.paper_account_path
    account_path.parent.mkdir(parents=True, exist_ok=True)
    account_path.write_text(json.dumps(account.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def new_account() -> PaperAccount:
    now = _now()
    return PaperAccount(
        account_id=uuid4().hex,
        cash=settings.paper_initial_cash,
        initial_cash=settings.paper_initial_cash,
        session_started_at=now,
        updated_at=now,
    )


def rollover_t1(account: PaperAccount) -> None:
    today = datetime.now(BEIJING_TZ).date().isoformat()
    for position in account.positions.values():
        if position.last_trade_date and position.last_trade_date < today:
            position.available_quantity = position.quantity


def mark_to_market(account: PaperAccount, prices: dict[str, float]) -> dict[str, Any]:
    for symbol, position in account.positions.items():
        price = _float(prices.get(symbol), position.last_price)
        if price > 0:
            position.last_price = price
        position.market_value = round(position.quantity * position.last_price, 4)
        position.unrealized_pnl = round((position.last_price - position.avg_cost) * position.quantity, 4)
    return account_summary(account)


def account_summary(account: PaperAccount) -> dict[str, Any]:
    market_value = sum(position.quantity * position.last_price for position in account.positions.values())
    unrealized_pnl = sum(position.unrealized_pnl for position in account.positions.values())
    equity = account.cash + market_value
    strategy_exposure: dict[str, dict[str, float]] = {}
    for position in account.positions.values():
        row = strategy_exposure.setdefault(position.strategy_id or "legacy", {"market_value": 0.0, "unrealized_pnl": 0.0})
        row["market_value"] += position.quantity * position.last_price
        row["unrealized_pnl"] += position.unrealized_pnl
    return {
        "cash": round(account.cash, 4),
        "market_value": round(market_value, 4),
        "equity": round(equity, 4),
        "realized_pnl": round(account.realized_pnl, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "position_count": len(account.positions),
        "return_pct": round((equity / account.initial_cash - 1) * 100, 4) if account.initial_cash else 0.0,
        "strategy_exposure": [
            {"strategy_id": key, "market_value": round(value["market_value"], 4), "unrealized_pnl": round(value["unrealized_pnl"], 4)}
            for key, value in sorted(strategy_exposure.items())
        ],
    }


def simulate_buy(
    account: PaperAccount,
    recommendation: dict[str, Any],
    cycle_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    symbol = str(recommendation.get("symbol") or "")
    name = str(recommendation.get("name") or symbol)
    strategy_id = "+".join(str(item) for item in (recommendation.get("strategy_ids") or [recommendation.get("strategy_id") or "legacy"]))
    raw_price = _float(recommendation.get("current_price") or recommendation.get("price"), 0.0)
    max_buy_price = _float(recommendation.get("max_buy_price"), 0.0)
    if not symbol or raw_price <= 0:
        return _order(cycle_id, symbol or "UNKNOWN", name, "BUY", "rejected", 0, 0.0, "缺少股票代码或价格", strategy_id), None
    if not recommendation.get("can_buy", True):
        return _order(cycle_id, symbol, name, "BUY", "rejected", 0, raw_price, "推荐已标记为不可买入", strategy_id), None
    if recommendation.get("is_limit_up") or recommendation.get("near_limit_up"):
        return _order(cycle_id, symbol, name, "BUY", "rejected", 0, raw_price, "涨停或接近涨停，模拟盘不追", strategy_id), None

    price = round(raw_price * (1 + settings.paper_slippage_pct), 4)
    if max_buy_price > 0 and price > max_buy_price:
        return _order(cycle_id, symbol, name, "BUY", "rejected", 0, price, "含滑点价格超过最高追价", strategy_id), None

    mark_to_market(account, {symbol: price})
    summary = account_summary(account)
    equity_before = max(_float(summary.get("equity"), 0.0), 0.0)
    target_weight = min(
        _float(recommendation.get("position") or recommendation.get("target_weight"), settings.paper_max_position_pct),
        settings.paper_max_position_pct,
        settings.max_position_weight,
    )
    target_value = summary["equity"] * target_weight
    current_position = account.positions.get(symbol)
    position_before_quantity = current_position.quantity if current_position else 0
    current_value = (current_position.quantity * current_position.last_price) if current_position else 0.0
    buy_value = max(0.0, target_value - current_value)
    desired_quantity = _lot_floor(buy_value / price)
    affordable_quantity = _max_affordable_quantity(account.cash, price)
    quantity = min(desired_quantity, affordable_quantity)
    if quantity <= 0:
        lot_cost = round(max(settings.paper_lot_size, 1) * price, 4)
        if buy_value < lot_cost:
            reason = "目标仓位与现有持仓差额不足一手，保持持仓"
            skip_kind = "target_within_lot"
        elif affordable_quantity <= 0:
            reason = "可用现金不足一手，未提交模拟订单"
            skip_kind = "cash_below_lot"
        else:
            reason = "目标仓位无需继续加仓，保持持仓"
            skip_kind = "target_satisfied"
        return (
            _order(
                cycle_id,
                symbol,
                name,
                "BUY",
                "skipped",
                0,
                price,
                reason,
                strategy_id,
                metadata={
                    "skip_kind": skip_kind,
                    "target_portfolio_weight": round(target_weight, 6),
                    "current_position_quantity": position_before_quantity,
                    "remaining_target_value": round(buy_value, 4),
                    "lot_cost": lot_cost,
                },
            ),
            None,
        )

    amount = round(quantity * price, 4)
    fee = _commission(amount)
    if amount + fee > account.cash:
        quantity = _max_affordable_quantity(account.cash - fee, price)
        if quantity <= 0:
            return _order(cycle_id, symbol, name, "BUY", "rejected", 0, price, "扣除费用后现金不足", strategy_id), None
        amount = round(quantity * price, 4)
        fee = _commission(amount)

    account.cash = round(account.cash - amount - fee, 4)
    today = datetime.now(BEIJING_TZ).date().isoformat()
    if current_position:
        old_cost = current_position.avg_cost * current_position.quantity
        new_quantity = current_position.quantity + quantity
        current_position.avg_cost = round((old_cost + amount + fee) / new_quantity, 4)
        current_position.quantity = new_quantity
        current_position.last_price = price
        current_position.market_value = round(new_quantity * price, 4)
        current_position.unrealized_pnl = round((price - current_position.avg_cost) * new_quantity, 4)
        current_position.last_trade_date = today
        current_position.strategy_id = strategy_id
    else:
        account.positions[symbol] = PaperPosition(
            symbol=symbol,
            name=name,
            quantity=quantity,
            available_quantity=0,
            avg_cost=round((amount + fee) / quantity, 4),
            last_price=price,
            market_value=amount,
            unrealized_pnl=round(amount - (amount + fee), 4),
            last_trade_date=today,
            strategy_id=strategy_id,
        )

    post_trade_equity = max(equity_before - fee, 0.0)
    post_trade_quantity = position_before_quantity + quantity
    order = _order(
        cycle_id,
        symbol,
        name,
        "BUY",
        "filled",
        quantity,
        price,
        "模拟成交",
        strategy_id,
        metadata={
            "executed_portfolio_weight": _ratio(amount, equity_before),
            "post_trade_portfolio_weight": _ratio(post_trade_quantity * price, post_trade_equity),
            "target_portfolio_weight": round(target_weight, 6),
            "position_before_quantity": position_before_quantity,
            "position_after_quantity": post_trade_quantity,
        },
    )
    trade = _trade(order, amount=amount, fee=fee, tax=0.0, slippage=round(price - raw_price, 4), realized_pnl=0.0)
    return order, trade


def simulate_sell_all(
    account: PaperAccount,
    cycle_id: str | None = None,
    prices: dict[str, float] | None = None,
    reason: str = "系统防守信号，模拟卖出可卖持仓",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prices = prices or {}
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    rollover_t1(account)
    for symbol, position in list(account.positions.items()):
        equity_before = max(_float(account_summary(account).get("equity"), 0.0), 0.0)
        position_before_quantity = position.quantity
        quantity = min(position.available_quantity, position.quantity)
        raw_price = _float(prices.get(symbol), position.last_price)
        if quantity <= 0:
            orders.append(
                _order(
                    cycle_id,
                    symbol,
                    position.name,
                    "SELL",
                    "skipped",
                    0,
                    raw_price,
                    "T+1限制，持仓当日不可卖",
                    position.strategy_id,
                    metadata={"skip_kind": "t1_locked", "position_quantity": position.quantity},
                )
            )
            continue
        if raw_price <= 0:
            orders.append(
                _order(
                    cycle_id,
                    symbol,
                    position.name,
                    "SELL",
                    "skipped",
                    0,
                    raw_price,
                    "缺少有效行情，未提交模拟卖单",
                    position.strategy_id,
                    metadata={"skip_kind": "missing_price", "position_quantity": position.quantity},
                )
            )
            continue
        price = round(raw_price * (1 - settings.paper_slippage_pct), 4)
        amount = round(quantity * price, 4)
        fee = _commission(amount)
        tax = round(amount * settings.paper_stamp_duty_rate, 4)
        realized_pnl = round(amount - fee - tax - quantity * position.avg_cost, 4)
        account.cash = round(account.cash + amount - fee - tax, 4)
        account.realized_pnl = round(account.realized_pnl + realized_pnl, 4)
        position.quantity -= quantity
        position.available_quantity = max(0, position.available_quantity - quantity)
        position.market_value = round(position.quantity * price, 4)
        position.unrealized_pnl = round((price - position.avg_cost) * position.quantity, 4)
        position.last_price = price
        if position.quantity <= 0:
            del account.positions[symbol]

        order = _order(
            cycle_id,
            symbol,
            position.name,
            "SELL",
            "filled",
            quantity,
            price,
            reason,
            position.strategy_id,
            metadata={
                "executed_position_ratio": _ratio(quantity, position_before_quantity),
                "executed_portfolio_weight": _ratio(amount, equity_before),
                "position_before_quantity": position_before_quantity,
                "position_after_quantity": position.quantity,
            },
        )
        trade = _trade(
            order,
            amount=amount,
            fee=fee,
            tax=tax,
            slippage=round(raw_price - price, 4),
            realized_pnl=realized_pnl,
        )
        orders.append(order)
        trades.append(trade)
    return orders, trades


def _order(
    cycle_id: str | None,
    symbol: str,
    name: str,
    side: str,
    status: str,
    quantity: int,
    price: float,
    reason: str,
    strategy_id: str = "legacy",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order = {
        "order_id": uuid4().hex,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "name": name,
        "side": side,
        "status": status,
        "quantity": int(quantity),
        "price": round(price, 4),
        "reason": reason,
        "strategy_id": strategy_id,
        "created_at": _now(),
        "simulated": True,
    }
    if metadata:
        order.update(metadata)
    return order


def _trade(
    order: dict[str, Any],
    amount: float,
    fee: float,
    tax: float,
    slippage: float,
    realized_pnl: float,
) -> dict[str, Any]:
    return {
        "trade_id": uuid4().hex,
        "order_id": order["order_id"],
        "cycle_id": order.get("cycle_id"),
        "symbol": order["symbol"],
        "name": order.get("name"),
        "side": order["side"],
        "quantity": order["quantity"],
        "price": order["price"],
        "amount": round(amount, 4),
        "fee": round(fee, 4),
        "tax": round(tax, 4),
        "slippage": round(slippage, 4),
        "realized_pnl": round(realized_pnl, 4),
        "strategy_id": order.get("strategy_id", "legacy"),
        "created_at": _now(),
        "simulated": True,
    }


def _commission(amount: float) -> float:
    if amount <= 0:
        return 0.0
    return round(max(amount * settings.paper_commission_rate, settings.paper_min_commission), 4)


def _lot_floor(quantity: float) -> int:
    lot = max(settings.paper_lot_size, 1)
    return int(floor(max(quantity, 0.0) / lot) * lot)


def _max_affordable_quantity(cash: float, price: float) -> int:
    if cash <= 0 or price <= 0:
        return 0
    return _lot_floor((cash - settings.paper_min_commission) / price)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ratio(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 6)


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
