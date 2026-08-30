from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from math import floor
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from etf_strategy.config import ETFStrategySettings, load_settings
from etf_strategy.ai_reviewer import (
    ETFMiniMaxReviewer,
    apply_ai_review_to_plan,
    execution_context_from_plan,
)
from etf_strategy.file_universe import (
    DEFENSIVE_ETF_SYMBOL,
    FILE_ETF_SYMBOLS,
    GLOBAL_ETF_SYMBOLS,
    bucket_for_symbol,
    restrict_to_file_universe,
)
from etf_strategy.models import ETFCandidate, ETFDecisionPlan
from etf_strategy.storage import ETFLocalStore
from etf_strategy.strategy import WufuETFStrategy
from etf_strategy.tushare_client import ETFDataError, TuShareETFClient
from etf_strategy.wufu_v7_static import (
    INTRADAY_PROXY_SYMBOLS,
    WUFU_INDEX_SYMBOLS,
    WufuV7StaticMinuteBacktester,
    _price_at,
)
from scheduler.trading_calendar import current_trading_window


logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
ETF_PAPER_ACCOUNT_ID = "etf_minute_paper"


class ETFMinutePaperRunner:
    """Minute-level ETF decision support with an isolated simulated account.

    This runner never routes orders to a broker. It only records simulated ETF
    fills after a daily momentum plan passes a closed 1-minute trend check.
    """

    def __init__(
        self,
        settings: ETFStrategySettings | None = None,
        store: ETFLocalStore | None = None,
        client: TuShareETFClient | None = None,
        strategy: WufuETFStrategy | None = None,
    ):
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        self.store = store or ETFLocalStore(self.settings.db_path)
        self.client = client or TuShareETFClient(self.settings)
        self.strategy = strategy or WufuETFStrategy(self.settings)
        self.ai_reviewer = ETFMiniMaxReviewer(self.settings)
        self.source_strategy = WufuV7StaticMinuteBacktester(
            self.settings,
            self.store,
            self.client,
        )

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        current_time = _beijing_time(now)
        if self.settings.minute_strategy_profile == "wufu_v7_static":
            return self._run_wufu_v7_static_once(current_time)
        window = current_trading_window(current_time)
        if not window.is_open:
            return {
                "status": "skipped_market_closed",
                "reason": window.reason,
                "mode": "etf_minute_paper_trading_only",
                "no_real_orders": True,
                "orders": [],
                "account": {},
                "plan": {},
                "stale_symbols": [],
            }
        trade_date = current_time.strftime("%Y-%m-%d")
        compact_date = current_time.strftime("%Y%m%d")
        state = self.store.load_paper_account(
            ETF_PAPER_ACCOUNT_ID,
            self.settings.minute_initial_cash,
            trade_date,
        )
        account = state["account"]
        positions = state["positions"]
        held_symbol = next(iter(positions), None)

        daily_as_of = self.store.latest_trade_date()
        if not daily_as_of:
            return self._result("skipped_missing_daily_context", account, positions, [], "缺少ETF日线上下文")

        daily = self.store.load_etf_daily(daily_as_of)
        index_daily = self.store.load_index_daily(daily_as_of)
        universe = restrict_to_file_universe(self.store.load_universe(daily_as_of))
        account_value = _account_equity(account, positions)
        plan = self.strategy.decide(
            as_of=daily_as_of,
            universe=universe,
            daily=daily,
            index_daily=index_daily,
            account_value=account_value,
            current_symbol=held_symbol,
        )

        target = plan.target if isinstance(plan.target, dict) else {}
        symbols = {symbol for symbol in (held_symbol, str(target.get("symbol") or "")) if symbol}
        bars_by_symbol: dict[str, pd.DataFrame] = {}
        prices: dict[str, float] = {}
        stale_symbols: list[str] = []
        for symbol in sorted(symbols):
            try:
                bars = _normalize_realtime_minutes(self.client.fetch_today_minutes(symbol), current_time)
            except ETFDataError as exc:
                logger.warning("ETF minute data unavailable for %s: %s", symbol, exc)
                bars = pd.DataFrame()
            if bars.empty:
                stale_symbols.append(symbol)
                continue
            self.store.upsert_minute_bars(
                symbol,
                bars,
                source=str(bars.attrs.get("source") or "tushare:rt_etf_min_daily"),
            )
            latest_time = pd.to_datetime(bars.iloc[-1]["trade_time"], errors="coerce")
            if pd.isna(latest_time) or (current_time.replace(tzinfo=None) - latest_time).total_seconds() > self.settings.minute_max_stale_seconds:
                stale_symbols.append(symbol)
                continue
            bars_by_symbol[symbol] = bars
            prices[symbol] = float(bars.iloc[-1]["close"])

        _mark_prices(positions, prices)
        account_value = _account_equity(account, positions)
        if held_symbol and held_symbol not in prices:
            return self._complete(
                "skipped_stale_position_price",
                account,
                positions,
                [],
                "持仓分钟行情不可用",
                plan,
                stale_symbols,
            )

        if target and plan.signal in {"BUY", "SWITCH"}:
            plan = self.strategy.confirm_intraday(plan, bars_by_symbol.get(str(target.get("symbol") or ""), pd.DataFrame()))

        if self.settings.ai_enabled and plan.signal in {"BUY", "SWITCH"}:
            apply_ai_review_to_plan(plan, self.ai_reviewer.review(plan.to_dict()))

        orders: list[dict[str, Any]] = []
        execution_context = execution_context_from_plan(plan)
        if held_symbol and _stop_triggered(positions[held_symbol], prices.get(held_symbol, 0.0), self.settings.minute_stop_loss_pct):
            order = self._sell(
                account,
                positions,
                symbol=held_symbol,
                price=prices[held_symbol],
                trade_date=trade_date,
                dedupe_key=f"{compact_date}:STOP_SELL:{held_symbol}",
                reason="ETF分钟风控止损",
                execution_context=execution_context,
            )
            if order:
                orders.append(order)
        elif plan.signal == "SELL" and held_symbol:
            order = self._sell(
                account,
                positions,
                symbol=held_symbol,
                price=prices.get(held_symbol, 0.0),
                trade_date=trade_date,
                dedupe_key=f"{compact_date}:SELL:{held_symbol}",
                reason="ETF日线计划转为空仓",
                execution_context=execution_context,
            )
            if order:
                orders.append(order)
        elif plan.signal in {"BUY", "SWITCH"} and plan.target:
            target_symbol = str(plan.target["symbol"])
            target_name = str(plan.target.get("name") or target_symbol)
            target_price = prices.get(target_symbol, 0.0)
            if target_price <= 0:
                return self._complete(
                    "skipped_stale_target_price",
                    account,
                    positions,
                    orders,
                    "目标ETF分钟行情不可用",
                    plan,
                    stale_symbols,
                )
            if held_symbol and held_symbol != target_symbol:
                sell_order = self._sell(
                    account,
                    positions,
                    symbol=held_symbol,
                    price=prices.get(held_symbol, 0.0),
                    trade_date=trade_date,
                    dedupe_key=f"{compact_date}:SWITCH_SELL:{held_symbol}",
                    reason="ETF目标切换",
                    execution_context=execution_context,
                )
                if sell_order:
                    orders.append(sell_order)
                # A failed T+1 sale must not create an unintended second ETF position.
                if held_symbol in positions:
                    return self._complete(
                        "skipped_t1_switch",
                        account,
                        positions,
                        orders,
                        "现有ETF不可卖，延后切换",
                        plan,
                        stale_symbols,
                    )
            if target_symbol not in positions:
                buy_order = self._buy(
                    account,
                    positions,
                    symbol=target_symbol,
                    name=target_name,
                    price=target_price,
                    target_weight=min(plan.target_weight, self.settings.minute_max_position_weight),
                    trade_date=trade_date,
                    dedupe_key=f"{compact_date}:BUY:{target_symbol}",
                    reason="ETF日线候选通过分钟趋势确认",
                    execution_context=execution_context,
                )
                if buy_order:
                    orders.append(buy_order)

        if not orders:
            self.store.save_paper_account_state(account, positions)
        return self._complete("ok", account, positions, orders, "", plan, stale_symbols)

    def refresh_source_context(self, now: datetime | None = None) -> dict[str, Any]:
        """Load the prior close needed by the file strategy before trading.

        This is deliberately data preparation only. It fetches no prices for a
        trading decision and makes no paper orders.
        """
        current_time = _beijing_time(now)
        compact_date = current_time.strftime("%Y%m%d")
        session = self.store.load_wufu_v7_session(compact_date)
        if session.get("context_ready"):
            return {"status": "already_ready", "daily_as_of": session.get("daily_as_of")}

        open_dates = self.client.fetch_open_trade_dates(
            (current_time.date() - timedelta(days=20)).strftime("%Y%m%d"),
            (current_time.date() - timedelta(days=1)).strftime("%Y%m%d"),
        )
        if not open_dates:
            return {"status": "missing_prior_trade_date"}
        daily_as_of = open_dates[-1]
        errors: list[str] = []
        universe = self.store.load_universe(daily_as_of)
        known_symbols = set(universe["symbol"].astype(str).str.upper()) if not universe.empty else set()
        if not FILE_ETF_SYMBOLS.issubset(known_symbols):
            try:
                self.store.upsert_universe(self.client.fetch_etf_universe())
            except ETFDataError as exc:
                errors.append(f"universe: {exc}")

        warmup_start = (current_time.date() - timedelta(days=100)).strftime("%Y%m%d")
        cached = self.store.load_etf_daily(
            daily_as_of,
            start_date=warmup_start,
            symbols=FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL},
        )
        cached_counts = cached.groupby("symbol")["trade_date"].nunique().to_dict() if not cached.empty else {}
        # The source loop simply skips unavailable or newly listed ETFs. It
        # must not halt the whole fixed pool because one old code lacks 25 bars.
        needs_history = not any(
            int(count) >= self.source_strategy.parameters.lookback_days
            for count in cached_counts.values()
        )
        # TuShare remains the primary daily source. When a new local database
        # needs warm-up data, do not fan out one whole-market proxy request for
        # every historical date: that was the source of prolonged timeouts.
        fetch_dates = [daily_as_of] if needs_history or not self.store.has_daily_snapshot(daily_as_of) else []
        for trade_date in fetch_dates:
            try:
                rows, _ = self.client.fetch_daily_snapshot(trade_date)
                self.store.upsert_daily_bars(rows)
            except ETFDataError as exc:
                errors.append(f"daily {trade_date}: {exc}")

        # A rebuilt cache needs only the strategy's bounded 25-day warm-up
        # window. Fill missing symbols through single-symbol TuShare requests
        # (with AKShare only as a fallback). This must stay serial: both the
        # TuShare proxy and Eastmoney may close a burst of parallel history
        # requests, which would otherwise leave the whole static pool empty.
        history_start = (current_time.date() - timedelta(days=60)).strftime("%Y%m%d")
        history = self.store.load_etf_daily(
            daily_as_of,
            start_date=history_start,
            symbols=FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL},
        )
        history_counts = history.groupby("symbol")["trade_date"].nunique().to_dict() if not history.empty else {}
        missing_history = {
            symbol
            for symbol in FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL}
            if int(history_counts.get(symbol, 0)) < self.source_strategy.parameters.lookback_days
        }
        daily_fallback = getattr(
            self.client,
            "fetch_daily_history",
            getattr(self.client, "fetch_daily_history_akshare", None),
        )
        if missing_history and callable(daily_fallback):
            recovered = 0

            def load_history(symbol: str) -> tuple[str, list[Any]]:
                return symbol, daily_fallback(symbol, history_start, daily_as_of)

            with ThreadPoolExecutor(max_workers=1) as executor:
                futures = {executor.submit(load_history, symbol): symbol for symbol in sorted(missing_history)}
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        _, rows = future.result()
                    except ETFDataError as exc:
                        errors.append(f"daily history {symbol}: {exc}")
                        continue
                    except Exception as exc:
                        errors.append(f"daily history {symbol}: {exc}")
                        continue
                    recovered += self.store.upsert_daily_bars(rows)
            errors.append(f"daily_history_recovery_rows={recovered}")
        elif missing_history:
            errors.append("daily history fallback unavailable")

        # The source strategy only needs the fixed file pool here. If the
        # proxy's all-ETF daily snapshot times out, recover the prior close from
        # one batch board snapshot instead of accepting a silently stale plan.
        required_daily_symbols = FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL}
        daily_snapshot = self.store.load_etf_daily(daily_as_of, symbols=required_daily_symbols)
        daily_coverage = set(daily_snapshot.loc[daily_snapshot["trade_date"] == daily_as_of, "symbol"].astype(str)) if not daily_snapshot.empty else set()
        if len(daily_coverage) / max(len(required_daily_symbols), 1) < self.settings.minute_batch_spot_min_coverage:
            try:
                fallback_rows = self.client.fetch_realtime_spot_daily(required_daily_symbols, daily_as_of)
                fallback_symbols = {row.symbol for row in fallback_rows}
                if len(fallback_symbols) / max(len(required_daily_symbols), 1) >= self.settings.minute_batch_spot_min_coverage:
                    self.store.upsert_daily_bars(fallback_rows)
                    errors.append(f"daily_batch_spot_fallback={len(fallback_symbols)}/{len(required_daily_symbols)}")
                else:
                    errors.append(f"daily_batch_spot_coverage_insufficient={len(fallback_symbols)}/{len(required_daily_symbols)}")
            except ETFDataError as exc:
                errors.append(f"daily batch spot: {exc}")

        for symbol in WUFU_INDEX_SYMBOLS:
            try:
                rows = self.client.fetch_index_daily(symbol, warmup_start, daily_as_of)
                self.store.upsert_index_bars(rows)
            except ETFDataError as exc:
                errors.append(f"index {symbol}: {exc}")

        index_history = self.store.load_index_daily(daily_as_of, start_date=history_start)
        index_counts = index_history.groupby("symbol")["trade_date"].nunique().to_dict() if not index_history.empty else {}
        missing_index_history = {
            symbol
            for symbol in WUFU_INDEX_SYMBOLS
            if int(index_counts.get(symbol, 0)) < self.source_strategy.parameters.ma_days
        }
        index_fallback = getattr(self.client, "fetch_index_daily_akshare", None)
        if missing_index_history and callable(index_fallback):
            for symbol in sorted(missing_index_history):
                try:
                    self.store.upsert_index_bars(index_fallback(symbol, history_start, daily_as_of))
                except ETFDataError as exc:
                    errors.append(f"index history {symbol}: {exc}")
                except Exception as exc:
                    errors.append(f"index history {symbol}: {exc}")

        daily = self.store.load_etf_daily(
            daily_as_of,
            start_date=warmup_start,
            symbols=FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL},
        )
        index_daily = self.store.load_index_daily(daily_as_of, start_date=warmup_start)
        daily_counts = daily.groupby("symbol")["trade_date"].nunique().to_dict() if not daily.empty else {}
        index_counts = index_daily.groupby("symbol")["trade_date"].nunique().to_dict() if not index_daily.empty else {}
        daily_ready_symbols = sum(
            int(daily_counts.get(symbol, 0)) >= self.source_strategy.parameters.lookback_days
            for symbol in FILE_ETF_SYMBOLS
        )
        daily_ready_ratio = daily_ready_symbols / max(len(FILE_ETF_SYMBOLS), 1)
        ready = (
            daily_ready_ratio >= self.settings.minute_batch_spot_min_coverage
            and all(
                int(index_counts.get(symbol, 0)) >= self.source_strategy.parameters.ma_days
                for symbol in WUFU_INDEX_SYMBOLS
            )
        )
        session.update(
            {
                "context_ready": ready,
                "daily_as_of": daily_as_of,
                "context_errors": errors,
                "daily_history_coverage": {
                    "ready_symbols": daily_ready_symbols,
                    "required_symbols": len(FILE_ETF_SYMBOLS),
                    "ratio": round(daily_ready_ratio, 4),
                },
                "context_updated_at": current_time.isoformat(timespec="seconds"),
            }
        )
        self.store.save_wufu_v7_session(compact_date, session)
        return {
            "status": "ready" if ready else "incomplete",
            "daily_as_of": daily_as_of,
            "errors": errors,
        }

    def _run_wufu_v7_static_once(self, current_time: datetime) -> dict[str, Any]:
        window = current_trading_window(current_time)
        if not window.is_open:
            return self._source_result("skipped_market_closed", {}, {}, [], window.reason, None, [])

        compact_date = current_time.strftime("%Y%m%d")
        trade_date = current_time.strftime("%Y-%m-%d")
        context = self.refresh_source_context(current_time)
        state = self.store.load_paper_account(
            ETF_PAPER_ACCOUNT_ID,
            self.settings.minute_initial_cash,
            trade_date,
        )
        account = state["account"]
        positions = state["positions"]
        held_symbol = next(iter(positions), None)
        if context.get("status") not in {"ready", "already_ready"}:
            plan = self._source_plan("", held_symbol, None, {}, "HOLD", "context", "日线数据尚未准备完成")
            return self._source_complete(
                "skipped_missing_daily_context", account, positions, [], "缺少文件策略所需的前收盘数据", plan, []
            )

        session = self.store.load_wufu_v7_session(compact_date)
        daily_as_of = str(session.get("daily_as_of") or "")
        if not daily_as_of:
            plan = self._source_plan("", held_symbol, None, {}, "HOLD", "context", "缺少前一交易日")
            return self._source_complete("skipped_missing_daily_context", account, positions, [], "缺少前一交易日", plan, [])

        warmup_start = (current_time.date() - timedelta(days=100)).strftime("%Y%m%d")
        daily = self.store.load_etf_daily(
            daily_as_of,
            start_date=warmup_start,
            symbols=FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL},
        )
        index_daily = self.store.load_index_daily(daily_as_of, start_date=warmup_start)
        daily_by_symbol = {
            symbol: frame.sort_values("trade_date").reset_index(drop=True)
            for symbol, frame in daily.groupby("symbol", sort=False)
        }
        names = self.source_strategy._names(daily_as_of)
        clock = current_time.strftime("%H:%M")

        if clock == "10:40":
            return self._source_morning_exit(
                current_time, account, positions, held_symbol, compact_date, daily_by_symbol, index_daily
            )
        if clock == "13:08":
            return self._source_capture_ranking(
                current_time, account, positions, held_symbol, compact_date, daily_by_symbol, index_daily, names
            )
        if clock == "13:09":
            return self._source_switch_sell(current_time, account, positions, held_symbol, compact_date, daily_as_of)
        if clock in {"13:10", "13:40", "14:10", "14:40", "14:55"}:
            return self._source_buy_stage(
                current_time, account, positions, held_symbol, compact_date, daily_as_of, force=clock == "14:55"
            )
        return self._source_result(
            "silent_source_schedule_window",
            account,
            positions,
            [],
            "文件策略在本分钟没有调仓事件",
            None,
            [],
        )

    def _source_morning_exit(
        self,
        now: datetime,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        held_symbol: str | None,
        compact_date: str,
        daily_by_symbol: dict[str, pd.DataFrame],
        index_daily: pd.DataFrame,
    ) -> dict[str, Any]:
        if not held_symbol:
            plan = self._source_plan(compact_date, None, None, {}, "HOLD", "10:40", "无持仓，无需早盘卖出判断")
            return self._source_complete("ok", account, positions, [], "", plan, [])
        bars, stale = self._source_minutes({held_symbol, *INTRADAY_PROXY_SYMBOLS}, now)
        action = self.source_strategy._morning_exit_action(
            compact_date,
            {"symbol": held_symbol, "entry_price": float(positions[held_symbol]["avg_cost"])},
            bars,
            index_daily,
            daily_by_symbol,
        )
        plan = self._source_plan(
            compact_date,
            held_symbol,
            None,
            {"morning_action": action},
            "SELL" if action["sell"] else "HOLD",
            "10:40",
            action["reason"],
        )
        orders: list[dict[str, Any]] = []
        if action["sell"]:
            price = _price_at(bars.get(held_symbol, pd.DataFrame()), "10:40:00")
            order = self._sell(
                account,
                positions,
                symbol=held_symbol,
                price=price,
                trade_date=now.strftime("%Y-%m-%d"),
                dedupe_key=f"{compact_date}:SOURCE_1040_SELL:{held_symbol}",
                reason=action["reason"],
                slippage_rate=self.source_strategy.parameters.slippage_rate,
                commission_rate=self.source_strategy.parameters.commission_rate,
                min_commission=self.source_strategy.parameters.min_commission,
            )
            if order:
                orders.append(order)
        if not orders:
            self.store.save_paper_account_state(account, positions)
        return self._source_complete("ok", account, positions, orders, "", plan, stale)

    def _source_capture_ranking(
        self,
        now: datetime,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        held_symbol: str | None,
        compact_date: str,
        daily_by_symbol: dict[str, pd.DataFrame],
        index_daily: pd.DataFrame,
        names: dict[str, str],
    ) -> dict[str, Any]:
        weak = self.source_strategy._is_weak_market(compact_date, index_daily)
        symbols = (GLOBAL_ETF_SYMBOLS if weak else FILE_ETF_SYMBOLS) | {DEFENSIVE_ETF_SYMBOL}
        bars, stale = self._source_minutes(symbols, now, prefer_batch_snapshot=True)
        target, ranking = self.source_strategy._select_target(
            compact_date, held_symbol, bars, daily_by_symbol, index_daily, names
        )
        selected = next((item for item in ranking.get("candidates", []) if item.get("symbol") == target), None)
        if target == DEFENSIVE_ETF_SYMBOL and selected is None:
            selected = {"symbol": target, "name": names.get(target, target), "price": _price_at(bars.get(target, pd.DataFrame()), "13:08:00")}
        session = self.store.load_wufu_v7_session(compact_date)
        session.update(
            {
                "snapshot_captured": True,
                "target_symbol": target,
                "target": selected or {},
                "ranking": ranking,
                "pending_symbol": None,
                "ranking_captured_at": now.isoformat(timespec="seconds"),
            }
        )
        self.store.save_wufu_v7_session(compact_date, session)
        plan = self._source_plan(
            compact_date, held_symbol, selected, ranking, "HOLD", "13:08", "按文件策略完成 13:08 分钟快照与候选排序"
        )
        return self._source_complete("ok", account, positions, [], "", plan, stale)

    def _source_switch_sell(
        self,
        now: datetime,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        held_symbol: str | None,
        compact_date: str,
        daily_as_of: str,
    ) -> dict[str, Any]:
        session = self.store.load_wufu_v7_session(compact_date)
        if not session.get("snapshot_captured"):
            plan = self._source_plan(daily_as_of, held_symbol, None, {}, "HOLD", "13:09", "缺少 13:08 原始快照，不补算")
            return self._source_complete("skipped_missing_source_snapshot", account, positions, [], "缺少13:08快照", plan, [])
        target_symbol = session.get("target_symbol")
        target = session.get("target") if isinstance(session.get("target"), dict) else {}
        plan = self._source_plan(
            daily_as_of,
            held_symbol,
            target,
            session.get("ranking") if isinstance(session.get("ranking"), dict) else {},
            "SELL" if held_symbol and held_symbol != target_symbol else "HOLD",
            "13:09",
            "按文件策略在 13:09 先卖出非目标持仓",
        )
        orders: list[dict[str, Any]] = []
        stale: list[str] = []
        if held_symbol and held_symbol != target_symbol:
            bars, stale = self._source_minutes({held_symbol}, now)
            price = _price_at(bars.get(held_symbol, pd.DataFrame()), "13:09:00")
            order = self._sell(
                account,
                positions,
                symbol=held_symbol,
                price=price,
                trade_date=now.strftime("%Y-%m-%d"),
                dedupe_key=f"{compact_date}:SOURCE_1309_SELL:{held_symbol}",
                reason="source_schedule_switch",
                slippage_rate=self.source_strategy.parameters.slippage_rate,
                commission_rate=self.source_strategy.parameters.commission_rate,
                min_commission=self.source_strategy.parameters.min_commission,
            )
            if order:
                orders.append(order)
        if not orders:
            self.store.save_paper_account_state(account, positions)
        return self._source_complete("ok", account, positions, orders, "", plan, stale)

    def _source_buy_stage(
        self,
        now: datetime,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        held_symbol: str | None,
        compact_date: str,
        daily_as_of: str,
        *,
        force: bool,
    ) -> dict[str, Any]:
        session = self.store.load_wufu_v7_session(compact_date)
        target_symbol = str(session.get("target_symbol") or "")
        target = session.get("target") if isinstance(session.get("target"), dict) else {}
        stage = now.strftime("%H:%M")
        if not session.get("snapshot_captured") or not target_symbol:
            plan = self._source_plan(daily_as_of, held_symbol, None, {}, "HOLD", stage, "无可执行的 13:08 文件快照目标")
            return self._source_complete("ok", account, positions, [], "", plan, [])
        if held_symbol or target_symbol in positions:
            plan = self._source_plan(daily_as_of, held_symbol, target, session.get("ranking", {}), "HOLD", stage, "已有持仓，不重复买入")
            return self._source_complete("ok", account, positions, [], "", plan, [])

        bars, stale = self._source_minutes({target_symbol}, now)
        approved = force or self.source_strategy._is_uptrend(bars.get(target_symbol, pd.DataFrame()), f"{stage}:00")
        if not approved:
            session["pending_symbol"] = target_symbol
            self.store.save_wufu_v7_session(compact_date, session)
            plan = self._source_plan(daily_as_of, None, target, session.get("ranking", {}), "HOLD", stage, "分钟趋势未转正，等待文件规定的下一次复核")
            return self._source_complete("ok", account, positions, [], "", plan, stale)

        price = _price_at(bars.get(target_symbol, pd.DataFrame()), f"{stage}:00")
        plan = self._source_plan(
            daily_as_of,
            None,
            target,
            session.get("ranking", {}),
            "BUY",
            stage,
            "source_schedule_forced_buy" if force else "source_schedule_trend_confirmed",
        )
        order = self._buy(
            account,
            positions,
            symbol=target_symbol,
            name=str(target.get("name") or target_symbol),
            price=price,
            target_weight=1.0,
            trade_date=now.strftime("%Y-%m-%d"),
            dedupe_key=f"{compact_date}:SOURCE_{stage.replace(':', '')}_BUY:{target_symbol}",
            reason="source_schedule_forced_buy" if force else "source_schedule_trend_confirmed",
            slippage_rate=self.source_strategy.parameters.slippage_rate,
            commission_rate=self.source_strategy.parameters.commission_rate,
            min_commission=self.source_strategy.parameters.min_commission,
            lot_size=100,
        )
        orders = [order] if order else []
        if order:
            session["pending_symbol"] = None
            self.store.save_wufu_v7_session(compact_date, session)
        else:
            self.store.save_paper_account_state(account, positions)
        return self._source_complete("ok", account, positions, orders, "", plan, stale)

    def _source_minutes(
        self,
        symbols: set[str] | frozenset[str],
        now: datetime,
        *,
        prefer_batch_snapshot: bool = False,
    ) -> tuple[dict[str, pd.DataFrame], list[str]]:
        """Load source-compatible minute bars without fanning out across the pool.

        At the 13:08 cross-sectional ranking point, a batch spot snapshot is the
        primary input. Only a handful of missing symbols may use a direct
        fallback. Later stages deal with a single target/position and retain the
        source strategy's regular minute-history request.
        """
        wanted = {str(symbol).upper() for symbol in symbols if symbol}
        bars_by_symbol: dict[str, pd.DataFrame] = {}
        missing = set(wanted)
        batch_attempted = False

        if prefer_batch_snapshot:
            fetch_batch = getattr(self.client, "fetch_realtime_spot_minutes", None)
            if callable(fetch_batch):
                batch_attempted = True
                try:
                    snapshots = fetch_batch(wanted, now)
                    for symbol, frame in snapshots.items():
                        normalized = _normalize_realtime_minutes(frame, now)
                        if normalized.empty:
                            continue
                        normalized_symbol = str(symbol).upper()
                        self.store.upsert_minute_bars(
                            normalized_symbol,
                            normalized,
                            source=str(normalized.attrs.get("source") or "akshare:fund_etf_spot_em"),
                        )
                        bars_by_symbol[normalized_symbol] = normalized
                    missing = wanted - set(bars_by_symbol)
                    coverage = len(bars_by_symbol) / max(len(wanted), 1)
                    if coverage < self.settings.minute_batch_spot_min_coverage:
                        logger.warning(
                            "source ETF batch spot coverage insufficient: %s/%s",
                            len(bars_by_symbol),
                            len(wanted),
                        )
                except Exception as exc:
                    logger.warning("source ETF batch spot unavailable: %s", exc)

        # A failed batch must not bring back the historical 100+ symbol timeout
        # storm. Direct fallbacks are safe for a target/position or the small
        # number of symbols absent from an otherwise healthy batch.
        batch_coverage = len(bars_by_symbol) / max(len(wanted), 1)
        allow_direct_fallback = not batch_attempted or len(missing) <= 6
        if batch_attempted and batch_coverage < self.settings.minute_batch_spot_min_coverage and len(wanted) > 6:
            allow_direct_fallback = False

        if missing and allow_direct_fallback:
            fast_fetch = getattr(self.client, "fetch_today_minutes_fast", self.client.fetch_today_minutes)

            def load(symbol: str) -> tuple[str, pd.DataFrame]:
                return symbol, _normalize_realtime_minutes(fast_fetch(symbol), now)

            worker_count = min(6, max(1, len(missing)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {executor.submit(load, symbol): symbol for symbol in sorted(missing)}
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        fetched_symbol, bars = future.result()
                    except Exception as exc:
                        logger.warning("source ETF minute data unavailable for %s: %s", symbol, exc)
                        continue
                    if bars.empty:
                        continue
                    self.store.upsert_minute_bars(
                        fetched_symbol,
                        bars,
                        source=str(bars.attrs.get("source") or "tushare:rt_etf_min_daily"),
                    )
                    bars_by_symbol[fetched_symbol] = _merge_minute_frames(bars_by_symbol.get(fetched_symbol), bars)

        start_time = f"{now.strftime('%Y-%m-%d')} 09:30:00"
        end_time = now.replace(second=0, microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        cached = self.store.load_minute_bars(start_time, end_time, wanted)
        if not cached.empty:
            for symbol, frame in cached.groupby("symbol", sort=False):
                normalized_symbol = str(symbol).upper()
                bars_by_symbol[normalized_symbol] = _merge_minute_frames(bars_by_symbol.get(normalized_symbol), frame)

        stale: list[str] = []
        current_naive = now.replace(second=0, microsecond=0, tzinfo=None)
        for symbol in sorted(wanted):
            bars = bars_by_symbol.get(symbol, pd.DataFrame())
            if bars.empty:
                stale.append(symbol)
                continue
            latest = pd.to_datetime(bars.iloc[-1].get("trade_time"), errors="coerce")
            if pd.isna(latest) or (current_naive - latest).total_seconds() > self.settings.minute_max_stale_seconds:
                stale.append(symbol)
        return bars_by_symbol, stale

    def _source_plan(
        self,
        as_of: str,
        held_symbol: str | None,
        target: dict[str, Any] | None,
        ranking: dict[str, Any],
        signal: str,
        stage: str,
        reasoning: str,
    ) -> ETFDecisionPlan:
        candidates: list[ETFCandidate] = []
        for item in (ranking.get("candidates") or [])[:10]:
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            candidates.append(
                ETFCandidate(
                    symbol=symbol,
                    name=str(item.get("name") or symbol),
                    bucket=bucket_for_symbol(symbol),
                    index_code="",
                    index_name="",
                    latest_price=round(float(item.get("price") or 0.0), 6),
                    momentum_score=round(float(item.get("score") or 0.0), 8),
                    annualized_trend=round(float(item.get("annualized_trend") or 0.0), 8),
                    r_squared=round(float(item.get("r_squared") or 0.0), 8),
                    ma10=0.0,
                    avg_turnover_yuan=0.0,
                    annualized_volatility=0.0,
                    three_day_min_return=0.0,
                    reasons=["五福文件 25 日加权动量与 R2 排名"],
                )
            )
        target_dict = dict(target) if target else None
        if target_dict and "latest_price" not in target_dict:
            target_dict["latest_price"] = float(target_dict.get("price") or 0.0)
        weak = bool(ranking.get("weak_market"))
        return ETFDecisionPlan(
            strategy_id=self.source_strategy.parameters.strategy_id,
            strategy_version=self.source_strategy.parameters.strategy_version,
            as_of=as_of,
            regime="weak" if weak else "normal",
            regime_detail={
                "source_file_strategy": True,
                "source_schedule_stage": stage,
                "weak_market": weak,
                "liquidity_threshold": ranking.get("liquidity_threshold"),
            },
            signal=signal,
            target_weight=1.0 if target_dict else 0.0,
            target=target_dict,
            current_symbol=held_symbol,
            trade_plan=[],
            candidates=candidates,
            reasoning=reasoning,
            intraday_confirmation={"source_file_schedule_stage": stage},
            nav_check={"status": "not_used_by_source_file_strategy"},
            ai_review={"status": "not_used_by_source_file_strategy"},
            execution_mode="source_file_v7_static_paper_only",
        )

    def _source_result(
        self,
        status: str,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        orders: list[dict[str, Any]],
        reason: str,
        plan: ETFDecisionPlan | None,
        stale_symbols: list[str],
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "mode": "etf_source_file_v7_static_paper_only",
            "no_real_orders": True,
            "orders": [order for order in orders if order.get("status") == "filled"],
            "account": {**account, "equity": round(_account_equity(account, positions), 4), "positions": positions},
            "plan": plan.to_dict() if plan else {},
            "stale_symbols": stale_symbols,
        }

    def _source_complete(
        self,
        status: str,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        orders: list[dict[str, Any]],
        reason: str,
        plan: ETFDecisionPlan,
        stale_symbols: list[str],
    ) -> dict[str, Any]:
        result = self._source_result(status, account, positions, orders, reason, plan, stale_symbols)
        payload = plan.to_dict()
        payload["minute_execution"] = {
            "status": status,
            "reason": reason,
            "stale_symbols": stale_symbols,
            "filled_order_count": len(result["orders"]),
        }
        result["decision_id"] = self.store.save_decision(payload)
        logger.info(
            "ETF source-file V7: stage=%s signal=%s target=%s status=%s filled=%s",
            plan.regime_detail.get("source_schedule_stage"),
            plan.signal,
            (plan.target or {}).get("symbol") or "NONE",
            status,
            len(result["orders"]),
        )
        return result

    def _buy(
        self,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        *,
        symbol: str,
        name: str,
        price: float,
        target_weight: float,
        trade_date: str,
        dedupe_key: str,
        reason: str,
        execution_context: dict[str, Any] | None = None,
        slippage_rate: float | None = None,
        commission_rate: float | None = None,
        min_commission: float | None = None,
        lot_size: int | None = None,
    ) -> dict[str, Any] | None:
        if self.store.has_paper_order(dedupe_key) or price <= 0:
            return None
        slippage = self.settings.minute_slippage_rate if slippage_rate is None else slippage_rate
        commission_rate = self.settings.minute_commission_rate if commission_rate is None else commission_rate
        min_commission = self.settings.minute_min_commission if min_commission is None else min_commission
        lot_size = self.settings.minute_lot_size if lot_size is None else lot_size
        execution_price = price * (1 + slippage)
        equity = _account_equity(account, positions)
        budget = min(equity * target_weight, float(account["cash"]))
        quantity = _affordable_quantity(
            float(account["cash"]),
            execution_price,
            lot_size,
            commission_rate,
            min_commission,
            budget,
        )
        if quantity <= 0:
            return None
        amount = quantity * execution_price
        commission = _commission(amount, commission_rate, min_commission)
        account["cash"] = round(float(account["cash"]) - amount - commission, 4)
        positions[symbol] = {
            "symbol": symbol,
            "name": name,
            "quantity": quantity,
            "available_quantity": 0,
            "avg_cost": round((amount + commission) / quantity, 6),
            "last_price": price,
            "last_trade_date": trade_date,
        }
        order = _order(
            dedupe_key,
            "BUY",
            symbol,
            name,
            quantity,
            execution_price,
            commission,
            reason,
            execution_context,
        )
        return order if self.store.save_paper_account_state(account, positions, order) else None

    def _sell(
        self,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        *,
        symbol: str,
        price: float,
        trade_date: str,
        dedupe_key: str,
        reason: str,
        execution_context: dict[str, Any] | None = None,
        slippage_rate: float | None = None,
        commission_rate: float | None = None,
        min_commission: float | None = None,
    ) -> dict[str, Any] | None:
        position = positions.get(symbol)
        if not position or self.store.has_paper_order(dedupe_key) or price <= 0:
            return None
        quantity = min(int(position["quantity"]), int(position["available_quantity"]))
        if quantity <= 0:
            return None
        slippage = self.settings.minute_slippage_rate if slippage_rate is None else slippage_rate
        commission_rate = self.settings.minute_commission_rate if commission_rate is None else commission_rate
        min_commission = self.settings.minute_min_commission if min_commission is None else min_commission
        execution_price = price * (1 - slippage)
        amount = quantity * execution_price
        commission = _commission(amount, commission_rate, min_commission)
        account["cash"] = round(float(account["cash"]) + amount - commission, 4)
        account["realized_pnl"] = round(
            float(account.get("realized_pnl") or 0.0) + amount - commission - quantity * float(position["avg_cost"]),
            4,
        )
        position["quantity"] = int(position["quantity"]) - quantity
        position["available_quantity"] = max(0, int(position["available_quantity"]) - quantity)
        position["last_price"] = price
        if position["quantity"] <= 0:
            positions.pop(symbol, None)
        order = _order(
            dedupe_key,
            "SELL",
            symbol,
            str(position["name"]),
            quantity,
            execution_price,
            commission,
            reason,
            execution_context,
        )
        return order if self.store.save_paper_account_state(account, positions, order) else None

    def _result(
        self,
        status: str,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        orders: list[dict[str, Any]],
        reason: str,
        plan: Any | None = None,
        stale_symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "mode": "etf_minute_paper_trading_only",
            "no_real_orders": True,
            "orders": [order for order in orders if order.get("status") == "filled"],
            "account": {**account, "equity": round(_account_equity(account, positions), 4), "positions": positions},
            "plan": plan.to_dict() if plan else {},
            "stale_symbols": stale_symbols or [],
        }

    def _complete(
        self,
        status: str,
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        orders: list[dict[str, Any]],
        reason: str,
        plan: Any,
        stale_symbols: list[str],
    ) -> dict[str, Any]:
        """Persist the final minute decision, including why it produced no order."""
        result = self._result(status, account, positions, orders, reason, plan, stale_symbols)
        payload = plan.to_dict()
        payload["minute_execution"] = {
            "status": status,
            "reason": reason,
            "stale_symbols": stale_symbols,
            "filled_order_count": len(result["orders"]),
        }
        result["decision_id"] = self.store.save_decision(payload)
        target = plan.target or {}
        logger.info(
            "ETF minute plan: signal=%s target=%s status=%s filled=%s reason=%s",
            plan.signal,
            target.get("symbol") or "NONE",
            status,
            len(result["orders"]),
            reason or "none",
        )
        return result


def _normalize_realtime_minutes(frame: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        raw_time = str(row.get("trade_time") or row.get("time") or "").strip()
        timestamp = pd.NaT
        if len(raw_time) > 8 and any(character in raw_time for character in ("-", "/")):
            timestamp = pd.to_datetime(raw_time, errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.to_datetime(f"{now.strftime('%Y-%m-%d')} {raw_time}", errors="coerce")
        price = _float(row.get("close"))
        if pd.isna(timestamp) or price <= 0:
            continue
        rows.append(
            {
                "trade_time": timestamp,
                "open": _float(row.get("open")) or price,
                "high": _float(row.get("high")) or price,
                "low": _float(row.get("low")) or price,
                "close": price,
                "vol": _float(row.get("vol", row.get("volume"))),
                "amount": _float(row.get("amount")),
            }
        )
    if not rows:
        return pd.DataFrame()
    normalized = pd.DataFrame(rows).sort_values("trade_time").drop_duplicates("trade_time", keep="last").reset_index(drop=True)
    normalized.attrs["source"] = frame.attrs.get("source") or "tushare:rt_etf_min_daily"
    return normalized


def _merge_minute_frames(left: pd.DataFrame | None, right: pd.DataFrame | None) -> pd.DataFrame:
    frames = [frame for frame in (left, right) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined["trade_time"] = pd.to_datetime(combined["trade_time"], errors="coerce")
    combined = combined.dropna(subset=["trade_time"]).sort_values("trade_time").drop_duplicates("trade_time", keep="last").reset_index(drop=True)
    combined.attrs["source"] = frames[-1].attrs.get("source") or frames[0].attrs.get("source") or "etf:merged_minutes"
    return combined


def _beijing_time(value: datetime | None) -> datetime:
    current = value or datetime.now(BEIJING_TZ)
    return current.replace(tzinfo=BEIJING_TZ) if current.tzinfo is None else current.astimezone(BEIJING_TZ)


def _account_equity(account: dict[str, Any], positions: dict[str, dict[str, Any]]) -> float:
    return float(account.get("cash") or 0.0) + sum(
        int(position.get("quantity") or 0) * float(position.get("last_price") or 0.0)
        for position in positions.values()
    )


def _mark_prices(positions: dict[str, dict[str, Any]], prices: dict[str, float]) -> None:
    for symbol, price in prices.items():
        if symbol in positions and price > 0:
            positions[symbol]["last_price"] = price


def _stop_triggered(position: dict[str, Any], price: float, stop_pct: float) -> bool:
    return price > 0 and float(position.get("avg_cost") or 0.0) > 0 and price <= float(position["avg_cost"]) * (1 - stop_pct)


def _affordable_quantity(cash: float, price: float, lot_size: int, commission_rate: float, min_commission: float, budget: float) -> int:
    max_value = min(cash, budget)
    quantity = int(floor(max_value / max(price, 1e-9) / lot_size) * lot_size)
    while quantity > 0:
        amount = quantity * price
        if amount + _commission(amount, commission_rate, min_commission) <= cash:
            return quantity
        quantity -= lot_size
    return 0


def _commission(amount: float, rate: float, minimum: float) -> float:
    return round(max(amount * rate, minimum), 4)


def _order(
    dedupe_key: str,
    side: str,
    symbol: str,
    name: str,
    quantity: int,
    price: float,
    commission: float,
    reason: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order = {
        "order_id": f"etf-order-{uuid4().hex}",
        "dedupe_key": dedupe_key,
        "side": side,
        "symbol": symbol,
        "name": name,
        "status": "filled",
        "quantity": int(quantity),
        "price": round(price, 6),
        "commission": round(commission, 4),
        "reason": reason,
        "created_at": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
    }
    if execution_context:
        order.update(execution_context)
    return order


def _float(value: Any) -> float:
    try:
        return float(value) if value not in {None, ""} else 0.0
    except (TypeError, ValueError):
        return 0.0
