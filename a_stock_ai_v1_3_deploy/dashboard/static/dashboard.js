const state = {
  payload: null,
  refreshTimer: null,
};

const $ = (id) => document.getElementById(id);
const BASE_PATH = (() => {
  const script = document.currentScript?.src ? new URL(document.currentScript.src) : null;
  if (!script) return "";
  const suffix = "/static/dashboard.js";
  return script.pathname.endsWith(suffix) ? script.pathname.slice(0, -suffix.length) : "";
})();
const route = (path) => `${BASE_PATH}${path}`;

document.addEventListener("DOMContentLoaded", () => {
  $("refreshButton").addEventListener("click", fetchDashboard);
  tickClock();
  setInterval(tickClock, 1000);
  fetchDashboard();
  state.refreshTimer = setInterval(fetchDashboard, 15000);
});

async function fetchDashboard() {
  try {
    const response = await fetch(route("/api/dashboard"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    render(state.payload);
  } catch (error) {
    $("healthValue").textContent = "异常";
    $("healthReason").textContent = error.message;
    $("healthValue").className = "status-attention";
  }
}

function render(data) {
  const engine = data.engine || {};
  const signal = data.signal || {};
  const health = data.health || {};
  const account = data.paper_account || {};
  const sentiment = data.sentiment || {};
  const latest = data.latest_cycle || {};

  $("engineName").textContent = engine.name || "A股市场实时感知引擎";
  $("signalValue").textContent = signal.signal || "--";
  $("signalValue").className = signalClass(signal.signal);
  $("signalReason").textContent = signal.reasoning || signal.no_recommendation_reason || "等待最新信号";
  $("positionValue").textContent = pct(signal.position);
  $("riskLevel").textContent = `风险 ${signal.risk_level || "--"}`;
  $("equityValue").textContent = money(account.equity);
  $("returnValue").textContent = `收益 ${num(account.return_pct, 4)}%`;
  $("stageValue").textContent = signal.market_stage || "--";
  $("stageReason").textContent = signal.market_stage_reason || "等待盘面确认";
  $("healthValue").textContent = health.ok ? "正常" : "关注";
  $("healthValue").className = health.ok ? "status-ok" : "status-attention";
  $("healthReason").textContent = health.message || "--";

  $("recommendationCount").textContent = `${data.recommendations?.length || 0} 只`;
  $("sentimentScore").textContent = num(sentiment.sentiment_score, 1);
  $("tradePermission").textContent = sentiment.trade_permission || "--";
  $("panicScore").textContent = num(sentiment.panic_score, 0);
  $("coverageValue").textContent = `${sentiment.coverage_count || 0} / ${sentiment.coverage_level || "--"}`;
  $("riskAppetite").textContent = sentiment.risk_appetite || "--";
  $("volumeState").textContent = `${data.state?.volume || "--"} · ${latest.quote_count || 0} 条行情`;

  renderRecommendations(data.recommendations || []);
  renderWatchlist(data.watchlist || []);
  renderPositions(account.positions || {});
  renderOrders(data.recent_orders || []);
  renderStrategy(data.strategy || {});
  renderCycles(data.recent_cycles || []);
  renderBacktest(data.backtests || {});
  drawGauge($("sentimentCanvas"), Number(sentiment.sentiment_score || 0), Number(sentiment.panic_score || 0));
  drawEquity($("equityCanvas"), data.paper_equity_curve || []);
}

function renderRecommendations(items) {
  const body = $("recommendationsBody");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="9"><div class="empty">本轮没有买入推荐，系统保持观察。</div></td></tr>`;
    return;
  }
  body.innerHTML = items
    .map((item) => {
      const range = item.buy_range || {};
      return `<tr>
        <td><span class="symbol">${escapeHtml(item.symbol)}</span><small class="name">${escapeHtml(item.name)}</small></td>
        <td><span class="mode mode--${escapeHtml(item.selection_mode || "watch")}">${escapeHtml(item.selection_mode || "--")}</span></td>
        <td class="number">${price(item.current_price)}</td>
        <td class="${Number(item.pct_change || 0) >= 0 ? "positive" : "negative"} number">${num(item.pct_change, 2)}%</td>
        <td class="number">${price(range.low)} - ${price(range.high)}<small>最高 ${price(item.max_buy_price)}</small></td>
        <td class="number">${price(item.stop_loss)}</td>
        <td class="number">${pct(item.target_take_profit_pct)}<small>移动 ${pct(item.trailing_stop_pct)}</small></td>
        <td class="number">${pct(item.position)}<small>${escapeHtml(item.position_plan || "")}</small></td>
        <td>${escapeHtml(item.reasoning || item.entry_note || "--")}</td>
      </tr>`;
    })
    .join("");
}

function renderWatchlist(items) {
  const host = $("watchlist");
  if (!items.length) {
    host.innerHTML = `<div class="empty">观察池为空。</div>`;
    return;
  }
  host.innerHTML = items
    .slice(0, 8)
    .map(
      (item) => `<div class="watch-item">
        <div><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.name)}</span></div>
        <div><strong class="number">${price(item.current_price)}</strong><span>${escapeHtml(item.selection_mode || "watch")}</span></div>
        <div><strong>${escapeHtml(item.action || "WATCH")}</strong><span>${escapeHtml(item.reasoning || "等待确认")}</span></div>
      </div>`,
    )
    .join("");
}

function renderPositions(positions) {
  const host = $("positionsList");
  const entries = Object.values(positions || {});
  if (!entries.length) {
    host.innerHTML = `<div class="empty">模拟盘暂无持仓。</div>`;
    return;
  }
  host.innerHTML = entries
    .map(
      (item) => `<div class="position-row">
        <div><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.name)}</span></div>
        <div><strong class="number">${item.quantity || 0}</strong><span>持仓股数</span></div>
        <div><strong class="number">${item.available_quantity || 0}</strong><span>T+1 可卖</span></div>
        <div><strong class="number">${price(item.avg_cost)}</strong><span>成本</span></div>
        <div><strong class="number ${Number(item.unrealized_pnl || 0) >= 0 ? "positive" : "negative"}">${money(item.unrealized_pnl)}</strong><span>浮盈亏</span></div>
      </div>`,
    )
    .join("");
}

function renderOrders(items) {
  const body = $("ordersBody");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="7"><div class="empty">暂无模拟订单。</div></td></tr>`;
    return;
  }
  body.innerHTML = items
    .slice(0, 24)
    .map(
      (item) => `<tr>
        <td>${shortTime(item.created_at)}</td>
        <td><span class="symbol">${escapeHtml(item.symbol)}</span><small>${escapeHtml(item.name)}</small></td>
        <td>${escapeHtml(item.side)}</td>
        <td class="status-${escapeHtml(item.status)}">${escapeHtml(item.status)}</td>
        <td class="number">${item.quantity || 0}</td>
        <td class="number">${price(item.price)}</td>
        <td>${escapeHtml(item.reason || "--")}</td>
      </tr>`,
    )
    .join("");
}

function renderStrategy(strategy) {
  $("strategyVersion").textContent = `v${strategy.version || 1}`;
  $("strategyPipeline").innerHTML = (strategy.rules || [])
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
  const params = strategy.params || {};
  const preferred = [
    "CONFIDENCE_THRESHOLD",
    "SENTIMENT_MIN_BUY_SCORE",
    "SENTIMENT_PANIC_THRESHOLD",
    "MIN_TURNOVER_YI",
    "MAX_RECOMMEND_PCT_CHANGE",
    "STOP_LOSS_PCT",
    "MAX_PUSH_STOCKS",
    "MAX_CHASE_PCT",
  ];
  $("strategyParams").innerHTML = preferred
    .filter((key) => key in params)
    .map((key) => `<div class="param"><span>${key}</span><strong>${formatParam(key, params[key])}</strong></div>`)
    .join("");
}

function renderCycles(items) {
  const host = $("cycleRail");
  if (!items.length) {
    host.innerHTML = `<div class="empty">暂无周期日志。</div>`;
    return;
  }
  host.innerHTML = items
    .slice(0, 22)
    .map(
      (item) => `<div class="cycle-item">
        <strong class="status-${escapeHtml(item.status)}">${escapeHtml(item.status)}</strong>
        <span>${escapeHtml(item.signal || "--")}</span>
        <span>${item.recommendation_count || 0} 只</span>
        <span>${shortTime(item.finished_at || item.started_at)} · ${escapeHtml(item.push_status || "--")}</span>
      </div>`,
    )
    .join("");
}

function renderBacktest(backtests) {
  const img = $("backtestChart");
  const fallback = $("backtestFallback");
  if (backtests.latest_chart_available) {
    img.src = `${route("/api/backtest-chart.svg")}?t=${Date.now()}`;
    img.style.display = "block";
    fallback.style.display = "none";
  } else {
    img.removeAttribute("src");
    img.style.display = "none";
    fallback.style.display = "block";
  }
}

function drawGauge(canvas, score, panic) {
  const { ctx, width, height } = scaledContext(canvas);
  ctx.clearRect(0, 0, width, height);
  const cx = width / 2;
  const cy = height * 0.92;
  const radius = Math.min(width * 0.42, height * 0.82);
  drawArc(ctx, cx, cy, radius, 180, 360, "#3f3a2d", 16);
  const color = panic >= 72 ? "#ef6b5d" : score >= 65 ? "#7ad66d" : score >= 53 ? "#e6a93a" : "#64d2d0";
  drawArc(ctx, cx, cy, radius, 180, 180 + clamp(score, 0, 100) * 1.8, color, 16);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  const angle = ((180 + clamp(score, 0, 100) * 1.8) * Math.PI) / 180;
  ctx.lineTo(cx + Math.cos(angle) * (radius - 18), cy + Math.sin(angle) * (radius - 18));
  ctx.strokeStyle = "#f2ead8";
  ctx.lineWidth = 3;
  ctx.stroke();
}

function drawEquity(canvas, points) {
  const { ctx, width, height } = scaledContext(canvas);
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(242, 234, 216, 0.12)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  const values = points.map((item) => Number(item.equity || 0)).filter((value) => value > 0);
  if (values.length < 2) {
    ctx.fillStyle = "#aaa18d";
    ctx.fillText("暂无权益曲线", 20, 40);
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = Math.max(max - min, 1);
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = (index / (values.length - 1)) * (width - 24) + 12;
    const y = height - 18 - ((value - min) / spread) * (height - 36);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#64d2d0";
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.fillStyle = "#aaa18d";
  ctx.fillText(`权益 ${money(values.at(-1))}`, 16, 24);
}

function scaledContext(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width || canvas.width;
  const height = rect.height || canvas.height;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function drawArc(ctx, cx, cy, radius, from, to, color, width) {
  ctx.beginPath();
  ctx.arc(cx, cy, radius, (from * Math.PI) / 180, (to * Math.PI) / 180);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.stroke();
}

function tickClock() {
  $("beijingTime").textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function signalClass(value) {
  const normalized = String(value || "").toUpperCase();
  if (normalized === "BUY") return "positive";
  if (normalized === "SELL") return "negative";
  return "";
}

function price(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "--";
}

function money(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(number);
}

function pct(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "--";
}

function num(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function formatParam(key, value) {
  if (key.includes("PCT") || key.includes("THRESHOLD") && Number(value) < 1) return pct(value);
  return String(value);
}

function shortTime(value) {
  if (!value) return "--";
  const normalized = String(value).replace("T", " ");
  return normalized.slice(5, 19);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
