#!/usr/bin/env python3
"""Build first-pass strategy signals from price factors."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
FACTOR_PATH = PROCESSED_DIR / "factors_price_daily.csv"
SIGNAL_DAILY_PATH = PROCESSED_DIR / "signal_daily.csv"
SIGNAL_LATEST_PATH = PROCESSED_DIR / "signal_latest.csv"


def is_missing(value: object) -> bool:
    return pd.isna(value)


def risk_flags(row: pd.Series) -> list[str]:
    flags: list[str] = []

    if is_missing(row["momentum_20d"]):
        flags.append("insufficient_20d_history")
    elif row["momentum_20d"] < 0:
        flags.append("momentum_20d_negative")

    if is_missing(row["close_vs_ma20"]):
        flags.append("ma20_unavailable")
    elif row["close_vs_ma20"] < 0:
        flags.append("below_ma20")

    if not is_missing(row["volatility_20d"]) and row["volatility_20d"] > 0.55:
        flags.append("high_volatility")

    if is_missing(row["amount_ratio_5_20"]):
        flags.append("amount_ratio_unavailable")
    elif row["amount_ratio_5_20"] < 0.8:
        flags.append("amount_contraction")

    return flags


def base_action(score: float) -> str:
    if score >= 75:
        return "buy"
    if score >= 60:
        return "watch"
    if score >= 40:
        return "hold_or_reduce"
    return "avoid"


def downgrade_action(action: str, flags: list[str]) -> str:
    hard_blocks = {"insufficient_20d_history", "ma20_unavailable"}
    if hard_blocks.intersection(flags):
        return "watch"

    if "momentum_20d_negative" in flags and action == "buy":
        return "watch"

    if "below_ma20" in flags and action in {"buy", "watch"}:
        return "watch"

    if "high_volatility" in flags and action == "buy":
        return "watch"

    if "amount_contraction" in flags and action == "buy":
        return "watch"

    return action


def target_weight(action: str, score: float, flags: list[str]) -> float:
    if action == "buy":
        if score >= 85:
            return 0.08
        return 0.05
    if action == "watch":
        return 0.0
    if action == "hold_or_reduce":
        return 0.0
    return 0.0


def build_reason(row: pd.Series, action: str, flags: list[str]) -> str:
    pieces: list[str] = []
    score = row["price_factor_score"]

    pieces.append(f"价格因子评分 {score:.1f}")

    if not is_missing(row["momentum_20d"]):
        pieces.append(f"20日动量 {row['momentum_20d']:.2%}")

    if not is_missing(row["amount_ratio_5_20"]):
        pieces.append(f"5/20日成交额比 {row['amount_ratio_5_20']:.2f}")

    if not is_missing(row["close_vs_ma20"]):
        pieces.append(f"收盘价相对20日均线 {row['close_vs_ma20']:.2%}")

    if int(row.get("breakout_20d", 0)) == 1:
        pieces.append("创20日收盘新高")

    if flags:
        pieces.append("风险标记: " + ", ".join(flags))

    action_text = {
        "buy": "可纳入候选买入清单",
        "watch": "进入观察清单，等待趋势或资金确认",
        "hold_or_reduce": "已有持仓可保守处理，未持仓不建议新开",
        "avoid": "暂不进入策略股票池",
    }[action]
    pieces.append(action_text)
    return "；".join(pieces)


def build_signals(factors: pd.DataFrame) -> pd.DataFrame:
    signals = factors.copy()
    signals["symbol"] = signals["symbol"].astype(str).str.zfill(6)
    signals["risk_flags"] = signals.apply(lambda row: risk_flags(row), axis=1)
    signals["base_action"] = signals["price_factor_score"].apply(base_action)
    signals["action"] = signals.apply(
        lambda row: downgrade_action(row["base_action"], row["risk_flags"]), axis=1
    )
    signals["target_weight"] = signals.apply(
        lambda row: target_weight(row["action"], row["price_factor_score"], row["risk_flags"]), axis=1
    )
    signals["reason"] = signals.apply(lambda row: build_reason(row, row["action"], row["risk_flags"]), axis=1)
    signals["risk_flags"] = signals["risk_flags"].apply(lambda flags: "|".join(flags))

    output_columns = [
        "trade_date",
        "symbol",
        "name",
        "close",
        "price_factor_score",
        "base_action",
        "action",
        "target_weight",
        "reason",
        "risk_flags",
        "momentum_20d",
        "close_vs_ma20",
        "amount_ratio_5_20",
        "volatility_20d",
        "breakout_20d",
    ]
    return signals[output_columns].sort_values(
        ["trade_date", "price_factor_score"], ascending=[True, False]
    )


def latest_by_symbol(signals: pd.DataFrame) -> pd.DataFrame:
    return (
        signals.sort_values("trade_date")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values("price_factor_score", ascending=False)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build strategy signals from price factors.")
    parser.add_argument("--factors", type=Path, default=FACTOR_PATH)
    args = parser.parse_args()

    if not args.factors.exists():
        raise SystemExit(f"Factor file not found: {args.factors}")

    factors = pd.read_csv(args.factors, dtype={"symbol": str})
    signals = build_signals(factors)
    latest = latest_by_symbol(signals)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    signals.to_csv(SIGNAL_DAILY_PATH, index=False, encoding="utf-8")
    latest.to_csv(SIGNAL_LATEST_PATH, index=False, encoding="utf-8")

    print(f"wrote {SIGNAL_DAILY_PATH.relative_to(PROJECT_ROOT)} rows={len(signals)}")
    print(f"wrote {SIGNAL_LATEST_PATH.relative_to(PROJECT_ROOT)} rows={len(latest)}")
    print("\nlatest signals")
    print(
        latest[
            ["trade_date", "symbol", "name", "price_factor_score", "action", "target_weight", "risk_flags"]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
