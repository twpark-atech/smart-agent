# Smart Agent Demo

문서 파싱 → 벡터 인덱싱 → 계층적 RAG 검색을 한 번에 체험할 수 있는 데모 시스템.

---

## 아키텍처

```
[문서 업로드]
     │
     ▼
┌─────────────┐      ┌─────────────────────┐
│  Indexer    │─────▶│  OpenSearch (9200)  │
│  (8001)     │      │  - smart_agent_documents │
└─────────────┘      │  - smart_agent_chunks    │
                     └─────────────┬───────┘
┌─────────────┐                   │
│  Searcher   │◀──────────────────┘
│  (8002)     │
└─────────────┘
       ▲
       │
┌─────────────┐
│  Demo UI    │
│  (8501)     │
└─────────────┘
```

### 서비스 구성

| 서비스 | 포트 | 역할 |
|---|---|---|
| OpenSearch | 9200 | 벡터 + BM25 인덱스 |
| Indexer | 8001 | 문서 파싱 · 인덱싱 API |
| Searcher | 8002 | RAG 검색 API |
| Demo UI | 8501 | Streamlit 웹 인터페이스 |

### LLM / 임베딩

| 항목 | 모델 | 엔드포인트 |
|---|---|---|
| LLM | Qwen2.5-3B-Instruct | `112.163.62.170:8012/v1` |
| Embedding | Qwen3-Embedding-0.6B (dim 1024) | `112.163.62.170:8032/v1` |

---

## 인덱싱 파이프라인

`chunking_strategy.md` 기준으로 구현.

```
[1] 문서 파싱
    .md/.txt → 직접 읽기
    PDF/DOCX/HWPX → Docling 변환

[2] 문서 유형·도메인 분류 (LLM)
    → doc_type, domain_category

[3] 목차 기반 섹션 분리
    목차 있음 → 목차 파싱 후 섹션 묶기
    목차 없음 → 헤딩(#) 기반 섹션 묶기

[3-5] 섹션 요약 생성 (LLM)
    → 2~3문장 요약 → section_summary_embedding

[4] 토큰 크기 조정
    2048 토큰 초과 섹션 → 하위 분할
    분할 서브청크는 parent_section_id 보존 (Auto-Merge용)

[5] Proposition 추출 (LLM)
    → 핵심 명제 1문장 → proposition_embedding

[6] Contextual Chunking 접두어 생성
    [도메인: ...] [문서: ...] [섹션: ...] + proposition

[7] OpenSearch 적재
    Layer 2: 문서 인덱스 (summary_embedding)
    Layer 3: 청크 인덱스 (section_summary_embedding + proposition_embedding)
```

---

## 검색 파이프라인

`hierarchical_rag.md` 기준으로 구현. 쿼리 복잡도에 따라 Track A/B 자동 분기.

```
[Step 0] Query Rewriter Agent (LLM)
    구어체·질문형 쿼리 → 검색 최적화 명제형 쿼리로 변환
    이미 명제형이면 원문 그대로 사용

[Step 1] 쿼리 임베딩 생성 (변환된 쿼리로)

[Step 2] 도메인 분류 + Track 결정 (LLM)
    is_complex=false → Track A
    is_complex=true  → Track B

─── Track A (단순 쿼리) ──────────────────────────────
[Step 3]   문서 검색 (Hybrid: Vector + BM25 + RRF) + 도메인 필터
           도메인 매칭 문서 없으면 필터 해제 후 전체 범위 재검색 (domain_fallback)
[Step 3-2] 섹션 검색 (section_summary_embedding)
[Step 4]   청크 검색 (proposition_embedding) + Auto-Merge

─── Track B (복합 쿼리) ──────────────────────────────
[Step 2]   LLM으로 서브 쿼리 분해 (쿼리별 domain_category 포함)
[Step 3]   서브 쿼리별 병렬 문서 검색
[Step 3-2] 서브 쿼리별 병렬 섹션 검색
[Step 4]   통합 청크 검색 + Auto-Merge

─── 공통 ─────────────────────────────────────────────
[Step 5] Writer Agent: 청크 기반 답변 작성
[Step 6] Validator Agent: 근거 충실성·완전성·일관성 검증
         미충족 시 Top-K 확장 후 1회 재생성
```

---

## 구현 현황

### 완료

| 구분 | 항목 | 파일 |
|---|---|---|
| **인덱싱** | 문서 파싱 (MD, PDF/DOCX via Docling) | `indexer/pipeline.py` |
| | 문서 유형·도메인 LLM 분류 | `shared/llm.py` |
| | 목차 기반 / 헤딩 기반 섹션 분리 | `indexer/pipeline.py` |
| | 섹션 요약 생성 (LLM) | `shared/llm.py` |
| | 토큰 크기 조정 + parent_section_id 보존 | `indexer/pipeline.py` |
| | Proposition 추출 (LLM) | `shared/llm.py` |
| | Contextual Chunking 접두어 생성 | `indexer/pipeline.py` |
| | OpenSearch 적재 (문서 + 청크 인덱스) | `shared/opensearch_client.py` |
| **검색** | Query Rewriter Agent (명제형 쿼리 변환) | `shared/llm.py` |
| | 쿼리 도메인 분류 + Track A/B 분기 (LLM) | `shared/llm.py` |
| | 문서 검색 Hybrid (Vector + BM25 + RRF) | `shared/opensearch_client.py` |
| | 섹션 검색 (Index Search) | `shared/opensearch_client.py` |
| | 청크 검색 (proposition_embedding) | `shared/opensearch_client.py` |
| | Auto-Merge (형제 청크 병합) | `searcher/pipeline.py` |
| | Track B 서브 쿼리 병렬 검색 | `searcher/pipeline.py` |
| | Writer Agent | `shared/llm.py` |
| | Validator Agent (Top-K 확장 재생성 포함) | `shared/llm.py` |
| **API** | Indexer REST API (업로드·목록·상태·삭제) | `indexer/main.py` |
| | Searcher REST API (검색) | `searcher/main.py` |
| **인프라** | OpenSearch 인덱스 매핑 (kNN + BM25) | `shared/opensearch_client.py` |
| | Docker Compose (OpenSearch + Indexer + Searcher + UI) | `docker-compose.yml` |
| **UI** | Streamlit 데모 (채팅 + 문서 관리) | `ui/app.py` |

### 미구현 (설계 완료, 데모 제외)

| 항목 | 비고 |
|---|---|
| Layer 1 도메인 인덱스 | `domain_type.md` 대분류 기반 사전 도메인 임베딩. 현재는 LLM 분류로 대체 |
| Re-ranking (Cross-Encoder) | Step 3-1. 현재는 RRF만 적용 |
| HWP/PPTX/IMG 파싱 | Docling이 미지원하는 포맷 별도 파서 필요 |
| Fallback 로직 | 섹션 필터 해제 등 일부 fallback 미구현 (도메인 필터 fallback은 구현됨) |
| 멀티테넌트 격리 | 테넌트별 인덱스/접근 권한 분리 |

---

## 실행

### Docker Compose (권장)

```bash
cd demo
docker-compose up --build
```

| 서비스 | URL |
|---|---|
| Demo UI | http://localhost:8501 |
| Indexer API | http://localhost:8001/docs |
| Searcher API | http://localhost:8002/docs |
| OpenSearch | http://localhost:9200 |

### 로컬 실행

```bash
cd demo
pip install -r requirements.txt

# OpenSearch는 Docker로 별도 실행
docker run -d -p 9200:9200 \
  -e discovery.type=single-node \
  -e DISABLE_SECURITY_PLUGIN=true \
  opensearchproject/opensearch:2.18.0

# 각 서비스 실행 (터미널 3개)
uvicorn indexer.main:app --port 8001 --reload
uvicorn searcher.main:app --port 8002 --reload
streamlit run ui/app.py
```

---

## API

### Indexer (8001)

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/documents` | 파일 업로드 + 인덱싱 시작 (비동기) |
| `GET` | `/documents` | 인덱싱된 문서 목록 (chunk_count 포함) |
| `GET` | `/documents/{id}` | 문서 상세 |
| `GET` | `/documents/{id}/status` | 인덱싱 진행 상태 |
| `DELETE` | `/documents/{id}` | 문서 + 청크 삭제 |
| `GET` | `/health` | 서버 상태 + 인덱스 통계 |

지원 파일 형식: `pdf`, `docx`, `hwpx`, `md`, `txt`

### Searcher (8002)

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/search` | RAG 검색 |
| `GET` | `/health` | 서버 상태 + 인덱스 통계 |

**검색 요청**
```json
{ "query": "질문 내용", "top_k": 5 }
```

**검색 응답**
```json
{
  "query": "질문 내용",
  "answer": "LLM 생성 답변",
  "track": "A",
  "original_query": "사용자 원본 질의",
  "rewritten_query": "검색 최적화 명제형 쿼리",
  "domain_candidates": [{ "domain_category": "산업/제조" }, { "domain_category": "과학기술/IT" }],
  "domain_fallback": false,
  "validation": { "valid": true, "score": 0.85, "message": "검증 결과" },
  "docs": [{ "document_id": "...", "title": "..." }],
  "chunks": [{ "section_path": "...", "proposition": "...", "content_preview": "..." }],
  "sub_queries": [{ "query": "서브쿼리", "domain_category": "..." }]
}
```
