from __future__ import annotations

from data.tushare_client import Quote, TushareClient


class MarketStream:
    def __init__(self, client: TushareClient | None = None) -> None:
        self.client = client or TushareClient()

    def latest_quotes(self) -> list[Quote]:
        return self.client.fetch_realtime_quotes()
