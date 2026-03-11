/**
 * MOMO Trading Admin Dashboard — SSE + Stock-Grouped Chat UI
 */
const API = '/api/v1/admin';
let currentView = 'live';
let activityCount = 0;
let autoScroll = true;
let accountPollTimer = null;

// Stock card tracking: key = "cycleId:symbol" → { element, headerEl, bodyEl, stepsEl, activities[], outcome }
let stockCards = {};

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  loadSettings();
  loadSystemStatus();
  loadReportList();
  loadAccountInfo();
  loadLLMStatus();
  loadLLMUsage();
  connectSSE();
  loadTodayActivities();
  setInterval(loadSystemStatus, 15000);
  accountPollTimer = setInterval(loadAccountInfo, 30000);
  setInterval(loadLLMUsage, 60000);
});

// ── SSE Connection ──
function connectSSE() {
  const es = new EventSource(`${API}/stream`);

  es.onopen = () => {
    setStatus('connected', 'SSE 연결됨');
    updateBadge('badge-sse', '연결', 'green');
  };

  es.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'connected') return;
      if (msg.type === 'activity' && currentView === 'live') {
        appendActivity(msg.data);
        if (msg.data && msg.data.phase === 'COMPLETE' &&
            ['DECISION', 'ORDER', 'TRADE_RESULT'].includes(msg.data.activity_type)) {
          setTimeout(loadAccountInfo, 2000);
        }
      }
      if (msg.type === 'account_changed') {
        loadAccountInfo();
      }
    } catch (err) {
      console.error('SSE parse error', err);
    }
  };

  es.onerror = () => {
    setStatus('disconnected', 'SSE 재연결 중...');
    updateBadge('badge-sse', '끊김', 'red');
    setTimeout(() => {
      if (es.readyState === EventSource.CLOSED) connectSSE();
    }, 3000);
  };
}

// ── Account Info ──
async function loadAccountInfo() {
  try {
    const [balResp, holdResp, pendResp] = await Promise.all([
      fetch(`${API}/account/balance`),
      fetch(`${API}/account/holdings`),
      fetch(`${API}/account/pending-orders`),
    ]);
    const balJson = await balResp.json();
    const holdJson = await holdResp.json();
    const pendJson = await pendResp.json();
    renderAccountBalance(balJson.data);
    renderAccountHoldings(holdJson.data);
    renderPendingOrders(pendJson.data);
  } catch (err) {
    console.error('Account info error:', err);
    const el = document.getElementById('account-info');
    if (el) el.innerHTML = '<div class="text-gray-600">조회 실패</div>';
  }
}

function renderAccountBalance(data) {
  const el = document.getElementById('account-info');
  if (!el || !data) {
    if (el) el.innerHTML = '<div class="text-gray-600">계좌 미연결</div>';
    return;
  }
  const pnlColor = data.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const cashRatio = data.total_asset > 0
    ? ((data.cash / data.total_asset) * 100).toFixed(1)
    : '0.0';
  el.innerHTML = `
    <div class="flex justify-between">
      <span class="text-gray-400">총자산</span>
      <span class="text-white font-medium">${formatKRW(data.total_asset)}</span>
    </div>
    <div class="flex justify-between">
      <span class="text-gray-400">현금</span>
      <span>${formatKRW(data.cash)} <span class="text-gray-600">(${cashRatio}%)</span></span>
    </div>
    <div class="flex justify-between">
      <span class="text-gray-400">주식</span>
      <span>${formatKRW(data.stock_value)}</span>
    </div>
    <div class="flex justify-between">
      <span class="text-gray-400">손익</span>
      <span class="${pnlColor}">${data.total_pnl >= 0 ? '+' : ''}${formatKRW(data.total_pnl)} (${data.total_pnl_rate >= 0 ? '+' : ''}${data.total_pnl_rate.toFixed(2)}%)</span>
    </div>`;
}

function renderAccountHoldings(data) {
  const el = document.getElementById('holdings-info');
  const countEl = document.getElementById('holdings-count');
  const sectionEl = document.getElementById('holdings-section');
  if (!el) return;
  if (!data || !data.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }
  if (sectionEl) sectionEl.style.display = '';
  if (countEl) countEl.textContent = `${data.length}종목`;
  el.innerHTML = data.map(h => {
    const pnlColor = h.pnl_rate >= 0 ? 'text-green-400' : 'text-red-400';
    const evalAmt = h.current_price * h.quantity;
    return `<div class="border border-gray-700 rounded p-1.5 space-y-0.5">
      <div class="flex justify-between items-center">
        <span class="text-gray-200 font-medium truncate" title="${h.symbol}">${h.name}</span>
        <span class="${pnlColor} font-medium">${h.pnl_rate >= 0 ? '+' : ''}${h.pnl_rate.toFixed(2)}%</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>${h.quantity}주 | 평단 ${Number(h.avg_buy_price).toLocaleString()}원</span>
        <span>현재 ${Number(h.current_price).toLocaleString()}원</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>평가 ${formatKRW(evalAmt)}</span>
        <span class="${pnlColor}">${h.pnl >= 0 ? '+' : ''}${formatKRW(h.pnl)}</span>
      </div>
    </div>`;
  }).join('');
}

function renderPendingOrders(data) {
  const el = document.getElementById('pending-orders-info');
  const countEl = document.getElementById('pending-count');
  const sectionEl = document.getElementById('pending-section');
  if (!el) return;
  if (!data || !data.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }
  if (sectionEl) sectionEl.style.display = '';
  if (countEl) {
    const totalAmt = data.reduce((s, o) => s + o.order_price * o.remaining_qty, 0);
    countEl.textContent = `${data.length}건 (${formatKRW(totalAmt)})`;
  }
  el.innerHTML = data.map(o => {
    const sideColor = o.side === '매수' ? 'text-red-400' : 'text-blue-400';
    const borderColor = o.side === '매수' ? 'border-yellow-700/60' : 'border-yellow-700/60';
    const orderAmt = o.order_price * o.remaining_qty;
    const timeStr = o.order_time ? o.order_time.slice(0,2) + ':' + o.order_time.slice(2,4) + ':' + o.order_time.slice(4,6) : '';
    return `<div class="border ${borderColor} bg-yellow-900/10 rounded p-1.5 space-y-0.5">
      <div class="flex justify-between items-center">
        <span class="text-gray-200 font-medium truncate" title="${o.symbol}">${o.name}</span>
        <span class="${sideColor} font-medium text-xs px-1.5 py-0.5 rounded ${o.side === '매수' ? 'bg-red-900/30' : 'bg-blue-900/30'}">${o.side}</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>미체결 ${o.remaining_qty}주 / ${o.order_qty}주</span>
        <span>${Number(o.order_price).toLocaleString()}원</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>${formatKRW(orderAmt)}</span>
        <span>${timeStr}</span>
      </div>
    </div>`;
  }).join('');
}

function toggleSettings() {
  const body = document.getElementById('settings-body');
  const arrow = document.getElementById('settings-arrow');
  if (!body) return;
  const isHidden = body.classList.contains('hidden');
  body.classList.toggle('hidden');
  if (arrow) arrow.style.transform = isHidden ? '' : 'rotate(-90deg)';
}

function formatKRW(amount) {
  if (amount == null) return '-';
  if (Math.abs(amount) >= 100000000) return (amount / 100000000).toFixed(1) + '억';
  if (Math.abs(amount) >= 10000) return (amount / 10000).toFixed(0) + '만';
  return amount.toLocaleString() + '원';
}

// ══════════════════════════════════════════════════════════
// ── Chat Rendering: Stock-Grouped View ──
// ══════════════════════════════════════════════════════════

/**
 * 활동 1건 추가 — 종목별 카드로 라우팅
 */
function appendActivity(data) {
  const container = document.getElementById('chat-container');

  // Remove placeholder
  if (container.children.length === 1 && container.children[0].classList.contains('text-center')) {
    container.innerHTML = '';
  }

  const symbol = data.symbol;
  const isCycleActivity = data.activity_type === 'CYCLE';
  const isDailyPlan = data.activity_type === 'DAILY_PLAN';
  const isLLMCall = data.activity_type === 'LLM_CALL';

  // Non-symbol activities → inline (cycle dividers, daily plan, events without symbol)
  if (!symbol || isCycleActivity || isDailyPlan) {
    if (isCycleActivity && data.phase === 'START') {
      const divider = createCycleDivider(data, true);
      container.appendChild(divider);
    } else if (isCycleActivity && (data.phase === 'COMPLETE' || data.phase === 'ERROR')) {
      // Remove matching START divider spinner
      const startKey = `cycle-start-${data.cycle_id}`;
      const existing = container.querySelector(`[data-cycle-start="${startKey}"]`);
      if (existing) {
        const spinner = existing.querySelector('.progress-spinner');
        if (spinner) spinner.remove();
        existing.querySelector('.cycle-text').textContent += ' → 완료';
      }
      container.appendChild(createCycleDivider(data, false));
    } else if (isLLMCall && !symbol) {
      // LLM calls without symbol → inline
      container.appendChild(createBubble(data));
    } else {
      container.appendChild(createBubble(data));
    }
  } else {
    // Symbol-specific → route to stock card
    const cardKey = `${data.cycle_id || 'ev'}:${symbol}`;
    let card = stockCards[cardKey];

    // 정확한 키 매칭 실패 시 → 같은 종목의 진행 중인 카드에 합류
    if (!card) {
      for (const [key, existing] of Object.entries(stockCards)) {
        if (key.endsWith(':' + symbol) && (!existing.outcome || existing.outcome === 'progress')) {
          card = existing;
          stockCards[cardKey] = card;  // alias 등록
          break;
        }
      }
    }

    if (!card) {
      card = createStockCard(symbol, data);
      stockCards[cardKey] = card;
      container.appendChild(card.element);
    }
    addStepToCard(card, data);
    updateCardHeader(card);
  }

  activityCount++;
  document.getElementById('activity-count').textContent = `${activityCount}건`;

  if (autoScroll) {
    container.scrollTop = container.scrollHeight;
  }
}

/**
 * 사이클 구분선 생성
 */
function createCycleDivider(data, isStart) {
  const div = document.createElement('div');
  div.className = 'cycle-divider';
  if (isStart) {
    div.setAttribute('data-cycle-start', `cycle-start-${data.cycle_id}`);
    div.innerHTML = `<span class="progress-spinner"></span><span class="cycle-text">${escapeHtml(data.summary)}</span>`;
  } else {
    const time = formatTime(data.created_at);
    const elapsed = data.execution_time_ms ? ` (${(data.execution_time_ms / 1000).toFixed(1)}초)` : '';
    div.innerHTML = `<span>${escapeHtml(data.summary)}${elapsed}</span><span class="text-gray-600">${time}</span>`;
  }
  return div;
}

/**
 * 종목 카드 생성
 */
function createStockCard(symbol, firstActivity) {
  const el = document.createElement('div');
  el.className = 'stock-card outcome-progress';

  // Extract stock name from summary: [종목명] or [심볼]
  const nameMatch = (firstActivity.summary || '').match(/\[([^\]]+)\]/);
  const stockName = nameMatch ? nameMatch[1] : symbol;

  // Header
  const header = document.createElement('div');
  header.className = 'stock-card-header';
  header.innerHTML = `
    <span class="text-sm">📊</span>
    <span class="text-sm font-medium text-white flex-1 truncate">
      ${escapeHtml(stockName)} <span class="text-gray-500 text-xs">${escapeHtml(symbol)}</span>
    </span>
    <span class="stock-outcome text-xs px-2 py-0.5 rounded bg-purple-900/40 text-purple-300">
      <span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:4px"></span>분석 중
    </span>
    <span class="stock-elapsed text-xs text-gray-600"></span>
    <span class="stock-expand text-gray-500 text-xs transition-transform" style="transform:rotate(-90deg)">▼</span>
  `;
  header.onclick = () => toggleCardBody(card);

  // Body
  const body = document.createElement('div');
  body.className = 'stock-card-body'; // default: collapsed

  const steps = document.createElement('div');
  steps.className = 'stock-card-steps';
  body.appendChild(steps);

  el.appendChild(header);
  el.appendChild(body);

  const card = {
    element: el,
    headerEl: header,
    bodyEl: body,
    stepsEl: steps,
    activities: [],
    symbol: symbol,
    stockName: stockName,
    outcome: null,       // BUY, SELL, HOLD, ERROR
    confidence: null,
    totalElapsed: 0,
    isOpen: false,
    startTime: Date.now(),
    liveTimer: null,
  };

  // Start live elapsed timer
  card.liveTimer = setInterval(() => {
    if (card.outcome && card.outcome !== 'progress') {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
      return;
    }
    const elapsed = ((Date.now() - card.startTime) / 1000).toFixed(0);
    const elapsedEl = card.headerEl.querySelector('.stock-elapsed');
    if (elapsedEl) elapsedEl.textContent = `${elapsed}초`;
  }, 1000);

  return card;
}

/**
 * 카드에 활동 스텝 추가
 */
function addStepToCard(card, data) {
  card.activities.push(data);

  const progressKey = getProgressKey(data);

  // START → compact progress indicator
  if (data.phase === 'START') {
    const step = document.createElement('div');
    step.className = 'stock-step';
    step.setAttribute('data-progress-key', progressKey);
    const time = formatTime(data.created_at);
    const label = (data.summary || '').replace(/시작$/, '').trim();
    step.innerHTML = `
      <span class="text-xs text-gray-600 shrink-0 w-14">${time}</span>
      <span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px"></span>
      <span class="text-xs text-gray-400">${escapeHtml(label)}...</span>
    `;
    card.stepsEl.appendChild(step);
    return;
  }

  // COMPLETE/ERROR → remove matching START spinner
  if (data.phase === 'COMPLETE' || data.phase === 'ERROR') {
    const existing = card.stepsEl.querySelector(`[data-progress-key="${progressKey}"]`);
    if (existing) existing.remove();
  }

  // Create step element
  const step = document.createElement('div');
  step.className = 'stock-step';
  const time = formatTime(data.created_at);
  const typeColor = getTypeColor(data.activity_type);
  const elapsed = data.execution_time_ms ? `${(data.execution_time_ms / 1000).toFixed(1)}초` : '';

  let html = `
    <span class="text-xs text-gray-600 shrink-0 w-14">${time}</span>
    <div class="flex-1 min-w-0">
      <div class="text-xs">${escapeHtml(data.summary)}</div>`;

  // Meta line
  const meta = [];
  if (data.llm_provider) meta.push(`<span class="text-${typeColor}-400">${data.llm_provider}</span>`);
  if (elapsed) meta.push(elapsed);
  if (data.confidence != null) {
    const pct = Math.round(data.confidence * 100);
    meta.push(`신뢰도 ${pct}%`);
  }
  if (meta.length) {
    html += `<div class="text-xs text-gray-600 mt-0.5">${meta.join(' · ')}</div>`;
  }

  // Detail (expandable)
  if (data.detail) {
    const detailId = 'sd-' + Math.random().toString(36).substr(2, 6);
    const isLLMCall = data.activity_type === 'LLM_CALL';
    html += `
      <button onclick="event.stopPropagation(); toggleDetail('${detailId}')" class="text-xs text-gray-600 hover:text-gray-400 mt-0.5">
        ${isLLMCall ? '💬 LLM 대화' : '▸ 상세'}
      </button>
      <div id="${detailId}" class="detail-content mt-1 text-xs bg-dark-900/50 rounded p-2 text-gray-400">
        ${isLLMCall ? formatLLMConversation(data.detail) : `<pre class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${formatDetail(data.detail)}</pre>`}
      </div>`;
  }

  // Error
  if (data.error_message) {
    html += `<div class="text-xs text-red-400 mt-0.5">${escapeHtml(data.error_message)}</div>`;
  }

  html += '</div>';
  step.innerHTML = html;
  card.stepsEl.appendChild(step);
}

/**
 * 카드 헤더 업데이트 (최신 활동 기반)
 */
function updateCardHeader(card) {
  const acts = card.activities;
  let outcome = 'progress';
  let outcomeText = '<span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:4px"></span>분석 중';
  let outcomeBg = 'bg-purple-900/40 text-purple-300';
  let totalMs = 0;

  for (const a of acts) {
    if (a.execution_time_ms) totalMs += a.execution_time_ms;

    // Error
    if (a.phase === 'ERROR' || a.error_message) {
      outcome = 'error';
      outcomeText = '❌ 오류';
      outcomeBg = 'bg-yellow-900/40 text-yellow-300';
    }

    // SKIP (데이터 부족, 리스크 차단 등) → HOLD 처리
    if (a.phase === 'SKIP' && outcome !== 'error') {
      outcome = 'hold';
      outcomeText = '⏭ 스킵';
      outcomeBg = 'bg-gray-700/60 text-gray-400';
    }

    // Tier1 result — 방향 결정
    if (a.activity_type === 'TIER1_ANALYSIS' && a.phase === 'COMPLETE') {
      const summ = a.summary || '';
      if (summ.includes('HOLD') || summ.includes('실패')) {
        outcome = 'hold';
        outcomeText = summ.includes('실패') ? '⚠ 분석 실패' : '⏸ HOLD';
        outcomeBg = 'bg-gray-700/60 text-gray-400';
      } else if (summ.includes('BUY')) {
        outcome = 'buy';
        outcomeText = '📈 매수';
        outcomeBg = 'bg-red-900/40 text-red-300';
      } else if (summ.includes('SELL')) {
        outcome = 'sell';
        outcomeText = '📉 매도';
        outcomeBg = 'bg-blue-900/40 text-blue-300';
      }
    }

    // Tier2 — 미승인만 뒤집음, 승인은 기존 방향 유지
    if (a.activity_type === 'TIER2_REVIEW' && a.phase === 'COMPLETE') {
      const summ = a.summary || '';
      if (summ.includes('미승인')) {
        outcome = outcome !== 'error' ? 'hold' : outcome;
        outcomeText = '⛔ 미승인';
        outcomeBg = 'bg-gray-700/60 text-gray-400';
      }
    }

    // Strategy eval — HOLD/스킵
    if (a.activity_type === 'STRATEGY_EVAL' && a.phase === 'COMPLETE') {
      const summ = a.summary || '';
      if ((summ.includes('HOLD') || summ.includes('스킵')) && outcome !== 'error') {
        outcome = 'hold';
        outcomeText = '⏸ HOLD';
        outcomeBg = 'bg-gray-700/60 text-gray-400';
      }
    }

    // 주문 실행/체결 — 방향 유지, 상태만 갱신
    if (a.activity_type === 'DECISION' || a.activity_type === 'ORDER') {
      const summ = a.summary || '';
      const isSell = outcome === 'sell' || summ.includes('SELL') || summ.includes('매도');
      if (a.phase === 'COMPLETE' && (summ.includes('주문 접수') || summ.includes('체결'))) {
        outcome = isSell ? 'sell' : 'buy';
        outcomeText = isSell ? '📉 매도 완료' : '📈 매수 완료';
        outcomeBg = isSell ? 'bg-blue-900/40 text-blue-300' : 'bg-red-900/40 text-red-300';
      } else if (summ.includes('주문 실행')) {
        // 주문 접수 전 — 방향만 표시
        if (outcome !== 'buy' && outcome !== 'sell') {
          outcome = isSell ? 'sell' : 'buy';
          outcomeText = isSell ? '📉 매도' : '📈 매수';
          outcomeBg = isSell ? 'bg-blue-900/40 text-blue-300' : 'bg-red-900/40 text-red-300';
        }
      }
    }

    // Confidence
    if (a.confidence != null) {
      card.confidence = a.confidence;
    }
  }

  card.outcome = outcome;
  card.totalElapsed = totalMs;

  // Update outcome badge
  const outcomeEl = card.headerEl.querySelector('.stock-outcome');
  if (outcomeEl) {
    outcomeEl.className = `stock-outcome text-xs px-2 py-0.5 rounded ${outcomeBg}`;
    outcomeEl.innerHTML = outcomeText;
  }

  // Update elapsed — when done, stop live timer and show final time
  if (outcome !== 'progress') {
    if (card.liveTimer) {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
    }
    const elapsedEl = card.headerEl.querySelector('.stock-elapsed');
    if (elapsedEl && totalMs > 0) {
      elapsedEl.textContent = `${(totalMs / 1000).toFixed(1)}초`;
    }
  }

  // Update card border color
  card.element.className = `stock-card outcome-${outcome}`;
}

/**
 * 카드 바디 토글
 */
function toggleCardBody(card) {
  card.isOpen = !card.isOpen;
  card.bodyEl.classList.toggle('open', card.isOpen);
  const arrow = card.headerEl.querySelector('.stock-expand');
  if (arrow) arrow.style.transform = card.isOpen ? '' : 'rotate(-90deg)';
}

// ══════════════════════════════════════════════════════════
// ── Legacy Bubble (for non-grouped activities) ──
// ══════════════════════════════════════════════════════════

function createBubble(data) {
  const div = document.createElement('div');
  div.className = 'chat-bubble';

  const time = formatTime(data.created_at);
  const typeColor = getTypeColor(data.activity_type);

  let html = `
    <div class="flex items-start gap-2 px-3 py-1.5 rounded-lg hover:bg-dark-700/50 transition group">
      <span class="text-xs text-gray-500 mt-0.5 shrink-0 w-14">${time}</span>
      <div class="flex-1 min-w-0">
        <div class="text-sm whitespace-pre-wrap">${escapeHtml(data.summary)}</div>`;

  const meta = [];
  if (data.llm_provider) meta.push(`<span class="text-${typeColor}-400">${data.llm_provider}</span>`);
  if (data.execution_time_ms) meta.push(`${(data.execution_time_ms / 1000).toFixed(1)}초`);
  if (data.confidence != null) {
    const pct = Math.round(data.confidence * 100);
    meta.push(`신뢰도 ${pct}%`);
  }
  if (meta.length) {
    html += `<div class="flex items-center gap-3 mt-0.5 text-xs text-gray-500">${meta.join(' | ')}</div>`;
  }

  if (data.detail) {
    const detailId = 'detail-' + (data.id || Math.random().toString(36).substr(2, 6));
    const isLLMCall = data.activity_type === 'LLM_CALL';
    html += `
      <button onclick="toggleDetail('${detailId}')" class="text-xs text-gray-500 hover:text-gray-300 mt-1">
        ${isLLMCall ? '💬 LLM 대화 보기' : '▼ 상세 보기'}
      </button>
      <div id="${detailId}" class="detail-content mt-1 text-xs bg-dark-900 rounded p-2 text-gray-400">
        ${isLLMCall ? formatLLMConversation(data.detail) : `<pre class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${formatDetail(data.detail)}</pre>`}
      </div>`;
  }

  if (data.error_message) {
    html += `<div class="text-xs text-red-400 mt-1">${escapeHtml(data.error_message)}</div>`;
  }

  html += `</div></div>`;
  div.innerHTML = html;
  return div;
}

function toggleDetail(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
}

// ── View Switching ──
function switchView(view) {
  currentView = view;
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm text-gray-400 hover:bg-dark-700';
  });
  const activeBtn = document.getElementById(`nav-${view}`);
  if (activeBtn) {
    activeBtn.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm font-medium bg-blue-900/30 text-blue-300';
  }
  if (view === 'live') {
    loadTodayActivities();
  } else if (view === 'today') {
    loadReport('today');
  }
}

function switchToReport(dateStr) {
  currentView = 'report';
  loadReport(dateStr);
}

// ── Data Loading ──
async function loadTodayActivities() {
  const container = document.getElementById('chat-container');
  container.innerHTML = '<div class="text-center text-gray-500 text-sm py-4">불러오는 중...</div>';
  // Clear card tracking
  cleanupStockCards();

  try {
    const resp = await fetch(`${API}/activities?limit=500`);
    const json = await resp.json();
    container.innerHTML = '';
    activityCount = 0;

    if (json.data && json.data.length) {
      // Pre-process: filter resolved STARTs
      const activities = filterResolvedStarts(json.data);
      activities.forEach(a => appendActivity(a));

      // History load: stop all timers and finalize stuck cards
      for (const card of Object.values(stockCards)) {
        if (card.liveTimer) {
          clearInterval(card.liveTimer);
          card.liveTimer = null;
        }
        // 히스토리 로드 후 여전히 progress면 → 종료된 분석으로 처리
        if (card.outcome === 'progress') {
          card.outcome = 'hold';
          const outcomeEl = card.headerEl.querySelector('.stock-outcome');
          if (outcomeEl) {
            outcomeEl.className = 'stock-outcome text-xs px-2 py-0.5 rounded bg-gray-700/60 text-gray-400';
            outcomeEl.innerHTML = '⏸ 완료';
          }
          card.element.className = 'stock-card outcome-hold';
        }
      }

      requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
      });
    } else {
      container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">아직 활동 기록이 없습니다</div>';
    }
  } catch (err) {
    container.innerHTML = `<div class="text-center text-red-400 text-sm py-8">로드 실패: ${err.message}</div>`;
  }
}

function filterResolvedStarts(activities) {
  const resolved = new Set();
  activities.forEach(a => {
    if (a.phase === 'COMPLETE' || a.phase === 'ERROR') {
      resolved.add(getProgressKey(a));
    }
  });
  return activities.filter(a => {
    if (a.phase === 'START' && resolved.has(getProgressKey(a))) return false;
    return true;
  });
}

function getProgressKey(data) {
  const match = (data.summary || '').match(/\[([^\]]+)\]/);
  const symbol = match ? match[1] : '';
  return `${data.activity_type}:${symbol}`;
}

// ── Clear Chat ──
function clearChat() {
  const container = document.getElementById('chat-container');
  container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">화면을 비웠습니다. 새 활동이 들어오면 여기에 표시됩니다.</div>';
  activityCount = 0;
  document.getElementById('activity-count').textContent = '0건';
  cleanupStockCards();
}

function cleanupStockCards() {
  for (const card of Object.values(stockCards)) {
    if (card.liveTimer) clearInterval(card.liveTimer);
  }
  stockCards = {};
}

// ── Reports ──
async function loadReport(dateStr) {
  const container = document.getElementById('chat-container');
  container.innerHTML = '<div class="text-center text-gray-500 text-sm py-4">리포트 불러오는 중...</div>';
  cleanupStockCards();

  try {
    let url = `${API}/reports/latest`;
    if (dateStr && dateStr !== 'today') url = `${API}/reports/${dateStr}`;
    const resp = await fetch(url);
    const json = await resp.json();
    const report = json.data;

    if (!report) {
      container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">해당 날짜의 리포트가 없습니다</div>';
      if (dateStr && dateStr !== 'today') await loadDateActivities(dateStr, container);
      return;
    }
    container.innerHTML = '';
    container.appendChild(createReportCard(report));
    if (report.report_date) await loadDateActivities(report.report_date, container);
  } catch (err) {
    container.innerHTML = `<div class="text-center text-red-400 text-sm py-8">리포트 로드 실패: ${err.message}</div>`;
  }
}

async function loadDateActivities(dateStr, container) {
  try {
    const resp = await fetch(`${API}/activities?target_date=${dateStr}&limit=500`);
    const json = await resp.json();
    if (json.data && json.data.length) {
      const section = document.createElement('div');
      section.className = 'mt-4 border-t border-gray-800';
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'w-full text-center text-gray-500 hover:text-gray-300 text-xs py-3 flex items-center justify-center gap-2 transition';
      toggleBtn.innerHTML = `<span class="activity-toggle-icon">▶</span> ${dateStr} 활동 로그 (${json.data.length}건)`;
      const logContainer = document.createElement('div');
      logContainer.className = 'hidden';
      logContainer.style.maxHeight = '600px';
      logContainer.style.overflowY = 'auto';
      json.data.forEach(a => logContainer.appendChild(createBubble(a)));
      toggleBtn.onclick = () => {
        const isHidden = logContainer.classList.contains('hidden');
        logContainer.classList.toggle('hidden');
        toggleBtn.querySelector('.activity-toggle-icon').innerHTML = isHidden ? '▼' : '▶';
      };
      section.appendChild(toggleBtn);
      section.appendChild(logContainer);
      container.appendChild(section);
    }
  } catch (err) {
    console.error('Activities load error:', err);
  }
}

function createReportCard(report) {
  const div = document.createElement('div');
  div.className = 'bg-dark-700 rounded-xl p-5 border border-gray-600 mx-2 chat-bubble';
  const winRate = (report.win_count + report.loss_count) > 0
    ? ((report.win_count / (report.win_count + report.loss_count)) * 100).toFixed(1)
    : '-';
  const realizedPnlColor = report.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const unrealizedPnl = report.unrealized_pnl || 0;
  const unrealizedPnlColor = unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const buyCount = report.buy_count || 0;
  const sellCount = report.sell_count || 0;
  const openCount = report.open_position_count || 0;
  let topPicks = '';
  try {
    const picks = JSON.parse(report.top_picks || '[]');
    topPicks = picks.map(p => typeof p === 'string' ? p : `${p.name || ''}(${p.symbol || ''})`).filter(Boolean).join(', ');
  } catch(e) {}

  div.innerHTML = `
    <div class="text-lg font-bold text-white mb-4">📋 ${report.report_date} 일일 리포트</div>
    <div class="grid grid-cols-3 gap-3 mb-3">
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-2xl font-bold text-blue-400">${report.total_cycles}</div>
        <div class="text-xs text-gray-500">사이클</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-2xl font-bold text-purple-400">${report.total_analyses}</div>
        <div class="text-xs text-gray-500">분석</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-2xl font-bold text-yellow-400">${buyCount}<span class="text-xs text-gray-500">매수</span> / ${sellCount}<span class="text-xs text-gray-500">매도</span></div>
        <div class="text-xs text-gray-500">주문 (보유 ${openCount}종목)</div>
      </div>
    </div>
    <div class="grid grid-cols-2 gap-3 mb-4">
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold ${realizedPnlColor}">${report.total_pnl >= 0 ? '+' : ''}${report.total_pnl.toLocaleString()}원</div>
        <div class="text-xs text-gray-500">실현 손익 (승률 ${winRate}%)</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold ${unrealizedPnlColor}">${unrealizedPnl >= 0 ? '+' : ''}${unrealizedPnl.toLocaleString()}원</div>
        <div class="text-xs text-gray-500">미실현 손익</div>
      </div>
    </div>
    ${report.market_summary ? `
    <div class="mb-3">
      <div class="text-sm font-medium text-gray-300 mb-1">📝 오늘 리뷰</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.market_summary)}</div>
    </div>` : ''}
    ${report.performance_review ? `
    <div class="mb-3">
      <div class="text-sm font-medium text-gray-300 mb-1">📊 포트폴리오 진단</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.performance_review)}</div>
    </div>` : ''}
    ${report.lessons_learned ? `
    <div class="mb-3">
      <div class="text-sm font-medium text-gray-300 mb-1">🔮 내일 전망</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.lessons_learned)}</div>
    </div>` : ''}
    ${report.next_day_plan ? `
    <div class="mb-3">
      <div class="text-sm font-medium text-gray-300 mb-1">📈 액션 플랜</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.next_day_plan)}</div>
    </div>` : ''}
    ${topPicks ? `
    <div class="mb-2">
      <div class="text-sm font-medium text-gray-300 mb-1">🎯 관심 종목</div>
      <div class="text-xs text-gray-400 bg-dark-900 rounded p-2">${escapeHtml(topPicks)}</div>
    </div>` : ''}`;
  return div;
}

// ── Settings ──
async function loadSettings() {
  try {
    const resp = await fetch(`${API}/settings`);
    const json = await resp.json();
    const s = json.data;
    if (!s) return;
    document.getElementById('set-trading').checked = s.TRADING_ENABLED;
    document.getElementById('set-mode').value = s.AUTONOMY_MODE;
    const riskEl = document.getElementById('set-risk-appetite');
    if (riskEl && s.RISK_APPETITE) riskEl.value = s.RISK_APPETITE;
    updateBadge('badge-trading', s.TRADING_ENABLED ? '매매:ON' : '매매:OFF', s.TRADING_ENABLED ? 'green' : 'red');
    updateBadge('badge-mode', s.AUTONOMY_MODE, 'purple');
  } catch (err) {
    console.error('Settings load error:', err);
  }
}

async function updateSetting(key, value) {
  try {
    await fetch(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    loadSettings();
    loadSystemStatus();
  } catch (err) {
    console.error('Setting update error:', err);
  }
}

// ── LLM Status ──
async function loadLLMStatus() {
  try {
    const resp = await fetch(`${API}/llm/status`);
    const json = await resp.json();
    const s = json.data;
    if (!s) return;
    const t1Model = document.getElementById('llm-tier1-model');
    if (t1Model) t1Model.textContent = `모델: ${s.tier1.model}`;
  } catch (err) {
    console.error('LLM status error:', err);
  }
}

// ── LLM Usage ──
async function loadLLMUsage() {
  try {
    const resp = await fetch(`${API}/llm/usage`);
    const json = await resp.json();
    const d = json.data;
    if (!d) {
      document.getElementById('usage-summary').innerHTML = '<div class="text-gray-600 text-xs">데이터 없음</div>';
      return;
    }
    document.getElementById('usage-summary').innerHTML = `
      <div class="flex justify-between">
        <span class="text-gray-400">총 세션</span>
        <span class="text-white">${d.total_sessions.toLocaleString()}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">총 메시지</span>
        <span class="text-white">${d.total_messages.toLocaleString()}</span>
      </div>`;
    const appEl = document.getElementById('usage-app');
    const app = d.app_usage;
    if (app && app.total_calls > 0) {
      let html = `
        <div class="flex justify-between"><span class="text-gray-400">호출 수</span><span class="text-cyan-400">${app.total_calls}</span></div>
        <div class="flex justify-between"><span class="text-gray-400">출력 토큰</span><span class="text-green-400">${formatTokens(app.total_output_tokens)}</span></div>`;
      if (app.by_model && Object.keys(app.by_model).length) {
        for (const [model, mu] of Object.entries(app.by_model)) {
          const short = model.replace('claude-', '').replace(/-\d{8,}$/, '');
          html += `<div class="bg-dark-900 rounded p-1.5 mt-1">
            <div class="text-gray-300 text-xs">${short} <span class="text-gray-600">(${mu.calls}회)</span></div>
            <div class="text-gray-500">${formatTokens(mu.input_tokens)} in / ${formatTokens(mu.output_tokens)} out</div>
          </div>`;
        }
      }
      appEl.innerHTML = html;
    } else {
      appEl.innerHTML = '<div class="text-gray-600">아직 호출 없음</div>';
    }
    const modelsEl = document.getElementById('usage-models');
    if (d.model_usage && Object.keys(d.model_usage).length) {
      let html = '';
      for (const [model, usage] of Object.entries(d.model_usage)) {
        const shortModel = model.replace('claude-', '').replace(/-\d{8}$/, '');
        html += `<div class="bg-dark-900 rounded p-2 mb-1">
          <div class="text-gray-300 font-medium mb-1" title="${model}">${shortModel}</div>
          <div class="grid grid-cols-2 gap-x-2 gap-y-0.5 text-gray-500">
            <span>입력</span><span class="text-right text-gray-400">${formatTokens(usage.inputTokens)}</span>
            <span>출력</span><span class="text-right text-green-400">${formatTokens(usage.outputTokens)}</span>
            <span>캐시읽기</span><span class="text-right text-blue-400">${formatTokens(usage.cacheReadInputTokens)}</span>
            <span>캐시생성</span><span class="text-right text-purple-400">${formatTokens(usage.cacheCreationInputTokens)}</span>
          </div>
        </div>`;
      }
      modelsEl.innerHTML = html;
    } else {
      modelsEl.innerHTML = '<div class="text-gray-600">데이터 없음</div>';
    }
    const chartEl = document.getElementById('usage-chart');
    const dailyTokens = (d.daily_model_tokens || []).slice(-7);
    if (dailyTokens.length) {
      const totals = dailyTokens.map(day => {
        let sum = 0;
        for (const t of Object.values(day.tokensByModel || {})) sum += t;
        return { date: day.date, tokens: sum };
      });
      const maxTokens = Math.max(...totals.map(t => t.tokens), 1);
      chartEl.innerHTML = totals.map(t => {
        const pct = Math.max((t.tokens / maxTokens) * 100, 2);
        const dateLabel = t.date.slice(5);
        return `<div class="flex items-center gap-2">
          <span class="text-gray-500 w-12 shrink-0">${dateLabel}</span>
          <div class="flex-1 bg-dark-900 rounded-full h-3 overflow-hidden">
            <div class="h-full bg-cyan-500/60 rounded-full" style="width:${pct}%"></div>
          </div>
          <span class="text-gray-400 w-14 text-right shrink-0">${formatTokens(t.tokens)}</span>
        </div>`;
      }).join('');
    } else {
      chartEl.innerHTML = '<div class="text-gray-600">데이터 없음</div>';
    }
  } catch (err) {
    console.error('LLM usage error:', err);
    document.getElementById('usage-summary').innerHTML = '<div class="text-gray-600 text-xs">조회 실패</div>';
  }
}

function formatTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1000000000) return (n / 1000000000).toFixed(1) + 'B';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString();
}

// ── System Status ──
async function loadSystemStatus() {
  try {
    const resp = await fetch(`${API}/system/status`);
    const json = await resp.json();
    const s = json.data;
    if (!s) return;
    updateBadge('badge-trading', s.trading_enabled ? '매매:ON' : '매매:OFF', s.trading_enabled ? 'green' : 'red');
    updateBadge('badge-mcp', s.mcp_connected ? 'MCP:✓' : 'MCP:✗', s.mcp_connected ? 'green' : 'red');
    const statusEl = document.getElementById('sys-status');
    const isHoliday = !!s.market_holiday;
    const marketLabel = s.market_open ? '장중' : (isHoliday ? `휴장 (${s.market_holiday})` : '장외');
    const marketColor = s.market_open ? 'bg-green-400' : (isHoliday ? 'bg-yellow-400' : 'bg-gray-500');
    const marketExtra = s.market_open ? '' : ` (다음: ${s.next_market_open || ''})`;
    statusEl.innerHTML = `
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${marketColor}"></span>
        <strong>${marketLabel}</strong>${marketExtra}
      </div>
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${s.mcp_connected ? 'bg-green-400' : 'bg-red-400'}"></span>
        MCP: ${s.mcp_connected ? '연결' : '끊김'}
      </div>
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${s.scheduler_running ? 'bg-green-400' : 'bg-yellow-400'}"></span>
        스케줄러: ${s.scheduler_running ? '동작' : '중지'}
      </div>
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${s.agent_running ? 'bg-green-400' : 'bg-yellow-400'}"></span>
        에이전트: ${s.agent_running ? '동작' : '중지'}
      </div>
      ${s.last_cycle_time ? `<div class="text-gray-600">마지막: ${formatTime(s.last_cycle_time)}</div>` : ''}
      <div class="text-gray-600">SSE: ${s.sse_clients}명</div>`;
    const triggerBtn = document.querySelector('[onclick="triggerCycle()"]');
    if (triggerBtn) {
      triggerBtn.textContent = s.market_open ? '▶ 매매 사이클 실행' : '▶ 장마감 리뷰 실행';
    }
  } catch (err) {
    console.error('Status load error:', err);
  }
}

// ── Report List ──
async function loadReportList() {
  try {
    const resp = await fetch(`${API}/reports?limit=10`);
    const json = await resp.json();
    const listEl = document.getElementById('report-list');
    listEl.innerHTML = '';
    if (json.data && json.data.length) {
      json.data.forEach(r => {
        const btn = document.createElement('button');
        btn.className = 'w-full text-left px-3 py-1 text-xs text-gray-400 hover:bg-dark-700 rounded';
        btn.textContent = r.report_date;
        btn.onclick = () => switchToReport(r.report_date);
        listEl.appendChild(btn);
      });
    } else {
      listEl.innerHTML = '<div class="px-3 text-xs text-gray-600">리포트 없음</div>';
    }
  } catch (err) {
    console.error('Report list error:', err);
  }
}

// ── Actions ──
let triggerPending = false;
async function triggerCycle() {
  if (triggerPending) return;
  triggerPending = true;
  try {
    await fetch(`${API}/agent/trigger`, { method: 'POST' });
  } catch (err) {
    console.error('Trigger error:', err);
  } finally {
    setTimeout(() => { triggerPending = false; }, 3000);
  }
}

async function generateReport() {
  try {
    await fetch(`${API}/reports/generate`, { method: 'POST' });
    loadReportList();
  } catch (err) {
    console.error('Report gen error:', err);
  }
}

// ── Utilities ──
function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return ts; }
}

function formatDetail(detail) {
  if (!detail) return '';
  try {
    const obj = typeof detail === 'string' ? JSON.parse(detail) : detail;
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(detail);
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function getTypeColor(type) {
  const map = {
    CYCLE: 'blue', SCAN: 'cyan', SCREENING: 'purple',
    TIER1_ANALYSIS: 'yellow', TIER2_REVIEW: 'green',
    STRATEGY_EVAL: 'blue', RISK_CHECK: 'yellow',
    RISK_TUNING: 'purple',
    DECISION: 'green', EVENT: 'gray', REPORT: 'purple',
    LLM_CALL: 'cyan', ORDER: 'red', DAILY_PLAN: 'purple',
    TRADE_RESULT: 'green', RISK_GATE: 'red',
  };
  return map[type] || 'gray';
}

function formatLLMConversation(detail) {
  let obj = detail;
  try {
    if (typeof detail === 'string') obj = JSON.parse(detail);
  } catch { return `<pre class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${escapeHtml(String(detail))}</pre>`; }
  const sys = obj.llm_system_prompt || '';
  const prompt = obj.llm_prompt || '';
  const response = obj.llm_response || '';
  const model = obj.llm_model || '';
  let html = '';
  if (model) html += `<div class="llm-model-tag">${escapeHtml(model)}</div>`;
  if (sys) html += `<div class="llm-msg llm-system"><div class="llm-role">SYSTEM</div><div class="llm-body">${escapeHtml(sys)}</div></div>`;
  if (prompt) html += `<div class="llm-msg llm-user"><div class="llm-role">PROMPT</div><div class="llm-body">${escapeHtml(prompt)}</div></div>`;
  if (response) html += `<div class="llm-msg llm-assistant"><div class="llm-role">RESPONSE</div><div class="llm-body">${escapeHtml(response)}</div></div>`;
  return `<div class="llm-conversation">${html}</div>`;
}

function getPhaseIcon(phase) {
  return { START: '▶', PROGRESS: '◆', COMPLETE: '✓', ERROR: '✗' }[phase] || '•';
}

function updateBadge(id, text, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  const colors = {
    green: 'bg-green-900/50 text-green-300',
    red: 'bg-red-900/50 text-red-300',
    yellow: 'bg-yellow-900/50 text-yellow-300',
    purple: 'bg-purple-900/50 text-purple-300',
    blue: 'bg-blue-900/50 text-blue-300',
  };
  el.className = `px-2 py-0.5 rounded text-xs font-medium ${colors[color] || colors.blue}`;
}

function setStatus(state, text) {
  const el = document.getElementById('status-text');
  if (el) el.textContent = text;
}

// Auto-scroll detection
document.getElementById('chat-container').addEventListener('scroll', function() {
  const el = this;
  autoScroll = (el.scrollHeight - el.scrollTop - el.clientHeight) < 50;
});
