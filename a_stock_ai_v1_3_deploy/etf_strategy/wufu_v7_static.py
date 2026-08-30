from __future__ import annotations

import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.file_universe import (
    DEFENSIVE_ETF_SYMBOL,
    FILE_ETF_SYMBOLS,
    GLOBAL_ETF_SYMBOLS,
)
from etf_strategy.storage import ETFLocalStore
from etf_strategy.tushare_client import ETFDataError, TuShareETFClient


WUFU_INDEX_SYMBOLS = ("000300.SH", "399101.SZ", "399006.SZ", "000510.SH")
INTRADAY_PROXY_SYMBOLS = ("512100.SH", "510500.SH")


@dataclass(frozen=True)
class WufuV7StaticParameters:
    """Source-compatible parameters for the original fixed-pool strategy."""

    strategy_id: str = "wufu_v7_static_intraday"
    strategy_version: str = "v7-static-tushare-1m"
    lookback_days: int = 25
    r_squared_min: float = 0.40
    ma_days: int = 10
    loss_floor: float = 0.97
    score_min: float = 0.0
    score_max: float = 5.0
    candidate_ratio: float = 0.90
    holdings_num: int = 1
    weak_below_count: int = 3
    liquidity_days: int = 3
    liquidity_divisor: int = 20_000
    trend_lookback_minutes: int = 30
    trend_slope_threshold_pct: float = 0.001
    slippage_rate: float = 0.0001
    commission_rate: float = 0.0001
    min_commission: float = 5.0
    minute_chunk_calendar_days: int = 28


class WufuV7StaticMinuteBacktester:
    """Executable TuShare version of the source Wufu fixed-pool workflow.

    The source dynamic all-market expansion is intentionally excluded. This
    preserves the user-selected static universe while retaining the source
    strategy's intra-day ranking and trade schedule.
    """

    def __init__(
        self,
        settings: ETFStrategySettings,
        store: ETFLocalStore,
        client: TuShareETFClient | None = None,
        parameters: WufuV7StaticParameters | None = None,
    ):
        self.settings = settings
        self.store = store
        self.client = client or TuShareETFClient(settings)
        self.parameters = parameters or WufuV7StaticParameters()

    def cache_minutes(
        self,
        start_date: str,
        end_date: str,
        workers: int = 4,
    ) -> dict[str, Any]:
        """Fetch and persist bounded 1-minute windows with resume support."""
        universe = self.store.load_universe(end_date)
        list_dates = {
            str(row["symbol"]).upper(): str(row.get("list_date") or "")
            for _, row in universe.iterrows()
        }
        pending_by_symbol: dict[str, list[tuple[str, str, str]]] = {}
        for symbol in sorted(FILE_ETF_SYMBOLS | set(INTRADAY_PROXY_SYMBOLS) | {DEFENSIVE_ETF_SYMBOL}):
            first_date = max(_as_date(start_date), _as_date(list_dates.get(symbol) or start_date))
            windows: list[tuple[str, str, str]] = []
            for window_start, window_end in _minute_windows(
                first_date,
                _as_date(end_date),
                self.parameters.minute_chunk_calendar_days,
            ):
                if not self.store.minute_window_is_cached(symbol, window_start, window_end):
                    windows.append((symbol, window_start, window_end))
            if windows:
                pending_by_symbol[symbol] = windows

        # The proxy can serialize same-code minute requests. Interleave symbols
        # so concurrent workers always start with different ETFs.
        tasks: list[tuple[str, str, str]] = []
        window_index = 0
        while True:
            added = 0
            for symbol in sorted(pending_by_symbol):
                windows = pending_by_symbol[symbol]
                if window_index < len(windows):
                    tasks.append(windows[window_index])
                    added += 1
            if not added:
                break
            window_index += 1

        fetched_rows = 0
        failures: list[str] = []
        if not tasks:
            return {"mode": "minute_cache", "windows": 0, "cached_windows": 0, "rows": 0, "failures": []}

        completed = 0
        worker_count = max(1, min(int(workers), 6))
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(_fetch_minute_window, self.settings, task): task for task in tasks}
            for future in as_completed(futures):
                symbol, window_start, window_end = futures[future]
                completed += 1
                try:
                    fetched_symbol, fetched_start, fetched_end, bars = future.result()
                    fetched_rows += self.store.upsert_minute_bars(fetched_symbol, bars)
                    self.store.mark_minute_window_cached(fetched_symbol, fetched_start, fetched_end, len(bars))
                except Exception as exc:  # Preserve failed windows for a safe retry.
                    failures.append(f"{symbol} {window_start}: {exc}")
                if completed % 25 == 0 or completed == len(tasks):
                    print(f"minute cache: {completed}/{len(tasks)} windows, {fetched_rows} rows")

        return {
            "mode": "minute_cache",
            "windows": len(tasks),
            "cached_windows": len(tasks) - len(failures),
            "rows": fetched_rows,
            "failures": failures,
        }

    def backtest(
        self,
        start_date: str,
        end_date: str,
        initial_cash: float = 1_000_000.0,
    ) -> dict[str, Any]:
        self._ensure_index_history(start_date, end_date)
        warmup_start = (_as_date(start_date) - timedelta(days=150)).strftime("%Y%m%d")
        daily = self.store.load_etf_daily(
            end_date,
            start_date=warmup_start,
            symbols=FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL},
        )
        index_daily = self.store.load_index_daily(end_date, start_date=warmup_start)
        if daily.empty:
            raise ETFDataError("Static ETF daily history is required before the Wufu V7 replay.")
        if index_daily.empty:
            raise ETFDataError("Index daily history is required before the Wufu V7 replay.")

        dates = sorted(item for item in daily["trade_date"].dropna().astype(str).unique() if start_date <= item <= end_date)
        if not dates:
            raise ETFDataError("No cached ETF dates found in the requested Wufu V7 window.")
        daily_by_symbol = {
            symbol: frame.sort_values("trade_date").reset_index(drop=True)
            for symbol, frame in daily.groupby("symbol", sort=False)
        }
        names = self._names(end_date)
        cash = float(initial_cash)
        position: dict[str, Any] | None = None
        pending_symbol: str | None = None
        trades: list[dict[str, Any]] = []
        curve: list[dict[str, Any]] = []
        diagnostics = {"days": len(dates), "missing_1308": 0, "missing_proxy_minutes": 0, "forced_buys": 0}

        for trade_date in dates:
            day_minutes = self.store.load_minute_bars(
                f"{_display_date(trade_date)} 09:30:00",
                f"{_display_date(trade_date)} 15:00:00",
                FILE_ETF_SYMBOLS | set(INTRADAY_PROXY_SYMBOLS) | {DEFENSIVE_ETF_SYMBOL},
            )
            minute_by_symbol = {
                symbol: frame.sort_values("trade_time").reset_index(drop=True)
                for symbol, frame in day_minutes.groupby("symbol", sort=False)
            }
            if position:
                action = self._morning_exit_action(trade_date, position, minute_by_symbol, index_daily, daily_by_symbol)
                if action["sell"]:
                    cash, position, trade = self._sell(position, cash, minute_by_symbol, "10:40:00", action["reason"])
                    if trade:
                        trades.append(trade)
                if action["proxy_missing"]:
                    diagnostics["missing_proxy_minutes"] += 1

            target, ranking = self._select_target(
                trade_date,
                position["symbol"] if position else None,
                minute_by_symbol,
                daily_by_symbol,
                index_daily,
                names,
            )
            if ranking["missing_price_count"]:
                diagnostics["missing_1308"] += ranking["missing_price_count"]

            if position and position["symbol"] != target:
                cash, position, trade = self._sell(position, cash, minute_by_symbol, "13:09:00", "source_schedule_switch")
                if trade:
                    trades.append(trade)

            pending_symbol = None
            if target and not position:
                if self._is_uptrend(minute_by_symbol.get(target, pd.DataFrame()), "13:10:00"):
                    cash, position, trade = self._buy(target, cash, minute_by_symbol, "13:10:00", "source_schedule_trend_confirmed")
                    if trade:
                        trades.append(trade)
                else:
                    pending_symbol = target

            for check_time in ("13:40:00", "14:10:00", "14:40:00"):
                if pending_symbol and self._is_uptrend(minute_by_symbol.get(pending_symbol, pd.DataFrame()), check_time):
                    cash, position, trade = self._buy(pending_symbol, cash, minute_by_symbol, check_time, "source_schedule_trend_recheck")
                    if trade:
                        trades.append(trade)
                    pending_symbol = None

            if pending_symbol:
                cash, position, trade = self._buy(pending_symbol, cash, minute_by_symbol, "14:55:00", "source_schedule_forced_buy")
                if trade:
                    trades.append(trade)
                    diagnostics["forced_buys"] += 1

            equity = cash
            if position:
                mark = _price_at(minute_by_symbol.get(position["symbol"], pd.DataFrame()), "15:00:00")
                if mark > 0:
                    equity += int(position["quantity"]) * mark
                else:
                    equity += int(position["quantity"]) * float(position["entry_price"])
            curve.append({"date": trade_date, "equity": round(equity, 2), "symbol": position["symbol"] if position else "CASH"})

        ending_equity = curve[-1]["equity"] if curve else initial_cash
        result = {
            "mode": "wufu_v7_static_one_minute_backtest",
            "strategy_id": self.parameters.strategy_id,
            "strategy_version": self.parameters.strategy_version,
            "universe_mode": "source_fixed_pool_only",
            "universe_size": len(FILE_ETF_SYMBOLS),
            "defensive_etf": DEFENSIVE_ETF_SYMBOL,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "ending_equity": round(ending_equity, 2),
            "total_return_pct": round((ending_equity / initial_cash - 1) * 100, 4),
            "max_drawdown_pct": round(_max_drawdown([item["equity"] for item in curve]) * 100, 4),
            "trade_count": len(trades),
            "trades": trades,
            "equity_curve": curve,
            "diagnostics": diagnostics,
            "assumptions": {
                "signal_time": "13:08:00",
                "sell_time": "13:09:00",
                "first_buy_time": "13:10:00",
                "recheck_times": ["13:40:00", "14:10:00", "14:40:00"],
                "forced_buy_time": "14:55:00",
                "commission_rate": self.parameters.commission_rate,
                "minimum_commission": self.parameters.min_commission,
                "slippage_rate": self.parameters.slippage_rate,
                "source_fixes": [
                    "Repaired the source intraday snapshot tag mismatch.",
                    "Removed the source weak-period counter reset bug.",
                    "Uses CSI1000 and CSI500 ETF proxies for source index intraday prices.",
                ],
            },
        }
        return result

    def _ensure_index_history(self, start_date: str, end_date: str) -> None:
        existing = self.store.load_index_daily(end_date, start_date=start_date)
        existing_symbols = set(existing["symbol"].astype(str)) if not existing.empty else set()
        for symbol in WUFU_INDEX_SYMBOLS:
            if symbol in existing_symbols:
                continue
            rows = self.client.fetch_index_daily(symbol, start_date, end_date)
            self.store.upsert_index_bars(rows)

    def _names(self, as_of: str) -> dict[str, str]:
        universe = self.store.load_universe(as_of)
        names = {str(row["symbol"]).upper(): str(row.get("name") or row["symbol"]) for _, row in universe.iterrows()}
        names.setdefault(DEFENSIVE_ETF_SYMBOL, DEFENSIVE_ETF_SYMBOL)
        return names

    def _select_target(
        self,
        trade_date: str,
        current_symbol: str | None,
        minute_by_symbol: dict[str, pd.DataFrame],
        daily_by_symbol: dict[str, pd.DataFrame],
        index_daily: pd.DataFrame,
        names: dict[str, str],
    ) -> tuple[str | None, dict[str, Any]]:
        weak = self._is_weak_market(trade_date, index_daily)
        symbols = GLOBAL_ETF_SYMBOLS if weak else FILE_ETF_SYMBOLS
        liquidity_threshold = self._liquidity_threshold(trade_date, daily_by_symbol)
        candidates: list[dict[str, Any]] = []
        missing_price_count = 0
        for symbol in symbols:
            history = daily_by_symbol.get(symbol)
            if history is None:
                continue
            prior = history[history["trade_date"] < trade_date].tail(self.parameters.lookback_days)
            if len(prior) < self.parameters.lookback_days:
                continue
            price = _price_at(minute_by_symbol.get(symbol, pd.DataFrame()), "13:08:00")
            if price <= 0:
                missing_price_count += 1
                continue
            closes = pd.to_numeric(prior["close"], errors="coerce").dropna().to_numpy(dtype=float)
            if len(closes) != self.parameters.lookback_days or np.any(closes <= 0):
                continue
            score, annualized, r_squared = _source_momentum(np.append(closes, price))
            if not (self.parameters.score_min <= score <= self.parameters.score_max) or r_squared <= self.parameters.r_squared_min:
                continue
            avg_turnover = float(pd.to_numeric(prior.tail(self.parameters.liquidity_days)["amount"], errors="coerce").fillna(0).mean())
            avg_turnover *= self.settings.daily_amount_multiplier
            if avg_turnover <= liquidity_threshold:
                continue
            if not weak:
                values = np.append(closes, price)
                if price <= float(np.mean(values[-self.parameters.ma_days:])):
                    continue
                day_ratios = values[-3:] / values[-4:-1]
                if float(np.min(day_ratios)) < self.parameters.loss_floor:
                    continue
            candidates.append(
                {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol),
                    "score": score,
                    "annualized_trend": annualized,
                    "r_squared": r_squared,
                    "price": price,
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        top = candidates[:10]
        target: str | None = None
        if top:
            threshold = top[0]["score"] * self.parameters.candidate_ratio
            retained = {item["symbol"] for item in top if item["score"] >= threshold}
            target = current_symbol if current_symbol in retained else top[0]["symbol"]
        elif _price_at(minute_by_symbol.get(DEFENSIVE_ETF_SYMBOL, pd.DataFrame()), "13:08:00") > 0:
            target = DEFENSIVE_ETF_SYMBOL
        return target, {
            "weak_market": weak,
            "liquidity_threshold": liquidity_threshold,
            "candidates": top,
            "missing_price_count": missing_price_count,
        }

    def _is_weak_market(self, trade_date: str, index_daily: pd.DataFrame) -> bool:
        below = 0
        for symbol in WUFU_INDEX_SYMBOLS:
            rows = index_daily[(index_daily["symbol"] == symbol) & (index_daily["trade_date"] < trade_date)].sort_values("trade_date")
            closes = pd.to_numeric(rows["close"], errors="coerce").dropna().tail(self.parameters.ma_days)
            if len(closes) == self.parameters.ma_days and float(closes.iloc[-1]) < float(closes.mean()):
                below += 1
        return below >= self.parameters.weak_below_count

    def _liquidity_threshold(self, trade_date: str, daily_by_symbol: dict[str, pd.DataFrame]) -> float:
        totals: list[float] = []
        for symbol in FILE_ETF_SYMBOLS:
            history = daily_by_symbol.get(symbol)
            if history is None:
                continue
            values = pd.to_numeric(history[history["trade_date"] < trade_date].tail(self.parameters.liquidity_days)["amount"], errors="coerce").fillna(0)
            if len(values) == self.parameters.liquidity_days:
                totals.extend((values * self.settings.daily_amount_multiplier).tolist())
        return float(np.mean(totals) * len(FILE_ETF_SYMBOLS) / self.parameters.liquidity_divisor) if totals else 10_000_000.0

    def _morning_exit_action(
        self,
        trade_date: str,
        position: dict[str, Any],
        minute_by_symbol: dict[str, pd.DataFrame],
        index_daily: pd.DataFrame,
        daily_by_symbol: dict[str, pd.DataFrame],
    ) -> dict[str, Any]:
        proxy_returns: list[tuple[float, float]] = []
        for symbol, weight in zip(INTRADAY_PROXY_SYMBOLS, (0.65, 0.35)):
            bars = minute_by_symbol.get(symbol, pd.DataFrame())
            opening = _price_at(bars, "09:30:00")
            at_1000 = _price_at(bars, "10:00:00")
            at_1030 = _price_at(bars, "10:30:00")
            if opening > 0 and at_1000 > 0:
                proxy_returns.append((weight * 0.40, at_1000 / opening - 1.0))
            if opening > 0 and at_1030 > 0:
                proxy_returns.append((weight * 0.20, at_1030 / opening - 1.0))
        if not proxy_returns:
            return {"sell": False, "reason": "source_morning_proxy_data_missing", "proxy_missing": True}
        score = sum(weight * value for weight, value in proxy_returns)
        signs = [value > 0 for _, value in proxy_returns]
        confidence = max(sum(signs), len(signs) - sum(signs)) / len(signs)
        label = _signal_label(score, confidence)
        regime = self._intraday_regime(trade_date, index_daily)
        multiplier = {"TRENDING_UP": 1.0, "TRENDING_DOWN": 0.6, "CHOPPY": 0.3, "EXTREME_VOL": 0.0}[regime]
        if multiplier == 0.0:
            return {"sell": False, "reason": "source_extreme_vol_normal_rebalance", "proxy_missing": False}
        price = _price_at(minute_by_symbol.get(position["symbol"], pd.DataFrame()), "10:40:00")
        prior = daily_by_symbol.get(position["symbol"], pd.DataFrame())
        prior_rows = prior[prior["trade_date"] < trade_date] if not prior.empty else prior
        previous_close = float(prior_rows.iloc[-1]["close"]) if len(prior_rows) else 0.0
        day_return = price / previous_close - 1.0 if price > 0 and previous_close > 0 else 0.0
        unrealized = price / float(position["entry_price"]) - 1.0 if price > 0 else 0.0
        adjusted = score * multiplier
        urgent = adjusted < -0.03 or (day_return < -0.03 and label in {"strong_negative", "weak_negative"})
        if unrealized > 0.05:
            urgent = urgent or label in {"weak_negative", "strong_negative"}
        elif unrealized >= 0:
            urgent = urgent or label in {"weak_negative", "strong_negative"}
        elif unrealized > -0.03:
            urgent = urgent or label in {"neutral", "weak_negative", "strong_negative"}
        else:
            urgent = True
        return {"sell": bool(urgent), "reason": f"source_1040_{label}_{regime}", "proxy_missing": False}

    def _intraday_regime(self, trade_date: str, index_daily: pd.DataFrame) -> str:
        rows = index_daily[index_daily["trade_date"] < trade_date]
        returns = []
        above = 0
        choppy = 0
        for symbol in WUFU_INDEX_SYMBOLS:
            closes = pd.to_numeric(rows[rows["symbol"] == symbol].sort_values("trade_date")["close"], errors="coerce").dropna()
            if len(closes) >= 20:
                above += int(float(closes.iloc[-1]) > float(closes.iloc[-20:].mean()))
            if len(closes) >= 11:
                choppy += int(abs(float(closes.iloc[-1] / closes.iloc[-11] - 1.0)) < 0.01)
            if symbol == "000300.SH" and len(closes) >= 21:
                returns = np.diff(np.log(closes.iloc[-21:].to_numpy(dtype=float))).tolist()
        if returns and float(np.std(returns) * math.sqrt(252)) > 0.45:
            return "EXTREME_VOL"
        if choppy >= 3:
            return "CHOPPY"
        if above >= 3:
            return "TRENDING_UP"
        return "TRENDING_DOWN" if above <= 1 else "CHOPPY"

    def _is_uptrend(self, bars: pd.DataFrame, time_of_day: str) -> bool:
        closes = _closes_until(bars, time_of_day, self.parameters.trend_lookback_minutes)
        if len(closes) < 5:
            return False
        slope = float(np.polyfit(np.arange(len(closes), dtype=float), closes, 1)[0])
        slope_pct = slope / float(np.mean(closes)) * 100.0
        return slope_pct > self.parameters.trend_slope_threshold_pct

    def _buy(
        self,
        symbol: str,
        cash: float,
        minute_by_symbol: dict[str, pd.DataFrame],
        time_of_day: str,
        reason: str,
    ) -> tuple[float, dict[str, Any] | None, dict[str, Any] | None]:
        price = _price_at(minute_by_symbol.get(symbol, pd.DataFrame()), time_of_day)
        if price <= 0:
            return cash, None, None
        execution_price = price * (1 + self.parameters.slippage_rate)
        quantity = int((cash - self.parameters.min_commission) / (execution_price * (1 + self.parameters.commission_rate)) // 100 * 100)
        while quantity > 0:
            value = quantity * execution_price
            commission = max(self.parameters.min_commission, value * self.parameters.commission_rate)
            if value + commission <= cash:
                break
            quantity -= 100
        if quantity <= 0:
            return cash, None, None
        value = quantity * execution_price
        commission = max(self.parameters.min_commission, value * self.parameters.commission_rate)
        trade = {
            "date": _date_from_bars(minute_by_symbol.get(symbol, pd.DataFrame())),
            "time": time_of_day,
            "side": "BUY",
            "symbol": symbol,
            "quantity": quantity,
            "price": round(execution_price, 6),
            "commission": round(commission, 2),
            "reason": reason,
        }
        return cash - value - commission, {"symbol": symbol, "quantity": quantity, "entry_price": execution_price}, trade

    def _sell(
        self,
        position: dict[str, Any],
        cash: float,
        minute_by_symbol: dict[str, pd.DataFrame],
        time_of_day: str,
        reason: str,
    ) -> tuple[float, None, dict[str, Any] | None]:
        price = _price_at(minute_by_symbol.get(position["symbol"], pd.DataFrame()), time_of_day)
        if price <= 0:
            return cash, position, None
        execution_price = price * (1 - self.parameters.slippage_rate)
        value = int(position["quantity"]) * execution_price
        commission = max(self.parameters.min_commission, value * self.parameters.commission_rate)
        trade = {
            "date": _date_from_bars(minute_by_symbol.get(position["symbol"], pd.DataFrame())),
            "time": time_of_day,
            "side": "SELL",
            "symbol": position["symbol"],
            "quantity": int(position["quantity"]),
            "price": round(execution_price, 6),
            "commission": round(commission, 2),
            "reason": reason,
        }
        return cash + value - commission, None, trade


def _source_momentum(prices: np.ndarray) -> tuple[float, float, float]:
    y = np.log(prices)
    x = np.arange(len(y), dtype=float)
    weights = np.linspace(1.0, 2.0, len(y))
    squared = weights**2
    x_bar = float(np.sum(squared * x) / np.sum(squared))
    y_bar = float(np.sum(squared * y) / np.sum(squared))
    denominator = float(np.sum(squared * (x - x_bar) ** 2))
    if denominator <= 0:
        return 0.0, 0.0, 0.0
    slope = float(np.sum(squared * (x - x_bar) * (y - y_bar)) / denominator)
    intercept = y_bar - slope * x_bar
    annualized = math.exp(slope * 250) - 1.0
    predicted = slope * x + intercept
    ss_res = float(np.sum(weights * (y - predicted) ** 2))
    ss_tot = float(np.sum(weights * (y - np.mean(y)) ** 2))
    r_squared = max(0.0, min(1.0, 1.0 - ss_res / ss_tot)) if ss_tot > 0 else 0.0
    return annualized * r_squared, annualized, r_squared


def _fetch_minute_window(
    settings: ETFStrategySettings,
    task: tuple[str, str, str],
) -> tuple[str, str, str, pd.DataFrame]:
    """Process-isolated TuShare minute request with bounded retries."""
    symbol, window_start, window_end = task
    for attempt in range(3):
        try:
            client = TuShareETFClient(settings)
            bars = client.fetch_historical_minutes(symbol, window_start, window_end, "1min")
            return symbol, window_start, window_end, bars
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _signal_label(score: float, confidence: float) -> str:
    if confidence < 0.55:
        return "neutral"
    if score >= 0.005:
        return "strong_positive"
    if score >= 0.002:
        return "weak_positive"
    if score <= -0.005:
        return "strong_negative"
    if score <= -0.002:
        return "weak_negative"
    return "neutral"


def _price_at(bars: pd.DataFrame, time_of_day: str) -> float:
    if bars.empty:
        return 0.0
    cutoff = pd.to_datetime(f"{bars.iloc[0]['trade_time'].date()} {time_of_day}")
    rows = bars[bars["trade_time"] <= cutoff]
    return float(rows.iloc[-1]["close"]) if not rows.empty else 0.0


def _closes_until(bars: pd.DataFrame, time_of_day: str, count: int) -> np.ndarray:
    if bars.empty:
        return np.array([])
    cutoff = pd.to_datetime(f"{bars.iloc[0]['trade_time'].date()} {time_of_day}")
    return pd.to_numeric(bars[bars["trade_time"] <= cutoff]["close"], errors="coerce").dropna().tail(count).to_numpy(dtype=float)


def _minute_windows(start: date, end: date, calendar_days: int) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    current = start
    while current <= end:
        final = min(current + timedelta(days=max(calendar_days - 1, 1)), end)
        output.append((f"{current.isoformat()} 09:30:00", f"{final.isoformat()} 15:00:00"))
        current = final + timedelta(days=1)
    return output


def _as_date(value: str) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()


def _display_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _date_from_bars(bars: pd.DataFrame) -> str:
    if bars.empty:
        return ""
    return pd.to_datetime(bars.iloc[0]["trade_time"]).strftime("%Y%m%d")


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, 1 - value / peak)
    return drawdown
