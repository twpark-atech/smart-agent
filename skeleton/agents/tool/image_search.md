# image_search

VLM(Qwen3-VL-32B)이 생성한 Image Embedding을 기반으로 OpenSearch에서 유사 이미지를 검색하는 Tool.

## 사용 Agent
- Retriever

## 사전 조건
- 문서 파싱 단계에서 이미지는 VLM으로 임베딩되어 OpenSearch image 인덱스에 적재된 상태
- 이미지 원본은 MinIO에 저장되며, OpenSearch에는 MinIO 경로가 기록됨

## 입력 파라미터

```python
{
    "query_type": str,              # "text" | "image"
    "text_query": str | None,       # query_type이 "text"인 경우: VLM으로 임베딩할 텍스트 쿼리
    "image_path": str | None,       # query_type이 "image"인 경우: MinIO 이미지 경로 또는 로컬 경로
    "domain_filter": str | None,    # 도메인 필터 (None이면 전체)
    "document_filter": list[str] | None,  # 문서 ID 필터
    "top_k": int                    # 반환할 최대 결과 수 (기본값: 5)
}
```

## 처리 흐름

```
1. query_type에 따라 쿼리 임베딩
   - "text": 텍스트 쿼리를 VLM(Qwen3-VL-32B)으로 임베딩
   - "image": 이미지를 VLM(Qwen3-VL-32B)으로 임베딩
2. OpenSearch image 인덱스에서 KNN 검색
   - domain_filter / document_filter 적용
3. top_k 개 반환
```

## 출력 형식

```python
[
    {
        "id": str,              # OpenSearch 문서 ID
        "score": float,         # 유사도 점수
        "domain": str,          # 도메인
        "document_id": str,     # 원본 문서 ID
        "document_name": str,   # 원본 문서명
        "image_description": str,  # VLM이 생성한 이미지 설명
        "minio_path": str,      # MinIO 이미지 원본 경로
        "source": {
            "file_name": str,
            "page": int | None,
            "section_path": str | None
        }
    }
]
```

## 참고사항
- 이미지 임베딩은 VLM(Qwen3-VL-32B-Instruct-AWQ) 서버를 사용하며, 텍스트 임베딩 서버(Qwen3-Embedding-0.6B)와 별개
- 결과가 0건인 경우 빈 리스트 반환
- 이미지 원본 접근이 필요한 경우 minio_path를 통해 MinIO에서 직접 조회
