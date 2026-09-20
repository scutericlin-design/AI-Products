"""Private relay fundamentals + timestamped prices; never use dynamic PE as TTM."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime
import time
import re
import requests
from sqlalchemy import select

from app.database import SessionLocal
from app.models import DataSourceConfig
from app.security import decrypt_secret
from app.services.timezone import now_beijing, BEIJING_TZ
from app.trading.types import Quote


class DurableData:
    def __init__(self):
        self.cache = {}

    def query(self, api, params, fields='', ttl=0):
        key = (api, str(sorted(params.items())), fields)
        old = self.cache.get(key)
        if old and time.monotonic() - old[0] < ttl:
            return old[1]
        with SessionLocal() as db:
            c = db.scalar(select(DataSourceConfig).where(
                DataSourceConfig.provider == 'tushare',
                DataSourceConfig.api_token_cipher.is_not(None),
                DataSourceConfig.status.in_({'available', 'configured_manual_check'})
            ).order_by(DataSourceConfig.priority, DataSourceConfig.user_id))
            if c is None or not c.base_url or not c.base_url.startswith('https://'):
                raise ValueError('已保存的HTTPS TuShare中转配置不可用')
            token, url = decrypt_secret(c.api_token_cipher), c.base_url
        r = requests.post(url, json={'api_name': api, 'token': token, 'params': params, 'fields': fields}, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get('code') not in (0, '0'):
            raise ValueError(f'{api}: data provider rejected request')
        d = j.get('data') or {}
        rows = [dict(zip(d.get('fields', []), x)) for x in d.get('items', [])]
        self.cache[key] = (time.monotonic(), rows)
        return rows

    def calendar(self, now=None):
        now = now or now_beijing()
        day = now.strftime('%Y%m%d')
        rows = self.query('trade_cal', {'exchange': 'SSE', 'start_date': day, 'end_date': day},
                          'cal_date,is_open,pretrade_date', ttl=3600)
        if len(rows) != 1 or rows[0]['cal_date'] != day:
            raise ValueError('缺少当天交易日历')
        return rows[0]

    def fetch_quotes(self, plan):
        now = now_beijing(); day = now.strftime('%Y%m%d')
        cal = self.calendar(now)
        if int(cal['is_open']) != 1:
            return []
        previous = cal['pretrade_date']
        symbols = [c['symbol'] for c in plan['candidates']]
        basics = self.query('daily_basic', {'trade_date': previous}, 'ts_code,trade_date,close,pe_ttm,pb', ttl=900)
        basicmap = {x['ts_code']: x for x in basics}
        limits = self.query('stk_limit', {'trade_date': day}, 'ts_code,trade_date,pre_close,up_limit,down_limit', ttl=900)
        limitmap = {x['ts_code']: x for x in limits}
        listed = self.query('stock_basic', {'list_status': 'L'}, 'ts_code,name', ttl=900)
        names = {x['ts_code']: x['name'] for x in listed}
        def report(symbol):
            rows = self.query('fina_indicator', {'ts_code': symbol, 'start_date': (now-timedelta(days=370)).strftime('%Y%m%d')},
                              'ts_code,ann_date,end_date', ttl=900)
            valid = [x for x in rows if x.get('ann_date') and x['ann_date'] <= day]
            return symbol, max(valid, key=lambda x: (x['end_date'], x['ann_date'])) if valid else {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = dict(pool.map(report, symbols))
        ids = ','.join(('sh' if s.endswith('.SH') else 'sz') + s[:6] for s in symbols)
        r = requests.get('https://hq.sinajs.cn/list=' + ids,
                         headers={'Referer': 'https://finance.sina.com.cn/'}, timeout=12)
        r.raise_for_status()
        output = []
        for market, code, body in re.findall(r'hq_str_(sh|sz)([0-9]{6})="([^"]*)"', r.text):
            s = code + ('.SH' if market == 'sh' else '.SZ')
            x = body.split(',')
            b, limit, reportrow = basicmap.get(s), limitmap.get(s), reports.get(s)
            if len(x) < 32 or s not in symbols or not b or not limit or not reportrow or s not in names:
                continue
            try:
                price, close = float(x[3]), float(b['close'])
                pe, pb = float(b['pe_ttm']), float(b['pb'])
                preclose = float(x[2])
                stamp = datetime.fromisoformat(x[30] + 'T' + x[31]).replace(tzinfo=BEIJING_TZ)
                if close <= 0 or price <= 0 or preclose <= 0 or b['trade_date'] != previous:
                    continue
                corporate = abs(float(limit['pre_close']) - close) > .011 or abs(preclose - close) > .011
                output.append(Quote(s, names[s], price, (price / preclose - 1) * 100, float(x[9]) / 1e8,
                    pe_ttm=pe * price / close, source='TuShare relay daily_basic + Sina timestamped price',
                    timestamp=stamp.isoformat(), raw={
                        'trade_open': True, 'valuation_as_of': previous, 'pb': pb * price / close,
                        'latest_report': reportrow['end_date'], 'latest_ann': reportrow['ann_date'],
                        'st': 'ST' in names[s].upper() or 'ST' in x[0].upper() or '退' in names[s],
                        'suspended': (price >= float(limit['up_limit']) or price <= float(limit['down_limit'])
                                      or float(x[6]) <= 0 or float(x[7]) <= 0),
                        'corporate_action': corporate}))
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        return output
