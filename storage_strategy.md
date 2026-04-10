# 문서 저장 전략

문서 유형별 저장 방식과 Schema 깊이를 정의한다.

---

## 저장 전략 분류

| 전략 | 설명 |
|---|---|
| **Schema + Embedding** | 구조 기반 청킹 + 임베딩 + 필드 추출. Schema 깊이에 따라 경량/풍부로 구분 |
| **Schema only** | 완전 정형 데이터. 필터/집계 기반 Structured Query |

---

## 1. Schema + Embedding (29개)

### 1-1. 경량 Schema (섹션 구조 기반 청킹용) — 6개

섹션 구조만 정의하여 청킹 기준으로 사용. 별도 필드 추출 없이 임베딩 중심 검색.

| 유형 | 청킹 기준 (섹션 구조) |
|---|---|
| 1-2. 보고서 | 개요 / 목적 / 현황 / 분석 / 결과 / 결론 |
| 3-2. 설계서 | 시스템 개요 / 아키텍처 / DB / 흐름도 / 인터페이스 |
| 3-4. 매뉴얼/가이드 | 개요 / 설치·설정 / 기능 설명 / 절차 / FAQ / 문제 해결 |
| 6-2. 리포트 | 문제 / 분석 / 해결 / 결론 |
| 10-1. 보도자료 | 헤드라인 / 부제 / 리드문 / 본문 / 문의처 |
| 10-2. 브로슈어/카탈로그 | 표지 / 제품·서비스 소개 / 사양 / 연락처 |

### 1-2. 풍부한 Schema (섹션 구조 + 필드 추출) — 23개

섹션 구조 기반 청킹 + 개별 필드를 구조화하여 추출. Embedding과 Structured Query 병행.

| 유형 | 추출 필드 |
|---|---|
| 1-1. 공문 | 수신, 참조, 발신, 첨부 |
| 1-3. 기안서 | 기안 목적, 기대 효과, 결재 라인 |
| 1-4. 결재문서 | 결재선, 승인/반려 상태, 이력 |
| 2-1. 제안서 | 비용, 일정 |
| 2-2. 사업계획서 | 시장 규모, 재무 계획 |
| 2-3. 회의록 | 회의 정보, 결정 사항, 액션 아이템 |
| 2-4. 이메일 | 발신자, 수신자, 날짜, 제목 |
| 3-1. API 명세서 | Endpoint, Method, Request, Response, Error |
| 3-3. 기술 스펙 | 요구 사항, 제약 조건, 기술 선택 |
| 4-1. 계약서 | 당사자, 목적, 조건, 책임, 기간 |
| 4-2. 약관 | 정의, 서비스 내용, 이용 조건, 책임 제한 |
| 4-3. NDA | 비밀 정보 정의, 보호 의무, 기간, 예외 |
| 5-2. 일지 | 날짜, 이슈 |
| 6-1. 논문 | 저자, 학회, 초록, 참고문헌 |
| 6-3. 연구보고서 | 연구 기간, 연구 기관, 활용 방안 |
| 7-1. 신청서 | 신청자 정보, 신청 항목, 사유, 첨부 |
| 7-2. 설문지 | 질문 구조, 응답 유형, 주관식 응답 |
| 8-1. 이력서 | 인적 사항, 학력, 경력, 자격 |
| 8-2. 채용공고 | 직무, 자격 요건, 우대 사항, 마감일 |
| 8-3. 인사평가서 | 평가 기간, 성과 목표, 달성도, 점수 |
| 9-2. 대시보드 리포트 | KPI, 수치 데이터, 기준 기간 |
| 9-3. 통계 보고서 | 조사 개요, 표본 설계, 결과표 |
| 10-3. 마케팅 기획서 | 타겟, 채널, 예산, KPI |

---

## 2. Schema only (5개)

완전 정형 데이터. 필드 기반 필터/집계 검색으로 충분하며, 임베딩은 불필요.

| 유형 | 추출 필드 | 검색 방식 |
|---|---|---|
| 5-1. 로그 | Timestamp, Level, Message, Context | 시간 범위 + 레벨 필터 |
| 5-3. 감사 기록 | 사용자, 행위, 시간, 변경 내용 | 사용자 + 행위 + 시간 필터 |
| 5-4. 히스토리 | 변경 전/후, 작성자, 시간, 이유 | 시간 + 작성자 필터 |
| 7-3. 체크리스트 | 점검 항목, 기준, 결과, 비고 | 항목 + 결과 필터 |
| 9-1. 스프레드시트 | 헤더, 데이터 행, 수식/집계 | 컬럼 필터 + 집계 쿼리 |

---

## 3. 스프레드시트 (XLSX / CSV) 파싱 구현

### 3-1. 파싱 파이프라인

```
XLSX / CSV
  │
  ▼
[1] 병합 셀 확장 (XLSX only)
  │  openpyxl로 merged_cells 순회
  │  → 상단 좌측 값을 병합 범위 전체에 fill
  │
  ▼
[2] 테이블 영역 탐지
  │  완전히 비어 있는 행/열을 경계로 독립 테이블 영역 분리
  │  → 시트 하나에 테이블이 여러 개일 때(사용자 친화적 파일) 대응
  │
  ▼
[3] 헤더 탐지
  │  상단 N행이 전부 문자열이고 수직 병합이 있으면 다중 헤더로 판정
  │  → 컬럼명 충돌 시 "부모.자식" 형태로 flat화
  │     예) Q1 예산 > 인건비 → "Q1 예산.인건비"
  │
  ▼
[4] JSON 구조화 → PostgreSQL (parser_tables) 저장
  │
  ▼
[5] 테이블 메타 직렬화 → LLM → Summary / Keywords 생성
     (section_parser 스킵, document_integrator에서 직접 처리)
```

### 3-2. PostgreSQL 스키마 (`parser_tables`)

```sql
CREATE TABLE parser_tables (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL,          -- parser_documents 참조
    sheet_name  TEXT,                   -- XLSX 시트명 / CSV는 파일명
    region      TEXT,                   -- 테이블 영역 "B2:F20" (XLSX only)
    header_depth INT DEFAULT 1,         -- 헤더 행 수 (다중 헤더 대응)
    headers     JSONB,                  -- 헤더 구조 (다중 헤더면 2차원 배열)
    rows        JSONB,                  -- [{컬럼명: 값, ...}, ...]
    row_count   INT,
    description TEXT,                   -- LLM 생성 테이블 설명 (OpenSearch 색인용)
    seq         INT,                    -- 시트 내 테이블 순서
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON parser_tables (document_id);
CREATE INDEX ON parser_tables USING GIN (rows);
```

**JSON 예시**

```json
{
  "sheet_name": "Q1",
  "region": "B2:F20",
  "header_depth": 2,
  "headers": [
    ["부서", "부서", "Q1 예산", "Q1 예산", "비고"],
    ["",     "",     "인건비",  "운영비",  ""   ]
  ],
  "rows": [
    {"부서": "개발팀",   "Q1 예산.인건비": 5000, "Q1 예산.운영비": 1200, "비고": ""},
    {"부서": "마케팅팀", "Q1 예산.인건비": 3000, "Q1 예산.운영비": 800,  "비고": "증액예정"}
  ]
}
```

### 3-3. Summary / Keywords 생성

섹션-명제 구조 없이 테이블 메타를 직렬화해 LLM에 전달한다.

```
[시트명 / 테이블 영역] 으로 "가상 섹션" 구성
propositions:
  - "컬럼: 부서, Q1 예산.인건비, Q1 예산.운영비, 비고"
  - "총 {N}개 행, {M}개 시트 포함"
  - 상위 5행 샘플 요약
keywords: 컬럼명 목록
```

`document_integrator._load_propositions()`에서 `parser_propositions`가 비어 있으면
`parser_tables`를 조회해 위 형태로 변환 후 `generate_summary_and_keywords()`에 전달.

### 3-4. Pipeline Step 처리

| Step | 처리 방식 |
|---|---|
| `format_converter` | 변환 없이 원본 경로 그대로 전달 |
| `index_parser` | LLM 호출 없이 즉시 반환. CSV: `doc_type="스프레드시트"`, TOC=`[데이터]`. XLSX: 시트명 → TOC 항목 |
| `structurer` | `_split_tabular()` 분기: table 블록 1개 = 섹션 1개. `parser_tables`에 JSON 저장 |
| `section_parser` | **스킵** (표 데이터는 명제 추출 대상 아님) |
| `document_integrator` | `parser_tables` 직렬화 → `generate_summary_and_keywords()` → OpenSearch 색인 |

### 3-5. 라이브러리

| 용도 | 라이브러리 |
|---|---|
| XLSX 파싱 / 병합 셀 | `openpyxl` |
| 데이터 정제 | `pandas` |
| CSV 인코딩 감지 | `chardet` |
