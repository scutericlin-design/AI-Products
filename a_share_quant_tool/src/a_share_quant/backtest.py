from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean

from .config import Settings
from .models import DailyBar, OrderIntent, Side


@dataclass
class Position:
    quantity: int
    cost: float
    entry_index: int
    last_price: float


@dataclass(frozen=True)
class BacktestResult:
    annual_return: float
    max_drawdown: float
    sharpe: float | None
    turnover: float
    final_equity: float
    trades: tuple[dict, ...]


class DailyBacktester:
    """Long-only event loop with explicit T+1 and price-limit constraints."""

    def __init__(self, settings: Settings, initial_cash: float = 1_000_000):
        self.settings, self.initial_cash = settings, initial_cash

    def run(self, bars_by_date: dict[str, dict[str, DailyBar]], intents_by_signal_date: dict[str, list[OrderIntent]]) -> BacktestResult:
        dates = sorted(bars_by_date)
        cash, positions, pending, curve, trades, gross_turnover = self.initial_cash, {}, [], [], [], 0.0
        for index, trade_date in enumerate(dates):
            today = bars_by_date[trade_date]
            for intent in pending:
                bar = today.get(intent.symbol)
                if not bar or bar.suspended or self._blocked(intent.side, bar):
                    continue
                if intent.side is Side.SELL:
                    pos = positions.get(intent.symbol)
                    if not pos or pos.entry_index >= index:
                        continue
                    price = bar.open * (1 - self.settings.slippage_rate)
                    proceeds = pos.quantity * price
                    fee = self._fee(proceeds) + proceeds * self.settings.stamp_duty_rate
                    cash += proceeds - fee
                    gross_turnover += proceeds
                    trades.append({"date": trade_date, "side": "SELL", "symbol": intent.symbol, "quantity": pos.quantity, "price": price, "fee": fee})
                    del positions[intent.symbol]
                elif intent.symbol not in positions and not bar.is_st:
                    price = bar.open * (1 + self.settings.slippage_rate)
                    quantity = intent.quantity // self.settings.lot_size * self.settings.lot_size
                    amount = quantity * price
                    fee = self._fee(amount)
                    if amount + fee > cash:
                        quantity = int(cash / (price * self.settings.lot_size)) * self.settings.lot_size
                        amount = quantity * price
                        fee = self._fee(amount) if quantity else 0.0
                    if quantity and amount + fee <= cash:
                        cash -= amount + fee
                        positions[intent.symbol] = Position(quantity, (amount + fee) / quantity, index, bar.close)
                        gross_turnover += amount
                        trades.append({"date": trade_date, "side": "BUY", "symbol": intent.symbol, "quantity": quantity, "price": price, "fee": fee})
            pending = list(intents_by_signal_date.get(trade_date, []))
            for symbol, pos in positions.items():
                if symbol in today:
                    pos.last_price = today[symbol].close
            curve.append(cash + sum(p.quantity * p.last_price for p in positions.values()))
        return self._metrics(curve, gross_turnover, trades)

    def _blocked(self, side: Side, bar: DailyBar) -> bool:
        limit = .30 if bar.symbol.endswith(".BJ") else .20 if bar.symbol.startswith(("300", "688")) else .10
        return bar.pct_change >= limit - .001 if side is Side.BUY else bar.pct_change <= -limit + .001

    def _fee(self, amount: float) -> float:
        return max(amount * self.settings.commission_rate, self.settings.min_commission)

    def _metrics(self, curve: list[float], turnover: float, trades: list[dict]) -> BacktestResult:
        if not curve:
            return BacktestResult(0, 0, None, 0, self.initial_cash, tuple())
        returns = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
        peak, drawdown = curve[0], 0.0
        for value in curve:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1)
        volatility = sqrt(mean((x - mean(returns)) ** 2 for x in returns)) if len(returns) > 1 else 0.0
        annual = (curve[-1] / self.initial_cash) ** (252 / max(len(curve), 1)) - 1
        sharpe = mean(returns) / volatility * sqrt(252) if volatility else None
        return BacktestResult(annual, drawdown, sharpe, turnover / self.initial_cash, curve[-1], tuple(trades))
