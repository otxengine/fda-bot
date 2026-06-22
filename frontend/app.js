// FDA Options Scanner — Dashboard JS v2

const API_BASE = "";
const REFRESH_MS = 5 * 60 * 1000;

let allSignals = [];
let currentDaysFilter = 7;
let currentScoreFilter = 0;
let sortColumn = "signal_score";
let sortAsc = false;
let currentTab = "signals";
let refreshTimer = null;

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  setupFilters();
  setupTableSort();
  loadAll();
  refreshTimer = setInterval(loadAll, REFRESH_MS);
  // Poll alerts every 60s
  pollAlerts();
  setInterval(pollAlerts, 60_000);
});

// ── Tabs ──────────────────────────────────────────────────────────────────────

function setupTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      currentTab = tab.dataset.tab;
      document.getElementById(`panel-${currentTab}`).classList.add("active");
      if (currentTab === "history")        loadHistory();
      if (currentTab === "calibration")   loadCalibration();
      if (currentTab === "trade-ideas")   loadTradeIdeas();
      if (currentTab === "alerts")        loadAlerts();
      if (currentTab === "stock-signals") loadStockSignals();
    });
  });
}

// ── Data Loading ──────────────────────────────────────────────────────────────

async function loadAll() {
  await Promise.all([loadStatus(), loadSignals()]);
  document.getElementById("last-updated").textContent = "Updated: " + new Date().toLocaleTimeString();
}

async function loadStatus() {
  try {
    const d = await (await fetch(`${API_BASE}/api/status`)).json();
    document.getElementById("stat-events").textContent = d.upcoming_events ?? "--";
    document.getElementById("status-dot").style.background = d.status === "running" ? "var(--green)" : "var(--red)";
    document.getElementById("status-text").textContent =
      d.status === "running" ? (d.polygon_configured ? "Live" : "Running") : "Error";
    document.getElementById("stat-history").textContent = d.historical_records ?? "--";
  } catch {}
}

async function loadSignals() {
  setTableLoading("signals-table-body", 16);
  try {
    const d = await (await fetch(`${API_BASE}/api/signals?days=${currentDaysFilter}&min_score=${currentScoreFilter}`)).json();
    allSignals = d.signals || [];
    renderSignals(allSignals);
    updateStats(allSignals);
  } catch (e) {
    setTableError("signals-table-body", 16, e.message);
  }
}

async function loadHistory() {
  setTableLoading("history-table-body", 10);
  try {
    const d = await (await fetch(`${API_BASE}/api/history?days=30`)).json();
    renderHistory(d.history || []);
  } catch (e) {
    setTableError("history-table-body", 10, e.message);
  }
}

async function loadCalibration() {
  const el = document.getElementById("calibration-body");
  if (!el) return;
  el.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
  try {
    const d = await (await fetch(`${API_BASE}/api/calibration`)).json();
    renderCalibration(d.buckets || []);
  } catch (e) {
    el.innerHTML = `<p style="color:var(--red)">Error: ${esc(e.message)}</p>`;
  }
}

async function triggerRefresh() {
  const btn = event.target;
  btn.disabled = true; btn.textContent = "Refreshing...";
  try {
    await fetch(`${API_BASE}/api/refresh`, { method: "POST" });
    setTimeout(loadAll, 3000);
  } catch {}
  setTimeout(() => { btn.disabled = false; btn.textContent = "Refresh Data"; }, 4500);
}

async function loadStockSignals() {
  setTableLoading("stock-signals-table-body", 11);
  try {
    const d = await (await fetch(`${API_BASE}/api/stock-signals`)).json();
    renderStockSignals(d.signals || []);
  } catch (e) {
    setTableError("stock-signals-table-body", 11, e.message);
  }
}

function renderStockSignals(signals) {
  const tbody = document.getElementById("stock-signals-table-body");
  if (!signals.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty-state">
      אין סיגנלים פעילים כרגע בחלון 1-5 ימים.<br>
      <small style="margin-top:6px;display:block">הסיגנלים מתעדכנים כל 30 דקות בשעות מסחר.</small>
    </td></tr>`;
    return;
  }

  tbody.innerHTML = signals.map(s => {
    const sigCls = s.stock_signal === "BUY" ? "stock-buy" : s.stock_signal === "AVOID" ? "stock-avoid" : "stock-watch";
    const daysClass = s.days_until <= 2 ? "days-urgent" : s.days_until <= 4 ? "days-soon" : "days-normal";
    const em = s.expected_move_pct;

    let warns = "";
    if (s.liquidity_warning) warns += `<span class="warn-icon" title="Low liquidity">💧</span>`;
    if (s.iv_crush_warning)  warns += `<span class="warn-icon" title="IV crush risk">⚠️</span>`;

    return `<tr data-ticker="${s.ticker}" style="cursor:pointer">
      <td class="ticker-cell">${s.ticker}${warns}</td>
      <td class="company-cell" title="${esc(s.company)}">${esc(s.company || "—")}</td>
      <td><span class="event-type">${esc(s.event_type || "FDA")}</span></td>
      <td><span class="days-pill ${daysClass}">${s.days_until}d</span></td>
      <td><span class="${sigCls}">${s.stock_signal}</span></td>
      <td class="mono" style="color:var(--green);font-weight:700">${s.entry_price ? "$"+s.entry_price.toFixed(2) : "—"}</td>
      <td class="mono" style="color:var(--red)">${s.stop_loss_price ? "$"+s.stop_loss_price.toFixed(2) : "—"}</td>
      <td class="mono" style="font-size:12px">${s.target_date || "—"}</td>
      <td>${em != null ? stockForecastHtml(em, s.call_put_ratio, s.stock_price) : "—"}</td>
      <td>
        <div class="score-bar-wrap ${s.composite_score>=70?"alert-red":s.composite_score>=50?"alert-orange":"alert-green"}">
          <div class="score-bar-bg"><div class="score-bar-fill" style="width:${s.composite_score||0}%"></div></div>
          <span class="score-num">${(s.composite_score||0).toFixed(0)}</span>
        </div>
      </td>
      <td style="font-size:11px;color:var(--text-muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${esc(s.stock_signal_reason)}">${esc(s.stock_signal_reason || "—")}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll("tr[data-ticker]").forEach(tr => {
    tr.addEventListener("click", () => openModal(tr.dataset.ticker));
  });
}

async function loadTradeIdeas() {
  setTableLoading("trade-ideas-table-body", 9);
  try {
    const d = await (await fetch(`${API_BASE}/api/trade-ideas`)).json();
    renderTradeIdeas(d.ideas || []);
  } catch (e) {
    setTableError("trade-ideas-table-body", 9, e.message);
  }
}

async function loadAlerts() {
  const el = document.getElementById("alerts-body");
  if (!el) return;
  el.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
  try {
    const d = await (await fetch(`${API_BASE}/api/alerts`)).json();
    renderAlerts(d.alerts || []);
  } catch (e) {
    el.innerHTML = `<p style="color:var(--red)">Error: ${esc(e.message)}</p>`;
  }
}

async function pollAlerts() {
  try {
    const d = await (await fetch(`${API_BASE}/api/alerts?unread=true`)).json();
    const count = d.unread_count || 0;
    const badge = document.getElementById("alert-count-badge");
    const bar   = document.getElementById("alert-bar");
    const barMsg = document.getElementById("alert-bar-msg");
    if (badge) {
      badge.textContent = count;
      badge.style.display = count > 0 ? "inline-block" : "none";
    }
    if (bar) {
      if (count > 0) {
        bar.classList.add("visible");
        const latest = (d.alerts || [])[0];
        barMsg.textContent = latest ? latest.message : `${count} unread alerts`;
      } else {
        bar.classList.remove("visible");
      }
    }
  } catch {}
}

async function acknowledgeAllAlerts() {
  try {
    await fetch(`${API_BASE}/api/alerts/acknowledge-all`, { method: "POST" });
    pollAlerts();
    if (currentTab === "alerts") loadAlerts();
  } catch {}
}

async function seedHistory(btn) {
  btn.disabled = true; btn.textContent = "Seeding...";
  try {
    await fetch(`${API_BASE}/api/seed-history`, { method: "POST" });
    setTimeout(loadHistory, 8000);
  } catch {}
  setTimeout(() => { btn.disabled = false; btn.textContent = "Seed Historical Data"; }, 9000);
}

// ── Signals Table ─────────────────────────────────────────────────────────────

function renderSignals(signals) {
  const tbody = document.getElementById("signals-table-body");
  const sorted = sortData([...signals]);

  document.getElementById("row-count").textContent = `${sorted.length} result${sorted.length !== 1 ? "s" : ""}`;

  if (sorted.length === 0) {
    tbody.innerHTML = `<tr><td colspan="16" class="empty-state">No signals found for selected filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = sorted.map(s => signalRowHtml(s)).join("");
  tbody.querySelectorAll("tr[data-ticker]").forEach(tr => {
    tr.addEventListener("click", () => openModal(tr.dataset.ticker));
  });
}

function signalRowHtml(s) {
  const alertCls = `alert-${s.alert_level || "unknown"}`;
  const score = s.signal_score !== null ? s.signal_score.toFixed(1) : "—";
  const sw = s.signal_score ?? 0;

  const daysClass = s.days_until <= 7 ? "days-urgent" : s.days_until <= 14 ? "days-soon" : "days-normal";

  // Entry window badge
  const ew = s.entry_window || "";
  const ewHtml = ew
    ? `<span class="ew-badge ew-${ew}">${ew}</span>`
    : `<span class="no-data">—</span>`;

  // Directional stock forecast until FDA event
  const emHtml = s.expected_move_pct != null
    ? stockForecastHtml(s.expected_move_pct, s.call_put_ratio, s.stock_price)
    : `<span class="no-data">—</span>`;

  // Warning icons
  let warnings = "";
  if (s.liquidity_warning)  warnings += `<span class="warn-icon" title="Low liquidity">💧</span>`;
  if (s.iv_crush_warning)   warnings += `<span class="warn-icon" title="IV crush risk">⚠️</span>`;
  if (s.earnings_overlap)   warnings += `<span class="warn-icon" title="Earnings within 5d of FDA event">📅</span>`;
  const flowVel = s.flow_velocity || 0;
  if (Math.abs(flowVel) > 30) {
    const arrow = flowVel > 0 ? "↑" : "↓";
    warnings += `<span class="warn-icon" title="Flow velocity ${flowVel.toFixed(0)}%" style="color:${flowVel>0?"var(--green)":"var(--red)"}">${arrow}</span>`;
  }

  // Probability cells
  const pUp5   = s.p_up_5   ?? null;
  const pUp10  = s.p_up_10  ?? null;
  const pDown5 = s.p_down_5 ?? null;
  const pDown10= s.p_down_10?? null;
  const pUp5Html   = pUp5   !== null ? `<span class="p-cell ${pColorClass(pUp5)}">${pct(pUp5)}</span>`   : `<span class="p-cell p-low">—</span>`;
  const pUp10Html  = pUp10  !== null ? `<span class="p-cell ${pColorClass(pUp10)}">${pct(pUp10)}</span>` : `<span class="p-cell p-low">—</span>`;
  const pDown5Html = pDown5 !== null ? `<span class="p-cell p-down">${pct(pDown5)}</span>`               : `<span class="p-cell p-low">—</span>`;
  const pDown10Html= pDown10!== null ? `<span class="p-cell p-down">${pct(pDown10)}</span>`              : `<span class="p-cell p-low">—</span>`;

  // Event pin badge
  const pin = s.event_pinned_ratio ?? 0;
  const pinPct = (pin * 100).toFixed(0);
  const pinBadgeCls = pin >= 0.6 ? "pin-badge-high" : pin >= 0.35 ? "pin-badge-med" : "pin-badge-low";
  const pinLabel    = pin >= 0.6 ? "PIN" : "PIN";
  const pinHtml = `<div class="pin-wrap">
    <div class="pin-bar-bg"><div class="pin-bar-fill" style="width:${pinPct}%"></div></div>
    <span style="font-family:monospace;font-size:12px">${pinPct}%</span>
    <span class="pin-badge ${pinBadgeCls}">${pinLabel}</span>
  </div>`;

  const bestExp = s.best_expiry ? s.best_expiry.slice(5) : "—"; // "06-21"

  return `<tr data-ticker="${s.ticker}" class="${alertCls}">
    <td class="ticker-cell">${s.ticker}${warnings}</td>
    <td class="company-cell" title="${esc(s.company)}">${esc(s.company || "—")}</td>
    <td><span class="event-type">${esc(s.event_type || "—")}</span></td>
    <td><span class="days-pill ${daysClass}">${s.days_until}d</span></td>
    <td>
      <div class="score-bar-wrap ${alertCls}">
        <div class="score-bar-bg"><div class="score-bar-fill" style="width:${sw}%"></div></div>
        <span class="score-num">${score}</span>
      </div>
    </td>
    <td>${ewHtml}</td>
    <td>${emHtml}</td>
    <td>${pUp5Html}</td>
    <td>${pUp10Html}</td>
    <td>${pDown5Html}</td>
    <td>${pDown10Html}</td>
    <td>${pinHtml}</td>
    <td style="font-family:monospace;font-size:12px">${bestExp}</td>
    <td class="mono">${s.iv_rank !== null ? s.iv_rank.toFixed(1) + "%" : "—"}</td>
    <td class="mono">${s.call_put_ratio !== null ? s.call_put_ratio.toFixed(2) : "—"}</td>
    <td class="mono">${s.premium_flow !== null ? formatMoney(s.premium_flow) : "—"}</td>
  </tr>`;
}

// ── Trade Ideas Table ──────────────────────────────────────────────────────────

function renderTradeIdeas(ideas) {
  const tbody = document.getElementById("trade-ideas-table-body");
  if (!ideas.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty-state">No actionable trade recommendations yet. Run a scan first.</td></tr>`;
    return;
  }

  const stratLabels = {
    long_call:     "Long Call",
    long_put:      "Long Put",
    long_straddle: "Long Straddle",
  };

  tbody.innerHTML = ideas.map(idea => {
    const stratCls = `strat-${idea.strategy}`;
    const stratLabel = stratLabels[idea.strategy] || idea.strategy;
    const convCls = `conviction-${idea.conviction || "low"}`;
    const ewHtml = idea.entry_window
      ? `<span class="ew-badge ew-${idea.entry_window}">${idea.entry_window}</span>`
      : "—";
    const emHtml = idea.expected_move_pct != null
      ? stockForecastHtml(idea.expected_move_pct, idea.call_put_ratio, null)
      : "—";
    const daysClass = idea.days_until <= 7 ? "days-urgent" : idea.days_until <= 14 ? "days-soon" : "days-normal";

    let warnings = "";
    if (idea.liquidity_warning) warnings += `<span class="warn-icon" title="Low liquidity">💧</span>`;
    if (idea.iv_crush_warning)  warnings += `<span class="warn-icon" title="IV crush risk">⚠️</span>`;
    if (idea.earnings_overlap)  warnings += `<span class="warn-icon" title="Earnings overlap">📅</span>`;

    return `<tr data-ticker="${idea.ticker}" style="cursor:pointer">
      <td class="ticker-cell">${idea.ticker}${warnings}</td>
      <td class="company-cell" title="${esc(idea.company)}">${esc(idea.company || "—")}</td>
      <td><span class="strat-badge ${stratCls}">${stratLabel}</span></td>
      <td><span class="${convCls}">${(idea.conviction || "").toUpperCase()}</span></td>
      <td style="font-size:12px;color:var(--text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(idea.rationale)}">${esc(idea.rationale || "—")}</td>
      <td class="mono">${emHtml}</td>
      <td>${ewHtml}</td>
      <td>
        <div class="score-bar-wrap ${idea.composite_score >= 70 ? "alert-red" : idea.composite_score >= 50 ? "alert-orange" : "alert-green"}">
          <div class="score-bar-bg"><div class="score-bar-fill" style="width:${idea.composite_score||0}%"></div></div>
          <span class="score-num">${(idea.composite_score||0).toFixed(1)}</span>
        </div>
      </td>
      <td>${idea.days_until != null ? `<span class="days-pill ${daysClass}">${idea.days_until}d</span>` : "—"}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll("tr[data-ticker]").forEach(tr => {
    tr.addEventListener("click", () => openModal(tr.dataset.ticker));
  });
}

// ── Alerts Panel ───────────────────────────────────────────────────────────────

function renderAlerts(alerts) {
  const el = document.getElementById("alerts-body");
  if (!el) return;
  if (!alerts.length) {
    el.innerHTML = `<div class="empty-state">No alerts yet. Alerts fire automatically during scans when significant changes are detected.</div>`;
    return;
  }

  const typeLabels = {
    score_spike:    "Score Spike",
    event_imminent: "Event Imminent",
    flow_surge:     "Flow Surge",
    iv_spike:       "IV Spike",
    cp_flip:        "C/P Flip",
  };

  el.innerHTML = alerts.map(a => {
    const label = typeLabels[a.alert_type] || a.alert_type;
    const time = new Date(a.triggered_at).toLocaleString();
    return `<div class="alert-item ${a.acknowledged ? "" : "unread"}">
      <span class="alert-type-badge">${label}</span>
      <span class="alert-msg">${esc(a.message)}</span>
      <span class="alert-time">${time}</span>
      ${!a.acknowledged ? `<button class="btn" style="font-size:11px;padding:2px 8px;flex-shrink:0" onclick="ackAlert(${a.id}, this)">Dismiss</button>` : ""}
    </div>`;
  }).join("");
}

async function ackAlert(id, btn) {
  btn.disabled = true;
  try {
    await fetch(`${API_BASE}/api/alerts/${id}/acknowledge`, { method: "POST" });
    btn.closest(".alert-item").classList.remove("unread");
    btn.remove();
    pollAlerts();
  } catch {}
}

// ── History Table ─────────────────────────────────────────────────────────────

function renderHistory(records) {
  const tbody = document.getElementById("history-table-body");
  if (!records.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-state">
      No historical results yet.<br>
      <small style="margin-top:8px;display:block">Click "Seed Historical Data" to fetch past events.</small>
    </td></tr>`;
    return;
  }

  tbody.innerHTML = records.map(r => {
    const outCls = `outcome-${r.outcome || "unknown"}`;
    const outLabel = { strong_up:"Strong Up", up:"Up", neutral:"Neutral",
                       down:"Down", strong_down:"Strong Down", unknown:"Pending" }[r.outcome] || "—";
    const sigHtml = r.pre_event_score !== null
      ? `<div class="signal-mini">
           <span class="dot" style="background:${r.pre_event_alert_level==="red"?"var(--red)":r.pre_event_alert_level==="orange"?"var(--orange)":"var(--green)"}"></span>
           <strong style="font-family:monospace">${r.pre_event_score.toFixed(1)}</strong>
           <span style="color:var(--text-muted);font-size:11px">IV:${(r.pre_event_iv_rank||0).toFixed(0)}%</span>
         </div>`
      : `<span class="no-data">—</span>`;

    const probHtml = r.pre_event_score !== null && r.p_up_5 !== undefined
      ? `<span class="${pColorClass(r.p_up_5)}" style="font-family:monospace;font-weight:700">${pct(r.p_up_5)}</span>`
      : "";

    return `<tr>
      <td class="ticker-cell">${esc(r.ticker)}</td>
      <td class="company-cell" title="${esc(r.company)}">${esc(r.company)}</td>
      <td><span class="event-type">${esc(r.event_type||"—")}</span></td>
      <td style="font-size:12px">${r.event_date}<br><span style="color:var(--text-muted);font-size:11px">${r.days_ago}d ago</span></td>
      <td>${sigHtml}</td>
      <td class="mono">${r.price_before ? "$" + r.price_before.toFixed(2) : "—"}</td>
      <td>${changePct(r.change_1d_pct)}</td>
      <td>${changePct(r.change_3d_pct)}</td>
      <td>${changePct(r.change_7d_pct)}</td>
      <td><span class="outcome-badge ${outCls}">${outLabel}</span></td>
    </tr>`;
  }).join("");
}

// ── Calibration Panel ─────────────────────────────────────────────────────────

function renderCalibration(buckets) {
  const el = document.getElementById("calibration-body");
  if (!el) return;

  if (!buckets.length || buckets.every(b => !b.n)) {
    el.innerHTML = `<div class="empty-state">Not enough historical data yet.<br>
      <small style="margin-top:8px;display:block">Historical win rates will appear after more events are tracked.</small></div>`;
    return;
  }

  el.innerHTML = `
    <p style="color:var(--text-muted);font-size:12px;margin-bottom:16px">
      Observed win rates from ${buckets.reduce((a,b) => a + (b.n||0), 0)} historical FDA catalyst events
      tracked by this system.
    </p>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Score Range</th><th>n</th>
          <th>P(+5% next day)</th><th>P(+10%)</th>
          <th>P(-5%)</th><th>P(-10%)</th>
          <th>Avg Move</th><th>Median Move</th>
        </tr></thead>
        <tbody>
          ${buckets.map(b => b.n ? `
            <tr>
              <td><strong>${b.range}</strong></td>
              <td class="mono">${b.n}</td>
              <td class="p-cell ${pColorClass(b.p_up5)}">${pct(b.p_up5)}</td>
              <td class="p-cell ${pColorClass(b.p_up10)}">${pct(b.p_up10)}</td>
              <td class="p-cell p-low">${pct(b.p_down5)}</td>
              <td class="p-cell p-low">${pct(b.p_down10)}</td>
              <td class="mono">${b.avg_change > 0 ? "+" : ""}${b.avg_change?.toFixed(1)}%</td>
              <td class="mono">${b.median_change > 0 ? "+" : ""}${b.median_change?.toFixed(1)}%</td>
            </tr>` : `
            <tr style="opacity:0.4">
              <td>${b.range}</td><td colspan="7" style="color:var(--text-muted);font-size:12px">no data yet</td>
            </tr>`
          ).join("")}
        </tbody>
      </table>
    </div>`;
}

// ── Modal ─────────────────────────────────────────────────────────────────────

async function openModal(ticker) {
  document.getElementById("modal-ticker").textContent = ticker;
  document.getElementById("modal-body").innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
  document.getElementById("modal-overlay").classList.add("open");
  try {
    const d = await (await fetch(`${API_BASE}/api/ticker/${ticker}`)).json();
    renderModal(d);
  } catch (e) {
    document.getElementById("modal-body").innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
  }
}

function renderModal(data) {
  const bd = data.signal_breakdown;
  const events = data.fda_events || [];

  let html = "";

  if (bd) {
    const score = bd.composite_score;
    const alertColor = bd.alert_level === "red" ? "var(--red)" : bd.alert_level === "orange" ? "var(--orange)" : "var(--green)";
    const prob  = bd.probability || {};
    const exp   = bd.expiration || {};
    const entry = bd.entry_analysis || {};

    html += `
      <!-- Score + Probability -->
      <div style="display:grid;grid-template-columns:auto 1fr;gap:24px;margin-bottom:24px;align-items:start">
        <div>
          <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Signal Score</div>
          <div style="font-size:52px;font-weight:800;color:${alertColor};line-height:1">${score.toFixed(1)}</div>
          <div style="font-size:12px;color:${alertColor};font-weight:600;text-transform:uppercase;margin-top:4px">${bd.alert_level}</div>
        </div>
        <div>
          <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">
            Probability Forecast
            ${prob.confidence ? `<span class="conf-${prob.confidence}" style="margin-left:6px">${prob.confidence} confidence</span>` : ""}
            ${prob.calibration_n ? `<span style="color:var(--text-muted)">(n=${prob.calibration_n})</span>` : ""}
          </div>
          ${exp.expected_move_pct != null ? `<div style="font-size:13px;margin-bottom:10px">Stock Forecast: ${stockForecastHtml(exp.expected_move_pct, bd.components?.call_put_ratio?.value, bd.raw_data?.stock_price)}</div>` : ""}
          ${entry.entry_window ? `<div style="margin-bottom:10px">Entry Window: <span class="ew-badge ew-${entry.entry_window}">${entry.entry_window}</span></div>` : ""}
          ${probRow("P(+5% move)", prob.p_up_5, "prob-up")}
          ${probRow("P(+10% move)", prob.p_up_10, "prob-up")}
          ${probRow("P(−5% move)", prob.p_down_5, "prob-down")}
          ${probRow("P(−10% move)", prob.p_down_10, "prob-down")}
        </div>
      </div>`;

    // Trade Recommendation
    const rec = bd.trade_recommendation || {};
    if (rec.strategy && rec.strategy !== "watch" && rec.strategy !== "avoid") {
      const stratLabels = { long_call: "Long Call", long_put: "Long Put", long_straddle: "Long Straddle" };
      const stratLabel = stratLabels[rec.strategy] || rec.strategy;
      const convColor = rec.conviction === "high" ? "var(--green)" : rec.conviction === "medium" ? "var(--orange)" : "var(--text-muted)";
      const ewHtml = entry.entry_window
        ? `<span class="ew-badge ew-${entry.entry_window}">${entry.entry_window}</span>`
        : "";
      const emHtml = exp.expected_move_pct != null
        ? `<strong>±${exp.expected_move_pct.toFixed(1)}%</strong> expected move` : "";

      let warnHtml = "";
      if (entry.liquidity_warning) warnHtml += `<span style="color:var(--orange);font-size:12px">💧 Low liquidity</span> `;
      if (entry.iv_crush_warning)  warnHtml += `<span style="color:var(--red);font-size:12px">⚠️ IV crush risk</span> `;
      if (entry.earnings_overlap)  warnHtml += `<span style="color:var(--orange);font-size:12px">📅 Earnings overlap</span>`;

      html += `
        <div class="modal-section">
          <h3>Trade Recommendation</h3>
          <div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:16px">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
              <span class="strat-badge strat-${rec.strategy}" style="font-size:14px;padding:4px 12px">${stratLabel}</span>
              <span style="color:${convColor};font-weight:700;text-transform:uppercase;font-size:12px">${rec.conviction} conviction</span>
              ${ewHtml}
            </div>
            <div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">${esc(rec.rationale || "")}</div>
            ${emHtml ? `<div style="font-size:13px;margin-bottom:6px">Stock move until FDA: ${emHtml}</div>` : ""}
            ${warnHtml ? `<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">${warnHtml}</div>` : ""}
          </div>
        </div>`;
    }

    // Expiration breakdown
    if (exp.breakdown && exp.breakdown.length > 0) {
      html += `
        <div class="modal-section">
          <h3>Options Expiration Analysis
            <span style="font-weight:400;color:var(--text-muted);text-transform:none;font-size:12px">
              — Event Pin: <strong style="color:var(--accent)">${((exp.event_pinned_ratio||0)*100).toFixed(0)}%</strong>
              of volume in event-proximal expiries
            </span>
          </h3>
          <table class="exp-table">
            <thead><tr>
              <th>Expiry</th><th>Days to Event</th><th>Weight</th>
              <th>Call Vol</th><th>Put Vol</th><th>C/P</th>
              <th>Wtd Call Vol</th><th>Strike Focus</th>
            </tr></thead>
            <tbody>
              ${exp.breakdown.map(row => `
                <tr class="${row.proximity_weight >= 2 ? "pinned-row" : ""}">
                  <td><strong>${row.expiry}</strong>
                    ${row.proximity_weight >= 2 ? '<span class="weight-badge-sm">event</span>' : ""}
                  </td>
                  <td class="mono">${row.days_to_event}d</td>
                  <td class="mono" style="color:var(--accent)">${row.proximity_weight}×</td>
                  <td class="mono">${fmt(row.call_volume)}</td>
                  <td class="mono">${fmt(row.put_volume)}</td>
                  <td class="mono">${row.call_put_ratio !== null ? row.call_put_ratio.toFixed(2) : "—"}</td>
                  <td class="mono">${fmt(row.weighted_call_vol)}</td>
                  <td style="font-size:11px;color:var(--text-muted)">${row.dominant_strike_type || "—"}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>`;
    }

    // Signal components
    html += `
      <div class="modal-section">
        <h3>Signal Components</h3>
        <div class="component-grid">
          ${Object.entries(bd.components).map(([key, c]) =>
            componentCard(
              key.replace(/_/g," "),
              formatComponentVal(key, c.value),
              c.weight + "%",
              c.description
            )
          ).join("")}
        </div>
      </div>`;

    // Raw data
    const rd = bd.raw_data;
    html += `
      <div class="modal-section">
        <h3>Raw Options Data</h3>
        <div class="data-grid">
          ${dataItem("Call Volume", fmt(rd.call_volume))}
          ${dataItem("Put Volume", fmt(rd.put_volume))}
          ${dataItem("Total Volume", fmt(rd.total_volume))}
          ${dataItem("Open Interest", fmt(rd.open_interest))}
          ${dataItem("Implied Vol", (rd.implied_volatility||0).toFixed(1) + "%")}
          ${dataItem("Stock Price", "$" + (rd.stock_price||0).toFixed(2))}
          ${dataItem("Market Cap", formatMoney(rd.market_cap))}
          ${dataItem("Scan Time", rd.scan_time ? new Date(rd.scan_time).toLocaleString() : "—")}
        </div>
      </div>`;
  } else {
    html += `<div class="empty-state" style="padding:20px 0">No signal data yet — pending next scan.</div>`;
  }

  // FDA Events
  if (events.length) {
    html += `
      <div class="modal-section">
        <h3>Upcoming FDA Events</h3>
        <div class="events-list">
          ${events.map(e => `
            <div class="event-card">
              <div class="event-info">
                <span class="event-name">${esc(e.event_type||"Event")}${e.drug_name ? " — " + esc(e.drug_name) : ""}</span>
                <span class="event-date-text">${esc(e.indication||"")}</span>
              </div>
              <div style="text-align:right">
                <div style="font-weight:700">${e.event_date}</div>
                <div class="days-pill ${e.days_until<=7?"days-urgent":e.days_until<=14?"days-soon":"days-normal"}" style="display:inline-block;margin-top:4px">${e.days_until}d away</div>
              </div>
            </div>`).join("")}
        </div>
      </div>`;
  }

  document.getElementById("modal-body").innerHTML = html;
}

function probRow(label, val, cls) {
  if (val === null || val === undefined) return "";
  const pctVal = (val * 100).toFixed(0);
  return `<div class="prob-row ${cls}">
    <span class="prob-label">${label}</span>
    <div class="prob-bar-bg"><div class="prob-bar-fill" style="width:${pctVal}%"></div></div>
    <span class="prob-val">${pctVal}%</span>
  </div>`;
}

function formatComponentVal(key, val) {
  if (val === null || val === undefined) return "—";
  if (key === "premium_flow") return formatMoney(val);
  if (key === "iv_rank" || key === "expiration") return val.toFixed(1) + "%";
  return val.toFixed ? val.toFixed(2) : String(val);
}

function componentCard(label, value, weight, desc) {
  return `<div class="component-card">
    <div class="component-label">${label} <span class="weight-badge">${weight}</span></div>
    <div class="component-value">${value}</div>
    <div class="component-desc">${desc}</div>
  </div>`;
}

function dataItem(label, value) {
  return `<div class="data-item"><span class="label">${label}</span><span class="value">${value || "—"}</span></div>`;
}

function closeModal(e) { if (e.target === document.getElementById("modal-overlay")) closeModalDirect(); }
function closeModalDirect() { document.getElementById("modal-overlay").classList.remove("open"); }
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModalDirect(); });

// ── Filters ───────────────────────────────────────────────────────────────────

function setupFilters() {
  document.querySelectorAll(".chip[data-days]").forEach(chip => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip[data-days]").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      currentDaysFilter = parseInt(chip.dataset.days);
      loadSignals();
    });
  });
  document.querySelectorAll(".chip[data-score]").forEach(chip => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip[data-score]").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      currentScoreFilter = parseInt(chip.dataset.score);
      loadSignals();
    });
  });
}

// ── Sorting ───────────────────────────────────────────────────────────────────

function setupTableSort() {
  document.querySelectorAll("th[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      sortAsc = sortColumn === col ? !sortAsc : col !== "signal_score";
      sortColumn = col;
      document.querySelectorAll("th[data-sort] .sort-icon").forEach(i => i.textContent = "↕");
      const icon = th.querySelector(".sort-icon");
      if (icon) icon.textContent = sortAsc ? "↑" : "↓";
      renderSignals(allSignals);
    });
  });
}

function sortData(arr) {
  return arr.sort((a, b) => {
    let va = a[sortColumn], vb = b[sortColumn];
    if (va === null || va === undefined) va = sortAsc ?  Infinity : -Infinity;
    if (vb === null || vb === undefined) vb = sortAsc ?  Infinity : -Infinity;
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ?  1 : -1;
    return 0;
  });
}

// ── Stats ─────────────────────────────────────────────────────────────────────

function updateStats(signals) {
  const ws = signals.filter(s => s.signal_score !== null);
  document.getElementById("stat-signals").textContent = signals.length;
  document.getElementById("stat-red").textContent    = ws.filter(s => s.signal_score >= 70).length;
  document.getElementById("stat-orange").textContent = ws.filter(s => s.signal_score >= 50 && s.signal_score < 70).length;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function stockForecastHtml(movePct, callPutRatio, stockPrice) {
  const cp = callPutRatio || 1;
  const pct = movePct.toFixed(1);
  let arrow, color, sign, target = "";

  if (cp >= 2.0) {
    arrow = "↑"; color = "var(--green)"; sign = "+";
    if (stockPrice) {
      const t = (stockPrice * (1 + movePct / 100)).toFixed(2);
      target = ` <span style="color:var(--text-muted);font-size:11px">→ $${t}</span>`;
    }
  } else if (cp <= 0.7) {
    arrow = "↓"; color = "var(--red)"; sign = "-";
    if (stockPrice) {
      const t = (stockPrice * (1 - movePct / 100)).toFixed(2);
      target = ` <span style="color:var(--text-muted);font-size:11px">→ $${t}</span>`;
    }
  } else {
    arrow = "↕"; color = "var(--orange)"; sign = "±";
  }

  return `<span class="mono" style="color:${color};font-weight:700;font-size:13px">${arrow} ${sign}${pct}%</span>${target}`;
}

function pColorClass(p) {
  if (p === null || p === undefined) return "p-low";
  return p >= 0.60 ? "p-high" : p >= 0.45 ? "p-mid" : "p-low";
}

function pct(p) {
  if (p === null || p === undefined) return "—";
  return (p * 100).toFixed(0) + "%";
}

function changePct(val) {
  if (val === null || val === undefined) return `<span class="change-neu">—</span>`;
  const cls = val >= 5 ? "change-pos" : val <= -5 ? "change-neg" : "change-neu";
  return `<span class="${cls}">${val > 0 ? "+" : ""}${val.toFixed(1)}%</span>`;
}

function esc(s) {
  if (!s) return "";
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function fmt(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString();
}

function formatMoney(n) {
  if (!n) return "$0";
  if (n >= 1_000_000) return `$${(n/1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n/1_000).toFixed(0)}K`;
  return `$${Number(n).toFixed(0)}`;
}

function setTableLoading(id, cols) {
  document.getElementById(id).innerHTML =
    `<tr><td colspan="${cols}" class="loading"><div class="spinner"></div><br>Loading...</td></tr>`;
}

function setTableError(id, cols, msg) {
  document.getElementById(id).innerHTML =
    `<tr><td colspan="${cols}" class="empty-state" style="color:var(--red)">Error: ${esc(msg)}</td></tr>`;
}

function updateLastUpdated() {
  document.getElementById("last-updated").textContent = "Updated: " + new Date().toLocaleTimeString();
}
