from __future__ import annotations

from typing import Any

import requests

from qgarp_strategy.config import QGARPSettings


def send_qgarp_execution_report(result: dict[str, Any], settings: QGARPSettings) -> dict[str, Any]:
    """ETF-style low-interruption push: only locally filled Q-GARP paper orders are eligible."""
    filled = [item for item in list(result.get("orders") or []) if str(item.get("status") or "").lower() == "filled"]
    if not filled:
        return {"status": "skipped_no_qgarp_filled_orders", "payload": {}}
    text = _format(filled)
    payload = {"msg_type": "text", "content": {"text": text}}
    if not settings.push_enabled:
        return {"status": "disabled", "payload": payload}
    if not settings.feishu_webhook_url:
        return {"status": "skipped_no_webhook", "payload": payload}
    if settings.dry_run:
        return {"status": "dry_run_skipped", "payload": payload}
    try:
        response = requests.post(settings.feishu_webhook_url, json=payload, timeout=10)
        return {"status": "sent" if response.ok else "failed", "response_code": response.status_code, "payload": payload}
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "payload": payload}


def _format(orders: list[dict[str, Any]]) -> str:
    lines = ["Q-GARP 独立模拟盘成交", "说明：本模块仅本地模拟，不连接券商、不执行真实委托。"]
    for item in orders:
        side = "买入" if str(item.get("side") or "").upper() == "BUY" else "卖出"
        lines.extend(["", f"{side}：{item.get('symbol')} {item.get('name')}", f"触发/成交价：{float(item.get('trigger_price') or item.get('price') or 0):.2f} / {float(item.get('price') or 0):.2f}", f"数量：{int(item.get('quantity') or 0)} 股 | 目标仓位：{float(item.get('target_weight') or 0):.1%}", f"止损：{float(item.get('stop_loss') or 0):.2f} | 止盈：{float(item.get('take_profit') or 0):.2f}", f"策略理由：{item.get('reason') or '-'}"])
    return "\n".join(lines)
