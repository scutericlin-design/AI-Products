#!/usr/bin/env python3
"""Build a system recommendation pool capped at 30 stocks."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"


def build_recommended_pool(signals: pd.DataFrame, limit: int) -> pd.DataFrame:
    signals = signals.copy()
    signals["symbol"] = signals["symbol"].astype(str).str.zfill(6)
    signals["trade_date"] = pd.to_datetime(signals["trade_date"]).dt.strftime("%Y-%m-%d")

    latest_trade_date = signals["trade_date"].max()
    current = signals[signals["trade_date"] == latest_trade_date].copy()
    current = current[current["action"].isin(["buy", "watch"])].copy()
    current = current.sort_values("price_factor_score", ascending=False).head(limit)

    current["pool_rank"] = range(1, len(current) + 1)
    current["recommendation_tier"] = current["action"].map(
        {
            "buy": "core_candidate",
            "watch": "watch_candidate",
        }
    )
    current["review_required"] = True

    columns = [
        "pool_rank",
        "trade_date",
        "symbol",
        "name",
        "close",
        "price_factor_score",
        "action",
        "target_weight",
        "recommendation_tier",
        "risk_flags",
        "reason",
        "review_required",
    ]
    return current[columns]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a latest-date recommendation pool from signals.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_LATEST_PATH)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 30:
        raise SystemExit("--limit must be between 1 and 30")

    if not args.signals.exists():
        raise SystemExit(f"Signal file not found: {args.signals}")

    signals = pd.read_csv(args.signals, dtype={"symbol": str}, encoding="utf-8-sig")
    pool = build_recommended_pool(signals, args.limit)
    RECOMMENDED_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    pool.to_csv(RECOMMENDED_POOL_PATH, index=False, encoding="utf-8-sig")

    print(f"wrote {RECOMMENDED_POOL_PATH.relative_to(PROJECT_ROOT)} rows={len(pool)}")
    print("\nrecommended pool")
    if pool.empty:
        print("No buy/watch candidates on the latest trade date.")
    else:
        print(
            pool[
                [
                    "pool_rank",
                    "trade_date",
                    "symbol",
                    "name",
                    "price_factor_score",
                    "action",
                    "recommendation_tier",
                    "risk_flags",
                ]
            ].to_string(index=False)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
