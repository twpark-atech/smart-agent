"""이미지 검색 Tool - VLM 임베딩 기반 OpenSearch KNN 검색"""
from __future__ import annotations

import sys
from pathlib import Path

from opensearchpy import OpenSearch
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    OPENSEARCH_HOST, OPENSEARCH_PORT, OPENSEARCH_SSL, OPENSEARCH_VERIFY_CERTS,
    OPENSEARCH_USER, OPENSEARCH_PASSWORD,
    LLM_URL, LLM_API_KEY, VLM_MODEL,
    EMBEDDING_DIM,
)

# 이미지 설명(description)이 적재된 인덱스
# parser_propositions에서 block_type=image 기반으로 생성된 레코드 검색
INDEX_PROPOSITION = "parser_propositions"

_os_client: OpenSearch | None = None
_vlm_client: OpenAI | None = None


def _get_os() -> OpenSearch:
    global _os_client
    if _os_client is None:
        _os_client = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_compress=True,
            use_ssl=OPENSEARCH_SSL,
            verify_certs=OPENSEARCH_VERIFY_CERTS,
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD) if OPENSEARCH_USER else None,
        )
    return _os_client


def _get_vlm() -> OpenAI:
    global _vlm_client
    if _vlm_client is None:
        _vlm_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)
    return _vlm_client


def _embed_text_with_vlm(text: str) -> list[float]:
    """VLM을 통해 텍스트를 임베딩 벡터로 변환.

    Note: VLM(Qwen3-VL-32B)이 임베딩 API를 지원하지 않는 경우,
    이미지 설명 텍스트에 대한 BM25 검색으로 폴백됩니다.
    """
    try:
        resp = _get_vlm().embeddings.create(model=VLM_MODEL, input=[text])
        return resp.data[0].embedding
    except Exception:
        return []


def _knn_search(vector: list[float], filters: list[dict], top_k: int) -> list[dict]:
    query: dict = {
        "size": top_k,
        "query": {
            "knn": {
                "embedding": {
                    "vector": vector,
                    "k": top_k,
                }
            }
        },
    }
    if filters:
        query["post_filter"] = {"bool": {"filter": filters}}
    resp = _get_os().search(index=INDEX_PROPOSITION, body=query)
    return resp["hits"]["hits"]


def _bm25_search(text_query: str, filters: list[dict], top_k: int) -> list[dict]:
    """VLM 임베딩 실패 시 텍스트 기반 폴백 검색."""
    query: dict = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [{"match": {"proposition": text_query}}],
                "filter": filters,
            }
        },
    }
    resp = _get_os().search(index=INDEX_PROPOSITION, body=query)
    return resp["hits"]["hits"]


def _build_filters(domain_filter: str | None, document_filter: list[str] | None) -> list[dict]:
    filters = []
    if domain_filter:
        filters.append({"term": {"domain_category": domain_filter}})
    if document_filter:
        filters.append({"terms": {"document_id": document_filter}})
    return filters


def _hit_to_result(hit: dict) -> dict:
    src = hit.get("_source", {})
    return {
        "id": hit["_id"],
        "score": hit.get("_score", 0.0),
        "domain": src.get("domain_category", ""),
        "document_id": src.get("document_id", ""),
        "document_name": src.get("source_path", ""),
        "image_description": src.get("proposition", ""),
        "minio_path": src.get("minio_key", ""),
        "keywords": src.get("keywords") or [],
        "content": src.get("proposition", ""),
        "source_type": "image",
        "source": {
            "file_name": src.get("source_path", ""),
            "page": src.get("page"),
            "section_path": src.get("section_path"),
            "minio_path": src.get("minio_key", ""),
        },
    }


def search(
    query_type: str,
    text_query: str | None = None,
    image_path: str | None = None,
    domain_filter: str | None = None,
    document_filter: list[str] | None = None,
    top_k: int = 5,
) -> list[dict]:
    """이미지 유사도 검색.

    Args:
        query_type: "text" | "image"
        text_query: query_type="text"일 때 VLM으로 임베딩할 텍스트 쿼리
        image_path: query_type="image"일 때 이미지 경로 (현재 미구현 - text 폴백)
        domain_filter: 도메인 필터
        document_filter: 문서 ID 필터
        top_k: 반환 결과 수

    Returns:
        이미지 검색 결과 목록
    """
    filters = _build_filters(domain_filter, document_filter)
    query_text = text_query or ""

    if query_type == "image" and image_path:
        # TODO: 이미지 파일을 VLM으로 임베딩하는 기능은 별도 구현 필요
        # 현재는 image_path를 텍스트 쿼리로 대체
        query_text = f"이미지: {Path(image_path).name}"

    vector = _embed_text_with_vlm(query_text) if query_text else []

    if vector and len(vector) == EMBEDDING_DIM:
        hits = _knn_search(vector, filters, top_k)
    else:
        # VLM 임베딩 실패 또는 차원 불일치 → BM25 폴백
        hits = _bm25_search(query_text, filters, top_k)

    return [_hit_to_result(hit) for hit in hits]
