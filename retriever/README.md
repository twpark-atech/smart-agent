# Retrieval Multi-Agent

문서 파서(parser)가 적재한 데이터를 기반으로 사용자 질의에 답변하는 Multi-Agent RAG 시스템.

---

## 목차

1. [시스템 구조](#시스템-구조)
2. [에이전트 흐름](#에이전트-흐름)
3. [디렉토리 구조](#디렉토리-구조)
4. [에이전트 상세](#에이전트-상세)
5. [Tool 상세](#tool-상세)
6. [데이터 모델](#데이터-모델)
7. [설치 및 실행](#설치-및-실행)
8. [설정](#설정)

---

## 시스템 구조

```
사용자 질의
    │
    ▼
┌─────────────────────────────────────────────┐
│                 Orchestrator                 │  흐름 제어 / retry 관리
└───────────────────┬─────────────────────────┘
                    │
          ┌─────────▼─────────┐
          │      Planner       │  쿼리 분석 / 도메인 선택 / Task Queue 생성
          └─────────┬─────────┘
                    │ Task Queue
          ┌─────────▼─────────┐
          │      Retriever     │  계층적 검색 / Small-to-Big 확장
          └─────────┬─────────┘
                    │ 검색 결과 (섹션 전체 내용)
          ┌─────────▼─────────┐
          │     Aggregator     │  품질 검토 / 병합 / 구조화
          └─────────┬─────────┘
                    │ 구조화된 내용
          ┌─────────▼─────────┐
          │       Writer       │  답변 작성  (Qwen2.5-3B)
          └─────────┬─────────┘
                    │ 작성된 답변
          ┌─────────▼─────────┐
          │     Supervisor     │  사실성 / 논리 / 품질 검증
          └─────────┬─────────┘
                    │
                    ▼
               최종 답변
```

---

## 에이전트 흐름

### 정상 흐름

```
Planner → Retriever → Aggregator → Writer → Supervisor → 완료
```

### Retrieval 재시도 (최대 3회)

```
Aggregator → RE_SEARCH
    └→ Orchestrator → Planner(쿼리 재작성) → Retriever → Aggregator
```

### Supervisor 재처리 (최대 2회)

```
Supervisor → RETRY_RETRIEVAL
    └→ Orchestrator → Retriever → Aggregator → Writer → Supervisor

Supervisor → RETRY_WRITER
    └→ Orchestrator → Writer → Supervisor
```

### 실패 응답

```python
{
    "status": "failed",
    "reason": "max_retry_exceeded",
    "detail": "...",
    "partial_result": "마지막 답변 (있는 경우)",
    "message": "검색 결과가 충분하지 않아 답변을 생성할 수 없습니다."
}
```

---

## 디렉토리 구조

```
retriever/
├── main.py              # CLI 진입점
├── config.py            # 설정 (parser/config.py 확장)
├── models.py            # 데이터 모델 (TaskQueue, SearchResult 등)
├── requirements.txt
├── agents/
│   ├── orchestrator.py  # 전체 흐름 제어
│   ├── planner.py       # 쿼리 분석 및 Task Queue 생성 (VLM)
│   ├── retriever.py     # 검색 실행 + Small-to-Big 확장
│   ├── aggregator.py    # 품질 검토 및 구조화 (VLM)
│   ├── writer.py        # 답변 작성 (3B)
│   └── supervisor.py    # 답변 검증 (VLM)
└── tools/
    ├── opensearch_search.py  # Hybrid Search (KNN + BM25 → RRF)
    ├── postgres_search.py    # 정량적 검색 (SELECT 전용)
    ├── web_search.py         # Serper API 웹 검색
    └── image_search.py       # 이미지 유사도 검색
```

---

## 에이전트 상세

### Orchestrator
- 전체 에이전트 호출 순서 및 조건 제어
- Aggregator / Supervisor의 재처리 신호 수신 및 라우팅
- `retrieval_retry` / `supervisor_retry` 카운터 관리 (별도 관리)
- retry 한도 초과 시 실패 응답 반환

| 신호 출처 | 신호 | 동작 |
|----------|------|------|
| Aggregator | `RE_SEARCH` | Planner 재호출(쿼리 재작성) → Retriever 재호출 |
| Supervisor | `RETRY_RETRIEVAL` | Retriever → Aggregator 재호출 |
| Supervisor | `RETRY_WRITER` | Writer만 재호출 |

### Planner
- 사용자 질의 → `task_type` 판단 / 도메인 선택 / 검색 쿼리 생성
- Task Queue 생성 후 Orchestrator에 전달 (이후 상태 관리는 Orchestrator 담당)
- 재작성 요청 수신 시 쿼리 갱신 및 step 상태 초기화
- 사용 모델: **Qwen3-VL-32B**

| task_type | 조건 |
|-----------|------|
| `document` | 내부 문서 검색으로 충분한 경우 |
| `web` | 최신 정보 / 외부 지식 필요 |
| `quantitative` | 수치·통계·집계 결과 필요 |
| `mixed` | document + web 복합 |

### Retriever
- Task Queue의 step을 의존성 순서에 따라 실행
- 의존성 없는 step은 `ThreadPoolExecutor`로 병렬 실행 (`mixed` 타입에서 document + web 동시 실행)
- **Small-to-Big Retrieval**: 명제(proposition) 검색으로 섹션을 찾은 뒤, PostgreSQL에서 섹션 전체 블록 내용(텍스트 + 이미지 설명 + 표)을 조회하여 반환

```
OpenSearch 명제 검색 (정밀 탐색)
    ↓
section_id 기준 중복 제거 (동일 섹션의 명제 여러 개 → 1건으로)
    ↓
PostgreSQL parser_blocks에서 섹션 전체 내용 조회
    ↓
Aggregator에 전달
```

### Aggregator
- 검색 결과와 사용자 질의의 관련성 판단 (LLM 자체 판단)
- 충분: 관련 내용 선별 → 섹션 단위 구조화 → Writer로 전달
- 불충분: `RE_SEARCH` 신호 → Orchestrator로 전달 (쿼리 재작성은 Planner 담당)
- 사용 모델: **Qwen3-VL-32B**

### Writer
- Aggregator의 구조화된 내용만 사용하여 답변 작성
- 확인되지 않은 정보는 사용하지 않음
- 답변 하단에 출처 기록
- 사용 모델: **Qwen2.5-3B** (정형화된 구조 기반 작성)

### Supervisor
- 작성된 답변을 구조화된 근거 자료와 대조하여 검증
- Retriever / Writer로 직접 요청하지 않고 반드시 Orchestrator를 경유
- 사용 모델: **Qwen3-VL-32B**

| 검증 결과 | 신호 | 조건 |
|----------|------|------|
| 통과 | `PASS` | 사실성·논리·품질 모두 통과 |
| 재검색 | `RETRY_RETRIEVAL` | 근거 데이터 자체가 부족한 경우 |
| 재작성 | `RETRY_WRITER` | 데이터는 있으나 답변 품질 저하 |

---

## Tool 상세

### opensearch_search
- **대상 인덱스**: `parser_documents` (문서 레벨) / `parser_propositions` (노드 레벨)
- **방식**: KNN(Vector) + BM25 병렬 실행 → RRF(Reciprocal Rank Fusion) 병합
- **임베딩**: Qwen/Qwen3-Embedding-0.6B (1024차원)
- **BM25 검색 필드**: `summary` / `proposition`, `keywords`, `section_path`, `doc_name`
- **필터**: `domain_category`, `document_id`

```
embedding_query → 임베딩 서버 → 1024차원 벡터
    ├→ KNN Search (top k×2)  ─┐
    └→ BM25 Search (top k×2) ─┴→ RRF 병합 → top k 반환
```

### postgres_search
- **용도**: 정량적 자료 검색 (Planner가 생성한 SQL 실행)
- **제한**: `SELECT`만 허용, DDL/DML 차단
- **대상 테이블**: `parser_tables`, `parser_table_rows` (파서가 적재한 표 데이터)

### web_search
- **API**: Serper API (`https://google.serper.dev/search`)
- **호출 조건**: Planner의 `task_type`이 `web` 또는 `mixed`일 때만 Retriever가 호출
- **반환**: title, url, snippet, 접근 시각

### image_search
- **방식**: VLM(Qwen3-VL-32B) 임베딩 → OpenSearch KNN 검색
- **폴백**: VLM 임베딩 실패 시 텍스트 BM25 검색
- **원본**: 이미지 파일은 MinIO에 저장, `minio_path`로 접근

---

## 데이터 모델

### TaskQueue

```python
TaskQueue(
    task_id: str,
    query: QueryPlan(
        original: str,          # 사용자 원본 질의
        domain_query: str,      # 도메인 검색 쿼리
        embedding_query: str,   # Vector 검색 쿼리
        keyword_query: str,     # BM25 키워드 쿼리
        sql_query: str | None,  # 정량적 검색 SQL
    ),
    task_type: str,             # document | web | quantitative | mixed
    domain: str,                # 선택된 도메인 (10개 중 1개)
    steps: list[TaskStep],
    retrieval_retry: int,       # 현재 재검색 횟수
    max_retrieval_retry: int,   # 최대 재검색 허용 횟수 (기본 3)
    supervisor_retry: int,      # 현재 Supervisor 재처리 횟수
    max_supervisor_retry: int,  # 최대 Supervisor 재처리 허용 횟수 (기본 2)
)
```

### 검색 결과 (node_search 기준)

```python
{
    "id": str,
    "score": float,                  # RRF 병합 점수
    "document_id": str,
    "section": str,                  # section_path
    "content": str,                  # 섹션 전체 블록 내용 (text + image 설명 + table)
    "matched_proposition": str,      # 매칭에 사용된 명제 (참고용)
    "keywords": list[str],
    "source_type": "node",
    "source": {
        "file_name": str,
        "section_path": str,
        "section_title": str,
    }
}
```

---

## 설치 및 실행

### 의존성 설치

```bash
cd retriever
pip install -r requirements.txt
```

### 실행

```bash
# 기본 실행
python main.py "질의 내용"

# 상세 로그 출력 (시스템 로그 포함)
python main.py "질의 내용" --verbose
```

기본 실행 시 각 에이전트의 LLM 응답이 로그로 출력됩니다.

```
2026-04-09 12:00:01 [INFO] - [Planner:plan] 응답:
{"task_type": "document", "domain": "산업/제조", ...}

2026-04-09 12:00:05 [INFO] - [Aggregator] 응답:
{"quality": "ok", "structured_content": [...]}

2026-04-09 12:00:07 [INFO] - [Writer] 응답:
스마트 제조에서 활용되는 DB 종류는 ...

2026-04-09 12:00:08 [INFO] - [Supervisor] 응답:
{"verdict": "PASS", "reason": "..."}
```

### 전제 조건

문서 파서(`parser/`)가 먼저 실행되어 아래 데이터가 적재된 상태여야 합니다.

| 저장소 | 데이터 |
|--------|--------|
| OpenSearch `parser_documents` | 문서 요약 임베딩 + 키워드 |
| OpenSearch `parser_propositions` | 명제 임베딩 + 키워드 |
| PostgreSQL `parser_*` | 섹션 / 블록 / 표 원문 데이터 |
| MinIO | 이미지 원본 파일 |

---

## 설정

`config.py`에서 환경변수로 오버라이드 가능합니다.

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `LLM_URL` | `http://112.163.62.170:8012/v1` | LLM 서버 주소 |
| `LLM_MODEL` | `Qwen2.5-3B-Instruct` | Writer 사용 모델 |
| `VLM_MODEL` | `Qwen3-VL-32B-Instruct-AWQ` | Planner/Aggregator/Supervisor 사용 모델 |
| `EMBEDDING_URL` | `http://112.163.62.170:8032/v1` | 임베딩 서버 주소 |
| `OPENSEARCH_HOST` | `localhost` | OpenSearch 호스트 |
| `OPENSEARCH_PORT` | `9200` | OpenSearch 포트 |
| `POSTGRES_HOST` | `localhost` | PostgreSQL 호스트 |
| `SERPER_URL` | `https://google.serper.dev/search` | Serper API 엔드포인트 |
| `SERPER_API_KEY` | _(설정 필요)_ | Serper API 키 |
