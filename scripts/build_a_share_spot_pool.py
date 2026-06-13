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

    df["score_change"] = percentile(df["pct_change"])
    df["score_amount"] = percentile(df["amount"])
    df["score_position"] = percentile(df["intraday_position"])
    df["score_amplitude"] = percentile(df["amplitude_pct"], higher_is_better=False)

    # Prefer liquid stocks with positive strength, but penalize very high daily swings.
    df["price_factor_score"] = (
        42 * df["score_change"]
        + 30 * df["score_amount"]
        + 18 * df["score_position"]
        + 10 * df["score_amplitude"]
    ).round(2)

    df["action"] = "watch"
    df.loc[(df["price_factor_score"] >= 82) & (df["pct_change"] > 0), "action"] = "buy"
    df["target_weight"] = df["action"].map({"buy": 0.05, "watch": 0.0})

    def flags(row: pd.Series) -> str:
        items: list[str] = []
        if row["pct_change"] < 0:
            items.append("negative_intraday_return")
        if row["amount"] < min_amount * 2:
            items.append("liquidity_watch")
        if row["amplitude_pct"] > 0.12:
            items.append("high_intraday_amplitude")
        if row["intraday_position"] < 0.45:
            items.append("weak_close_position")
        return "|".join(items)

    df["risk_flags"] = df.apply(flags, axis=1)
    df["recommendation_tier"] = df["action"].map(
        {"buy": "core_candidate", "watch": "watch_candidate"}
    )
    df["trade_date"] = trade_date
    df["review_required"] = True
    df["reason"] = df.apply(
        lambda row: (
            f"全A实时初筛评分 {row['price_factor_score']:.1f}；"
            f"涨跌幅 {row['pct_change']:.2f}%；"
            f"成交额 {row['amount'] / 100000000:.2f}亿；"
            f"日内收盘位置 {row['intraday_position']:.0%}；"
            f"{'核心候选' if row['action'] == 'buy' else '观察候选'}，需继续用20日趋势和公告风险复核"
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
        "risk_flags",
        "reason",
        "review_required",
        "amount",
        "pct_change",
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
            ["pool_rank", "trade_date", "symbol", "name", "price_factor_score", "action", "pct_change", "amount"]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
