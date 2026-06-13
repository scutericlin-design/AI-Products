#!/usr/bin/env python3
"""Check which AKShare A-share quote endpoints work in this environment."""

from __future__ import annotations

import traceback

import akshare as ak


def check_stock_zh_a_daily() -> bool:
    print("\n[check] stock_zh_a_daily / sz000001")
    try:
        df = ak.stock_zh_a_daily(
            symbol="sz000001",
            start_date="20240101",
            end_date="20240131",
            adjust="",
        )
        print(f"ok rows={len(df)} columns={list(df.columns)}")
        print(df.head(3).to_string(index=False))
        return True
    except Exception:
        print("failed")
        traceback.print_exc(limit=3)
        return False


def check_stock_zh_a_hist() -> bool:
    print("\n[check] stock_zh_a_hist / 000001")
    try:
        df = ak.stock_zh_a_hist(
            symbol="000001",
            period="daily",
            start_date="20240101",
            end_date="20240131",
            adjust="",
        )
        print(f"ok rows={len(df)} columns={list(df.columns)}")
        print(df.head(3).to_string(index=False))
        return True
    except Exception:
        print("failed")
        traceback.print_exc(limit=3)
        return False


def main() -> int:
    print(f"akshare version: {ak.__version__}")
    daily_ok = check_stock_zh_a_daily()
    hist_ok = check_stock_zh_a_hist()
    print("\nsummary")
    print(f"stock_zh_a_daily: {'ok' if daily_ok else 'failed'}")
    print(f"stock_zh_a_hist: {'ok' if hist_ok else 'failed'}")
    return 0 if daily_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
