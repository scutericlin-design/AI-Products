from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.models import PortfolioPosition
from scripts.build_portfolio_advice import build_advice, load_signals


SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    return value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in record.items()}


def positions_to_frame(positions: list[PortfolioPosition]) -> pd.DataFrame:
    rows = [
        {
            "symbol": position.symbol,
            "name": position.name,
            "weight": position.weight,
            "cost_price": position.cost_price,
            "shares": position.shares,
        }
        for position in positions
    ]
    return pd.DataFrame(rows, columns=["symbol", "name", "weight", "cost_price", "shares"])


def build_user_advice(
    positions: list[PortfolioPosition],
    signals_path: Path = SIGNAL_LATEST_PATH,
    max_single: float = 0.12,
    watch_cap: float = 0.04,
) -> list[dict[str, Any]]:
    if not positions:
        return []
    portfolio = positions_to_frame(positions)
    signals = load_signals(signals_path)
    advice = build_advice(portfolio, signals, max_single=max_single, watch_cap=watch_cap)
    return [_clean_record(record) for record in advice.to_dict(orient="records")]


def summarize_risk(advice_rows: list[dict[str, Any]], max_single: float = 0.12) -> dict[str, Any]:
    total_weight = sum(float(row.get("weight") or 0) for row in advice_rows)
    max_weight = max((float(row.get("weight") or 0) for row in advice_rows), default=0.0)
    reduce_count = sum(1 for row in advice_rows if "reduce" in str(row.get("portfolio_action") or ""))
    exit_count = sum(1 for row in advice_rows if str(row.get("portfolio_action")) == "exit_or_strong_reduce")
    pnl_values = [float(row["pnl_pct"]) for row in advice_rows if row.get("pnl_pct") is not None]
    average_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else None

    notes: list[str] = []
    if total_weight > 0.95:
        notes.append("总仓位接近满仓，建议保留现金缓冲以应对高波动。")
    if max_weight > max_single:
        notes.append(f"存在单票仓位超过 {max_single:.0%} 上限，需要优先降集中度。")
    if exit_count:
        notes.append(f"有 {exit_count} 只持仓被模型标记为强减/退出。")
    if reduce_count:
        notes.append(f"有 {reduce_count} 只持仓需要降低风险暴露。")
    if average_pnl is not None and average_pnl < -0.08:
        notes.append("组合平均浮亏超过 8%，需要检查止损纪律。")
    if not notes:
        notes.append("组合未触发硬性风险阈值，继续按信号变化观察。")

    if exit_count or max_weight > max_single * 1.8 or total_weight > 1.1:
        risk_level = "high"
    elif reduce_count or max_weight > max_single or total_weight > 0.95:
        risk_level = "medium"
    else:
        risk_level = "controlled"

    return {
        "total_weight": total_weight,
        "position_count": len(advice_rows),
        "max_single_weight": max_weight,
        "reduce_count": reduce_count,
        "exit_count": exit_count,
        "average_pnl_pct": average_pnl,
        "risk_level": risk_level,
        "notes": notes,
    }
