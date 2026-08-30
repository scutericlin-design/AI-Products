from __future__ import annotations

from collections import defaultdict
from math import sqrt
from statistics import mean
from typing import Any

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.models import DailyBar, FactorScore, FundamentalSnapshot, Instrument


STRATEGY_ID = "qgarp_alpha"
STRATEGY_VERSION = "v2.0_quality_value_momentum_research"


class QGARPStrategy:
    def __init__(self, settings: QGARPSettings):
        self.settings = settings

    def decide(self, *, as_of: str, instruments: list[Instrument], histories: dict[str, list[DailyBar]], fundamentals: dict[str, FundamentalSnapshot], benchmark: list[DailyBar]) -> dict[str, Any]:
        regime, exposure, regime_reason = self._regime(benchmark)
        raw: list[dict[str, Any]] = []
        rejected: list[str] = []
        for instrument in instruments:
            bars = histories.get(instrument.symbol) or []
            fundamental = fundamentals.get(instrument.symbol)
            reason = self._eligibility_reason(instrument, bars, fundamental, as_of)
            if reason:
                rejected.append(f"{instrument.symbol}:{reason}")
                continue
            raw.append({"instrument": instrument, "bars": bars, "fundamental": fundamental, "raw": self._raw_factors(bars, fundamental)})

        ranked = self._rank_and_score(raw)
        recommendations = self._portfolio(ranked, exposure)
        signal = "BUY" if recommendations and exposure > 0 else "HOLD"
        return {
            "strategy_id": STRATEGY_ID,
            "strategy_version": self.settings.strategy_version,
            "strategy_profile": self.settings.strategy_profile,
            "parameters": self.settings.parameter_snapshot(),
            "as_of": as_of,
            "signal": signal,
            "target_exposure": round(sum(item["target_weight"] for item in recommendations), 4),
            "market_regime": regime,
            "market_regime_reason": regime_reason,
            "recommendations": recommendations,
            "candidate_count": len(raw),
            "rejected_count": len(rejected),
            "rejected_sample": rejected[:30],
            "risk_flags": ["local_paper_only", "hard_risk_overrides_ai", "no_financial_fallback", "parameter_snapshot_saved"],
            "selection_logic": "行业内排名的质量、盈利成长、估值、中期动量、低波与流动性模块；权重、门槛和再平衡频率均由 QGARP 参数控制。",
            "data_requirements": "财务与公告时点数据缺失时不生成买入；TuShare复权因子优先用于动量/波动计算，缺失时明确降级为原始价格；AKShare仅用于价格备用。",
        }

    def _eligibility_reason(self, instrument: Instrument, bars: list[DailyBar], fundamental: FundamentalSnapshot | None, as_of: str) -> str | None:
        name = instrument.name.upper()
        if "ST" in name or "退" in name:
            return "ST_or_delisting_risk"
        if instrument.list_date and as_of and _days_between(instrument.list_date, as_of) < self.settings.min_listing_days:
            return "listing_age"
        if instrument.delist_date and instrument.delist_date <= as_of:
            return "delisted_as_of_date"
        required_history = max(
            self.settings.min_history_days,
            self.settings.momentum_long_days + self.settings.momentum_skip_days + 1,
        )
        if len(bars) < required_history:
            return "insufficient_history"
        if fundamental is None:
            return "missing_point_in_time_fundamental"
        if fundamental.available_at > as_of:
            return "future_financial_data"
        if fundamental.roe is not None and fundamental.roe < self.settings.min_roe:
            return "low_roe"
        if fundamental.debt_to_assets is not None and fundamental.debt_to_assets > self.settings.max_debt_to_assets:
            return "high_leverage"
        average_amount = mean(max(bar.amount, 0.0) for bar in bars[-20:])
        if average_amount < self.settings.min_average_amount_yuan:
            return "illiquid"
        if bars[-1].close <= 0 or bars[-1].pct_chg >= 9.5:
            return "untradable_or_near_limit_up"
        if self.settings.require_positive_pe and (bars[-1].pe_ttm is None or bars[-1].pe_ttm <= 0):
            return "non_positive_pe"
        if self.settings.require_positive_pb and (bars[-1].pb is None or bars[-1].pb <= 0):
            return "non_positive_pb"
        return None

    def _raw_factors(self, bars: list[DailyBar], f: FundamentalSnapshot) -> dict[str, float]:
        closes = _adjusted_closes(bars)
        short_days = self.settings.momentum_short_days
        long_days = self.settings.momentum_long_days
        skip_days = self.settings.momentum_skip_days
        anchor = len(closes) - 1 - skip_days
        r_short = closes[anchor] / closes[anchor - short_days] - 1
        r_long = closes[anchor] / closes[anchor - long_days] - 1
        ma20 = mean(closes[-20:])
        ma60 = mean(closes[-60:])
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        volatility = sqrt(mean(value * value for value in returns[-20:])) if returns else 0.0
        current = bars[-1]
        return {
            "quality": _value(f.roe) * 0.60 + _value(f.operating_cashflow_per_share) * 10 - _value(f.debt_to_assets) * 0.20,
            "growth": _value(f.revenue_yoy) * 0.40 + _value(f.profit_yoy) * 0.60,
            # Positive PE/PB are eligibility gates, so rank is a transparent
            # relative valuation signal rather than an accidental loss-maker bias.
            "value": -_value(current.pe_ttm) * 0.65 - _value(current.pb) * 3.5,
            "momentum": r_short * 0.45 + r_long * 0.55 + (0.02 if closes[-1] >= ma20 >= ma60 else -0.02),
            "low_vol": -volatility,
            "liquidity": min(mean(bar.amount for bar in bars[-20:]) / 100_000_000, 20.0),
        }

    def _rank_and_score(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[str(item["instrument"].industry or "未分类")].append(item)
        for group in grouped.values():
            # A small industry group is ranked against the full sample, but only its
            # own members receive those ranks.  Writing ranks straight onto the full
            # sample here would let every later small industry overwrite unrelated
            # industries' scores.
            peer_group = group if len(group) >= 5 else items
            for key in ("quality", "growth", "value", "momentum", "low_vol", "liquidity"):
                ranks = _percentile_map(peer_group, key, ascending=False)
                for item in group:
                    item.setdefault("ranks", {})[key] = ranks[id(item)]
        for item in items:
            ranks = item["ranks"]
            weights = self.settings.factor_weights
            score = sum(ranks[key] * weights[key] * 100 for key in weights)
            item["factor_score"] = FactorScore(symbol=item["instrument"].symbol, quality=round(ranks["quality"] * 100, 2), earnings=round(ranks["growth"] * 100, 2), valuation=round(ranks["value"] * 100, 2), trend=round(ranks["momentum"] * 100, 2), liquidity=round(ranks["liquidity"] * 100, 2), low_vol=round(ranks["low_vol"] * 100, 2), risk_penalty=0.0, total_score=round(score, 2))
        return sorted(items, key=lambda item: item["factor_score"].total_score, reverse=True)

    def _portfolio(self, ranked: list[dict[str, Any]], exposure: float) -> list[dict[str, Any]]:
        top_count = max(1, int(len(ranked) * self.settings.selection_top_pct))
        # A disabled factor must not remain a hidden eligibility gate.  In
        # particular, research profiles that deliberately set momentum to zero
        # should not still have to pass a momentum confirmation test.
        score_fields = {
            "quality": "quality",
            "growth": "earnings",
            "value": "valuation",
            "momentum": "trend",
            "low_vol": "low_vol",
            "liquidity": "liquidity",
        }
        active_confirmation_fields = [
            score_fields[factor]
            for factor, weight in self.settings.factor_weights.items()
            if weight > 0 and factor in score_fields
        ]
        required_confirmations = min(self.settings.factor_confirmation_count, len(active_confirmation_fields))
        eligible = [
            item for item in ranked[:top_count]
            if sum(int(getattr(item["factor_score"], field) >= 60) for field in active_confirmation_fields) >= required_confirmations
        ]
        selected: list[dict[str, Any]] = []
        industry_weights: dict[str, float] = defaultdict(float)
        for item in eligible:
            if len(selected) >= self.settings.max_names:
                break
            industry = str(item["instrument"].industry or "未分类")
            if industry_weights[industry] + self.settings.max_single_weight > self.settings.max_industry_weight + 1e-9:
                continue
            selected.append(item)
            industry_weights[industry] += self.settings.max_single_weight
        if not selected:
            return []
        scores = [max(item["factor_score"].total_score, 1.0) for item in selected]
        allocation = min(exposure, self.settings.max_single_weight * len(selected))
        total = sum(scores) if self.settings.weighting_mode == "score" else float(len(selected))
        result = []
        for item, score in zip(selected, scores):
            instrument, bars, factor = item["instrument"], item["bars"], item["factor_score"]
            price = bars[-1].close
            numerator = score if self.settings.weighting_mode == "score" else 1.0
            weight = min(self.settings.max_single_weight, allocation * numerator / total, self.settings.paper_max_position_pct)
            automated_stop = self.settings.catastrophic_stop_pct if self.settings.daily_exit_enabled else self.settings.stop_loss_pct
            result.append({
                "symbol": instrument.symbol, "name": instrument.name, "industry": instrument.industry,
                "action": "BUY", "trigger_price": round(price, 2), "current_price": round(price, 2),
                "buy_range": {"low": round(price * 0.985, 2), "high": round(price * 1.005, 2)},
                "max_buy_price": round(price * 1.005, 2), "target_weight": round(weight, 4), "position": round(weight, 4),
                "stop_loss": round(price * (1 - automated_stop), 2),
                "take_profit": round(price * (1 + self.settings.take_profit_pct), 2),
                "trailing_stop_pct": self.settings.trailing_stop_pct,
                "strategy_score": factor.total_score, "factor_scores": factor.__dict__,
                "reasoning": f"质量{factor.quality:.0f}、成长{factor.earnings:.0f}、估值{factor.valuation:.0f}、中期动量{factor.trend:.0f}、低波{factor.low_vol:.0f}；{self.settings.strategy_profile}参数档。",
                "strategy_id": STRATEGY_ID, "strategy_version": self.settings.strategy_version,
            })
        return result

    def _regime(self, bars: list[DailyBar]) -> tuple[str, float, str]:
        if len(bars) < 61:
            return "range", self.settings.range_exposure, "基准历史不足，按中性仓位且不放大风险"
        closes = [bar.close for bar in bars]
        ma20, ma60 = mean(closes[-20:]), mean(closes[-60:])
        if closes[-1] >= ma20 >= ma60:
            return "uptrend", self.settings.uptrend_exposure, "基准位于20日与60日均线上方"
        if closes[-1] < ma20 and ma20 < ma60:
            return "risk_off", self.settings.risk_off_exposure, "基准跌破20日与60日均线，执行防守仓位"
        return "range", self.settings.range_exposure, "基准趋势分歧，按中性仓位"


def _percentile_map(items: list[dict[str, Any]], key: str, ascending: bool) -> dict[int, float]:
    ordered = sorted(items, key=lambda item: item["raw"][key], reverse=not ascending)
    n = max(len(ordered) - 1, 1)
    return {id(item): index / n if ascending else 1 - index / n for index, item in enumerate(ordered)}


def _value(value: float | None) -> float:
    return float(value) if value is not None else 0.0


def _adjusted_closes(bars: list[DailyBar]) -> list[float]:
    """Return a common-scale price series for return factors.

    The latest factor is the scale anchor, so this changes neither the latest
    price nor relative returns.  If a historical cache has not yet been
    enriched with TuShare adj_factor, it deliberately falls back to raw closes
    and the plan declares that limitation instead of inventing adjusted data.
    """
    factors = [bar.adj_factor for bar in bars]
    if any(value is None or value <= 0 for value in factors):
        return [bar.close for bar in bars]
    anchor = float(factors[-1] or 1.0)
    return [bar.close * float(bar.adj_factor or anchor) / anchor for bar in bars]


def _days_between(left: str, right: str) -> int:
    try:
        from datetime import datetime
        return (datetime.strptime(right, "%Y%m%d") - datetime.strptime(left, "%Y%m%d")).days
    except ValueError:
        return 9999
