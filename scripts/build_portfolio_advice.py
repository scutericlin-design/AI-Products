#!/usr/bin/env python3
"""Build personalized portfolio advice from current holdings and latest signals."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = PROJECT_ROOT / "data" / "portfolio.csv"
SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
ADVICE_PATH = PROJECT_ROOT / "data" / "processed" / "portfolio_advice.csv"


def load_portfolio(path: Path) -> pd.DataFrame:
    portfolio = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig")
    required = {"symbol", "name", "weight", "cost_price"}
    missing = required.difference(portfolio.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    portfolio = portfolio.copy()
    portfolio["symbol"] = portfolio["symbol"].astype(str).str.zfill(6)
    portfolio["weight"] = pd.to_numeric(portfolio["weight"], errors="coerce")
    portfolio["cost_price"] = pd.to_numeric(portfolio["cost_price"], errors="coerce")
    if "shares" in portfolio.columns:
        portfolio["shares"] = pd.to_numeric(portfolio["shares"], errors="coerce")
    else:
        portfolio["shares"] = pd.NA
    return portfolio


def load_signals(path: Path) -> pd.DataFrame:
    signals = pd.read_csv(path, dtype={"symbol": str})
    signals = signals.copy()
    signals["symbol"] = signals["symbol"].astype(str).str.zfill(6)
    return signals


def target_for_signal(signal_action: str, current_weight: float, max_single: float, watch_cap: float) -> float:
    if signal_action == "buy":
        return min(max_single, max(0.05, min(0.08, max_single)))
    if signal_action == "watch":
        return min(current_weight, watch_cap)
    if signal_action == "hold_or_reduce":
        return min(current_weight, watch_cap)
    if signal_action == "avoid":
        return 0.0
    return min(current_weight, watch_cap)


def advice_action(signal_action: str, current_weight: float, target_weight: float) -> str:
    if signal_action == "avoid":
        return "exit_or_strong_reduce"
    if current_weight > target_weight + 0.02:
        return "reduce"
    if signal_action == "buy" and current_weight < target_weight - 0.01:
        return "add"
    if signal_action == "watch":
        return "hold_watch_no_new"
    return "hold"


def build_reason(row: pd.Series, max_single: float, watch_cap: float) -> str:
    parts: list[str] = []
    parts.append(f"当前仓位 {row['weight']:.2%}")

    if pd.notna(row.get("pnl_pct")):
        parts.append(f"浮盈亏 {row['pnl_pct']:.2%}")

    if pd.notna(row.get("price_factor_score")):
        parts.append(f"策略评分 {row['price_factor_score']:.1f}")

    signal_action = row.get("signal_action", "no_signal")
    parts.append(f"模型信号 {signal_action}")

    if row["weight"] > max_single:
        parts.append(f"超过单票上限 {max_single:.0%}")

    if signal_action in {"watch", "hold_or_reduce"} and row["weight"] > watch_cap:
        parts.append(f"观察/降级票建议压到 {watch_cap:.0%} 以内")

    if signal_action == "avoid":
        parts.append("策略不支持继续持有，优先制定退出计划")

    risk_flags = row.get("risk_flags")
    if isinstance(risk_flags, str) and risk_flags:
        parts.append(f"风险标记: {risk_flags}")

    return "；".join(parts)


def build_advice(portfolio: pd.DataFrame, signals: pd.DataFrame, max_single: float, watch_cap: float) -> pd.DataFrame:
    merged = portfolio.merge(
        signals,
        how="left",
        on="symbol",
        suffixes=("_portfolio", "_signal"),
    )

    merged["name"] = merged["name_portfolio"].fillna(merged.get("name_signal"))
    merged["signal_action"] = merged["action"].fillna("no_signal")
    merged["latest_close"] = merged["close"]
    merged["pnl_pct"] = merged["latest_close"] / merged["cost_price"] - 1
    merged.loc[merged["latest_close"].isna() | merged["cost_price"].isna(), "pnl_pct"] = pd.NA

    merged["suggested_target_weight"] = merged.apply(
        lambda row: target_for_signal(row["signal_action"], row["weight"], max_single, watch_cap),
        axis=1,
    )
    merged["portfolio_action"] = merged.apply(
        lambda row: advice_action(row["signal_action"], row["weight"], row["suggested_target_weight"]),
        axis=1,
    )
    merged["weight_delta"] = merged["suggested_target_weight"] - merged["weight"]
    merged["advice_reason"] = merged.apply(lambda row: build_reason(row, max_single, watch_cap), axis=1)

    output_columns = [
        "symbol",
        "name",
        "weight",
        "suggested_target_weight",
        "weight_delta",
        "portfolio_action",
        "signal_action",
        "price_factor_score",
        "latest_close",
        "cost_price",
        "pnl_pct",
        "shares",
        "trade_date",
        "risk_flags",
        "advice_reason",
    ]
    return merged[output_columns].sort_values("weight", ascending=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build personalized advice from data/portfolio.csv.")
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--signals", type=Path, default=SIGNAL_LATEST_PATH)
    parser.add_argument("--max-single", type=float, default=0.12, help="Maximum target weight for one stock.")
    parser.add_argument("--watch-cap", type=float, default=0.04, help="Target cap for watch or degraded stocks.")
    args = parser.parse_args()

    portfolio = load_portfolio(args.portfolio)
    signals = load_signals(args.signals)
    advice = build_advice(portfolio, signals, args.max_single, args.watch_cap)

    ADVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    advice.to_csv(ADVICE_PATH, index=False, encoding="utf-8-sig")

    print(f"wrote {ADVICE_PATH.relative_to(PROJECT_ROOT)} rows={len(advice)}")
    print("\nportfolio advice")
    print(
        advice[
            [
                "symbol",
                "name",
                "weight",
                "suggested_target_weight",
                "portfolio_action",
                "signal_action",
                "price_factor_score",
                "pnl_pct",
            ]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
