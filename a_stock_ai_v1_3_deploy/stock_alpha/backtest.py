"""Next-open replay in fractional adjusted units, explicitly not an execution test."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from stock_alpha.model import (
    ModelConfig, PreparedModelData, _date, _input_signature, _positive,
    build_targets, build_weight_deltas, prepare_model_data,
)


def _sell_tax_rate(date: str) -> float:
    return 0.001 if date < "20230828" else 0.0005


def _fee(notional: float, config: ModelConfig, multiplier: float) -> float:
    return multiplier * max(config.min_commission, notional * config.commission_bps / 10000) if notional > 0 else 0.0


def _metrics(curve: list[dict], initial: float, orders: list[dict]) -> dict:
    if not curve:
        return {"sessions": 0, "return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": None, "turnover": 0.0, "costs": 0.0}
    equity = np.asarray([initial] + [row["equity"] for row in curve], dtype=float)
    returns = equity[1:] / equity[:-1] - 1
    deviation = float(returns.std(ddof=0))
    filled = [order for order in orders if order["status"] == "filled"]
    return {
        "sessions": len(curve), "return_pct": float((equity[-1] / initial - 1) * 100),
        "max_drawdown_pct": float(np.min(equity / np.maximum.accumulate(equity) - 1) * 100),
        "sharpe": float(returns.mean() / deviation * np.sqrt(252)) if deviation > 1e-12 else None,
        "turnover": float(sum(order["notional"] for order in filled) / initial),
        "costs": float(sum(order["commission"] + order["tax"] + order["slippage_cost"] for order in filled)),
    }


def run_backtest(
    bars: pd.DataFrame,
    fundamentals: pd.DataFrame,
    memberships: pd.DataFrame,
    calendar: list[str],
    start: str,
    end: str,
    config: ModelConfig,
    initial_cash: float = 1_000_000,
    cost_multiplier: float = 1,
    *,
    prepared: PreparedModelData | None = None,
) -> dict:
    """Form first signal at start's first close, then every N supplied sessions.

    Portfolio prices are raw OHLC * same-date adj_factor, quantities are
    fractional adjusted units, and costs apply to their cash notional. Factor
    changes are a TOTAL RETURN PROXY, never inferred cash dividends or splits.
    There are no real-share quantities, board lots, auction/limit/status checks,
    or executability claims. Missing quotes cannot fill and stale marks are
    flagged; invalid adjustment data needed for accounting blocks promotion
    and the result status. No source frames are modified.
    """
    start, end = _date(start), _date(end)
    if start > end:
        raise ValueError("start must not exceed end")
    if not np.isfinite(initial_cash) or initial_cash <= 0 or not np.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("initial_cash must be positive and cost_multiplier nonnegative")
    if config.slippage_bps * cost_multiplier >= 10000:
        raise ValueError("slippage must remain below 100 percent")
    sessions = [_date(date) for date in calendar]
    if sessions != sorted(set(sessions)):
        raise ValueError("calendar must be strictly increasing and unique")
    dates = [date for date in sessions if start <= date <= end]
    if not dates:
        raise ValueError("calendar has no sessions within the requested period")
    signal_dates = dates[::config.rebalance_sessions]
    if prepared is None:
        prepared = prepare_model_data(bars, fundamentals, memberships, signal_dates, config, end=end)
    else:
        prepared.check_config(config)
        if prepared.end != end or prepared.signature != _input_signature(bars, fundamentals, memberships, end):
            raise ValueError("prepared data source/end mismatch; rebuild after changing inputs")
        if not set(signal_dates).issubset(prepared.snapshots):
            raise ValueError("prepared data lacks required signal dates")
    quote_dates = {date: index for index, date in enumerate(prepared.opens.index)}
    quote_symbols = {symbol: index for index, symbol in enumerate(prepared.opens.columns)}
    raw_opens, raw_closes = prepared.opens.to_numpy(), prepared.closes.to_numpy()
    factors, presence = prepared.adjustments.to_numpy(), prepared.presence.to_numpy()
    positions: dict[str, dict] = {}
    cash = float(initial_cash)
    pending: dict | None = None
    curve, orders, plans, warnings, fatal_errors = [], [], [], [], []
    slip = config.slippage_bps / 10000 * cost_multiplier
    previous_risk_limit = config.total_weight

    def rejected(date: str, signal_date: str, symbol: str, side: str, reason: str) -> None:
        orders.append({"date": date, "signal_date": signal_date, "ts_code": symbol, "side": side,
                       "status": "unfilled", "reason": reason, "accounting": "adjusted_units_proxy"})

    for index, date in enumerate(dates):
        row_index = quote_dates.get(date)
        required_symbols = set(positions) | (set(pending["weights"]) if pending else set())
        quotes = {}
        if row_index is not None:
            for symbol in required_symbols:
                column_index = quote_symbols.get(symbol)
                if column_index is not None and presence[row_index, column_index]:
                    quotes[symbol] = {"open": raw_opens[row_index, column_index],
                                      "close": raw_closes[row_index, column_index], "adj_factor": factors[row_index, column_index]}
        if row_index is None or not presence[row_index].any():
            fatal_errors.append({"date": date, "reason": "missing_calendar_session_bars"})
        opening_prices: dict[str, float] = {}
        # Opening valuation/sizing must never inspect this day's close/high/low.
        for symbol in set(positions) | (set(pending["weights"]) if pending else set()):
            quote = quotes.get(symbol)
            if quote and _positive(quote["open"]) and _positive(quote["adj_factor"]):
                price = float(quote["open"]) * float(quote["adj_factor"])
                if _positive(price):
                    opening_prices[symbol] = price
        opening_equity = cash + sum(position["units"] * opening_prices.get(symbol, position["mark"]) for symbol, position in positions.items())

        if pending is not None:
            desired = pending["weights"]
            signal_date = pending["as_of"]
            risk_limit = config.total_weight * pending["diagnostics"].get("risk_scale", 1.0)
            opening_weights = {symbol: position["units"] * opening_prices.get(symbol, position["mark"]) / opening_equity
                               for symbol, position in positions.items()}
            decision = build_weight_deltas(opening_weights, desired, config,
                                           risk_reduction=risk_limit < previous_risk_limit - 1e-10,
                                           exposure_limit=risk_limit)
            previous_risk_limit = risk_limit
            sell_orders, buy_orders = [], []
            for symbol in sorted(set(positions) | set(desired)):
                if symbol in decision["suppressed"]:
                    orders.append({"date": date, "signal_date": signal_date, "ts_code": symbol,
                                   "side": "REBALANCE", "status": "skipped", "accounting": "adjusted_units_proxy",
                                   **decision["suppressed"][symbol]})
                    continue
                position = positions.get(symbol)
                held = float(position["units"]) if position else 0.0
                price = opening_prices.get(symbol)
                if price is None:
                    side = "REBALANCE" if held and desired.get(symbol, 0) > 0 else "SELL" if held else "BUY"
                    rejected(date, signal_date, symbol, side, "missing_or_invalid_open_or_adjustment")
                    quote = quotes.get(symbol)
                    if quote is not None and not _positive(quote["adj_factor"]):
                        fatal_errors.append({"date": date, "ts_code": symbol, "reason": "invalid_execution_adjustment"})
                    continue
                delta = decision["deltas"][symbol] * opening_equity / price
                if delta < -1e-10:
                    available = sum(units for acquired, units in position["lots"] if acquired < date)
                    units = min(-delta, available)
                    if units <= 1e-10:
                        rejected(date, signal_date, symbol, "SELL", "t_plus_one")
                    else:
                        sell_orders.append((symbol, units, price))
                elif delta > 1e-10:
                    buy_orders.append((symbol, delta, price))

            for symbol, units, price in sell_orders:
                notional = units * price * (1 - slip)
                fee = _fee(notional, config, cost_multiplier)
                tax = notional * _sell_tax_rate(date) * cost_multiplier
                if fee + tax > notional + cash:
                    rejected(date, signal_date, symbol, "SELL", "insufficient_cash_for_fees")
                    continue
                cash += notional - fee - tax
                position = positions[symbol]
                left = units
                lots = []
                for acquired, quantity in position["lots"]:
                    sold = min(left, quantity) if acquired < date else 0.0
                    left -= sold
                    if quantity - sold > 1e-10:
                        lots.append((acquired, quantity - sold))
                position["lots"] = lots
                position["units"] = sum(quantity for _, quantity in lots)
                if position["units"] <= 1e-10:
                    del positions[symbol]
                orders.append({"date": date, "signal_date": signal_date, "ts_code": symbol, "side": "SELL",
                               "status": "filled", "adjusted_units": units, "adjusted_price": price * (1 - slip),
                               "notional": notional, "commission": fee, "tax": tax,
                               "sell_tax_rate": _sell_tax_rate(date), "slippage_cost": units * price * slip,
                               "accounting": "adjusted_units_proxy"})

            # Failed exits keep consuming name and exposure capacity. Allocate buy
            # headroom only after sales, with the same frozen opening NAV denominator.
            gross = sum(position["units"] * opening_prices.get(symbol, position["mark"]) for symbol, position in positions.items())
            exposure_room = max(0.0, opening_equity * risk_limit - gross)
            requests = []
            names = set(positions)
            for symbol, units, price in sorted(buy_orders, key=lambda item: (-desired[item[0]], item[0])):
                if symbol not in names and len(names) >= config.max_names:
                    rejected(date, signal_date, symbol, "BUY", "max_names_with_unfilled_exits")
                    continue
                held_value = positions[symbol]["units"] * price if symbol in positions else 0.0
                value = min(units * price, max(0.0, opening_equity * config.max_weight - held_value), exposure_room)
                if value <= 1e-8:
                    rejected(date, signal_date, symbol, "BUY", "exposure_cap")
                    continue
                names.add(symbol)
                exposure_room -= value
                requests.append((symbol, value / price, price))
            needed = sum(units * price * (1 + slip) + _fee(units * price * (1 + slip), config, cost_multiplier) for _, units, price in requests)
            scale = min(1.0, cash / needed) if needed > 0 else 0.0
            for symbol, units, price in requests:
                units *= scale
                execution_price = price * (1 + slip)
                # A proportional shrink does not shrink minimum commissions.
                notional = min(units * execution_price, max(0.0, cash - config.min_commission * cost_multiplier))
                rate = config.commission_bps / 10000 * cost_multiplier
                notional = min(notional, cash / (1 + rate))
                units = notional / execution_price
                fee = _fee(notional, config, cost_multiplier)
                if units <= 1e-10 or notional + fee > cash + 1e-8:
                    rejected(date, signal_date, symbol, "BUY", "insufficient_cash_for_fees")
                    continue
                cash -= notional + fee
                position = positions.setdefault(symbol, {"units": 0.0, "lots": [], "mark": price, "mark_date": date})
                position["units"] += units
                position["lots"].append((date, units))
                orders.append({"date": date, "signal_date": signal_date, "ts_code": symbol, "side": "BUY",
                               "status": "filled", "adjusted_units": units, "adjusted_price": execution_price,
                               "notional": notional, "commission": fee, "tax": 0.0,
                               "sell_tax_rate": 0.0, "slippage_cost": units * price * slip,
                               "accounting": "adjusted_units_proxy"})
            pending = None

        stale = []
        for symbol, position in positions.items():
            quote = quotes.get(symbol)
            if quote and _positive(quote["close"]) and _positive(quote["adj_factor"]) and _positive(float(quote["close"]) * float(quote["adj_factor"])):
                position["mark"] = float(quote["close"]) * float(quote["adj_factor"])
                position["mark_date"] = date
            else:
                stale.append(symbol)
                warnings.append({"date": date, "ts_code": symbol, "reason": "stale_mark", "mark_date": position["mark_date"]})
                if quote is not None and not _positive(quote["adj_factor"]):
                    fatal_errors.append({"date": date, "ts_code": symbol, "reason": "invalid_valuation_adjustment"})
        market_value = sum(position["units"] * position["mark"] for position in positions.values())
        equity = cash + market_value
        weights = {symbol: position["units"] * position["mark"] / equity for symbol, position in sorted(positions.items())}
        curve.append({"date": date, "equity": float(equity), "cash": float(cash), "market_value": float(market_value),
                      "exposure": float(market_value / equity), "weights": weights, "names": len(positions), "stale_symbols": stale})
        if index % config.rebalance_sessions == 0:
            plan = build_targets(bars, fundamentals, memberships, date, weights, config, prepared=prepared)
            plans.append({"as_of": date, "status": plan["status"], "targets": plan["targets"], "diagnostics": plan["diagnostics"]})
            if plan["status"] == "ok" and index + 1 < len(dates):
                pending = plan
        if cash < -1e-6 or not np.isfinite(equity) or equity <= 0:
            raise ArithmeticError("invalid portfolio accounting")

    annual = []
    previous_equity = float(initial_cash)
    for year in sorted({row["date"][:4] for row in curve}):
        rows = [row for row in curve if row["date"].startswith(year)]
        year_orders = [order for order in orders if order["date"].startswith(year)]
        annual.append({"year": int(year), "start_date": rows[0]["date"], "end_date": rows[-1]["date"],
                       "start_equity": previous_equity, "end_equity": rows[-1]["equity"],
                       **_metrics(rows, previous_equity, year_orders)})
        previous_equity = rows[-1]["equity"]
    good_plans = sum(plan["status"] == "ok" for plan in plans)
    status = "blocked" if fatal_errors or not good_plans else "ok"
    return {
        "status": status, "research_proxy": True, "promotable": False,
        "execution_mode": "research_proxy", "accounting": "fractional_adjusted_units_total_return_proxy",
        "variant": config.variant, "start": dates[0], "end": dates[-1],
        "initial_cash": float(initial_cash), "cost_multiplier": float(cost_multiplier),
        "annual": annual, "daily_curve": curve, "orders": orders,
        "metrics": _metrics(curve, float(initial_cash), orders),
        "diagnostics": {
            "configuration": asdict(config), "plans": plans, "warnings": warnings, "fatal_errors": fatal_errors,
            "blocked_plans": len(plans) - good_plans,
            "unfilled_orders": sum(order["status"] == "unfilled" for order in orders),
            "band_skips": sum(order["status"] == "skipped" for order in orders),
            "feature_preparation": "vector rolling per symbol; explicit snapshot reusable across variants/costs",
            "rebalance_band_policy": "ongoing-name absolute weight delta < 1% NAV by default; never suppress full exit or cap/risk reduction",
            "calendar_policy": "caller-supplied exchange sessions; first requested close then every rebalance_sessions",
            "annual_policy": "continuous portfolio; prior year closing equity is next year starting equity",
            "cost_policy": "multiplier scales commission, minimum fee, slippage and dated sell tax",
            "sell_tax_policy": "0.001 before 20230828; 0.0005 from 20230828",
            "t_plus_one": "adjusted units acquired today cannot be sold today",
            "unfilled_policy": "cancel remainder at this open; reconsider only at a later rebalance",
            "weight_cap_policy": "targets/opening pre-cost NAV; passive price drift can exceed caps until rebalance",
            "limitations": [
                "Daily adj_factor is a total-return proxy, not identified split ratios or cash dividends.",
                "Fractional adjusted units: no real-share, 100-share or STAR board lot claim.",
                "No historical suspension/ST/price-limit/auction-liquidity or corporate-action ledger.",
                "No strict executability or promotion claim, even with complete cached bars.",
                "Date-lagged financials do not establish original vendor revision vintages.",
                "Missing quotes carry stale marks; unknown delisting recoveries are not fabricated.",
                "No forced terminal liquidation or fabricated fills; final session is marked.",
            ],
        },
    }
