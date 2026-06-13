#!/usr/bin/env python3
"""Run a portfolio-level backtest from daily signal data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_DAILY_PATH = PROJECT_ROOT / "data" / "processed" / "signal_daily.csv"
OUTPUT_JSON = PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_latest.json"
OUTPUT_CURVE = PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_curve.csv"
OUTPUT_REBALANCES = PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_rebalances.csv"


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    drawdown = equity / peak - 1
    return float(drawdown.min())


def annualized_return(equity: pd.Series) -> float | None:
    if len(equity) < 2:
        return None
    years = len(equity) / 252
    if years <= 0:
        return None
    return float(equity.iloc[-1] ** (1 / years) - 1)


def run_portfolio_backtest(
    signals: pd.DataFrame,
    top_n: int = 10,
    min_score: float = 78.0,
    rebalance_days: int = 5,
    max_position: float = 0.10,
    target_exposure: float = 0.60,
    actions: set[str] | None = None,
    fee_bps: float = 5.0,
    slippage_bps: float = 10.0,
) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    actions = actions or {"buy"}
    data = signals.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    data["symbol"] = data["symbol"].astype(str).str.zfill(6)
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["price_factor_score"] = pd.to_numeric(data["price_factor_score"], errors="coerce")
    data = data.dropna(subset=["trade_date", "symbol", "close"]).sort_values(["trade_date", "symbol"])

    close = data.pivot_table(index="trade_date", columns="symbol", values="close", aggfunc="last").sort_index()
    returns = close.pct_change().shift(-1)
    dates = list(close.index)
    cost_rate = (fee_bps + slippage_bps) / 10000

    weights: dict[str, float] = {}
    curve_rows: list[dict[str, object]] = []
    rebalance_rows: list[dict[str, object]] = []
    equity = 1.0

    signal_by_date = {date: frame for date, frame in data.groupby("trade_date")}

    for index, day in enumerate(dates[:-1]):
        turnover = 0.0
        rebalanced = index % rebalance_days == 0
        if rebalanced:
            frame = signal_by_date.get(day, pd.DataFrame())
            candidates = frame[
                (frame["action"].isin(actions))
                & (frame["price_factor_score"] >= min_score)
            ].sort_values("price_factor_score", ascending=False).head(top_n)

            if candidates.empty:
                new_weights: dict[str, float] = {}
            else:
                raw_scores = (candidates["price_factor_score"] - min_score + 1).clip(lower=1)
                score_weights = raw_scores / raw_scores.sum()
                desired = {
                    row.symbol: float(target_exposure * score_weights.iloc[index])
                    for index, row in enumerate(candidates.itertuples())
                }
                new_weights = {symbol: min(weight, max_position) for symbol, weight in desired.items()}

                # Redistribute leftover exposure to uncapped names. This keeps total exposure closer
                # to the profile target while respecting the single-name cap.
                for _ in range(4):
                    leftover = target_exposure - sum(new_weights.values())
                    if leftover <= 0.0001:
                        break
                    open_symbols = [symbol for symbol, weight in new_weights.items() if weight < max_position - 0.0001]
                    if not open_symbols:
                        break
                    add_each = leftover / len(open_symbols)
                    for symbol in open_symbols:
                        new_weights[symbol] = min(max_position, new_weights[symbol] + add_each)

            symbols = set(weights) | set(new_weights)
            turnover = sum(abs(new_weights.get(symbol, 0.0) - weights.get(symbol, 0.0)) for symbol in symbols)
            weights = new_weights
            rebalance_rows.append(
                {
                    "trade_date": day.date().isoformat(),
                    "names": len(weights),
                    "gross_exposure": sum(weights.values()),
                    "turnover": turnover,
                    "symbols": "|".join(weights.keys()),
                }
            )

        next_return = 0.0
        for symbol, weight in weights.items():
            value = returns.at[day, symbol] if symbol in returns.columns else 0.0
            if pd.notna(value):
                next_return += weight * float(value)
        cost = turnover * cost_rate if rebalanced else 0.0
        net_return = next_return - cost
        equity *= 1 + net_return
        curve_rows.append(
            {
                "trade_date": dates[index + 1].date().isoformat(),
                "daily_return": net_return,
                "gross_return": next_return,
                "transaction_cost": cost,
                "equity": equity,
                "names": len(weights),
                "gross_exposure": sum(weights.values()),
                "turnover": turnover if rebalanced else 0.0,
                "rebalanced": rebalanced,
            }
        )

    curve = pd.DataFrame(curve_rows)
    rebalances = pd.DataFrame(rebalance_rows)
    if curve.empty:
        metrics = {"trade_days": 0}
        return metrics, curve, rebalances

    curve["trade_date_dt"] = pd.to_datetime(curve["trade_date"])
    returns_series = curve["daily_return"].fillna(0)
    equity_series = curve["equity"].ffill()
    active_curve = curve[curve["gross_exposure"] > 0].copy()
    active_returns = active_curve["daily_return"].fillna(0)
    monthly = curve.set_index("trade_date_dt")["daily_return"].resample("ME").apply(lambda items: (1 + items).prod() - 1)
    ann_return = annualized_return(equity_series)
    ann_vol = float(returns_series.std(ddof=0) * (252 ** 0.5)) if len(returns_series) > 1 else None
    mdd = max_drawdown(equity_series)
    metrics = {
        "mode": "portfolio_level",
        "trade_days": int(len(curve)),
        "active_days": int(len(active_curve)),
        "start_date": curve["trade_date"].iloc[0],
        "end_date": curve["trade_date"].iloc[-1],
        "top_n": top_n,
        "min_score": min_score,
        "rebalance_days": rebalance_days,
        "max_position": max_position,
        "target_exposure": target_exposure,
        "actions": sorted(actions),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "cumulative_return": float(equity_series.iloc[-1] - 1),
        "annualized_return": ann_return,
        "annualized_volatility": ann_vol,
        "max_drawdown": mdd,
        "sharpe": float(ann_return / ann_vol) if ann_return is not None and ann_vol else None,
        "calmar": float(ann_return / abs(mdd)) if ann_return is not None and mdd < 0 else None,
        "daily_win_rate": float((active_returns > 0).mean()) if len(active_returns) else None,
        "monthly_win_rate": float((monthly > 0).mean()) if len(monthly) else None,
        "average_names": float(active_curve["names"].mean()) if len(active_curve) else 0.0,
        "average_exposure": float(curve["gross_exposure"].mean()),
        "average_active_exposure": float(active_curve["gross_exposure"].mean()) if len(active_curve) else 0.0,
        "exposure_utilization": float(curve["gross_exposure"].mean() / target_exposure) if target_exposure else None,
        "average_turnover_on_rebalance": float(rebalances["turnover"].mean()) if not rebalances.empty else 0.0,
        "total_transaction_cost": float(curve["transaction_cost"].sum()),
    }
    return metrics, curve.drop(columns=["trade_date_dt"]), rebalances


def main() -> int:
    parser = argparse.ArgumentParser(description="Run portfolio-level backtest from signal_daily.csv.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_DAILY_PATH)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=78.0)
    parser.add_argument("--rebalance-days", type=int, default=5)
    parser.add_argument("--max-position", type=float, default=0.10)
    parser.add_argument("--target-exposure", type=float, default=0.60)
    parser.add_argument("--actions", default="buy", help="Comma separated signal actions, e.g. buy,watch")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    args = parser.parse_args()

    signals = pd.read_csv(args.signals, dtype={"symbol": str}, encoding="utf-8-sig")
    metrics, curve, rebalances = run_portfolio_backtest(
        signals,
        top_n=args.top_n,
        min_score=args.min_score,
        rebalance_days=args.rebalance_days,
        max_position=args.max_position,
        target_exposure=args.target_exposure,
        actions={item.strip() for item in args.actions.split(",") if item.strip()},
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({"metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")
    curve.to_csv(OUTPUT_CURVE, index=False, encoding="utf-8-sig")
    rebalances.to_csv(OUTPUT_REBALANCES, index=False, encoding="utf-8-sig")
    print(json.dumps({"metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
