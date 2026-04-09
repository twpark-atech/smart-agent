# Document Parser

문서 파일(PDF, DOCX, HWPX, PPTX 등)을 파싱하여 텍스트·이미지·테이블을 추출하고, Vector DB(OpenSearch) / RDB(PostgreSQL) / Object Storage(MinIO)에 적재하는 파이프라인.

## 파이프라인 구조

문서 처리는 아래 5단계 step이 순차 실행됩니다. 중단 후 재실행 시 완료된 step은 자동으로 건너뜁니다.

| 순서 | Step 이름 | 설명 |
|------|-----------|------|
| 1 | `format_converter` | 파일 포맷 감지 및 공통 포맷으로 변환, MinIO 업로드 |
| 2 | `index_parser` | 문서 목차(제목/계층) 추출 |
| 3 | `structurer` | 섹션별 블록(텍스트·이미지·테이블) 분류 후 PostgreSQL 적재 |
| 4 | `section_parser` | 섹션 내 명제 추출 및 OpenSearch 임베딩 적재 |
| 5 | `document_integrator` | 전체 문서 통합 메타 생성 |

## PostgreSQL 적재 구조

`structurer` step 완료 시 아래 테이블에 데이터가 적재됩니다.

| 테이블 | 설명 |
|--------|------|
| `parser_documents` | 문서 메타데이터 (파일 경로, 유형, 도메인, MinIO 키) |
| `parser_sections` | 섹션 목록 (제목, 계층, 순서) |
| `parser_blocks` | 섹션별 블록 (텍스트 / 이미지 / 테이블 raw) |
| `parser_propositions` | 섹션별 명제 및 키워드 |
| `parser_tables` | 표 메타데이터 (헤더 목록, 행 수, 문서 내 순서) |
| `parser_table_rows` | 표 행 단위 데이터 (정규화 적재) |

### 표 정규화 적재

`block_type = 'table'`인 블록은 `parser_blocks.table_json`에 raw JSON으로 저장되는 동시에, 행 단위로 분리되어 `parser_tables` / `parser_table_rows`에 정규화 적재됩니다.

```
parser_tables
  └─ id, document_id, block_id, section_id, page
     headers: ["컬럼1", "컬럼2", ...]
     row_count, table_index

parser_table_rows
  └─ id, table_id, row_index
     row_data: {"컬럼1": "값1", "컬럼2": "값2", ...}
```

**조회 예시**

```sql
-- 특정 문서의 표 목록
SELECT table_index, headers, row_count
FROM parser_tables
WHERE document_id = '<job_id>'
ORDER BY table_index;

-- 특정 표의 모든 행
SELECT row_index, row_data
FROM parser_table_rows
WHERE table_id = 1
ORDER BY row_index;

-- JSONB 필드로 특정 컬럼 값 조회
SELECT row_data->>'금액' AS 금액
FROM parser_table_rows
WHERE table_id = 1;
```

## 사전 요구사항

### 인프라 실행

```bash
cd parser
docker compose up -d
```

OpenSearch(9200), PostgreSQL(5432), MinIO(9000/9001) 컨테이너가 실행됩니다.

### 의존성 설치

```bash
pip install -r requirements.txt
# 또는 uv 사용 시
uv sync
```

### 환경변수 (선택)

기본값이 설정되어 있으므로 로컬 개발 환경에서는 별도 설정 불필요. 변경이 필요할 경우 환경변수로 오버라이드합니다.

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `LLM_URL` | `http://112.163.62.170:8012/v1` | LLM API 엔드포인트 |
| `LLM_MODEL` | `Qwen2.5-3B-Instruct` | LLM 모델명 |
| `VLM_MODEL` | `Qwen3-VL-32B-Instruct-AWQ` | VLM(이미지) 모델명 |
| `OCR_MODEL` | `DeepSeek-OCR` | OCR 모델명 |
| `EMBEDDING_URL` | `http://112.163.62.170:8032/v1` | 임베딩 API 엔드포인트 |
| `OPENSEARCH_HOST` | `localhost` | OpenSearch 호스트 |
| `POSTGRES_HOST` | `localhost` | PostgreSQL 호스트 |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO 엔드포인트 |

## 사용법

### run — 워크플로우 실행

```bash
python main.py run <파일경로>
```

```bash
python main.py run ./sample.hwpx
python main.py run ./report.pdf
```

- 최초 실행 시 모든 step을 순차 실행합니다.
- 중단 후 재실행 시 이미 완료된 step은 DB 결과를 재사용하고 건너뜁니다.
- 완료 시 전체 context를 JSON으로 출력합니다.

### status — 진행 상태 조회

```bash
python main.py status <파일경로>
python main.py status <파일경로> --step <step이름>
```

```bash
# 전체 step 요약 출력
python main.py status ./report.pdf

# 특정 step의 상세 결과만 출력
python main.py status ./report.pdf --step index_parser
python main.py status ./report.pdf --step structurer
```

### inspect — 섹션/블록 내용 조회

```bash
python main.py inspect <파일경로>
python main.py inspect <파일경로> --section <섹션번호 또는 제목>
```

```bash
# 섹션 목록 출력 (seq, 계층, 블록수, 제목)
python main.py inspect ./report.pdf

# seq 번호로 특정 섹션의 블록·명제 출력
python main.py inspect ./report.pdf --section 3

# 제목 키워드로 검색
python main.py inspect ./report.pdf --section "서론"
```

### reset — 특정 step 초기화

```bash
python main.py reset <파일경로> <step이름>
```

```bash
python main.py reset ./report.pdf index_parser
```

- 지정한 step의 완료 상태를 초기화합니다.
- 다음 `run` 실행 시 해당 step부터 재실행됩니다.
- step 이름: `format_converter`, `index_parser`, `structurer`, `section_parser`, `document_integrator`

## 주요 변경 이력

### 문서 삭제 / 추출 중단

`workflow/job_store.py`에 다음 함수가 추가됐습니다.

| 함수 | 설명 |
|------|------|
| `cancel_job(job_id)` | job 상태를 `cancelled`로 변경 |
| `is_cancelled(job_id)` | 취소 여부 조회 |
| `delete_job(job_id)` | `parser_job_steps`, `parser_jobs` 삭제 |

`db/__init__.py`에 `delete_document(document_id)`가 추가됐습니다 (FK 순서 준수).

`document_integrator/opensearch.py`에 `delete_by_document(document_id)`가 추가됐습니다.

**중단 흐름:** `cancel_job()` 호출 → `runner.py`가 각 step 시작 전 `is_cancelled()` 체크 → `JobCancelledError` 발생 → 워크플로우 종료. 완료된 step 결과는 보존됩니다.

### 영어 PDF 목차 파싱 개선

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| TOC 헤딩 정규식 | `^#+\s*목\s*차` | `^#+\s*(목\s*차\|table\s+of\s+contents\|contents\|toc)` (대소문자 무시) |
| LLM 프롬프트 | 한국어 예시만 | 영어 예시 추가 (`Chapter 1`, `Appendix A` 등) |
| TOC 추출 모델 | `Qwen2.5-3B-Instruct` | `Qwen3-VL-32B-Instruct-AWQ` (대형 모델) |
| 토큰 계산 | 한글 기준 고정 | 언어 비율 감지 후 분기 (영어: 단어 수 × 1.3) |

### OpenSearch Korean Analyzer 개선 (한국어 + 영어 혼합 지원)

`section_parser/opensearch.py`, `document_integrator/opensearch.py` 의 `korean` analyzer 필터 파이프라인 변경.

| 필터 | 역할 |
|------|------|
| `nori_tokenizer` | 한글 형태소 분리 / 영어 ASCII 연속열 토큰화 |
| `nori_part_of_speech` | 조사·어미 제거 ("검색을"/"검색이" → "검색") |
| `lowercase` | 영어 대소문자 통일 |
| `english_stemmer` | 영어 어간 추출 ("searching"/"searched" → "search") |

**변경 전**: `nori_tokenizer` + `lowercase` 만 사용 → 조사/어미 붙은 채 인덱싱, 영어 어간 추출 없음

**변경 후**: `nori_part_of_speech` + `english_stemmer` 추가 → 한국어·영어 어형 변화 무관 BM25 매칭

> 인덱스가 이미 존재할 경우 삭제 후 재생성 필요 (analyzer 설정은 인덱스 생성 시에만 적용).