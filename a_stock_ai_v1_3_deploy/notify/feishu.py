from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.config import FEISHU_WEBHOOK, settings
from learning.strategy_params import param_int
from notify.push_guard import (
    evaluate_intraday_push,
    mark_heartbeat_sent,
    mark_intraday_push_sent,
    should_send_heartbeat,
)


def send_feishu(final_signal: dict[str, Any]) -> dict[str, Any]:
    if (
        not settings.push_no_recommendation
        and str(final_signal.get("signal", "HOLD")).upper() == "HOLD"
        and not final_signal.get("recommendations")
        and not _should_alert_no_data(final_signal)
    ):
        heartbeat = should_send_heartbeat(final_signal)
        if heartbeat.should_send:
            push_result = send_feishu_text(_format_heartbeat_message(final_signal))
            push_result["dedupe"] = {
                "reason": heartbeat.reason,
                "fingerprint": heartbeat.fingerprint,
                "periodic_summary": False,
            }
            if push_result.get("status") in {"sent", "dry_run_skipped", "disabled"}:
                mark_heartbeat_sent(heartbeat)
            return push_result
        return {"status": "skipped_no_recommendation", "payload": {}}

    decision = evaluate_intraday_push(final_signal)
    if not decision.should_send:
        return {
            "status": "skipped_duplicate_intraday",
            "reason": decision.reason,
            "fingerprint": decision.fingerprint,
            "payload": {"summary": decision.summary},
        }

    message = _format_intraday_message(final_signal)
    if decision.periodic_summary:
        message = "状态摘要：盘中推荐未发生实质变化，以下为最新快照。\n\n" + message

    push_result = send_feishu_text(message)
    push_result["dedupe"] = {
        "reason": decision.reason,
        "fingerprint": decision.fingerprint,
        "periodic_summary": decision.periodic_summary,
    }
    if push_result.get("status") in {"sent", "dry_run_skipped", "disabled"}:
        mark_intraday_push_sent(decision)
    return push_result


def send_feishu_text(text: str) -> dict[str, Any]:
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if not settings.feishu_enabled:
        return {"status": "disabled", "payload": payload}
    webhook = settings.feishu_webhook_url or FEISHU_WEBHOOK
    if not webhook:
        return {"status": "skipped_no_webhook", "payload": payload}
    if settings.dry_run and not settings.send_in_dry_run:
        return {"status": "dry_run_skipped", "payload": payload}

    try:
        response = requests.post(webhook, json=payload, timeout=10)
        return {
            "status": "sent" if response.ok else "failed",
            "response_code": response.status_code,
            "response_text": response.text[:2000],
            "payload": payload,
        }
    except Exception as exc:
        return {"status": "failed", "response_text": str(exc), "payload": payload}


def send_feishu_markdown(markdown: str, title: str = "A股盘后复盘") -> dict[str, Any]:
    return send_feishu_text(f"{title}\n\n{markdown}")


class FeishuNotifier:
    def send(self, final_signal: dict[str, Any]) -> dict[str, Any]:
        return send_feishu(final_signal)


def _format_intraday_message(final_signal: dict[str, Any]) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    state = final_signal.get("state") or {}
    sentiment = final_signal.get("market_sentiment") if isinstance(final_signal.get("market_sentiment"), dict) else {}
    lines = [
        f"{settings.engine_name} 盘中感知",
        f"时间：{now}",
        f"整体信号：{final_signal.get('signal', 'HOLD')} | 仓位：{float(final_signal.get('position') or 0):.2%} | 风险：{final_signal.get('risk_level', 'normal')}",
        f"市场：{state.get('state', 'unknown')} | 情绪：{state.get('sentiment', 0)} | 成交：{state.get('volume', 'unknown')}",
        f"阶段：{final_signal.get('market_stage') or 'unknown'} | {final_signal.get('market_stage_reason') or '等待盘面确认'}",
        (
            "情绪画像："
            f"{sentiment.get('sentiment_status', 'unknown')} | "
            f"风险偏好：{sentiment.get('risk_appetite', 'unknown')} | "
            f"权限：{sentiment.get('trade_permission', 'unknown')} | "
            f"覆盖：{sentiment.get('coverage_count', 0)}只/{sentiment.get('coverage_level', 'unknown')} | "
            f"恐慌：{float(sentiment.get('panic_score') or 0):.0f}"
        ),
        f"逻辑：{final_signal.get('selection_logic') or '市场状态、龙头强度、可买性、质地和风控综合判断'}",
    ]

    recommendations = list(final_signal.get("recommendations") or [])
    if recommendations:
        lines.append("")
        lines.append(f"本轮推荐：{len(recommendations)} 只")
        for index, item in enumerate(recommendations, start=1):
            buy_range = item.get("buy_range") or {}
            lines.extend(
                [
                    "",
                    f"{index}. {item.get('symbol')} {item.get('name')}",
                    (
                        f"当前价：{_price(item.get('current_price'))} | "
                        f"涨幅：{float(item.get('pct_change') or 0):.2f}% | "
                        f"模式：{item.get('selection_mode', 'unknown')} | "
                        f"综合分：{float(item.get('rank_score') or 0):.1f}"
                    ),
                    f"买入区间：{_price(buy_range.get('low'))}-{_price(buy_range.get('high'))} | 最高追价：{_price(item.get('max_buy_price'))} | 止损：{_price(item.get('stop_loss'))}",
                    f"执行：{item.get('buy_mode')}",
                    (
                        f"计划：{item.get('position_plan', 'small_probe')} | "
                        f"止盈观察：{float(item.get('target_take_profit_pct') or 0):.1%} | "
                        f"移动止盈：{float(item.get('trailing_stop_pct') or 0):.1%}"
                    ),
                    f"备注：{item.get('entry_note', '不追高，等待下一轮确认')}",
                    f"理由：{item.get('reasoning')}",
                ]
            )
    else:
        watchlist = list(final_signal.get("watchlist") or [])[: param_int("MAX_PUSH_STOCKS", settings.max_push_stocks)]
        lines.append("")
        lines.append(f"本轮无买入推荐：{final_signal.get('no_recommendation_reason') or '没有满足条件的可买标的'}")
        if watchlist:
            lines.append("观察池：")
            for item in watchlist:
                lines.append(
                    f"- {item.get('symbol')} {item.get('name')}，现价{_price(item.get('current_price'))}，模式{item.get('selection_mode', 'unknown')}，原因：{item.get('reasoning')}"
                )
        lines.append("处理方式：不勉强交易，等待下一轮刷新。")

    if final_signal.get("ai_error"):
        lines.append("")
        lines.append(f"AI层提示：{final_signal.get('ai_error')}")
    return "\n".join(lines)


def _format_heartbeat_message(final_signal: dict[str, Any]) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    state = final_signal.get("state") if isinstance(final_signal.get("state"), dict) else {}
    sentiment = final_signal.get("market_sentiment") if isinstance(final_signal.get("market_sentiment"), dict) else {}
    watchlist = list(final_signal.get("watchlist") or [])[: param_int("MAX_PUSH_STOCKS", settings.max_push_stocks)]
    lines = [
        f"{settings.engine_name} 盘中心跳",
        f"时间：{now}",
        "状态：系统正常运行，本交易半日暂无买入推荐",
        f"阶段：{final_signal.get('market_stage') or 'unknown'} | {final_signal.get('market_stage_reason') or '等待盘面确认'}",
        f"市场：{state.get('state', 'unknown')} | 情绪：{float(sentiment.get('sentiment_score') or 0):.1f} | 权限：{sentiment.get('trade_permission', 'unknown')}",
        f"覆盖：{sentiment.get('coverage_count', 0)}只/{sentiment.get('coverage_level', 'unknown')} | 恐慌：{float(sentiment.get('panic_score') or 0):.0f}",
        f"原因：{final_signal.get('no_recommendation_reason') or '没有满足条件的可买标的'}",
    ]
    if watchlist:
        lines.append("观察池：")
        for item in watchlist:
            lines.append(
                f"- {item.get('symbol')} {item.get('name')}，现价{_price(item.get('current_price'))}，模式{item.get('selection_mode', 'unknown')}，综合分{float(item.get('rank_score') or 0):.1f}"
            )
    lines.append(f"说明：这是低频心跳，不是买入信号；无推荐时约每 {settings.push_heartbeat_interval_minutes} 分钟最多一条。")
    return "\n".join(lines)


def _should_alert_no_data(final_signal: dict[str, Any]) -> bool:
    state = final_signal.get("state") if isinstance(final_signal.get("state"), dict) else {}
    market_state = str(state.get("state") or "").upper()
    phase = str(state.get("phase") or "").lower()
    return market_state == "NO_DATA" and phase in {"morning", "afternoon"}


def _price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"
