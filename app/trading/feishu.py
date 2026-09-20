from __future__ import annotations

from dataclasses import asdict
from typing import Any

import requests
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import KnowledgeJob
from app.services.knowledge_orchestrator import FeishuMessenger
from app.trading.types import Decision, MarketState


class FeishuNotifier:
    channel = "feishu"

    def send(self, market_state: MarketState, decisions: list[Decision]) -> dict[str, Any]:
        text = self._format_text(market_state, decisions)
        payload = {"msg_type": "text", "content": {"text": text}}

        if not settings.trading_push_enabled:
            return {"status": "disabled", "payload": payload}
        if settings.trading_dry_run and not settings.trading_send_in_dry_run:
            return {"status": "dry_run_skipped", "payload": payload}
        if not settings.trading_feishu_webhook_url:
            if settings.trading_use_latest_feishu_chat:
                return self._send_to_latest_verified_chat(text, payload)
            return {"status": "skipped_no_webhook", "payload": payload}

        try:
            response = requests.post(settings.trading_feishu_webhook_url, json=payload, timeout=10)
            return {
                "status": "sent" if response.ok else "failed",
                "response_code": response.status_code,
                "response_text": response.text[:2000],
                "payload": payload,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "response_code": None,
                "response_text": str(exc)[:2000],
                "payload": payload,
            }

    def _send_to_latest_verified_chat(self, text: str, payload: dict[str, Any]) -> dict[str, Any]:
        db = SessionLocal()
        try:
            chat_id = db.scalar(
                select(KnowledgeJob.chat_id)
                .where(KnowledgeJob.chat_id.is_not(None))
                .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
                .limit(1)
            )
        finally:
            db.close()
        if not chat_id:
            return {"status": "skipped_no_feishu_chat", "payload": payload}
        result = FeishuMessenger().send_text(text, chat_id=chat_id)
        return {**result, "payload": payload}

    def _format_text(self, market_state: MarketState, decisions: list[Decision]) -> str:
        if any(item.raw.get("plan_version") for item in decisions):
            return self._format_personal_plan(market_state, decisions)
        lines = [
            "[A-Share Trading Engine v1.3]",
            f"Phase: {market_state.phase} | Regime: {market_state.regime} | Breadth: {market_state.breadth:.2%}",
            f"Avg change: {market_state.avg_pct_change:.2f}% | Amount: {market_state.total_amount_yi:.2f} yi",
        ]
        if not decisions:
            lines.append("No actionable leaders in this cycle.")
            return "\n".join(lines)

        for decision in decisions[:8]:
            lines.append(
                f"{decision.symbol} {decision.name}: {decision.action} "
                f"conf={decision.confidence:.2f} target={decision.target_weight:.2%} "
                f"risk={decision.risk_level}"
            )
        return "\n".join(lines)

    def _format_personal_plan(self, market_state: MarketState, decisions: list[Decision]) -> str:
        actionable = [item for item in decisions if item.action in {"buy", "add", "reduce", "exit"}]
        rows = actionable or decisions
        lines = [
            "【个人五年投资计划｜需人工确认】",
            f"市场状态：{market_state.regime}｜数据：{market_state.quote_count}只｜阶段：{market_state.phase}",
            "系统不连接券商，也不会自动下单。请先核对实时行情、公告、账户现金与持仓。",
        ]
        labels = {"buy": "买入", "add": "加仓", "reduce": "减仓", "exit": "退出", "hold": "持有", "observe": "观察"}
        for item in rows[:10]:
            raw = item.raw or {}
            planned_shares = raw.get("planned_shares")
            amount = raw.get("planned_amount")
            limit_price = raw.get("max_limit_price")
            order = ""
            if planned_shares:
                order = f"；计划 {planned_shares:.0f}股，金额约{amount:.0f}元，限价不高于{limit_price:.2f}元"
            lines.append(f"{item.name}（{item.symbol}）：{labels.get(item.action, item.action)}{order}\n理由：{item.reason}")
        return "\n".join(lines)

    def preview_payload(self, market_state: MarketState, decisions: list[Decision]) -> dict[str, Any]:
        return {"market_state": asdict(market_state), "decisions": [asdict(item) for item in decisions]}
