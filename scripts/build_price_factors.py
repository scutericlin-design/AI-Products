#!/usr/bin/env python3
"""Build first-pass price and liquidity factors from local daily price CSVs."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_PRICE_DIR = PROJECT_ROOT / "data" / "raw" / "prices"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
FACTOR_PATH = PROCESSED_DIR / "factors_price_daily.csv"
LATEST_FACTOR_PATH = PROCESSED_DIR / "factors_price_latest.csv"


def pct_change(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods=periods)


def build_for_symbol(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"symbol": str})
    required = {"symbol", "name", "trade_date", "close", "amount", "turnover"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    df = df.sort_values("trade_date").copy()
    df["symbol"] = df["symbol"].str.zfill(6)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["return_1d"] = pct_change(df["close"], 1)
    df["momentum_5d"] = pct_change(df["close"], 5)
    df["momentum_10d"] = pct_change(df["close"], 10)
    df["momentum_20d"] = pct_change(df["close"], 20)
    df["ma_5"] = df["close"].rolling(5, min_periods=5).mean()
    df["ma_10"] = df["close"].rolling(10, min_periods=10).mean()
    df["ma_20"] = df["close"].rolling(20, min_periods=20).mean()
    df["close_vs_ma20"] = df["close"] / df["ma_20"] - 1
    df["amount_ma5"] = df["amount"].rolling(5, min_periods=5).mean()
    df["amount_ma20"] = df["amount"].rolling(20, min_periods=20).mean()
    df["amount_ratio_5_20"] = df["amount_ma5"] / df["amount_ma20"]
    df["turnover_5d"] = df["turnover"].rolling(5, min_periods=5).mean()
    df["volatility_20d"] = df["return_1d"].rolling(20, min_periods=10).std() * np.sqrt(252)
    df["high_20d"] = df["close"].rolling(20, min_periods=20).max()
    df["breakout_20d"] = (df["close"] >= df["high_20d"]).astype(int)

    columns = [
        "symbol",
        "name",
        "trade_date",
        "close",
        "return_1d",
        "momentum_5d",
        "momentum_10d",
        "momentum_20d",
        "close_vs_ma20",
        "amount_ratio_5_20",
        "turnover_5d",
        "volatility_20d",
        "breakout_20d",
    ]
    return df[columns]


def add_cross_section_scores(factors: pd.DataFrame) -> pd.DataFrame:
    scored = factors.copy()
    score_inputs = {
        "score_momentum": "momentum_20d",
        "score_amount": "amount_ratio_5_20",
        "score_trend": "close_vs_ma20",
        "score_turnover": "turnover_5d",
    }

    for score_column, source_column in score_inputs.items():
        scored[score_column] = scored.groupby("trade_date")[source_column].rank(pct=True)

    scored["risk_penalty_volatility"] = scored.groupby("trade_date")["volatility_20d"].rank(pct=True)
    scored["price_factor_score"] = (
        30 * scored["score_momentum"].fillna(0)
        + 25 * scored["score_amount"].fillna(0)
        + 25 * scored["score_trend"].fillna(0)
        + 10 * scored["score_turnover"].fillna(0)
        + 10 * scored["breakout_20d"].fillna(0)
        - 10 * scored["risk_penalty_volatility"].fillna(0)
    ).round(2)
    return scored.sort_values(["trade_date", "price_factor_score"], ascending=[True, False])


def latest_by_symbol(factors: pd.DataFrame) -> pd.DataFrame:
    return (
        factors.sort_values("trade_date")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values("price_factor_score", ascending=False)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local price factors from data/raw/prices/*.csv.")
    parser.add_argument("--price-dir", type=Path, default=RAW_PRICE_DIR)
    args = parser.parse_args()

    files = sorted(args.price_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"No price CSV files found in {args.price_dir}")

    frames = []
    for path in files:
        frame = build_for_symbol(path)
        frames.append(frame)
        print(f"loaded {path.relative_to(PROJECT_ROOT)} rows={len(frame)}")

    factors = add_cross_section_scores(pd.concat(frames, ignore_index=True))
    latest = latest_by_symbol(factors)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    factors.to_csv(FACTOR_PATH, index=False, encoding="utf-8")
    latest.to_csv(LATEST_FACTOR_PATH, index=False, encoding="utf-8")

    print(f"\nwrote {FACTOR_PATH.relative_to(PROJECT_ROOT)} rows={len(factors)}")
    print(f"wrote {LATEST_FACTOR_PATH.relative_to(PROJECT_ROOT)} rows={len(latest)}")
    print("\nlatest ranking")
    print(latest[["symbol", "name", "trade_date", "close", "price_factor_score"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
