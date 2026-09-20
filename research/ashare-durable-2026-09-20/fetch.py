import json, sys, time
from pathlib import Path
import requests
import pandas as pd
from sqlalchemy import select
from app.database import SessionLocal
from app.models import DataSourceConfig
from app.security import decrypt_secret

OUT = Path('/tmp/ashare_durable_20260920')
OUT.mkdir(exist_ok=True)
with SessionLocal() as db:
    c = db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider=='tushare', DataSourceConfig.api_token_cipher.is_not(None)).order_by(DataSourceConfig.priority, DataSourceConfig.user_id))
    token, url = decrypt_secret(c.api_token_cipher), c.base_url
assert url and token

def fetch(api, params, fields, filename):
    path=OUT/(filename+'.csv')
    if path.exists():
        print(filename, 'cached', flush=True); return
    frames=[]
    for offset in range(0, 30000, 5000):
        r=requests.post(url, json={'api_name':api,'token':token,'params':dict(params,limit=5000,offset=offset),'fields':fields},timeout=40)
        if r.status_code!=200:
            print(filename,'HTTP',r.status_code,flush=True); return
        j=r.json()
        if j.get('code') not in (0,'0'):
            print(filename,'code',j.get('code'),flush=True); return
        d=j.get('data') or {}; f=pd.DataFrame(d.get('items',[]),columns=d.get('fields',[]))
        if f.empty: break
        if frames and f.equals(frames[-1]):
            print(filename,'pagination_repeated',flush=True); break
        frames.append(f)
        if len(f)<5000: break
        time.sleep(.2)
    df=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    df.to_csv(path,index=False)
    print(filename,'rows',len(df),'stocks',df.ts_code.nunique() if 'ts_code' in df else 0,flush=True)

fetch('stock_basic',{'list_status':'L'},'ts_code,name,industry,market,list_date,exchange','universe')
fetch('daily_basic',{'trade_date':'20260918'},'ts_code,trade_date,close,pe_ttm,pb,total_mv,dv_ttm','valuation')
for period in ['20260630','20251231','20241231','20231231','20221231','20211231','20201231']:
    fetch('fina_indicator_vip',{'period':period},'ts_code,ann_date,end_date,roe,roe_dt,roic,profit_dedt,ocfps,eps,debt_to_assets,grossprofit_margin,netprofit_margin,or_yoy,netprofit_yoy,dt_netprofit_yoy,ocf_to_or,ocf_to_profit,interestdebt,netdebt,current_ratio,rd_exp','fina_'+period)
