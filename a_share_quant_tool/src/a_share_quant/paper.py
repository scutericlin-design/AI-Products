from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .config import Settings
from .models import OrderIntent, Side


@dataclass
class PaperAccount:
    cash: float
    holdings: dict[str, int] = field(default_factory=dict)
    entry_date: dict[str, str] = field(default_factory=dict)
    audit_log: list[dict] = field(default_factory=list)


class PaperBroker:
    """Paper ledger with A-share lot size, fees, and T+1 availability."""

    def __init__(self, settings: Settings, initial_cash: float = 1_000_000):
        self.settings = settings
        self.account = PaperAccount(initial_cash)

    def submit(self, intent: OrderIntent, execution_price: float, trade_date: str) -> dict:
        if intent.quantity <= 0 or intent.quantity % self.settings.lot_size:
            return self._record(intent, "rejected", "数量必须为100股整手")
        if intent.side is Side.SELL and self.account.entry_date.get(intent.symbol) == trade_date:
            return self._record(intent, "rejected", "T+1：当日买入不可卖出")
        amount = intent.quantity * execution_price
        fee = max(amount * self.settings.commission_rate, self.settings.min_commission)
        if intent.side is Side.BUY:
            if self.account.cash < amount + fee:
                return self._record(intent, "rejected", "可用现金不足")
            self.account.cash -= amount + fee
            self.account.holdings[intent.symbol] = self.account.holdings.get(intent.symbol, 0) + intent.quantity
            self.account.entry_date[intent.symbol] = trade_date
        else:
            if self.account.holdings.get(intent.symbol, 0) < intent.quantity:
                return self._record(intent, "rejected", "可卖持仓不足")
            tax = amount * self.settings.stamp_duty_rate
            self.account.cash += amount - fee - tax
            self.account.holdings[intent.symbol] -= intent.quantity
        return self._record(intent, "filled", "paper fill", execution_price=execution_price, fee=fee)

    def _record(self, intent: OrderIntent, status: str, reason: str, **extra: object) -> dict:
        row = {"paper_order_id": uuid4().hex, "status": status, "reason": reason, **intent.as_dict(), **extra}
        self.account.audit_log.append(row)
        return row
