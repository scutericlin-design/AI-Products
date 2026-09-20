import csv, json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
rows=list(csv.DictReader(open(root/'ashare-durable-2026-09-20/top20.csv',encoding='utf-8-sig')))
pe=[20,22,16,25,18,24,28,23,22,25,23,28,23,20,24,35,30,22,8,12]
sectors=['电力与电气','电力与电气','消费','工业自动化','汽车零部件','电力与电气','电力与电气','医疗','消费','电子硬件','电力与电气','汽车零部件','专业服务','电子硬件','电子硬件','软件','工业自动化','工业设备','银行','资源']
notes={'300750.SZ':'财务通过，但当前100股约3万元，超过单只2万元上限，暂不买入',
'600406.SH':'等待回款改善：H1经营现金流同比下降78.95%',
'000333.SZ':'仅对2026H1汇兑与套保口径例外复核通过；下一份财报重新复核',
'300124.SZ':'等待归母利润恢复增长（当前同比-5.35%）',
'600660.SH':'剔除汇兑后利润总额已增长，但等待实际净利润与经营现金流恢复',
'600885.SH':'等待回款复核：H1经营现金流/利润总额仅6.65%',
'002028.SZ':'等待回款复核：H1经营现金流/利润总额仅12.59%',
'002475.SZ':'等待经营现金流转正并验证并购盈利转化',
'002050.SZ':'等待归母利润恢复增长，并满足估值上限',
'300628.SZ':'等待季节性与回款复核：H1现金/利润总额49.28%',
'300866.SZ':'等待季节性与回款复核：H1现金/利润总额34.58%',
'688111.SH':'等待剔除投资收益后的正常化PE核算，不能直接使用原始PE',
'688188.SH':'等待回款复核：H1现金/利润总额57.35%',
'600036.SH':'银行独立复核通过；PE≤8且PB≤0.9；继续核对不良率、拨备及资本充足率',
'601899.SH':'PE≤12，按资源周期谨慎配置；不得将当前金属价格外推十年'}
wait={'600406.SH','300124.SZ','600660.SH','600885.SH','002028.SZ','002475.SZ','002050.SZ','300628.SZ','300866.SZ','688111.SH','688188.SH'}
p={'version':'durable20_buy_v2','plan_started_at':'2026-09-20','updated_at':'2026-09-20',
'account_value':200000,'available_cash':200000,'portfolio_peak_value':200000,'account_as_of':'2026-09-20',
'max_equity_amount':120000,'low_volatility_reserve':80000,'single_name_cap':20000,'sector_cap':40000,
'max_holdings':8,'signal_epoch':0,'holdings':{},'candidates':[],'notification_policy':'buy_only','feishu_chat_id':None}
for r,cap,sector in zip(rows,pe,sectors):
 s=r['ts_code']
 p['candidates'].append({'symbol':s,'name':r['name'],'sector':sector,'max_pe_ttm':cap,'max_pb':.9 if s=='600036.SH' else None,
 'review_status':'wait' if s in wait else 'pass','review_as_of':'2026-09-20',
 'review_period':str(int(float(r['h1_end_date']))),'review_ann':str(int(float(r['h1_ann_date']))),
 'review_note':notes.get(s,'财务复核通过，等待估值、有效行情和可用预算同时达标'),
 'reference_close':float(r['close']),'reference_pe_ttm':float(r['pe_ttm']),
 'reference_price_cap':round(float(r['close'])/float(r['pe_ttm'])*cap,2),
 'source_url':r['source_url']})
out=Path(__file__).parent
(out/'plan.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
print('Generated',len(p['candidates']),'candidates;',sum(c['review_status']=='pass' for c in p['candidates']),'financial passes')
