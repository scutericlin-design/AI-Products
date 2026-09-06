"""Close-only factors and fixed risk policies for an independent research model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import re

import numpy as np
import pandas as pd


BAR_COLUMNS = (
    "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
    "vol", "amount", "adj_factor", "pe_ttm", "pb",
)
FUNDAMENTAL_COLUMNS = (
    "ts_code", "ann_date", "end_date", "roe", "or_yoy", "ocfps", "debt_to_assets",
)
MEMBERSHIP_COLUMNS = ("ts_code", "trade_date")
FACTORS = ("quality", "value", "momentum", "lowvol")
_STOCK = re.compile(r"(?:60\d{4}\.SH|68\d{4}\.SH|00\d{4}\.SZ|30\d{4}\.SZ)\Z")


@dataclass(frozen=True)
class ModelConfig:
    """Fixed, predeclared policies, not a fitted model or an AQR replication.

    Commission and slippage are basis points per side. Cash fees and sell tax
    are also scaled by the replay's cost_multiplier. Weights are fractions.
    """

    variant: str = "balanced"
    max_names: int = 12
    max_weight: float = 0.08
    total_weight: float = 0.80
    retention_rank: int = 24
    rebalance_sessions: int = 5
    min_bars: int = 126
    min_candidates: int = 12
    financial_max_age_days: int = 550
    momentum_sessions: int = 126
    momentum_skip_sessions: int = 5
    volatility_sessions: int = 60
    volatility_floor: float = 0.05
    breadth_ma_sessions: int = 100
    breadth_smoothing_sessions: int = 20
    breadth_low: float = 0.35
    breadth_high: float = 0.65
    trend_min_scale: float = 0.25
    market_volatility_cap: float = 0.15
    correlation_threshold: float = 0.75
    correlation_cluster_size: int = 3
    rebalance_band: float = 0.01
    commission_bps: float = 5.0
    min_commission: float = 5.0
    slippage_bps: float = 10.0

    def __post_init__(self) -> None:
        integers = (
            "max_names", "retention_rank", "rebalance_sessions", "min_bars",
            "min_candidates", "financial_max_age_days", "momentum_sessions",
            "volatility_sessions", "breadth_ma_sessions", "breadth_smoothing_sessions",
            "correlation_cluster_size",
        )
        if any(type(getattr(self, name)) is not int or getattr(self, name) < 1 for name in integers):
            raise ValueError("session counts, ranks and minimums must be positive integers")
        if type(self.momentum_skip_sessions) is not int or not 0 <= self.momentum_skip_sessions < self.momentum_sessions - 1:
            raise ValueError("invalid momentum skip window")
        if self.variant not in {"balanced", "trend_scaled"}:
            raise ValueError("variant must be balanced or trend_scaled")
        if self.retention_rank < self.max_names or self.volatility_sessions < 2:
            raise ValueError("invalid retention rank or volatility window")
        values = [value for value in asdict(self).values() if isinstance(value, float)]
        if not all(np.isfinite(value) for value in values):
            raise ValueError("configuration must be finite")
        if not 0 < self.max_weight <= 1 or not 0 < self.total_weight <= 1:
            raise ValueError("weight limits must be in (0, 1]")
        if not 0 <= self.breadth_low < self.breadth_high <= 1 or not 0 <= self.trend_min_scale <= 1:
            raise ValueError("invalid breadth policy")
        if min(self.volatility_floor, self.market_volatility_cap) <= 0:
            raise ValueError("volatility parameters must be positive")
        if not -1 <= self.correlation_threshold <= 1:
            raise ValueError("correlation_threshold must be in [-1, 1]")
        if not 0 <= self.rebalance_band < 1:
            raise ValueError("rebalance_band must be in [0, 1)")
        if min(self.commission_bps, self.min_commission, self.slippage_bps) < 0:
            raise ValueError("costs must be nonnegative")

    @property
    def required_bars(self) -> int:
        return max(
            self.min_bars, self.momentum_sessions, self.volatility_sessions + 1,
            self.breadth_ma_sessions + self.breadth_smoothing_sessions - 1,
        )


def _date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("dates must be YYYYMMDD strings")
    datetime.strptime(value, "%Y%m%d")
    return value


def _frame(frame: pd.DataFrame, columns: tuple[str, ...], dates: tuple[str, ...], cutoff: str) -> pd.DataFrame:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    result = frame.loc[:, list(columns)].copy()
    for column in dates:
        result[column] = result[column].astype(str)
    result = result.loc[result[dates[0]] <= cutoff].copy()
    for column in dates:
        for value in result[column].unique():
            _date(value)
    result["ts_code"] = result["ts_code"].astype(str).str.strip().str.upper()
    return result


def _positive(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)) and float(value) > 0)
    except (ValueError, TypeError):
        return False


def _capped_inverse_vol(volatilities: np.ndarray, budget: float, cap: float, floor: float) -> np.ndarray:
    weights = np.zeros(len(volatilities), dtype=float)
    remaining = min(budget, len(weights) * cap)
    active = np.arange(len(weights))
    while len(active) and remaining > 1e-12:
        inverse = 1.0 / np.maximum(volatilities[active], floor)
        proposed = remaining * inverse / inverse.sum()
        saturated = proposed >= cap
        if not saturated.any():
            weights[active] = proposed
            break
        weights[active[saturated]] = cap
        remaining -= cap * int(saturated.sum())
        active = active[~saturated]
    return weights


def build_targets(
    bars: pd.DataFrame,
    fundamentals: pd.DataFrame,
    memberships: pd.DataFrame,
    as_of: str,
    current_weights: dict[str, float],
    config: ModelConfig,
    *,
    prepared: PreparedModelData | None = None,
) -> dict:
    """Return targets, all ranked scores, weights and exclusion diagnostics.

    Memberships are complete dated snapshots, not additions/deletions. Financial
    freshness uses report end date; announcements on as_of are never visible.
    Targets are desired total weights, not orders. Callers control scheduling.
    """
    as_of = _date(as_of)
    if any(not np.isfinite(weight) or weight < 0 for weight in current_weights.values()):
        raise ValueError("current_weights must be finite and nonnegative")
    if prepared is None:
        history = _frame(bars, BAR_COLUMNS, ("trade_date",), as_of)
        financial = _frame(fundamentals, FUNDAMENTAL_COLUMNS, ("ann_date", "end_date"), as_of)
        membership = _frame(memberships, MEMBERSHIP_COLUMNS, ("trade_date",), as_of)
        financial = financial.loc[(financial.ann_date < as_of) & (financial.end_date <= financial.ann_date)].copy()
    else:
        prepared.check_config(config)
        membership = prepared.memberships.loc[prepared.memberships.trade_date <= as_of]
    snapshot = str(membership.trade_date.max()) if len(membership) else None
    symbols = sorted(membership.loc[membership.trade_date == snapshot, "ts_code"].unique())
    exclusions: dict[str, str] = {}
    diagnostics = {
        "as_of": as_of, "membership_date": snapshot, "universe_count": len(symbols),
        "required_bars": config.required_bars, "exclusions": exclusions,
        "financial_timing": "ann_date < as_of; freshness measured from end_date",
        "industry_neutral": False, "configuration": asdict(config),
        "financial_vintages": "source financial values; original provider vintages not independently verified",
        "quality_definition": "equal ROE rank, cashflow sign (negative=0, zero=0.5, positive=1), inverse debt rank; limited cashflow proxy, not CFO/assets",
        "correlation_policy": "trailing completed-return correlation cluster, not industry neutrality",
        "membership_policy": "latest complete known snapshot, not full-market coverage",
    }
    result = {"as_of": as_of, "status": "blocked", "targets": [], "weights": {}, "scores": [], "diagnostics": diagnostics}
    if not symbols:
        diagnostics["reason"] = "missing_membership_snapshot"
        return result
    records = []
    adjusted_histories = {}
    if prepared is None:
        grouped_bars = {symbol: group for symbol, group in history.groupby("ts_code", sort=False)}
        grouped_financial = {symbol: group for symbol, group in financial.groupby("ts_code", sort=False)}
        observed_sessions = sorted(history.trade_date.unique())[-config.required_bars:]
    else:
        records, cached_exclusions = prepared.snapshot(as_of, symbols)
        exclusions.update(cached_exclusions)
    for symbol in symbols if prepared is None else []:
        if not _STOCK.fullmatch(symbol):
            exclusions[symbol] = "not_supported_a_share_stock"
            continue
        rows = grouped_bars.get(symbol)
        if rows is None or len(rows) < config.required_bars:
            exclusions[symbol] = "insufficient_bars"
            continue
        rows = rows.sort_values("trade_date", kind="stable")
        if rows.trade_date.duplicated().any():
            exclusions[symbol] = "duplicate_bar"
            continue
        if rows.iloc[-1].trade_date != as_of:
            exclusions[symbol] = "stale_bar"
            continue
        rows = rows.tail(config.required_bars).copy()
        if rows.trade_date.tolist() != observed_sessions:
            exclusions[symbol] = "missing_required_session"
            continue
        for column in ("close", "adj_factor", "pe_ttm", "pb"):
            rows[column] = pd.to_numeric(rows[column], errors="coerce")
        values = rows[["close", "adj_factor"]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            exclusions[symbol] = "missing_or_invalid_close_or_adjustment"
            continue
        if not _positive(rows.iloc[-1].pe_ttm) or not _positive(rows.iloc[-1].pb):
            exclusions[symbol] = "nonpositive_or_missing_valuation"
            continue
        reports = grouped_financial.get(symbol)
        if reports is None or reports.empty:
            exclusions[symbol] = "no_strictly_prior_financial"
            continue
        # Latest report first, then its latest available vintage; late corrections
        # to an older report must not replace a newer reporting period.
        reports = reports.sort_values(["end_date", "ann_date"], kind="stable")
        report = reports.iloc[-1]
        same_vintage = reports.loc[(reports.end_date == report.end_date) & (reports.ann_date == report.ann_date)]
        if len(same_vintage.drop_duplicates()) != 1:
            exclusions[symbol] = "ambiguous_financial_vintage"
            continue
        age = (datetime.strptime(as_of, "%Y%m%d") - datetime.strptime(report.end_date, "%Y%m%d")).days
        if age > config.financial_max_age_days:
            exclusions[symbol] = "stale_financial"
            continue
        numbers = pd.to_numeric(report[["roe", "or_yoy", "ocfps", "debt_to_assets"]], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(numbers).all() or not 0 <= numbers[3] <= 100:
            exclusions[symbol] = "missing_or_invalid_financial"
            continue
        adjusted = pd.Series(
            rows.close.to_numpy() * rows.adj_factor.to_numpy(), index=rows.trade_date.to_numpy(), dtype=float,
        )
        if not np.isfinite(adjusted.to_numpy()).all():
            exclusions[symbol] = "invalid_adjusted_price"
            continue
        returns = adjusted.pct_change(fill_method=None).tail(config.volatility_sessions)
        volatility = float(returns.std(ddof=0) * np.sqrt(252))
        records.append({
            "ts_code": symbol, "roe": float(numbers[0]), "or_yoy": float(numbers[1]),
            "ocfps": float(numbers[2]), "debt_to_assets": float(numbers[3]),
            "earnings_yield": 1.0 / float(rows.iloc[-1].pe_ttm),
            "book_yield": 1.0 / float(rows.iloc[-1].pb),
            "momentum": float(adjusted.iloc[-1 - config.momentum_skip_sessions] / adjusted.iloc[-config.momentum_sessions] - 1),
            "volatility": volatility, "ann_date": str(report.ann_date), "end_date": str(report.end_date),
        })
        adjusted_histories[symbol] = adjusted
    diagnostics["eligible_count"] = len(records)
    if len(records) < config.min_candidates:
        diagnostics["reason"] = "insufficient_eligible_candidates"
        return result
    ranked = pd.DataFrame(records)
    def rank(column: str, ascending: bool = True) -> pd.Series:
        return ranked[column].rank(method="average", pct=True, ascending=ascending)
    ranked["cashflow_sign_score"] = (np.sign(ranked.ocfps) + 1) / 2
    ranked["quality"] = (rank("roe") + ranked.cashflow_sign_score + rank("debt_to_assets", False)) / 3
    ranked["value"] = (rank("earnings_yield") + rank("book_yield")) / 2
    ranked["momentum_rank"] = rank("momentum")
    ranked["lowvol"] = rank("volatility", False)
    ranked["score"] = 100 * ranked[["quality", "value", "momentum_rank", "lowvol"]].mean(axis=1)
    ranked = ranked.sort_values(["score", "ts_code"], ascending=[False, True], kind="stable").reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    retained = ranked.loc[(ranked.ts_code.map(current_weights).fillna(0) > 0) & (ranked["rank"] <= config.retention_rank)]
    priority = pd.concat([retained, ranked.loc[~ranked.ts_code.isin(retained.ts_code)]])

    breadth_values, return_series = [], []
    for prices in adjusted_histories.values():
        moving = prices.rolling(config.breadth_ma_sessions).mean()
        breadth_values.append(float((prices > moving).tail(config.breadth_smoothing_sessions).mean()))
        return_series.append(prices.pct_change(fill_method=None).tail(config.volatility_sessions))
    if prepared is None:
        breadth = float(np.mean(breadth_values))
        return_matrix = pd.concat(return_series, axis=1)
        return_matrix.columns = list(adjusted_histories)
    else:
        breadth, return_matrix = prepared.risk_inputs(as_of, ranked.ts_code.tolist(), config)
    market_returns = return_matrix.dropna().mean(axis=1)
    if len(market_returns) < config.volatility_sessions:
        diagnostics["reason"] = "unaligned_market_risk_history"
        return result
    market_vol = float(market_returns.std(ddof=0) * np.sqrt(252))
    breadth_scale = config.trend_min_scale + (1 - config.trend_min_scale) * float(np.clip(
        (breadth - config.breadth_low) / (config.breadth_high - config.breadth_low), 0, 1,
    ))
    vol_scale = min(1.0, config.market_volatility_cap / max(market_vol, 1e-12))
    risk_scale = 1.0 if config.variant == "balanced" else min(breadth_scale, vol_scale)
    selected_ids, selected_symbols, correlation_rejections = [], [], {}
    centered = return_matrix - return_matrix.mean()
    norms = np.sqrt((centered * centered).sum())
    normalized = centered.divide(norms.where(norms > 1e-12))
    for row_index, row in priority.iterrows():
        symbol = row.ts_code
        if len(selected_symbols) >= config.correlation_cluster_size:
            correlations = normalized[selected_symbols].to_numpy().T @ normalized[symbol].to_numpy()
            if not np.isfinite(correlations).all():
                correlation_rejections[symbol] = "undefined_trailing_correlation"
                continue
            if int((correlations > config.correlation_threshold).sum()) >= config.correlation_cluster_size:
                correlation_rejections[symbol] = "correlated_with_selected_cluster"
                continue
        selected_ids.append(row_index)
        selected_symbols.append(symbol)
        if len(selected_ids) >= config.max_names:
            break
    selected = ranked.loc[selected_ids].copy()
    selected["weight"] = _capped_inverse_vol(
        selected.volatility.to_numpy(), config.total_weight * risk_scale, config.max_weight, config.volatility_floor,
    )
    targets = selected.to_dict("records")
    diagnostics.update({
        "retained": retained.loc[retained.ts_code.isin(selected_symbols)].ts_code.tolist(),
        "correlation_rejections": correlation_rejections,
        "target_exposure": float(selected.weight.sum()), "risk_scale": risk_scale,
        "slow_breadth": breadth, "market_volatility": market_vol,
        "risk_policy": "none" if config.variant == "balanced" else "fixed_trailing_eligible_universe_breadth_and_market_vol_cap; not_AQR_replication",
    })
    result.update(status="ok", targets=targets, weights={row["ts_code"]: row["weight"] for row in targets}, scores=ranked.to_dict("records"))
    diagnostics["band_preview_at_close"] = build_weight_deltas(current_weights, result["weights"], config, exposure_limit=config.total_weight * risk_scale)
    return result


def build_weight_deltas(current_weights: dict[str, float], target_weights: dict[str, float], config: ModelConfig,
                        *, risk_reduction: bool = False, exposure_limit: float | None = None) -> dict:
    """Shared decision rule; desired weights remain unchanged by the band.

    Full exits, oversized-name reductions and aggregate risk-cap reductions are
    never suppressed. New positions are not subject to the ongoing-name band.
    """
    limit = config.total_weight if exposure_limit is None else exposure_limit
    cap_reduction = sum(current_weights.values()) > limit + 1e-10
    deltas, suppressed = {}, {}
    for symbol in sorted(set(current_weights) | set(target_weights)):
        current, target = current_weights.get(symbol, 0.0), target_weights.get(symbol, 0.0)
        delta = target - current
        urgent = delta < 0 and (target <= 0 or current > config.max_weight + 1e-10 or risk_reduction or cap_reduction)
        if current > 0 and target > 0 and abs(delta) < config.rebalance_band and not urgent:
            deltas[symbol] = 0.0
            suppressed[symbol] = {"current_weight": current, "target_weight": target, "reason": "within_nav_band"}
        else:
            deltas[symbol] = delta
    return {"deltas": deltas, "suppressed": suppressed, "band": config.rebalance_band}


def _feature_key(config: ModelConfig) -> tuple:
    return (config.required_bars, config.financial_max_age_days, config.momentum_sessions,
            config.momentum_skip_sessions, config.volatility_sessions,
            config.breadth_ma_sessions, config.breadth_smoothing_sessions)


def _input_signature(bars: pd.DataFrame, fundamentals: pd.DataFrame, memberships: pd.DataFrame, end: str) -> str:
    digest = hashlib.sha256()
    for frame, columns, date_column in ((bars, BAR_COLUMNS, "trade_date"),
                                        (fundamentals, FUNDAMENTAL_COLUMNS, "ann_date"),
                                        (memberships, MEMBERSHIP_COLUMNS, "trade_date")):
        subset = frame.loc[frame[date_column].astype(str) <= end, list(columns)]
        digest.update(pd.util.hash_pandas_object(subset, index=False).to_numpy().tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparedModelData:
    """Explicit owned input snapshot; reuse across variants/costs, never mutate.

    Rolling features are computed once per symbol, not once per rebalance.
    Only requested signal cross-sections are retained. Quote matrices replace
    hundreds of thousands of Python per-row dictionaries in replay.
    """

    end: str
    signature: str
    feature_key: tuple
    memberships: pd.DataFrame
    snapshots: dict[str, pd.DataFrame]
    opens: pd.DataFrame
    closes: pd.DataFrame
    adjustments: pd.DataFrame
    presence: pd.DataFrame
    returns: pd.DataFrame

    def check_config(self, config: ModelConfig) -> None:
        if _feature_key(config) != self.feature_key:
            raise ValueError("prepared data feature configuration mismatch")

    def snapshot(self, as_of: str, symbols: list[str]) -> tuple[list[dict], dict]:
        if as_of not in self.snapshots:
            raise ValueError(f"prepared data lacks signal date {as_of}")
        frame = self.snapshots[as_of]
        available, exclusions = [], {}
        for symbol in symbols:
            if not _STOCK.fullmatch(symbol):
                exclusions[symbol] = "not_supported_a_share_stock"
            elif symbol not in frame.index:
                exclusions[symbol] = "insufficient_bars"
            else:
                available.append(symbol)
        selected = frame.loc[available]
        failed = selected.exclusion != ""
        exclusions.update(selected.loc[failed, "exclusion"].to_dict())
        records = selected.loc[~failed].drop(columns=["exclusion", "breadth"]).reset_index().to_dict("records") if available else []
        return records, exclusions

    def risk_inputs(self, as_of: str, symbols: list[str], config: ModelConfig) -> tuple[float, pd.DataFrame]:
        breadth = float(self.snapshots[as_of].loc[symbols, "breadth"].mean())
        returns = self.returns.loc[:as_of, symbols].tail(config.volatility_sessions)
        return breadth, returns


def prepare_model_data(bars: pd.DataFrame, fundamentals: pd.DataFrame, memberships: pd.DataFrame,
                       as_of_dates: list[str], config: ModelConfig, *, end: str | None = None) -> PreparedModelData:
    """Prepare once, pass as ``prepared=...`` to each run_backtest call.

    as_of_dates should be the requested exchange sessions [::rebalance_sessions].
    end must include the final replay mark, not just the final signal date.
    """
    requested = sorted(set(_date(date) for date in as_of_dates))
    if not requested:
        raise ValueError("preparation requires signal dates")
    end = _date(end or requested[-1])
    if requested[-1] > end:
        raise ValueError("signal date exceeds prepared end")
    history = _frame(bars, BAR_COLUMNS, ("trade_date",), end)
    financial = _frame(fundamentals, FUNDAMENTAL_COLUMNS, ("ann_date", "end_date"), end)
    membership = _frame(memberships, MEMBERSHIP_COLUMNS, ("trade_date",), end)
    if history.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate stock/date bars")
    for column in ("open", "close", "adj_factor", "pe_ttm", "pb"):
        history[column] = pd.to_numeric(history[column], errors="coerce")
    dates = sorted(set(history.trade_date) | set(requested))
    columns = sorted(history.ts_code.unique())
    matrices = {}
    for field in ("open", "close", "adj_factor"):
        matrices[field] = history.pivot(index="trade_date", columns="ts_code", values=field).reindex(index=dates, columns=columns)
    presence = history.assign(present=True).pivot(index="trade_date", columns="ts_code", values="present").reindex(index=dates, columns=columns).fillna(False).astype(bool)
    adjusted = matrices["close"] * matrices["adj_factor"]
    valid = np.isfinite(adjusted) & (matrices["close"] > 0) & (matrices["adj_factor"] > 0)
    adjusted = adjusted.where(valid)
    returns = adjusted.pct_change(fill_method=None)
    volatility = returns.rolling(config.volatility_sessions).std(ddof=0) * np.sqrt(252)
    momentum = adjusted.shift(config.momentum_skip_sessions) / adjusted.shift(config.momentum_sessions - 1) - 1
    moving = adjusted.rolling(config.breadth_ma_sessions).mean()
    breadth = (adjusted > moving).rolling(config.breadth_smoothing_sessions).mean()
    complete = valid.rolling(config.required_bars).sum() == config.required_bars
    present_count = presence.rolling(config.required_bars).sum()
    cumulative = presence.cumsum()
    signal_dates = pd.Index(requested)
    signal_timestamps = pd.to_datetime(signal_dates, format="%Y%m%d")
    financial = financial.loc[financial.end_date <= financial.ann_date]
    groups = {symbol: group for symbol, group in financial.groupby("ts_code", sort=False)}
    parts = []
    for symbol, stock in history.groupby("ts_code", sort=False):
        stock = stock.set_index("trade_date").reindex(signal_dates)
        part = pd.DataFrame(index=signal_dates)
        part["ts_code"] = symbol
        part["exclusion"] = ""
        part["momentum"] = momentum.loc[signal_dates, symbol]
        part["volatility"] = volatility.loc[signal_dates, symbol]
        part["breadth"] = breadth.loc[signal_dates, symbol]
        part["earnings_yield"] = 1.0 / stock.pe_ttm
        part["book_yield"] = 1.0 / stock.pb
        events = []
        reports = groups.get(symbol)
        latest = None
        if reports is not None:
            for ann_date, group in reports.sort_values(["ann_date", "end_date"]).groupby("ann_date", sort=True):
                for report_end, vintage in group.groupby("end_date", sort=True):
                    if latest is None or report_end >= latest["end_date"]:
                        latest = vintage.iloc[-1].to_dict()
                        latest["ambiguous"] = len(vintage.drop_duplicates()) > 1
                events.append({**latest, "event_date": ann_date})
        if events:
            event_frame = pd.DataFrame(events)
            indices = np.searchsorted(event_frame.event_date.to_numpy(), signal_dates.to_numpy(), side="left") - 1
            chosen = event_frame.iloc[np.maximum(indices, 0)].astype(object).copy()
            chosen.index = signal_dates
            chosen.loc[indices < 0, :] = None
            for field in ("roe", "or_yoy", "ocfps", "debt_to_assets"):
                part[field] = pd.to_numeric(chosen[field], errors="coerce")
            part["ann_date"] = chosen.ann_date
            part["end_date"] = chosen.end_date
            ages = (signal_timestamps - pd.to_datetime(chosen.end_date, format="%Y%m%d")).dt.days
            bad = ~np.isfinite(part[["roe", "or_yoy", "ocfps", "debt_to_assets"]]).all(axis=1) | ~part.debt_to_assets.between(0, 100)
            part.loc[bad, "exclusion"] = "missing_or_invalid_financial"
            part.loc[ages > config.financial_max_age_days, "exclusion"] = "stale_financial"
            part.loc[chosen.ambiguous.fillna(False).astype(bool), "exclusion"] = "ambiguous_financial_vintage"
            part.loc[indices < 0, "exclusion"] = "no_strictly_prior_financial"
        else:
            for field in ("roe", "or_yoy", "ocfps", "debt_to_assets", "ann_date", "end_date"):
                part[field] = None
            part["exclusion"] = "no_strictly_prior_financial"
        valuation = stock[["pe_ttm", "pb"]]
        bad_value = ~np.isfinite(valuation).all(axis=1) | (valuation <= 0).any(axis=1)
        part.loc[bad_value, "exclusion"] = "nonpositive_or_missing_valuation"
        part.loc[~complete.loc[signal_dates, symbol], "exclusion"] = "missing_or_invalid_close_or_adjustment"
        part.loc[present_count.loc[signal_dates, symbol] < config.required_bars, "exclusion"] = "missing_required_session"
        part.loc[~presence.loc[signal_dates, symbol], "exclusion"] = "stale_bar"
        part.loc[cumulative.loc[signal_dates, symbol] < config.required_bars, "exclusion"] = "insufficient_bars"
        parts.append(part)
    stacked = pd.concat(parts) if parts else pd.DataFrame(columns=["ts_code", "exclusion"])
    snapshots = {date: group.set_index("ts_code") for date, group in stacked.groupby(level=0, sort=False)}
    for date in requested:
        snapshots.setdefault(date, pd.DataFrame(columns=["exclusion"]))
    return PreparedModelData(end, _input_signature(bars, fundamentals, memberships, end), _feature_key(config),
                             membership, snapshots, matrices["open"], matrices["close"], matrices["adj_factor"], presence, returns)
