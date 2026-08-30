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


def send_feishu_execution_report(paper_result: dict[str, Any] | None) -> dict[str, Any]:
    """Pushes only paper-trading orders that the simulator actually filled."""
    if not isinstance(paper_result, dict):
        return {"status": "skipped_no_paper_result", "payload": {}}

    filled_orders = [
        order
        for order in (paper_result.get("orders") or [])
        if isinstance(order, dict) and str(order.get("status") or "").lower() == "filled"
    ]
    if not filled_orders:
        return {
            "status": "skipped_no_filled_orders",
            "reason": str(paper_result.get("status") or "no_filled_orders"),
            "payload": {},
        }

    return send_feishu_text(_format_execution_message(filled_orders))


def send_feishu_etf_execution_report(etf_result: dict[str, Any] | None) -> dict[str, Any]:
    """Push only filled ETF paper orders; silence all holds and observations."""
    if not isinstance(etf_result, dict):
        return {"status": "skipped_no_etf_result", "payload": {}}
    filled_orders = [
        order
        for order in (etf_result.get("orders") or [])
        if isinstance(order, dict) and str(order.get("status") or "").lower() == "filled"
    ]
    if not filled_orders:
        return {
            "status": "skipped_no_etf_filled_orders",
            "reason": str(etf_result.get("status") or "no_etf_filled_orders"),
            "payload": {},
        }
    return send_feishu_text(_format_etf_execution_message(filled_orders))


def send_feishu_etf_event_report(etf_result: dict[str, Any] | None) -> dict[str, Any]:
    """Push source-file ETF recommendations and actual simulated fills.

    The V7 source workflow determines the target at 13:08, then performs its
    own 13:09/13:10+ execution callbacks. A target notification therefore is a
    recommendation, not an already-filled order. Every other ETF minute stays
    silent to preserve the requested low-noise notification policy.
    """
    execution = send_feishu_etf_execution_report(etf_result)
    if execution.get("status") not in {"skipped_no_etf_filled_orders", "skipped_no_etf_result"}:
        return execution
    if not isinstance(etf_result, dict):
        return execution

    plan = etf_result.get("plan") if isinstance(etf_result.get("plan"), dict) else {}
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    detail = plan.get("regime_detail") if isinstance(plan.get("regime_detail"), dict) else {}
    source_stage = str(detail.get("source_schedule_stage") or "")
    if source_stage != "13:08" or not str(target.get("symbol") or ""):
        return {
            "status": "skipped_no_etf_event",
            "reason": str(etf_result.get("status") or "no_etf_event"),
            "payload": {},
        }
    return send_feishu_text(_format_etf_recommendation_message(plan))


def send_feishu_market_status(cycle_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Send the scheduled market-status brief without creating an order signal."""
    if not isinstance(cycle_payload, dict):
        return {"status": "skipped_no_market_status_payload", "payload": {}}
    return send_feishu_text(_format_market_status_message(cycle_payload))


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


def _format_execution_message(orders: list[dict[str, Any]]) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"{settings.engine_name} 模拟盘成交回报",
        f"时间：{now}",
        "说明：以下为云端模拟盘已成交订单，系统未接入券商实盘。",
    ]
    if any(bool(order.get("ai_degraded")) for order in orders):
        lines.append(
            "AI执行：降级模式。StepFun与MiniMax均不可用或未配置，"
            "按Hybrid Alpha规则引擎的原信号、原仓位和硬风控执行。"
        )

    for index, order in enumerate(orders, start=1):
        side = str(order.get("side") or "").upper()
        symbol = str(order.get("symbol") or "UNKNOWN")
        name = str(order.get("name") or symbol)
        quantity = int(order.get("quantity") or 0)
        price = _price(order.get("price"))
        strategy_id = str(order.get("strategy_id") or "legacy")
        lines.append("")
        if side == "BUY":
            lines.extend(
                [
                    f"{index}. 买入：{symbol} {name}",
                    f"成交价：{price} | 成交数量：{quantity} 股",
                    f"本次买入比例（账户权益）：{_percent(order.get('executed_portfolio_weight'))}",
                    f"成交后该股仓位：{_percent(order.get('post_trade_portfolio_weight'))} | 策略：{strategy_id}",
                ]
            )
        elif side == "SELL":
            lines.extend(
                [
                    f"{index}. 卖出：{symbol} {name}",
                    f"成交价：{price} | 成交数量：{quantity} 股",
                    f"本次卖出比例（该股持仓）：{_percent(order.get('executed_position_ratio'))}",
                    f"卖出金额占账户权益：{_percent(order.get('executed_portfolio_weight'))} | 策略：{strategy_id}",
                ]
            )
        else:
            lines.append(f"{index}. 成交：{symbol} {name} | 数量：{quantity} 股 | 价格：{price}")
    return "\n".join(lines)


def _format_etf_execution_message(orders: list[dict[str, Any]]) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    lines = ["ETF模拟盘成交", f"时间：{now}"]
    if any(order.get("ai_degraded") for order in orders):
        lines.extend(
            [
                "AI执行：降级模式。StepFun与MiniMax均不可用或未配置，按ETF策略原信号、原仓位和硬风控执行。",
            ]
        )
    for order in orders:
        side = "买入" if str(order.get("side") or "").upper() == "BUY" else "卖出"
        lines.extend(
            [
                "",
                f"{side}ETF：{order.get('symbol')} {order.get('name')}",
                f"成交价：{_price(order.get('price'))}",
                f"成交份额：{int(order.get('quantity') or 0)} 份",
            ]
        )
    return "\n".join(lines)


def _format_etf_recommendation_message(plan: dict[str, Any]) -> str:
    """Render the single 13:08 source-strategy target for Feishu."""
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    symbol = str(target.get("symbol") or "UNKNOWN")
    name = str(target.get("name") or symbol)
    snapshot_price = target.get("latest_price", target.get("price"))
    candidates = [item for item in (plan.get("candidates") or []) if isinstance(item, dict)]
    selected = next((item for item in candidates if str(item.get("symbol") or "") == symbol), target)
    lines = [
        "ETF策略推荐",
        f"时间：{now}",
        f"目标ETF：{symbol} {name}",
        f"13:08快照价：{_price(snapshot_price)}",
        f"市场状态：{'弱市全球池' if str(plan.get('regime') or '') == 'weak' else '常规固定池'}",
    ]
    score = selected.get("momentum_score", selected.get("score"))
    r_squared = selected.get("r_squared")
    if score is not None or r_squared is not None:
        lines.append(f"趋势评分：{float(score or 0):.4f} | R2：{float(r_squared or 0):.4f}")
    lines.extend(
        [
            "执行安排：13:09 如需切换先卖出；13:10 起按文件策略分钟趋势确认买入，未确认则按既定复核时点处理。",
            "说明：这是策略推荐，不代表已成交；实际买入或卖出将另行发送成交回报。",
        ]
    )
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


def _format_market_status_message(cycle_payload: dict[str, Any]) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    state = cycle_payload.get("state") if isinstance(cycle_payload.get("state"), dict) else {}
    sentiment = (
        cycle_payload.get("market_sentiment")
        if isinstance(cycle_payload.get("market_sentiment"), dict)
        else {}
    )
    leader = cycle_payload.get("leader") if isinstance(cycle_payload.get("leader"), dict) else {}
    bundle = (
        cycle_payload.get("recommendation_bundle")
        if isinstance(cycle_payload.get("recommendation_bundle"), dict)
        else {}
    )
    final_signal = (
        cycle_payload.get("final_signal") if isinstance(cycle_payload.get("final_signal"), dict) else {}
    )
    paper = cycle_payload.get("paper_simulation") if isinstance(cycle_payload.get("paper_simulation"), dict) else {}
    ai_error = str((cycle_payload.get("ai_result") or {}).get("ai_error") or "")
    ai_degraded = bool((cycle_payload.get("ai_result") or {}).get("ai_degraded"))
    candidate_count = int(bundle.get("candidate_count") or 0)
    rule_recommendation_count = int(bundle.get("recommendation_count") or 0)
    final_recommendation_count = int(final_signal.get("recommendation_count") or 0)
    leader_text = "暂无有效龙头"
    if leader.get("stock"):
        leader_text = (
            f"{leader.get('stock')} {leader.get('name') or ''} | "
            f"强度：{float(leader.get('strength') or 0):.0f} | "
            f"涨幅：{float(leader.get('pct_change') or 0):.2f}%"
        )
    ai_status = "AI复核正常"
    if ai_degraded:
        ai_status = "AI复核异常，Hybrid Alpha规则引擎降级执行（仓位维持规则计算结果）"
    elif ai_error:
        ai_status = "AI复核异常，当前保持防守"

    lines = [
        f"{settings.engine_name} 市场状态",
        f"时间：{now}",
        f"市场：{state.get('state', 'unknown')} | 阶段：{state.get('phase', 'unknown')} | 成交：{state.get('volume', 'unknown')}",
        (
            f"情绪：{float(sentiment.get('sentiment_score') or state.get('sentiment') or 0):.1f} | "
            f"{sentiment.get('sentiment_status', 'unknown')} | "
            f"权限：{sentiment.get('trade_permission', 'unknown')} | "
            f"恐慌：{float(sentiment.get('panic_score') or 0):.0f}"
        ),
        f"龙头：{leader_text}",
        f"股票筛选：扫描 {candidate_count} 只 | 规则可买 {rule_recommendation_count} 只 | 当前执行 {final_recommendation_count} 只",
        (
            f"执行状态：{final_signal.get('signal', 'HOLD')} | "
            f"建议总仓位：{float(final_signal.get('position') or 0):.2%} | "
            f"模拟成交：{int((paper.get('summary') or {}).get('filled_orders') or 0)} 笔"
        ),
        f"AI：{ai_status}",
        "ETF：分钟策略独立运行，ETF成交将单独推送。",
        "说明：这是每30分钟市场状态简报，不构成买卖指令；成交仍以独立成交回报为准。",
    ]
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


def _percent(value: Any) -> str:
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "-"
