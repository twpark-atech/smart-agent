# opensearch_search

OpenSearch에서 Vector Search(KNN)와 BM25 Search를 병렬로 실행하고 결과를 병합하는 Hybrid Search Tool.

## 사용 Agent
- Retriever

## 검색 대상 인덱스

| 인덱스 유형 | 검색 대상 | Vector 필드 | Keyword 필드 |
|------------|----------|-------------|-------------|
| document | 문서 요약 (Summary) | `summary_embedding` | `keywords` |
| node | 명제 (Proposition) | `proposition_embedding` | `keywords` |

- 임베딩 모델: Qwen/Qwen3-Embedding-0.6B (Dimension: 1024)
- 임베딩 서버: http://112.163.62.170:8032/v1

## 입력 파라미터

```python
{
    "index_type": str,              # "document" | "node"
    "embedding_query": str,         # Vector 검색에 사용할 쿼리 (임베딩 후 KNN 검색)
    "keyword_query": str,           # BM25 검색에 사용할 쿼리
    "domain_filter": str | None,    # 도메인 필터 (domain_type.md 기준, None이면 전체)
    "document_filter": list[str] | None,  # 문서 ID 필터 (node 검색 시 상위 문서 범위 제한)
    "top_k": int                    # 반환할 최대 결과 수 (기본값: 10)
}
```

## 처리 흐름

```
1. embedding_query → 임베딩 서버 호출 → 1024차원 벡터 생성
2. KNN Search (vector) + BM25 Search (keyword) 병렬 실행
   - domain_filter / document_filter 적용
3. 두 결과 병합 (RRF: Reciprocal Rank Fusion)
4. top_k 개 반환
```

## 출력 형식

```python
[
    {
        "id": str,              # OpenSearch 문서 ID
        "score": float,         # 병합 점수
        "domain": str,          # 도메인
        "document_id": str,     # 원본 문서 ID
        "document_name": str,   # 원본 문서명
        "section": str | None,  # 섹션명 (node 인덱스만 해당)
        "content": str,         # 요약(document) 또는 명제(node) 내용
        "keywords": list[str],  # 키워드 목록
        "source": {
            "file_name": str,
            "page": int | None,
            "section_path": str | None  # 예: "1장 > 1.2 > 1.2.1"
        }
    }
]
```

## 참고사항
- document 인덱스 검색 결과를 기반으로 node 검색 시 `document_filter`에 document_id 목록을 전달하여 범위 제한
- 결과가 0건인 경우 빈 리스트 반환 (예외 발생 없음)
- top_k는 각 검색(KNN, BM25) 단계에서 각각 적용되며, 병합 후 다시 top_k로 절삭
