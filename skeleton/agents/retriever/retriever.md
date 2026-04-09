# 역할
Planner가 수립한 실행 계획(Task Queue)을 기반으로 검색을 수행하는 Agent에 대한 코드를 작성한다.

## Retriever의 역할
- 도메인 검색
- 문서 검색
- 노드 검색
- 정량적 자료 검색
- 이미지 검색
- 웹 검색

## 검색 유형별 동작

### 계층적 검색 (document 유형)
도메인 검색 → 문서 검색 → 노드 검색 순으로 단계적 실행

1. **도메인 검색**: Task Queue의 `domain_query`를 기반으로 domain_type.md에 정의된 도메인 중 해당 도메인 필터링
2. **문서 검색**: 검색된 도메인 내에서 `embedding_query`로 Summary Vector Search + `keyword_query`로 BM25 Search 병렬 실행 후 결과 병합
3. **노드 검색**: 검색된 문서 내에서 `embedding_query`로 Proposition Vector Search + `keyword_query`로 BM25 Search 병렬 실행 후 결과 병합

### 웹 검색 (web 유형)
- Task Queue의 `task_type`이 "web" 또는 "mixed"인 경우에만 실행
- Planner가 판단한 유형을 따르며, Retriever가 자체적으로 웹 검색 여부를 결정하지 않음
- `embedding_query` 또는 `keyword_query`를 웹 검색 쿼리로 사용

### 정량적 검색 (quantitative 유형)
- Task Queue의 `sql_query`를 기반으로 Text-to-SQL 실행
- PostgreSQL에서 수치/통계/집계 자료 검색

### 이미지 검색
- 이미지 유사도 기반 검색
- VLM(Qwen3-VL-32B)으로 생성된 Image Embedding 활용

## 참고사항
- 모든 검색 결과에는 반드시 출처(source) 정보를 포함
- 계층적 검색 각 단계는 Task Queue의 step별 status를 업데이트하며 진행
- mixed 유형의 경우 document 검색과 web 검색을 병렬 실행
- Retriever는 검색 실행만 담당하며, 결과 품질 판단은 Aggregator가 수행
