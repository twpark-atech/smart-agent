/**
 * Smart Agent API 클라이언트
 * 모든 fetch 호출을 중앙화하여 관리
 */
const API_BASE = '';  // 같은 origin에서 서빙

async function _fetch(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// ── Parser API ──────────────────────────────────────────────────────────

/** 전체 job 목록 조회 */
function listJobs(status = '', limit = 200) {
  const params = new URLSearchParams({ limit });
  if (status) params.set('status', status);
  return _fetch(`/parser/jobs?${params}`);
}

/** 파일 업로드 후 워크플로우 시작 */
async function uploadFile(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/parser/jobs', { method: 'POST', body: form });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/** job 상태 조회 */
function getJobStatus(jobId) {
  return _fetch(`/parser/jobs/${jobId}`);
}

/** step 초기화 */
function resetStep(jobId, step) {
  return _fetch(`/parser/jobs/${jobId}/reset`, {
    method: 'POST',
    body: JSON.stringify({ step }),
  });
}

/** 워크플로우 재실행 */
function runJob(jobId) {
  return _fetch(`/parser/jobs/${jobId}/run`, { method: 'POST' });
}

/** 추출 중단 요청 */
function cancelJob(jobId) {
  return _fetch(`/parser/jobs/${jobId}/cancel`, { method: 'POST' });
}

/** 문서 삭제 (모든 저장소) */
function deleteJob(jobId) {
  return _fetch(`/parser/jobs/${jobId}`, { method: 'DELETE' });
}

/** 문서 메타데이터 조회 */
function getDocMeta(jobId) {
  return _fetch(`/parser/jobs/${jobId}/meta`);
}

/** 섹션 목록 조회 */
function listSections(jobId) {
  return _fetch(`/parser/jobs/${jobId}/sections`);
}

/** 특정 섹션 상세 조회 */
function getSection(jobId, seq) {
  return _fetch(`/parser/jobs/${jobId}/sections/${seq}`);
}

/** MinIO 이미지 프록시 URL 생성 */
function imageUrl(minioKey) {
  return `/parser/images/${encodeURIComponent(minioKey).replace(/%2F/g, '/')}`;
}

// ── Retriever API ────────────────────────────────────────────────────────

/** RAG 질의 (단일 응답) */
function queryRetriever(query) {
  return _fetch('/retriever/query', {
    method: 'POST',
    body: JSON.stringify({ query }),
  });
}

/**
 * RAG 스트리밍 질의 (SSE)
 * onEvent(ev): progress / done / error 이벤트마다 호출
 * 반환값: AbortController (취소 가능)
 */
function queryRetrieverStream(query, onEvent) {
  const ctrl = new AbortController();

  (async () => {
    let res;
    try {
      res = await fetch('/retriever/query/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
        signal: ctrl.signal,
      });
    } catch (e) {
      if (e.name !== 'AbortError') onEvent({ type: 'error', detail: e.message });
      return;
    }

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch {}
      onEvent({ type: 'error', detail });
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer    = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop();
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(part.slice(6));
            onEvent(ev);
            if (ev.type === 'done' || ev.type === 'error') return;
          } catch {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') onEvent({ type: 'error', detail: e.message });
    }
  })();

  return ctrl;
}

// ── Health ────────────────────────────────────────────────────────────────

function healthCheck() {
  return _fetch('/health');
}
