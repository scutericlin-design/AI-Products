from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import Settings
from .data import TushareDataClient
from .storage import ArchiveStore


@dataclass(frozen=True)
class RefreshResult:
    trade_date: str
    datasets: tuple[str, ...]
    failures: tuple[str, ...]


class DailyPipeline:
    """Archive-first daily public-data collector.

    A partial run is surfaced as a failure; strategy generation must check the
    result rather than silently use stale data.
    """

    def __init__(self, client: TushareDataClient):
        self.client = client

    def refresh(self, trade_date: str, earnings_report_date: str | None = None) -> RefreshResult:
        completed: list[str] = []
        failures: list[str] = []
        for name, loader in (
            ("daily", lambda: self.client.market_snapshot(trade_date)),
            ("daily_basic", lambda: self.client.daily_basic(trade_date)),
            ("stk_limit", lambda: self.client.price_limits(trade_date)),
            ("suspend_d", lambda: self.client.suspensions(trade_date)),
            ("stock_basic", lambda: self.client.stock_basic(trade_date)),
            ("limit_up_pool", lambda: self.client.limit_up_pool(trade_date)),
            ("broken_board_pool", lambda: self.client.broken_board_pool(trade_date)),
            ("limit_down_pool", lambda: self.client.limit_down_pool(trade_date)),
            ("limit_cpt_list", lambda: self.client.strongest_themes(trade_date)),
            ("moneyflow", lambda: self.client.moneyflow(trade_date)),
        ):
            try:
                loader(); completed.append(name)
            except Exception as exc:  # provider failure is audit-relevant
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        if earnings_report_date:
            try:
                self.client.earnings_forecast(earnings_report_date); completed.append("forecast")
            except Exception as exc:
                failures.append(f"earnings_forecast: {type(exc).__name__}: {exc}")
        return RefreshResult(trade_date, tuple(completed), tuple(failures))


def build_daily_pipeline(settings: Settings | None = None) -> DailyPipeline:
    """Build the canonical collector with the configured, audited TuShare source."""
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    return DailyPipeline(TushareDataClient.from_settings(ArchiveStore(settings.data_dir), settings))
