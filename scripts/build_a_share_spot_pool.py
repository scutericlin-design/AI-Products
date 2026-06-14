#!/usr/bin/env python3
"""Build a full-A-share recommendation pool from AKShare Sina spot quotes.

This is the fast daily first pass. It scores the whole A-share spot universe
with current price action, liquidity, tradability, risk controls and pool-level
diversification, then writes the same recommended_pool.csv consumed by the UI.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
SCORED_UNIVERSE_PATH = PROJECT_ROOT / "data" / "processed" / "scored_universe_latest.csv"
MODEL_VERSION = "institutional_score_v3"


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


def classify_board(market: str, symbol: str) -> str:
    if market == "bj" or symbol.startswith(("4", "8", "9")):
        return "beijing"
    if symbol.startswith(("300", "301")):
        return "chinext"
    if symbol.startswith(("688", "689")):
        return "star"
    if symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return "main"
    return "other"


def percentile(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    ranks = series.rank(pct=True)
    return ranks if higher_is_better else 1 - ranks


def clipped_score(series: pd.Series, low: float, high: float, higher_is_better: bool = True) -> pd.Series:
    scaled = ((series - low) / (high - low)).clip(0, 1)
    return scaled if higher_is_better else 1 - scaled


def sweet_spot_score(series: pd.Series, low: float, peak: float, high: float) -> pd.Series:
    if high <= low + 0.2:
        return clipped_score(series, low, high)
    peak = min(max(peak, low + 0.1), high - 0.1)
    left = ((series - low) / (peak - low)).clip(0, 1)
    right = ((high - series) / (high - peak)).clip(0, 1)
    return pd.concat([left, right], axis=1).min(axis=1)


def min_max_score(series: pd.Series, low: float, high: float) -> pd.Series:
    return ((series - low) / (high - low)).clip(0, 1)


def cap_by_board(frame: pd.DataFrame, limit: int, board_cap: int) -> pd.DataFrame:
    selected: list[pd.Series] = []
    board_counts: dict[str, int] = {}
    for _, row in frame.iterrows():
        board = str(row["board"])
        if board_counts.get(board, 0) >= board_cap:
            continue
        selected.append(row)
        board_counts[board] = board_counts.get(board, 0) + 1
        if len(selected) >= limit:
            break
    return pd.DataFrame(selected)


def build_pool(
    spot: pd.DataFrame,
    limit: int,
    trade_date: str,
    min_amount: float,
    include_beijing: bool,
    markets: set[str],
    buy_score_threshold: float,
    min_pct_change: float,
    max_pct_change: float,
    min_close_position: float,
    max_amplitude: float,
    target_weight: float,
    return_all: bool = False,
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
    df["board"] = df.apply(lambda row: classify_board(row["market"], row["symbol"]), axis=1)

    numeric_columns = ["close", "pct_change", "prev_close", "open", "high", "low", "volume", "amount"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["close", "pct_change", "high", "low", "amount"])
    df = df[df["close"] > 2]
    df = df[df["amount"] >= min_amount]
    df = df[~df["name"].astype(str).str.contains("ST|退", regex=True)]
    if not include_beijing:
        df = df[df["market"].isin(["sh", "sz"])]
    df = df[df["board"].isin(markets)]
    output_columns = [
        "pool_rank",
        "trade_date",
        "symbol",
        "name",
        "close",
        "price_factor_score",
        "action",
        "target_weight",
        "recommendation_tier",
        "confidence",
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
        "alpha_score",
        "liquidity_capacity_score",
        "risk_control_score",
        "crowding_penalty",
        "momentum_score",
        "liquidity_score",
        "capacity_score",
        "close_position_score",
        "stability_score",
        "tradability_score",
        "reversal_risk_score",
        "board",
        "source_time",
    ]
    if df.empty:
        return pd.DataFrame(columns=output_columns)

    range_width = (df["high"] - df["low"]).replace(0, pd.NA)
    df["intraday_position"] = ((df["close"] - df["low"]) / range_width).fillna(0.5)
    df["gap_pct"] = df["open"] / df["prev_close"] - 1
    df["amplitude_pct"] = (df["high"] - df["low"]) / df["prev_close"]
    df["turnover_proxy"] = df["amount"] / df["close"].replace(0, pd.NA)

    # Professional-style scoring card: alpha and risk are separated first, then
    # recombined. This avoids a single hot variable, such as one-day return,
    # dominating the recommendation pool.
    df["momentum_score"] = sweet_spot_score(df["pct_change"], min_pct_change, 5.5, max_pct_change)
    df["liquidity_score"] = percentile(df["amount"])
    df["capacity_score"] = min_max_score(df["amount"] / 100000000, min_amount / 100000000, 35.0)
    df["close_position_score"] = df["intraday_position"].clip(0, 1)
    df["stability_score"] = clipped_score(df["amplitude_pct"], 0.025, max_amplitude, higher_is_better=False)
    df["gap_quality_score"] = (1 - (df["gap_pct"].abs() / 0.055)).clip(0, 1)
    df["reversal_risk_score"] = (
        1
        - (
            0.45 * clipped_score(df["amplitude_pct"], 0.06, max_amplitude)
            + 0.35 * clipped_score(df["pct_change"], 7.5, max_pct_change)
            + 0.20 * (1 - df["close_position_score"])
        )
    ).clip(0, 1)
    df["tradability_score"] = 1.0
    df.loc[df["pct_change"] >= 9.7, "tradability_score"] = 0.35
    df.loc[df["pct_change"] >= 19.0, "tradability_score"] = 0.20
    df.loc[df["intraday_position"] < 0.45, "tradability_score"] *= 0.65
    df.loc[df["amplitude_pct"] > max_amplitude, "tradability_score"] *= 0.60
    df.loc[df["amount"] < min_amount * 1.5, "tradability_score"] *= 0.80

    df["alpha_score"] = (
        45 * df["momentum_score"]
        + 35 * df["close_position_score"]
        + 20 * df["gap_quality_score"]
    ).round(2)
    df["liquidity_capacity_score"] = (
        65 * df["liquidity_score"]
        + 35 * df["capacity_score"]
    ).round(2)
    df["risk_control_score"] = (
        40 * df["stability_score"]
        + 35 * df["tradability_score"]
        + 25 * df["reversal_risk_score"]
    ).round(2)
    df["crowding_penalty"] = (
        14 * clipped_score(df["pct_change"], 8.2, max(max_pct_change, 8.3))
        + 8 * clipped_score(df["amplitude_pct"], 0.10, max(max_amplitude, 0.101))
        + 6 * (1 - df["gap_quality_score"])
    ).clip(0, 28)

    df["price_factor_score"] = (
        0.42 * df["alpha_score"]
        + 0.26 * df["liquidity_capacity_score"]
        + 0.32 * df["risk_control_score"]
        - df["crowding_penalty"]
    ).round(2)

    df["action"] = "watch"
    df.loc[
        (df["price_factor_score"] >= buy_score_threshold)
        & (df["alpha_score"] >= 68)
        & (df["liquidity_capacity_score"] >= 55)
        & (df["risk_control_score"] >= 58)
        & (df["pct_change"] > min_pct_change)
        & (df["pct_change"] < max_pct_change)
        & (df["intraday_position"] >= min_close_position)
        & (df["amplitude_pct"] <= max_amplitude),
        "action",
    ] = "buy"
    df["confidence"] = "low"
    df.loc[(df["price_factor_score"] >= 70) & (df["risk_control_score"] >= 55), "confidence"] = "medium"
    df.loc[
        (df["action"] == "buy")
        & (df["price_factor_score"] >= buy_score_threshold + 4)
        & (df["alpha_score"] >= 76)
        & (df["risk_control_score"] >= 68),
        "confidence",
    ] = "high"
    df["target_weight"] = 0.0
    df.loc[df["action"] == "buy", "target_weight"] = target_weight
    df.loc[(df["action"] == "buy") & (df["confidence"] == "medium"), "target_weight"] = target_weight * 0.70
    df.loc[(df["action"] == "buy") & (df["confidence"] == "low"), "target_weight"] = target_weight * 0.40
    df["target_weight"] = df["target_weight"].round(4)

    def flags(row: pd.Series) -> str:
        items: list[str] = []
        if row["pct_change"] < 0:
            items.append("negative_intraday_return")
        if row["pct_change"] >= 9.7:
            items.append("limit_up_or_hard_to_buy")
        if row["amount"] < min_amount * 2:
            items.append("liquidity_watch")
        if row["amplitude_pct"] > max_amplitude:
            items.append("high_intraday_amplitude")
        if abs(row["gap_pct"]) > 0.06:
            items.append("large_gap_open")
        if row["intraday_position"] < 0.45:
            items.append("weak_close_position")
        if row["risk_control_score"] < 58:
            items.append("risk_score_gate")
        if row["alpha_score"] < 68:
            items.append("alpha_score_gate")
        if row["crowding_penalty"] >= 12:
            items.append("crowding_or_chase_risk")
        if str(row["name"]).startswith(("N", "C")):
            items.append("new_stock_watch")
        return "|".join(items)

    df["risk_flags"] = df.apply(flags, axis=1)
    df.loc[df["risk_flags"].str.contains("new_stock_watch", na=False), "action"] = "watch"
    df["recommendation_tier"] = df["action"].map(
        {"buy": "core_candidate", "watch": "watch_candidate"}
    )
    df.loc[df["confidence"] == "high", "recommendation_tier"] = "high_conviction"
    df["model_version"] = MODEL_VERSION
    df["trade_date"] = trade_date
    df["review_required"] = True
    df["amount_yi"] = (df["amount"] / 100000000).round(2)
    df["intraday_position_pct"] = (df["intraday_position"] * 100).round(1)
    df["amplitude_pct_display"] = (df["amplitude_pct"] * 100).round(2)
    df["gap_pct_display"] = (df["gap_pct"] * 100).round(2)
    df["reason"] = df.apply(
        lambda row: (
            f"机构评分 {row['price_factor_score']:.1f}；"
            f"Alpha {row['alpha_score']:.1f} / 流动性 {row['liquidity_capacity_score']:.1f} / 风控 {row['risk_control_score']:.1f}；"
            f"涨跌幅 {row['pct_change']:.2f}%；"
            f"成交额 {row['amount_yi']:.2f}亿；"
            f"日内收盘位置 {row['intraday_position']:.0%}；"
            f"振幅 {row['amplitude_pct_display']:.2f}%；"
            f"拥挤扣分 {row['crowding_penalty']:.1f}；"
            f"{'核心候选' if row['action'] == 'buy' else '观察候选'}，入选原因是强势确认、资金容量、交易可行性和追高风险综合评估"
        ),
        axis=1,
    )

    if return_all:
        scored = df.sort_values("price_factor_score", ascending=False).copy()
        scored["pool_rank"] = range(1, len(scored) + 1)
        return scored[output_columns]

    ranked = df.sort_values(
        ["action", "confidence", "price_factor_score", "risk_control_score"],
        ascending=[True, True, False, False],
    )
    ranked["action_sort"] = ranked["action"].map({"buy": 0, "watch": 1}).fillna(2)
    ranked["confidence_sort"] = ranked["confidence"].map({"high": 0, "medium": 1, "low": 2}).fillna(3)
    ranked = ranked.sort_values(
        ["action_sort", "confidence_sort", "price_factor_score", "risk_control_score"],
        ascending=[True, True, False, False],
    )
    selected = cap_by_board(ranked, limit=limit, board_cap=max(6, round(limit * 0.45))).copy()
    if len(selected) < limit:
        selected_symbols = set(selected["symbol"])
        fill = ranked[~ranked["symbol"].isin(selected_symbols)].head(limit - len(selected))
        selected = pd.concat([selected, fill], ignore_index=True)
    selected["pool_rank"] = range(1, len(selected) + 1)
    return selected[output_columns]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a full-A-share spot recommendation pool.")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--min-amount", type=float, default=300_000_000)
    parser.add_argument("--include-beijing", action="store_true")
    parser.add_argument("--markets", default="main,chinext,star", help="Comma separated: main,chinext,star,beijing")
    parser.add_argument("--buy-score-threshold", type=float, default=78.0)
    parser.add_argument("--min-pct-change", type=float, default=1.0)
    parser.add_argument("--max-pct-change", type=float, default=9.7)
    parser.add_argument("--min-close-position-pct", type=float, default=55.0)
    parser.add_argument("--max-amplitude-pct", type=float, default=12.0)
    parser.add_argument("--target-weight", type=float, default=0.05)
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 30:
        raise SystemExit("--limit must be between 1 and 30")
    markets = {item.strip() for item in args.markets.split(",") if item.strip()}
    allowed_markets = {"main", "chinext", "star", "beijing"}
    unknown_markets = markets.difference(allowed_markets)
    if unknown_markets:
        raise SystemExit(f"--markets contains unsupported boards: {sorted(unknown_markets)}")
    if not markets:
        raise SystemExit("--markets must include at least one board")

    spot = ak.stock_zh_a_spot()
    pool = build_pool(
        spot=spot,
        limit=args.limit,
        trade_date=args.trade_date,
        min_amount=args.min_amount,
        include_beijing=args.include_beijing,
        markets=markets,
        buy_score_threshold=args.buy_score_threshold,
        min_pct_change=args.min_pct_change,
        max_pct_change=args.max_pct_change,
        min_close_position=args.min_close_position_pct / 100,
        max_amplitude=args.max_amplitude_pct / 100,
        target_weight=args.target_weight,
    )
    scored_universe = build_pool(
        spot=spot,
        limit=args.limit,
        trade_date=args.trade_date,
        min_amount=0,
        include_beijing=True,
        markets={"main", "chinext", "star", "beijing"},
        buy_score_threshold=args.buy_score_threshold,
        min_pct_change=args.min_pct_change,
        max_pct_change=args.max_pct_change,
        min_close_position=args.min_close_position_pct / 100,
        max_amplitude=args.max_amplitude_pct / 100,
        target_weight=args.target_weight,
        return_all=True,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pool.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    scored_universe.to_csv(SCORED_UNIVERSE_PATH, index=False, encoding="utf-8-sig")

    print(f"universe rows={len(spot)}")
    print(f"wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)} rows={len(pool)}")
    print(f"wrote {SCORED_UNIVERSE_PATH.relative_to(PROJECT_ROOT)} rows={len(scored_universe)}")
    print("\nrecommended pool")
    print(
        pool[
            ["pool_rank", "trade_date", "symbol", "name", "price_factor_score", "action", "pct_change", "amount_yi", "risk_flags"]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
