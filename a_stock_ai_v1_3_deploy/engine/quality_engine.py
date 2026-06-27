from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import settings
from data.tushare_client import Quote
from learning.strategy_params import param_float


@dataclass(frozen=True)
class QualityResult:
    symbol: str
    name: str
    passed: bool
    score: float
    status: str
    factors: dict[str, float]
    warnings: list[str]
    source: str


def evaluate_quality(quote: Quote) -> dict[str, Any]:
    warnings: list[str] = []
    min_turnover_yi = param_float("MIN_TURNOVER_YI", settings.min_turnover_yi)
    factors = {
        "liquidity": _liquidity_score(quote),
        "price_sanity": _price_sanity_score(quote),
        "intraday_stability": _intraday_stability_score(quote),
        "name_risk": _name_risk_score(quote),
    }

    if _has_name_risk(quote.name):
        warnings.append("名称含 ST/退 等风险标签，剔除")
    if quote.amount_yi < min_turnover_yi:
        warnings.append("成交额偏低，盘中冲击成本可能较高")
    if _intraday_range_pct(quote) > 12:
        warnings.append("日内振幅过大，买点稳定性不足")

    score = (
        factors["liquidity"] * 0.35
        + factors["price_sanity"] * 0.2
        + factors["intraday_stability"] * 0.25
        + factors["name_risk"] * 0.2
    )
    passed = score >= settings.min_quality_score and not _has_name_risk(quote.name)
    status = "passed" if passed else "filtered"

    return asdict(
        QualityResult(
            symbol=quote.symbol,
            name=quote.name,
            passed=passed,
            score=round(score, 2),
            status=status,
            factors={key: round(value, 2) for key, value in factors.items()},
            warnings=warnings or ["基础质地过滤通过"],
            source="realtime_heuristic_quality",
        )
    )


class QualityEngine:
    def evaluate(self, quote: Quote) -> dict[str, Any]:
        return evaluate_quality(quote)


def _liquidity_score(quote: Quote) -> float:
    if quote.amount_yi <= 0:
        return 0.0
    min_turnover_yi = param_float("MIN_TURNOVER_YI", settings.min_turnover_yi)
    return max(0.0, min(100.0, 35 + min(quote.amount_yi / max(min_turnover_yi, 0.1), 4) * 16))


def _price_sanity_score(quote: Quote) -> float:
    if quote.price <= 0:
        return 0.0
    if quote.price < 3:
        return 35.0
    if quote.price > 300:
        return 70.0
    return 90.0


def _intraday_stability_score(quote: Quote) -> float:
    range_pct = _intraday_range_pct(quote)
    if range_pct <= 0:
        return 70.0
    if range_pct <= 5:
        return 95.0
    if range_pct <= 8:
        return 80.0
    if range_pct <= 12:
        return 60.0
    return 30.0


def _name_risk_score(quote: Quote) -> float:
    return 0.0 if _has_name_risk(quote.name) else 95.0


def _has_name_risk(name: str) -> bool:
    normalized = str(name).upper()
    return any(flag in normalized for flag in ("ST", "*ST", "退", "退市"))


def _intraday_range_pct(quote: Quote) -> float:
    if quote.low <= 0 or quote.high <= 0:
        return 0.0
    base = quote.pre_close or quote.low
    if base <= 0:
        return 0.0
    return (quote.high - quote.low) / base * 100
