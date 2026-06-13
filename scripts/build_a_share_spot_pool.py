#!/usr/bin/env python3
"""Build a full-A-share recommendation pool from AKShare Sina spot quotes.

This is the fast daily first pass. It scores the whole A-share spot universe
with current price action and liquidity, then writes the same
recommended_pool.csv consumed by the UI.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
MODEL_VERSION = "quality_first_v2"


def normalize_code(raw_code: str) -> tuple[str, str]:
    code = str(raw_code).strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code[:2], code[2:].zfill(6)
    if code.startswith("6"):
        return "sh", code.zfill(6)
    if code.startswith(("0", "3")):
        return "sz", code.zfill(6)
    if code.startswith(("8", "9")):
        return "bj", code.zfill(6)
    return "", code.zfill(6)


def percentile(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    ranks = series.rank(pct=True)
    return ranks if higher_is_better else 1 - ranks


def clipped_score(series: pd.Series, low: float, high: float, higher_is_better: bool = True) -> pd.Series:
    scaled = ((series - low) / (high - low)).clip(0, 1)
    return scaled if higher_is_better else 1 - scaled


def build_pool(
    spot: pd.DataFrame,
    limit: int,
    trade_date: str,
    min_amount: float,
    include_beijing: bool,
) -> pd.DataFrame:
    df = spot.rename(
        columns={
            "代码": "raw_symbol",
            "名称": "name",
            "最新价": "close",
            "涨跌幅": "pct_change",
            "昨收": "prev_close",
            "今开": "open",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "时间戳": "source_time",
        }
    ).copy()

    market_and_code = df["raw_symbol"].apply(normalize_code)
    df["market"] = market_and_code.apply(lambda item: item[0])
    df["symbol"] = market_and_code.apply(lambda item: item[1])

    numeric_columns = ["close", "pct_change", "prev_close", "open", "high", "low", "volume", "amount"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["close", "pct_change", "high", "low", "amount"])
    df = df[df["close"] > 2]
    df = df[df["amount"] >= min_amount]
    df = df[~df["name"].astype(str).str.contains("ST|退", regex=True)]
    if not include_beijing:
        df = df[df["market"].isin(["sh", "sz"])]

    range_width = (df["high"] - df["low"]).replace(0, pd.NA)
    df["intraday_position"] = ((df["close"] - df["low"]) / range_width).fillna(0.5)
    df["gap_pct"] = df["open"] / df["prev_close"] - 1
    df["amplitude_pct"] = (df["high"] - df["low"]) / df["prev_close"]

    df["strength_raw_score"] = clipped_score(df["pct_change"], 1.0, 8.0)
    df["overheat_penalty"] = clipped_score(df["pct_change"], 8.0, 12.0)
    df["strength_score"] = (df["strength_raw_score"] - 0.35 * df["overheat_penalty"]).clip(0, 1)
    df["liquidity_score"] = percentile(df["amount"])
    df["close_position_score"] = df["intraday_position"].clip(0, 1)
    df["stability_score"] = clipped_score(df["amplitude_pct"], 0.02, 0.14, higher_is_better=False)
    df["gap_quality_score"] = (1 - (df["gap_pct"].abs() / 0.06)).clip(0, 1)
    df["tradability_score"] = 1.0
    df.loc[df["pct_change"] >= 9.7, "tradability_score"] = 0.35
    df.loc[df["pct_change"] >= 19.0, "tradability_score"] = 0.20
    df.loc[df["intraday_position"] < 0.45, "tradability_score"] *= 0.65

    # Quality-first score: strength matters, but only if liquidity and tradability are acceptable.
    df["price_factor_score"] = (
        25 * df["strength_score"]
        + 25 * df["liquidity_score"]
        + 20 * df["close_position_score"]
        + 15 * df["tradability_score"]
        + 10 * df["stability_score"]
        + 5 * df["gap_quality_score"]
    ).round(2)

    df["action"] = "watch"
    df.loc[
        (df["price_factor_score"] >= 78)
        & (df["pct_change"] > 1.0)
        & (df["pct_change"] < 9.7)
        & (df["intraday_position"] >= 0.55)
        & (df["amplitude_pct"] <= 0.12),
        "action",
    ] = "buy"
    df["target_weight"] = df["action"].map({"buy": 0.05, "watch": 0.0})

    def flags(row: pd.Series) -> str:
        items: list[str] = []
        if row["pct_change"] < 0:
            items.append("negative_intraday_return")
        if row["pct_change"] >= 9.7:
            items.append("limit_up_or_hard_to_buy")
        if row["amount"] < min_amount * 2:
            items.append("liquidity_watch")
        if row["amplitude_pct"] > 0.12:
            items.append("high_intraday_amplitude")
        if abs(row["gap_pct"]) > 0.06:
            items.append("large_gap_open")
        if row["intraday_position"] < 0.45:
            items.append("weak_close_position")
        return "|".join(items)

    df["risk_flags"] = df.apply(flags, axis=1)
    df["recommendation_tier"] = df["action"].map(
        {"buy": "core_candidate", "watch": "watch_candidate"}
    )
    df["model_version"] = MODEL_VERSION
    df["trade_date"] = trade_date
    df["review_required"] = True
    df["amount_yi"] = (df["amount"] / 100000000).round(2)
    df["intraday_position_pct"] = (df["intraday_position"] * 100).round(1)
    df["amplitude_pct_display"] = (df["amplitude_pct"] * 100).round(2)
    df["gap_pct_display"] = (df["gap_pct"] * 100).round(2)
    df["reason"] = df.apply(
        lambda row: (
            f"全A实时初筛评分 {row['price_factor_score']:.1f}；"
            f"涨跌幅 {row['pct_change']:.2f}%；"
            f"成交额 {row['amount_yi']:.2f}亿；"
            f"日内收盘位置 {row['intraday_position']:.0%}；"
            f"振幅 {row['amplitude_pct_display']:.2f}%；"
            f"{'核心候选' if row['action'] == 'buy' else '观察候选'}，入选原因是强势、流动性、收盘位置和可交易性综合较优"
        ),
        axis=1,
    )

    selected = df.sort_values("price_factor_score", ascending=False).head(limit).copy()
    selected["pool_rank"] = range(1, len(selected) + 1)
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
        "model_version",
        "risk_flags",
        "reason",
        "review_required",
        "amount",
        "amount_yi",
        "pct_change",
        "intraday_position_pct",
        "amplitude_pct_display",
        "gap_pct_display",
        "strength_score",
        "liquidity_score",
        "close_position_score",
        "stability_score",
        "tradability_score",
        "source_time",
    ]
    return selected[columns]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a full-A-share spot recommendation pool.")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--min-amount", type=float, default=300_000_000)
    parser.add_argument("--include-beijing", action="store_true")
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 30:
        raise SystemExit("--limit must be between 1 and 30")

    spot = ak.stock_zh_a_spot()
    pool = build_pool(
        spot=spot,
        limit=args.limit,
        trade_date=args.trade_date,
        min_amount=args.min_amount,
        include_beijing=args.include_beijing,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pool.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print(f"universe rows={len(spot)}")
    print(f"wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)} rows={len(pool)}")
    print("\nrecommended pool")
    print(
        pool[
            ["pool_rank", "trade_date", "symbol", "name", "price_factor_score", "action", "pct_change", "amount_yi", "risk_flags"]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
