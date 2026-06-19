#!/usr/bin/env python3
"""Build personalized portfolio advice from API/database holdings and latest signals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
ADVICE_PATH = PROJECT_ROOT / "data" / "processed" / "portfolio_advice.csv"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.models import PortfolioPosition, User


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


def load_portfolio_from_db(email: str | None = None) -> pd.DataFrame:
    db = SessionLocal()
    try:
        query = db.query(PortfolioPosition).join(User, PortfolioPosition.user_id == User.id)
        if email:
            query = query.filter(User.email == email.lower())
        positions = query.order_by(PortfolioPosition.user_id, PortfolioPosition.weight.desc()).all()
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
    finally:
        db.close()


def load_signals(path: Path) -> pd.DataFrame:
    signals = pd.read_csv(path, dtype={"symbol": str})
    signals = signals.copy()
    signals["symbol"] = signals["symbol"].astype(str).str.zfill(6)
    return signals


OPTIONAL_SIGNAL_COLUMNS = [
    "alpha_score",
    "fundamental_quality_score",
    "valuation_sanity_score",
    "liquidity_capacity_score",
    "risk_control_score",
    "crowding_penalty",
    "financial_data_score",
    "data_completeness",
    "raw_institutional_score",
    "score_rank",
    "gate_penalty_score",
    "confidence",
    "model_version",
    "market_regime_key",
    "market_regime_label",
    "market_breadth_20d",
    "market_breadth_60d",
    "market_avg_momentum_20d",
    "market_amount_trend",
    "market_volatility_20d",
    "adaptive_note",
    "effective_buy_score_threshold",
    "score_weight_alpha",
    "score_weight_fundamental",
    "score_weight_valuation",
    "score_weight_liquidity",
    "score_weight_risk",
    "recommendation_tier",
    "signal_source",
    "turnover_rate",
]


def target_for_signal(
    signal_action: str,
    current_weight: float,
    max_single: float,
    watch_cap: float,
    signal_target_weight: float | None = None,
) -> float:
    if signal_action == "buy":
        if signal_target_weight is not None and pd.notna(signal_target_weight) and signal_target_weight > 0:
            return min(max_single, float(signal_target_weight))
        return min(max_single, max(0.05, min(0.08, max_single)))
    if signal_action == "watch":
        return min(current_weight, watch_cap)
    if signal_action == "hold_or_reduce":
        return min(current_weight, watch_cap)
    if signal_action == "avoid":
        return 0.0
    if signal_action == "not_in_system_pool":
        return min(current_weight, watch_cap)
    return min(current_weight, watch_cap)


def advice_action(signal_action: str, current_weight: float, target_weight: float) -> str:
    if signal_action == "avoid":
        return "exit_or_strong_reduce"
    if signal_action == "not_in_system_pool" and current_weight > target_weight + 0.02:
        return "reduce"
    if current_weight > target_weight + 0.02:
        return "reduce"
    if signal_action == "buy" and current_weight < target_weight - 0.01:
        return "add"
    if signal_action == "watch":
        return "hold_watch_no_new"
    return "hold"


def _factor_value(row: pd.Series, column: str) -> float | None:
    value = row.get(column)
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_factor_summary(row: pd.Series) -> str:
    strengths: list[str] = []
    risks: list[str] = []
    alpha = _factor_value(row, "alpha_score")
    quality = _factor_value(row, "fundamental_quality_score")
    valuation = _factor_value(row, "valuation_sanity_score")
    liquidity = _factor_value(row, "liquidity_capacity_score")
    risk = _factor_value(row, "risk_control_score")
    completeness = _factor_value(row, "data_completeness")
    crowding = _factor_value(row, "crowding_penalty")
    gate = _factor_value(row, "gate_penalty_score")

    if alpha is not None and alpha >= 70:
        strengths.append("趋势/Alpha强")
    if quality is not None and quality >= 65:
        strengths.append("基本面较好")
    if valuation is not None and valuation >= 60:
        strengths.append("估值相对合理")
    if liquidity is not None and liquidity >= 70:
        strengths.append("成交承接好")
    if risk is not None and risk >= 65:
        strengths.append("波动风险可控")

    if gate is not None and gate > 0:
        risks.append(f"门禁扣 {gate:.1f}")
    if crowding is not None and crowding >= 8:
        risks.append("拥挤/追高风险")
    if completeness is not None and completeness < 0.75:
        risks.append("数据覆盖待补齐")
    if risk is not None and risk < 55:
        risks.append("风控分偏低")
    if liquidity is not None and liquidity < 55:
        risks.append("流动性不足")

    strength_text = "、".join(strengths[:3]) if strengths else "暂无突出优势"
    risk_text = "、".join(risks[:3]) if risks else "硬性风险暂未突出"
    return f"优势：{strength_text}；风险：{risk_text}"


def build_reason(row: pd.Series, max_single: float, watch_cap: float) -> str:
    parts: list[str] = []
    parts.append(f"当前仓位 {row['weight']:.2%}")

    if pd.notna(row.get("pnl_pct")):
        parts.append(f"浮盈亏 {row['pnl_pct']:.2%}")

    if pd.notna(row.get("price_factor_score")):
        parts.append(f"机构评分 {row['price_factor_score']:.1f}")
    if pd.notna(row.get("turnover_rate")):
        turnover_rate = float(row["turnover_rate"])
        parts.append(f"换手率 {turnover_rate:.2f}%")

    if pd.notna(row.get("market_regime_label")):
        parts.append(f"市场风格 {row['market_regime_label']}")
    parts.append(build_factor_summary(row))

    signal_action = row.get("signal_action", "no_signal")
    parts.append(f"模型信号 {signal_action}")

    if row.get("model_version"):
        parts.append(f"模型版本 {row['model_version']}")

    if row["weight"] > max_single:
        parts.append(f"超过单票上限 {max_single:.0%}")

    if signal_action == "not_in_system_pool":
        parts.append("未进入当前系统股票池，不建议新增仓位")

    if signal_action in {"watch", "hold_or_reduce"} and row["weight"] > watch_cap:
        parts.append(f"观察/降级票建议压到 {watch_cap:.0%} 以内")

    if signal_action == "avoid":
        parts.append("策略不支持继续持有，优先制定退出计划")

    risk_flags = row.get("risk_flags")
    if isinstance(risk_flags, str) and risk_flags:
        parts.append(f"风险标记: {risk_flags}")

    return "；".join(parts)


def build_advice(portfolio: pd.DataFrame, signals: pd.DataFrame, max_single: float, watch_cap: float) -> pd.DataFrame:
    signals = signals.copy()
    for column in OPTIONAL_SIGNAL_COLUMNS:
        if column not in signals.columns:
            signals[column] = pd.NA

    merged = portfolio.merge(
        signals,
        how="left",
        on="symbol",
        suffixes=("_portfolio", "_signal"),
    )

    merged["name"] = merged["name_portfolio"].fillna(merged.get("name_signal"))
    merged["signal_action"] = merged["action"].fillna("not_in_system_pool")
    merged["model_version"] = merged["model_version"].fillna("institutional_score_v6_adaptive_tushare")
    merged["confidence"] = merged["confidence"].fillna("low")
    merged["signal_source"] = merged["signal_source"].fillna("no_system_signal")
    merged["latest_close"] = merged["close"]
    merged["pnl_pct"] = merged["latest_close"] / merged["cost_price"] - 1
    merged.loc[merged["latest_close"].isna() | merged["cost_price"].isna(), "pnl_pct"] = pd.NA

    merged["suggested_target_weight"] = merged.apply(
        lambda row: target_for_signal(
            row["signal_action"],
            row["weight"],
            max_single,
            watch_cap,
            row.get("target_weight"),
        ),
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
        "turnover_rate",
        "alpha_score",
        "fundamental_quality_score",
        "valuation_sanity_score",
        "liquidity_capacity_score",
        "risk_control_score",
        "crowding_penalty",
        "financial_data_score",
        "data_completeness",
        "raw_institutional_score",
        "score_rank",
        "gate_penalty_score",
        "confidence",
        "model_version",
        "market_regime_key",
        "market_regime_label",
        "market_breadth_20d",
        "market_breadth_60d",
        "market_avg_momentum_20d",
        "market_amount_trend",
        "market_volatility_20d",
        "adaptive_note",
        "effective_buy_score_threshold",
        "score_weight_alpha",
        "score_weight_fundamental",
        "score_weight_valuation",
        "score_weight_liquidity",
        "score_weight_risk",
        "recommendation_tier",
        "signal_source",
        "advice_reason",
    ]
    return merged[output_columns].sort_values(
        ["price_factor_score", "weight"],
        ascending=[False, False],
        na_position="last",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build personalized advice from server-side portfolio positions.")
    parser.add_argument("--email", help="Optional account email; defaults to all stored portfolio positions.")
    parser.add_argument("--portfolio-csv", type=Path, help="Legacy explicit CSV import for development only.")
    parser.add_argument("--signals", type=Path, default=SIGNAL_LATEST_PATH)
    parser.add_argument("--max-single", type=float, default=0.12, help="Maximum target weight for one stock.")
    parser.add_argument("--watch-cap", type=float, default=0.04, help="Target cap for watch or degraded stocks.")
    args = parser.parse_args()

    portfolio = load_portfolio(args.portfolio_csv) if args.portfolio_csv else load_portfolio_from_db(args.email)
    if portfolio.empty:
        raise SystemExit("No portfolio positions found. Add holdings in the web page first.")
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
