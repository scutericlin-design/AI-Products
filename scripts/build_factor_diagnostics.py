#!/usr/bin/env python3
"""Build factor diagnostics from daily price factor data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FACTORS_PATH = PROJECT_ROOT / "data" / "processed" / "factors_price_daily.csv"
INSTITUTIONAL_FACTORS_PATH = PROJECT_ROOT / "data" / "processed" / "institutional_factors_latest.csv"
OUTPUT_JSON = PROJECT_ROOT / "data" / "processed" / "factor_diagnostics_latest.json"


FACTOR_COLUMNS = [
    "price_factor_score",
    "momentum_20d",
    "close_vs_ma20",
    "amount_ratio_5_20",
    "turnover_5d",
    "volatility_20d",
]
V4_CROSS_SECTIONAL_FACTORS = [
    "price_factor_score",
    "raw_institutional_score",
    "alpha_score",
    "fundamental_quality_score",
    "valuation_sanity_score",
    "liquidity_capacity_score",
    "risk_control_score",
    "crowding_penalty",
    "gate_penalty_score",
    "data_completeness",
]


def safe_corr(left: pd.Series, right: pd.Series, method: str = "pearson") -> float | None:
    frame = pd.concat([left, right], axis=1).dropna()
    if len(frame) < 4:
        return None
    if method == "spearman":
        value = frame.iloc[:, 0].rank(method="average").corr(frame.iloc[:, 1].rank(method="average"))
    else:
        value = frame.iloc[:, 0].corr(frame.iloc[:, 1])
    if pd.isna(value):
        return None
    return float(value)


def build_diagnostics(data: pd.DataFrame, forward_days: int = 20, groups: int = 5) -> dict[str, object]:
    df = data.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    for column in FACTOR_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.sort_values(["symbol", "trade_date"])
    df["future_close"] = df.groupby("symbol")["close"].shift(-forward_days)
    df["forward_return"] = df["future_close"] / df["close"] - 1

    factor_summaries = []
    for column in FACTOR_COLUMNS:
        if column not in df.columns:
            continue
        daily = []
        for _, frame in df.groupby("trade_date"):
            ic = safe_corr(frame[column], frame["forward_return"])
            rank_ic = safe_corr(frame[column], frame["forward_return"], method="spearman")
            if ic is not None or rank_ic is not None:
                daily.append({"ic": ic, "rank_ic": rank_ic})
        daily_df = pd.DataFrame(daily)
        coverage = float(df[column].notna().mean())
        factor_summaries.append(
            {
                "factor": column,
                "coverage": coverage,
                "ic_mean": float(daily_df["ic"].dropna().mean()) if not daily_df.empty and daily_df["ic"].notna().any() else None,
                "rank_ic_mean": float(daily_df["rank_ic"].dropna().mean()) if not daily_df.empty and daily_df["rank_ic"].notna().any() else None,
                "ic_positive_rate": float((daily_df["ic"].dropna() > 0).mean()) if not daily_df.empty and daily_df["ic"].notna().any() else None,
            }
        )

    score_frame = df.dropna(subset=["price_factor_score", "forward_return"]).copy()
    group_rows = []
    for day, frame in score_frame.groupby("trade_date"):
        if len(frame) < groups:
            continue
        ranked = frame.copy()
        ranked["group"] = pd.qcut(ranked["price_factor_score"].rank(method="first"), groups, labels=False) + 1
        for group, group_frame in ranked.groupby("group"):
            group_rows.append({"trade_date": day, "group": int(group), "return": group_frame["forward_return"].mean()})
    group_df = pd.DataFrame(group_rows)
    group_returns = []
    if not group_df.empty:
        for group, frame in group_df.groupby("group"):
            group_returns.append({"group": int(group), "average_forward_return": float(frame["return"].mean())})

    correlation = df[[column for column in FACTOR_COLUMNS if column in df.columns]].corr().round(3).fillna(0)
    return {
        "forward_days": forward_days,
        "sample_rows": int(len(df)),
        "start_date": df["trade_date"].min().date().isoformat() if len(df) else None,
        "end_date": df["trade_date"].max().date().isoformat() if len(df) else None,
        "factor_summaries": factor_summaries,
        "score_group_returns": group_returns,
        "factor_correlation": correlation.to_dict(),
    }


def append_v4_cross_sectional_diagnostics(diagnostics: dict[str, object], path: Path) -> dict[str, object]:
    if not path.exists():
        return diagnostics
    latest = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig")
    if latest.empty:
        return diagnostics
    summaries = list(diagnostics.get("factor_summaries", []))
    for column in V4_CROSS_SECTIONAL_FACTORS:
        if column not in latest.columns:
            continue
        values = pd.to_numeric(latest[column], errors="coerce")
        summaries.append(
            {
                "factor": f"v4:{column}",
                "coverage": float(values.notna().mean()),
                "ic_mean": None,
                "rank_ic_mean": None,
                "ic_positive_rate": None,
                "mean": float(values.dropna().mean()) if values.notna().any() else None,
            }
        )
    diagnostics["factor_summaries"] = summaries
    diagnostics["cross_sectional_rows"] = int(len(latest))
    diagnostics["cross_sectional_trade_date"] = str(latest.get("trade_date", pd.Series([None])).iloc[0])
    diagnostics["cross_sectional_note"] = "v4 factors report latest cross-sectional coverage only; IC requires historical forward returns."
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Build factor diagnostics.")
    parser.add_argument("--factors", type=Path, default=FACTORS_PATH)
    parser.add_argument("--forward-days", type=int, default=20)
    args = parser.parse_args()

    data = pd.read_csv(args.factors, dtype={"symbol": str}, encoding="utf-8-sig")
    diagnostics = build_diagnostics(data, forward_days=args.forward_days)
    diagnostics = append_v4_cross_sectional_diagnostics(diagnostics, INSTITUTIONAL_FACTORS_PATH)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
