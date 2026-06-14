#!/usr/bin/env python3
"""Run walk-forward validation for portfolio strategy profiles."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_portfolio_backtest import run_portfolio_backtest
from scripts.optimize_strategy_profiles import PROFILES, objective


SIGNAL_DAILY_PATH = PROJECT_ROOT / "data" / "processed" / "signal_daily.csv"
OUTPUT_JSON = PROJECT_ROOT / "data" / "processed" / "walk_forward_latest.json"


def as_float(value: object, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    return float(value)


def param_signature(params: dict[str, object]) -> str:
    return (
        f"Top{params.get('top_n')} / 阈值{params.get('min_score')} / "
        f"单票{as_float(params.get('max_position')):.0%} / "
        f"目标{as_float(params.get('target_exposure')):.0%}"
    )


def optimize_train_window(signals: pd.DataFrame, profile_name: str) -> dict[str, object]:
    profile = PROFILES[profile_name]
    best: dict[str, object] | None = None

    for top_n in profile["top_n"]:
        for min_score in profile["min_score"]:
            for max_position in profile["max_position"]:
                for target_exposure in profile["target_exposure"]:
                    metrics, _, _ = run_portfolio_backtest(
                        signals,
                        top_n=int(top_n),
                        min_score=float(min_score),
                        rebalance_days=5,
                        max_position=float(max_position),
                        target_exposure=float(target_exposure),
                        actions=set(profile["actions"]),
                        fee_bps=5,
                        slippage_bps=10,
                        enforce_trading_rules=True,
                        stamp_tax_bps=5,
                        limit_buffer_pct=0.003,
                    )
                    score = objective(
                        metrics,
                        drawdown_limit=float(profile["max_drawdown_limit"]),
                        target_return=float(profile["target_annual_return"]),
                    )
                    candidate = {
                        "params": {
                            "top_n": int(top_n),
                            "min_score": float(min_score),
                            "max_position": float(max_position),
                            "target_exposure": float(target_exposure),
                            "rebalance_days": 5,
                            "actions": profile["actions"],
                        },
                        "metrics": metrics,
                        "objective": score,
                    }
                    if best is None or score > float(best["objective"]):
                        best = candidate

    return best or {"params": {}, "metrics": {}, "objective": None}


def slice_dates(signals: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return signals[(signals["trade_date"] >= start) & (signals["trade_date"] <= end)].copy()


def stability_grade(window_rows: list[dict[str, object]]) -> tuple[str, str]:
    if len(window_rows) < 3:
        return "样本不足", "滚动窗口少于 3 个，先补齐更长历史再判断稳定性。"

    positive_rate = sum(as_float(row["test_cumulative_return"]) > 0 for row in window_rows) / len(window_rows)
    median_annual = pd.Series([as_float(row["test_annualized_return"]) for row in window_rows]).median()
    worst_drawdown = min(as_float(row["test_max_drawdown"]) for row in window_rows)

    if positive_rate >= 0.65 and median_annual > 0.08 and worst_drawdown > -0.18:
        return "较稳健", "样本外窗口多数赚钱，回撤暂时可控；仍需全 A 历史和行业切片确认。"
    if positive_rate >= 0.50 and median_annual > 0:
        return "可继续验证", "样本外结果有一定延续性，但还不足以进入实盘自动化。"
    return "不稳定", "样本外表现不足，当前参数应继续收紧风控或扩充因子。"


def run_walk_forward(
    signals: pd.DataFrame,
    profile_name: str = "balanced",
    train_days: int = 120,
    test_days: int = 40,
    step_days: int = 40,
) -> dict[str, object]:
    if profile_name not in PROFILES:
        raise ValueError(f"Unknown profile: {profile_name}")

    data = signals.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    data["symbol"] = data["symbol"].astype(str).str.zfill(6)
    dates = pd.Index(sorted(data["trade_date"].dropna().unique()))
    window_rows: list[dict[str, object]] = []

    max_start = len(dates) - train_days - test_days
    starts = range(0, max_start + 1, step_days) if max_start >= 0 else []

    for window_no, start_index in enumerate(starts, start=1):
        train_start = pd.Timestamp(dates[start_index])
        train_end = pd.Timestamp(dates[start_index + train_days - 1])
        test_start = pd.Timestamp(dates[start_index + train_days])
        test_end = pd.Timestamp(dates[min(start_index + train_days + test_days - 1, len(dates) - 1)])

        train = slice_dates(data, train_start, train_end)
        test = slice_dates(data, test_start, test_end)
        if train["trade_date"].nunique() < 30 or test["trade_date"].nunique() < 10:
            continue

        best = optimize_train_window(train, profile_name)
        params = best["params"]
        metrics, _, _ = run_portfolio_backtest(
            test,
            top_n=int(params["top_n"]),
            min_score=float(params["min_score"]),
            rebalance_days=int(params["rebalance_days"]),
            max_position=float(params["max_position"]),
            target_exposure=float(params["target_exposure"]),
            actions=set(params["actions"]),
            fee_bps=5,
            slippage_bps=10,
            enforce_trading_rules=True,
            stamp_tax_bps=5,
            limit_buffer_pct=0.003,
        )
        window_rows.append(
            {
                "window": window_no,
                "train_start": train_start.date().isoformat(),
                "train_end": train_end.date().isoformat(),
                "test_start": test_start.date().isoformat(),
                "test_end": test_end.date().isoformat(),
                "params": params,
                "params_label": param_signature(params),
                "train_objective": best["objective"],
                "train_annualized_return": best["metrics"].get("annualized_return"),
                "train_max_drawdown": best["metrics"].get("max_drawdown"),
                "test_cumulative_return": metrics.get("cumulative_return"),
                "test_annualized_return": metrics.get("annualized_return"),
                "test_max_drawdown": metrics.get("max_drawdown"),
                "test_sharpe": metrics.get("sharpe"),
                "test_average_active_exposure": metrics.get("average_active_exposure"),
                "test_execution_fill_rate": metrics.get("execution_fill_rate"),
                "test_blocked_buy_count": metrics.get("blocked_buy_count"),
                "test_blocked_sell_count": metrics.get("blocked_sell_count"),
            }
        )

    grade, grade_reason = stability_grade(window_rows)
    annuals = pd.Series([as_float(row["test_annualized_return"]) for row in window_rows], dtype="float64")
    cumulatives = pd.Series([as_float(row["test_cumulative_return"]) for row in window_rows], dtype="float64")
    drawdowns = pd.Series([as_float(row["test_max_drawdown"]) for row in window_rows], dtype="float64")
    params_counter = Counter(row["params_label"] for row in window_rows)
    most_common_params = params_counter.most_common(1)[0][0] if params_counter else "--"

    production_ready = (
        len(window_rows) >= 6
        and data["symbol"].nunique() >= 100
        and grade in {"较稳健", "可继续验证"}
        and float((cumulatives > 0).mean()) >= 0.55 if len(cumulatives) else False
    )
    summary = {
        "profile": profile_name,
        "profile_label": PROFILES[profile_name]["label"],
        "trade_days": int(len(dates)),
        "universe_symbol_count": int(data["symbol"].nunique()),
        "window_count": len(window_rows),
        "positive_window_rate": float((cumulatives > 0).mean()) if len(cumulatives) else None,
        "average_test_annualized_return": float(annuals.mean()) if len(annuals) else None,
        "median_test_annualized_return": float(annuals.median()) if len(annuals) else None,
        "worst_test_drawdown": float(drawdowns.min()) if len(drawdowns) else None,
        "best_test_annualized_return": float(annuals.max()) if len(annuals) else None,
        "most_common_params": most_common_params,
        "stability_grade": grade,
        "stability_reason": grade_reason,
        "production_ready": bool(production_ready),
    }
    return {
        "source": str(SIGNAL_DAILY_PATH.relative_to(PROJECT_ROOT)),
        "summary": summary,
        "windows": window_rows,
        "warning": "滚动样本外验证已计入A股执行约束。当前本地历史股票数较少，不能替代全A历史样本验证。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run walk-forward validation from signal_daily.csv.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_DAILY_PATH)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="balanced")
    parser.add_argument("--train-days", type=int, default=120)
    parser.add_argument("--test-days", type=int, default=40)
    parser.add_argument("--step-days", type=int, default=40)
    args = parser.parse_args()

    signals = pd.read_csv(args.signals, dtype={"symbol": str}, encoding="utf-8-sig")
    result = run_walk_forward(
        signals,
        profile_name=args.profile,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
