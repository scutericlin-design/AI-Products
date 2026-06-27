from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.config import settings
from learning.strategy_params import get_strategy_param_snapshot, get_strategy_params


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def optimize_parameters(evaluation: dict[str, Any]) -> dict[str, Any]:
    outcomes = list(evaluation.get("outcomes") or [])
    metrics = _metrics(outcomes)
    current = get_strategy_params()
    evaluated_count = int(evaluation.get("evaluated_count") or len(outcomes))

    if evaluated_count < settings.self_learning_min_samples:
        return {
            "status": "skipped_insufficient_samples",
            "should_apply": False,
            "reason": (
                f"有效样本 {evaluated_count} 条，低于最小学习样本 "
                f"{settings.self_learning_min_samples} 条，保持当前参数"
            ),
            "metrics": metrics,
            "changes": {},
            "current_params": current,
        }

    changes: dict[str, Any] = {}
    reason_parts: list[str] = []
    win_rate = float(metrics.get("win_rate") or 0)
    avg_return = float(metrics.get("avg_close_return_pct") or 0)
    stop_rate = float(metrics.get("stop_hit_rate") or 0)
    avg_adverse = float(metrics.get("avg_max_adverse_pct") or 0)
    stable_performance = _is_stable_performance(metrics)
    if stable_performance:
        return {
            "status": "stable_no_change",
            "should_apply": False,
            "reason": "近窗口表现稳定，系统保持当前参数，不做为了优化而优化",
            "metrics": metrics,
            "changes": {},
            "current_params": current,
            "stability": _stability_payload(metrics),
        }

    weak_performance = win_rate < 0.46 or avg_return < -0.2
    strong_performance = (
        evaluated_count >= settings.self_learning_min_samples * 2
        and win_rate >= 0.62
        and avg_return >= 0.6
        and stop_rate <= 0.12
    )

    if weak_performance:
        changes["CONFIDENCE_THRESHOLD"] = current["CONFIDENCE_THRESHOLD"] + 0.015
        changes["SENTIMENT_MIN_BUY_SCORE"] = current["SENTIMENT_MIN_BUY_SCORE"] + 1.5
        changes["MAX_CHASE_PCT"] = current["MAX_CHASE_PCT"] - 0.001
        reason_parts.append("近窗口推荐胜率或收盘收益偏弱，提高买入门槛并减少追价")

    if stop_rate >= 0.18 or avg_adverse <= -3.5:
        changes["CONFIDENCE_THRESHOLD"] = max(changes.get("CONFIDENCE_THRESHOLD", current["CONFIDENCE_THRESHOLD"]), current["CONFIDENCE_THRESHOLD"] + 0.01)
        changes["BUY_RANGE_PULLBACK_PCT"] = current["BUY_RANGE_PULLBACK_PCT"] + 0.001
        changes["MAX_CHASE_PCT"] = current["MAX_CHASE_PCT"] - 0.001
        reason_parts.append("止损或回撤压力偏高，要求更深回落买点并降低追价")

    if _loss_rate_if(outcomes, lambda item: _feature(item, "pct_change") >= current["MAX_RECOMMEND_PCT_CHANGE"] - 1.0) >= 0.58:
        changes["MAX_RECOMMEND_PCT_CHANGE"] = current["MAX_RECOMMEND_PCT_CHANGE"] - 0.4
        reason_parts.append("高涨幅推荐的失败率偏高，下调最大推荐涨幅")

    if _loss_rate_if(outcomes, lambda item: _feature(item, "amount_yi") < current["MIN_TURNOVER_YI"] * 1.5) >= 0.58:
        changes["MIN_TURNOVER_YI"] = current["MIN_TURNOVER_YI"] + 0.2
        reason_parts.append("低成交额标的表现偏弱，提高流动性门槛")

    if _loss_rate_if(outcomes, lambda item: _feature(item, "sentiment_score") < current["SENTIMENT_MIN_BUY_SCORE"] + 4) >= 0.55:
        changes["SENTIMENT_MIN_BUY_SCORE"] = max(
            changes.get("SENTIMENT_MIN_BUY_SCORE", current["SENTIMENT_MIN_BUY_SCORE"]),
            current["SENTIMENT_MIN_BUY_SCORE"] + 1.5,
        )
        changes["SENTIMENT_RISK_OFF_SCORE"] = current["SENTIMENT_RISK_OFF_SCORE"] + 1.0
        reason_parts.append("低情绪环境下推荐效果偏弱，提高情绪准入门槛")

    if weak_performance and int(current["MAX_PUSH_STOCKS"]) > 1:
        changes["MAX_PUSH_STOCKS"] = int(current["MAX_PUSH_STOCKS"]) - 1
        reason_parts.append("推荐质量偏弱，减少每轮推送数量")

    if strong_performance:
        changes["CONFIDENCE_THRESHOLD"] = current["CONFIDENCE_THRESHOLD"] - 0.005
        changes["SENTIMENT_MIN_BUY_SCORE"] = current["SENTIMENT_MIN_BUY_SCORE"] - 0.5
        if int(current["MAX_PUSH_STOCKS"]) < 3:
            changes["MAX_PUSH_STOCKS"] = int(current["MAX_PUSH_STOCKS"]) + 1
        reason_parts.append("近窗口表现稳定，轻微放宽准入但不提高最大仓位")

    changes = {key: value for key, value in changes.items() if value != current.get(key)}
    if not changes:
        return {
            "status": "no_change",
            "should_apply": False,
            "reason": "近窗口表现没有触发参数变更条件，保持当前参数",
            "metrics": metrics,
            "changes": {},
            "current_params": current,
        }

    if _cooldown_active() and not _is_emergency_degradation(metrics):
        return {
            "status": "cooldown_no_change",
            "should_apply": False,
            "reason": f"距上次参数更新未超过 {settings.self_learning_cooldown_days} 天冷却期，保持当前参数",
            "metrics": metrics,
            "changes": changes,
            "current_params": current,
            "stability": _stability_payload(metrics),
        }

    return {
        "status": "proposed",
        "should_apply": False,
        "reason": "；".join(reason_parts),
        "metrics": metrics,
        "changes": changes,
        "current_params": current,
        "stability": _stability_payload(metrics),
        "requires_ai_review": settings.self_learning_ai_review_enabled,
    }


class ParameterOptimizer:
    def optimize(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        return optimize_parameters(evaluation)


def _metrics(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    if not outcomes:
        return {
            "evaluated_count": 0,
            "win_rate": 0.0,
            "avg_close_return_pct": 0.0,
            "avg_max_favorable_pct": 0.0,
            "avg_max_adverse_pct": 0.0,
            "stop_hit_rate": 0.0,
            "avg_outcome_score": 0.0,
        }

    count = len(outcomes)
    return {
        "evaluated_count": count,
        "win_rate": round(sum(1 for item in outcomes if item.get("success")) / count, 4),
        "avg_close_return_pct": round(_avg(outcomes, "close_return_pct"), 4),
        "avg_max_favorable_pct": round(_avg(outcomes, "max_favorable_pct"), 4),
        "avg_max_adverse_pct": round(_avg(outcomes, "max_adverse_pct"), 4),
        "stop_hit_rate": round(sum(1 for item in outcomes if item.get("stop_hit")) / count, 4),
        "avg_outcome_score": round(_avg(outcomes, "outcome_score"), 4),
        "loss_rate": round(sum(1 for item in outcomes if float(item.get("outcome_score") or 0) < 0) / count, 4),
    }


def _is_stable_performance(metrics: dict[str, Any]) -> bool:
    return (
        float(metrics.get("win_rate") or 0) >= settings.self_learning_stable_min_win_rate
        and float(metrics.get("avg_close_return_pct") or 0) >= settings.self_learning_stable_min_avg_return_pct
        and float(metrics.get("stop_hit_rate") or 0) <= settings.self_learning_stable_max_stop_rate
    )


def _is_emergency_degradation(metrics: dict[str, Any]) -> bool:
    return (
        float(metrics.get("win_rate") or 0) <= 0.35
        or float(metrics.get("avg_close_return_pct") or 0) <= -1.0
        or float(metrics.get("stop_hit_rate") or 0) >= 0.30
    )


def _cooldown_active() -> bool:
    if settings.self_learning_cooldown_days <= 0:
        return False
    snapshot = get_strategy_param_snapshot()
    last_at = snapshot.get("last_self_learning_at")
    if not last_at:
        return False
    try:
        previous = datetime.fromisoformat(str(last_at))
    except ValueError:
        return False
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=BEIJING_TZ)
    elapsed_days = (datetime.now(BEIJING_TZ) - previous.astimezone(BEIJING_TZ)).total_seconds() / 86400
    return elapsed_days < settings.self_learning_cooldown_days


def _stability_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_min_win_rate": settings.self_learning_stable_min_win_rate,
        "stable_min_avg_return_pct": settings.self_learning_stable_min_avg_return_pct,
        "stable_max_stop_rate": settings.self_learning_stable_max_stop_rate,
        "is_stable": _is_stable_performance(metrics),
        "is_emergency_degradation": _is_emergency_degradation(metrics),
        "cooldown_active": _cooldown_active(),
    }


def _loss_rate_if(outcomes: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> float:
    selected = [item for item in outcomes if predicate(item)]
    if not selected:
        return 0.0
    losses = sum(1 for item in selected if float(item.get("outcome_score") or 0) < 0)
    return losses / len(selected)


def _avg(outcomes: list[dict[str, Any]], key: str) -> float:
    if not outcomes:
        return 0.0
    return sum(float(item.get(key) or 0) for item in outcomes) / len(outcomes)


def _feature(outcome: dict[str, Any], name: str) -> float:
    features = outcome.get("features") if isinstance(outcome.get("features"), dict) else {}
    try:
        return float(features.get(name) or 0)
    except (TypeError, ValueError):
        return 0.0
