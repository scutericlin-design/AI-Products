from __future__ import annotations

from dataclasses import asdict
from typing import Any

import requests

from app.config import settings
from app.trading.types import Decision, MarketState


class FeishuNotifier:
    channel = "feishu"

    def send(self, market_state: MarketState, decisions: list[Decision]) -> dict[str, Any]:
        text = self._format_text(market_state, decisions)
        payload = {"msg_type": "text", "content": {"text": text}}

        if not settings.trading_push_enabled:
            return {"status": "disabled", "payload": payload}
        if not settings.trading_feishu_webhook_url:
            return {"status": "skipped_no_webhook", "payload": payload}
        if settings.trading_dry_run and not settings.trading_send_in_dry_run:
            return {"status": "dry_run_skipped", "payload": payload}

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

    def _format_text(self, market_state: MarketState, decisions: list[Decision]) -> str:
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

    def preview_payload(self, market_state: MarketState, decisions: list[Decision]) -> dict[str, Any]:
        return {"market_state": asdict(market_state), "decisions": [asdict(item) for item in decisions]}
