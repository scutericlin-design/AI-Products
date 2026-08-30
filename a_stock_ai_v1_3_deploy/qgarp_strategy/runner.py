from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from qgarp_strategy.config import QGARPSettings, load_settings
from qgarp_strategy.data_client import QGARPDataClient
from qgarp_strategy.models import Instrument
from qgarp_strategy.notify import send_qgarp_execution_report
from qgarp_strategy.paper import QGARPPaperRunner
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.strategy import QGARPStrategy


logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class QGARPStrategyRunner:
    def __init__(self, settings: QGARPSettings | None = None, store: QGARPStore | None = None, client: QGARPDataClient | None = None):
        self.settings = settings or load_settings()
        self.store = store or QGARPStore(self.settings.db_path)
        self.client = client or QGARPDataClient(self.settings)
        self.strategy = QGARPStrategy(self.settings)

    def refresh(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        memberships = []
        if start_date:
            refresh_end = end_date or datetime.now(BEIJING_TZ).strftime("%Y%m%d")
            if self.settings.backtest_universe_source == "daily_basic_pit":
                memberships = self.client.fetch_daily_basic_memberships(start_date, refresh_end, self.settings.backtest_universe_limit)
            elif self.settings.backtest_universe_source == "index_weight":
                memberships = self.client.fetch_index_memberships(self.settings.backtest_index_code, start_date, refresh_end)
            else:
                raise ValueError(f"不支持的 Q-GARP 回测股票池来源：{self.settings.backtest_universe_source}")
            self.store.upsert_memberships(self.settings.backtest_universe_id, memberships)
            symbols = {item[1] for item in memberships}
            instruments = [item for item in self.client.fetch_universe(include_inactive=True, limit=None) if item.symbol in symbols]
            known = {item.symbol for item in instruments}
            instruments.extend(Instrument(symbol=symbol, name=symbol) for symbol in sorted(symbols - known))
        else:
            instruments = self.client.fetch_universe()
        self.store.upsert_instruments(instruments)
        bar_count = financial_count = failures = 0
        for instrument in instruments:
            try:
                bars = self.client.fetch_daily_history(instrument.symbol, end_date=end_date, days=2500 if start_date else 150, start_date=start_date)
                bar_count += self.store.upsert_daily_bars(bars)
            except Exception as exc:
                failures += 1
                self.store.log_quality_check("daily_data", "failed", f"{instrument.symbol}: {exc}")
            try:
                financials = self.client.fetch_financial_history(instrument.symbol, start_date=start_date, end_date=end_date)
                financial_count += self.store.upsert_financials(financials)
            except Exception as exc:
                failures += 1
                self.store.log_quality_check("financial_data", "failed", f"{instrument.symbol}: {exc}")
        try:
            benchmark = self.client.fetch_index_history(self.settings.benchmark_symbol, end_date=end_date, days=2500 if start_date else 150, start_date=start_date)
            self.store.upsert_daily_bars(benchmark)
        except Exception as exc:
            failures += 1
            self.store.log_quality_check("benchmark", "failed", str(exc))
        status = "ok" if failures == 0 else "partial"
        self.store.log_quality_check("refresh", status, f"universe={len(instruments)} bars={bar_count} financials={financial_count} failures={failures}")
        return {"status": status, "universe_count": len(instruments), "membership_count": len(memberships), "bar_count": bar_count, "financial_count": financial_count, "failures": failures}

    def signal_once(self, refresh_if_empty: bool = True) -> dict[str, Any]:
        if refresh_if_empty and not self.store.latest_trade_date():
            self.refresh()
        as_of = self.store.latest_trade_date()
        if not as_of:
            return {"status": "no_data", "signal": "HOLD", "recommendations": [], "reason": "Q-GARP本地数据为空"}
        instruments = self.store.load_instruments(as_of, self.settings.universe_limit)
        histories = {item.symbol: self.store.bars(item.symbol, as_of) for item in instruments}
        fundamentals = {item.symbol: value for item in instruments if (value := self.store.fundamental_as_of(item.symbol, as_of)) is not None}
        plan = self.strategy.decide(as_of=as_of, instruments=instruments, histories=histories, fundamentals=fundamentals, benchmark=self.store.bars(self.settings.benchmark_symbol, as_of))
        plan["data_sources"] = {"prices": "tushare_primary_akshare_fallback", "fundamentals": "tushare_only_point_in_time"}
        plan["execution_mode"] = "qgarp_local_paper_only"
        signal_id = self.store.save_signal(plan)
        self.store.save_factor_run(as_of, {"signal_id": signal_id, "recommendations": plan.get("recommendations"), "candidate_count": plan.get("candidate_count")})
        return {"status": "ok", "signal_id": signal_id, "plan": plan}

    def hydrate_adjustment_factors(self, start_date: str, end_date: str, limit: int | None = None) -> dict[str, Any]:
        """Enrich an existing isolated cache without re-fetching prices or financials.

        This is intentionally a separate, resumable action because historical
        adjustment data is required before an adjusted-price research result can
        be called decision-grade.
        """
        instruments = self.store.load_instruments(limit=limit)
        updated = failures = 0
        for instrument in instruments:
            try:
                updated += self.store.upsert_adjustment_factors(
                    instrument.symbol,
                    self.client.fetch_adjustment_factors(instrument.symbol, start_date, end_date),
                )
            except Exception as exc:
                failures += 1
                self.store.log_quality_check("adj_factor", "failed", f"{instrument.symbol}: {exc}")
        coverage = self.store.adjustment_factor_coverage(start_date, end_date)
        status = "ok" if failures == 0 else "partial"
        self.store.log_quality_check("adj_factor", status, f"instruments={len(instruments)} updated={updated} failures={failures} coverage={coverage}")
        return {"status": status, "instrument_count": len(instruments), "updated": updated, "failures": failures, "coverage": coverage}

    def hydrate_adjustment_factors_by_date(self, start_date: str, end_date: str, limit: int = 20) -> dict[str, Any]:
        """Resumable, cross-sectional adjustment-factor enrichment.

        TuShare supports a one-day full-market endpoint (about 5,500 rows). It
        is substantially safer for the proxy than issuing thousands of
        stock-by-stock requests.  The next invocation automatically resumes
        from the earliest date below 98% cached coverage.
        """
        dates = self.store.adjustment_dates_needing_enrichment(start_date, end_date, max(1, limit))
        updated = failures = 0
        for trade_date in dates:
            try:
                updated += self.store.upsert_adjustment_factors_for_date(
                    trade_date,
                    self.client.fetch_adjustment_factors_for_date(trade_date),
                )
            except Exception as exc:
                failures += 1
                self.store.log_quality_check("adj_factor_by_date", "failed", f"{trade_date}: {exc}", trade_date)
        coverage = self.store.adjustment_factor_coverage(start_date, end_date)
        status = "ok" if failures == 0 else "partial"
        self.store.log_quality_check("adj_factor_by_date", status, f"dates={len(dates)} updated={updated} failures={failures} coverage={coverage}")
        return {"status": status, "date_count": len(dates), "updated": updated, "failures": failures, "coverage": coverage, "next_dates": self.store.adjustment_dates_needing_enrichment(start_date, end_date, 3)}

    def paper_once(self) -> dict[str, Any]:
        # The independent Q-GARP research track is execution-locked.  It does
        # not even generate a paper order, and its push path remains disabled.
        return {
            "status": "disabled_research_only",
            "mode": "qgarp_research_only_no_paper_no_push",
            "orders": [],
            "no_real_orders": True,
        }

    def account(self) -> dict[str, Any]:
        return self.store.paper_snapshot("qgarp_alpha_paper", self.settings.paper_initial_cash)
