# 수정 이력

---

## 2026-04-10 — XLSX 시트 특정 셀 값 검색 실패 수정

**문제**
- "직물 설계표에서 RC-7242의 성통폭은 몇 인치야?" 질의 시 검색 실패
- 정확한 값이 `parser_table_rows.row_data` JSONB에 저장되어 있어도 찾지 못함

**원인 (3가지)**

1. **`_PG_SCHEMA` 오래된 정보** (`retriever/agents/planner.py`)
   - Planner LLM에 전달하는 스키마 힌트에 `sheet_name` 컬럼이 없었음
   - LLM이 `WHERE sheet_name = 'RC-7242'` SQL을 생성할 수 없어 정량적 검색 실패

2. **`quantitative` 작업 유형 분류 기준 불명확** (동일 파일)
   - 프롬프트에 "엑셀 셀 값 조회는 quantitative 선택" 지침이 없어
     LLM이 `document` 또는 `web`으로 오분류하는 경우 발생

3. **xlsx 파일이 OpenSearch 명제 인덱스에 미등록** (`parser/section_parser/__init__.py`)
   - `section_parser`가 xlsx/csv를 무조건 skip → `parser_propositions` 인덱스에 데이터 없음
   - 벡터/키워드 검색 경로에서도 특정 셀 값을 찾을 수 없었음

**수정**

| 파일 | 변경 내용 |
|------|-----------|
| `retriever/agents/planner.py` | `_PG_SCHEMA`에 `sheet_name TEXT`, `description TEXT` 추가; JSONB 조회 예시·시트명 필터 예시 주석 추가 |
| `retriever/agents/planner.py` | `_PLAN_PROMPT`의 `quantitative` 설명에 "엑셀 특정 값 조회 시 반드시 선택" 지침 추가 |
| `parser/section_parser/__init__.py` | xlsx/csv skip 대신 `_run_table_index()` 호출; 테이블 행 전체를 `시트명: col=val, ...` 형태로 명제 생성 → 임베딩 → OpenSearch + PostgreSQL 적재 |

**동작 변화**
- Planner가 `sheet_name` 기반 SQL (`WHERE t.sheet_name = 'RC-7242'`) 생성 가능
- 향후 xlsx 업로드 시 모든 행이 OpenSearch에 명제로 색인 → 벡터/키워드 검색도 동작
- 기존 파싱 완료 파일은 재업로드 시 적용

---

## 2026-04-10 — parser_tables 컬럼 누락 오류 수정

**문제**
- `init_schema()` 호출 시 `CREATE TABLE IF NOT EXISTS`는 테이블이 이미 존재하면 DDL을 무시
- `sheet_name`, `header_depth`, `description` 컬럼이 기존 DB에 추가되지 않아 INSERT 오류 발생

**수정** (`parser/db/__init__.py`)
- `MIGRATIONS` 상수 추가: `ALTER TABLE parser_tables ADD COLUMN IF NOT EXISTS` 3개
- `init_schema()`에서 DDL 실행 후 MIGRATIONS도 실행하도록 변경
- `IF NOT EXISTS`이므로 재실행 시 멱등성 보장

---

## 2026-04-10 — 업로드 UI에서 XLSX / CSV 파일 지원 추가

**수정** (`api/static/js/app.js`)
- 파일 선택 `<input>`의 `accept` 속성에 `.xlsx`, `.csv` 추가
- 업로드 영역 안내 문구에 `XLSX · CSV` 추가
- 백엔드(`detector.py`)는 이미 두 포맷을 지원하고 있어 별도 수정 불필요

---

## 2026-04-10 — XLSX / CSV 파싱 완성

**배경**
- `flat_parser`의 병합 셀 미처리, 테이블 영역 탐지 없음, CSV 인코딩 하드코딩 문제
- `parser_tables` 테이블에 `sheet_name`, `header_depth`, `description` 컬럼 누락
- `section_parser`가 xlsx/csv도 명제 추출을 시도하여 무의미한 LLM 호출 발생
- `document_integrator`가 명제 없으면 Summary/Keywords 미생성

**수정 파일**

| 파일 | 변경 내용 |
|------|-----------|
| `parser/document_parser/models.py` | `Block`에 `sheet_name`, `header_depth`, `description` 필드 추가; `to_dict()` 반영 |
| `parser/document_parser/flat_parser.py` | XLSX: `read_only=False` 변경, `_expand_merged_cells()` / `_detect_tables()` / `_detect_header_depth()` / `_build_flat_headers()` 구현. CSV: `chardet`으로 인코딩 자동 감지 |
| `parser/db/__init__.py` | `parser_tables` DDL에 `sheet_name TEXT`, `header_depth INT DEFAULT 1`, `description TEXT` 추가; `_insert_table_rows()` 시그니처 및 INSERT 반영 |
| `parser/section_parser/__init__.py` | `run()` 진입 시 `original_ext`가 `.xlsx`/`.csv`이면 즉시 반환 (`skipped: True`) |
| `parser/document_integrator/__init__.py` | `_load_propositions()` 결과 없으면 `_load_from_tables()` 호출; `_load_from_tables()` 구현 (컬럼목록·행수·샘플 5행 가상 명제 생성) |
| `parser/workflow/runner.py` | `_step_structurer` 결과에 `original_ext` 추가 (section_parser 스킵 판단용) |

**상세**
- `_expand_merged_cells`: `ws.merged_cells.ranges` 순회 → 상단 좌측 값을 범위 전체에 fill 후 unmerge
- `_detect_tables`: 완전히 빈 행을 경계로 독립 테이블 영역 분리 (시트 내 복수 테이블 대응)
- `_detect_header_depth`: 상단 행이 전부 문자열인 경우 다중 헤더로 판정
- `_build_flat_headers`: 다중 헤더를 `"부모.자식"` 형태로 flat화, 중복 컬럼명 자동 dedup
- CSV 인코딩: `chardet.detect()` 결과 우선, 감지 실패 시 `utf-8-sig` 폴백

---

## 2026-04-10 — 파싱 실패 시 UI 에러 메시지 사용자 친화적으로 개선

**문제**
- 문서 관리 상세 화면에서 실패한 step의 에러로 Python 스택트레이스 전체가 노출됨

**수정** (`parser/workflow/runner.py`)
- DB(`parser_job_steps.error`)에 저장하는 에러를 `traceback.format_exc()` → `"step_name: str(e)"` 형식으로 변경
  - 예: `format_converter: 변환된 PDF를 찾을 수 없습니다: /tmp/parser_fc__wfax56i`
- 스택트레이스는 `logger.error(..., exc_info=True)`로 서버 로그에만 기록
- `import traceback` 제거

---

## 2026-04-10 — .hwp 파일 지원 추가

**배경**
- 구형 `.hwp` 포맷 업로드 시 "지원하지 않는 포맷" 오류 발생
- LibreOffice가 `.hwp`를 직접 PDF로 변환하지 못함 (returncode=0이나 PDF 미생성)

**변환 경로**
```
.hwp → hwp5html → .html → LibreOffice → .pdf → 기존 PDF 파이프라인
```

**수정 파일**

| 파일 | 변경 내용 |
|------|-----------|
| `parser/format_converter/detector.py` | `.hwp`를 `SUPPORTED_EXTENSIONS` 및 `CONVERT_TO_PDF`에 추가 |
| `parser/format_converter/converter.py` | `.hwp` 전용 `_hwp_to_pdf()` 분기 추가 (hwp5html → HTML → LibreOffice → PDF). LibreOffice 호출을 `_libreoffice_convert()`로 분리하고 stderr 로깅 강화 |

- `hwp5html`은 시스템에 이미 설치된 CLI 도구 활용 (`/home/atech/.local/bin/hwp5html`)
- `.hwpx` / `.docx`는 기존 LibreOffice 직접 변환 경로 유지

---

## 2026-04-10 — PPTX `shape is not a placeholder` 오류 수정

**문제**
- PPTX 파일 처리 시 `[FAIL] index_parser: shape is not a placeholder` 발생

**원인**
- python-pptx에서 `shape.placeholder_format` 프로퍼티는 `hasattr()`이 `True`를 반환해도 실제 접근 시 `ValueError: shape is not a placeholder`를 던지는 경우 존재

**수정** (`parser/index_parser/heading_extractor.py` `extract_from_pptx()`)
- `shape.placeholder_format` 접근을 `try/except ValueError`로 보호

---

## 2026-04-10 — xlsx / csv 파싱 파이프라인 완성

**문제**
- `flat_parser.py`에 xlsx/csv 파싱 코드는 있었으나, index_parser와 structurer가 표 문서를 적절히 처리하지 못함
  - `index_parser`: `_blocks_to_text()`가 `text` 블록만 추출 → xlsx/csv의 `table` 블록은 무시 → 빈 문자열로 LLM 분류/TOC 추출 호출 (낭비 + 무의미)
  - `structurer`: `_split_by_toc()`(텍스트 헤딩 매칭) 적용 → 표 문서에 부적절

**수정 내용**

| 파일 | 변경 내용 |
|------|-----------|
| `parser/index_parser/__init__.py` | `.csv`/`.xlsx` 진입 시 LLM 없이 즉시 반환. csv: `doc_type="스프레드시트"`, TOC=`[데이터]`. xlsx: table 블록 첫 줄에서 시트명 추출 → 시트별 TOC 항목 생성 |
| `parser/structurer/__init__.py` | `_split_tabular()` 추가: table 블록 1개 = 섹션 1개, TOC 제목을 섹션 제목으로 매핑. `run()`에서 `.csv`/`.xlsx` 분기 추가 |

---

## 2026-04-10 — 이미지 크롭 시 "Crop exceeds page dimensions" 오류 수정

### 문제

**증상**
- `format_converter` 단계에서 `[FAIL] format_converter: Crop exceeds page dimensions` 로그 발생

**원인 분석**

| # | 위치 | 원인 |
|---|------|------|
| 1 | `parser/document_parser/pdf_text.py` `_extract_images()` | pdfplumber가 반환하는 이미지 bbox가 페이지 경계를 미세하게 벗어나는 경우 발생 |
| 2 | `pypdfium2` 내부 | `page.render(crop=...)` 호출 시 `math.ceil(crop * scale)` 적용 후 렌더링 가능 크기(`width = src_w - crop_l - crop_r`)가 0 이하가 되면 `ValueError: Crop exceeds page dimensions` 발생 |
| 3 | 기존 체크 | `crop_left + crop_right >= page_width` 조건은 부동소수점 연산 후 통과해도, ceil 반올림으로 실제 렌더 크기가 0이 될 수 있음 |

**수정 내용** (`parser/document_parser/pdf_text.py`)

1. **bbox 클램핑**: `x0, x1, y0, y1`을 `[0, page_width/height]` 범위로 강제 클램핑하여 페이지 밖 좌표 차단
2. **사전 검증**: pypdfium2와 동일하게 `math.ceil(crop * scale)` 적용 후 렌더 크기를 미리 계산, 1px 미만이면 skip
3. **`try/except ValueError`**: 사전 검증 통과 후에도 예외 발생 시 해당 이미지만 건너뜀 (전체 파싱 중단 방지)

---

## 2026-04-10 — config 통합 및 max_retry_exceeded UI 개선

### 변경 1: config 통합

**배경**
- `parser/config.py`, `api/config.py`, `retriever/config.py`, `demo/shared/config.py` 4곳에 설정이 분산되어 있었고, api/retriever config는 importlib으로 parser config를 동적 로드하는 방식

**수정 내용**

| 파일 | 변경 내용 |
|------|-----------|
| `config.py` (신규) | 루트에 모든 설정 통합. LLM, Embedding, OpenSearch, PostgreSQL, MinIO, Serper, API, Chunking, Taxonomy 포함 |
| `parser/config.py` | 삭제 |
| `api/config.py` | 삭제 |
| `retriever/config.py` | 삭제 |
| `demo/shared/config.py` | 루트 config 재수출 shim으로 교체 (`from shared.config import ...` 호환 유지) |
| `api/main.py` | sys.path에 프로젝트 루트(`_ROOT`) 추가 |
| `api/routers/parser.py` | importlib 동적 로드 제거 → `from config import UPLOAD_DIR` |
| `.env` (신규) | 전체 환경 변수 기본값 파일 생성 |

**추가된 OpenSearch 설정**

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OPENSEARCH_SSL` | `false` | SSL 사용 여부 |
| `OPENSEARCH_USER` | (빈 값) | 인증 ID |
| `OPENSEARCH_PASSWORD` | (빈 값) | 인증 PW |

---

### 변경 2: max_retry_exceeded UI 개선

**문제**
- 질의 재시도 한도 초과 시 UI에 `max_retry_exceeded` 같은 기술적 코드 문자열이 그대로 노출됨

**수정 내용**

| 파일 | 변경 내용 |
|------|-----------|
| `api/static/js/app.js` | 실패 상태 표시 시 `result.reason` → `result.message` 사용 (2곳). `result.detail` 표출 제거 |

- `reason: "max_retry_exceeded"`, `detail: "retrieval_retry 한도(N) 초과: ..."` 등 기술적 내용은 서버 로그(`logger.warning`)에만 남음
- `partial_result`(부분 답변)가 있을 경우 ⚠ 부분 결과로 계속 표시
- `orchestrator.py`의 `_failure_response`에 이미 존재하던 `message` 필드 활용 (백엔드 수정 없음)

---

## 2026-04-10 — Agent 실시간 진행 상황 스트리밍 표시

### 배경
기존에는 질의 실행 중 `Planner → Retriever → Aggregator → Writer → Supervisor` 텍스트가 고정 노출되었고, 실제 어떤 에이전트가 무엇을 하는지 알 수 없었음.

### 변경 내용

**목표**: 현재 실행 중인 에이전트명 + 추론 내용을 실시간으로 교체하여 표시 (누적 X)

| 에이전트 | 레이블 | 메시지 예시 |
|---------|--------|------------|
| Planner | 계획 수립 | `"Attention 모델이란 무엇인가?"` |
| Retriever | 데이터 수집 | `검색 유형: document | 도메인: 과학기술/IT` |
| Aggregator | 데이터 검증 | `검색 결과 12건 분석 중...` |
| Aggregator | 재검색 요청 | RE_SEARCH 사유 문자열 |
| Writer | 답변 작성 | `검색된 내용 기반으로 답변 생성 중...` |
| Supervisor | 품질 검증 | `사실성·완전성 검토 중...` |
| Supervisor | 재검색/재작성 지시 | RETRY 사유 문자열 |

**수정 파일**

| 파일 | 변경 내용 |
|------|-----------|
| `retriever/agents/orchestrator.py` | `run(query, progress_cb=None)` 파라미터 추가. 각 에이전트 호출 전 `emit()` 으로 `{"type":"progress","agent":..,"label":..,"message":..}` 이벤트 발행 |
| `api/routers/retriever.py` | `POST /retriever/query/stream` SSE 엔드포인트 추가. `queue.SimpleQueue` + `threading.Thread`로 동기 Orchestrator와 비동기 SSE를 브리징. 최종 이벤트: `{"type":"done","result":{...}}` |
| `api/static/js/api.js` | `queryRetrieverStream(query, onEvent)` 함수 추가. `fetch` + `ReadableStream` 방식으로 SSE 수신, `AbortController` 반환 |
| `api/static/js/app.js` | `renderThinking(label, message)` 헬퍼 추가. `runStreamQuery()` 공통 스트림 실행기 추가. `submitQuery()` / `quickQuery()` 모두 스트리밍 방식으로 전환. 이전 실행 자동 취소(AbortController). |
| `api/static/css/style.css` | `.agent-thinking`, `.thinking-pulse`(pulse 애니메이션), `.thinking-label`, `.thinking-message` 스타일 추가 |

---

## 2026-04-10 — 웹 검색 결과 URL 출처 추가

**변경 내용**
- 웹 검색(`source_type: "web"`) 결과가 Aggregator에서 실제 사용된 경우, `source.url`을 출처로 수집
- 문서 출처(BlockRef → `filename.pdf, pp.N-M`)와 웹 출처(URL)를 합산하여 최종 `sources` 반환
- 중복 URL 제거 처리

**수정 파일**

| 파일 | 변경 내용 |
|------|-----------|
| `retriever/models.py` | `AggregatedContent`, `WriterOutput`에 `web_refs: list[str]` 필드 추가 |
| `retriever/agents/aggregator.py` | used 결과 순회 시 `source_type == "web"`이면 URL 수집, 그 외는 기존 BlockRef 수집 |
| `retriever/agents/writer.py` | `WriterOutput` 생성 시 `web_refs` 전달 |
| `retriever/agents/orchestrator.py` | `sources = _resolve_block_refs(...) + writer_output.web_refs` |

**출처 예시 (혼합 질의)**
```
• attention-residuals.pdf, pp.5-14
• https://arxiv.org/abs/2405.21060
```

---

## 2026-04-10 — 출처 과다·무관 출처 포함 문제 수정

### 문제

**증상**
- 답변 2문단에 출처 44개 표출 (같은 문서에서 블록 번호 0~30 개별 나열)
- 질의와 무관한 문서(`AT_2026_스마트제조혁신_아키텍처_정의서_R0.3.docx`)가 출처에 포함

**원인 분석**

| # | 위치 | 원인 |
|---|------|------|
| 1 | `retriever/agents/retriever.py` `_fetch_section_block_refs()` | 섹션 내 모든 블록을 각각 BlockRef로 반환 → 블록 15개인 섹션 = 출처 15개 |
| 2 | `retriever/agents/aggregator.py` `aggregate()` | Aggregator LLM이 실제 사용한 결과를 알 수 없어 전체 검색 결과의 block_refs를 수집 → 관련 없는 문서 블록도 포함 |

**수정 내용**

1. **`retriever/models.py`** — `BlockRef` 구조 변경
   - 기존: `{section_id, block_seq, page}` (블록 단위)
   - 변경: `{section_id, first_page, last_page}` (섹션 단위)

2. **`retriever/agents/retriever.py`**
   - `_fetch_section_block_refs()` → `_fetch_section_ref()` 로 교체
   - `SELECT MIN(page), MAX(page) FROM parser_blocks WHERE section_id = %s` 로 섹션 페이지 범위 조회
   - 결과 dict의 `block_refs: list` → `block_ref: BlockRef` (1개)로 단순화

3. **`retriever/agents/aggregator.py`**
   - LLM 프롬프트에 `"used_result_indices": [1, 3]` 필드 추가 (실제 사용한 결과 번호 반환)
   - `used_result_indices` 기반으로 해당 결과의 `block_ref`만 수집
   - LLM이 필드를 반환하지 않으면 전체 결과를 폴백으로 사용

4. **`retriever/agents/orchestrator.py`** — 출처 포맷 변경
   - 단일 페이지: `filename.pdf, p.5`
   - 복수 페이지: `filename.pdf, pp.5-14`

**개선 결과**

| 항목 | 이전 | 이후 |
|------|------|------|
| 출처 수 | 44개 | 검색 결과 중 실제 사용한 섹션 수 (2~5개 수준) |
| 무관 문서 | 포함 | LLM이 사용하지 않은 결과 제외 |
| 포맷 | `filename.pdf, p.5 #3` (블록 번호) | `filename.pdf, pp.5-14` (페이지 범위) |

---

## 2026-04-10 — 로그 통합 및 출처 표출 방식 개선

### 변경 1: API access 로그 제거

**증상**
- uvicorn이 출력하는 `GET /parser/jobs HTTP/1.1 200 OK` 형태의 HTTP access 로그가 프로젝트 로그와 섞여 가독성 저하

**수정 내용**

1. **`parser/log_config.py` 신규 추가**
   - `ProjectFormatter`: 밀리초 포함 타임스탬프 포맷 (`YYYY-MM-DD HH:MM:SS.mmm --- 메시지`)
   - `setup_logging(suppress_access_log=True)`: `uvicorn.access` 로거 비활성화

2. **`api/main.py`** — `setup_logging(suppress_access_log=True)` 호출 추가

---

### 변경 2: 프로젝트 로그 포맷 통일

**기존 포맷**: `2026-04-10 10:45:12,101 [INFO] retriever.agents.orchestrator - [Orchestrator] Planner 호출`

**변경 포맷**: `2026-04-10 10:45:12.101 --- [DONE] format_converter`

**수정 내용**

1. **`retriever/main.py`** — 기존 `logging.basicConfig()` 제거, `setup_logging()` 호출로 대체

2. **`parser/workflow/__init__.py`** — `print(f"[JOB ] ...")` → `logger.info("[JOB] ...")`

3. **`parser/workflow/runner.py`** — 모든 `print()` → `logger` 호출로 전환

   | 이전 | 이후 |
   |------|------|
   | `print(f"[SKIP] {step_name} ...")` | `logger.info("[SKIP] %s", step_name)` |
   | `print(f"[RUN ] {step_name}")` | `logger.info("[RUN] %s", step_name)` |
   | `print(f"[DONE] {step_name}")` | `logger.info("[DONE] %s", step_name)` |
   | `print(f"[FAIL] {step_name}: {e}")` | `logger.error("[FAIL] %s: %s", step_name, e)` |
   | `print(f"[CANCELLED] ...")` | `logger.info("[CANCELLED] ...")` |

---

### 변경 3: 답변 출처 표출 방식 개선

**기존 방식의 문제**
- Aggregator LLM이 `sources` 문자열을 직접 생성 → 실제 문서와 다른 출처 생성 가능
- Writer LLM에 `## 출처` 생성 지시 → 답변 본문에 출처가 혼입됨

**개선 방식**
- LLM은 출처를 생성하지 않음
- 검색 단계에서 실제 블록 ID(`BlockRef: section_id, block_seq, page`)를 추적
- 답변 생성 후 DB에서 블록 메타데이터를 조회하여 출처를 후처리로 구성
- 콘텐츠와 출처를 API 응답에서 분리하여 반환

**출처 포맷**: `2023-annual-report.pdf, p.15 #2`

**수정 파일 및 내용**

| 파일 | 변경 내용 |
|------|-----------|
| `retriever/models.py` | `BlockRef` 데이터클래스 추가. `StructuredSection`에서 `sources` 필드 제거. `AggregatedContent`에 `block_refs` 추가. `WriterOutput.sources` → `block_refs`로 교체 |
| `retriever/agents/retriever.py` | `_fetch_section_block_refs()` 추가. `_expand_node_results()`에서 섹션 확장 시 `block_refs` 포함 |
| `retriever/agents/aggregator.py` | Aggregator LLM 프롬프트에서 `sources` 필드 제거. 검색 결과에서 `block_refs` 수집하여 `AggregatedContent`에 포함 |
| `retriever/agents/writer.py` | Writer LLM 프롬프트에서 `## 출처` 생성 지시 제거. `WriterOutput`에 `block_refs` 전달 |
| `retriever/agents/orchestrator.py` | `_resolve_block_refs()` 추가: `parser_sections` JOIN `parser_documents` 쿼리로 파일명 조회 후 `"filename.pdf, p.N #M"` 형식으로 변환. Supervisor PASS 시 후처리로 출처 구성 |

---

## 2026-04-10 — 문서 유형 분류 오류 및 TOC 없을 때 섹션 미분리 문제 수정

### 문제 1: 논문을 보고서로 오분류

**증상**
- 논문(PDF)을 입력하면 `classify_doc_type()`이 `"보고서 (행정/업무)"`를 반환

**원인 분석**

| # | 위치 | 원인 |
|---|------|------|
| 1 | `parser/index_parser/llm.py` `_load_doc_types()` | `"- 구조 (IMRAD 표준)"` 라인을 파싱할 때 `line.strip() == "- 구조"` 조건이 False라 `in_structure`가 True로 전환되지 않음. 결과적으로 논문의 structure가 빈 문자열로 저장됨 |
| 2 | `parser/index_parser/llm.py` | `DOC_TYPE_LIST`에 유형 이름과 대분류만 포함되어, 3B 소형 모델이 논문/보고서/연구보고서 등 유사 유형을 구분할 정보가 없음 |
| 3 | `parser/index_parser/llm.py` `classify_doc_type()` | 시스템 프롬프트에 혼동되기 쉬운 유형 간 식별 힌트가 없음 |
| 4 | `document_type.md` | 논문의 `특징` 항목이 "재현성 + 근거 중심" 한 줄뿐이라 보고서 등과 명확히 구별되지 않음 |

**수정 내용**

1. **파싱 버그 수정** (`parser/index_parser/llm.py` `_load_doc_types()`)
   - `line.strip() == "- 구조"` → `stripped.startswith("- 구조")` 로 변경
   - 동일하게 `"- 특징"`, `"- 포맷"` 도 `startswith()` 방식으로 통일
   - 이제 `"- 구조 (IMRAD 표준)"` 라인도 정상 파싱됨
   - `infer_toc_from_type("논문")` 이 빈 리스트 대신 `[Introduction, Methods, Results, Discussion]` 반환

2. **특징(features) 파싱 추가** (`parser/index_parser/llm.py` `_load_doc_types()`)
   - 기존: 구조(`- 구조`) 항목만 파싱
   - 변경: 특징(`- 특징`) 항목도 함께 파싱하여 `features` 필드로 저장

3. **DOC_TYPE_LIST 강화** (`parser/index_parser/llm.py`)
   - 기존: `"- 논문 (학술/연구 문서)"`
   - 변경: `"- 논문 (학술/연구 문서): 구조=[...], 특징=[...]"` 형태로 구조+특징 포함

4. **classify_doc_type 프롬프트 강화** (`parser/index_parser/llm.py`)
   - 시스템 프롬프트에 혼동 주의 힌트 추가:
     - 논문: Abstract/초록·References/참고문헌·IMRAD 구조 포함
     - 보고서(행정/업무): 기관 내부 보고용, Abstract·References 없음
     - 연구보고서: 연구기관·정부기관 발간, 연구책임자·과제번호 명시
     - 리포트(학술/분석): 학과 과제·실습 제출물

5. **document_type.md 특징 보강**
   - **논문(6-1)**: Abstract/초록 필수, References/참고문헌 필수, 학술지·학회 제출용, IMRAD 구조 명시
   - **보고서 행정/업무(1-2)**: 기관·부서 단위 작성, Abstract·References 없음(논문·연구보고서와 구별) 명시
   - **리포트 학술/분석(6-2)**: 학과 과제·실습·조사 결과 제출, 교수 대상, Abstract 없음, 연구기관 발간 아님 명시
   - **연구보고서(6-3)**: 연구기관·정부출연기관 발간, 연구책임자·과제번호 명시, 논문보다 실용적 명시

---

### 문제 2: TOC 없는 문서가 하나의 섹션으로만 분리됨

**증상**
- 목차(TOC)가 없는 문서(특히 논문 PDF)를 처리하면, 문서 유형별 구조(예: 개요→목적→현황→분석→결과→결론)를 기반으로 섹션을 나눠야 하는데, 전체 내용이 "문서 헤더" 또는 "전체" 1개 섹션으로 합쳐짐

**원인 분석**

| # | 위치 | 원인 |
|---|------|------|
| 1 | `parser/index_parser/llm.py` `_load_doc_types()` | 문제 1의 파싱 버그로 인해 논문의 structure가 비어 있어 `infer_toc_from_type("논문")`이 `[]`를 반환 → `_split_by_toc()`에 빈 toc가 전달되어 "전체" 1개 섹션으로 반환 |
| 2 | `parser/structurer/__init__.py` `_split_by_toc()` | `infer_toc_from_type()`으로 구조 기반 TOC(`["개요", "목적", ...]`)를 생성해도, `_split_by_toc()`는 블록 첫 줄과 TOC 제목을 **정확 매칭**하므로 항상 실패. 실제 문서 텍스트에 "개요", "목적" 같은 단어가 첫 줄에 나타날 가능성이 거의 없음 |
| 3 | `parser/structurer/__init__.py` `run()` | `index_result`의 `toc_found` 값을 읽지 않아, structurer가 TOC가 추론된 것인지 원문에서 발견된 것인지 알 수 없음 |

**수정 내용**

1. **`toc_found` 전파** (`parser/structurer/__init__.py` `run()`)
   - `toc_found = index_result.get("toc_found", False)` 를 읽어 `_split_by_toc()`에 전달

2. **`_split_by_toc()` 분기 추가** (`parser/structurer/__init__.py`)
   - `toc_found=True` (원문 목차 존재): 기존 텍스트 정확 매칭 로직 유지
   - `toc_found=False` (구조 추론 TOC): 텍스트 매칭 대신 아래 순서로 폴백
     1. `_split_by_heading_detection()` 시도
     2. 실패 시 `_split_proportionally()` 실행

3. **`_split_by_heading_detection()` 추가** (`parser/structurer/__init__.py`)
   - 블록 내 라인 단위로 헤딩 패턴을 정규식으로 감지
   - 감지 대상: 번호 붙은 절(`1. xxx`, `1.1. xxx`), 로마 숫자(`I. xxx`), 한국어 구조(`제1장`), 고정 키워드(`Abstract`, `Introduction`, `References`, `서론`, `결론`, `참고문헌` 등)
   - 헤딩 2개 이상 감지 시 섹션 분리, 미만이면 빈 리스트 반환(다음 폴백으로 이행)

4. **`_split_proportionally()` 추가** (`parser/structurer/__init__.py`)
   - 헤딩 감지 실패 시, 블록을 추론된 구조 섹션 수에 맞춰 균등 분배
   - 단일 섹션보다 의미 있는 섹션 분리를 보장하는 최후 폴백

---

## 2026-04-10 — References 섹션 내 참고문헌 항목이 개별 H1 섹션으로 분리되는 문제 수정

### 문제

**증상**
- References 섹션은 TOC에서 정상 생성되지만, 내부 번호 항목(1~73개)이 일부는 References 하위에 들어가고 나머지(예: 9번 이후)는 개별 H1 섹션으로 분리됨

**원인 분석**

| # | 위치 | 원인 |
|---|------|------|
| 1 | `parser/index_parser/llm.py` `extract_toc()` | VLM이 References 섹션 하위의 개별 참고문헌 항목(`1. Smith et al.`, `2. Jones et al.` 등)을 TOC children으로 잘못 인식. `max_tokens=2048` 제한으로 인해 항목 1~8은 References의 level-2 children으로, 항목 9~73은 토큰 공간 부족으로 level-1 최상위 TOC 항목으로 나열됨 |
| 2 | `parser/structurer/__init__.py` `_split_by_toc()` | `_flatten_toc()` 결과에 참고문헌 번호 항목이 포함되면 `toc_map`에 등록되어, 해당 블록과 매칭 시 별도 Section이 생성됨 |
| 3 | `parser/structurer/__init__.py` `_split_by_heading_detection()` | `_HEADING_RE`의 `\d+\.(?:\d+\.)*\s+\S` 패턴이 참고문헌 번호 항목(`1. Smith...`, `9. Adams...`)을 실제 섹션 헤딩과 동일하게 인식함. `toc_found=False`인 경우 모든 번호 항목이 개별 H1 섹션으로 분리됨 |

**수정 내용**

1. **`extract_toc()` 프롬프트 규칙 추가** (`parser/index_parser/llm.py`)
   - 규칙 6 추가: "References/참고문헌/Bibliography 섹션 자체는 TOC 항목으로 추출하되, 내부 개별 인용 항목(`1. Smith, J. et al.`, `[1] Author...` 형태)은 TOC 항목이 아니므로 절대 포함하지 마세요."
   - LLM이 참고문헌 목록 항목을 TOC에 포함시키는 것을 사전 차단

2. **`_split_by_toc()` TOC 필터링 추가** (`parser/structurer/__init__.py`)
   - `_flatten_toc()` 결과에서 `_BIBLIO_ITEM_RE` 패턴(`^\d+[\.\)]\s+\S|^\[\d+\]\s+\S`)과 일치하는 항목 제거
   - LLM 프롬프트가 실패하더라도 코드 레벨에서 참고문헌 항목이 섹션으로 생성되지 않도록 안전장치 역할

3. **`_split_by_heading_detection()` References 컨텍스트 추적 추가** (`parser/structurer/__init__.py`)
   - `_BIBLIO_SECTION_RE` 패턴으로 References/참고문헌/Bibliography 헤딩 감지
   - `_BIBLIO_ITEM_RE` 패턴으로 참고문헌 번호 항목 감지
   - References 헤딩 이후 등장하는 번호 항목(`\d+[\.\)]\s+\S`, `\[\d+\]\s+\S`)을 헤딩 이벤트에서 일반 라인 이벤트로 강등
   - 일반 헤딩(비번호형)이 나오면 bibliography 모드 해제

### 추가 수정 (동일 날짜)

**증상**: 위 수정 후에도 여전히 분리됨

**추가 원인**

| # | 위치 | 원인 |
|---|------|------|
| 1 | `parser/index_parser/llm.py` `extract_toc()` | `max_tokens=2048` 부족으로 TOC JSON 응답이 잘리면 `json.loads` 실패 → `{"toc_found": False, "toc": []}` 반환 → `infer_toc_from_type()` 경로로 폴백 → `_split_by_heading_detection()` 호출 |
| 2 | `parser/structurer/__init__.py` | `_BIBLIO_SECTION_RE`가 `^...$` 앵커 사용으로 "5. References" 같은 복합 헤딩에서 bibliography 모드 미진입 |

**추가 수정 내용**

1. **`extract_toc()` `max_tokens` 증가** (`parser/index_parser/llm.py`): 2048 → 4096. TOC JSON 잘림 방지
   - 참고: `extract_toc()`는 이미 `VLM_MODEL`(Qwen3-VL-32B-Instruct-AWQ) 사용 중. 모델 변경 불필요

2. **`_BIBLIO_SECTION_RE` 패턴 완화** (`parser/structurer/__init__.py`)
   - `^(?:References?)$` → `(?:References?|참고\s*문헌|Bibliography)` (앵커 제거)
   - "5. References", "References 목록" 등 복합 형태도 bibliography 모드 진입 가능

---

## 2026-04-10 — 이미지/표 설명 중국어 혼입 시 해당 호출만 재시도

### 문제

**증상**
- `describe_image()`, `describe_table()`, `extract_page_with_vlm()` 호출 결과에 가끔 중국어가 혼입됨
- `section_parser/llm.py`에는 중국어 검증+재시도 로직이 존재했으나, `document_parser/llm.py`에는 부재

**원인**
- VLM 모델(Qwen3-VL-32B) 및 LLM 모델(Qwen2.5-3B)이 한국어 지시에도 불구하고 중국어로 응답하는 경우 발생
- 문서 단위 재처리가 아닌 **해당 LLM 호출 단위**의 재시도 로직 필요

**수정 내용** (`parser/document_parser/llm.py`)

1. **`_has_chinese()` / `_CHINESE_RE` 추가**
   - `section_parser/llm.py`와 동일한 유니코드 범위(`\u4e00-\u9fff`, `\u3400-\u4dbf`) 사용

2. **`_chat_korean()` 래퍼 추가**
   - `_chat()` 결과에 중국어가 감지되면 최대 `_MAX_RETRIES=2`회 재시도
   - 재시도 시 마지막 user 메시지 앞에 "이전 답변에 중국어가 포함되었습니다. 반드시 한국어로만 답하세요." 힌트 추가
   - 멀티모달 메시지(이미지+텍스트)의 경우 `content` 리스트 내 `text` 파트에만 힌트 삽입
   - 모든 재시도 후에도 중국어가 남아 있으면 마지막 결과 그대로 반환 (빈 문자열보다 낫기 때문)

3. **`describe_image()` → `_chat_korean()` 교체**

4. **`describe_table()` → `_chat_korean()` 교체** + system 프롬프트에 한국어 명시 추가

5. **`extract_page_with_vlm()` → `_chat_korean()` 교체**
   - 주의: `ocr_image()`는 원문 텍스트 추출(중국어 문서도 존재 가능)이므로 `_chat()` 유지

---

## 2026-04-10 — References 항목 분리 문제 3차 수정 (arXiv ID 형식 대응)

### 문제

**증상**
```
H1: References        (1 block)
H1: 2405.21060. URL: https://doi.org/10.48550/arXiv.2405.21060.  (1 block)
H1: 2021. arXiv: 2104.04473 [cs.CL]. URL: ...                    (1 block)
```

**추가 원인 분석**

| # | 원인 |
|---|------|
| 1 | `_BIBLIO_ITEM_RE = ^\d+[\.\)]\s+\S` 패턴이 `2405.21060. URL:` 형식을 미매칭. `2405.`뒤에 공백이 없고 `21060`이 오기 때문 |
| 2 | 미매칭 시 `elif not _BIBLIO_ITEM_RE.match(stripped): in_bibliography = False` 로직이 발동 → **bibliography 모드가 조기 해제됨** |
| 3 | `_BIBLIO_SECTION_RE.match()` 는 문자열 시작부터만 매칭 → `"5. References"` 같은 복합 헤딩에서 bibliography 모드 미진입 (search()로 수정했으나 코드상 여전히 match() 사용) |

**수정 내용** (`parser/structurer/__init__.py`)

1. **`_BIBLIO_ITEM_RE` 패턴 수정**: `^\d+[\.\)]\s+\S` → `^\d+\.(?:\d+\.)*\s+\S`
   - `2405.21060. URL:` → `\d+`=2405, `\.`=`.`, `(?:\d+\.)*`=`21060.`, `\s+`=` `, `\S`=`U` → **매칭**
   - `2021. arXiv:` → `\d+`=2021, `\.`=`.`, `(?:\d+\.)*`=(없음), `\s+`=` `, `\S`=`a` → **매칭**

2. **`_BIBLIO_RESET_RE` 추가**: bibliography 모드를 해제하는 명시적 구조 헤딩만 정의
   - Abstract, Introduction, Conclusion, Appendix, 제N장, Chapter N 등
   - **이 패턴에 해당할 때만 모드 해제**, 그 외 모든 헤딩은 본문으로 강등

3. **`_split_by_heading_detection()` 로직 전면 수정**
   - 기존: `_BIBLIO_ITEM_RE` 미매칭 시 무조건 `in_bibliography=False` → **버그**
   - 변경: `in_bibliography=True` 상태에서는 `_BIBLIO_RESET_RE` 매칭 시만 모드 해제, 나머지는 전부 본문 강등
   - `_BIBLIO_SECTION_RE` 검사를 `.match()` → `.search()`로 변경하여 복합 헤딩 감지

4. **`_split_by_toc()` TOC 필터 개선**
   - 기존: `_BIBLIO_ITEM_RE.match()` 만 사용 → `\d+.xxx` 형 실제 섹션도 필터될 위험
   - 변경: URL/doi/arXiv 포함 여부(`_BIBLIO_TOC_URL_RE`) + `[\d+]` 형식만 제거 (보수적 필터)

5. **`_BIBLIO_TOC_URL_RE` 추가**: `arXiv:|doi\.org|https?://` 포함 TOC 항목 제거

---
