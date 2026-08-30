"""Research-only point-in-time backtest for Q-GARP v3's hysteresis portfolio."""

from __future__ import annotations

from math import sqrt
from statistics import mean
from typing import Any

from qgarp_strategy.backtest import _close, _month_end, _open, _tradable_buy, _tradable_sell
from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.v3_research import QGARPv3Research, QGARPv3Settings, load_v3_settings


def run_v3_historical_backtest(store: QGARPStore, base: QGARPSettings, start_date: str, end_date: str, settings: QGARPv3Settings | None = None, planner: Any | None = None, strategy_version: str | None = None, parameter_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same conservative execution conventions as v2, with v3's hold buffer.

    Signals are generated at month-end and execute at the next session open.
    Membership and fundamentals are point-in-time; no v3 plan can submit paper
    orders or notifications.
    """
    v3 = settings or load_v3_settings()
    dates = store.trade_dates(start_date, end_date)
    instruments = store.load_instruments()
    if len(dates) < 65 or not instruments:
        return _failure(store, start_date, end_date, "v3 缺少历史交易日或股票池")
    all_dates = store.trade_dates("20000101", end_date)
    start_index = next((i for i, day in enumerate(all_dates) if day >= start_date), 0)
    coverage = store.adjustment_factor_coverage(all_dates[max(0, start_index - 160)], end_date)
    if not coverage["total"] or coverage["adjusted"] / coverage["total"] < v3.adjustment_coverage_floor:
        return _failure(store, start_date, end_date, "v3 复权因子覆盖不足")

    rebalances = {day for i, day in enumerate(dates[:-1]) if _month_end(dates, i)}
    engine, cash, positions = planner or QGARPv3Research(v3), 1_000_000.0, {}
    pending_buys: dict[str, list[dict[str, Any]]] = {}
    pending_sells: dict[str, list[str]] = {}
    curve: list[dict[str, Any]] = []
    turnover = 0.0
    trades = 0
    exposure_samples: list[float] = []
    benchmark_start = _close(store, base.benchmark_symbol, dates[0])
    benchmark_last = benchmark_start
    exposure_benchmark_equity = 1_000_000.0
    previous_benchmark = benchmark_start
    previous_exposure = 0.0
    for index, date in enumerate(dates[:-1]):
        for position in positions.values():
            position["available"] = date > position["entry_date"]
        for symbol in pending_sells.pop(date, []):
            position = positions.get(symbol)
            price = _open(store, symbol, date)
            if not position or not position["available"] or price <= 0 or not _tradable_sell(store, symbol, date):
                continue
            proceeds = position["quantity"] * price * (1 - base.paper_slippage_pct)
            fee = max(proceeds * base.paper_commission_rate, base.paper_min_commission)
            cash += proceeds * (1 - base.paper_stamp_duty_rate) - fee
            turnover += proceeds
            trades += 1
            del positions[symbol]
        for candidate in pending_buys.pop(date, []):
            symbol, price = str(candidate["symbol"]), _open(store, str(candidate["symbol"]), date)
            if symbol in positions or price <= 0 or not _tradable_buy(store, symbol, date):
                continue
            equity = cash + sum(item["quantity"] * item["last_price"] for item in positions.values())
            budget = min(equity * min(float(candidate["target_weight"]), base.paper_max_position_pct), cash)
            quantity = int(budget / (price * (1 + base.paper_slippage_pct)) / base.paper_lot_size) * base.paper_lot_size
            fee = max(quantity * price * (1 + base.paper_slippage_pct) * base.paper_commission_rate, base.paper_min_commission)
            if quantity <= 0 or quantity * price * (1 + base.paper_slippage_pct) + fee > cash:
                continue
            cash -= quantity * price * (1 + base.paper_slippage_pct) + fee
            turnover += quantity * price
            positions[symbol] = {"quantity": quantity, "last_price": price, "entry_date": date, "available": False}
            trades += 1
        for symbol, position in positions.items():
            close = _close(store, symbol, date)
            if close > 0:
                position["last_price"] = close
        equity = cash + sum(item["quantity"] * item["last_price"] for item in positions.values())
        if date in rebalances:
            members = store.membership_symbols_as_of(base.backtest_universe_id, date)
            active = [item for item in instruments if item.symbol in members]
            histories = {item.symbol: store.bars(item.symbol, date) for item in active}
            fundamentals = {item.symbol: value for item in active if (value := store.fundamental_as_of(item.symbol, date)) is not None}
            plan = engine.plan(as_of=date, instruments=active, histories=histories, fundamentals=fundamentals, benchmark=store.bars(base.benchmark_symbol, date), current_symbols=set(positions))
            target = {item["symbol"]: item for item in plan["recommendations"]}
            next_date = dates[index + 1]
            for symbol in positions:
                if symbol not in target:
                    pending_sells.setdefault(next_date, []).append(symbol)
            for symbol, candidate in target.items():
                if symbol not in positions:
                    pending_buys.setdefault(next_date, []).append(candidate)
        equity = cash + sum(item["quantity"] * item["last_price"] for item in positions.values())
        current_exposure = (equity - cash) / equity if equity else 0.0
        exposure_samples.append(current_exposure)
        current_benchmark = _close(store, base.benchmark_symbol, date) or benchmark_last
        if previous_benchmark > 0 and current_benchmark > 0:
            exposure_benchmark_equity *= 1 + previous_exposure * (current_benchmark / previous_benchmark - 1)
        previous_benchmark = current_benchmark
        previous_exposure = current_exposure
        benchmark_last = current_benchmark
        curve.append({"date": date, "equity": round(equity, 2)})
    returns = [curve[i]["equity"] / curve[i - 1]["equity"] - 1 for i in range(1, len(curve))]
    peak, drawdown = curve[0]["equity"], 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        drawdown = min(drawdown, point["equity"] / peak - 1)
    vol = sqrt(mean(value * value for value in returns) - mean(returns) ** 2) if len(returns) > 1 else 0.0
    annual = (curve[-1]["equity"] / 1_000_000) ** (252 / len(curve)) - 1
    total_return = curve[-1]["equity"] / 1_000_000 - 1
    benchmark_return = benchmark_last / benchmark_start - 1 if benchmark_start else 0.0
    exposure_adjusted_benchmark_return = exposure_benchmark_equity / 1_000_000 - 1
    metrics = {"total_return_pct": round(total_return * 100, 3), "annual_return_pct": round(annual * 100, 3), "benchmark_return_pct": round(benchmark_return * 100, 3), "excess_return_pct": round((total_return - benchmark_return) * 100, 3), "exposure_adjusted_benchmark_return_pct": round(exposure_adjusted_benchmark_return * 100, 3), "selection_alpha_pct": round((total_return - exposure_adjusted_benchmark_return) * 100, 3), "max_drawdown_pct": round(drawdown * 100, 3), "sharpe": round(mean(returns) / vol * sqrt(252), 3) if vol else None, "trade_count": trades, "turnover_multiple": round(turnover / 1_000_000, 3), "average_exposure_pct": round(mean(exposure_samples) * 100, 3), "days": len(curve)}
    assumptions = {"strategy_version": strategy_version or v3.version, "parameters": parameter_snapshot or v3.snapshot(), "execution": "month_end_signal_next_open; T+1; entry/hold hysteresis", "financial_timing": "available_at<=rebalance_date", "survivorship": "point_in_time_daily_basic_membership", "costs": "slippage+commission+stamp_duty", "research_only": True}
    run_id = store.log_backtest("ok", start_date, end_date, metrics, assumptions, {"curve": curve})
    return {"run_id": run_id, "status": "ok", "metrics": metrics, "assumptions": assumptions, "curve": curve}


def run_v4_historical_backtest(store: QGARPStore, base: QGARPSettings, start_date: str, end_date: str) -> dict[str, Any]:
    from qgarp_strategy.v4_research import QGARPv4Research

    engine = QGARPv4Research()
    if hasattr(engine, "prepare"):
        engine.prepare(store, base, end_date)
    return run_v3_historical_backtest(store, base, start_date, end_date, planner=engine, strategy_version=engine.settings.version, parameter_snapshot=engine.settings.snapshot())


def run_v4_annual_backtests(store: QGARPStore, base: QGARPSettings, start_year: int, end_year: int, final_end_date: str | None = None) -> dict[str, Any]:
    """Run independent calendar-year tests so one market regime cannot hide another."""
    from qgarp_strategy.v4_research import QGARPv4Research

    engine = QGARPv4Research()
    effective_end = final_end_date or f"{end_year}1231"
    if hasattr(engine, "prepare"):
        engine.prepare(store, base, effective_end)
    rows = []
    for year in range(start_year, end_year + 1):
        start, end = f"{year}0101", f"{year}1231"
        if final_end_date and year == end_year:
            end = min(end, final_end_date)
        result = run_v3_historical_backtest(store, base, start, end, planner=engine, strategy_version=engine.settings.version, parameter_snapshot=engine.settings.snapshot())
        rows.append({"year": year, "status": result.get("status"), "metrics": result.get("metrics") or {}, "run_id": result.get("run_id")})
    valid = [row["metrics"] for row in rows if row["status"] == "ok"]
    return {"strategy_version": engine.settings.version, "period": [start_year, end_year], "annual_runs": rows, "summary": {"positive_excess_years": sum(float(row.get("excess_return_pct") or 0) > 0 for row in valid), "total_years": len(valid), "average_excess_return_pct": round(mean(float(row.get("excess_return_pct") or 0) for row in valid), 3) if valid else None, "worst_max_drawdown_pct": min((float(row.get("max_drawdown_pct") or 0) for row in valid), default=None)}}


def _failure(store: QGARPStore, start: str, end: str, reason: str) -> dict[str, Any]:
    run_id = store.log_backtest("blocked", start, end, {}, {"fail_closed": True, "strategy_version": "v3"}, {"reason": reason})
    return {"run_id": run_id, "status": "blocked", "reason": reason, "metrics": {}}
