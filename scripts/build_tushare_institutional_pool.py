#!/usr/bin/env python3
"""Build an institutional A-share pool from TuShare Pro proxy data.

This pipeline upgrades the real-time spot-only pool into a professional
multi-layer score that combines price action, liquidity/capacity, valuation,
fundamental quality, risk control, crowding and data completeness.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.tushare_proxy_client import TushareProxyClient, credentials_from_db
from app.services.data_cache import read_market_cache, write_market_cache


MODEL_VERSION = "institutional_score_v4_tushare"
RAW_TUSHARE_DIR = PROJECT_ROOT / "data" / "tushare"
DAILY_DIR = RAW_TUSHARE_DIR / "daily"
DAILY_BASIC_DIR = RAW_TUSHARE_DIR / "daily_basic"
FUNDAMENTAL_DIR = RAW_TUSHARE_DIR / "fina_indicator"
STOCK_BASIC_PATH = RAW_TUSHARE_DIR / "stock_basic.csv"
TRADE_CAL_PATH = RAW_TUSHARE_DIR / "trade_cal.csv"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_PATH = PROCESSED_DIR / "recommended_pool.csv"
SCORED_UNIVERSE_PATH = PROCESSED_DIR / "scored_universe_latest.csv"
INSTITUTIONAL_FACTORS_PATH = PROCESSED_DIR / "institutional_factors_latest.csv"


def normalize_symbol(ts_code: object) -> str:
    return str(ts_code).split(".")[0].zfill(6)


def classify_board(ts_code: str, market: object = None) -> str:
    symbol = normalize_symbol(ts_code)
    market_text = str(market or "")
    if "北交所" in market_text or symbol.startswith(("4", "8", "9")):
        return "beijing"
    if symbol.startswith(("300", "301")):
        return "chinext"
    if symbol.startswith(("688", "689")):
        return "star"
    return "main"


def clipped(series: pd.Series, low: float, high: float, higher_is_better: bool = True) -> pd.Series:
    score = ((series - low) / (high - low)).clip(0, 1)
    return score if higher_is_better else 1 - score


def sweet_spot(series: pd.Series, low: float, peak: float, high: float) -> pd.Series:
    left = ((series - low) / (peak - low)).clip(0, 1)
    right = ((high - series) / (high - peak)).clip(0, 1)
    return pd.concat([left, right], axis=1).min(axis=1).fillna(0)


def percentile(frame: pd.DataFrame, column: str, higher_is_better: bool = True, neutral: float = 0.5) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    mask = values.notna()
    scores = pd.Series(neutral, index=frame.index, dtype="float64")
    if mask.sum() >= 3:
        ranks = values[mask].rank(pct=True)
        scores.loc[mask] = ranks if higher_is_better else 1 - ranks
    return scores.clip(0, 1)


def valid_low_percentile(frame: pd.DataFrame, column: str, low: float = 0, high: float | None = None) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    mask = values.notna() & (values > low)
    if high is not None:
        mask &= values <= high
    scores = pd.Series(0.5, index=frame.index, dtype="float64")
    if mask.sum() >= 3:
        scores.loc[mask] = 1 - values[mask].rank(pct=True)
    return scores.clip(0, 1)


def cache_query(
    client: TushareProxyClient,
    api_name: str,
    path: Path,
    params: dict[str, object],
    fields: str,
    refresh: bool,
    dtype: dict[str, str] | None = None,
) -> pd.DataFrame:
    cache_key = path.stem
    trade_date = str(params.get("trade_date")) if params.get("trade_date") else None
    cached = None if refresh else read_market_cache("tushare", api_name, cache_key, path, dtype=dtype)
    if cached is not None and not cached.empty:
        return cached
    frame = client.query(api_name, params=params, fields=fields)
    write_market_cache("tushare", api_name, cache_key, path, frame, trade_date=trade_date)
    return frame


def load_trade_dates(client: TushareProxyClient, lookback: int, end_date: str, refresh: bool) -> list[str]:
    start = (pd.to_datetime(end_date) - pd.Timedelta(days=max(lookback * 3, 420))).strftime("%Y%m%d")
    fields = "cal_date,is_open,pretrade_date"
    cached = None if refresh else read_market_cache("tushare", "trade_cal", "trade_cal", TRADE_CAL_PATH, dtype={"cal_date": str})
    needs_fetch = cached is None or cached.empty
    if cached is not None and not cached.empty:
        cached["cal_date"] = cached["cal_date"].astype(str)
        min_cached = cached["cal_date"].min()
        max_cached = cached["cal_date"].max()
        needs_fetch = min_cached > start or max_cached < end_date
    if needs_fetch:
        fresh = client.query(
            "trade_cal",
            params={"exchange": "SSE", "start_date": start, "end_date": end_date},
            fields=fields,
        )
        if cached is not None and not cached.empty:
            frame = pd.concat([cached, fresh], ignore_index=True)
            frame["cal_date"] = frame["cal_date"].astype(str)
            frame = frame.drop_duplicates("cal_date", keep="last").sort_values("cal_date")
        else:
            frame = fresh
        write_market_cache("tushare", "trade_cal", "trade_cal", TRADE_CAL_PATH, frame)
    else:
        frame = cached
    frame["cal_date"] = frame["cal_date"].astype(str)
    frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(int)
    dates = sorted(frame.loc[frame["is_open"] == 1, "cal_date"].unique())
    return dates[-lookback:]


def load_stock_basic(client: TushareProxyClient, refresh: bool) -> pd.DataFrame:
    frame = cache_query(
        client,
        "stock_basic",
        STOCK_BASIC_PATH,
        {"exchange": "", "list_status": "L"},
        "ts_code,symbol,name,area,industry,market,list_date",
        refresh=refresh,
        dtype={"ts_code": str, "symbol": str, "list_date": str},
    )
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["board"] = frame.apply(lambda row: classify_board(row["ts_code"], row.get("market")), axis=1)
    frame["list_date_dt"] = pd.to_datetime(frame["list_date"], errors="coerce")
    return frame


def load_daily_data(client: TushareProxyClient, dates: list[str], refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_frames: list[pd.DataFrame] = []
    basic_frames: list[pd.DataFrame] = []
    daily_fields = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    basic_fields = "ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv"
    for index, trade_date in enumerate(dates, start=1):
        daily_path = DAILY_DIR / f"{trade_date}.csv"
        basic_path = DAILY_BASIC_DIR / f"{trade_date}.csv"
        daily = cache_query(
            client,
            "daily",
            daily_path,
            {"trade_date": trade_date},
            daily_fields,
            refresh=refresh,
            dtype={"ts_code": str, "trade_date": str},
        )
        daily_basic = cache_query(
            client,
            "daily_basic",
            basic_path,
            {"trade_date": trade_date},
            basic_fields,
            refresh=refresh,
            dtype={"ts_code": str, "trade_date": str},
        )
        if daily.empty or not {"ts_code", "trade_date", "close"}.issubset(daily.columns):
            print(f"warn skip {trade_date}: TuShare daily not available yet")
            continue
        if daily_basic.empty or not {"ts_code", "trade_date"}.issubset(daily_basic.columns):
            daily_basic = pd.DataFrame(columns=basic_fields.split(","))
        daily_frames.append(daily)
        basic_frames.append(daily_basic)
        if index % 10 == 0 or index == len(dates):
            print(f"loaded TuShare dates {index}/{len(dates)} latest={trade_date}")
    if not daily_frames:
        raise SystemExit("No valid TuShare daily data available for the selected dates.")
    return pd.concat(daily_frames, ignore_index=True), pd.concat(basic_frames, ignore_index=True)


def load_fundamentals(
    client: TushareProxyClient,
    symbols: list[str],
    start_date: str,
    end_date: str,
    refresh: bool,
    sleep: float,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    fields = "ts_code,end_date,roe_dt,grossprofit_margin,netprofit_yoy,or_yoy,debt_to_assets,ocfps"
    for index, ts_code in enumerate(symbols, start=1):
        path = FUNDAMENTAL_DIR / f"{ts_code.replace('.', '_')}.csv"
        cache_key = path.stem
        cached = None if refresh else read_market_cache(
            "tushare",
            "fina_indicator",
            cache_key,
            path,
            dtype={"ts_code": str, "end_date": str},
        )
        if cached is None:
            try:
                cached = client.query(
                    "fina_indicator",
                    params={"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                    fields=fields,
                    retries=1,
                )
                write_market_cache("tushare", "fina_indicator", cache_key, path, cached)
                time.sleep(sleep)
            except Exception as exc:
                print(f"warn finance {ts_code}: {exc}")
                cached = pd.DataFrame(columns=fields.split(","))
        if not cached.empty:
            frames.append(cached)
        if index % 50 == 0 or index == len(symbols):
            print(f"loaded finance {index}/{len(symbols)}")
    if not frames:
        return pd.DataFrame(columns=fields.split(","))
    finance = pd.concat(frames, ignore_index=True)
    finance["end_date"] = finance["end_date"].astype(str)
    return finance.sort_values("end_date").groupby("ts_code", as_index=False).tail(1)


def add_history_features(daily: pd.DataFrame, daily_basic: pd.DataFrame, stock_basic: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily_basic = daily_basic.copy()
    for frame in [daily, daily_basic]:
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["trade_date"] = frame["trade_date"].astype(str)

    numeric_daily = ["open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]
    numeric_basic = ["turnover_rate", "volume_ratio", "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv"]
    for column in numeric_daily:
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    for column in numeric_basic:
        daily_basic[column] = pd.to_numeric(daily_basic[column], errors="coerce")

    merged = daily.merge(daily_basic, how="left", on=["ts_code", "trade_date"])
    merged = merged.merge(
        stock_basic[
            ["ts_code", "symbol", "name", "area", "industry", "market", "board", "list_date_dt"]
        ],
        how="left",
        on="ts_code",
    )
    merged["symbol"] = merged["symbol"].fillna(merged["ts_code"].apply(normalize_symbol)).astype(str).str.zfill(6)
    merged["name"] = merged["name"].fillna(merged["symbol"])
    merged["trade_date_dt"] = pd.to_datetime(merged["trade_date"], format="%Y%m%d", errors="coerce")
    merged = merged.sort_values(["ts_code", "trade_date_dt"])
    grouped = merged.groupby("ts_code", group_keys=False)
    merged["return_1d"] = grouped["close"].pct_change()
    merged["momentum_5d"] = grouped["close"].pct_change(5)
    merged["momentum_20d"] = grouped["close"].pct_change(20)
    merged["momentum_60d"] = grouped["close"].pct_change(60)
    merged["ma_20"] = grouped["close"].transform(lambda item: item.rolling(20, min_periods=15).mean())
    merged["ma_60"] = grouped["close"].transform(lambda item: item.rolling(60, min_periods=40).mean())
    merged["close_vs_ma20"] = merged["close"] / merged["ma_20"] - 1
    merged["close_vs_ma60"] = merged["close"] / merged["ma_60"] - 1
    merged["high_20d"] = grouped["close"].transform(lambda item: item.rolling(20, min_periods=15).max())
    merged["high_60d"] = grouped["close"].transform(lambda item: item.rolling(60, min_periods=40).max())
    merged["close_to_high_60d"] = merged["close"] / merged["high_60d"]
    merged["amount_yi"] = merged["amount"] / 100000
    merged["amount_ma5_yi"] = grouped["amount_yi"].transform(lambda item: item.rolling(5, min_periods=3).mean())
    merged["amount_ma20_yi"] = grouped["amount_yi"].transform(lambda item: item.rolling(20, min_periods=10).mean())
    merged["amount_ratio_5_20"] = merged["amount_ma5_yi"] / merged["amount_ma20_yi"]
    merged["volatility_20d"] = grouped["return_1d"].transform(lambda item: item.rolling(20, min_periods=10).std() * math.sqrt(252))
    merged["history_count"] = grouped.cumcount() + 1
    merged["total_mv_yi"] = merged["total_mv"] / 10000
    merged["circ_mv_yi"] = merged["circ_mv"] / 10000
    merged["list_age_days"] = (merged["trade_date_dt"] - merged["list_date_dt"]).dt.days
    return merged


def build_latest_scores(
    history: pd.DataFrame,
    finance: pd.DataFrame,
    markets: set[str],
    min_amount_yi: float,
    buy_score_threshold: float,
    min_pct_change: float,
    max_pct_change: float,
    min_close_position_pct: float,
    max_amplitude_pct: float,
    target_weight: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    latest_date = str(history["trade_date"].max())
    latest = history[history["trade_date"] == latest_date].copy()
    finance = finance.copy()
    if not finance.empty:
        finance["ts_code"] = finance["ts_code"].astype(str)
        for column in ["roe_dt", "grossprofit_margin", "netprofit_yoy", "or_yoy", "debt_to_assets", "ocfps"]:
            finance[column] = pd.to_numeric(finance[column], errors="coerce")
        latest = latest.merge(finance, how="left", on="ts_code", suffixes=("", "_finance"))
    else:
        for column in ["roe_dt", "grossprofit_margin", "netprofit_yoy", "or_yoy", "debt_to_assets", "ocfps", "end_date"]:
            latest[column] = pd.NA

    latest = latest[latest["board"].isin(markets)].copy()
    latest = latest[latest["close"] > 2].copy()
    latest = latest[~latest["name"].astype(str).str.contains("ST|退", regex=True, na=False)].copy()
    latest = latest[latest["amount_ma20_yi"].fillna(latest["amount_yi"]) >= min_amount_yi].copy()
    if latest.empty:
        return latest, latest

    latest["momentum_score"] = sweet_spot(latest["momentum_20d"], 0.02, 0.18, 0.55)
    latest["medium_momentum_score"] = sweet_spot(latest["momentum_60d"], 0.04, 0.35, 0.90)
    latest["trend20_score"] = clipped(latest["close_vs_ma20"], -0.03, 0.22)
    latest["trend60_score"] = clipped(latest["close_vs_ma60"], -0.08, 0.45)
    latest["volume_confirm_score"] = sweet_spot(latest["amount_ratio_5_20"], 0.75, 1.55, 3.80)
    latest["breakout_quality_score"] = clipped(latest["close_to_high_60d"], 0.78, 1.0)
    for column in [
        "momentum_score",
        "medium_momentum_score",
        "trend20_score",
        "trend60_score",
        "volume_confirm_score",
        "breakout_quality_score",
    ]:
        latest[column] = latest[column].fillna(0.5)

    latest["alpha_score"] = (
        100
        * (
            0.25 * latest["momentum_score"]
            + 0.15 * latest["medium_momentum_score"]
            + 0.20 * latest["trend20_score"]
            + 0.15 * latest["trend60_score"]
            + 0.15 * latest["volume_confirm_score"]
            + 0.10 * latest["breakout_quality_score"]
        )
    ).round(2)

    latest["profitability_score"] = percentile(latest, "roe_dt")
    latest["profit_growth_score"] = percentile(latest, "netprofit_yoy")
    latest["revenue_growth_score"] = percentile(latest, "or_yoy")
    latest["margin_score"] = percentile(latest, "grossprofit_margin")
    latest["cashflow_score"] = percentile(latest, "ocfps")
    latest["leverage_score"] = percentile(latest, "debt_to_assets", higher_is_better=False)
    latest["fundamental_quality_score"] = (
        100
        * (
            0.24 * latest["profitability_score"]
            + 0.22 * latest["profit_growth_score"]
            + 0.18 * latest["revenue_growth_score"]
            + 0.14 * latest["margin_score"]
            + 0.12 * latest["cashflow_score"]
            + 0.10 * latest["leverage_score"]
        )
    ).round(2)

    latest["pe_score"] = valid_low_percentile(latest, "pe_ttm", low=0, high=100)
    latest["pb_score"] = valid_low_percentile(latest, "pb", low=0, high=20)
    latest["ps_score"] = valid_low_percentile(latest, "ps_ttm", low=0, high=80)
    latest["dividend_score"] = percentile(latest, "dv_ttm")
    latest["valuation_sanity_score"] = (
        100
        * (
            0.35 * latest["pe_score"]
            + 0.25 * latest["pb_score"]
            + 0.20 * latest["ps_score"]
            + 0.20 * latest["dividend_score"]
        )
    ).round(2)

    latest["liquidity_score"] = percentile(latest, "amount_ma20_yi")
    latest["latest_liquidity_score"] = percentile(latest, "amount_yi")
    latest["turnover_quality_score"] = sweet_spot(latest["turnover_rate"], 0.6, 4.5, 14.0)
    latest["capacity_score"] = percentile(latest, "circ_mv_yi")
    latest["liquidity_capacity_score"] = (
        100
        * (
            0.40 * latest["liquidity_score"]
            + 0.25 * latest["latest_liquidity_score"]
            + 0.20 * latest["turnover_quality_score"]
            + 0.15 * latest["capacity_score"]
        )
    ).round(2)
    latest["intraday_position_pct"] = (
        (latest["close"] - latest["low"]) / (latest["high"] - latest["low"]).replace(0, pd.NA) * 100
    ).fillna(50).round(1)
    latest["amplitude_pct_display"] = ((latest["high"] - latest["low"]) / latest["pre_close"] * 100).round(2)
    latest["gap_pct_display"] = ((latest["open"] / latest["pre_close"] - 1) * 100).round(2)

    latest["stability_score"] = percentile(latest, "volatility_20d", higher_is_better=False)
    latest["drawdown_control_score"] = clipped(latest["close_to_high_60d"], 0.72, 1.0)
    latest["drawdown_control_score"] = latest["drawdown_control_score"].fillna(0.5)
    latest["tradability_score"] = 1.0
    latest.loc[latest["pct_chg"].abs() >= 9.7, "tradability_score"] = 0.35
    latest.loc[latest["pct_chg"].abs() >= 19.0, "tradability_score"] = 0.20
    latest.loc[latest["amount_ma20_yi"] < min_amount_yi * 1.5, "tradability_score"] *= 0.75
    latest["new_stock_score"] = (latest["list_age_days"].fillna(9999) >= 180).map({True: 1.0, False: 0.45})
    latest["risk_control_score"] = (
        100
        * (
            0.30 * latest["stability_score"]
            + 0.25 * latest["drawdown_control_score"]
            + 0.25 * latest["tradability_score"]
            + 0.10 * latest["new_stock_score"]
            + 0.10 * latest["leverage_score"]
        )
    ).round(2)
    latest["reversal_risk_score"] = (latest["drawdown_control_score"] * latest["tradability_score"]).clip(0, 1)

    finance_cols = ["roe_dt", "netprofit_yoy", "or_yoy", "debt_to_assets", "ocfps"]
    latest["financial_data_score"] = latest[finance_cols].notna().mean(axis=1)
    latest["history_data_score"] = (latest["history_count"] / 80).clip(0, 1)
    latest["market_data_score"] = latest[["pe_ttm", "pb", "turnover_rate", "circ_mv_yi"]].notna().mean(axis=1)
    latest["data_completeness"] = (
        0.45 * latest["history_data_score"]
        + 0.25 * latest["market_data_score"]
        + 0.30 * latest["financial_data_score"]
    ).round(3)

    latest["crowding_penalty"] = (
        10 * clipped(latest["pct_chg"], 7.5, 10.0).fillna(0)
        + 7 * clipped(latest["turnover_rate"], 10.0, 25.0).fillna(0)
        + 6 * clipped(latest["volume_ratio"], 2.5, 5.0).fillna(0)
        + 5 * clipped(latest["close_vs_ma60"], 0.35, 0.75).fillna(0)
        + 4 * (1 - latest["valuation_sanity_score"] / 100)
    ).clip(0, 30).round(2)

    latest["raw_institutional_score"] = (
        (
            0.30 * latest["alpha_score"]
            + 0.22 * latest["fundamental_quality_score"]
            + 0.14 * latest["valuation_sanity_score"]
            + 0.19 * latest["liquidity_capacity_score"]
            + 0.15 * latest["risk_control_score"]
            - latest["crowding_penalty"]
        )
        * (0.90 + 0.10 * latest["data_completeness"])
    ).round(2)
    latest["score_rank"] = percentile(latest, "raw_institutional_score")
    latest["price_factor_score"] = (
        0.70 * latest["raw_institutional_score"]
        + 35 * latest["score_rank"]
    ).clip(0, 100).round(2)

    def flags(row: pd.Series) -> str:
        items: list[str] = []
        if row["history_count"] < 80:
            items.append("insufficient_history")
        if row["financial_data_score"] < 0.5:
            items.append("financial_data_sparse")
        if pd.notna(row.get("netprofit_yoy")) and row["netprofit_yoy"] < -20:
            items.append("negative_profit_growth")
        if pd.notna(row.get("debt_to_assets")) and row["debt_to_assets"] > 75:
            items.append("high_debt_ratio")
        if row["valuation_sanity_score"] < 35:
            items.append("valuation_expensive_or_invalid")
        if row["crowding_penalty"] >= 12:
            items.append("crowding_or_chase_risk")
        if row["pct_chg"] >= max_pct_change:
            items.append("limit_up_or_hard_to_buy")
        if row["pct_chg"] < min_pct_change:
            items.append("below_min_momentum")
        if row["pct_chg"] <= -9.7:
            items.append("limit_down_or_stress")
        if row["intraday_position_pct"] < min_close_position_pct:
            items.append("weak_close_position")
        if row["amplitude_pct_display"] > max_amplitude_pct:
            items.append("wide_intraday_amplitude")
        if row["amount_ma20_yi"] < min_amount_yi * 1.5:
            items.append("liquidity_watch")
        if row["alpha_score"] < 55:
            items.append("weak_price_alpha")
        elif row["alpha_score"] < 62:
            items.append("alpha_buy_gate")
        if row["liquidity_capacity_score"] < 55:
            items.append("liquidity_buy_gate")
        if row["risk_control_score"] < 55:
            items.append("risk_score_gate")
        if row["data_completeness"] < 0.55:
            items.append("data_completeness_gate")
        if row["list_age_days"] < 180:
            items.append("new_stock_watch")
        return "|".join(items)

    latest["risk_flags"] = latest.apply(flags, axis=1)
    penalty_rules = {
        "below_min_momentum": 5.0,
        "weak_close_position": 6.0,
        "wide_intraday_amplitude": 6.0,
        "limit_up_or_hard_to_buy": 14.0,
        "limit_down_or_stress": 14.0,
        "new_stock_watch": 8.0,
        "risk_score_gate": 6.0,
        "weak_price_alpha": 5.0,
        "alpha_buy_gate": 4.0,
        "liquidity_buy_gate": 4.0,
        "data_completeness_gate": 5.0,
        "financial_data_sparse": 4.0,
        "valuation_expensive_or_invalid": 3.0,
        "negative_profit_growth": 5.0,
        "high_debt_ratio": 3.0,
        "liquidity_watch": 3.0,
        "crowding_or_chase_risk": 4.0,
    }
    latest["gate_penalty_score"] = latest["risk_flags"].apply(
        lambda value: min(
            18.0,
            sum(penalty for flag, penalty in penalty_rules.items() if flag in str(value).split("|")),
        )
    )
    latest["price_factor_score"] = (latest["price_factor_score"] - latest["gate_penalty_score"]).clip(0, 100).round(2)
    latest["action"] = "avoid"
    latest.loc[latest["price_factor_score"] >= 60, "action"] = "watch"
    latest.loc[
        (latest["price_factor_score"] >= buy_score_threshold)
        & (latest["alpha_score"] >= 62)
        & (latest["liquidity_capacity_score"] >= 55)
        & (latest["risk_control_score"] >= 55)
        & (latest["data_completeness"] >= 0.55)
        & (latest["pct_chg"] >= min_pct_change)
        & (latest["pct_chg"] < max_pct_change)
        & (latest["intraday_position_pct"] >= min_close_position_pct)
        & (latest["amplitude_pct_display"] <= max_amplitude_pct)
        & ~latest["risk_flags"].str.contains(
            "limit_up_or_hard_to_buy|limit_down_or_stress|new_stock_watch|weak_close_position|wide_intraday_amplitude",
            na=False,
        ),
        "action",
    ] = "buy"

    latest["confidence"] = "low"
    latest.loc[(latest["price_factor_score"] >= 68) & (latest["data_completeness"] >= 0.55), "confidence"] = "medium"
    latest.loc[
        (latest["action"] == "buy")
        & (latest["price_factor_score"] >= buy_score_threshold + 5)
        & (latest["fundamental_quality_score"] >= 58)
        & (latest["risk_control_score"] >= 65)
        & (latest["data_completeness"] >= 0.75),
        "confidence",
    ] = "high"
    latest["target_weight"] = 0.0
    latest.loc[latest["action"] == "buy", "target_weight"] = target_weight
    latest.loc[(latest["action"] == "buy") & (latest["confidence"] == "medium"), "target_weight"] = target_weight * 0.75
    latest.loc[(latest["action"] == "buy") & (latest["confidence"] == "low"), "target_weight"] = target_weight * 0.45
    latest["target_weight"] = latest["target_weight"].round(4)
    latest["recommendation_tier"] = latest["action"].map(
        {"buy": "core_candidate", "watch": "watch_candidate", "avoid": "avoid"}
    )
    latest.loc[latest["confidence"] == "high", "recommendation_tier"] = "high_conviction"
    latest["model_version"] = MODEL_VERSION
    latest["trade_date"] = pd.to_datetime(latest["trade_date"], format="%Y%m%d").dt.date.astype(str)
    latest["source_time"] = "tushare_proxy"
    latest["review_required"] = True
    latest["reason"] = latest.apply(
        lambda row: (
            f"TuShare机构评分 {row['price_factor_score']:.1f}；"
            f"价格Alpha {row['alpha_score']:.1f} / 基本面 {row['fundamental_quality_score']:.1f} / "
            f"估值 {row['valuation_sanity_score']:.1f} / 流动性 {row['liquidity_capacity_score']:.1f} / "
            f"风控 {row['risk_control_score']:.1f}；"
            f"20日动量 {row['momentum_20d']:.2%}；20日成交 {row['amount_ma20_yi']:.2f}亿；"
            f"PE_TTM {row['pe_ttm'] if pd.notna(row['pe_ttm']) else '--'}；"
            f"ROE {row['roe_dt'] if pd.notna(row['roe_dt']) else '--'}；"
            f"数据完整度 {row['data_completeness']:.0%}；门禁扣分 {row['gate_penalty_score']:.1f}；"
            f"{'核心候选' if row['action'] == 'buy' else '观察候选' if row['action'] == 'watch' else '暂不入池'}"
        ),
        axis=1,
    )

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
        "pct_chg",
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
        "trend20_score",
        "stability_score",
        "tradability_score",
        "reversal_risk_score",
        "board",
        "source_time",
        "industry",
        "market",
        "fundamental_quality_score",
        "valuation_sanity_score",
        "financial_data_score",
        "data_completeness",
        "raw_institutional_score",
        "score_rank",
        "gate_penalty_score",
        "amount_ma20_yi",
        "turnover_rate",
        "volume_ratio",
        "pe_ttm",
        "pb",
        "ps_ttm",
        "dv_ttm",
        "total_mv_yi",
        "roe_dt",
        "grossprofit_margin",
        "netprofit_yoy",
        "or_yoy",
        "debt_to_assets",
        "ocfps",
        "end_date",
    ]
    latest = latest.rename(columns={"pct_chg": "pct_change"})
    output_columns = ["pct_change" if item == "pct_chg" else item for item in output_columns]
    latest["pool_rank"] = range(1, len(latest) + 1)
    all_scored = latest.sort_values("price_factor_score", ascending=False).copy()
    all_scored["pool_rank"] = range(1, len(all_scored) + 1)

    ranked = all_scored[all_scored["action"].isin(["buy", "watch"])].copy()
    ranked["action_sort"] = ranked["action"].map({"buy": 0, "watch": 1}).fillna(2)
    ranked["confidence_sort"] = ranked["confidence"].map({"high": 0, "medium": 1, "low": 2}).fillna(3)
    ranked = ranked.sort_values(
        ["action_sort", "confidence_sort", "price_factor_score", "risk_control_score"],
        ascending=[True, True, False, False],
    )
    selected_rows: list[pd.Series] = []
    board_counts: dict[str, int] = {}
    industry_counts: dict[str, int] = {}
    board_cap = max(6, round(30 * 0.45))
    industry_cap = 4
    for _, row in ranked.iterrows():
        board = str(row.get("board") or "unknown")
        industry = str(row.get("industry") or "未分类")
        if board_counts.get(board, 0) >= board_cap:
            continue
        if industry_counts.get(industry, 0) >= industry_cap:
            continue
        selected_rows.append(row)
        board_counts[board] = board_counts.get(board, 0) + 1
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected_rows) >= 30:
            break
    pool = pd.DataFrame(selected_rows)
    if pool.empty:
        pool = ranked.head(30).copy()
    pool["pool_rank"] = range(1, len(pool) + 1)
    return all_scored[output_columns], pool[output_columns]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TuShare institutional A-share pool.")
    parser.add_argument("--email", help="Account email with TuShare data-source config.")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--lookback-trade-days", type=int, default=90)
    parser.add_argument("--finance-limit", type=int, default=800, help="Top liquidity symbols for financial enhancement; 0 means all.")
    parser.add_argument("--min-amount-yi", type=float, default=3.0)
    parser.add_argument("--buy-score-threshold", type=float, default=78.0)
    parser.add_argument("--min-pct-change", type=float, default=1.0)
    parser.add_argument("--max-pct-change", type=float, default=9.7)
    parser.add_argument("--min-close-position-pct", type=float, default=55.0)
    parser.add_argument("--max-amplitude-pct", type=float, default=12.0)
    parser.add_argument("--target-weight", type=float, default=0.05)
    parser.add_argument("--markets", default="main,chinext,star", help="Comma separated: main,chinext,star,beijing")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--finance-refresh", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    markets = {item.strip() for item in args.markets.split(",") if item.strip()}
    credentials = credentials_from_db(args.email)
    client = TushareProxyClient(credentials, timeout=45, sleep=args.sleep)
    print(f"TuShare proxy source: {credentials.base_url}; token={credentials.token_mask or 'configured'}")

    dates = load_trade_dates(client, args.lookback_trade_days, args.end_date, refresh=args.refresh)
    if len(dates) < 30:
        raise SystemExit("Not enough TuShare trade dates to build institutional factors.")
    stock_basic = load_stock_basic(client, refresh=args.refresh)
    daily, daily_basic = load_daily_data(client, dates, refresh=args.refresh)
    history = add_history_features(daily, daily_basic, stock_basic)

    latest_date = str(history["trade_date"].max())
    latest_for_finance = history[history["trade_date"] == latest_date].copy()
    latest_for_finance = latest_for_finance[latest_for_finance["amount_ma20_yi"] >= args.min_amount_yi]
    latest_for_finance = latest_for_finance.sort_values("amount_ma20_yi", ascending=False)
    finance_symbols = latest_for_finance["ts_code"].dropna().astype(str).tolist()
    if args.finance_limit > 0:
        finance_symbols = finance_symbols[: args.finance_limit]
    finance_start = (pd.to_datetime(args.end_date) - pd.Timedelta(days=900)).strftime("%Y%m%d")
    finance = load_fundamentals(
        client,
        finance_symbols,
        start_date=finance_start,
        end_date=args.end_date,
        refresh=args.finance_refresh,
        sleep=args.sleep,
    )
    all_scored, pool = build_latest_scores(
        history,
        finance,
        markets=markets,
        min_amount_yi=args.min_amount_yi,
        buy_score_threshold=args.buy_score_threshold,
        min_pct_change=args.min_pct_change,
        max_pct_change=args.max_pct_change,
        min_close_position_pct=args.min_close_position_pct,
        max_amplitude_pct=args.max_amplitude_pct,
        target_weight=args.target_weight,
    )
    if args.limit < len(pool):
        pool = pool.head(args.limit).copy()
        pool["pool_rank"] = range(1, len(pool) + 1)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    all_scored.to_csv(SCORED_UNIVERSE_PATH, index=False, encoding="utf-8-sig")
    all_scored.to_csv(INSTITUTIONAL_FACTORS_PATH, index=False, encoding="utf-8-sig")
    pool.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)} rows={len(pool)}")
    print(f"wrote {SCORED_UNIVERSE_PATH.relative_to(PROJECT_ROOT)} rows={len(all_scored)}")
    print(f"wrote {INSTITUTIONAL_FACTORS_PATH.relative_to(PROJECT_ROOT)} rows={len(all_scored)}")
    print("\nTuShare institutional pool")
    print(
        pool[
            [
                "pool_rank",
                "trade_date",
                "symbol",
                "name",
                "industry",
                "price_factor_score",
                "action",
                "confidence",
                "fundamental_quality_score",
                "valuation_sanity_score",
                "risk_flags",
            ]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
