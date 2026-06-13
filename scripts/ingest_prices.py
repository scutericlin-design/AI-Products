#!/usr/bin/env python3
"""Ingest A-share daily prices from AKShare into local CSV files.

The first production MVP uses ak.stock_zh_a_daily because it works in this
environment. stock_zh_a_hist is intentionally not the primary source because
the Eastmoney endpoint can close connections in some networks.
"""

from __future__ import annotations

import argparse
import csv
import time
from dataclasses import dataclass
from pathlib import Path

import akshare as ak
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_PRICE_DIR = PROJECT_ROOT / "data" / "raw" / "prices"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "price_ingest_manifest.csv"


@dataclass(frozen=True)
class Stock:
    code: str
    name: str

    @property
    def ak_daily_symbol(self) -> str:
        if self.code.startswith("6"):
            return f"sh{self.code}"
        return f"sz{self.code}"


DEFAULT_STOCKS = [
    Stock("000001", "平安银行"),
    Stock("300750", "宁德时代"),
    Stock("688012", "中微公司"),
    Stock("603986", "兆易创新"),
]


def normalize_daily_price(df: pd.DataFrame, stock: Stock) -> pd.DataFrame:
    if df.empty:
        return df

    renamed = df.rename(
        columns={
            "date": "trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "amount",
            "outstanding_share": "outstanding_share",
            "turnover": "turnover",
        }
    ).copy()

    renamed.insert(0, "symbol", stock.code)
    renamed.insert(1, "name", stock.name)
    renamed["trade_date"] = pd.to_datetime(renamed["trade_date"]).dt.strftime("%Y-%m-%d")

    preferred = [
        "symbol",
        "name",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "outstanding_share",
        "turnover",
    ]
    existing = [column for column in preferred if column in renamed.columns]
    return renamed[existing].sort_values("trade_date")


def load_stock_list(path: Path | None) -> list[Stock]:
    if path is None:
        return DEFAULT_STOCKS

    stocks: list[Stock] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            code = str(row.get("code") or row.get("symbol") or "").strip()
            name = str(row.get("name") or "").strip()
            if not code:
                continue
            stocks.append(Stock(code=code.zfill(6), name=name or code.zfill(6)))
    return stocks


def ingest_one(stock: Stock, start: str, end: str, adjust: str) -> tuple[bool, str]:
    try:
        df = ak.stock_zh_a_daily(
            symbol=stock.ak_daily_symbol,
            start_date=start,
            end_date=end,
            adjust=adjust,
        )
        normalized = normalize_daily_price(df, stock)
        if normalized.empty:
            return False, "empty"

        RAW_PRICE_DIR.mkdir(parents=True, exist_ok=True)
        output_path = RAW_PRICE_DIR / f"{stock.code}.csv"
        normalized.to_csv(output_path, index=False, encoding="utf-8")
        return True, str(output_path.relative_to(PROJECT_ROOT))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def write_manifest(rows: list[dict[str, str]]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(MANIFEST_PATH, index=False, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest A-share daily prices from AKShare.")
    parser.add_argument("--start", default="20240101", help="Start date in YYYYMMDD format.")
    parser.add_argument("--end", default="20240131", help="End date in YYYYMMDD format.")
    parser.add_argument("--adjust", default="", choices=["", "qfq", "hfq"], help="AKShare adjustment mode.")
    parser.add_argument("--stock-list", type=Path, help="Optional CSV with code,name columns.")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds to sleep between symbols.")
    args = parser.parse_args()

    stocks = load_stock_list(args.stock_list)
    rows: list[dict[str, str]] = []
    ok_count = 0

    for stock in stocks:
        ok, message = ingest_one(stock, args.start, args.end, args.adjust)
        ok_count += int(ok)
        rows.append(
            {
                "symbol": stock.code,
                "name": stock.name,
                "start": args.start,
                "end": args.end,
                "adjust": args.adjust or "none",
                "ok": str(ok),
                "message": message,
            }
        )
        print(f"{'ok' if ok else 'fail'} {stock.code} {stock.name}: {message}")
        time.sleep(args.sleep)

    write_manifest(rows)
    print(f"\nmanifest: {MANIFEST_PATH.relative_to(PROJECT_ROOT)}")
    print(f"success: {ok_count}/{len(stocks)}")
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
