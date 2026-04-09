"""OpenSearch Hybrid Search Tool (KNN + BM25 → RRF 병합)"""
from __future__ import annotations

import sys
from pathlib import Path

from opensearchpy import OpenSearch
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    OPENSEARCH_HOST, OPENSEARCH_PORT,
    EMBEDDING_URL, EMBEDDING_MODEL, EMBEDDING_DIM,
)

# ── 인덱스 이름 (parser 워크플로우와 공유) ───────────────────────────
INDEX_DOCUMENT     = "parser_documents"    # 문서 레벨: summary embedding + keywords
INDEX_PROPOSITION  = "parser_propositions" # 노드 레벨: proposition embedding + keywords

_os_client: OpenSearch | None = None
_emb_client: OpenAI | None = None

RRF_K = 60  # Reciprocal Rank Fusion 상수


def _get_os() -> OpenSearch:
    global _os_client
    if _os_client is None:
        _os_client = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_compress=True,
        )
    return _os_client


def _get_emb() -> OpenAI:
    global _emb_client
    if _emb_client is None:
        _emb_client = OpenAI(base_url=EMBEDDING_URL, api_key="na")
    return _emb_client


def _embed(text: str) -> list[float]:
    resp = _get_emb().embeddings.create(model=EMBEDDING_MODEL, input=[text])
    return resp.data[0].embedding


def _build_filter(domain_filter: str | None, document_filter: list[str] | None) -> list[dict]:
    filters = []
    if domain_filter:
        filters.append({"term": {"domain_category": domain_filter}})
    if document_filter:
        filters.append({"terms": {"document_id": document_filter}})
    return filters


def _knn_search(
    index: str,
    vector: list[float],
    filters: list[dict],
    top_k: int,
) -> list[dict]:
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
        "_source": True,
    }
    if filters:
        query["post_filter"] = {"bool": {"filter": filters}}

    resp = _get_os().search(index=index, body=query)
    return resp["hits"]["hits"]


def _bm25_search(
    index: str,
    keyword_query: str,
    content_field: str,
    filters: list[dict],
    top_k: int,
) -> list[dict]:
    # content_field / keywords 는 nori(한국어) analyzer, 나머지는 standard(영문 경로)
    # keywords 필드가 text 타입으로 변경되어 형태소 단위 BM25 매칭 가능
    must_clauses: list[dict] = [
        {"multi_match": {
            "query": keyword_query,
            "fields": [content_field, "keywords^2", "section_path", "doc_name"],
            # keywords 에 가중치(^2): 핵심 키워드 필드가 content보다 정밀 매칭에 유리
        }}
    ]
    query: dict = {
        "size": top_k,
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filters,
            }
        },
        "_source": True,
    }
    resp = _get_os().search(index=index, body=query)
    return resp["hits"]["hits"]


def _rrf_merge(
    knn_hits: list[dict],
    bm25_hits: list[dict],
    top_k: int,
) -> list[dict]:
    """Reciprocal Rank Fusion으로 두 결과 목록 병합."""
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, hit in enumerate(knn_hits):
        doc_id = hit["_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        docs[doc_id] = hit

    for rank, hit in enumerate(bm25_hits):
        doc_id = hit["_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        docs[doc_id] = hit

    ranked = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    result = []
    for doc_id in ranked[:top_k]:
        hit = docs[doc_id]
        hit["_rrf_score"] = scores[doc_id]
        result.append(hit)
    return result


def _hit_to_result(hit: dict, source_type: str) -> dict:
    src = hit.get("_source", {})
    return {
        "id": hit["_id"],
        "score": hit.get("_rrf_score", hit.get("_score", 0.0)),
        "domain": src.get("domain_category", ""),
        "document_id": src.get("document_id", ""),
        "document_name": src.get("source_path", ""),
        "section_id": src.get("section_id"),        # node 검색 시 Small-to-Big 확장용
        "section": src.get("section_path"),
        "content": src.get("proposition") or src.get("summary") or "",
        "keywords": src.get("keywords") or [],
        "source_type": source_type,
        "source": {
            "file_name": src.get("source_path", ""),
            "page": None,
            "section_path": src.get("section_path"),
        },
    }


def search(
    index_type: str,
    embedding_query: str,
    keyword_query: str,
    domain_filter: str | None = None,
    document_filter: list[str] | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Hybrid Search 실행.

    Args:
        index_type: "document" | "node"
        embedding_query: Vector 검색 쿼리 텍스트
        keyword_query: BM25 검색 쿼리 텍스트
        domain_filter: 도메인 필터 (None이면 전체)
        document_filter: 문서 ID 필터 목록
        top_k: 반환 결과 수

    Returns:
        SearchResult 형식의 dict 목록
    """
    if index_type == "document":
        index = INDEX_DOCUMENT
        content_field = "summary"
        source_type = "document"
    elif index_type == "node":
        index = INDEX_PROPOSITION
        content_field = "proposition"
        source_type = "node"
    else:
        raise ValueError(f"index_type must be 'document' or 'node', got: {index_type}")

    filters = _build_filter(domain_filter, document_filter)
    vector = _embed(embedding_query)
    fetch_k = top_k * 2

    knn_hits  = _knn_search(index, vector, filters, fetch_k)
    bm25_hits = _bm25_search(index, keyword_query, content_field, filters, fetch_k)
    merged    = _rrf_merge(knn_hits, bm25_hits, top_k)

    return [_hit_to_result(hit, source_type) for hit in merged]
