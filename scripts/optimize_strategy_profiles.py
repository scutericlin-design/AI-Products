#!/usr/bin/env python3
"""Optimize portfolio backtest parameters for conservative/balanced/aggressive profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_portfolio_backtest import run_portfolio_backtest

SIGNAL_DAILY_PATH = PROJECT_ROOT / "data" / "processed" / "signal_daily.csv"
OUTPUT_JSON = PROJECT_ROOT / "data" / "processed" / "strategy_optimization_latest.json"


PROFILES = {
    "conservative": {
        "label": "保守",
        "target_annual_return": 0.08,
        "max_drawdown_limit": -0.08,
        "top_n": [5, 8, 10],
        "min_score": [78, 80, 82, 84],
        "max_position": [0.08, 0.10, 0.12],
        "target_exposure": [0.25, 0.35, 0.45],
        "actions": ["buy"],
    },
    "balanced": {
        "label": "平衡",
        "target_annual_return": 0.12,
        "max_drawdown_limit": -0.15,
        "top_n": [5, 8, 10, 12],
        "min_score": [72, 75, 78, 80],
        "max_position": [0.10, 0.12, 0.15, 0.20],
        "target_exposure": [0.45, 0.60, 0.75],
        "actions": ["buy"],
    },
    "aggressive": {
        "label": "进攻",
        "target_annual_return": 0.18,
        "max_drawdown_limit": -0.25,
        "top_n": [3, 5, 8, 10],
        "min_score": [70, 75, 78, 80],
        "max_position": [0.15, 0.20, 0.25, 0.30],
        "target_exposure": [0.65, 0.85, 1.00],
        "actions": ["buy"],
    },
}


def objective(metrics: dict[str, object], drawdown_limit: float, target_return: float) -> float:
    ann = float(metrics.get("annualized_return") or 0)
    mdd = float(metrics.get("max_drawdown") or 0)
    sharpe = float(metrics.get("sharpe") or 0)
    exposure = float(metrics.get("average_exposure") or 0)
    fill_rate = metrics.get("execution_fill_rate")
    fill_rate = float(fill_rate) if fill_rate is not None else 1.0
    data_gap_days = float(metrics.get("suspended_position_days") or 0)
    active_days = max(float(metrics.get("active_days") or 1), 1)
    average_names = max(float(metrics.get("average_names") or 1), 1)
    tradable_symbols = int(metrics.get("tradable_symbol_count") or 0)
    drawdown_penalty = max(0.0, abs(mdd) - abs(drawdown_limit)) * 2.5
    target_bonus = min(ann / target_return, 1.5) * 0.05 if target_return > 0 else 0
    exposure_penalty = 0.03 if exposure < 0.02 else 0
    fill_penalty = max(0.0, 0.70 - fill_rate) * 0.10
    data_gap_penalty = min(0.12, data_gap_days / (active_days * average_names) * 0.08)
    sample_penalty = 0.05 if tradable_symbols < 30 else 0.0
    return ann + 0.04 * sharpe + target_bonus - drawdown_penalty - exposure_penalty - fill_penalty - data_gap_penalty - sample_penalty


def optimize_profile(signals: pd.DataFrame, profile_name: str, profile: dict[str, object]) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
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
                    candidates.append(
                        {
                            "params": {
                                "top_n": top_n,
                                "min_score": min_score,
                                "max_position": max_position,
                                "target_exposure": target_exposure,
                                "rebalance_days": 5,
                                "actions": profile["actions"],
                            },
                            "metrics": metrics,
                            "objective": score,
                        }
                    )

    ranked = sorted(candidates, key=lambda item: item["objective"], reverse=True)
    best = ranked[0] if ranked else {"params": {}, "metrics": {}, "objective": None}
    return {
        "profile": profile_name,
        "label": profile["label"],
        "target_annual_return": profile["target_annual_return"],
        "max_drawdown_limit": profile["max_drawdown_limit"],
        "best": best,
        "top_candidates": ranked[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize strategy profiles.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_DAILY_PATH)
    args = parser.parse_args()

    signals = pd.read_csv(args.signals, dtype={"symbol": str}, encoding="utf-8-sig")
    profiles = [optimize_profile(signals, name, profile) for name, profile in PROFILES.items()]
    result = {
        "source": str(args.signals.relative_to(PROJECT_ROOT)),
        "profiles": profiles,
        "warning": "当前优化基于本地历史样本，并已计入A股涨跌停、T+1、停牌缺口、手续费、滑点和卖出印花税近似；全A历史样本补齐前，结果仅用于参数方向参考。",
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
