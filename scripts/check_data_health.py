#!/usr/bin/env python3
"""Check local data health for the investment system."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed"
RAW_PRICES = PROJECT_ROOT / "data" / "raw" / "prices"
RAW_TUSHARE = PROJECT_ROOT / "data" / "tushare"
OUTPUT_JSON = PROCESSED / "data_health_latest.json"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


DATASETS = {
    "signal_daily": PROCESSED / "signal_daily.csv",
    "signal_latest": PROCESSED / "signal_latest.csv",
    "factors_price_daily": PROCESSED / "factors_price_daily.csv",
    "recommended_pool": PROCESSED / "recommended_pool.csv",
    "scored_universe_latest": PROCESSED / "scored_universe_latest.csv",
    "institutional_factors_latest": PROCESSED / "institutional_factors_latest.csv",
    "tushare_stock_basic": RAW_TUSHARE / "stock_basic.csv",
    "tushare_trade_cal": RAW_TUSHARE / "trade_cal.csv",
}


def inspect_csv(name: str, path: Path) -> dict[str, object]:
    if not path.exists():
        return {"name": name, "exists": False, "status": "missing"}
    df = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig")
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=BEIJING_TZ).isoformat(timespec="seconds")
    null_cells = int(df.isna().sum().sum())
    row_count = int(len(df))
    latest_trade_date = None
    if "trade_date" in df.columns and row_count:
        latest_trade_date = str(pd.to_datetime(df["trade_date"], errors="coerce").max().date())
    duplicate_rows = int(df.duplicated().sum())
    status = "ok"
    issues: list[str] = []
    if row_count == 0:
        status = "bad"
        issues.append("empty_dataset")
    if null_cells > row_count * max(len(df.columns), 1) * 0.35:
        status = "warn"
        issues.append("high_null_ratio")
    if duplicate_rows:
        status = "warn"
        issues.append("duplicate_rows")
    return {
        "name": name,
        "exists": True,
        "status": status,
        "rows": row_count,
        "columns": int(len(df.columns)),
        "null_cells": null_cells,
        "duplicate_rows": duplicate_rows,
        "latest_trade_date": latest_trade_date,
        "updated_at": updated_at,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local data health.")
    parser.parse_args()
    datasets = [inspect_csv(name, path) for name, path in DATASETS.items()]
    raw_price_files = sorted(RAW_PRICES.glob("*.csv"))
    tushare_daily_files = sorted((RAW_TUSHARE / "daily").glob("*.csv"))
    tushare_daily_basic_files = sorted((RAW_TUSHARE / "daily_basic").glob("*.csv"))
    tushare_fundamental_files = sorted((RAW_TUSHARE / "fina_indicator").glob("*.csv"))
    bad = sum(1 for item in datasets if item["status"] == "bad")
    warn = sum(1 for item in datasets if item["status"] == "warn")
    status = "bad" if bad else "warn" if warn else "ok"
    result = {
        "status": status,
        "checked_at": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
        "dataset_count": len(datasets),
        "raw_price_file_count": len(raw_price_files),
        "tushare_daily_file_count": len(tushare_daily_files),
        "tushare_daily_basic_file_count": len(tushare_daily_basic_files),
        "tushare_fundamental_file_count": len(tushare_fundamental_files),
        "datasets": datasets,
        "summary": {
            "ok": sum(1 for item in datasets if item["status"] == "ok"),
            "warn": warn,
            "bad": bad,
        },
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
