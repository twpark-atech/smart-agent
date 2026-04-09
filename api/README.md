# Smart Agent API

Document Parser Workflow와 Retrieval Multi-Agent를 통합한 FastAPI 서버 및 웹 UI입니다.

## 구조

```
api/
├── main.py              # FastAPI 앱 진입점
├── config.py            # 설정 (parser/config.py 재사용)
├── routers/
│   ├── parser.py        # /parser/* 엔드포인트
│   └── retriever.py     # /retriever/* 엔드포인트
└── static/
    ├── index.html
    ├── css/style.css
    └── js/
        ├── api.js       # fetch 클라이언트
        └── app.js       # SPA 라우터 & 페이지 렌더러
```

---

## 사전 준비

### 1. 인프라 실행 (Docker)

`parser/docker-compose.yml` 기준으로 OpenSearch, MinIO, PostgreSQL을 실행합니다.

```bash
cd /home/atech/Projects/smart-agent/parser
docker compose up -d
```

| 서비스 | 포트 | 용도 |
|---|---|---|
| OpenSearch | 9200 | Vector / BM25 검색 |
| MinIO | 9000 / 9001 | 이미지 원본 저장 (콘솔: :9001) |
| PostgreSQL | 5432 | Job 상태, 섹션·블록·명제 저장 |

### 2. 의존성 설치

```bash
cd /home/atech/Projects/smart-agent
pip install fastapi uvicorn python-multipart psycopg2-binary \
            opensearch-py minio openai httpx
```

> parser / retriever 각 디렉토리에 `.venv`가 있는 경우 해당 venv를 사용하세요.

### 3. 환경 변수 (선택)

`parser/config.py`의 기본값이 적용됩니다. 필요 시 환경 변수로 재정의하세요.

```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=parser
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres

export OPENSEARCH_HOST=localhost
export OPENSEARCH_PORT=9200

export MINIO_ENDPOINT=localhost:9000
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin

export LLM_URL=http://112.163.62.170:8012/v1
export EMBEDDING_URL=http://112.163.62.170:8032/v1
```

---

## 서버 실행

```bash
# smart-agent 루트에서 실행
cd /home/atech/Projects/smart-agent
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

| 경로 | 설명 |
|---|---|
| `http://localhost:8000/ui` | 웹 UI |
| `http://localhost:8000/docs` | Swagger UI (API 문서) |
| `http://localhost:8000/health` | 헬스 체크 |

---

## 웹 UI 사용법

브라우저에서 `http://localhost:8000/ui` 에 접속합니다.

### 대시보드

- 전체 문서 수, 완료/실행중/실패 통계를 표시합니다.
- 최근 작업 목록을 확인하고 바로 문서를 탐색할 수 있습니다.
- 빠른 질의 창에서 RAG 검색을 즉시 실행할 수 있습니다.

### 문서 업로드

1. **문서 업로드** 메뉴 클릭
2. 파일을 드래그 앤 드롭하거나 클릭하여 선택
3. 지원 형식: `PDF`, `DOCX`, `HWPX`, `PPTX`, `PNG`, `JPG`
4. 업로드 즉시 파싱 워크플로우가 백그라운드에서 자동 실행됩니다.
5. 각 Step의 진행 상황이 실시간으로 표시됩니다.
   - `포맷 변환` → `목차 추출` → `구조화` → `섹션 파싱` → `문서 통합`

> 동일 파일을 재업로드하면 완료된 Step은 건너뛰고 미완료 Step부터 재개됩니다 (MD5 기준 멱등성).

### 문서 관리

- 전체 문서 목록을 확인합니다. 페이지 새로고침 후에도 DB에서 복원됩니다.
- **파일명 검색** 및 **상태 필터** (대기/실행중/완료/실패/중단됨)를 지원합니다.
- **새로고침** 버튼으로 최신 상태를 불러옵니다.
- **상세** 버튼: 각 Step의 시작/완료 시간, 오류 메시지를 모달로 확인합니다.
- **탐색** 버튼 (완료된 문서만): 문서 탐색 페이지로 이동합니다.
- **재실행** 버튼 (실패/중단됨): 특정 Step을 초기화해 재실행을 허용합니다.
- **중단** 버튼 (실행중): 현재 Step이 끝난 후 다음 Step 진입 전에 파싱을 중단합니다.
- **삭제** 버튼: 확인 모달 후 문서를 영구 삭제합니다 (PostgreSQL · OpenSearch · MinIO 전체).

### 문서 탐색

1. 상단 드롭다운에서 파싱 완료된 문서를 선택
2. 좌측 섹션 트리에서 목차 항목 클릭
3. 우측에 해당 섹션의 **블록**(텍스트/이미지/표)과 **명제** 목록이 표시됩니다.

### 검색 질의

1. **검색 질의** 메뉴 클릭
2. 질문 입력 후 **실행** 버튼 클릭 (또는 `Ctrl+Enter`)
3. `Planner → Retriever → Aggregator → Writer → Supervisor` 파이프라인을 거쳐 답변이 생성됩니다.
4. 답변과 함께 출처(섹션 경로) 목록이 표시됩니다.
5. 최근 질의 이력은 하단에서 클릭해 재실행할 수 있습니다.

---

## API 레퍼런스

전체 문서는 `/docs` (Swagger UI) 또는 `/redoc` 에서 확인하세요.

### Parser

#### `GET /parser/jobs`
전체 job 목록을 반환합니다.

| 쿼리 파라미터 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `status` | string | — | `pending` \| `running` \| `completed` \| `failed` 필터 |
| `limit` | int | 100 | 최대 반환 수 |
| `offset` | int | 0 | 페이지 오프셋 |

```bash
curl http://localhost:8000/parser/jobs
curl http://localhost:8000/parser/jobs?status=completed&limit=20
```

#### `POST /parser/jobs`
파일을 업로드하고 파싱 워크플로우를 시작합니다.

```bash
curl -X POST http://localhost:8000/parser/jobs \
     -F "file=@/path/to/document.pdf"
```

응답:
```json
{ "job_id": "abc123...", "filename": "document.pdf", "status": "started" }
```

#### `GET /parser/jobs/{job_id}`
job 상태와 각 Step의 진행 상황을 반환합니다.

```bash
curl http://localhost:8000/parser/jobs/abc123
```

응답:
```json
{
  "job_id": "abc123",
  "source_path": "/tmp/.../document.pdf",
  "status": "running",
  "steps": [
    { "step_name": "format_converter", "status": "completed", ... },
    { "step_name": "index_parser",     "status": "running",   ... },
    ...
  ]
}
```

Job/Step 상태값: `pending` | `running` | `completed` | `failed` | `cancelled`

#### `DELETE /parser/jobs/{job_id}`
문서와 관련된 모든 데이터를 영구 삭제합니다.

삭제 대상:
- PostgreSQL: `parser_documents`, `parser_sections`, `parser_blocks`, `parser_propositions`, `parser_tables`, `parser_table_rows`, `parser_jobs`, `parser_job_steps`
- OpenSearch: `parser_propositions` 인덱스, `parser_documents` 인덱스
- MinIO: 변환 파일 원본

```bash
curl -X DELETE http://localhost:8000/parser/jobs/abc123
```

응답:
```json
{ "job_id": "abc123", "deleted": true }
```

> 실행 중인 job은 먼저 `/cancel`을 호출해 중단한 후 삭제하세요.

#### `POST /parser/jobs/{job_id}/cancel`
실행 중인 파싱 워크플로우를 중단 요청합니다.

```bash
curl -X POST http://localhost:8000/parser/jobs/abc123/cancel
```

응답:
```json
{ "job_id": "abc123", "status": "cancelled", "message": "현재 step 완료 후 중단됩니다" }
```

- 현재 실행 중인 Step이 완료된 후 **다음 Step 시작 전**에 중단됩니다.
- 이미 완료된 Step의 결과는 보존됩니다.
- 재개하려면 `/run`을 호출하세요.

#### `POST /parser/jobs/{job_id}/reset`
특정 Step을 초기화합니다. 다음 업로드(또는 재실행) 시 해당 Step부터 재시작됩니다.

```bash
curl -X POST http://localhost:8000/parser/jobs/abc123/reset \
     -H "Content-Type: application/json" \
     -d '{"step": "section_parser"}'
```

Step 이름: `format_converter` | `index_parser` | `structurer` | `section_parser` | `document_integrator`

#### `GET /parser/jobs/{job_id}/sections`
파싱 완료된 문서의 섹션 목록을 반환합니다.

```bash
curl http://localhost:8000/parser/jobs/abc123/sections
```

응답:
```json
[
  { "seq": 1, "level": 1, "title": "서론", "block_count": 5 },
  { "seq": 2, "level": 2, "title": "1.1 배경", "block_count": 3 },
  ...
]
```

#### `GET /parser/jobs/{job_id}/sections/{seq}`
특정 섹션의 블록(텍스트/이미지/표)과 명제 목록을 반환합니다.

```bash
curl http://localhost:8000/parser/jobs/abc123/sections/1
```

응답:
```json
{
  "job_id": "abc123",
  "seq": 1,
  "title": "서론",
  "section_path": "서론",
  "blocks": [
    { "seq": 1, "block_type": "text", "content": "...", "page": 1, "minio_key": null },
    { "seq": 2, "block_type": "image", "content": "이미지 설명...", "page": 2, "minio_key": "abc123/img_001.png" }
  ],
  "propositions": [
    { "seq": 1, "proposition": "...", "keywords": ["키워드1", "키워드2"] }
  ]
}
```

---

### Retriever

#### `POST /retriever/query`
사용자 질의를 받아 Multi-Agent RAG 파이프라인을 실행하고 답변을 반환합니다.

```bash
curl -X POST http://localhost:8000/retriever/query \
     -H "Content-Type: application/json" \
     -d '{"query": "재난 관리 체계에서 중앙정부의 역할은 무엇인가?"}'
```

성공 응답:
```json
{
  "status": "success",
  "answer": "중앙정부는 ...",
  "sources": ["서론 > 1.1 배경", "제2장 > 재난 대응 체계"]
}
```

실패 응답:
```json
{
  "status": "failed",
  "reason": "관련 문서를 찾을 수 없습니다",
  "detail": "...",
  "partial_result": "..."
}
```

파이프라인 흐름: `Planner → Retriever → Aggregator → Writer → Supervisor`

---

### Health

#### `GET /health`
서버 정상 여부를 확인합니다.

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## 파싱 워크플로우 단계

| 순서 | Step 이름 | 설명 |
|---|---|---|
| 1 | `format_converter` | 비PDF 형식(DOCX, HWPX, PPTX 등)을 PDF로 변환 |
| 2 | `index_parser` | 목차(TOC) 추출 |
| 3 | `structurer` | 목차 기반 섹션 구조화 및 도메인 분류, 블록(텍스트/이미지/표) 추출 |
| 4 | `section_parser` | 섹션별 명제 및 키워드 추출 후 OpenSearch에 임베딩 적재 |
| 5 | `document_integrator` | 문서 전체 요약 및 키워드 추출 후 OpenSearch에 적재 |

워크플로우 중단 시 완료된 Step은 보존되며, 재업로드하면 미완료 Step부터 자동 재개됩니다.

### 취소 흐름

```
POST /cancel  →  job.status = "cancelled"
                      ↓
runner.py가 다음 step 시작 전 is_cancelled() 체크
                      ↓
JobCancelledError 발생 → 워크플로우 종료 (step_fail 기록 없음)
```

완료된 step의 결과는 DB에 보존되므로 `/run` 재호출 시 해당 step부터 재개됩니다.
