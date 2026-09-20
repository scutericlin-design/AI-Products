"""Persistent buy-only outbox. Unknown delivery is held for reconciliation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
import requests

from app.config import settings
from app.models import PlatformSetting
from app.services.knowledge_orchestrator import FeishuMessenger
from app.services.timezone import now_beijing
from app.trading.durable_plan import ALERT_KEY


def read_ledger(db):
    row = db.get(PlatformSetting, ALERT_KEY)
    return json.loads(row.value) if row else {}


def save_ledger(db, ledger):
    row = db.get(PlatformSetting, ALERT_KEY)
    value = json.dumps(ledger, ensure_ascii=False, allow_nan=False)
    if row:
        row.value = value
    else:
        db.add(PlatformSetting(key=ALERT_KEY, value=value))
    db.commit()


def message(items):
    lines = ['【买入条件已达到｜20万元长期投资计划】',
             '以下是人工限价买入建议；不自动下单。行情时点后5分钟内有效，过期不追价。']
    for d in items:
        r = d.raw
        lines += [f'{d.name} {d.symbol}：{"加仓" if d.action == "add" else "首买"} {r["planned_shares"]}股',
                  f'最高限价 {r["max_limit_price"]:.2f}元；金额≤{r["planned_amount"]:.2f}元（另预留费用）',
                  d.reason, f'行情时间：{r["quote_as_of"]}；财报期：{r["review_period"]}',
                  f'信号编号：{r["signal_id"]}']
    lines += ['下单前核对最新公告、实时价格、账户与交易权限；价格高于限价即放弃。',
              '已占用对应计划预算。买入后或决定不买时，请在本投资任务反馈股数、成交价和剩余现金，核对后解锁预算。',
              '阈值是研究规则，未经收益回测；不保证盈利或将回撤限制在30%。']
    return '\n'.join(lines)


def deliver(plan, text, uuid):
    payload = {'msg_type': 'text', 'content': {'text': text}}
    try:
        if settings.trading_feishu_webhook_url:
            r = requests.post(settings.trading_feishu_webhook_url, json=payload, timeout=12)
        elif plan.get('feishu_chat_id') and settings.feishu_bot_app_id and settings.feishu_bot_app_secret:
            token = FeishuMessenger()._tenant_token()
            r = requests.post('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
                              headers={'Authorization': 'Bearer ' + token}, json={
                                  'receive_id': plan['feishu_chat_id'], 'msg_type': 'text',
                                  'content': json.dumps({'text': text}, ensure_ascii=False), 'uuid': uuid}, timeout=12)
        else:
            return {'status': 'skipped_no_channel'}
        try:
            body = r.json()
        except ValueError:
            return {'status': 'unknown', 'response_code': r.status_code}
        code = body.get('code', body.get('StatusCode'))
        return {'status': 'sent' if r.ok and code in (0, '0') else 'failed',
                'response_code': r.status_code, 'business_code': code}
    except requests.RequestException:
        return {'status': 'unknown'}
    except Exception:
        return {'status': 'failed'}


def publish(db, plan, decisions, sender=deliver):
    result = {'channel': 'feishu_personal_plan', 'status': 'no_buy_signal', 'payload': {}}
    if not settings.trading_push_enabled or (settings.trading_dry_run and not settings.trading_send_in_dry_run):
        return {**result, 'status': 'disabled'}
    ledger = read_ledger(db)
    items = [d for d in decisions if d.action in ('buy', 'add') and d.raw.get('planned_shares', 0) > 0
             and d.raw['signal_id'] not in ledger]
    if not items:
        return result
    uuid = hashlib.sha256('|'.join(d.raw['signal_id'] for d in items).encode()).hexdigest()[:32]
    now = now_beijing().isoformat()
    for d in items:
        ledger[d.raw['signal_id']] = {**d.raw, 'symbol': d.symbol, 'name': d.name,
                                     'state': 'sending', 'created_at': now, 'delivery_uuid': uuid}
    # Persist before network I/O, so restarts cannot resend an uncertain delivery.
    save_ledger(db, ledger)
    sent = sender(plan, message(items), uuid)
    for d in items:
        ledger[d.raw['signal_id']]['state'] = sent['status'] if sent['status'] in ('sent', 'failed', 'unknown') else 'failed'
        ledger[d.raw['signal_id']]['delivery_result'] = sent
    save_ledger(db, ledger)
    return {**result, **sent, 'payload': {'signal_ids': [d.raw['signal_id'] for d in items], 'delivery_uuid': uuid}}
