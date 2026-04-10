/**
 * Smart Agent UI - SPA 라우터 & 전역 상태
 */

// ── 전역 상태 ──────────────────────────────────────────────────────────
const State = {
  currentPage: null,
  jobs: [],          // 캐시된 job 목록
  pollingMap: {},    // { jobId: intervalId }
};

// ── 토스트 ─────────────────────────────────────────────────────────────
function toast(msg, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── 라우터 ─────────────────────────────────────────────────────────────
const PAGES = {
  dashboard:  { label: '대시보드',    icon: '▦',  render: renderDashboard },
  upload:     { label: '문서 업로드',  icon: '↑',  render: renderUpload },
  documents:  { label: '문서 관리',    icon: '☰',  render: renderDocuments },
  viewer:     { label: '문서 탐색',    icon: '⊞',  render: renderViewer },
  query:      { label: '검색 질의',    icon: '⊙',  render: renderQuery },
};

function navigate(page, params = {}) {
  State.currentPage = page;
  State.currentParams = params;

  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });

  const topbarTitle = document.getElementById('topbar-title');
  topbarTitle.textContent = PAGES[page]?.label || page;

  const content = document.getElementById('content');
  content.innerHTML = '';
  PAGES[page]?.render(content, params);
}

// ── 사이드바 빌드 ───────────────────────────────────────────────────────
function buildSidebar() {
  const nav = document.getElementById('sidebar-nav');
  nav.innerHTML = `
    <div class="nav-group-label">메인</div>
    ${Object.entries(PAGES).map(([key, { label, icon }]) => `
      <div class="nav-item" data-page="${key}" onclick="navigate('${key}')">
        <span class="icon">${icon}</span>
        <span>${label}</span>
      </div>
    `).join('')}
    <div class="nav-group-label" style="margin-top:16px">외부 링크</div>
    <div class="nav-item" onclick="window.open('/docs','_blank')">
      <span class="icon">⌘</span>
      <span>API 문서</span>
    </div>
  `;
}

// ── 유틸 ───────────────────────────────────────────────────────────────
function badge(status) {
  const map = {
    pending:   '대기',
    running:   '실행중',
    completed: '완료',
    failed:    '실패',
    started:   '시작됨',
    cancelled: '중단됨',
  };
  return `<span class="badge badge-${status}">${map[status] || status}</span>`;
}

function stepDot(status) {
  const icon = { pending:'○', running:'◉', completed:'✓', failed:'✕' };
  return `<div class="step-dot ${status}">${icon[status] || '○'}</div>`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('ko-KR', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' });
}

function filename(path) {
  return path ? path.split('/').pop() : '—';
}

// step 이름 한글 맵
const STEP_NAMES = {
  format_converter:    '포맷 변환',
  index_parser:        '목차 추출',
  structurer:          '구조화',
  section_parser:      '섹션 파싱',
  document_integrator: '문서 통합',
};

// ── Jobs 원격 로드 ──────────────────────────────────────────────────────
async function fetchAndSyncJobs() {
  try {
    const jobs = await listJobs();
    // 서버 데이터를 State에 병합 (session 중 추가된 항목도 유지)
    const serverMap = Object.fromEntries(jobs.map(j => [j.job_id, j]));
    // State.jobs에서 서버에 없는 것(방금 업로드한 running job)은 그대로 두고
    // 서버에 있는 것은 최신 상태로 갱신
    const existingIds = new Set(State.jobs.map(j => j.job_id));
    // 서버 데이터로 기존 항목 업데이트
    State.jobs = State.jobs.map(j => serverMap[j.job_id] ? { ...j, ...serverMap[j.job_id] } : j);
    // 서버에만 있는 항목 추가
    for (const j of jobs) {
      if (!existingIds.has(j.job_id)) State.jobs.push(j);
    }
  } catch {}
}

// ── 폴링 헬퍼 ──────────────────────────────────────────────────────────
function startPolling(jobId, onUpdate, interval = 3000) {
  stopPolling(jobId);
  const id = setInterval(async () => {
    try {
      const data = await getJobStatus(jobId);
      onUpdate(data);
      if (['completed', 'failed', 'cancelled'].includes(data.status)) stopPolling(jobId);
    } catch {}
  }, interval);
  State.pollingMap[jobId] = id;
}

function stopPolling(jobId) {
  if (State.pollingMap[jobId]) {
    clearInterval(State.pollingMap[jobId]);
    delete State.pollingMap[jobId];
  }
}

// ══════════════════════════════════════════════════════════════════════════
// 페이지: 대시보드
// ══════════════════════════════════════════════════════════════════════════
async function renderDashboard(el) {
  el.innerHTML = `
    <div class="grid-4 mb-16" id="stats-row">
      <div class="stat-card"><div class="stat-label">전체 문서</div><div class="stat-value" id="stat-total"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div></div></div>
      <div class="stat-card"><div class="stat-label">완료</div><div class="stat-value" id="stat-completed"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div></div></div>
      <div class="stat-card"><div class="stat-label">실행중</div><div class="stat-value" id="stat-running"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div></div></div>
      <div class="stat-card"><div class="stat-label">실패</div><div class="stat-value" id="stat-failed"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div></div></div>
    </div>
    <div class="grid-2">
      <div class="card">
        <div class="card-title">⏱ 최근 작업</div>
        <div id="recent-jobs"><div class="spinner"></div></div>
      </div>
      <div class="card">
        <div class="card-title">⊙ 빠른 질의</div>
        <div class="form-row">
          <textarea class="input" id="quick-query" rows="3" placeholder="질의를 입력하세요..."></textarea>
        </div>
        <button class="btn btn-primary" onclick="quickQuery()">질의 실행</button>
        <div id="quick-answer" class="mt-16" style="display:none"></div>
      </div>
    </div>
  `;

  await fetchAndSyncJobs();
  loadDashboardStats(el);
}

async function loadDashboardStats(el) {
  const jobs = State.jobs;
  const total = jobs.length;
  const completed = jobs.filter(j => j.status === 'completed').length;
  const running   = jobs.filter(j => j.status === 'running').length;
  const failed    = jobs.filter(j => j.status === 'failed').length;

  document.getElementById('stat-total').textContent     = total;
  document.getElementById('stat-completed').textContent = completed;
  document.getElementById('stat-running').textContent   = running;
  document.getElementById('stat-failed').textContent    = failed;

  const recentEl = el.querySelector('#recent-jobs');
  if (!jobs.length) {
    recentEl.innerHTML = `<div class="empty-state" style="padding:30px 0">
      <div class="empty-icon">📄</div>
      <div class="empty-title">아직 업로드된 문서가 없습니다</div>
      <div class="empty-sub"><span class="btn btn-sm btn-primary" onclick="navigate('upload')" style="margin-top:10px;display:inline-flex">문서 업로드하기</span></div>
    </div>`;
    return;
  }

  const recent = [...jobs].reverse().slice(0, 6);
  recentEl.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>파일명</th><th>상태</th><th>업데이트</th><th></th></tr></thead>
    <tbody>
      ${recent.map(j => `
        <tr>
          <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${j.source_path}">${filename(j.source_path)}</td>
          <td>${badge(j.status)}</td>
          <td class="text-dim text-sm">${fmtTime(j.updated_at)}</td>
          <td><button class="btn btn-sm btn-secondary" onclick="navigate('documents')">보기</button></td>
        </tr>`
      ).join('')}
    </tbody>
  </table></div>`;
}

// ── 에이전트 진행 상황 표시 공통 헬퍼 ────────────────────────────────
const AGENT_ICONS = {
  planner:    '🗺',
  retriever:  '🔍',
  aggregator: '🔬',
  writer:     '✏️',
  supervisor: '✅',
};

function renderThinking(label, message) {
  const icon = AGENT_ICONS[label] || '⚙️';
  return `
    <div class="agent-thinking">
      <div class="thinking-header">
        <span class="thinking-pulse"></span>
        <span class="thinking-label">${escHtml(label)}</span>
      </div>
      <div class="thinking-message">${icon} ${escHtml(message)}</div>
    </div>`;
}

/**
 * 스트리밍 질의 공통 실행기
 * @param {string} query
 * @param {HTMLElement} statusEl  — 진행 상태를 교체할 요소
 * @param {function} onDone       — fn(result, elapsedSec)
 * @param {function} onError      — fn(msg)
 * @returns {AbortController}
 */
function runStreamQuery(query, statusEl, onDone, onError) {
  const startTime = Date.now();

  return queryRetrieverStream(query, ev => {
    if (ev.type === 'progress') {
      statusEl.innerHTML = renderThinking(ev.label, ev.message);
    } else if (ev.type === 'done') {
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      onDone(ev.result, elapsed);
    } else if (ev.type === 'error') {
      onError(ev.detail || '알 수 없는 오류');
    }
  });
}

function quickQuery() {
  const q = document.getElementById('quick-query').value.trim();
  if (!q) return toast('질의를 입력하세요', 'error');
  const ansEl = document.getElementById('quick-answer');
  ansEl.style.display = 'block';
  ansEl.innerHTML = renderThinking('계획 수립', `"${q.slice(0, 60)}"`);;

  runStreamQuery(
    q,
    ansEl,
    (result) => {
      if (result.status === 'success') {
        ansEl.innerHTML = `<div class="answer-box">
          <div class="answer-status text-success">✓ 답변 완료</div>
          <div class="answer-text">${escHtml(result.answer)}</div>
        </div>`;
      } else {
        ansEl.innerHTML = `<div class="answer-box">
          <div class="answer-status text-danger">✕ ${escHtml(result.message || '검색 결과가 충분하지 않아 답변을 생성할 수 없습니다.')}</div>
          ${result.partial_result ? `<hr class="divider"><div class="card-title" style="font-size:13px;color:var(--warning)">⚠ 부분 결과</div><div class="answer-text">${escHtml(result.partial_result)}</div>` : ''}
        </div>`;
      }
    },
    (msg) => {
      ansEl.innerHTML = `<div class="answer-box text-danger">오류: ${escHtml(msg)}</div>`;
    },
  );
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toggleSummary(id, btn) {
  const el = document.getElementById(id);
  if (!el) return;
  const collapsed = el.style.webkitLineClamp !== 'unset';
  if (collapsed) {
    el.style.webkitLineClamp = 'unset';
    el.style.display = 'block';
    btn.textContent = '접기';
  } else {
    el.style.display = '-webkit-box';
    el.style.webkitLineClamp = '3';
    btn.textContent = '더보기';
  }
}

// ══════════════════════════════════════════════════════════════════════════
// 페이지: 문서 업로드
// ══════════════════════════════════════════════════════════════════════════
function renderUpload(el) {
  el.innerHTML = `
    <div style="max-width:680px;margin:0 auto">
      <div class="card">
        <div class="card-title">↑ 문서 업로드</div>
        <div class="upload-zone" id="upload-zone" onclick="document.getElementById('file-input').click()">
          <div class="upload-icon">📂</div>
          <div class="upload-label">파일을 드래그하거나 클릭하여 업로드</div>
          <div class="upload-sub">PDF · DOCX · HWPX · PPTX · XLSX · CSV · PNG · JPG 지원</div>
        </div>
        <input type="file" id="file-input" style="display:none"
               accept=".pdf,.docx,.hwpx,.pptx,.xlsx,.csv,.png,.jpg,.jpeg"
               onchange="handleFileSelect(this.files)">

        <div id="upload-queue" class="mt-20"></div>
      </div>

      <div class="card mt-20" id="active-jobs-card">
        <div class="card-title">⏳ 진행 중인 작업</div>
        <div id="active-jobs-list">
          ${State.jobs.filter(j=>j.status==='running').length === 0
            ? '<div class="text-dim text-sm">현재 실행 중인 작업이 없습니다</div>'
            : ''}
        </div>
      </div>
    </div>
  `;

  setupUploadZone();
  renderActiveJobs(el);
}

function setupUploadZone() {
  const zone = document.getElementById('upload-zone');
  if (!zone) return;
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    handleFileSelect(e.dataTransfer.files);
  });
}

async function handleFileSelect(files) {
  const queue = document.getElementById('upload-queue');
  for (const file of files) {
    const rowId = `upload-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const row = document.createElement('div');
    row.id = rowId;
    row.className = 'card mt-8';
    row.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px">
        <span style="font-size:22px">📄</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(file.name)}</div>
          <div class="text-dim text-sm">${(file.size/1024/1024).toFixed(2)} MB</div>
        </div>
        <div class="spinner"></div>
      </div>
      <div id="${rowId}-status" class="text-dim text-sm mt-8">업로드 중...</div>
    `;
    queue.prepend(row);

    try {
      const res = await uploadFile(file);
      const statusEl = document.getElementById(`${rowId}-status`);
      statusEl.innerHTML = `${badge('started')} job_id: <code style="font-size:11px;color:var(--text-dim)">${res.job_id}</code>`;
      row.querySelector('.spinner').outerHTML = `<span style="color:var(--success);font-size:18px">✓</span>`;

      // State에 추가 (임시)
      State.jobs.push({ job_id: res.job_id, source_path: file.name, status: 'running', updated_at: new Date().toISOString() });

      toast(`업로드 완료: ${file.name}`, 'success');

      // 폴링 시작 → 상태 업데이트
      startPolling(res.job_id, (data) => {
        const s = document.getElementById(`${rowId}-status`);
        if (s) s.innerHTML = `${badge(data.status)} ${buildStepProgress(data.steps)}`;
        // State 업데이트
        const ji = State.jobs.findIndex(j => j.job_id === data.job_id);
        if (ji >= 0) State.jobs[ji] = { ...State.jobs[ji], ...data };
        if (data.status === 'completed') toast(`파싱 완료: ${file.name}`, 'success');
        if (data.status === 'failed')    toast(`파싱 실패: ${file.name}`, 'error');
      });

    } catch(e) {
      document.getElementById(`${rowId}-status`).innerHTML = `<span class="text-danger">✕ ${escHtml(e.message)}</span>`;
      toast(`업로드 실패: ${e.message}`, 'error');
    }
  }
}

function buildStepProgress(steps) {
  if (!steps || !steps.length) return '';
  const icons = { pending:'○', running:'◉', completed:'✓', failed:'✕' };
  return steps.map(s => `<span title="${STEP_NAMES[s.step_name]||s.step_name}: ${s.status}"
    style="color:${s.status==='completed'?'var(--success)':s.status==='failed'?'var(--danger)':s.status==='running'?'var(--info)':'var(--text-dim)'}"
  >${icons[s.status]||'○'}</span>`).join(' ');
}

function renderActiveJobs(el) {
  const listEl = el.querySelector('#active-jobs-list');
  if (!listEl) return;
  const running = State.jobs.filter(j => j.status === 'running');
  if (!running.length) return;
  listEl.innerHTML = running.map(j => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
      <div class="spinner"></div>
      <span style="flex:1;font-size:14px">${filename(j.source_path)}</span>
      ${badge(j.status)}
    </div>
  `).join('');
}

// ══════════════════════════════════════════════════════════════════════════
// 페이지: 문서 관리
// ══════════════════════════════════════════════════════════════════════════
async function renderDocuments(el) {
  el.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
      <div style="display:flex;gap:10px;align-items:center">
        <input class="input" id="doc-search" style="width:240px" placeholder="파일명 검색..." oninput="filterDocTable()">
        <select class="input select" id="doc-filter" style="width:140px" onchange="filterDocTable()">
          <option value="">전체 상태</option>
          <option value="pending">대기</option>
          <option value="running">실행중</option>
          <option value="completed">완료</option>
          <option value="failed">실패</option>
        </select>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-secondary" id="docs-refresh-btn" onclick="refreshDocuments()">↻ 새로고침</button>
        <button class="btn btn-primary" onclick="navigate('upload')">↑ 업로드</button>
      </div>
    </div>
    <div class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>파일명</th>
              <th>Job ID</th>
              <th>상태</th>
              <th>업데이트</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="doc-table-body">
            <tr><td colspan="5" style="text-align:center;padding:32px"><div class="spinner"></div></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `;

  await fetchAndSyncJobs();
  _renderDocTableBody();
}

function _renderDocTableBody() {
  const tbody = document.getElementById('doc-table-body');
  if (!tbody) return;
  tbody.innerHTML = State.jobs.length === 0
    ? `<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">📄</div>
        <div class="empty-title">문서가 없습니다</div>
        <div class="empty-sub">문서를 업로드하면 여기에 표시됩니다</div></div></td></tr>`
    : renderDocRows(State.jobs);
}

async function refreshDocuments() {
  const btn = document.getElementById('docs-refresh-btn');
  if (btn) btn.disabled = true;
  const tbody = document.getElementById('doc-table-body');
  if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:24px"><div class="spinner"></div></td></tr>`;
  await fetchAndSyncJobs();
  _renderDocTableBody();
  filterDocTable();
  if (btn) btn.disabled = false;
  toast('목록을 갱신했습니다', 'info');
}

function renderDocRows(jobs) {
  return jobs.map(j => `
    <tr data-status="${j.status}" data-name="${filename(j.source_path).toLowerCase()}">
      <td style="max-width:220px">
        <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500" title="${j.source_path}">
          ${escHtml(filename(j.source_path))}
        </div>
        <div class="text-dim" style="font-size:11px;margin-top:2px">${j.source_path || ''}</div>
      </td>
      <td><code style="font-size:11px;color:var(--text-dim)">${j.job_id.slice(0,12)}…</code></td>
      <td>${badge(j.status)}</td>
      <td class="text-dim text-sm">${fmtTime(j.updated_at)}</td>
      <td>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn btn-sm btn-secondary" onclick="showJobDetail('${j.job_id}')">상세</button>
          ${j.status==='completed'
            ? `<button class="btn btn-sm btn-secondary" onclick="navigate('viewer',{jobId:'${j.job_id}',name:'${escHtml(filename(j.source_path))}'})">탐색</button>`
            : ''}
          ${j.status==='failed' || j.status==='cancelled'
            ? `<button class="btn btn-sm btn-secondary" onclick="showResetModal('${j.job_id}')">재실행</button>`
            : ''}
          ${j.status==='running'
            ? `<button class="btn btn-sm btn-warning" onclick="doCancelJob('${j.job_id}', this)">중단</button>`
            : ''}
          <button class="btn btn-sm btn-danger" onclick="showDeleteModal('${j.job_id}', '${escHtml(filename(j.source_path))}')">삭제</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function filterDocTable() {
  const search = document.getElementById('doc-search').value.toLowerCase();
  const filter = document.getElementById('doc-filter').value;
  document.querySelectorAll('#doc-table-body tr').forEach(row => {
    const matchName   = !search || (row.dataset.name||'').includes(search);
    const matchStatus = !filter || row.dataset.status === filter;
    row.style.display = (matchName && matchStatus) ? '' : 'none';
  });
}

// Job 상세 모달
async function showJobDetail(jobId) {
  let data;
  try { data = await getJobStatus(jobId); }
  catch(e) { toast(e.message, 'error'); return; }

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" style="width:560px">
      <div class="modal-title">📋 Job 상세</div>
      <div class="form-row">
        <div class="form-label">Job ID</div>
        <code style="font-size:12px;color:var(--text-dim)">${data.job_id}</code>
      </div>
      <div class="form-row">
        <div class="form-label">파일</div>
        <div class="text-sm">${escHtml(data.source_path)}</div>
      </div>
      <div class="form-row">
        <div class="form-label">상태</div>
        ${badge(data.status)}
      </div>
      <div class="form-label" style="margin-bottom:12px">Step 진행상황</div>
      <div class="steps-timeline">
        ${data.steps.map(s => `
          <div class="step-row">
            ${stepDot(s.status)}
            <div class="step-info">
              <div class="step-name">${STEP_NAMES[s.step_name] || s.step_name}
                <span style="font-weight:400;color:var(--text-dim);font-size:12px">(${s.step_name})</span>
              </div>
              <div class="step-time">
                ${s.started_at ? `시작: ${fmtTime(s.started_at)}` : ''}
                ${s.completed_at ? ` · 완료: ${fmtTime(s.completed_at)}` : ''}
              </div>
              ${s.error ? `<div class="step-error">${escHtml(s.error.slice(0,400))}</div>` : ''}
            </div>
            ${badge(s.status)}
          </div>
        `).join('')}
        ${!data.steps.length ? '<div class="text-dim text-sm">아직 실행된 step이 없습니다</div>' : ''}
      </div>
      <div class="modal-actions">
        ${data.status === 'completed'
          ? `<button class="btn btn-primary" onclick="navigate('viewer',{jobId:'${data.job_id}',name:'${escHtml(filename(data.source_path))}'});this.closest('.modal-overlay').remove()">문서 탐색</button>`
          : ''}
        <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">닫기</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

// Step 재실행 모달
function showResetModal(jobId) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-title">🔄 Step 재실행</div>
      <div class="form-row">
        <label class="form-label">초기화할 Step</label>
        <select class="input select" id="reset-step-select">
          ${Object.entries(STEP_NAMES).map(([k,v]) => `<option value="${k}">${v} (${k})</option>`).join('')}
        </select>
      </div>
      <div class="text-dim text-sm">선택한 step을 초기화하면 다음 실행 시 해당 step부터 재실행됩니다.</div>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">취소</button>
        <button class="btn btn-primary" onclick="doResetStep('${jobId}', this)">초기화 후 재실행</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

async function doResetStep(jobId, btn) {
  const step = document.getElementById('reset-step-select').value;
  btn.disabled = true;
  btn.textContent = '처리 중...';
  try {
    await resetStep(jobId, step);
    await runJob(jobId);
    toast(`'${STEP_NAMES[step] || step}'부터 재실행을 시작했습니다`, 'success');
    btn.closest('.modal-overlay').remove();
    // 문서 관리 페이지면 테이블 갱신
    if (State.currentPage === 'documents') {
      await fetchAndSyncJobs();
      _renderDocTableBody();
    }
  } catch(e) {
    toast(e.message, 'error');
    btn.disabled = false;
    btn.textContent = '초기화 후 재실행';
  }
}

// 추출 중단
async function doCancelJob(jobId, btn) {
  btn.disabled = true;
  btn.textContent = '중단 중...';
  try {
    const res = await cancelJob(jobId);
    toast(res.message || '중단 요청을 전송했습니다', 'info');
    await fetchAndSyncJobs();
    _renderDocTableBody();
    filterDocTable();
  } catch(e) {
    toast(e.message, 'error');
    btn.disabled = false;
    btn.textContent = '중단';
  }
}

// 삭제 확인 모달
function showDeleteModal(jobId, fname) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" style="width:420px">
      <div class="modal-title" style="color:var(--danger)">⚠ 문서 삭제</div>
      <p style="margin:0 0 8px;font-size:14px">다음 문서를 삭제합니다:</p>
      <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:6px;padding:10px 14px;margin-bottom:16px;word-break:break-all;font-size:13px;font-weight:600">
        ${escHtml(fname)}
      </div>
      <p style="margin:0 0 20px;font-size:13px;color:var(--text-dim)">
        PostgreSQL · OpenSearch · MinIO의 모든 데이터가 영구 삭제됩니다.<br>이 작업은 되돌릴 수 없습니다.
      </p>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">취소</button>
        <button class="btn btn-danger" id="confirm-delete-btn" onclick="doDeleteJob('${jobId}', this)">삭제</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

async function doDeleteJob(jobId, btn) {
  btn.disabled = true;
  btn.textContent = '삭제 중...';
  try {
    await deleteJob(jobId);
    btn.closest('.modal-overlay').remove();
    // State에서 제거
    State.jobs = State.jobs.filter(j => j.job_id !== jobId);
    stopPolling(jobId);
    _renderDocTableBody();
    filterDocTable();
    toast('문서가 삭제되었습니다', 'success');
  } catch(e) {
    toast(`삭제 실패: ${e.message}`, 'error');
    btn.disabled = false;
    btn.textContent = '삭제';
  }
}

// ══════════════════════════════════════════════════════════════════════════
// 페이지: 문서 탐색
// ══════════════════════════════════════════════════════════════════════════
async function renderViewer(el, params = {}) {
  // 최신 job 목록 확보
  await fetchAndSyncJobs().catch(() => {});

  el.innerHTML = `
    <div style="display:flex;gap:20px;height:calc(100vh - var(--header-h) - 56px)">
      <!-- 좌: 문서 선택 + 메타데이터 + 섹션 트리 -->
      <div style="width:300px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;min-height:0">
        <div class="card" style="flex-shrink:0">
          <div class="form-label">문서 선택</div>
          <select class="input select" id="viewer-job-select" onchange="loadSections(this.value)">
            <option value="">— 선택하세요 —</option>
            ${State.jobs.filter(j=>j.status==='completed').map(j=>
              `<option value="${j.job_id}" ${params.jobId===j.job_id?'selected':''}>${escHtml(filename(j.source_path))}</option>`
            ).join('')}
          </select>
        </div>
        <div id="doc-meta-panel" style="display:none;flex-shrink:0"></div>
        <div class="card" style="flex:1;min-height:0;overflow-y:auto;padding:16px">
          <div id="section-tree">
            <div class="text-dim text-sm">문서를 선택하면 섹션 목록이 나타납니다</div>
          </div>
        </div>
      </div>

      <!-- 우: 섹션 콘텐츠 -->
      <div style="flex:1;overflow-y:auto">
        <div class="card" style="min-height:100%;box-sizing:border-box">
          <div id="section-content">
            <div class="empty-state">
              <div class="empty-icon">⊞</div>
              <div class="empty-title">섹션을 선택하세요</div>
              <div class="empty-sub">좌측에서 섹션을 클릭하면 내용이 표시됩니다</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  if (params.jobId) {
    document.getElementById('viewer-job-select').value = params.jobId;
    await loadSections(params.jobId);
  }
}

async function loadSections(jobId) {
  if (!jobId) {
    document.getElementById('doc-meta-panel').style.display = 'none';
    document.getElementById('section-tree').innerHTML =
      '<div class="text-dim text-sm">문서를 선택하면 섹션 목록이 나타납니다</div>';
    return;
  }
  const treeEl    = document.getElementById('section-tree');
  const metaPanel = document.getElementById('doc-meta-panel');
  treeEl.innerHTML    = '<div class="spinner"></div>';
  metaPanel.style.display = 'none';

  // 메타데이터 + 섹션 목록 병렬 로드
  const [metaResult, sectionsResult] = await Promise.allSettled([
    getDocMeta(jobId),
    listSections(jobId),
  ]);

  // 메타데이터 렌더링
  if (metaResult.status === 'fulfilled') {
    const m = metaResult.value;
    const kwHtml = (m.keywords || []).length
      ? m.keywords.map(k => `<span class="kw-tag">${escHtml(k)}</span>`).join('')
      : '<span class="text-dim text-sm">—</span>';
    const summaryId = `meta-summary-${jobId}`;
    const summaryHtml = m.summary
      ? `<div id="${summaryId}" style="line-height:1.5;color:var(--text-dim);overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical">${escHtml(m.summary)}</div>
         <button class="btn btn-sm" style="margin-top:4px;padding:2px 6px;font-size:11px" onclick="toggleSummary('${summaryId}',this)">더보기</button>`
      : '<span class="text-dim">—</span>';
    metaPanel.innerHTML = `
      <div class="card" style="padding:12px;font-size:12px;max-height:220px;overflow-y:auto">
        <div style="font-size:11px;font-weight:700;letter-spacing:.05em;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px">문서 정보</div>
        <div style="display:flex;flex-direction:column;gap:7px">
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            ${m.domain_category ? `<span class="badge badge-completed" style="font-size:11px">${escHtml(m.domain_category)}</span>` : ''}
            ${m.doc_type       ? `<span class="badge badge-pending"    style="font-size:11px">${escHtml(m.doc_type)}</span>`       : ''}
          </div>
          <div>${summaryHtml}</div>
          <div style="display:flex;flex-wrap:wrap;gap:3px">${kwHtml}</div>
        </div>
      </div>`;
    metaPanel.style.display = 'block';
  }

  // 섹션 트리 렌더링
  State._viewerJobId = jobId;
  if (sectionsResult.status === 'fulfilled') {
    const sections = sectionsResult.value;
    State._viewerSections = sections;
    treeEl.innerHTML = sections.map(s => `
      <div class="section-item" data-seq="${s.seq}" onclick="loadSectionDetail(${s.seq})">
        <span class="section-level">H${s.level||1}</span>
        <span class="section-title" style="padding-left:${(s.level||1)*8}px">${escHtml(s.title)}</span>
        <span class="section-badge">${s.block_count}</span>
      </div>
    `).join('') || '<div class="text-dim text-sm">섹션이 없습니다</div>';
  } else {
    treeEl.innerHTML = `<div class="text-danger text-sm">${escHtml(sectionsResult.reason?.message || '로드 실패')}</div>`;
  }
}

async function loadSectionDetail(seq) {
  // 활성 표시
  document.querySelectorAll('.section-item').forEach(el => el.classList.remove('active'));
  document.querySelector(`.section-item[data-seq="${seq}"]`)?.classList.add('active');

  const contentEl = document.getElementById('section-content');
  contentEl.innerHTML = '<div class="spinner"></div>';

  try {
    const data = await getSection(State._viewerJobId, seq);
    contentEl.innerHTML = `
      <div style="margin-bottom:16px">
        <div style="font-size:18px;font-weight:700;margin-bottom:4px">${escHtml(data.title)}</div>
        <div class="text-dim text-sm">${escHtml(data.section_path || '')}</div>
      </div>
      <hr class="divider">

      ${data.blocks.length
        ? `<div style="margin-bottom:20px">
            <div class="card-title">블록 (${data.blocks.length})</div>
            ${data.blocks.map(b => renderBlock(b)).join('')}
           </div>`
        : '<div class="text-dim text-sm mb-16">블록 없음</div>'
      }

      ${data.propositions.length
        ? `<div>
            <div class="card-title">명제 (${data.propositions.length})</div>
            ${data.propositions.map(p => `
              <div class="proposition-item">
                <div>${escHtml(p.proposition)}</div>
                ${p.keywords.length ? `<div class="prop-keywords">${p.keywords.map(k=>`<span class="kw-tag">${escHtml(k)}</span>`).join('')}</div>` : ''}
              </div>
            `).join('')}
           </div>`
        : ''
      }
    `;
  } catch(e) {
    contentEl.innerHTML = `<div class="text-danger">${escHtml(e.message)}</div>`;
  }
}

function renderBlock(b) {
  const typeClass = `block-type-${b.block_type}`;
  const typeLabel = { text:'텍스트', image:'이미지', table:'표' }[b.block_type] || b.block_type;
  const pageInfo  = b.page != null ? ` · p.${b.page}` : '';

  let bodyHtml;
  if (b.block_type === 'image' && b.minio_key) {
    bodyHtml = `
      <div class="block-content" style="padding:12px;display:flex;flex-direction:column;gap:10px">
        <img src="${imageUrl(b.minio_key)}"
             alt="${escHtml(b.content || '')}"
             style="max-width:100%;max-height:480px;object-fit:contain;border-radius:6px;background:var(--bg3)"
             onerror="this.style.display='none';this.nextElementSibling.style.display='block'">
        <div style="display:none;color:var(--danger);font-size:12px">이미지를 불러올 수 없습니다 (${escHtml(b.minio_key)})</div>
        ${b.content ? `<div style="font-size:13px;color:var(--text-dim);line-height:1.6">${escHtml(b.content)}</div>` : ''}
      </div>`;
  } else {
    bodyHtml = `<div class="block-content">${escHtml(b.content || '(내용 없음)')}</div>`;
  }

  return `
    <div class="block-card">
      <div class="block-header">
        <span class="${typeClass}">${typeLabel}</span>
        <span>블록 #${b.seq}${pageInfo}</span>
        ${b.minio_key ? `<span class="text-dim" style="margin-left:auto;font-size:11px">${escHtml(b.minio_key)}</span>` : ''}
      </div>
      ${bodyHtml}
    </div>
  `;
}

// ══════════════════════════════════════════════════════════════════════════
// 페이지: 검색 질의
// ══════════════════════════════════════════════════════════════════════════
function renderQuery(el) {
  el.innerHTML = `
    <div style="max-width:800px;margin:0 auto">
      <div class="card mb-16">
        <div class="card-title">⊙ 검색 질의 (RAG)</div>
        <div class="text-dim text-sm mb-16">
          업로드된 문서 기반으로 Multi-Agent가 검색·분석·작성합니다.
        </div>
        <div class="query-box">
          <textarea id="main-query" placeholder="질문을 입력하세요&#10;예: 재난 관리 체계에서 중앙정부의 역할은 무엇인가?"
            onkeydown="if(e.ctrlKey&&e.key==='Enter')submitQuery()"></textarea>
          <div style="display:flex;flex-direction:column;gap:8px">
            <button class="btn btn-primary" id="query-btn" onclick="submitQuery()" style="height:48px;white-space:nowrap">
              실행
            </button>
            <button class="btn btn-secondary btn-sm" onclick="clearQuery()">초기화</button>
          </div>
        </div>
        <div class="text-dim" style="font-size:12px">Ctrl+Enter로도 실행 가능</div>
      </div>

      <div id="query-result" style="display:none"></div>

      <!-- 히스토리 -->
      <div class="card mt-20" id="query-history-card">
        <div class="card-title">📜 최근 질의 이력</div>
        <div id="query-history">
          ${renderQueryHistory()}
        </div>
      </div>
    </div>
  `;

  // textarea keydown
  document.getElementById('main-query').addEventListener('keydown', e => {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); submitQuery(); }
  });
}

// 히스토리 (sessionStorage 기반)
function getHistory() {
  try { return JSON.parse(sessionStorage.getItem('queryHistory') || '[]'); } catch { return []; }
}
function saveHistory(item) {
  const h = getHistory();
  h.unshift(item);
  sessionStorage.setItem('queryHistory', JSON.stringify(h.slice(0, 20)));
}
function renderQueryHistory() {
  const h = getHistory();
  if (!h.length) return '<div class="text-dim text-sm">이력이 없습니다</div>';
  return h.slice(0,8).map((item,i) => `
    <div style="padding:10px 0;border-bottom:1px solid var(--border);cursor:pointer"
         onclick="replayQuery(${i})">
      <div style="font-size:14px;font-weight:500;margin-bottom:4px">${escHtml(item.query.slice(0,80))}${item.query.length>80?'…':''}</div>
      <div style="display:flex;gap:8px;align-items:center">
        ${badge(item.status)}
        <span class="text-dim text-sm">${fmtTime(item.time)}</span>
      </div>
    </div>
  `).join('');
}
function replayQuery(idx) {
  const item = getHistory()[idx];
  if (!item) return;
  document.getElementById('main-query').value = item.query;
}

let _queryCtrl = null;  // 현재 실행 중인 스트림 AbortController

function submitQuery() {
  const q = document.getElementById('main-query').value.trim();
  if (!q) return toast('질의를 입력하세요', 'error');

  // 이전 실행 취소
  if (_queryCtrl) { _queryCtrl.abort(); _queryCtrl = null; }

  const resultEl = document.getElementById('query-result');
  const btn      = document.getElementById('query-btn');
  btn.disabled   = true;
  resultEl.style.display = 'block';

  // 초기 진행 표시
  resultEl.innerHTML = `<div class="answer-box" id="stream-box">${renderThinking('계획 수립', `"${q.slice(0, 60)}"`)}</div>`;
  const streamBox = document.getElementById('stream-box');

  _queryCtrl = runStreamQuery(
    q,
    streamBox,
    (result, elapsed) => {
      _queryCtrl = null;
      btn.disabled = false;
      saveHistory({ query: q, status: result.status, time: new Date().toISOString() });

      if (result.status === 'success') {
        resultEl.innerHTML = `
          <div class="answer-box">
            <div class="answer-status">
              <span class="text-success">✓ 답변 완료</span>
              <span class="text-dim text-sm" style="margin-left:auto">${elapsed}초 소요</span>
            </div>
            <div class="answer-text">${escHtml(result.answer)}</div>
            ${result.sources && result.sources.length ? `
              <div class="sources-list">
                <div class="sources-title">📎 출처 (${result.sources.length})</div>
                ${result.sources.map(s => `<div class="source-item">• ${escHtml(s)}</div>`).join('')}
              </div>` : ''}
          </div>`;
      } else {
        resultEl.innerHTML = `
          <div class="answer-box">
            <div class="answer-status text-danger">✕ ${escHtml(result.message || '검색 결과가 충분하지 않아 답변을 생성할 수 없습니다.')}</div>
            ${result.partial_result ? `
              <hr class="divider">
              <div class="card-title" style="font-size:13px;color:var(--warning)">⚠ 부분 결과</div>
              <div class="answer-text">${escHtml(result.partial_result)}</div>` : ''}
          </div>`;
      }

      const histEl = document.getElementById('query-history');
      if (histEl) histEl.innerHTML = renderQueryHistory();
    },
    (msg) => {
      _queryCtrl = null;
      btn.disabled = false;
      resultEl.innerHTML = `<div class="answer-box text-danger">오류: ${escHtml(msg)}</div>`;
      toast(msg, 'error');
    },
  );
}

function clearQuery() {
  document.getElementById('main-query').value = '';
  document.getElementById('query-result').style.display = 'none';
}

// ══════════════════════════════════════════════════════════════════════════
// 초기화
// ══════════════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', async () => {
  buildSidebar();
  // 서버에서 jobs를 미리 로드한 뒤 dashboard 렌더링
  await fetchAndSyncJobs().catch(() => {});
  navigate('dashboard');
});
