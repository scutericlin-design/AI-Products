from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from data.tushare_client import Quote
from scheduler.trading_calendar import BEIJING_TZ


class RegimeEngine:
    """Turns market breadth and sentiment into a stable, auditable allocation regime."""

    def evaluate(
        self,
        state: dict[str, Any],
        sentiment: dict[str, Any],
        quotes: list[Quote],
    ) -> dict[str, Any]:
        candidate, reason, score = _classify(state, sentiment, quotes)
        previous = self._load_state()
        current = str(previous.get("current") or "range")
        previous_candidate = str(previous.get("candidate") or current)
        streak = int(previous.get("streak") or 0)

        if candidate == "risk_off":
            current, streak = candidate, settings.market_regime_confirm_cycles
        elif candidate == current:
            streak = 0
        elif candidate == previous_candidate:
            streak += 1
            if streak >= settings.market_regime_confirm_cycles:
                current, streak = candidate, 0
        else:
            streak = 1

        result = {
            "regime": current,
            "candidate_regime": candidate,
            "confirmed": current == candidate,
            "confirmation_streak": streak,
            "required_confirmations": settings.market_regime_confirm_cycles,
            "score": round(score, 2),
            "reason": reason,
            "features": {
                "market_state": str(state.get("state") or "NO_DATA"),
                "breadth": round(_float(sentiment.get("breadth") or state.get("breadth")), 4),
                "sentiment_score": round(_float(sentiment.get("sentiment_score") or state.get("sentiment")), 2),
                "panic_score": round(_float(sentiment.get("panic_score")), 2),
                "trade_permission": str(sentiment.get("trade_permission") or "NO_BUY"),
                "quote_count": len(quotes),
            },
            "updated_at": _now(),
        }
        self._save_state({"current": current, "candidate": candidate, "streak": streak, "updated_at": result["updated_at"]})
        return result

    def _load_state(self) -> dict[str, Any]:
        path = settings.market_regime_state_path
        if not path.exists():
            return {}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, value: dict[str, Any]) -> None:
        path: Path = settings.market_regime_state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _classify(state: dict[str, Any], sentiment: dict[str, Any], quotes: list[Quote]) -> tuple[str, str, float]:
    market_state = str(state.get("state") or "NO_DATA").upper()
    permission = str(sentiment.get("trade_permission") or "NO_BUY").upper()
    breadth = _float(sentiment.get("breadth") or state.get("breadth"))
    sentiment_score = _float(sentiment.get("sentiment_score") or state.get("sentiment"))
    panic = _float(sentiment.get("panic_score"))
    limit_up_ratio = _float(sentiment.get("limit_up_count")) / max(len(quotes), 1)
    near_limit_up_ratio = _float(sentiment.get("near_limit_up_count")) / max(len(quotes), 1)
    trend_score = sentiment_score * 0.48 + breadth * 100 * 0.36 + max(0.0, _float(state.get("avg_pct_change"))) * 5

    if not quotes or market_state == "NO_DATA":
        return "risk_off", "行情覆盖不足，组合只保留防守预算", 0.0
    if market_state == "DOWNTREND" or permission == "NO_BUY" or panic >= settings.sentiment_panic_threshold:
        return "risk_off", "下行、恐慌或情绪风控禁止新增仓位", trend_score
    if sentiment_score >= 80 and (limit_up_ratio + near_limit_up_ratio) >= 0.12:
        return "overheat", "情绪过热，保留趋势仓但降低追涨预算", trend_score
    if market_state == "UPTREND" and breadth >= 0.58 and sentiment_score >= 62 and panic < 45:
        return "trend", "趋势、广度和情绪同步改善", trend_score
    if (market_state == "UPTREND" and sentiment_score >= 52) or (breadth >= 0.48 and sentiment_score >= 55):
        return "structural", "市场允许结构性机会，优先质量与稳健因子", trend_score
    return "range", "市场方向未统一，采用均衡配置与较高现金比例", trend_score


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
