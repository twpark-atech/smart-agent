# 문서 메타데이터 스키마

문서 파서 Agent가 모든 문서에 공통으로 부여하는 메타데이터 정의.
문서 유형별 본문 추출 데이터(예산, 결재선, 계약 당사자 등)는 별도의 파싱 스키마에서 정의한다.

---

## 1. 식별 정보

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `document_id` | string (UUID) | Y | 문서 고유 식별자 (시스템 자동 생성) |
| `title` | string | Y | 문서 제목 |

## 2. 분류 정보

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `document_category` | string | Y | 문서 대분류 (document_type.md 1~10) |
| `document_type` | string | Y | 문서 소분류 (document_type.md 1-1, 2-3 등) |
| `domain_category` | string | Y | 도메인 대분류 (domain_type.md 1~10) |
| `confidence_score` | float (0.0~1.0) | Y | Agent 자동 분류 신뢰도 |

## 3. 문서 속성

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `source_format` | string | Y | 원본 파일 포맷 (PDF, DOCX, HWP, HTML, CSV 등) |
| `language` | string | Y | 문서 언어 (ko, en, ja, mixed 등) |
| `page_count` | integer | N | 페이지 수 / 분량 |

## 4. 작성 정보

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `author` | string | N | 작성자 / 발신자 |
| `organization` | string | N | 소속 기관 / 조직 |
| `created_date` | string (ISO 8601) | N | 문서 작성일 |

## 5. 요약 정보

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `summary` | string | Y | 문서 요약 (LLM 기반 자동 생성) |
| `keywords` | list[string] | Y | 핵심 키워드 (도메인 분류 및 검색용) |

## 6. 파싱 정보

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `parsed_at` | string (ISO 8601) | Y | 파싱 수행 시각 (시스템 자동 생성) |
| `parser_version` | string | Y | 파서 버전 (재파싱 추적용) |
