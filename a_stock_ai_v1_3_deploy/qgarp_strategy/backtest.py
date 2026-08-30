from __future__ import annotations

from statistics import mean
from math import sqrt
from typing import Any

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.strategy import QGARPStrategy


def run_historical_backtest(store: QGARPStore, settings: QGARPSettings, start_date: str, end_date: str) -> dict[str, Any]:
    """A conservative point-in-time daily backtest over data already persisted locally.

    It refuses to fabricate historical financial data. Rebalance signals use only financial
    snapshots whose available_at is no later than the rebalance date, and execute next-bar open.
    """
    instruments = store.load_instruments()
    if not instruments:
        return _failure(store, start_date, end_date, "缺少历史股票池")
    dates = store.trade_dates(start_date, end_date)
    if len(dates) < 65:
        return _failure(store, start_date, end_date, "历史日线不足；不生成伪回测")
    # Momentum uses a 120-day lookback plus a skip window.  Check both the
    # requested period and its warm-up history before emitting any v2 result.
    all_dates = store.trade_dates("20000101", end_date)
    start_index = next((index for index, date in enumerate(all_dates) if date >= start_date), 0)
    coverage_start = all_dates[max(0, start_index - 160)] if all_dates else start_date
    coverage = store.adjustment_factor_coverage(coverage_start, end_date)
    coverage_ratio = coverage["adjusted"] / coverage["total"] if coverage["total"] else 0.0
    if coverage_ratio < settings.backtest_min_adjustment_coverage:
        return _failure(
            store,
            start_date,
            end_date,
            f"复权因子覆盖率 {coverage_ratio:.2%} 低于 {settings.backtest_min_adjustment_coverage:.0%}；拒绝生成不可验证的 v2 回测",
        )
    month_ends = [date for index, date in enumerate(dates[:-1]) if _month_end(dates, index)]
    rebalances = set(month_ends[::settings.rebalance_months])
    engine = QGARPStrategy(settings)
    cash, equity = 1_000_000.0, 1_000_000.0
    positions: dict[str, dict[str, Any]] = {}
    pending_sells: dict[str, list[tuple[str, str]]] = {}
    pending_buys: dict[str, list[dict[str, Any]]] = {}
    curve: list[dict[str, Any]] = []
    trade_count = 0
    turnover_value = 0.0
    exit_counts: dict[str, int] = {}
    exposure_samples: list[float] = []
    benchmark_start = _close(store, settings.benchmark_symbol, dates[0])
    benchmark_last = benchmark_start
    for index, date in enumerate(dates[:-1]):
        # Orders are created from a close-of-day signal and can only execute at
        # the following session's open.  This removes the old one-day look-ahead
        # in both the equity curve and T+1 availability.
        for position in positions.values():
            position["available"] = date > str(position.get("entry_date") or "")
        for symbol, reason in pending_sells.pop(date, []):
            position = positions.get(symbol)
            price = _open(store, symbol, date)
            if not position or not position.get("available") or price <= 0 or not _tradable_sell(store, symbol, date):
                continue
            proceeds = position["quantity"] * price * (1 - settings.paper_slippage_pct)
            fee = max(proceeds * settings.paper_commission_rate, settings.paper_min_commission)
            cash += proceeds * (1 - settings.paper_stamp_duty_rate) - fee
            turnover_value += proceeds
            trade_count += 1
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
            del positions[symbol]
        for candidate in pending_buys.pop(date, []):
            symbol = str(candidate["symbol"])
            price = _open(store, symbol, date)
            if symbol in positions or price <= 0 or not _tradable_buy(store, symbol, date):
                continue
            equity = cash + sum(item["quantity"] * item["last_price"] for item in positions.values())
            budget = min(equity * min(float(candidate["target_weight"]), settings.paper_max_position_pct), cash)
            quantity = int(budget / (price * (1 + settings.paper_slippage_pct)) / settings.paper_lot_size) * settings.paper_lot_size
            if quantity <= 0:
                continue
            amount = quantity * price * (1 + settings.paper_slippage_pct)
            fee = max(amount * settings.paper_commission_rate, settings.paper_min_commission)
            if amount + fee > cash:
                continue
            cash -= amount + fee
            turnover_value += amount
            positions[symbol] = {
                "quantity": quantity,
                "last_price": price,
                "available": False,
                "entry_date": date,
                "avg_cost": round((amount + fee) / quantity, 6),
                "stop_loss": float(candidate.get("stop_loss") or 0.0),
                "take_profit": float(candidate.get("take_profit") or 0.0),
                "trailing_stop_pct": float(candidate.get("trailing_stop_pct") or 0.0),
                "high_watermark": price,
            }
            trade_count += 1

        # A daily OHLC backtest must execute the exit rules that the live/paper
        # implementation promises.  A stock bought today is not sellable today.
        if settings.daily_exit_enabled:
            for symbol, position in list(positions.items()):
                exit_price, exit_reason = _daily_exit(store, symbol, position, date)
                if exit_price <= 0 or not exit_reason or not position["available"]:
                    continue
                proceeds = position["quantity"] * exit_price * (1 - settings.paper_slippage_pct)
                fee = max(proceeds * settings.paper_commission_rate, settings.paper_min_commission)
                cash += proceeds * (1 - settings.paper_stamp_duty_rate) - fee
                turnover_value += proceeds
                trade_count += 1
                exit_counts[exit_reason] = exit_counts.get(exit_reason, 0) + 1
                del positions[symbol]

        # Daily mark-to-market only needs the actual portfolio, not every historical candidate.
        today_prices = {symbol: _close(store, symbol, date) for symbol in positions}
        for symbol, position in positions.items():
            if today_prices.get(symbol, 0) > 0:
                position["last_price"] = today_prices[symbol]
        equity = cash + sum(item["quantity"] * item["last_price"] for item in positions.values())
        if date in rebalances:
            member_symbols = store.membership_symbols_as_of(settings.backtest_universe_id, date)
            if not member_symbols:
                return _failure(store, start_date, end_date, f"{date} 缺少 {settings.backtest_universe_id} 点时点股票池，拒绝幸存者偏差回测")
            as_of_instruments = [item for item in instruments if item.symbol in member_symbols]
            histories = {item.symbol: store.bars(item.symbol, date) for item in as_of_instruments}
            fundamentals = {item.symbol: value for item in as_of_instruments if (value := store.fundamental_as_of(item.symbol, date)) is not None}
            if len(fundamentals) < max(10, len(as_of_instruments) // 4):
                return _failure(store, start_date, end_date, f"{date} 缺少足够点时点财务数据")
            plan = engine.decide(as_of=date, instruments=as_of_instruments, histories=histories, fundamentals=fundamentals, benchmark=store.bars(settings.benchmark_symbol, date))
            next_date = dates[index + 1]
            target = {item["symbol"]: item for item in plan.get("recommendations") or []}
            for symbol, position in list(positions.items()):
                if symbol not in target:
                    pending_sells.setdefault(next_date, []).append((symbol, "rebalance"))
            for symbol, candidate in target.items():
                if symbol in positions:
                    continue
                pending_buys.setdefault(next_date, []).append(candidate)
        equity = cash + sum(item["quantity"] * item["last_price"] for item in positions.values())
        exposure_samples.append((equity - cash) / equity if equity > 0 else 0.0)
        benchmark_last = _close(store, settings.benchmark_symbol, date) or benchmark_last
        curve.append({"date": date, "equity": round(equity, 2)})
    returns = [curve[index]["equity"] / curve[index - 1]["equity"] - 1 for index in range(1, len(curve))]
    peak, max_drawdown = curve[0]["equity"], 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        max_drawdown = min(max_drawdown, point["equity"] / peak - 1)
    daily_volatility = sqrt(mean(value * value for value in returns) - mean(returns) ** 2) if len(returns) > 1 else 0.0
    annual_return = (curve[-1]["equity"] / 1_000_000) ** (252 / max(len(curve), 1)) - 1
    annual_volatility = daily_volatility * sqrt(252)
    metrics = {
        "total_return_pct": round((curve[-1]["equity"] / 1_000_000 - 1) * 100, 3),
        "annual_return_pct": round(annual_return * 100, 3),
        "benchmark_return_pct": round((benchmark_last / benchmark_start - 1) * 100, 3) if benchmark_start > 0 else None,
        "excess_return_pct": round(((curve[-1]["equity"] / 1_000_000) - (benchmark_last / benchmark_start)) * 100, 3) if benchmark_start > 0 else None,
        "max_drawdown_pct": round(max_drawdown * 100, 3),
        "sharpe": round((mean(returns) / daily_volatility) * sqrt(252), 3) if daily_volatility > 0 else None,
        "calmar": round(annual_return / abs(max_drawdown), 3) if max_drawdown < 0 else None,
        "trade_count": trade_count,
        "turnover_multiple": round(turnover_value / 1_000_000, 3),
        "average_exposure_pct": round(mean(exposure_samples) * 100, 3) if exposure_samples else 0.0,
        "daily_return_mean_pct": round(mean(returns) * 100, 4) if returns else 0.0,
        "days": len(curve),
    }
    assumptions = {"strategy_version": settings.strategy_version, "parameters": settings.parameter_snapshot(), "data_integrity": {"adjustment_coverage": coverage, "adjustment_coverage_ratio": round(coverage_ratio, 6), "coverage_period": [coverage_start, end_date]}, "execution": "rebalance_date_close_signal_next_trading_day_open; daily exits only when explicitly enabled", "financial_timing": "available_at<=rebalance_date", "survivorship": f"{settings.backtest_universe_id} point-in-time universe plus list/delist dates", "costs": "slippage+commission+stamp_duty", "unfilled": "non-positive or limit-down next open cannot trade", "exits": "disabled_by_default_for_monthly_model" if not settings.daily_exit_enabled else "stop_loss,take_profit,trailing_stop,T+1"}
    run_id = store.log_backtest("ok", start_date, end_date, metrics, assumptions, {"curve": curve, "exit_counts": exit_counts})
    return {"run_id": run_id, "status": "ok", "metrics": metrics, "assumptions": assumptions, "curve": curve}


def _month_end(dates: list[str], index: int) -> bool:
    return index == len(dates) - 2 or dates[index][:6] != dates[index + 1][:6]


def _open(store: QGARPStore, symbol: str, date: str) -> float:
    rows = [bar for bar in store.bars(symbol, date, 1) if bar.trade_date == date]
    return rows[-1].open if rows else 0.0


def _close(store: QGARPStore, symbol: str, date: str) -> float:
    rows = [bar for bar in store.bars(symbol, date, 1) if bar.trade_date == date]
    return rows[-1].close if rows else 0.0


def _bar(store: QGARPStore, symbol: str, date: str):
    rows = [bar for bar in store.bars(symbol, date, 1) if bar.trade_date == date]
    return rows[-1] if rows else None


def _tradable_sell(store: QGARPStore, symbol: str, date: str) -> bool:
    bar = _bar(store, symbol, date)
    return bool(bar and bar.open > 0 and bar.pct_chg > -9.5)


def _tradable_buy(store: QGARPStore, symbol: str, date: str) -> bool:
    bar = _bar(store, symbol, date)
    return bool(bar and bar.open > 0 and bar.pct_chg < 9.5)


def _daily_exit(store: QGARPStore, symbol: str, position: dict[str, Any], date: str) -> tuple[float, str]:
    bar = _bar(store, symbol, date)
    if not bar or bar.low <= 0 or bar.pct_chg <= -9.5:
        return 0.0, ""
    high_watermark = max(float(position.get("high_watermark") or 0.0), float(position.get("last_price") or 0.0))
    stop = float(position.get("stop_loss") or 0.0)
    take = float(position.get("take_profit") or 0.0)
    trailing = float(position.get("trailing_stop_pct") or 0.0)
    trailing_trigger = high_watermark * (1 - trailing) if trailing > 0 else 0.0
    trigger, reason = 0.0, ""
    if stop > 0 and bar.low <= stop:
        trigger, reason = stop, "stop_loss"
    elif trailing_trigger > 0 and bar.low <= trailing_trigger:
        trigger, reason = trailing_trigger, "trailing_stop"
    elif take > 0 and bar.high >= take:
        trigger, reason = take, "take_profit"
    position["high_watermark"] = max(high_watermark, bar.high)
    if not reason:
        return 0.0, ""
    # Gap-through exits receive the open; otherwise the trigger is the conservative fill.
    price = min(bar.open, trigger) if reason != "take_profit" else max(bar.open, trigger)
    return max(price, 0.0), reason


def _failure(store: QGARPStore, start: str, end: str, reason: str) -> dict[str, Any]:
    run_id = store.log_backtest("blocked", start, end, {}, {"fail_closed": True}, {"reason": reason})
    return {"run_id": run_id, "status": "blocked", "reason": reason, "metrics": {}}
