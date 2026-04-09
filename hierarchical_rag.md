# 계층적 RAG 검색 전략

도서관에서 책을 찾듯이, 도메인 → 문서 → 섹션/청크 순으로 범위를 좁혀가며 검색하는 다단계 검색 구조.
쿼리 복잡도에 따라 Track A(간단 검색) / Track B(심화 검색)로 분기하여 속도와 정확도를 모두 확보한다.

---

## 1. 인덱스 구조 (3-Layer)

```
[Layer 1] 서가 (도메인 분류)     → 어느 서가에서 찾을지
[Layer 2] 책 (문서)              → 어떤 책을 꺼낼지
[Layer 3] 챕터/페이지 (섹션/청크) → 어디를 펼칠지
```

### Layer 1 — 도메인 인덱스

검색 범위를 좁히는 필터. Vector Search가 아닌 Structured Query.

| 필드 | 타입 | 설명 |
|---|---|---|
| `domain_category` | string | 대분류 (산업/제조) |
| `document_count` | integer | 해당 도메인의 문서 수 |
| `domain_keywords` | list[string] | 도메인 대표 키워드 (domain_type.md 대분류 기준) |
| `domain_keyword_embedding` | vector | 도메인 키워드 임베딩 (사전 생성) |

### Layer 2 — 문서 인덱스

후보 문서 선정. 요약 임베딩 Vector Search + 키워드 매칭 병행.

| 필드 | 타입 | 설명 |
|---|---|---|
| `document_id` | string (UUID) | 문서 식별자 |
| `title` | string | 문서 제목 |
| `summary` | string | 문서 전체 요약 (LLM 생성) |
| `keywords` | list[string] | 문서 핵심 키워드 |
| `summary_embedding` | vector | 요약의 임베딩 벡터 |
| `metadata` | object | 공통 메타데이터 (metadata.md 기준) |

### Layer 3 — 섹션/청크 인덱스

최종 답변 생성에 사용할 컨텍스트 검색. Contextual Chunking 적용. 임베딩은 목차 레벨(section_summary_embedding)과 청크 레벨(proposition_embedding) 두 종류를 사용한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `chunk_id` | string (UUID) | 청크 식별자 |
| `document_id` | string (UUID) | 상위 문서 참조 |
| `parent_section_id` | string (UUID) | 상위 섹션 참조 (Auto-Merge용) |
| `section_name` | string | 소속 섹션명 |
| `section_depth` | integer | 섹션 깊이 (대분류=1, 중분류=2, 소분류=3) |
| `section_path` | string | 섹션 경로 (대분류 > 중분류 > 소분류) |
| `section_summary` | string | 섹션 요약 2~3문장 (LLM 생성) |
| `section_summary_embedding` | vector | 섹션 요약 임베딩 (Step 3-2 Index Search용) |
| `proposition` | string | LLM이 추출한 핵심 명제 (1문장) |
| `contextual_proposition` | string | 접두어 + Proposition (임베딩 원본) |
| `proposition_embedding` | vector | contextual_proposition의 임베딩 벡터 (Step 4 청크 검색용) |
| `content` | string | 원본 섹션 텍스트 (답변 생성용) |
| `token_count` | integer | 원본 텍스트 토큰 수 |
| `keywords` | list[string] | 섹션 키워드 |

---

## 2. Contextual Chunking

청크를 임베딩할 때 청크 텍스트만 단독으로 임베딩하지 않고, 문서 제목과 섹션 계층 정보를 접두어로 붙여 임베딩한다.
문맥이 잘린 청크의 의미를 보완하여 검색 정확도를 높인다.

### 적용 방식

```
-- 기존: 청크 텍스트만 임베딩
"이 공정에서는 온도를 150도로 유지한다" → embedding

-- 개선: 문서 제목 + 섹션 계층을 접두어로 붙여 임베딩
"[문서: 기능성 가공 실무 > 섹션: 섬유 가공 공정 > 열처리]
 이 공정에서는 온도를 150도로 유지한다" → embedding
```

### 효과

- 동일한 텍스트라도 소속 문서/섹션에 따라 다른 임베딩 생성
- 검색 시 "섬유 가공" 쿼리가 반도체 공정의 유사 문장과 혼동되지 않음
- 인덱싱 시 1회 처리, 검색 시 추가 비용 없음

---

## 3. 스코어 합산 방식 (RRF)

Hybrid Search(Step 3)에서 Vector Search와 Keyword Search의 결과를 합산할 때 Reciprocal Rank Fusion을 사용한다.
두 검색의 스코어 스케일이 다르기 때문에 단순 가중합 대신 순위 기반으로 합산한다.

### 산출 공식

```
RRF_score(d) = Σ  1 / (k + rank_i(d))
               i
```

- `rank_i(d)`: i번째 검색(Vector / Keyword)에서 문서 d의 순위
- `k`: 상수 (일반적으로 60)
- 순위가 높을수록(1에 가까울수록) 높은 점수

### 예시

| 문서 | Vector 순위 | Keyword 순위 | RRF 점수 |
|---|---|---|---|
| 문서 A | 1 | 3 | 1/61 + 1/63 = 0.0323 |
| 문서 B | 3 | 1 | 1/63 + 1/61 = 0.0323 |
| 문서 C | 2 | 2 | 1/62 + 1/62 = 0.0323 |
| 문서 D | 1 | 10 | 1/61 + 1/70 = 0.0307 |

Vector에서만 높거나 Keyword에서만 높은 문서보다, 양쪽 모두 적절한 순위를 가진 문서가 상위에 오게 된다.

---

## 4. 검색 플로우

### Step 0 — Query Rewriter Agent

사용자의 자연어 질의를 벡터 검색에 최적화된 명제 단위 쿼리로 변환한다.

```
query (원본) → rewritten_query (명제형)
```

| 변환 규칙 | 예시 |
|---|---|
| 질문형 → 서술형 명제 | "섬유 가공이 뭐야?" → "섬유 가공은 제품 품질 향상을 위한 처리 과정이다." |
| 구어체 → 명시적 키워드 포함 | "그 처리 방법 알려줘" → "산업 폐수 처리 방법 및 절차" |
| 이미 명확한 명제형 | 원문 그대로 사용 |

이후 모든 단계(임베딩, 도메인 분류, 검색)는 변환된 쿼리를 기준으로 수행. 응답에는 `original_query`와 `rewritten_query`를 모두 포함하여 UI에서 변환 내용을 확인할 수 있다.

### Step 1 — 쿼리 임베딩 생성

변환된 쿼리를 임베딩 벡터로 변환. 이후 전 단계에서 재사용.

```
rewritten_query → query_embedding (1회 생성)
```

### Step 2 — 쿼리 복잡도 판별 및 도메인 분류

쿼리 임베딩과 도메인 키워드 임베딩을 비교하여 복잡도를 판별하고, Track을 분기한다.

```
query_embedding <-> domain_keyword_embedding (전체 도메인 대상)
→ top-1 유사도 확인
```

| 조건 | Track | 처리 방식 |
|---|---|---|
| top-1 유사도 ≥ 임계값 | **Track A** | 임베딩 매칭 결과로 도메인 확정 |
| top-1 유사도 < 임계값 | **Track B** | LLM 쿼리 분석으로 도메인 판별 |

---

### Track A — 간단 검색 (단일 도메인, 명확한 쿼리)

```
예시: "섬유 가공 공정에 대해 알려줘"

[Step 2] 도메인 분류 (임베딩 매칭)
│  query_embedding <-> domain_keyword_embedding
│  → top-1: "산업/제조 > 산업기술·진흥" (유사도 0.87)
│  → 임계값 이상 → Track A 확정
│
[Step 3] 문서 검색 (Layer 1 + Layer 2 통합)
│  metadata filter: domain = "산업/제조 > 산업기술·진흥"
│  + summary_embedding <-> query_embedding (Vector Search)
│  + keywords MATCH (Keyword Search)
│  → RRF로 스코어 합산, Top-K 후보 선정 (넉넉히)
│
[Step 3-1] Re-ranking
│  Cross-Encoder로 (query, document_summary) 쌍을 정밀 비교
│  → 최종 Top-K 문서 확정
│
[Step 3-2] 섹션/목차 검색 (Index Search)
│  선정된 문서 내에서:
│  section_summary_embedding <-> query_embedding
│  → 관련 섹션 범위 확정 (section_depth 기준 필터링 가능)
│  → 이후 청크 검색 범위를 해당 섹션들로 제한
│
[Step 4] 청크 검색 (Layer 3)
│  Step 3-2에서 확정된 섹션 범위 내에서:
│  proposition_embedding <-> query_embedding
│  → Top-K 청크 추출 + Auto-Merge 적용
│
[Step 5] 답변 작성 (Writer Agent)
│  검색된 청크 + 섹션 요약 + 메타데이터 기반 답변 구성
│  → 다중 소스 합성 (출처 섹션 명시)
│  → 질문 유형에 따라 출력 형식 제어 (목록/표/단락)
│
[Step 6] 검증 (Validator Agent) — 필수
   LLM이 (답변, 청크 원문)을 비교하여 아래 기준 검증:
   → 근거 충실성: 주요 주장이 컨텍스트 원문에 기반하는가
   → 완전성: 질문의 모든 부분에 답변했는가
   → 일관성: 답변 내 상충하는 내용이 없는가
   기준 충족 → 답변 반환
   기준 미충족 → Step 4에서 Top-K 범위 확장 후 재생성 (최대 1회)
```

| 항목 | 값 |
|---|---|
| 총 단계 | 7단계 |
| LLM 호출 | 2회 (Writer Agent + Validator Agent) |
| Vector Search | 3회 (문서 + 섹션 + 청크) |
| Cross-Encoder | 1회 (문서 Re-ranking) |

---

### Track B — 심화 검색 (다중 도메인, 모호/복합 쿼리)

```
예시: "반도체 공정에서 발생하는 폐수 처리 규정"

[Step 2] 쿼리 분석 + 서브 쿼리 분해 (LLM)
│  LLM이 쿼리를 분석하여:
│  → 검색 의도: "반도체 제조 공정의 폐수 처리에 관한 법적 규정"
│  → 서브 쿼리 분해:
│     sub_query_1: "반도체 제조 공정 종류" → 도메인: 산업/제조
│     sub_query_2: "반도체 폐수 처리 방법" → 도메인: 환경/에너지
│     sub_query_3: "산업 폐수 배출 규정"  → 도메인: 법제/사법
│
[Step 2-1] 서브 쿼리 임베딩 생성
│  각 서브 쿼리를 개별 임베딩
│  sub_query_1 → sub_embedding_1
│  sub_query_2 → sub_embedding_2
│  sub_query_3 → sub_embedding_3
│
[Step 3] 문서 검색 (도메인별 병렬 Hybrid Search)
│  각 서브 쿼리를 해당 도메인에서 병렬 검색:
│  ① 산업/제조 범위 + sub_embedding_1 → 반도체 공정 문서
│  ② 환경/에너지 범위 + sub_embedding_2 → 폐수 처리 문서
│  ③ 법제/사법 범위 + sub_embedding_3 → 환경 규제 문서
│  → 각 도메인별 RRF 합산 후 Top-K 문서를 통합
│
[Step 3-1] Re-ranking
│  Cross-Encoder로 (original_query, document_summary) 쌍을 정밀 비교
│  → 통합 결과에서 최종 Top-K 문서 확정
│
[Step 3-2] 섹션/목차 검색 (Index Search)
│  각 서브 쿼리로 해당 문서 내 관련 섹션 검색:
│  sub_embedding_N <-> section_summary_embedding (도메인별 병렬 처리)
│  → 도메인별 관련 섹션 범위 확정
│  → 청크 검색 범위를 해당 섹션들로 제한
│
[Step 4] 청크 검색 (Layer 3)
│  Step 3-2에서 확정된 섹션 범위 내에서:
│  proposition_embedding <-> query_embedding
│  → Top-K 청크 추출 + Auto-Merge 적용
│
[Step 5] 답변 작성 (Writer Agent)
│  검색된 청크 + 섹션 요약 + 메타데이터 기반 답변 구성
│  → 다중 소스 합성 (출처 섹션 명시)
│  → 질문 유형에 따라 출력 형식 제어 (목록/표/단락)
│
[Step 6] 검증 (Validator Agent) — 필수
   LLM이 (답변, 청크 원문)을 비교하여 아래 기준 검증:
   → 근거 충실성: 주요 주장이 컨텍스트 원문에 기반하는가
   → 완전성: 질문의 모든 부분에 답변했는가
   → 일관성: 답변 내 상충하는 내용이 없는가
   기준 충족 → 답변 반환
   기준 미충족 → Step 4에서 Top-K 범위 확장 후 재생성 (최대 1회)
```

| 항목 | 값 |
|---|---|
| 총 단계 | 8단계 |
| LLM 호출 | 3회 (쿼리 분석 + Writer Agent + Validator Agent) |
| Vector Search | 2N+1회 (서브 쿼리 수 N × 섹션 검색 + 청크 1) |
| Cross-Encoder | 1회 (통합 문서 Re-ranking) |

---

## 5. Track 비교

| 항목 | Track A (간단) | Track B (심화) |
|---|---|---|
| 분기 조건 | 도메인 유사도 ≥ 임계값 | 도메인 유사도 < 임계값 |
| 도메인 분류 | 임베딩 매칭 | LLM 분석 |
| 쿼리 처리 | 명제형 변환 후 단일 쿼리 | 명제형 변환 후 서브 쿼리로 분해 |
| 도메인 범위 | 단일 | 다중 (병렬 검색) |
| Re-ranking | 단일 도메인 결과 | 다중 도메인 통합 결과 |
| 섹션 검색 (Index Search) | 단일 쿼리로 섹션 범위 확정 | 서브 쿼리별 병렬 섹션 검색 |
| Writer Agent | 단일 소스 합성 | 다중 소스 합성 |
| Validator Agent | 필수 (최대 1회 재생성) | 필수 (최대 1회 재생성) |
| LLM 호출 | 2회 | 3회 |
| 속도 | 빠름 | 상대적 느림 |
| 정확도 | 명확한 쿼리에 높음 | 모호/복합 쿼리에 높음 |
| 예상 비율 | ~70% | ~30% |

---

## 6. 핵심 기술 요소

| 요소 | 설명 | 적용 위치 |
|---|---|---|
| **Hybrid Search** | Vector Search + Keyword Search 병행 | Step 3 |
| **RRF (Reciprocal Rank Fusion)** | 순위 기반 스코어 합산으로 Hybrid Search 결과 통합 | Step 3 |
| **Metadata Filtering** | 도메인 기반 사전 필터링과 Vector Search를 단일 쿼리로 통합 | Step 3 |
| **Contextual Chunking** | 청크 임베딩 시 문서 제목 + 섹션 계층을 접두어로 포함 | 인덱싱 단계 |
| **Re-ranking** | Cross-Encoder로 초기 검색 결과를 정밀 재정렬 | Step 3-1 |
| **Query Rewriting** | 구어체·질문형 쿼리를 명제형으로 변환하여 벡터 검색 정확도 향상 | Step 0 |
| **Query Decomposition** | 복합 쿼리를 도메인별 서브 쿼리로 분해 | Track B Step 2 |
| **Index Search** | 문서 확정 후 섹션 요약 임베딩으로 관련 섹션 범위를 좁혀 청크 검색 정밀도 향상 | Step 3-2 |
| **Multi-level Summary** | 문서 요약(Layer 2) + 섹션 요약(Layer 3)을 각각 임베딩하여 계층별 검색 | Layer 2, 3 |
| **Parent-Child Retrieval** | 청크 검색 시 상위 문서/섹션 정보를 함께 참조 | Step 4, 5 |
| **Adaptive Routing** | 쿼리 복잡도에 따라 Track A/B 자동 분기 | Step 2 |
| **Writer Agent** | 다중 소스 합성, 출처 섹션 명시, 출력 형식 제어 (목록/표/단락) | Step 5 |
| **Validator Agent** | 근거 충실성·완전성·일관성 3가지 기준으로 답변 검증 (필수, 최대 1회 재생성) | Step 6 |

---

## 7. 검색 실패 시 Fallback

| 단계 | 실패 상황 | Fallback |
|---|---|---|
| Step 2 (Track A) | 도메인 유사도 전체 임계값 미달 | Track B로 전환 |
| Step 3 | Top-K 문서의 유사도 임계값 미달 | 도메인 필터 해제 후 전체 범위 Vector Search |
| Step 3-1 | Re-ranking 후 스코어 임계값 미달 | Top-K 범위 확장 후 재검색 |
| Step 3-2 | 섹션 유사도 임계값 미달 (관련 섹션 없음) | 섹션 필터 해제 후 문서 전체 범위에서 청크 검색 |
| Step 4 | 청크 유사도 임계값 미달 | 섹션 요약(section_summary)을 컨텍스트로 직접 사용 |
| Step 5 | 답변 작성 불가 (컨텍스트 부족) | "관련 문서를 찾지 못했습니다" + 유사 문서 목록 제시 |
| Step 6 | Validator 기준 미충족 | Step 4에서 Top-K 범위 확장 후 재생성 (최대 1회) |
