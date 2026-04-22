/**
 * MOMO Trading Admin Dashboard — SSE + Stock-Grouped Chat UI
 */
const API = '/api/v1/admin';
let currentView = 'live';
let activityCount = 0;
let autoScroll = true;
let accountPollTimer = null;
let currentAgentFilter = 'all';

// 종목 심볼 → 이름 매핑 (서버에서 로드)
let symbolNames = {};

// Stock card tracking: key = "cycleId:symbol" → { element, headerEl, bodyEl, stepsEl, activities[], outcome }
let stockCards = {};

// Sidebar section state
const sidebarState = {
  account: true,
  holdings: true,
  'agent-limits': true,
  system: true,
};

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  loadSystemStatus();
  loadReportList();
  loadAccountInfo();
  loadLLMStatus();
  loadSymbolNames();
  connectSSE();
  loadTodayActivities();
  initSidebarSections();
  setInterval(loadSystemStatus, 15000);
  setInterval(loadSymbolNames, 60000); // 1분마다 종목명 갱신
  accountPollTimer = setInterval(loadAccountInfo, 30000);
  setInterval(updateReviewCountdowns, 1000); // 재평가 카운트다운 1초 갱신
});

// 보유종목 카드의 재평가 카운트다운 갱신
function updateReviewCountdowns() {
  const now = Date.now();
  document.querySelectorAll('.review-countdown').forEach(el => {
    const iso = el.dataset.reviewAt;
    if (!iso) return;
    const target = new Date(iso).getTime();
    const diff = target - now;
    if (diff <= 0) {
      el.textContent = '재평가 중...';
      el.classList.add('text-yellow-300');
      el.classList.remove('text-purple-300');
      return;
    }
    const totalSec = Math.floor(diff / 1000);
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    if (min > 0) {
      el.textContent = `${min}분 ${sec}초 후`;
    } else {
      el.textContent = `${sec}초 후`;
    }
    el.classList.add('text-purple-300');
    el.classList.remove('text-yellow-300');
  });
}

async function loadSymbolNames() {
  try {
    const resp = await fetch(`${API}/symbol-names`);
    const json = await resp.json();
    if (json.data) symbolNames = json.data;
  } catch (e) { /* ignore */ }
}

// ── Sidebar Accordion ──
function initSidebarSections() {
  for (const [id, isOpen] of Object.entries(sidebarState)) {
    const body = document.getElementById(`body-${id}`);
    const arrow = document.getElementById(`arrow-${id}`);
    if (body) {
      body.classList.toggle('open', isOpen);
    }
    if (arrow) {
      arrow.classList.toggle('collapsed', !isOpen);
    }
  }
}

function toggleSidebarSection(id) {
  sidebarState[id] = !sidebarState[id];
  const body = document.getElementById(`body-${id}`);
  const arrow = document.getElementById(`arrow-${id}`);
  if (body) body.classList.toggle('open', sidebarState[id]);
  if (arrow) arrow.classList.toggle('collapsed', !sidebarState[id]);
}

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
        // AI 한도 결정 / 국면 변화 → 즉시 에이전트 판단 갱신
        if (msg.data && msg.data.activity_type === 'RISK_TUNING' && msg.data.phase === 'COMPLETE') {
          loadAccountInfo();
        }
        if (msg.data && msg.data.activity_type === 'EVENT' &&
            typeof msg.data.summary === 'string' && msg.data.summary.includes('국면 변경')) {
          loadAccountInfo();
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
    const [balResp, holdResp, limitsResp] = await Promise.all([
      fetch(`${API}/account/balance`),
      fetch(`${API}/account/holdings`),
      fetch(`${API}/agent/limits`),
    ]);
    const balJson = await balResp.json();
    const holdJson = await holdResp.json();
    const limitsJson = await limitsResp.json();
    renderAccountBalance(balJson.data);
    renderAccountHoldings(holdJson.data);
    renderAgentLimits(limitsJson.data);
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
  if (!el) return;
  if (!data || !data.length) {
    if (countEl) countEl.textContent = '0';
    el.innerHTML = '<div class="text-gray-600 text-xs">보유종목 없음</div>';
    return;
  }
  if (countEl) countEl.textContent = `${data.length}`;
  el.innerHTML = data.map(h => {
    const pnlColor = h.pnl_rate >= 0 ? 'text-green-400' : 'text-red-400';
    const bgTint = h.pnl_rate >= 0 ? 'bg-green-900/5' : 'bg-red-900/5';
    const evalAmt = h.current_price * h.quantity;
    // 손절/익절가 (event_detector에서 활성 임계값)
    const th = h.thresholds || {};
    const sl = Number(th.stop_loss) || 0;
    const tp = Number(th.take_profit) || 0;
    const trailPct = Number(th.trailing_stop_pct) || 0;
    // 평단가 중심 발산형 바: sl ← 평단가 → tp, 현재가에 따라 중앙에서 좌/우 채움
    const entry = Number(h.avg_buy_price) || 0;
    const cur = Number(h.current_price) || 0;
    let entryBarHtml = '';
    let entryBarLabel = '';
    if (entry > 0) {
      let lossPct = 0, profitPct = 0, overflowLeft = false, overflowRight = false;
      if (cur < entry && sl > 0 && sl < entry) {
        const ratio = (entry - cur) / (entry - sl);
        lossPct = Math.min(Math.max(ratio, 0), 1) * 50;
        if (ratio > 1) overflowLeft = true;
      } else if (cur > entry && tp > entry) {
        const ratio = (cur - entry) / (tp - entry);
        profitPct = Math.min(Math.max(ratio, 0), 1) * 50;
        if (ratio > 1) overflowRight = true;
      }
      const lossHtml = lossPct > 0 ? `<div class="entry-bar-loss" style="width:${lossPct.toFixed(2)}%"></div>` : '';
      const profitHtml = profitPct > 0 ? `<div class="entry-bar-profit" style="width:${profitPct.toFixed(2)}%"></div>` : '';
      const ovlHtml = overflowLeft ? '<div class="entry-bar-overflow-left"></div>' : '';
      const ovrHtml = overflowRight ? '<div class="entry-bar-overflow-right"></div>' : '';
      entryBarHtml = `<div class="entry-bar" title="손절 ${sl > 0 ? Number(sl).toLocaleString() : '—'} ← 평단 ${Number(entry).toLocaleString()} → 익절 ${tp > 0 ? Number(tp).toLocaleString() : '—'}">${lossHtml}${profitHtml}<div class="entry-bar-center"></div>${ovlHtml}${ovrHtml}</div>`;
      // 바 아래 3포인트 라벨: 손절 | 평단 | 익절
      const slLabel = sl > 0 ? Number(sl).toLocaleString() : '—';
      const tpLabel = tp > 0 ? Number(tp).toLocaleString() : '—';
      entryBarLabel = `<div class="flex justify-between text-[10px] text-gray-500">
        <span class="${sl > 0 ? 'text-red-400' : 'text-gray-600'}">${slLabel}</span>
        <span class="text-gray-400">${Number(entry).toLocaleString()}</span>
        <span class="${tp > 0 ? 'text-green-400' : 'text-gray-600'}">${tpLabel}</span>
      </div>`;
    }
    // 트레일링 스탑은 별도 한 줄 유지 (바에 표시 불가)
    let thresholdLine = '';
    if (trailPct > 0) {
      thresholdLine = `<div class="flex justify-end text-gray-500">
        <span class="text-blue-400">트레일 ${trailPct.toFixed(1)}%</span>
      </div>`;
    }
    // 재평가 카운트다운 (data-review-at 속성 → updateReviewCountdowns에서 매초 갱신)
    let reviewLine = '';
    if (th.next_review_at) {
      reviewLine = `<div class="flex justify-between text-gray-500">
        <span class="text-purple-300">🔍 다음 재평가</span>
        <span class="review-countdown text-purple-300" data-review-at="${th.next_review_at}">계산 중...</span>
      </div>`;
    }
    return `<div class="border border-gray-700 rounded p-1.5 space-y-0.5 ${bgTint}">
      <div class="flex justify-between items-center">
        <span class="text-gray-200 font-medium truncate" title="${h.symbol}">${h.name}${h.tradeable_market === 'NXT' ? ' <span class="text-green-400 text-xs">[NXT]</span>' : h.tradeable_market === 'KRX_ONLY' ? ' <span class="text-yellow-500 text-xs">[KRX종가]</span>' : ''}</span>
        <span class="${pnlColor} font-bold text-sm">${h.pnl_rate >= 0 ? '+' : ''}${h.pnl_rate.toFixed(2)}%</span>
      </div>
      ${entryBarHtml}
      ${entryBarLabel}
      <div class="flex justify-between text-gray-500">
        <span>${h.quantity}주 | 평단 ${Number(h.avg_buy_price).toLocaleString()}원</span>
        <span>현재 ${Number(h.current_price).toLocaleString()}원</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>평가 ${formatKRW(evalAmt)}</span>
        <span class="${pnlColor} font-medium">${h.pnl >= 0 ? '+' : ''}${formatKRW(h.pnl)}</span>
      </div>
      ${thresholdLine}
      ${reviewLine}
    </div>`;
  }).join('');
}

function renderAgentLimits(data) {
  const el = document.getElementById('agent-limits-info');
  if (!el) return;
  if (!data) {
    el.innerHTML = '<div class="text-gray-600">조회 실패</div>';
    return;
  }
  const rt = data.risk_tuner || {};
  const rg = data.regime_agent || {};
  const tr = data.trading || {};

  // 시장 국면 뱃지 색상
  const regime = rg.current_regime || tr.market_regime || '미판단';
  const regimeColor = {
    'BULL': 'bg-green-900/40 text-green-300',
    'BEAR': 'bg-red-900/40 text-red-300',
    'THEME': 'bg-purple-900/40 text-purple-300',
    'SIDEWAYS': 'bg-gray-700 text-gray-300',
  }[regime] || 'bg-gray-700 text-gray-400';

  // 숫자 포맷: 0은 "무제한", 그 외는 값
  const fmtLimit = (v, unit) => {
    const n = Number(v) || 0;
    if (n === 0) return '<span class="text-green-400">무제한</span>';
    if (unit === 'KRW') return formatKRW(n);
    if (unit === '%') return `${Number(n).toFixed(0)}%`;
    return `${n}${unit || ''}`;
  };

  const fmtTime = (iso) => {
    if (!iso) return '-';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch { return '-'; }
  };

  // 지수 등락률
  const kospi = rg.kospi || {};
  const kosdaq = rg.kosdaq || {};
  const kospiRate = kospi.change_rate != null ? Number(kospi.change_rate).toFixed(2) : null;
  const kosdaqRate = kosdaq.change_rate != null ? Number(kosdaq.change_rate).toFixed(2) : null;
  const rateColor = (r) => r == null ? 'text-gray-500' : (Number(r) >= 0 ? 'text-green-400' : 'text-red-400');

  const tradeCount = tr.today_trade_count || 0;
  const maxTrades = rt.max_daily_trades || 0;
  const tradeCountStr = maxTrades > 0 ? `${tradeCount}/${maxTrades}` : `${tradeCount}`;

  el.innerHTML = `
    <!-- 시장 국면 -->
    <div class="flex justify-between items-center">
      <span class="text-gray-400">시장 국면</span>
      <span class="px-2 py-0.5 rounded text-xs font-medium ${regimeColor}">${regime}</span>
    </div>
    ${rg.previous_regime ? `
    <div class="flex justify-between text-gray-500">
      <span>이전 국면</span>
      <span>${rg.previous_regime}</span>
    </div>` : ''}
    ${kospiRate != null ? `
    <div class="flex justify-between text-gray-500">
      <span>KOSPI</span>
      <span class="${rateColor(kospiRate)}">${kospiRate >= 0 ? '+' : ''}${kospiRate}%</span>
    </div>` : ''}
    ${kosdaqRate != null ? `
    <div class="flex justify-between text-gray-500">
      <span>KOSDAQ</span>
      <span class="${rateColor(kosdaqRate)}">${kosdaqRate >= 0 ? '+' : ''}${kosdaqRate}%</span>
    </div>` : ''}

    <!-- AI 한도 결정 -->
    <div class="border-t border-gray-700 pt-1.5 mt-1.5">
      <div class="text-gray-500 font-medium mb-1">🎯 AI 한도 결정</div>
      <div class="flex justify-between">
        <span class="text-gray-400">일일 거래</span>
        <span class="text-white">${tradeCountStr}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">주문 한도</span>
        <span class="text-white">${fmtLimit(rt.max_single_order_krw, 'KRW')}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">포지션 한도</span>
        <span class="text-white">${fmtLimit(rt.max_position_pct, '%')}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">최소 현금</span>
        <span class="text-white">${Number(rt.min_cash_ratio || 0).toFixed(0)}%</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">최소 매수량</span>
        <span class="text-white">${rt.min_buy_quantity || 1}주</span>
      </div>
    </div>

    <!-- 스케줄 -->
    <div class="border-t border-gray-700 pt-1.5 mt-1.5">
      <div class="text-gray-500 font-medium mb-1">⏱ 모니터링</div>
      <div class="flex justify-between text-gray-500">
        <span>국면 체크</span>
        <span>${Math.round((rg.regime_check_interval_sec || 0) / 60)}분 간격</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>스캔 주기</span>
        <span>${Math.round((rg.scan_interval_sec || 0) / 60)}분 간격</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>최근 체크</span>
        <span>${fmtTime(rg.last_check_at)}</span>
      </div>
      <div class="flex justify-between text-gray-500">
        <span>최근 스캔</span>
        <span>${fmtTime(rg.last_scan_at)}</span>
      </div>
    </div>

    ${rt.reasoning ? `
    <!-- AI 판단 근거 -->
    <div class="border-t border-gray-700 pt-1.5 mt-1.5">
      <div class="text-gray-500 font-medium mb-1">💭 판단 근거</div>
      <div class="text-gray-400 text-xs leading-relaxed">${escapeHtml(rt.reasoning)}</div>
    </div>` : ''}
  `;
}

function formatKRW(amount) {
  if (amount == null) return '-';
  if (Math.abs(amount) >= 100000000) return (amount / 100000000).toFixed(1) + '억';
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

  const agentCategory = getAgentCategory(data);

  // Non-symbol activities → inline (cycle dividers, daily plan, events without symbol)
  if (!symbol || isCycleActivity || isDailyPlan) {
    if (isCycleActivity && data.phase === 'START') {
      const divider = createCycleDivider(data, true);
      divider.dataset.agentCategory = 'system';
      container.appendChild(divider);
    } else if (isCycleActivity && (data.phase === 'COMPLETE' || data.phase === 'ERROR')) {
      const startKey = `cycle-start-${data.cycle_id}`;
      const existing = container.querySelector(`[data-cycle-start="${startKey}"]`);
      if (existing) {
        const spinner = existing.querySelector('.progress-spinner');
        if (spinner) spinner.remove();
        existing.querySelector('.cycle-text').textContent += ' → 완료';
      }
      const divider = createCycleDivider(data, false);
      divider.dataset.agentCategory = 'system';
      container.appendChild(divider);
    } else if (isLLMCall && !symbol) {
      const bubble = createBubble(data);
      bubble.dataset.agentCategory = agentCategory;
      container.appendChild(bubble);
    } else {
      const bubble = createBubble(data);
      bubble.dataset.agentCategory = agentCategory;
      container.appendChild(bubble);
    }
  } else {
    // Symbol-specific → route to stock card
    const cardKey = `${data.cycle_id || 'ev'}:${symbol}`;
    let card = stockCards[cardKey];

    // 매도 완료 카드는 재사용 금지 — frozen 처리 후 새 카드 생성 경로로
    if (card && card.outcome === 'sell') {
      const frozenKey = `${cardKey}:sold:${card.soldAt || Date.now()}`;
      stockCards[frozenKey] = card;
      delete stockCards[cardKey];
      card = null;
    }

    if (!card) {
      for (const [key, existing] of Object.entries(stockCards)) {
        if (key.endsWith(':' + symbol) && (!existing.outcome || existing.outcome === 'progress' || existing.outcome === 'buy' || existing.outcome === 'hold')) {
          card = existing;
          stockCards[cardKey] = card;
          break;
        }
      }
    }

    if (!card) {
      card = createStockCard(symbol, data);
      stockCards[cardKey] = card;
      card.agentCategories = new Set();
      container.appendChild(card.element);
    } else if (card.element.parentNode === container &&
               card.element !== container.lastElementChild) {
      // 이미 존재하는 카드 → 맨 아래로 부드럽게 이동 (FLIP 애니메이션)
      moveCardToBottom(card.element, container);
    }
    // 카드 카테고리: 매수/매도 확정 시 해당 탭에만 노출
    card.agentCategories = card.agentCategories || new Set();
    if (agentCategory === 'buy' || agentCategory === 'sell') {
      // 매수/매도 확정 → 기존 카테고리(analysis 등) 제거, 해당 탭에만
      card.agentCategories.clear();
      card.agentCategories.add(agentCategory);
    } else if (!card.agentCategories.has('buy') && !card.agentCategories.has('sell')) {
      // 아직 매수/매도 미확정 → 카테고리 누적
      card.agentCategories.add(agentCategory);
    }
    card.element.dataset.agentCategory = Array.from(card.agentCategories).join(',');
    addStepToCard(card, data);
    updateCardHeader(card);
  }

  // Apply current filter to new element
  if (currentAgentFilter !== 'all') {
    applyAgentFilter();
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
 * 카드를 맨 아래로 부드럽게 이동 (FLIP 애니메이션)
 * - First: 현재 위치 기록
 * - Last: DOM 이동 후 최종 위치 계산
 * - Invert: 이전 위치로 transform 순간 이동
 * - Play: 다음 프레임에서 transition 켜고 원래 위치로 슬라이드
 */
function moveCardToBottom(element, container) {
  const firstRect = element.getBoundingClientRect();
  container.appendChild(element);
  const lastRect = element.getBoundingClientRect();
  const deltaY = firstRect.top - lastRect.top;
  if (deltaY === 0) return;

  element.style.transition = 'none';
  element.style.transform = `translateY(${deltaY}px)`;

  requestAnimationFrame(() => {
    element.style.transition = 'transform 400ms cubic-bezier(0.4, 0, 0.2, 1)';
    element.style.transform = '';
    element.addEventListener('transitionend', () => {
      element.style.transition = '';
      element.style.transform = '';
    }, { once: true });
  });
}

/**
 * 종목 카드 생성
 */
function createStockCard(symbol, firstActivity) {
  const el = document.createElement('div');
  el.className = 'stock-card outcome-progress';

  // 종목명: symbolNames 매핑 → summary 추출 → fallback symbol
  const summary = firstActivity.summary || '';
  let stockName = symbolNames[symbol] || null;
  if (!stockName) {
    const bracketMatch = summary.match(/\[(?!TIER[12]\b)([^\]]+)\]/);
    if (bracketMatch) {
      stockName = bracketMatch[1];
    } else {
      const purposeMatch = summary.match(/\[TIER[12]\]\s+(.+?)\s+분석/);
      stockName = purposeMatch ? purposeMatch[1] : symbol;
    }
  }

  // Header
  const header = document.createElement('div');
  header.className = 'stock-card-header';
  header.innerHTML = `
    <span class="text-sm">📊</span>
    <span class="text-sm font-medium text-white flex-1 truncate">
      ${escapeHtml(stockName)} <span class="text-gray-500 text-xs">${escapeHtml(symbol)}</span>
    </span>
    <span class="stock-confidence"></span>
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

  // Activity type color dot
  const dotColor = getTypeDotColor(data.activity_type);

  let html = `
    <span class="text-xs text-gray-600 shrink-0 w-14">${time}</span>
    <span class="shrink-0 w-2 h-2 rounded-full bg-${dotColor}-400 mt-1.5"></span>
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
        outcomeBg = 'bg-red-900/50 text-red-200 font-bold border border-red-700/50';
      } else if (summ.includes('SELL')) {
        outcome = 'sell';
        outcomeText = '📉 매도';
        outcomeBg = 'bg-blue-900/50 text-blue-200 font-bold border border-blue-700/50';
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

    // LLM_CALL 분석 완료 → "분석 완료" 표시 (BUY/SELL 결과는 매수/매도 탭에서 확인)
    if (a.activity_type === 'LLM_CALL' && a.phase === 'COMPLETE') {
      if (outcome === 'progress') {
        outcome = 'hold';
        outcomeText = '✅ 분석 완료';
        outcomeBg = 'bg-gray-700/60 text-gray-300';
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
    if (a.activity_type === 'DECISION' || a.activity_type === 'ORDER' || a.activity_type === 'TRADE_RESULT') {
      const summ = a.summary || '';
      const isSell = outcome === 'sell' || summ.includes('SELL') || summ.includes('매도');
      if (a.phase === 'COMPLETE' && (summ.includes('주문 접수') || summ.includes('체결'))) {
        outcome = isSell ? 'sell' : 'buy';
        outcomeText = isSell ? '📉 매도 완료' : '📈 매수 완료';
        outcomeBg = isSell ? 'bg-blue-900/50 text-blue-200 font-bold border border-blue-700/50' : 'bg-red-900/50 text-red-200 font-bold border border-red-700/50';
      } else if (summ.includes('주문 실행')) {
        // 주문 접수 전 — 방향만 표시
        if (outcome !== 'buy' && outcome !== 'sell') {
          outcome = isSell ? 'sell' : 'buy';
          outcomeText = isSell ? '📉 매도' : '📈 매수';
          outcomeBg = isSell ? 'bg-blue-900/50 text-blue-200 font-bold border border-blue-700/50' : 'bg-red-900/50 text-red-200 font-bold border border-red-700/50';
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
  // 매도 확정 시각 기록 → frozenKey 생성에 사용 (같은 심볼 새 분석 시 별도 카드)
  if (outcome === 'sell' && !card.soldAt) {
    card.soldAt = Date.now();
  }

  // Update outcome badge
  const outcomeEl = card.headerEl.querySelector('.stock-outcome');
  if (outcomeEl) {
    outcomeEl.className = `stock-outcome text-xs px-2 py-0.5 rounded ${outcomeBg}`;
    outcomeEl.innerHTML = outcomeText;
  }

  // Update confidence mini-bar
  const confEl = card.headerEl.querySelector('.stock-confidence');
  if (confEl && card.confidence != null) {
    const pct = Math.round(card.confidence * 100);
    let barColor = '#ef4444'; // red
    if (pct >= 70) barColor = '#22c55e'; // green
    else if (pct >= 50) barColor = '#eab308'; // yellow
    confEl.innerHTML = `
      <span class="text-xs text-gray-500">${pct}%</span>
      <span class="stock-confidence-bar">
        <span class="stock-confidence-fill" style="width:${pct}%;background:${barColor}"></span>
      </span>`;
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
    const resp = await fetch(`${API}/activities?limit=2000`);
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

// ── Q&A ──
async function askQuestion() {
  const input = document.getElementById('qa-input');
  const btn = document.getElementById('qa-btn');
  const respEl = document.getElementById('qa-response');
  const question = input.value.trim();
  if (!question) return;

  btn.disabled = true;
  btn.textContent = '...';
  respEl.classList.remove('hidden');
  respEl.innerHTML = '<span class="text-gray-500">답변 생성 중...</span>';

  try {
    const res = await fetch(`${API}/qa/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    const json = await res.json();
    if (json.data) {
      const d = json.data;
      respEl.innerHTML =
        `<div class="text-xs text-gray-500 mb-1">${d.context_summary} | ${d.llm_provider} | ${(d.execution_time_ms/1000).toFixed(1)}s</div>` +
        `<div class="whitespace-pre-wrap">${escapeHtml(d.answer)}</div>`;
    } else {
      respEl.innerHTML = `<span class="text-red-400">${json.message || '답변 생성 실패'}</span>`;
    }
  } catch (e) {
    respEl.innerHTML = `<span class="text-red-400">요청 실패: ${e.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '질문';
    input.value = '';
  }
}

// ── Agent Filter ──
const AGENT_MAP = {
  'CYCLE': 'system', 'SCHEDULE': 'system', 'DAILY_PLAN': 'system',
  'SCAN': 'scan', 'SCREENING': 'scan',
  'TIER1_ANALYSIS': 'analysis', 'TIER2_REVIEW': 'analysis',
  'STRATEGY_EVAL': 'analysis', 'RISK_CHECK': 'analysis', 'RISK_GATE': 'analysis',
  'TRADING_RULE': 'analysis',
  // LLM_CALL은 getAgentCategory에서 summary 기반 분류 (고정 매핑 제거)
};

const SELL_KEYWORDS = ['매도', 'sell', '손절', '익절', '스탑', 'stop', '청산', '트레일링'];
const BUY_KEYWORDS = ['매수', 'buy'];

function _isSellSummary(summary) {
  return SELL_KEYWORDS.some(k => summary.includes(k));
}

function _isBuySummary(summary) {
  return BUY_KEYWORDS.some(k => summary.includes(k));
}

function getAgentCategory(data) {
  const type = data.activity_type || '';
  const summary = (data.summary || '').toLowerCase();

  // LLM_CALL: summary 키워드로 세분화
  // 재평가/보유 LLM_CALL은 분석 활동이므로 analysis로 분류 (실제 매도 주문만 sell 탭)
  if (type === 'LLM_CALL') {
    if (summary.includes('리스크') || summary.includes('한도')) return 'system';
    if (summary.includes('스캔') || summary.includes('스크리너') || summary.includes('선별')) return 'scan';
    return 'analysis';
  }

  // 고정 매핑
  if (AGENT_MAP[type]) return AGENT_MAP[type];

  // ORDER / DECISION / TRADE_RESULT: summary 키워드로 매수/매도 판별
  if (type === 'ORDER' || type === 'DECISION' || type === 'TRADE_RESULT') {
    if (_isSellSummary(summary)) return 'sell';
    if (_isBuySummary(summary)) return 'buy';
    return 'buy'; // fallback
  }

  // EVENT: 손절/익절이면 guard, 아니면 regime
  if (type === 'EVENT') {
    if (_isSellSummary(summary)) return 'guard';
    return 'regime';
  }

  return 'system';
}

function setAgentFilter(filter) {
  currentAgentFilter = filter;
  // Update tab styles
  document.querySelectorAll('.agent-tab').forEach(btn => {
    if (btn.dataset.agent === filter) {
      btn.className = 'agent-tab px-2.5 py-1 rounded text-xs font-medium bg-blue-900/30 text-blue-300 whitespace-nowrap';
    } else {
      btn.className = 'agent-tab px-2.5 py-1 rounded text-xs text-gray-400 hover:bg-dark-700 whitespace-nowrap';
    }
  });
  // Apply filter to existing elements
  applyAgentFilter();
}

function applyAgentFilter() {
  const container = document.getElementById('chat-container');
  for (const child of container.children) {
    const agentCat = child.dataset.agentCategory || '';
    if (!agentCat || currentAgentFilter === 'all') {
      child.style.display = '';
    } else {
      // 카드는 여러 카테고리를 가질 수 있음 (쉼표 구분)
      const cats = agentCat.split(',');
      child.style.display = cats.includes(currentAgentFilter) ? '' : 'none';
    }
  }
  // 필터 적용 후 최신 항목(맨 아래)로 스크롤 (display 변경 후 scrollHeight 재계산 대기)
  requestAnimationFrame(() => {
    container.scrollTop = container.scrollHeight;
  });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
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
    if (report.report_date) {
      await loadTradeHistory(report.report_date, container);
      await loadDateActivities(report.report_date, container);
    }
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

// ── Trade History ──
async function loadTradeHistory(dateStr, container) {
  try {
    const resp = await fetch(`${API}/trades?target_date=${dateStr}`);
    const json = await resp.json();
    const data = json.data;
    if (!data) return;

    const opened = data.opened || [];
    const completed = data.completed || [];
    const openPositions = data.open_positions || [];
    if (!opened.length && !completed.length && !openPositions.length) return;

    const section = document.createElement('div');
    section.className = 'bg-dark-700 rounded-xl p-5 border border-gray-600 mx-2 mt-3 chat-bubble';

    let html = '<div class="text-sm font-bold text-white mb-3">💰 매매 내역</div>';

    // 청산 완료 (실현 손익)
    if (completed.length) {
      html += '<div class="text-xs font-medium text-gray-400 mb-2">청산 완료</div>';
      html += completed.map(t => renderTradeCard(t, 'completed')).join('');
    }

    // 오늘 매수
    if (opened.length) {
      html += `<div class="text-xs font-medium text-gray-400 mb-2 ${completed.length ? 'mt-3' : ''}">오늘 매수</div>`;
      html += opened.map(t => renderTradeCard(t, 'opened')).join('');
    }

    // 미청산 보유
    if (openPositions.length) {
      html += `<div class="text-xs font-medium text-gray-400 mb-2 mt-3">보유 중 (미청산)</div>`;
      // 종목별 그룹핑
      const grouped = {};
      openPositions.forEach(t => {
        if (!grouped[t.stock_symbol]) grouped[t.stock_symbol] = { name: t.stock_name, symbol: t.stock_symbol, trades: [] };
        grouped[t.stock_symbol].trades.push(t);
      });
      html += Object.values(grouped).map(g => {
        const totalQty = g.trades.reduce((s, t) => s + t.quantity, 0);
        const avgPrice = g.trades.reduce((s, t) => s + t.entry_price * t.quantity, 0) / totalQty;
        const entries = g.trades.map(t => {
          const time = t.entry_at ? new Date(t.entry_at).toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit'}) : '';
          return `${time} ${t.quantity}주 @${t.entry_price.toLocaleString()}원`;
        }).join(' → ');
        const conf = g.trades[0].ai_confidence;
        return `<div class="bg-dark-900 rounded-lg p-3 mb-2 border-l-2 border-blue-500">
          <div class="flex justify-between items-center">
            <span class="text-sm text-white font-medium">${g.name}<span class="text-gray-500 text-xs ml-1">${g.symbol}</span></span>
            <span class="text-xs text-blue-400">${totalQty}주 · 평단 ${Math.round(avgPrice).toLocaleString()}원</span>
          </div>
          <div class="text-xs text-gray-500 mt-1">${entries}</div>
          ${conf ? `<div class="text-xs text-gray-600 mt-1">신뢰도 ${(conf*100).toFixed(0)}%</div>` : ''}
        </div>`;
      }).join('');
    }

    section.innerHTML = html;
    container.appendChild(section);
  } catch (err) {
    console.error('Trade history load error:', err);
  }
}

function renderTradeCard(t, type) {
  const time = (type === 'completed' && t.exit_at)
    ? new Date(t.exit_at).toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit'})
    : (t.entry_at ? new Date(t.entry_at).toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit'}) : '');

  if (type === 'completed') {
    const pnlColor = t.pnl >= 0 ? 'text-green-400' : 'text-red-400';
    const borderColor = t.pnl >= 0 ? 'border-green-500' : 'border-red-500';
    const pnlSign = t.pnl >= 0 ? '+' : '';
    const returnSign = t.return_pct >= 0 ? '+' : '';
    return `<div class="bg-dark-900 rounded-lg p-3 mb-2 border-l-2 ${borderColor}">
      <div class="flex justify-between items-center">
        <span class="text-sm text-white font-medium">${t.stock_name}<span class="text-gray-500 text-xs ml-1">${t.stock_symbol}</span></span>
        <span class="text-xs ${pnlColor} font-medium">${pnlSign}${t.pnl.toLocaleString()}원 (${returnSign}${t.return_pct}%)</span>
      </div>
      <div class="flex justify-between text-xs text-gray-500 mt-1">
        <span>${t.quantity}주 · ${t.entry_price.toLocaleString()} → ${t.exit_price.toLocaleString()}원</span>
        <span>${time} · ${t.exit_reason || 'SIGNAL'}${t.hold_days > 0 ? ` · ${t.hold_days}일 보유` : ''}</span>
      </div>
      ${t.ai_confidence ? `<div class="text-xs text-gray-600 mt-1">신뢰도 ${(t.ai_confidence*100).toFixed(0)}% · ${t.strategy_type || ''}</div>` : ''}
    </div>`;
  }

  // opened (매수)
  const conf = t.ai_confidence ? `신뢰도 ${(t.ai_confidence*100).toFixed(0)}%` : '';
  return `<div class="bg-dark-900 rounded-lg p-3 mb-2 border-l-2 border-red-500">
    <div class="flex justify-between items-center">
      <span class="text-sm text-white font-medium">${t.stock_name}<span class="text-gray-500 text-xs ml-1">${t.stock_symbol}</span></span>
      <span class="text-xs text-red-400">매수 ${t.quantity}주 @${t.entry_price.toLocaleString()}원</span>
    </div>
    <div class="flex justify-between text-xs text-gray-500 mt-1">
      <span>${time} · ${t.strategy_type || ''}</span>
      <span>${conf}</span>
    </div>
  </div>`;
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

// ── System Status ──
async function loadSystemStatus() {
  try {
    const resp = await fetch(`${API}/system/status`);
    const json = await resp.json();
    const s = json.data;
    if (!s) return;
    updateBadge('badge-trading', s.trading_enabled ? '매매:ON' : '매매:OFF', s.trading_enabled ? 'green' : 'red');
    if (s.autonomy_mode) updateBadge('badge-mode', s.autonomy_mode, 'purple');
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

function getTypeDotColor(type) {
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

  // System prompt: collapsed by default, show first 2 lines
  if (sys) {
    const sysId = 'sys-' + Math.random().toString(36).substr(2, 6);
    const lines = sys.split('\n');
    const preview = lines.slice(0, 2).join('\n');
    const hasMore = lines.length > 2;
    html += `<div class="llm-msg llm-system">
      <div class="llm-role">SYSTEM</div>
      <div class="llm-body" style="max-height:none">
        <span>${escapeHtml(preview)}${hasMore ? '...' : ''}</span>
        ${hasMore ? `
          <div id="${sysId}" style="display:none"><br>${escapeHtml(lines.slice(2).join('\n'))}</div>
          <button onclick="event.stopPropagation();var el=document.getElementById('${sysId}');var show=el.style.display==='none';el.style.display=show?'':'none';this.textContent=show?'접기':'시스템 프롬프트 전체 보기'" class="text-purple-400 hover:text-purple-300 text-xs mt-1 block">시스템 프롬프트 전체 보기</button>
        ` : ''}
      </div>
    </div>`;
  }

  if (prompt) html += `<div class="llm-msg llm-user"><div class="llm-role">PROMPT</div><div class="llm-body">${escapeHtml(prompt)}</div></div>`;

  // Response: try JSON formatting
  if (response) {
    let formattedResponse = escapeHtml(response);
    try {
      const parsed = JSON.parse(response);
      formattedResponse = '<pre class="whitespace-pre-wrap break-all">' + escapeHtml(JSON.stringify(parsed, null, 2)) + '</pre>';
    } catch {
      // not JSON, use plain text
    }
    html += `<div class="llm-msg llm-assistant"><div class="llm-role">RESPONSE</div><div class="llm-body">${formattedResponse}</div></div>`;
  }

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
