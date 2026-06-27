from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime
from typing import Any
from uuid import uuid4

import requests

from ai.prompt_builder import build_review_prompt
from app.config import settings
from notify.feishu import send_feishu_markdown
from scheduler.trading_calendar import BEIJING_TZ, is_trading_day
from storage.logger import fetch_daily_review_inputs, log_review


logger = logging.getLogger(__name__)


def run_daily_review(trigger_source: str = "scheduler", review_date: date | None = None) -> str | None:
    target_date = review_date or datetime.now(BEIJING_TZ).date()
    if trigger_source != "manual" and not is_trading_day(datetime.combine(target_date, datetime.min.time(), tzinfo=BEIJING_TZ)):
        logger.info("skip daily review: %s is not configured trading day", target_date)
        return None

    review_id = uuid4().hex
    try:
        article = generate_daily_review_article(target_date)
        push_result = send_feishu_markdown(article["markdown"], title=article["title"])
        log_review(
            review_id=review_id,
            review_date=target_date.isoformat(),
            status="ok" if push_result.get("status") in {"sent", "dry_run_skipped", "disabled"} else "push_failed",
            title=article["title"],
            article_markdown=article["markdown"],
            summary=article["summary"],
            push_result=push_result,
        )
        logger.info("daily review %s finished: status=%s", review_id, push_result.get("status"))
        return review_id
    except Exception as exc:
        logger.exception("daily review %s failed", review_id)
        log_review(
            review_id=review_id,
            review_date=target_date.isoformat(),
            status="failed",
            title="A股盘后复盘",
            article_markdown="",
            summary={},
            error_text=str(exc),
        )
        return review_id


def generate_daily_review_article(review_date: date) -> dict[str, Any]:
    raw = fetch_daily_review_inputs(review_date)
    summary = _summarize(raw)
    prompt = build_review_prompt(summary, settings.review_target_chars)
    markdown, ai_error = _call_minimax_text(prompt)
    if not markdown:
        markdown = _fallback_article(summary)
    title = _extract_title(markdown) or f"{review_date.isoformat()} A股盘后复盘"
    return {
        "title": title,
        "markdown": markdown.strip(),
        "summary": summary,
        "ai_error": ai_error,
    }


def _summarize(raw: dict[str, Any]) -> dict[str, Any]:
    cycles = raw["cycles"]
    decisions = raw["decisions"]
    pushes = raw["pushes"]

    states: list[str] = []
    sentiments: list[float] = []
    top_recommendations: list[dict[str, Any]] = []
    no_recommendation_reasons: Counter[str] = Counter()

    for row in cycles:
        payload = _loads(row.get("payload_json"))
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        final_signal = payload.get("final_signal") if isinstance(payload.get("final_signal"), dict) else {}
        if state.get("state"):
            states.append(str(state["state"]))
        if state.get("sentiment") is not None:
            sentiments.append(float(state.get("sentiment") or 0))
        reason = final_signal.get("no_recommendation_reason")
        if reason:
            no_recommendation_reasons[str(reason)] += 1

    for row in decisions[:10]:
        payload = _loads(row.get("payload_json"))
        if str(row.get("action") or "").upper() in {"BUY", "WATCH"}:
            top_recommendations.append(
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "action": row.get("action"),
                    "confidence": row.get("confidence"),
                    "target_weight": row.get("target_weight"),
                    "reason": row.get("reason"),
                    "buy_range": payload.get("buy_range"),
                    "max_buy_price": payload.get("max_buy_price"),
                    "stop_loss": payload.get("stop_loss"),
                }
            )

    status_counts = Counter(str(row.get("status")) for row in cycles)
    push_counts = Counter(str(row.get("status")) for row in pushes)
    action_counts = Counter(str(row.get("action")) for row in decisions)
    return {
        "review_date": raw["review_date"],
        "cycle_count": len(cycles),
        "cycle_status": dict(status_counts),
        "market_states": dict(Counter(states)),
        "avg_sentiment": round(sum(sentiments) / len(sentiments), 2) if sentiments else 0,
        "max_sentiment": round(max(sentiments), 2) if sentiments else 0,
        "decision_count": len(decisions),
        "actions": dict(action_counts),
        "top_recommendations": top_recommendations,
        "no_recommendation_reasons": dict(no_recommendation_reasons.most_common(5)),
        "push_status": dict(push_counts),
    }


def _call_minimax_text(prompt: str) -> tuple[str, str | None]:
    if settings.dry_run:
        return "", "dry_run"
    if not settings.minimax_api_key or not settings.minimax_endpoint:
        return "", "missing_minimax_config"
    try:
        response = requests.post(
            settings.minimax_endpoint,
            headers={
                "Authorization": f"Bearer {settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.minimax_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.45,
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        text = _extract_text(payload)
        return text.strip(), None
    except Exception as exc:
        return "", str(exc)


def _fallback_article(summary: dict[str, Any]) -> str:
    date_text = summary.get("review_date")
    cycle_count = summary.get("cycle_count", 0)
    top_recommendations = summary.get("top_recommendations") or []
    no_reasons = summary.get("no_recommendation_reasons") or {}
    market_states = summary.get("market_states") or {}
    actions = summary.get("actions") or {}

    lines = [
        f"# {date_text} A股盘后复盘：该出手时再出手",
        "",
        "## 今日盘面",
        f"今天系统共完成 {cycle_count} 次盘中感知。市场状态分布为：{market_states or '暂无有效状态'}，平均情绪值 {summary.get('avg_sentiment', 0)}。这个数字不负责热血，只负责提醒我们：盘面配不配得上出手。",
        "",
        "## 系统观察",
        f"盘中动作统计：{actions or '暂无动作'}。如果没有买入推荐，并不是系统偷懒，而是可买性、涨停距离、基础质地和市场时机没有同时亮绿灯。",
        "",
        "## 候选股复盘",
    ]
    if top_recommendations:
        for item in top_recommendations[:5]:
            lines.append(
                f"- {item.get('symbol')} {item.get('name')}：{item.get('action')}，"
                f"买入区间 {item.get('buy_range') or '-'}，理由：{item.get('reason') or '系统综合评分靠前'}。"
            )
    else:
        lines.append("- 今日没有形成稳定的可买推荐。市场不给球，就别硬起脚，纪律有时候比灵感值钱。")

    lines.extend(
        [
            "",
            "## 风险提醒",
            f"主要未推荐原因：{no_reasons or '无明显异常记录'}。接近涨停、流动性不足、基础质地不过关，都会让系统把股票从推荐池里请出去。",
            "",
            "## 明日观察",
            "明天继续看三件事：市场情绪是否继续修复，龙头是否保持强度，以及候选股能不能给出不追高也能成交的价格。真正好的交易，往往不是买得多勇，而是等得够稳。",
        ]
    )
    return "\n".join(lines)


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(choices[0].get("text") or "")
    for key in ("reply", "output_text", "content"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _extract_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
