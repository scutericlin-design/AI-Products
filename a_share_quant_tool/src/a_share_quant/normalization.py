from __future__ import annotations

import pandas as pd


class SchemaError(ValueError):
    pass


def first_board_candidates(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize AKShare limit-up data to the strategy's canonical schema.

    Units are deliberately not inferred: callers must state float-market-cap and
    seal-amount units before a row can pass the trading strategy.
    """
    required = {"代码", "名称", "首次封板时间", "换手率", "流通市值", "封板资金"}
    missing = required - set(raw.columns)
    if missing:
        raise SchemaError(f"涨停池字段变化或不完整: {sorted(missing)}")
    frame = raw.copy()
    frame["code"] = frame["代码"].astype(str).str.zfill(6)
    frame["name"] = frame["名称"].astype(str)
    frame["seal_time"] = frame["首次封板时间"].astype(str)
    frame["turnover_rate"] = pd.to_numeric(frame["换手率"], errors="coerce")
    frame["float_market_cap"] = pd.to_numeric(frame["流通市值"], errors="coerce")
    frame["seal_amount"] = pd.to_numeric(frame["封板资金"], errors="coerce")
    frame["is_one_word"] = frame.get("一字板", False).astype(bool) if "一字板" in frame else False
    return frame.dropna(subset=["turnover_rate", "float_market_cap", "seal_amount"])


def earnings_forecasts(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"股票代码", "股票简称", "业绩变动幅度", "预测数值", "公告日期"}
    missing = required - set(raw.columns)
    if missing:
        raise SchemaError(f"业绩预告字段变化或不完整: {sorted(missing)}")
    frame = raw.copy()
    frame["code"] = frame["股票代码"].astype(str).str.zfill(6)
    frame["name"] = frame["股票简称"].astype(str)
    frame["profit_growth_pct"] = pd.to_numeric(frame["业绩变动幅度"], errors="coerce")
    frame["profit"] = pd.to_numeric(frame["预测数值"], errors="coerce")
    frame["announcement_date"] = frame["公告日期"].astype(str).str.replace("-", "", regex=False)
    # Non-recurring profit share is not present in this endpoint.  A merged
    # financial-statement dataset must populate it; unknown values fail closed.
    frame["non_recurring_ratio"] = pd.NA
    return frame.dropna(subset=["profit_growth_pct", "profit"])
