const state = { refreshTimer: null };

const $ = (id) => document.getElementById(id);
const BASE_PATH = (() => {
  const script = document.currentScript?.src ? new URL(document.currentScript.src) : null;
  const suffix = "/static/etf.js";
  return script?.pathname.endsWith(suffix) ? script.pathname.slice(0, -suffix.length) : "";
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
    const response = await fetch(route("/api/etf-dashboard"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    $("healthValue").textContent = "异常";
    $("healthReason").textContent = error.message;
    $("healthValue").className = "status-attention";
  }
}

function render(data) {
  const decision = data.latest_decision || {};
  const strategy = data.strategy || {};
  const target = decision.target || {};
  const account = data.account || {};
  const health = data.health || {};
  const confirmation = decision.confirmation || {};
  const execution = decision.execution || {};

  $("signalValue").textContent = decision.signal || "HOLD";
  $("signalValue").className = signalClass(decision.signal);
  $("signalReason").textContent = strategy.reasoning || "等待ETF分钟策略决策";
  $("targetValue").textContent = target.symbol || "现金";
  $("targetWeight").textContent = `${target.name || "未选定目标"} · 目标仓位 ${pct(decision.target_weight)}`;
  $("equityValue").textContent = money(account.equity);
  $("returnValue").textContent = `收益 ${num(account.return_pct, 2)}%`;
  $("regimeValue").textContent = strategy.regime || "--";
  $("regimeReason").textContent = regimeDetail(strategy.regime_detail || {});
  $("healthValue").textContent = health.ok ? "正常" : "关注";
  $("healthValue").className = health.ok ? "status-ok" : "status-attention";
  $("healthReason").textContent = health.message || "--";

  $("candidateCount").textContent = `${data.candidates?.length || 0} 只`;
  $("confirmStatus").textContent = confirmation.status || "--";
  $("confirmApproved").textContent = confirmation.approved === true ? "通过" : confirmation.approved === false ? "未通过" : "--";
  $("confirmBars").textContent = confirmation.bars ?? "--";
  $("confirmSlope").textContent = confirmation.slope_pct_per_min != null ? pct(confirmation.slope_pct_per_min) : "--";
  $("executionStatus").textContent = execution.status || "--";
  $("filledOrders").textContent = execution.filled_order_count ?? 0;

  $("cashValue").textContent = money(account.cash);
  $("marketValue").textContent = money(account.market_value);
  $("realizedPnl").textContent = money(account.realized_pnl);
  $("accountUpdated").textContent = account.updated_at ? `账户更新时间：${shortTime(account.updated_at)}` : "模拟账户尚未初始化";
  $("strategyVersion").textContent = `v${strategy.strategy_version || "--"}`;

  renderCandidates(data.candidates || []);
  renderRiskFlags(strategy.risk_flags || [], execution);
  renderPositions(data.positions || []);
  renderStrategy(strategy, decision.trade_plan || []);
  renderOrders(data.recent_orders || []);
  renderDecisions(data.recent_decisions || []);
}

function renderCandidates(items) {
  const body = $("candidatesBody");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="9"><div class="empty">当前没有通过流动性、趋势与风险过滤的ETF候选。</div></td></tr>`;
    return;
  }
  body.innerHTML = items.slice(0, 10).map((item) => `<tr>
    <td><span class="symbol">${escapeHtml(item.symbol)}</span><small class="name">${escapeHtml(item.name)}</small></td>
    <td>${escapeHtml(item.bucket || "--")}<small>${escapeHtml(item.index_name || item.index_code || "")}</small></td>
    <td class="number">${price(item.latest_price)}</td>
    <td class="number">${num(item.momentum_score, 3)}</td>
    <td class="number">${pct(item.annualized_trend)}</td>
    <td class="number">${price(item.ma10)}</td>
    <td class="number">${money(item.avg_turnover_yuan)}</td>
    <td class="number">${pct(item.annualized_volatility)}</td>
    <td>${escapeHtml((item.reasons || []).join("；") || "--")}</td>
  </tr>`).join("");
}

function renderRiskFlags(flags, execution) {
  const host = $("riskFlags");
  const items = [...flags];
  if (execution.reason) items.push(execution.reason);
  if (!items.length) {
    host.innerHTML = `<div class="empty">分钟确认和执行层未发现额外风险提示。</div>`;
    return;
  }
  host.innerHTML = items.slice(0, 5).map((item) => `<div class="watch-item"><div><strong>风控</strong><span>${escapeHtml(item)}</span></div></div>`).join("");
}

function renderPositions(items) {
  const host = $("positionsList");
  if (!items.length) {
    host.innerHTML = `<div class="empty">ETF模拟盘当前为空仓。</div>`;
    return;
  }
  host.innerHTML = items.map((item) => `<div class="position-row">
    <div><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.name)}</span></div>
    <div><strong class="number">${item.quantity || 0}</strong><span>持仓份额</span></div>
    <div><strong class="number">${item.available_quantity || 0}</strong><span>T+1 可卖</span></div>
    <div><strong class="number">${price(item.avg_cost)}</strong><span>成本</span></div>
    <div><strong class="number ${Number(item.unrealized_pnl || 0) >= 0 ? "positive" : "negative"}">${money(item.unrealized_pnl)}</strong><span>浮盈亏</span></div>
  </div>`).join("");
}

function renderStrategy(strategy, tradePlan) {
  const rules = [
    "日线动量、流动性与市场状态共同筛选目标ETF",
    "同一跟踪指数只保留流动性更好的代表标的",
    "买入或切换必须通过最近30根1分钟趋势确认",
    "模拟盘执行100份一手、T+1、滑点、佣金与分钟止损",
  ];
  $("strategyPipeline").innerHTML = rules.map((rule) => `<li>${escapeHtml(rule)}</li>`).join("");
  const host = $("tradePlan");
  if (!tradePlan.length) {
    host.innerHTML = `<div class="empty">本轮没有可执行交易计划，策略保持当前仓位或现金。</div>`;
    return;
  }
  host.innerHTML = tradePlan.map((item) => `<div class="watch-item">
    <div><strong>${escapeHtml(item.action || "--")}</strong><span>${escapeHtml(item.symbol || "--")} ${escapeHtml(item.name || "")}</span></div>
    <div><strong class="number">${pct(item.target_weight)}</strong><span>目标仓位</span></div>
    <div><strong>${escapeHtml(item.reason || "执行计划")}</strong><span>${escapeHtml(strategy.execution_mode || "模拟盘")}</span></div>
  </div>`).join("");
}

function renderOrders(items) {
  const body = $("ordersBody");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="7"><div class="empty">暂无ETF模拟订单。</div></td></tr>`;
    return;
  }
  body.innerHTML = items.slice(0, 24).map((item) => `<tr>
    <td>${shortTime(item.created_at)}</td>
    <td><span class="symbol">${escapeHtml(item.symbol)}</span><small>${escapeHtml(item.name)}</small></td>
    <td>${escapeHtml(item.side)}</td>
    <td class="status-${escapeHtml(item.status)}">${escapeHtml(item.status)}</td>
    <td class="number">${item.quantity || 0}</td>
    <td class="number">${price(item.price)}</td>
    <td>${escapeHtml(item.reason || "--")}</td>
  </tr>`).join("");
}

function renderDecisions(items) {
  const host = $("decisionRail");
  if (!items.length) {
    host.innerHTML = `<div class="empty">尚无ETF分钟决策记录。</div>`;
    return;
  }
  host.innerHTML = items.slice(0, 18).map((item) => `<div class="cycle-item">
    <strong>${shortTime(item.created_at)}</strong>
    <span class="${signalClass(item.signal)}">${escapeHtml(item.signal || "HOLD")}</span>
    <span>${escapeHtml(item.regime || "--")}</span>
    <span>${escapeHtml(item.target_symbol || "现金")} ${escapeHtml(item.target_name || "")} · ${escapeHtml(item.execution_status || "--")}</span>
  </div>`).join("");
}

function regimeDetail(detail) {
  const index = detail.index_name || detail.index_symbol || "市场指数";
  const trend = detail.trend != null ? `趋势 ${num(detail.trend, 3)}` : "等待日线确认";
  return `${index} · ${trend}`;
}

function tickClock() {
  $("beijingTime").textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date());
}

function signalClass(value) {
  const normalized = String(value || "").toUpperCase();
  if (normalized === "BUY" || normalized === "SWITCH") return "positive";
  if (normalized === "SELL") return "negative";
  return "";
}

function price(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "--";
}

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(number) : "--";
}

function pct(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "--";
}

function num(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function shortTime(value) {
  return value ? String(value).replace("T", " ").slice(5, 19) : "--";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
