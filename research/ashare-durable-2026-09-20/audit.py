import concurrent.futures,json
from pathlib import Path
import requests
from sqlalchemy import select
from app.database import SessionLocal
from app.models import DataSourceConfig
from app.security import decrypt_secret
with SessionLocal() as db:
    c=db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider=='tushare',DataSourceConfig.api_token_cipher.is_not(None)).order_by(DataSourceConfig.priority,DataSourceConfig.user_id))
    token,url=decrypt_secret(c.api_token_cipher),c.base_url
codes='300750.SZ 600406.SH 000333.SZ 300124.SZ 600660.SH 600885.SH 002028.SZ 300832.SZ 605499.SH 002475.SZ 600563.SH 002050.SZ 300012.SZ 300628.SZ 300866.SZ 688111.SH 688188.SH 603699.SH 600036.SH 601899.SH'.split()
def run(code):
    try:
        r=requests.post(url,json={'api_name':'fina_audit','token':token,'params':{'ts_code':code,'period':'20251231'},'fields':'ts_code,ann_date,end_date,audit_result'},timeout=30)
        j=r.json()
        if r.status_code!=200 or j.get('code') not in (0,'0'):return {'ts_code':code,'status':'unavailable'}
        d=j.get('data') or {}; rows=[dict(zip(d.get('fields',[]),x)) for x in d.get('items',[])]
        rows=[x for x in rows if str(x.get('end_date')).replace('-','').split('.')[0]=='20251231']
        return {'ts_code':code,'status':'available' if rows else 'missing','rows':rows}
    except Exception:return {'ts_code':code,'status':'request_failed'}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    results=list(ex.map(run,codes))
Path('/tmp/ashare_durable_20260920/audit_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False),flush=True)
