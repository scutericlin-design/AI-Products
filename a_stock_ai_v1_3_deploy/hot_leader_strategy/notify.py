from __future__ import annotations

from typing import Any
import requests

from hot_leader_strategy.config import HotLeaderSettings

def send_execution_report(result:dict[str,Any],settings:HotLeaderSettings)->dict[str,Any]:
    filled=[x for x in result.get("orders",[]) if x.get("status")=="filled"]
    if not filled:return {"status":"skipped_no_hot_leader_fills"}
    text="热点龙头独立模拟盘成交\n说明：仅模拟，不连接券商。"+"".join(f"\n\n{'买入' if x['side']=='BUY' else '卖出'}：{x['symbol']} {x['name']}\n触发/成交：{float(x.get('trigger_price') or 0):.2f} / {x['price']:.2f}\n仓位：{float(x.get('target_weight') or 0):.1%}｜止损：{float(x.get('stop_loss') or 0):.2f}｜止盈：{float(x.get('take_profit') or 0):.2f}\n理由：{x['reason']}" for x in filled)
    payload={"msg_type":"text","content":{"text":text}}
    if not settings.push_enabled:return {"status":"disabled","payload":payload}
    if not settings.feishu_webhook_url:return {"status":"skipped_no_webhook","payload":payload}
    if settings.dry_run:return {"status":"dry_run_skipped","payload":payload}
    try:
        response=requests.post(settings.feishu_webhook_url,json=payload,timeout=10); return {"status":"sent" if response.ok else "failed","response_code":response.status_code}
    except Exception as exc:return {"status":"failed","reason":str(exc)}
