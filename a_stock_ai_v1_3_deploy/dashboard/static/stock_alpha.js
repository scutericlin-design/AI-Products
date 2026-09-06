(() => {
  const $ = (id) => document.getElementById(id);
  const base = new URL(document.currentScript.src).pathname.replace(/\/static\/stock_alpha\.js$/, '');
  const names = {C_ai: 'AI正式模拟盘', B_enhanced: '增强规则 · B', A_baseline: '原规则 · A'};
  const modes = {ai_reviewed: 'AI已复核', rules_only: '规则执行', no_eligible_event: '无有效新事件', ai_degraded_stock_alpha_rules: 'AI降级 · 规则执行'};
  let payload = null, selected = 'C_ai', pending = false;
  const number = (v, digits = 2) => v == null ? '--' : Number(v).toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const money = (v) => v == null ? '--' : `¥${number(v)}`;
  const pct = (v) => `${number(v)}%`;
  const escape = (v) => String(v ?? '--').replace(/[&<>"']/g, (c) => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const time = (v) => v ? new Date(v).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '尚无成交快照';
  function table(id, rows, columns, empty) {
    $(id).innerHTML = rows.length ? rows.map((cells) => `<tr>${cells.map((v) => `<td>${escape(v)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${columns}" class="empty">${escape(empty)}</td></tr>`;
  }
  function render() {
    const data = payload;
    if (!data || data.status !== 'ok') return;
    const account = data.accounts[selected];
    $('equity').textContent = money(account.equity);
    $('returns').textContent = `累计收益 ${pct(account.return_pct)}`;
    $('cash').textContent = money(account.cash);
    $('exposure').textContent = `当前仓位 ${pct((account.equity - account.cash) / account.equity * 100)}`;
    $('realized').textContent = money(account.realized_pnl);
    $('fees').textContent = `累计费用 ${money(account.fees)}`;
    $('drawdown').textContent = pct(account.max_drawdown_pct);
    $('trades').textContent = `${account.trades}笔成交`;
    const states = {started:'已启动', closed:'休市', exchange_holiday:'交易所休市', running:'运行中', prepared:'已准备', preparing:'准备中', blocked:'执行阻断', data_degraded:'数据待核对', close_backed_up:'已收盘备份'};
    $('health').textContent = data.worker_stale ? '心跳过期' : (states[data.health?.status] || '待核验');
    $('updated').textContent = time(data.heartbeat?.at);
    $('loadStatus').textContent = `${names[selected]} · ${time(data.fetched_at)}${account.stale_marks.length ? ' · 估值待核对：'+account.stale_marks.join('、') : ''}`;
    table('comparison', Object.entries(data.accounts).map(([id,a]) => [names[id],money(a.equity),pct(a.return_pct),pct(a.max_drawdown_pct),a.trades,money(a.fees)]), 6, '暂无对照数据');
    $('positionCount').textContent = `${account.positions.length}只`;
    table('positions', account.positions.map((p) => [`${p.symbol} ${p.name}`,p.qty,money(p.cost),money(p.mark),money(p.qty*p.mark),money(p.qty*(p.mark-p.cost)),pct(p.qty*p.mark/account.equity*100),money(p.cost*0.92),time(p.mark_at)]),9,'当前为空仓，尚无持仓');
    table('orders', account.recent_trades.map((t) => [time(t.at),`${t.symbol} ${t.name}`,t.side==='BUY'?'买入':'卖出',money(t.price),t.qty,money(t.fee),money(t.realized),modes[t.ai_mode]||t.ai_mode,t.reason]),9,'暂无模拟成交');
    const plan = account.plan;
    $('aiMode').textContent = modes[plan.ai_mode] || '--';
    const info = [
      ['账本',names[selected]], ['资金边界','独立100万元，不与其他策略调资金'],
      ['组合上限','总仓80% / 单股8% / 最多12只'], ['检查与再平衡','交易时段2分钟 / 5个交易日'],
      ['选股模型',selected==='A_baseline'?'冻结原规则':'质量35% / 增长25% / 估值20% / 动量20%'],
      ['止损与退出','成本回落8%硬止损；排名与目标仓位退出，无固定止盈'],
      ['财务快照',data.snapshot_as_of || '--'], ['计划准备时间',time(plan.prepared_at)],
      ['AI模型',selected==='C_ai'?(data.ai.model || '--'):'不调用AI'],
      ['AI主动配置',selected==='C_ai'?pct((plan.ai_positive_active_weight || 0)*100):'不适用'],
      ['目标股票',plan.targets.map((t)=>`${t.symbol} ${pct(t.weight*100)}`).join('；') || '暂无计划'],
      ['飞书通知',selected==='C_ai'?'仅模拟成交':'对照账本不推送']
    ];
    $('strategyInfo').innerHTML = info.map(([k,v])=>`<div><dt>${escape(k)}</dt><dd>${escape(v)}</dd></div>`).join('');
    $('evidence').innerHTML = plan.evidence.length ? plan.evidence.map((e)=>`<article><h3>${escape(e.ts_code)}</h3><p>${escape(e.thesis)}</p><p>反向证据：${escape(e.counter_evidence)}</p><small>证据编号：${escape((e.event_ids||[]).join('、'))}</small></article>`).join('') : '<p>当前账本无AI机会调整记录。</p>';
    table('blocks',account.recent_blocks.map((b)=>[b.cycle,b.symbol,b.side,b.status,b.reason]),5,'暂无执行检查记录');
    draw();
  }
  function draw() {
    const canvas = $('equityChart'), account = payload?.accounts?.[selected];
    const rect = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width*ratio); canvas.height = Math.round(rect.height*ratio);
    const ctx=canvas.getContext('2d'); ctx.scale(ratio,ratio);
    const w=rect.width,h=rect.height;
    ctx.strokeStyle='#cfd8d5'; ctx.lineWidth=1; ctx.beginPath();ctx.moveTo(60,15);ctx.lineTo(60,h-30);ctx.lineTo(w-12,h-30);ctx.stroke();
    ctx.font='12px sans-serif';ctx.fillStyle='#59636b';
    if (!account?.curve?.length) {
      ctx.fillText('暂无净值快照',75,50);
      $('curveSummary').textContent = account ? `初始资金 ${money(account.initial_cash)}，尚无交易周期净值记录。` : '数据暂不可用';
      return;
    }
    const points=account.curve, values=points.map((p)=>p.nav/account.initial_cash);
    const min=Math.min(1,...values), max=Math.max(1,...values), pad=Math.max((max-min)*0.1,0.005);
    const low=min-pad,high=max+pad;
    const y=(v)=>15+(high-v)/(high-low)*(h-45);
    ctx.fillText(high.toFixed(3),4,25);ctx.fillText(low.toFixed(3),4,h-30);
    ctx.strokeStyle='#087f70';ctx.lineWidth=2;ctx.beginPath();
    values.forEach((v,i)=>{const x=60+i/Math.max(1,values.length-1)*(w-75);if(i)ctx.lineTo(x,y(v));else ctx.moveTo(x,y(v));});ctx.stroke();
    if(values.length===1){ctx.beginPath();ctx.arc(60,y(values[0]),3,0,Math.PI*2);ctx.fillStyle='#087f70';ctx.fill();}
    ctx.fillStyle='#59636b';ctx.fillText(points[0].at.slice(0,10),60,h-8);
    if(w>350&&points.length>1)ctx.fillText(points.at(-1).at.slice(0,10),w-90,h-8);
    $('curveSummary').textContent = `${points.length}个每日快照 · 当前收益 ${pct(account.return_pct)} · 记录内最大回撤 ${pct(account.max_drawdown_pct)}`;
  }
  async function refresh() {
    if(pending) return;
    pending=true; $('refreshButton').disabled=true;
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),12000);
    try {
      const response=await fetch(`${base}/api/stock-alpha-dashboard`,{cache:'no-store',signal:controller.signal});
      if(response.status===401||response.redirected){location.assign(`${base}/login`);return;}
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const next=await response.json();
      if(next.status!=='ok')throw new Error(next.message||'独立账本暂不可用');
      payload=next;render();
    } catch(error) {
      $('loadStatus').textContent=`刷新失败：${error.message}${payload?'；下方为上次成功读取的数据。':''}`;
      $('health').textContent='数据未更新';
    } finally {clearTimeout(timeout);pending=false;$('refreshButton').disabled=false;}
  }
  const tabs=Array.from(document.querySelectorAll('[data-account]'));
  function select(tab){selected=tab.dataset.account;tabs.forEach((t)=>{const active=t===tab;t.setAttribute('aria-selected',String(active));t.tabIndex=active?0:-1;});render();}
  tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>select(tab));tab.addEventListener('keydown',(e)=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const index=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;select(tabs[index]);tabs[index].focus();});});
  $('refreshButton').addEventListener('click',refresh);
  new ResizeObserver(draw).observe($('equityChart').parentElement);
  refresh();setInterval(()=>{if(!document.hidden)refresh();},15000);
})();
