from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from etf_strategy.ai_reviewer import ETFMiniMaxReviewer, apply_ai_review_to_plan
from etf_strategy.config import ETFStrategySettings, load_settings
from etf_strategy.file_universe import FILE_ETF_SYMBOLS, restrict_to_file_universe
from etf_strategy.storage import ETFLocalStore
from etf_strategy.strategy import INDEX_SYMBOLS, WufuETFStrategy
from etf_strategy.tushare_client import ETFDataError, TuShareETFClient
from etf_strategy.wufu_v7_static import WufuV7StaticMinuteBacktester


logger = logging.getLogger(__name__)


class ETFStrategyRunner:
    """Manual local workflow for ETF data refresh, plans, and offline backtests."""

    def __init__(
        self,
        settings: ETFStrategySettings | None = None,
        client: TuShareETFClient | None = None,
        store: ETFLocalStore | None = None,
        strategy: WufuETFStrategy | None = None,
    ):
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        self.store = store or ETFLocalStore(self.settings.db_path)
        self.client = client or TuShareETFClient(self.settings)
        self.strategy = strategy or WufuETFStrategy(self.settings)
        self.ai_reviewer = ETFMiniMaxReviewer(self.settings)

    def refresh(
        self,
        as_of: str | None = None,
        lookback_calendar_days: int = 120,
        force: bool = False,
    ) -> dict[str, Any]:
        end_date = _as_date(as_of or date.today().strftime("%Y%m%d"))
        start_date = end_date - timedelta(days=max(lookback_calendar_days, 60))
        instruments = self.client.fetch_etf_universe()
        universe_count = self.store.upsert_universe(instruments)
        trade_dates = self.client.fetch_open_trade_dates(
            start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
        )
        daily_rows = 0
        fetched_dates = 0
        errors: list[str] = []
        for trade_date in trade_dates:
            if not force and self.store.has_daily_snapshot(trade_date):
                continue
            try:
                bars, endpoint = self.client.fetch_daily_snapshot(trade_date)
                if bars:
                    daily_rows += self.store.upsert_daily_bars(bars)
                    fetched_dates += 1
                else:
                    errors.append(f"{trade_date}: {endpoint} returned no ETF bars")
            except ETFDataError as exc:
                errors.append(f"{trade_date}: {exc}")

        index_rows = 0
        for symbol in INDEX_SYMBOLS:
            try:
                index_rows += self.store.upsert_index_bars(
                    self.client.fetch_index_daily(
                        symbol,
                        start_date.strftime("%Y%m%d"),
                        end_date.strftime("%Y%m%d"),
                    )
                )
            except ETFDataError as exc:
                errors.append(f"{symbol}: {exc}")

        summary = {
            "mode": "local_refresh_only",
            "as_of": end_date.strftime("%Y%m%d"),
            "universe_rows": universe_count,
            "daily_rows": daily_rows,
            "daily_dates_fetched": fetched_dates,
            "index_rows": index_rows,
            "errors": errors,
            "db_path": str(self.settings.db_path),
        }
        if not self.store.latest_trade_date():
            raise ETFDataError(
                "ETF refresh completed without usable daily bars. Check TuShare ETF endpoint permissions and proxy configuration."
            )
        return summary

    def decide(
        self,
        as_of: str | None = None,
        account_value: float = 1_000_000.0,
        current_symbol: str | None = None,
        confirm_intraday: bool = False,
        enrich_nav: bool = False,
        with_ai: bool = False,
    ) -> dict[str, Any]:
        decision_date = as_of or self.store.latest_trade_date()
        if not decision_date:
            raise ETFDataError("No local ETF daily data. Run `python -m etf_strategy refresh` first.")
        universe = restrict_to_file_universe(self.store.load_universe(decision_date))
        daily = self.store.load_etf_daily(decision_date)
        index_daily = self.store.load_index_daily(decision_date)
        plan = self.strategy.decide(
            as_of=decision_date,
            universe=universe,
            daily=daily,
            index_daily=index_daily,
            account_value=account_value,
            current_symbol=current_symbol,
        )

        if plan.target and enrich_nav:
            try:
                nav_start = (_as_date(decision_date) - timedelta(days=14)).strftime("%Y%m%d")
                nav_rows = self.client.fetch_nav(plan.target["symbol"], nav_start, decision_date)
                self.store.upsert_nav_rows(nav_rows)
                plan = self.strategy.apply_nav_check(
                    plan,
                    self.store.load_latest_nav(plan.target["symbol"], decision_date),
                )
            except ETFDataError as exc:
                plan.nav_check = {"status": "unavailable", "error": str(exc)}

        if plan.target and confirm_intraday:
            try:
                plan = self.strategy.confirm_intraday(
                    plan,
                    self.client.fetch_today_minutes(plan.target["symbol"]),
                )
            except ETFDataError as exc:
                plan = self.strategy.confirm_intraday(plan, pd.DataFrame())
                plan.intraday_confirmation["error"] = str(exc)

        if with_ai:
            apply_ai_review_to_plan(plan, self.ai_reviewer.review(plan.to_dict()))

        payload = plan.to_dict()
        payload["decision_id"] = self.store.save_decision(payload)
        payload["report_path"] = str(self._write_report("decision", decision_date, payload))
        return payload

    def run(
        self,
        as_of: str | None = None,
        account_value: float = 1_000_000.0,
        current_symbol: str | None = None,
        confirm_intraday: bool = False,
        enrich_nav: bool = False,
        with_ai: bool = False,
    ) -> dict[str, Any]:
        refresh = self.refresh(as_of=as_of)
        decision = self.decide(
            as_of=as_of,
            account_value=account_value,
            current_symbol=current_symbol,
            confirm_intraday=confirm_intraday,
            enrich_nav=enrich_nav,
            with_ai=with_ai,
        )
        return {"refresh": refresh, "decision": decision, "mode": "local_decision_support_only"}

    def backtest(
        self,
        start_date: str,
        end_date: str,
        initial_cash: float = 1_000_000.0,
        cost_bps_per_side: float = 10.0,
    ) -> dict[str, Any]:
        """A conservative daily-close signal / next-open execution research replay.

        It is deliberately simple: no same-day sell-buy assumption, no AI, no
        current-day data in the signal, and a configurable transaction-cost grid.
        """
        # The strategy only needs a short warm-up window for its 25-day trend
        # and 10-day regime inputs. Loading all prior years multiplies memory
        # use for later annual replays without changing the signal.
        warmup_start = (
            _as_date(start_date)
            - timedelta(days=max(90, self.strategy.parameters.lookback_days * 4))
        ).strftime("%Y%m%d")
        daily = self.store.load_etf_daily(
            end_date,
            start_date=warmup_start,
            symbols=FILE_ETF_SYMBOLS,
        )
        index_daily = self.store.load_index_daily(end_date, start_date=warmup_start)
        if daily.empty or index_daily.empty:
            raise ETFDataError("ETF and index daily data are required for local backtest.")
        all_dates = sorted(
            item
            for item in daily["trade_date"].dropna().astype(str).unique().tolist()
            if item <= end_date
        )
        try:
            start_index = next(index for index, item in enumerate(all_dates) if item >= start_date)
        except StopIteration as exc:
            raise ETFDataError("The requested backtest window has no locally cached ETF dates.") from exc
        if start_index < self.strategy.parameters.lookback_days or len(all_dates) - start_index < 2:
            raise ETFDataError(
                "Insufficient pre-start ETF history or too few dates in the requested backtest window. "
                "Refresh at least 25 prior trading days."
            )
        daily_by_date = {
            trade_date: rows.copy()
            for trade_date, rows in daily.groupby("trade_date", sort=False)
        }
        index_by_date = {
            trade_date: rows.copy()
            for trade_date, rows in index_daily.groupby("trade_date", sort=False)
        }

        cash = float(initial_cash)
        position_symbol: str | None = None
        quantity = 0
        trades: list[dict[str, Any]] = []
        curve: list[dict[str, Any]] = []
        cost_rate = max(cost_bps_per_side, 0.0) / 10_000.0

        # Dates preceding start_date warm the indicators only; performance begins
        # with the first next-open execution after the requested start date.
        for index in range(start_index, len(all_dates) - 1):
            signal_date = all_dates[index]
            execution_date = all_dates[index + 1]
            signal_window_dates = all_dates[index - self.strategy.parameters.lookback_days : index + 1]
            signal_daily = pd.concat(
                [daily_by_date[trade_date] for trade_date in signal_window_dates],
                ignore_index=True,
            )
            index_window_dates = all_dates[index - self.strategy.parameters.ma_days + 1 : index + 1]
            signal_index_daily = pd.concat(
                [index_by_date[trade_date] for trade_date in index_window_dates if trade_date in index_by_date],
                ignore_index=True,
            )
            universe = restrict_to_file_universe(self.store.load_universe(signal_date))
            plan = self.strategy.decide(
                as_of=signal_date,
                universe=universe,
                daily=signal_daily,
                index_daily=signal_index_daily,
                account_value=self._equity(daily, signal_date, cash, position_symbol, quantity),
                current_symbol=position_symbol,
            )
            target_symbol = (plan.target or {}).get("symbol") if plan.signal in {"BUY", "SWITCH", "HOLD"} else None
            if plan.signal == "SELL":
                target_symbol = None

            if target_symbol != position_symbol:
                if position_symbol and quantity > 0:
                    sell_price = self._execution_price(daily, position_symbol, execution_date, "SELL")
                    if sell_price > 0:
                        proceeds = quantity * sell_price * (1 - cost_rate)
                        cash += proceeds
                        trades.append({"date": execution_date, "side": "SELL", "symbol": position_symbol, "quantity": quantity, "price": sell_price})
                    position_symbol = None
                    quantity = 0
                if target_symbol and plan.target_weight > 0:
                    buy_price = self._execution_price(daily, target_symbol, execution_date, "BUY")
                    if buy_price > 0:
                        equity = self._equity(daily, signal_date, cash, None, 0)
                        budget = equity * plan.target_weight
                        quantity = int(budget / (buy_price * (1 + cost_rate)) // 100 * 100)
                        if quantity > 0:
                            cash -= quantity * buy_price * (1 + cost_rate)
                            position_symbol = target_symbol
                            trades.append({"date": execution_date, "side": "BUY", "symbol": target_symbol, "quantity": quantity, "price": buy_price})

            equity = self._equity(daily, execution_date, cash, position_symbol, quantity)
            curve.append({"date": execution_date, "equity": round(equity, 2), "symbol": position_symbol or "CASH"})

        equities = [row["equity"] for row in curve]
        max_drawdown = _max_drawdown(equities)
        ending_equity = equities[-1] if equities else initial_cash
        result = {
            "mode": "local_research_backtest_only",
            "strategy_id": self.strategy.parameters.strategy_id,
            "universe_mode": "file_static_only",
            "universe_size": len(FILE_ETF_SYMBOLS),
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "ending_equity": round(ending_equity, 2),
            "total_return_pct": round((ending_equity / initial_cash - 1) * 100, 4),
            "max_drawdown_pct": round(max_drawdown * 100, 4),
            "trade_count": len(trades),
            "cost_bps_per_side": cost_bps_per_side,
            "trades": trades,
            "equity_curve": curve,
        }
        result["report_path"] = str(self._write_report("backtest", f"{start_date}_{end_date}", result))
        return result

    def backtest_wufu_v7(
        self,
        start_date: str,
        end_date: str,
        initial_cash: float = 1_000_000.0,
        fetch_minutes: bool = False,
        minute_fetch_workers: int = 4,
    ) -> dict[str, Any]:
        """Run the source-compatible static-pool Wufu V7 minute replay."""
        engine = WufuV7StaticMinuteBacktester(self.settings, self.store, self.client)
        cache_summary: dict[str, Any] | None = None
        if fetch_minutes:
            cache_summary = engine.cache_minutes(start_date, end_date, minute_fetch_workers)
            if cache_summary["failures"]:
                raise ETFDataError(
                    f"Minute cache is incomplete ({len(cache_summary['failures'])} failed windows); rerun safely to resume."
                )
        result = engine.backtest(start_date, end_date, initial_cash)
        result["minute_cache"] = cache_summary
        result["report_path"] = str(self._write_report("wufu_v7_static_1m", f"{start_date}_{end_date}", result))
        return result

    def _equity(
        self,
        daily: pd.DataFrame,
        trade_date: str,
        cash: float,
        symbol: str | None,
        quantity: int,
    ) -> float:
        if not symbol or quantity <= 0:
            return cash
        rows = daily[(daily["symbol"] == symbol) & (daily["trade_date"] == trade_date)]
        if rows.empty:
            return cash
        return cash + quantity * float(rows.iloc[-1]["close"])

    def _execution_price(self, daily: pd.DataFrame, symbol: str, trade_date: str, side: str) -> float:
        rows = daily[(daily["symbol"] == symbol) & (daily["trade_date"] == trade_date)]
        if rows.empty:
            return 0.0
        row = rows.iloc[-1]
        price = float(row.get("open") or row.get("close") or 0.0)
        return price if price > 0 else 0.0

    def _write_report(self, kind: str, suffix: str, payload: dict[str, Any]) -> Path:
        safe_suffix = "".join(character for character in suffix if character.isalnum() or character in {"_", "-"})
        path = self.settings.reports_dir / f"{kind}_{safe_suffix}_{uuid4().hex[:8]}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _as_date(value: str) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, 1 - value / peak)
    return drawdown
