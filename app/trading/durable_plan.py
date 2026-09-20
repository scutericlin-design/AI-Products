"""Budgeted manual buy proposals. Missing/stale evidence always closes a gate."""
from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime, date, time

from app.services.timezone import now_beijing
from app.trading.types import Decision

VERSION = 'durable20_buy_v2'
ALERT_KEY = 'durable20_buy_alert_ledger_v2'


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(plan):
    p = copy.deepcopy(plan)
    for key in ('account_value', 'available_cash', 'portfolio_peak_value', 'max_equity_amount',
                'low_volatility_reserve', 'single_name_cap', 'sector_cap'):
        if not finite(p.get(key)) or p[key] < 0:
            raise ValueError(f'{key} 必须为有限非负数')
    if not 0 < p['single_name_cap'] <= p['max_equity_amount'] <= p['account_value']:
        raise ValueError('个股、权益和总资产上限不一致')
    if not p['single_name_cap'] <= p['sector_cap'] <= p['max_equity_amount']:
        raise ValueError('行业上限不一致')
    if not 1 <= p.get('max_holdings', 0) <= 8:
        raise ValueError('持股数量上限必须为1至8')
    date.fromisoformat(p['plan_started_at'])
    date.fromisoformat(p['account_as_of'])
    if not isinstance(p.get('candidates'), list) or not 1 <= len(p['candidates']) <= 50:
        raise ValueError('候选池必须含1至50家公司')
    symbols = set()
    for c in p['candidates']:
        s = c.get('symbol', '')
        if len(s) != 9 or not s[:6].isdigit() or s[6:] not in ('.SH', '.SZ') or s in symbols:
            raise ValueError('股票代码无效或重复')
        symbols.add(s)
        if not finite(c.get('max_pe_ttm')) or c['max_pe_ttm'] <= 0 or not c.get('sector'):
            raise ValueError('缺少估值或行业门槛')
        if c.get('max_pb') is not None and (not finite(c['max_pb']) or c['max_pb'] <= 0):
            raise ValueError('PB上限无效')
        if c.get('review_status') not in ('pass', 'wait'):
            raise ValueError('财务状态无效')
    for s, h in p.get('holdings', {}).items():
        if s not in symbols or not finite(h.get('shares')) or h['shares'] < 0 or h['shares'] != int(h['shares']):
            raise ValueError('持仓必须包含在候选池并具有有效整数股数；清仓前不可移除')
    return p


def signal_key(plan, symbol, held):
    # Deliberately independent of live price, clock, or research rank.
    return hashlib.sha256(f'{VERSION}|{symbol}|{int(held)}|{plan.get("signal_epoch",0)}'.encode()).hexdigest()[:32]


def propose(plan, quotes, ledger, now=None):
    now = now or now_beijing()
    qmap = {q.symbol: q for q in quotes}
    today = now.date()
    p = validate(plan)
    holdings = p.get('holdings', {})
    pending = [x for x in ledger.values() if x.get('state') in ('sending', 'sent', 'unknown', 'failed')]
    pending_symbols = {x['symbol'] for x in pending}
    reserve = sum(x['reserved_amount'] for x in pending)
    days = (today - date.fromisoformat(p['plan_started_at'])).days
    stage_cap = min(p['max_equity_amount'], 40000 if days < 60 else 80000 if days < 150 else 120000)
    values = {}
    global_issue = ''
    for symbol, h in holdings.items():
        if h['shares'] and (symbol not in qmap or not fresh(qmap[symbol], now)):
            global_issue = '持仓行情缺失，无法核对组合预算'
        values[symbol] = h['shares'] * qmap[symbol].price if symbol in qmap else 0
    equity = sum(values.values())
    account = p['available_cash'] + equity
    drawdown = 1 - account / max(p['portfolio_peak_value'], account, 1)
    if drawdown >= .12:
        global_issue = '组合回撤达到12%，暂停买入'
    if not 0 <= (today - date.fromisoformat(p['account_as_of'])).days <= 35:
        global_issue = '账户记录超过35天，等待核对现金和持仓'
    if now.weekday() >= 5 or not (time(9,35) <= now.time().replace(tzinfo=None) < time(11,25)
                                  or time(13,5) <= now.time().replace(tzinfo=None) < time(14,50)):
        global_issue = '非买入提醒时段'
    remaining = min(stage_cap - equity - reserve, p['available_cash'] - p['low_volatility_reserve'] - reserve)
    sectors = {}
    for c in p['candidates']:
        sectors[c['sector']] = sectors.get(c['sector'], 0) + values.get(c['symbol'], 0)
    for x in pending:
        sectors[x['sector']] = sectors.get(x['sector'], 0) + x['reserved_amount']
    occupied = {s for s, h in holdings.items() if h['shares'] > 0} | pending_symbols
    decisions = []
    for c in p['candidates']:
        s = c['symbol']; q = qmap.get(s); held = holdings.get(s, {}).get('shares', 0)
        key = signal_key(p, s, held)
        reason = global_issue or gate(c, q, now)
        if s in pending_symbols or key in ledger:
            reason = '本轮已提醒或等待成交反馈，不重复推送'
        if not held and s not in occupied and len(occupied) >= p['max_holdings']:
            reason = '已达到持股数量上限'
        if held:
            last = holdings[s].get('last_buy_date')
            if not last or (today - date.fromisoformat(last)).days < 30:
                reason = '距上次买入不足30天或买入日期缺失'
            elif q and (not finite(q.pe_ttm) or q.pe_ttm > c['max_pe_ttm'] * .9):
                reason = '加仓需要估值进一步降到首买门槛的90%以内'
        raw = {'plan_version': VERSION, 'manual_execution_only': True, 'signal_id': key,
               'sector': c['sector'], 'held_shares': held, 'stage_cap': stage_cap}
        shares = 0
        if not reason:
            # Quote is executable evidence; valuation cap and fees apply to the LIMIT, not only last price.
            limit = math.floor(min(q.price * 1.002, q.price * c['max_pe_ttm'] / q.pe_ttm,
                                   q.price * c['max_pb'] / q.raw['pb'] if c.get('max_pb') else float('inf')) * 100 + 1e-8) / 100
            step, minimum = (1, 200) if s.startswith('688') else (100, 100)
            # A high-priced board lot may exceed the usual 10k tranche, but
            # must still fit the 20k name cap and the shared remaining budget.
            tranche = max(10000, minimum * limit * 1.001 + 5.01) if not held else 10000
            budget = min(tranche, p['single_name_cap'] - values.get(s, 0), remaining,
                         p['sector_cap'] - sectors.get(c['sector'], 0))
            shares = max(0, math.floor((budget - 5) / (limit * 1.001) / step) * step)
            if shares < minimum:
                shares = 0; reason = '可用预算不足最低申报股数，等待而不突破仓位限制'
            else:
                amount = round(shares * limit, 2)
                reserved = round(amount * 1.001 + 5, 2)
                remaining -= reserved; sectors[c['sector']] = sectors.get(c['sector'], 0) + reserved
                occupied.add(s)
                raw.update(price=q.price, pe_ttm=q.pe_ttm, planned_shares=shares, planned_amount=amount,
                           max_limit_price=limit, reserved_amount=reserved, quote_as_of=q.timestamp,
                           valuation_as_of=q.raw['valuation_as_of'], review_period=c['review_period'],
                           source=q.source)
                reason = f'财务复核通过；PE TTM {q.pe_ttm:.2f}≤{c["max_pe_ttm"]:g}；预算及交易状态通过'
        decisions.append(Decision(s, c['name'], ('add' if held else 'buy') if shares else 'observe',
                                  0, (shares * q.price / max(account, 1)) if shares else 0,
                                  reason, raw=raw))
    return decisions


def fresh(q, now):
    try:
        stamp = datetime.fromisoformat(q.timestamp)
        return (stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= 180
                and finite(q.price) and q.price > 0 and q.raw.get('trade_open') is True)
    except (TypeError, ValueError):
        return False


def gate(c, q, now):
    if c.get('review_status') != 'pass':
        return c.get('review_note', '等待财报复核')
    try:
        if not 0 <= (now.date() - date.fromisoformat(c['review_as_of'])).days <= 100:
            return '财务复核已过期'
    except (KeyError, TypeError, ValueError):
        return '缺少财务复核日期'
    if q is None or not fresh(q, now):
        return '行情无效、过期或休市'
    if q.raw.get('latest_report') != c['review_period'] or q.raw.get('latest_ann') != c['review_ann']:
        return '新财报或更正公告尚未复核'
    if q.raw.get('st') or q.raw.get('suspended') or q.raw.get('corporate_action'):
        return '风险警示、停牌或除权变更等待复核'
    if not finite(q.pe_ttm) or q.pe_ttm <= 0 or q.pe_ttm > c['max_pe_ttm']:
        return 'PE TTM尚未达到买入门槛'
    if c.get('max_pb') and (not finite(q.raw.get('pb')) or not 0 < q.raw['pb'] <= c['max_pb']):
        return 'PB尚未达到门槛'
    if not finite(q.pct_change) or not -3 <= q.pct_change <= 3:
        return '当日涨跌幅超出±3%，等待波动稳定'
    if not finite(q.amount_yi) or q.amount_yi <= 0:
        return '没有有效成交'
    return ''
