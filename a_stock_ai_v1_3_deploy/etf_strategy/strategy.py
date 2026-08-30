from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.models import ETFCandidate, ETFDecisionPlan


INDEX_SYMBOLS = ("000300.SH", "000852.SH", "399006.SZ", "000510.SH")
GLOBAL_KEYWORDS = (
    "黄金",
    "白银",
    "原油",
    "油气",
    "豆粕",
    "有色",
    "纳指",
    "标普",
    "日经",
    "德国",
    "沙特",
    "全球",
    "海外",
)


@dataclass(frozen=True)
class WufuETFParameters:
    strategy_id: str = "wufu_etf_local"
    strategy_version: str = "v1.0-local-research"
    lookback_days: int = 25
    ma_days: int = 10
    r_squared_min: float = 0.40
    short_loss_floor: float = -0.03
    target_holdings: int = 1
    normal_target_weight: float = 0.70
    weak_target_weight: float = 0.45
    neutral_target_weight: float = 0.35
    candidate_hysteresis: float = 0.90
    max_annualized_volatility: float = 0.85
    catastrophic_stop_pct: float = 0.08


class WufuETFStrategy:
    """Safer local implementation of the reviewed ETF momentum rotation concept.

    The engine only produces a trade plan. It has no broker, no paper-account
    side effect, no notifier, and no dependency on the stock strategy modules.
    """

    def __init__(
        self,
        settings: ETFStrategySettings,
        parameters: WufuETFParameters | None = None,
    ):
        self.settings = settings
        self.parameters = parameters or WufuETFParameters()

    def decide(
        self,
        as_of: str,
        universe: pd.DataFrame,
        daily: pd.DataFrame,
        index_daily: pd.DataFrame,
        account_value: float,
        current_symbol: str | None = None,
    ) -> ETFDecisionPlan:
        regime, regime_detail = self._market_regime(as_of, index_daily)
        target_weight = self._target_weight(regime)
        candidates = self._rank_candidates(as_of, universe, daily, regime, account_value)
        current_symbol = _normalize_symbol(current_symbol) if current_symbol else None
        selected = candidates[0] if candidates else None
        if current_symbol and selected:
            existing = next((item for item in candidates if item.symbol == current_symbol), None)
            if existing and existing.momentum_score >= selected.momentum_score * self.parameters.candidate_hysteresis:
                selected = existing
        target = selected.to_dict() if selected else None

        risk_flags: list[str] = []
        if regime == "unknown":
            risk_flags.append("指数历史数据不足，禁止新增 ETF 仓位")
            target_weight = 0.0
        if target is None:
            if current_symbol:
                signal = "SELL"
                trade_plan = [{"action": "SELL", "symbol": current_symbol, "target_weight": 0.0, "reason": "无合格ETF候选"}]
                reasoning = "所有候选均未通过动量、趋势质量、流动性或风险门槛。"
            else:
                signal = "HOLD"
                trade_plan = []
                reasoning = "无合格 ETF 候选，保持现金。"
        elif current_symbol == target["symbol"]:
            signal = "HOLD"
            trade_plan = []
            reasoning = "当前持仓仍处于满足硬门槛的候选池，维持而非频繁切换。"
        elif target_weight <= 0:
            signal = "HOLD"
            trade_plan = []
            reasoning = "候选存在，但市场状态或数据完整性不允许新增仓位。"
        else:
            signal = "BUY" if not current_symbol else "SWITCH"
            trade_plan = []
            if current_symbol:
                trade_plan.append(
                    {"action": "SELL", "symbol": current_symbol, "target_weight": 0.0, "reason": "目标ETF发生切换"}
                )
            trade_plan.append(
                {
                    "action": "BUY",
                    "symbol": target["symbol"],
                    "name": target["name"],
                    "target_weight": target_weight,
                    "reference_price": target["latest_price"],
                    "catastrophic_stop": round(target["latest_price"] * (1 - self.parameters.catastrophic_stop_pct), 4),
                    "reason": "量化动量、趋势质量、均线、短期风险和流动性均通过",
                }
            )
            reasoning = "仅输出计划，需先通过实时价格、折溢价和分钟趋势复核后再人工执行。"

        return ETFDecisionPlan(
            strategy_id=self.parameters.strategy_id,
            strategy_version=self.parameters.strategy_version,
            as_of=as_of,
            regime=regime,
            regime_detail=regime_detail,
            signal=signal,
            target_weight=target_weight,
            target=target,
            current_symbol=current_symbol,
            trade_plan=trade_plan,
            candidates=candidates[:10],
            risk_flags=risk_flags,
            reasoning=reasoning,
        )

    def confirm_intraday(self, plan: ETFDecisionPlan, minute_bars: pd.DataFrame) -> ETFDecisionPlan:
        """Applies a fail-closed 30-minute trend confirmation to a planned entry."""
        if plan.signal not in {"BUY", "SWITCH"} or not plan.target:
            plan.intraday_confirmation = {"status": "not_required"}
            return plan
        if minute_bars is None or minute_bars.empty or len(minute_bars) < 15:
            plan.intraday_confirmation = {"status": "unavailable", "approved": False}
            plan.signal = "HOLD"
            plan.target_weight = 0.0
            plan.trade_plan = []
            plan.risk_flags.append("分钟行情不足，拒绝强制买入")
            plan.reasoning = "分钟趋势无法确认，保持现金并等待下一次数据刷新。"
            return plan

        closes = pd.to_numeric(minute_bars["close"], errors="coerce").dropna().tail(30).to_numpy(dtype=float)
        if len(closes) < 15 or np.any(closes <= 0):
            plan.intraday_confirmation = {"status": "invalid", "approved": False}
            plan.signal = "HOLD"
            plan.target_weight = 0.0
            plan.trade_plan = []
            plan.risk_flags.append("分钟行情无效，拒绝强制买入")
            return plan

        x = np.arange(len(closes), dtype=float)
        slope = float(np.polyfit(x, closes, 1)[0])
        slope_pct_per_min = slope / float(np.mean(closes))
        approved = slope_pct_per_min > 0.00001
        plan.intraday_confirmation = {
            "status": "ok",
            "approved": approved,
            "bars": int(len(closes)),
            "slope_pct_per_min": round(slope_pct_per_min, 8),
        }
        if not approved:
            plan.signal = "HOLD"
            plan.target_weight = 0.0
            plan.trade_plan = []
            plan.risk_flags.append("近30分钟趋势未转正，拒绝追买")
            plan.reasoning = "日线候选有效，但分钟趋势未确认；不使用尾盘强制买入。"
        return plan

    def apply_nav_check(self, plan: ETFDecisionPlan, nav_row: dict[str, Any] | None) -> ETFDecisionPlan:
        if not plan.target:
            plan.nav_check = {"status": "not_required"}
            return plan
        nav = _float((nav_row or {}).get("unit_nav"))
        nav_date = str((nav_row or {}).get("nav_date") or "")
        price = _float(plan.target.get("latest_price"))
        if nav <= 0 or price <= 0:
            plan.nav_check = {"status": "unavailable", "nav_date": nav_date}
            return plan
        premium = price / nav - 1.0
        plan.nav_check = {
            "status": "ok",
            "nav_date": nav_date,
            "unit_nav": nav,
            "market_price": price,
            "premium_pct": round(premium, 6),
        }
        if premium > self.settings.max_nav_premium_pct and plan.signal in {"BUY", "SWITCH"}:
            plan.signal = "HOLD"
            plan.target_weight = 0.0
            plan.trade_plan = []
            plan.risk_flags.append(f"二级市场溢价{premium:.2%}超过上限{self.settings.max_nav_premium_pct:.2%}")
            plan.reasoning = "候选存在异常溢价，等待折溢价回归后再评估。"
        return plan

    def _market_regime(self, as_of: str, index_daily: pd.DataFrame) -> tuple[str, dict[str, Any]]:
        details: dict[str, Any] = {"as_of": as_of, "indices": []}
        if index_daily.empty:
            return "unknown", details
        above = 0
        below = 0
        for symbol in INDEX_SYMBOLS:
            rows = index_daily[(index_daily["symbol"] == symbol) & (index_daily["trade_date"] <= as_of)].sort_values("trade_date")
            closes = pd.to_numeric(rows["close"], errors="coerce").dropna().tail(self.parameters.ma_days)
            if len(closes) < self.parameters.ma_days:
                continue
            close = float(closes.iloc[-1])
            ma = float(closes.mean())
            status = "above" if close > ma else "below" if close < ma else "flat"
            details["indices"].append({"symbol": symbol, "close": close, "ma10": ma, "status": status})
            above += int(status == "above")
            below += int(status == "below")
        details["above_count"] = above
        details["below_count"] = below
        if len(details["indices"]) < 3:
            return "unknown", details
        if below >= 3:
            return "weak", details
        if above >= 3:
            return "normal", details
        return "neutral", details

    def _rank_candidates(
        self,
        as_of: str,
        universe: pd.DataFrame,
        daily: pd.DataFrame,
        regime: str,
        account_value: float,
    ) -> list[ETFCandidate]:
        if universe.empty or daily.empty:
            return []
        min_turnover = max(
            self.settings.min_avg_turnover_yuan,
            max(account_value, 0.0) * self.settings.turnover_to_order_multiple,
        )
        candidates: list[ETFCandidate] = []
        instruments = {
            _normalize_symbol(item.get("symbol")): item
            for _, item in universe.iterrows()
            if _normalize_symbol(item.get("symbol"))
        }
        scoped_daily = daily[
            (daily["trade_date"] <= as_of)
            & daily["symbol"].astype(str).str.upper().isin(instruments)
        ]
        for raw_symbol, history in scoped_daily.groupby("symbol", sort=False):
            symbol = _normalize_symbol(raw_symbol)
            item = instruments.get(symbol)
            if item is None:
                continue
            candidate = self._candidate_from_history(item, history, min_turnover)
            if candidate is None:
                continue
            if regime == "weak" and candidate.bucket != "global":
                continue
            candidates.append(candidate)

        # Retain only the most liquid representative for a common tracking index.
        representatives: dict[str, ETFCandidate] = {}
        for candidate in candidates:
            group_key = candidate.index_code or candidate.index_name or candidate.symbol
            existing = representatives.get(group_key)
            if existing is None or candidate.avg_turnover_yuan > existing.avg_turnover_yuan:
                representatives[group_key] = candidate
        return sorted(
            representatives.values(),
            key=lambda item: (item.momentum_score, item.avg_turnover_yuan),
            reverse=True,
        )

    def _candidate_from_history(
        self,
        instrument: pd.Series,
        history: pd.DataFrame,
        min_turnover: float,
    ) -> ETFCandidate | None:
        required = max(self.parameters.lookback_days + 1, self.parameters.ma_days, 4)
        if len(history) < required:
            return None
        closes = pd.to_numeric(history["close"], errors="coerce").dropna().to_numpy(dtype=float)
        if len(closes) < required or np.any(closes[-required:] <= 0):
            return None
        prices = closes[-(self.parameters.lookback_days + 1):]
        score, annualized_trend, r_squared = _weighted_momentum(prices)
        ma10 = float(np.mean(closes[-self.parameters.ma_days:]))
        latest_price = float(prices[-1])
        daily_returns = prices[1:] / prices[:-1] - 1.0
        three_day_min_return = float(np.min(daily_returns[-3:]))
        annualized_volatility = float(np.std(daily_returns, ddof=1) * math.sqrt(250)) if len(daily_returns) > 1 else 0.0
        amount_values = pd.to_numeric(history["amount"], errors="coerce").fillna(0.0).tail(3)
        avg_turnover_yuan = float(amount_values.mean() * self.settings.daily_amount_multiplier)

        if score <= 0 or r_squared < self.parameters.r_squared_min:
            return None
        if latest_price <= ma10 or three_day_min_return < self.parameters.short_loss_floor:
            return None
        if avg_turnover_yuan < min_turnover:
            return None
        if annualized_volatility > self.parameters.max_annualized_volatility:
            return None

        name = str(instrument.get("name") or instrument.get("symbol") or "")
        etf_type = str(instrument.get("etf_type") or "")
        bucket = "global" if _is_global(name, etf_type) else "china"
        return ETFCandidate(
            symbol=_normalize_symbol(instrument.get("symbol")),
            name=name,
            bucket=bucket,
            index_code=str(instrument.get("index_code") or ""),
            index_name=str(instrument.get("index_name") or ""),
            latest_price=round(latest_price, 4),
            momentum_score=round(score, 6),
            annualized_trend=round(annualized_trend, 6),
            r_squared=round(r_squared, 6),
            ma10=round(ma10, 4),
            avg_turnover_yuan=round(avg_turnover_yuan, 2),
            annualized_volatility=round(annualized_volatility, 6),
            three_day_min_return=round(three_day_min_return, 6),
            reasons=["25日加权趋势为正", "R2趋势质量通过", "站上MA10", "近三日无深跌", "流动性通过"],
        )

    def _target_weight(self, regime: str) -> float:
        if regime == "normal":
            return self.parameters.normal_target_weight
        if regime == "weak":
            return self.parameters.weak_target_weight
        if regime == "neutral":
            return self.parameters.neutral_target_weight
        return 0.0


def _weighted_momentum(prices: np.ndarray) -> tuple[float, float, float]:
    y = np.log(prices)
    x = np.arange(len(y), dtype=float)
    weights = np.linspace(1.0, 2.0, len(y)) ** 2
    x_bar = float(np.average(x, weights=weights))
    y_bar = float(np.average(y, weights=weights))
    slope_denominator = float(np.sum(weights * (x - x_bar) ** 2))
    if slope_denominator <= 0:
        return 0.0, 0.0, 0.0
    slope = float(np.sum(weights * (x - x_bar) * (y - y_bar)) / slope_denominator)
    fitted = slope * x + (y_bar - slope * x_bar)
    ss_res = float(np.sum(weights * (y - fitted) ** 2))
    ss_tot = float(np.sum(weights * (y - y_bar) ** 2))
    r_squared = max(0.0, min(1.0, 1.0 - ss_res / ss_tot)) if ss_tot > 0 else 0.0
    annualized_trend = math.exp(slope * 250) - 1.0
    return annualized_trend * r_squared, annualized_trend, r_squared


def _is_global(name: str, etf_type: str) -> bool:
    text = f"{name} {etf_type}".upper()
    return "QDII" in text or any(keyword in text for keyword in GLOBAL_KEYWORDS)


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        return ""
    if "." in symbol:
        code, exchange = symbol.split(".", 1)
        return f"{code.zfill(6)}.{exchange}"
    return f"{symbol.zfill(6)}.SH" if symbol.startswith(("5", "6", "9")) else f"{symbol.zfill(6)}.SZ"


def _float(value: Any) -> float:
    try:
        return float(value) if value not in {None, ""} else 0.0
    except (TypeError, ValueError):
        return 0.0
