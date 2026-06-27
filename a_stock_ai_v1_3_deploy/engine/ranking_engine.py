from __future__ import annotations

from typing import Any

from app.config import settings
from data.tushare_client import Quote
from engine.quality_engine import evaluate_quality
from engine.tradability_engine import evaluate_tradability
from learning.strategy_params import param_float, param_int


def build_recommendation_bundle(
    state: dict[str, Any],
    leader: dict[str, Any],
    quotes: list[Quote],
    market_sentiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sentiment = market_sentiment or {}
    if not quotes:
        return _empty_bundle("没有实时行情数据，系统保持空仓观察", sentiment)

    market_state = str(state.get("state") or "SIDEWAYS").upper()
    phase = _market_phase_profile(state, sentiment)
    if market_state == "DOWNTREND":
        return {
            **_empty_bundle("市场处于下行状态，暂停新增买入推荐", sentiment, phase),
            "signal": "SELL",
            "risk_level": "high",
        }
    if market_state == "NO_DATA":
        return _empty_bundle("行情状态不可用，暂停推荐", sentiment, phase)
    if _sentiment_blocks_new_buys(sentiment):
        return {
            **_empty_bundle(_sentiment_no_buy_reason(sentiment), sentiment, phase),
            "signal": "HOLD",
            "risk_level": "high",
        }

    leader_strength = _leader_strength_map(leader)
    ranked_rows = []
    for quote in quotes:
        strength = leader_strength.get(quote.symbol, _fallback_strength(quote))
        quality = evaluate_quality(quote)
        tradability = evaluate_tradability(quote)
        selection_mode = _selection_mode(quote, market_state, sentiment, quality, tradability, strength, phase)
        score = _rank_score(state, sentiment, strength, quality, tradability, quote, selection_mode, phase)
        action = _action_for(market_state, sentiment, strength, score, quality, tradability, selection_mode, phase)
        ranked_rows.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "action": action,
                "selection_mode": selection_mode,
                "market_stage": phase["stage"],
                "market_stage_reason": phase["reason"],
                "current_price": round(quote.price, 2),
                "pct_change": round(quote.pct_change, 2),
                "amount_yi": round(quote.amount_yi, 2),
                "volume_ratio": round(quote.volume_ratio, 2),
                "intraday_recovery": round(_intraday_recovery(quote), 4),
                "distance_from_high_pct": round(_distance_from_high_pct(quote), 2),
                "leader_strength": round(strength, 2),
                "sentiment_score": _sentiment_score(sentiment, state),
                "sentiment_status": sentiment.get("sentiment_status", "unknown"),
                "trade_permission": sentiment.get("trade_permission", "BUY_ALLOWED"),
                "quality_score": quality["score"],
                "tradability_score": tradability["score"],
                "rank_score": round(score, 2),
                "confidence": round(score / 100, 4),
                "position": _position_for(action, score, sentiment, selection_mode, phase, quality),
                "buy_range": {
                    "low": tradability["buy_range_low"],
                    "high": tradability["buy_range_high"],
                },
                "max_buy_price": tradability["max_buy_price"],
                "stop_loss": tradability["stop_loss"],
                "target_take_profit_pct": _target_take_profit_pct(selection_mode, quality, phase),
                "trailing_stop_pct": _trailing_stop_pct(selection_mode, quality, phase),
                "position_plan": _position_plan(action, selection_mode, phase),
                "buy_mode": _buy_mode(action, tradability, selection_mode, phase),
                "entry_note": _entry_note(action, quote, tradability, selection_mode),
                "can_buy": tradability["can_buy"],
                "is_limit_up": tradability["is_limit_up"],
                "near_limit_up": tradability["near_limit_up"],
                "limit_up_price": tradability["limit_up_price"],
                "quality": quality,
                "tradability": tradability,
                "reasoning": _reasoning(
                    action,
                    state,
                    sentiment,
                    strength,
                    quality,
                    tradability,
                    selection_mode,
                    phase,
                ),
                "risk_flags": _risk_flags(quality, tradability, sentiment, selection_mode, phase),
            }
        )

    ranked_rows.sort(key=lambda item: item["rank_score"], reverse=True)
    max_push_stocks = param_int("MAX_PUSH_STOCKS", settings.max_push_stocks)
    recommendation_limit = max(0, min(max_push_stocks, int(phase["max_recommendations"])))
    recommendations = [item for item in ranked_rows if item["action"] == "BUY"][:recommendation_limit]
    watchlist = [item for item in ranked_rows if item["action"] == "WATCH"][:max_push_stocks]
    filtered_count = sum(1 for item in ranked_rows if item["action"] == "FILTERED")

    signal = "BUY" if recommendations else "HOLD"
    position = min(sum(item["position"] for item in recommendations), settings.max_position_weight)
    return {
        "output_type": "v1_7_stage_aware_ranked_recommendations",
        "signal": signal,
        "position": round(position, 4),
        "risk_level": _bundle_risk_level(recommendations, phase, sentiment),
        "recommendations": recommendations,
        "watchlist": watchlist,
        "candidate_count": len(ranked_rows),
        "recommendation_count": len(recommendations),
        "filtered_count": filtered_count,
        "market_stage": phase["stage"],
        "market_stage_reason": phase["reason"],
        "phase_profile": phase,
        "market_sentiment": sentiment,
        "no_recommendation_reason": (
            None if recommendations else _no_recommendation_reason(market_state, sentiment, watchlist, phase)
        ),
        "selection_logic": (
            "先判断市场阶段，再把候选股分为突破、回踩和观察模式；"
            "随后过滤涨停/接近涨停/ST/低流动性，按情绪、龙头强度、可买性、质地和执行价综合排序"
        ),
    }


class RankingEngine:
    def build(
        self,
        state: dict[str, Any],
        leader: dict[str, Any],
        quotes: list[Quote],
        market_sentiment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_recommendation_bundle(state, leader, quotes, market_sentiment)


def merge_signal_with_recommendations(signal: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    merged = dict(signal)
    merged["signal"] = bundle.get("signal", merged.get("signal", "HOLD"))
    merged["position"] = bundle.get("position", merged.get("position", 0.0))
    merged["risk_level"] = bundle.get("risk_level", merged.get("risk_level", "normal"))
    merged["recommendations"] = bundle.get("recommendations", [])
    merged["watchlist"] = bundle.get("watchlist", [])
    merged["candidate_count"] = bundle.get("candidate_count", 0)
    merged["recommendation_count"] = bundle.get("recommendation_count", 0)
    merged["filtered_count"] = bundle.get("filtered_count", 0)
    merged["market_stage"] = bundle.get("market_stage")
    merged["market_stage_reason"] = bundle.get("market_stage_reason")
    merged["phase_profile"] = bundle.get("phase_profile")
    merged["market_sentiment"] = bundle.get("market_sentiment", merged.get("market_sentiment", {}))
    merged["no_recommendation_reason"] = bundle.get("no_recommendation_reason")
    merged["selection_logic"] = bundle.get("selection_logic")
    return merged


def _empty_bundle(
    reason: str,
    market_sentiment: dict[str, Any] | None = None,
    phase_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase = phase_profile or {"stage": "risk_off", "reason": reason, "max_recommendations": 0}
    return {
        "output_type": "v1_7_stage_aware_ranked_recommendations",
        "signal": "HOLD",
        "position": 0.0,
        "risk_level": "high",
        "recommendations": [],
        "watchlist": [],
        "candidate_count": 0,
        "recommendation_count": 0,
        "filtered_count": 0,
        "market_stage": phase.get("stage", "risk_off"),
        "market_stage_reason": phase.get("reason", reason),
        "phase_profile": phase,
        "market_sentiment": market_sentiment or {},
        "no_recommendation_reason": reason,
        "selection_logic": "行情不可用或市场状态不适合新增买入",
    }


def _leader_strength_map(leader: dict[str, Any]) -> dict[str, float]:
    mapping: dict[str, float] = {}
    for item in leader.get("candidates") or []:
        symbol = item.get("symbol")
        if symbol:
            mapping[str(symbol)] = float(item.get("strength") or 0)
    if leader.get("stock"):
        mapping[str(leader["stock"])] = float(leader.get("strength") or mapping.get(str(leader["stock"]), 0))
    return mapping


def _fallback_strength(quote: Quote) -> float:
    momentum = max(min(quote.pct_change, 10), -10) * 4
    liquidity = min(quote.amount_yi, 20) * 1.5
    volume = min(quote.volume_ratio, 3) * 8
    return max(0.0, min(100.0, 45 + momentum + liquidity + volume))


def _rank_score(
    state: dict[str, Any],
    market_sentiment: dict[str, Any],
    strength: float,
    quality: dict[str, Any],
    tradability: dict[str, Any],
    quote: Quote,
    selection_mode: str,
    phase: dict[str, Any],
) -> float:
    market_score = _market_score(state)
    sentiment_score = _sentiment_score(market_sentiment, state)
    setup_score = _setup_score(quote, selection_mode)
    risk_penalty = 0.0
    if not quality["passed"]:
        risk_penalty += 12.0
    if not tradability["can_buy"]:
        risk_penalty += 18.0
    if selection_mode == "overheated":
        risk_penalty += 16.0
    if selection_mode == "filtered":
        risk_penalty += 24.0
    if str(market_sentiment.get("trade_permission") or "").upper() == "LIGHT_ONLY":
        risk_penalty += 4.0
    if str(market_sentiment.get("trade_permission") or "").upper() == "NO_BUY":
        risk_penalty += 30.0
    if float(market_sentiment.get("panic_score") or 0) >= 55:
        risk_penalty += 10.0
    return max(
        0.0,
        min(
            100.0,
            market_score * 0.12
            + sentiment_score * 0.20
            + strength * 0.24
            + float(tradability["score"]) * 0.17
            + float(quality["score"]) * 0.18
            + setup_score * 0.09
            + _mode_score_adjustment(selection_mode, phase)
            + float(phase.get("rank_adjustment", 0.0))
            - risk_penalty,
        ),
    )


def _market_score(state: dict[str, Any]) -> float:
    market_state = str(state.get("state") or "SIDEWAYS").upper()
    sentiment = float(state.get("sentiment") or 50)
    if market_state == "UPTREND":
        return min(100.0, sentiment + 12.0)
    if market_state == "SIDEWAYS":
        return min(72.0, sentiment)
    return max(0.0, sentiment * 0.45)


def _action_for(
    market_state: str,
    market_sentiment: dict[str, Any],
    strength: float,
    score: float,
    quality: dict[str, Any],
    tradability: dict[str, Any],
    selection_mode: str,
    phase: dict[str, Any],
) -> str:
    threshold = param_float("CONFIDENCE_THRESHOLD", settings.confidence_threshold) * 100
    sentiment_score = _sentiment_score(market_sentiment, {})
    panic_score = float(market_sentiment.get("panic_score") or 0)
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    min_buy_score = param_float("SENTIMENT_MIN_BUY_SCORE", settings.sentiment_min_buy_score)
    trade_permission = str(market_sentiment.get("trade_permission") or "BUY_ALLOWED").upper()
    stage = str(phase.get("stage") or "transition")
    if stage == "risk_off" or trade_permission == "NO_BUY" or panic_score >= panic_threshold:
        return "WATCH" if score >= 65 and quality["passed"] and not tradability["is_limit_up"] else "FILTERED"
    if stage in {"cold", "overheat"}:
        return "WATCH" if score >= 64 and quality["passed"] and not tradability["is_limit_up"] else "FILTERED"
    if selection_mode not in {"breakout", "pullback"}:
        return "WATCH" if score >= 62 and quality["passed"] and not tradability["is_limit_up"] else "FILTERED"

    if trade_permission == "LIGHT_ONLY":
        threshold = max(threshold, 78.0)
        min_strength = 70.0
        min_sentiment = min_buy_score + 2
    else:
        min_strength = 66.0
        min_sentiment = min_buy_score
        if stage == "confirmed":
            threshold = max(threshold, 73.0 if selection_mode == "breakout" else 75.0)
        else:
            threshold = max(threshold, 79.0 if selection_mode == "breakout" else 74.0)

    state_allows_buy = market_state == "UPTREND" or (stage == "transition" and selection_mode == "pullback")

    if (
        state_allows_buy
        and sentiment_score >= min_sentiment
        and panic_score < 55
        and strength >= min_strength
        and score >= threshold
        and quality["passed"]
        and tradability["can_buy"]
    ):
        return "BUY"
    if score >= 62 and quality["passed"] and not tradability["is_limit_up"]:
        return "WATCH"
    return "FILTERED"


def _position_for(
    action: str,
    score: float,
    market_sentiment: dict[str, Any],
    selection_mode: str,
    phase: dict[str, Any],
    quality: dict[str, Any],
) -> float:
    if action != "BUY":
        return 0.0
    scaled = 0.035 + max(0.0, score - 72.0) / 100 * 0.14
    permission = str(market_sentiment.get("trade_permission") or "BUY_ALLOWED").upper()
    coverage_level = str(market_sentiment.get("coverage_level") or "").lower()
    multiplier = 1.0
    if selection_mode == "pullback":
        multiplier *= 0.82
    if float(quality.get("score") or 0) >= 82:
        multiplier *= 1.08
    multiplier *= float(phase.get("position_multiplier") or 1.0)
    if permission == "LIGHT_ONLY":
        multiplier *= 0.55
    if coverage_level == "low":
        multiplier *= 0.7
    return round(min(settings.max_position_weight, scaled * multiplier), 4)


def _buy_mode(action: str, tradability: dict[str, Any], selection_mode: str, phase: dict[str, Any]) -> str:
    if action != "BUY":
        return "仅观察，不追价"
    if selection_mode == "breakout":
        return "突破确认型：当前价在区间内可小仓分批；超过最高追价立即放弃，不排队追涨停"
    if selection_mode == "pullback":
        return "回踩低吸型：只在买入区间内分批挂单，等回落确认，不用市价追高"
    return (
        "当前价落入区间可分批挂单；超过买入上沿不追，等回落或下一轮刷新"
        if tradability["can_buy"]
        else "不可买入，等待重新进入可成交区"
    )


def _reasoning(
    action: str,
    state: dict[str, Any],
    market_sentiment: dict[str, Any],
    strength: float,
    quality: dict[str, Any],
    tradability: dict[str, Any],
    selection_mode: str,
    phase: dict[str, Any],
) -> str:
    sentiment_score = _sentiment_score(market_sentiment, state)
    trade_permission = market_sentiment.get("trade_permission", "BUY_ALLOWED")
    if action == "BUY":
        return (
            f"阶段{phase.get('stage')}，模式{selection_mode}，市场{state.get('state')}，龙头强度{strength:.0f}，"
            f"情绪{sentiment_score:.0f}/{trade_permission}，"
            f"质地{quality['score']:.0f}，可买性{tradability['score']:.0f}，满足小仓试错条件"
        )
    if action == "WATCH":
        return f"模式{selection_mode}尚未完成买点确认，或当前阶段{phase.get('stage')}只适合观察"
    return "未通过可买性、基础质地或市场时机过滤"


def _risk_flags(
    quality: dict[str, Any],
    tradability: dict[str, Any],
    market_sentiment: dict[str, Any],
    selection_mode: str,
    phase: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if not quality["passed"]:
        flags.append("quality_filtered")
    if not tradability["can_buy"]:
        flags.append("tradability_filtered")
    if tradability["is_limit_up"]:
        flags.append("limit_up")
    elif tradability["near_limit_up"]:
        flags.append("near_limit_up")
    for flag in market_sentiment.get("flags") or []:
        if flag in {"low_sentiment_coverage", "panic_pressure", "sentiment_no_buy", "sentiment_light_only"}:
            flags.append(flag)
    if selection_mode in {"breakout", "pullback", "overheated", "filtered"}:
        flags.append(f"mode_{selection_mode}")
    stage = str(phase.get("stage") or "")
    if stage in {"risk_off", "overheat", "cold"}:
        flags.append(f"stage_{stage}")
    return flags


def _no_recommendation_reason(
    market_state: str,
    market_sentiment: dict[str, Any],
    watchlist: list[dict[str, Any]],
    phase: dict[str, Any],
) -> str:
    trade_permission = str(market_sentiment.get("trade_permission") or "").upper()
    if trade_permission == "NO_BUY":
        return _sentiment_no_buy_reason(market_sentiment)
    stage = str(phase.get("stage") or "")
    if stage == "overheat":
        return "盘面热度偏高，系统不追已经拥挤的方向，只保留观察"
    if stage == "cold":
        return "盘面承接不足，候选股没有形成稳定买点，等待下一轮"
    if trade_permission == "LIGHT_ONLY":
        return "市场情绪只允许轻仓观察，当前标的尚未达到更高买入门槛"
    if market_state != "UPTREND":
        return f"市场状态为{market_state}，系统只观察不新增买入"
    if watchlist:
        return "有观察标的，但尚未同时满足时机、质地、流动性和可买价格"
    return "候选池没有通过涨停过滤、质地过滤和成交额过滤的标的"


def _sentiment_score(market_sentiment: dict[str, Any], state: dict[str, Any]) -> float:
    try:
        return float(market_sentiment.get("sentiment_score") or state.get("sentiment") or 50)
    except (TypeError, ValueError):
        return 50.0


def _market_phase_profile(state: dict[str, Any], market_sentiment: dict[str, Any]) -> dict[str, Any]:
    market_state = str(state.get("state") or "SIDEWAYS").upper()
    sentiment_score = _sentiment_score(market_sentiment, state)
    panic_score = _float(market_sentiment.get("panic_score"), 0.0)
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    trade_permission = str(market_sentiment.get("trade_permission") or "BUY_ALLOWED").upper()
    breadth = _float(market_sentiment.get("breadth"), 0.5)
    avg_pct_change = _float(market_sentiment.get("avg_pct_change"), 0.0)
    volume_active_ratio = _float(market_sentiment.get("volume_active_ratio"), 0.0)
    active_ratio = _float(market_sentiment.get("active_ratio"), 0.0)
    coverage_count = _int(market_sentiment.get("coverage_count"), 0)
    heat_count = _int(market_sentiment.get("limit_up_count"), 0) + _int(
        market_sentiment.get("near_limit_up_count"), 0
    )
    strong_up_count = _int(market_sentiment.get("strong_up_count"), 0)
    heat_ratio = heat_count / max(coverage_count, 1)

    phase = {
        "stage": "transition",
        "reason": "市场处于可观察到可试错之间，优先等待回踩买点",
        "breakout_weight_scale": 0.75,
        "pullback_weight_scale": 1.05,
        "position_multiplier": 0.65,
        "rank_adjustment": -1.5,
        "max_recommendations": min(settings.max_push_stocks, 2),
        "breadth": round(breadth, 4),
        "avg_pct_change": round(avg_pct_change, 4),
        "volume_active_ratio": round(volume_active_ratio, 4),
        "active_ratio": round(active_ratio, 4),
    }

    if market_state in {"DOWNTREND", "NO_DATA"} or trade_permission == "NO_BUY" or panic_score >= panic_threshold:
        phase.update(
            stage="risk_off",
            reason="趋势或情绪风控不允许新增买入",
            breakout_weight_scale=0.0,
            pullback_weight_scale=0.0,
            position_multiplier=0.0,
            rank_adjustment=-18.0,
            max_recommendations=0,
        )
    elif (
        sentiment_score >= 72
        and avg_pct_change >= 1.8
        and (heat_ratio >= 0.08 or heat_count >= 3 or strong_up_count >= max(4, coverage_count * 0.18))
    ):
        phase.update(
            stage="overheat",
            reason="短线热度偏拥挤，避免追在情绪高潮处",
            breakout_weight_scale=0.45,
            pullback_weight_scale=0.85,
            position_multiplier=0.45,
            rank_adjustment=-6.0,
            max_recommendations=1,
        )
    elif (
        market_state == "UPTREND"
        and sentiment_score >= 60
        and breadth >= 0.55
        and panic_score < 45
        and (volume_active_ratio >= 0.2 or active_ratio >= 0.45)
    ):
        phase.update(
            stage="confirmed",
            reason="趋势、情绪和成交活跃度同步改善，允许小仓试错",
            breakout_weight_scale=1.15,
            pullback_weight_scale=0.95,
            position_multiplier=1.0,
            rank_adjustment=2.0,
            max_recommendations=settings.max_push_stocks,
        )
    elif sentiment_score < 48 or panic_score >= 55 or breadth < 0.42:
        phase.update(
            stage="cold",
            reason="盘面承接不足，买点需要更高确认度",
            breakout_weight_scale=0.45,
            pullback_weight_scale=0.65,
            position_multiplier=0.35,
            rank_adjustment=-8.0,
            max_recommendations=1,
        )

    if trade_permission == "LIGHT_ONLY":
        phase["position_multiplier"] = float(phase["position_multiplier"]) * 0.55
        phase["rank_adjustment"] = float(phase["rank_adjustment"]) - 2.0
        phase["max_recommendations"] = min(int(phase["max_recommendations"]), 1)
        if phase["stage"] == "confirmed":
            phase["stage"] = "transition"
            phase["reason"] = "情绪只允许轻仓，突破信号也需要降级验证"

    return phase


def _selection_mode(
    quote: Quote,
    market_state: str,
    market_sentiment: dict[str, Any],
    quality: dict[str, Any],
    tradability: dict[str, Any],
    strength: float,
    phase: dict[str, Any],
) -> str:
    if not quality["passed"] or not tradability["can_buy"]:
        return "filtered"
    if tradability["is_limit_up"] or tradability["near_limit_up"]:
        return "overheated"
    if quote.pct_change >= min(settings.max_recommend_pct_change, 6.8):
        return "overheated"

    recovery = _intraday_recovery(quote)
    distance_from_high = _distance_from_high_pct(quote)
    volume_ratio = max(quote.volume_ratio, 0.0)
    stage = str(phase.get("stage") or "transition")
    sentiment_score = _sentiment_score(market_sentiment, {})

    if (
        market_state == "UPTREND"
        and stage in {"confirmed", "transition"}
        and strength >= 70
        and 1.1 <= quote.pct_change <= 5.8
        and volume_ratio >= 1.12
        and recovery >= 0.58
        and distance_from_high <= 3.5
        and sentiment_score >= settings.sentiment_min_buy_score
    ):
        return "breakout"

    if (
        stage in {"confirmed", "transition"}
        and strength >= 64
        and -2.8 <= quote.pct_change <= 2.4
        and volume_ratio >= 0.75
        and recovery >= 0.45
        and distance_from_high >= 0.6
    ):
        return "pullback"

    if strength >= 68 and quote.pct_change > -1.5 and volume_ratio >= 0.9:
        return "watch_strength"
    return "watch"


def _setup_score(quote: Quote, selection_mode: str) -> float:
    recovery = _intraday_recovery(quote)
    distance_from_high = _distance_from_high_pct(quote)
    volume_score = min(max(quote.volume_ratio - 0.7, 0.0), 2.5) * 8.0
    if selection_mode == "breakout":
        return max(
            0.0,
            min(100.0, 58.0 + min(max(quote.pct_change, 0.0), 5.8) * 4.4 + volume_score + recovery * 12),
        )
    if selection_mode == "pullback":
        return max(
            0.0,
            min(
                100.0,
                62.0
                + recovery * 18.0
                + min(max(2.5 - abs(quote.pct_change), 0.0), 2.5) * 3.0
                + volume_score * 0.65
                - max(distance_from_high - 6.0, 0.0) * 1.2,
            ),
        )
    if selection_mode == "watch_strength":
        return max(0.0, min(100.0, 55.0 + volume_score + recovery * 8.0))
    if selection_mode == "overheated":
        return 42.0
    if selection_mode == "filtered":
        return 30.0
    return 48.0


def _mode_score_adjustment(selection_mode: str, phase: dict[str, Any]) -> float:
    if selection_mode == "breakout":
        return 7.0 * float(phase.get("breakout_weight_scale") or 0.0)
    if selection_mode == "pullback":
        return 6.0 * float(phase.get("pullback_weight_scale") or 0.0)
    if selection_mode == "watch_strength":
        return 1.5
    if selection_mode == "overheated":
        return -10.0
    if selection_mode == "filtered":
        return -12.0
    return -2.0


def _intraday_recovery(quote: Quote) -> float:
    if quote.high > quote.low and quote.price > 0:
        return max(0.0, min(1.0, (quote.price - quote.low) / (quote.high - quote.low)))
    if quote.open > 0 and quote.price >= quote.open:
        return 0.6
    return 0.4


def _distance_from_high_pct(quote: Quote) -> float:
    if quote.high <= 0 or quote.price <= 0:
        return 0.0
    return max(0.0, (quote.high - quote.price) / quote.high * 100)


def _target_take_profit_pct(selection_mode: str, quality: dict[str, Any], phase: dict[str, Any]) -> float:
    quality_score = float(quality.get("score") or 0)
    base = 0.16 if selection_mode == "breakout" else 0.11 if selection_mode == "pullback" else 0.08
    if quality_score >= 82:
        base += 0.03
    if str(phase.get("stage") or "") == "overheat":
        base -= 0.03
    return round(max(0.06, min(base, 0.22)), 4)


def _trailing_stop_pct(selection_mode: str, quality: dict[str, Any], phase: dict[str, Any]) -> float:
    quality_score = float(quality.get("score") or 0)
    base = 0.075 if selection_mode == "breakout" else 0.055 if selection_mode == "pullback" else 0.045
    if quality_score >= 82:
        base += 0.015
    if str(phase.get("stage") or "") == "cold":
        base -= 0.01
    return round(max(0.035, min(base, 0.095)), 4)


def _position_plan(action: str, selection_mode: str, phase: dict[str, Any]) -> str:
    if action != "BUY":
        return "observe_only"
    if selection_mode == "breakout":
        return "small_breakout_probe"
    if selection_mode == "pullback":
        return "pullback_probe_then_add_on_confirmation"
    return f"{phase.get('stage')}_probe"


def _entry_note(action: str, quote: Quote, tradability: dict[str, Any], selection_mode: str) -> str:
    if action != "BUY":
        return "不在买入状态，只跟踪价格和情绪变化"
    buy_low = float(tradability.get("buy_range_low") or 0)
    buy_high = float(tradability.get("buy_range_high") or 0)
    if selection_mode == "pullback" and quote.price > buy_low:
        return "优先等回踩到买入区间中下沿；若继续放量走强，等待下一轮重新确认"
    if quote.price <= buy_high:
        return "当前价仍在系统最高追价以内，可按模拟盘目标仓位分批"
    return "当前价超过最高追价，本轮信号自动失效"


def _bundle_risk_level(
    recommendations: list[dict[str, Any]],
    phase: dict[str, Any],
    market_sentiment: dict[str, Any],
) -> str:
    if not recommendations:
        return "high" if str(phase.get("stage") or "") in {"risk_off", "cold"} else "normal"
    if str(phase.get("stage") or "") in {"transition", "overheat"}:
        return "medium"
    if str(market_sentiment.get("trade_permission") or "").upper() == "LIGHT_ONLY":
        return "medium"
    return "normal"


def _sentiment_blocks_new_buys(market_sentiment: dict[str, Any]) -> bool:
    permission = str(market_sentiment.get("trade_permission") or "").upper()
    panic_score = float(market_sentiment.get("panic_score") or 0)
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    return permission == "NO_BUY" or panic_score >= panic_threshold


def _sentiment_no_buy_reason(market_sentiment: dict[str, Any]) -> str:
    status = market_sentiment.get("sentiment_status", "unknown")
    score = _sentiment_score(market_sentiment, {})
    panic = float(market_sentiment.get("panic_score") or 0)
    flags = ",".join(market_sentiment.get("flags") or [])
    return f"市场情绪为{status}，情绪分{score:.0f}，恐慌分{panic:.0f}，暂停新增买入；标记：{flags or 'none'}"


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
