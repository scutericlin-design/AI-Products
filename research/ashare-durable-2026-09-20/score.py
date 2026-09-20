from pathlib import Path
import pandas as pd
import numpy as np
import json
P=Path('/private/tmp/ashare_durable_20260920')
u=pd.read_csv(P/'universe.csv').set_index('ts_code')
v=pd.read_csv(P/'valuation.csv').set_index('ts_code')
frames={}
coverage={}
for period in ['20201231','20211231','20221231','20231231','20241231','20251231','20260630']:
    f=pd.read_csv(P/f'fina_{period}.csv')
    f=f[(f.ann_date<=20260920)&f.ts_code.isin(u.index)].sort_values('ann_date').drop_duplicates('ts_code',keep='last').set_index('ts_code')
    frames[period]=f
    coverage[period]=len(f)
out=u.join(v).join(frames['20260630'].add_prefix('h1_')).join(frames['20251231'].add_prefix('fy_'))
years=['20211231','20221231','20231231','20241231','20251231']
def panel(key): return pd.concat({y:frames[y][key] for y in years},axis=1).reindex(out.index)
out['years']=panel('profit_dedt').notna().sum(axis=1)
out['profit_positive_years']=(panel('profit_dedt')>0).sum(axis=1)
out['cash_positive_years']=(panel('ocfps')>0).sum(axis=1)
out['roe_median']=panel('roe_dt').median(axis=1)
out['roic_median']=panel('roic').median(axis=1)
out['cash_conversion_median']=panel('ocf_to_profit').median(axis=1)
out['gross_margin_std']=panel('grossprofit_margin').std(axis=1)
out['profit_cagr5']=((frames['20251231'].profit_dedt/frames['20201231'].profit_dedt).where((frames['20251231'].profit_dedt>0)&(frames['20201231'].profit_dedt>0))**.2-1)*100
rev=panel('or_yoy')
out['revenue_cagr5']=((1+rev/100).prod(axis=1).where(rev.notna().all(axis=1))**.2-1)*100
out['market_cap_yi']=out.total_mv/10000
out['financial']=out.industry.fillna('').str.contains('银行|保险|证券|多元金融')
def scale(s,lo,hi): return ((s-lo)/(hi-lo)).clip(0,1).fillna(0)
# Score weights chosen before examining results; interpretive research, not calibrated probability.
out['quality_score']=20*scale(out.roe_median,8,25)+10*scale(out.roic_median,5,20)+10*scale(out.cash_conversion_median,60,130)+5*scale(out.cash_positive_years,2,5)+10*(1-scale(out.h1_debt_to_assets.fillna(100),30,75))+5*(1-scale(out.gross_margin_std.fillna(99),2,10))
out['growth_score']=8*scale(out.revenue_cagr5,0,20)+8*scale(out.profit_cagr5,0,25)+4*scale(out.h1_or_yoy,0,20)+5*scale(out.h1_dt_netprofit_yoy,-10,30)
out['valuation_score']=10*(1-scale(out.pe_ttm.where(out.pe_ttm>0).fillna(200),15,60))+5*scale(out.dv_ttm,0,4)
out['score']=out.quality_score+out.growth_score+out.valuation_score
out['eligible_basic']=(~out.name.str.contains('ST|退'))&(out.list_date<=20210920)&(out.market_cap_yi>=200)&(out.years==5)&(out.profit_positive_years==5)&(out.roe_median>=10)&(out.profit_cagr5>=5)&(out.revenue_cagr5>=3)&(out.h1_profit_dedt>0)&(out.h1_or_yoy>0)&(out.h1_dt_netprofit_yoy>=-10)
out['eligible']=out.eligible_basic&(~out.financial)&(out.cash_positive_years>=4)&(out.h1_debt_to_assets<75)&(out.cash_conversion_median>=60)
out.loc[out.financial,['quality_score','growth_score','valuation_score','score']]=np.nan
gates={
    'ST或退市标记':~out.name.str.contains('ST|退'),
    '上市不足五年':out.list_date<=20210920,
    '总市值低于200亿元或缺失':out.market_cap_yi>=200,
    '五年扣非利润数据不足':out.years==5,
    '五年扣非未连续为正':out.profit_positive_years==5,
    '五年扣非ROE中位数低于10%':out.roe_median>=10,
    '五年扣非利润CAGR低于5%或缺失':out.profit_cagr5>=5,
    '五年营收连乘增速低于3%或缺失':out.revenue_cagr5>=3,
    '最新扣非利润非正或缺失':out.h1_profit_dedt>0,
    '最新营收不增长或缺失':out.h1_or_yoy>0,
    '最新扣非利润下降超过10%或缺失':out.h1_dt_netprofit_yoy>=-10,
    '经营现金流正值年份不足四年':(out.cash_positive_years>=4)|out.financial,
    '最新资产负债率不低于75%或缺失':(out.h1_debt_to_assets<75)|out.financial,
    '五年经营现金流利润总额比中位数低于60%或缺失':(out.cash_conversion_median>=60)|out.financial,
}
out['gate_failures']=['；'.join(k for k,s in gates.items() if not s.loc[idx]) for idx in out.index]
out.loc[out.financial,'gate_failures']=out.loc[out.financial,'gate_failures']+'；金融业需独立资本和资产质量复核，不使用非金融综合分'
out.sort_values('score',ascending=False).to_csv(P/'all_scored.csv')
short=out[out.eligible].sort_values('score',ascending=False)
short.to_csv(P/'quant_shortlist.csv')
summary={'universe':len(out),'coverage':coverage,'valuation_matches':int(out.pe_ttm.notna().sum()),'with_daily_basic':int(out.trade_date.notna().sum()),'five_year_records':int((out.years==5).sum()),'nonfinancial_pass':len(short),'financial_basic_pass':out[out.eligible_basic&out.financial].index.tolist()}
(P/'coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
cols=['name','industry','score','market_cap_yi','roe_median','profit_cagr5','revenue_cagr5','cash_conversion_median','h1_or_yoy','h1_dt_netprofit_yoy','pe_ttm']
print(short[cols].head(90).round(2).to_string())
print('FINANCIAL',out[out.financial&out.eligible_basic][cols].round(2).to_string())
