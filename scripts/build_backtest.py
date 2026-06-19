#!/usr/bin/env python3
"""Run an event-style backtest from existing daily signal data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_DAILY_PATH = PROJECT_ROOT / "data" / "processed" / "signal_daily.csv"
BACKTEST_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "backtest_latest.json"
BACKTEST_TRADES_PATH = PROJECT_ROOT / "data" / "processed" / "backtest_trades.csv"


def max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1 + returns.fillna(0)).cumprod()
    peak = equity.cummax()
    drawdown = equity / peak - 1
    return float(drawdown.min())


def run_backtest(
    signals: pd.DataFrame,
    hold_days: int = 20,
    min_score: float = 78,
    action: str = "buy",
) -> tuple[dict[str, object], pd.DataFrame]:
    data = signals.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    data["symbol"] = data["symbol"].astype(str).str.zfill(6)
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["price_factor_score"] = pd.to_numeric(data["price_factor_score"], errors="coerce")
    data = data.sort_values(["symbol", "trade_date"])
    data["future_close"] = data.groupby("symbol")["close"].shift(-hold_days)
    data["future_date"] = data.groupby("symbol")["trade_date"].shift(-hold_days)
    data["forward_return"] = data["future_close"] / data["close"] - 1

    trades = data[
        (data["action"] == action)
        & (data["price_factor_score"] >= min_score)
        & data["close"].notna()
        & data["future_close"].notna()
    ].copy()

    if trades.empty:
        metrics: dict[str, object] = {
            "trade_count": 0,
            "hit_rate": None,
            "average_return": None,
            "median_return": None,
            "best_return": None,
            "worst_return": None,
            "max_drawdown": None,
            "hold_days": hold_days,
            "min_score": min_score,
            "action": action,
            "start_date": None,
            "end_date": None,
        }
        return metrics, trades

    daily_event_returns = trades.groupby("trade_date")["forward_return"].mean().sort_index()
    metrics = {
        "trade_count": int(len(trades)),
        "hit_rate": float((trades["forward_return"] > 0).mean()),
        "average_return": float(trades["forward_return"].mean()),
        "median_return": float(trades["forward_return"].median()),
        "best_return": float(trades["forward_return"].max()),
        "worst_return": float(trades["forward_return"].min()),
        "max_drawdown": max_drawdown(daily_event_returns),
        "hold_days": hold_days,
        "min_score": min_score,
        "action": action,
        "start_date": trades["trade_date"].min().date().isoformat(),
        "end_date": trades["trade_date"].max().date().isoformat(),
    }

    output_columns = [
        "trade_date",
        "future_date",
        "symbol",
        "name",
        "close",
        "future_close",
        "forward_return",
        "price_factor_score",
        "risk_flags",
        "reason",
    ]
    return metrics, trades[output_columns].sort_values("trade_date")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run event-style backtest from signal_daily.csv.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_DAILY_PATH)
    parser.add_argument("--hold-days", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=78)
    parser.add_argument("--action", default="buy", choices=["buy", "watch", "hold_or_reduce"])
    parser.add_argument("--output-json", type=Path, default=BACKTEST_JSON_PATH)
    parser.add_argument("--output-trades", type=Path, default=BACKTEST_TRADES_PATH)
    args = parser.parse_args()

    signals = pd.read_csv(args.signals, dtype={"symbol": str}, encoding="utf-8-sig")
    metrics, trades = run_backtest(signals, hold_days=args.hold_days, min_score=args.min_score, action=args.action)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps({"metrics": metrics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    trades.to_csv(args.output_trades, index=False, encoding="utf-8-sig")

    print(json.dumps({"metrics": metrics, "trades_path": str(args.output_trades)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
