"""OpenSearch 클라이언트 - 인덱스 관리 및 검색"""
import asyncio
from opensearchpy import OpenSearch
from shared.config import (
    OPENSEARCH_HOST, OPENSEARCH_PORT, EMBEDDING_DIM,
    IDX_DOCUMENTS, IDX_CHUNKS,
)


def _get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_compress=True,
        use_ssl=False,
        verify_certs=False,
    )


# ── 인덱스 매핑 ────────────────────────────────────────────

_KNN_FIELD = {
    "type": "knn_vector",
    "dimension": EMBEDDING_DIM,
    "method": {
        "name": "hnsw",
        "space_type": "cosinesimil",
        "engine": "lucene",
        "parameters": {"ef_construction": 128, "m": 16},
    },
}

_DOC_MAPPING = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "document_id":      {"type": "keyword"},
            "title":            {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "summary":          {"type": "text"},
            "keywords":         {"type": "keyword"},
            "summary_embedding": _KNN_FIELD,
            "domain_category":  {"type": "keyword"},
            "doc_type":         {"type": "keyword"},
            "file_name":        {"type": "keyword"},
            "created_at":       {"type": "date"},
        }
    },
}

_CHUNK_MAPPING = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "chunk_id":                  {"type": "keyword"},
            "document_id":               {"type": "keyword"},
            "parent_section_id":         {"type": "keyword"},
            "section_name":              {"type": "text"},
            "section_depth":             {"type": "integer"},
            "section_path":              {"type": "keyword"},
            "section_summary":           {"type": "text"},
            "section_summary_embedding": _KNN_FIELD,
            "proposition":               {"type": "text"},
            "contextual_proposition":    {"type": "text"},
            "proposition_embedding":     _KNN_FIELD,
            "content":                   {"type": "text"},
            "token_count":               {"type": "integer"},
            "keywords":                  {"type": "keyword"},
        }
    },
}


# ── 초기화 ────────────────────────────────────────────────

def ensure_indices():
    client = _get_client()
    for idx, mapping in [(IDX_DOCUMENTS, _DOC_MAPPING), (IDX_CHUNKS, _CHUNK_MAPPING)]:
        if not client.indices.exists(index=idx):
            client.indices.create(index=idx, body=mapping)
            print(f"[OpenSearch] 인덱스 생성: {idx}")


# ── 적재 ──────────────────────────────────────────────────

def _index_doc(doc: dict):
    client = _get_client()
    client.index(index=IDX_DOCUMENTS, id=doc["document_id"], body=doc)


def _index_chunk(chunk: dict):
    client = _get_client()
    client.index(index=IDX_CHUNKS, id=chunk["chunk_id"], body=chunk)


def _delete_document(document_id: str):
    client = _get_client()
    client.delete(index=IDX_DOCUMENTS, id=document_id, ignore=[404])
    client.delete_by_query(
        index=IDX_CHUNKS,
        body={"query": {"term": {"document_id": document_id}}},
    )


async def index_document(doc: dict):
    await asyncio.to_thread(_index_doc, doc)


async def index_chunk(chunk: dict):
    await asyncio.to_thread(_index_chunk, chunk)


async def delete_document(document_id: str):
    await asyncio.to_thread(_delete_document, document_id)


# ── 검색 ──────────────────────────────────────────────────

def _build_domain_filter(domain_filters: list[dict]) -> dict | None:
    """도메인 후보 리스트를 OR 조건 필터로 변환.
    domain_category 단일 필드이므로 terms 쿼리로 처리."""
    values = [f["domain_category"] for f in domain_filters if f.get("domain_category")]
    if not values:
        return None
    if len(values) == 1:
        return {"term": {"domain_category": values[0]}}
    return {"terms": {"domain_category": values}}


def _knn_search(index: str, field: str, vector: list[float], k: int,
                domain_filters: list[dict] | None = None) -> list[dict]:
    knn_body: dict = {"vector": vector, "k": k}
    filter_query = _build_domain_filter(domain_filters) if domain_filters else None
    if filter_query:
        knn_body["filter"] = filter_query
    resp = _get_client().search(
        index=index,
        body={"size": k, "query": {"knn": {field: knn_body}}},
    )
    return resp["hits"]["hits"]


def _bm25_search(index: str, query: str, fields: list[str], size: int,
                 domain_filters: list[dict] | None = None) -> list[dict]:
    must: list = [{"multi_match": {"query": query, "fields": fields}}]
    body: dict = {"size": size, "query": {"bool": {"must": must}}}
    filter_query = _build_domain_filter(domain_filters) if domain_filters else None
    if filter_query:
        body["query"]["bool"]["filter"] = [filter_query]
    resp = _get_client().search(index=index, body=body)
    return resp["hits"]["hits"]


def _rrf(lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion - 여러 결과 목록을 합산"""
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}
    for ranked in lists:
        for rank, hit in enumerate(ranked):
            doc_id = hit["_id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            docs[doc_id] = hit
    return [docs[i] for i in sorted(scores, key=scores.__getitem__, reverse=True)]


def _list_documents(size: int = 100) -> list[dict]:
    resp = _get_client().search(
        index=IDX_DOCUMENTS,
        body={"size": size, "query": {"match_all": {}}},
    )
    return [h["_source"] for h in resp["hits"]["hits"]]


def _get_document(document_id: str) -> dict | None:
    try:
        resp = _get_client().get(index=IDX_DOCUMENTS, id=document_id)
        return resp["_source"]
    except Exception:
        return None


def _count_chunks(document_id: str) -> int:
    resp = _get_client().count(
        index=IDX_CHUNKS,
        body={"query": {"term": {"document_id": document_id}}},
    )
    return resp["count"]


def _search_documents(query_emb: list[float], query_text: str,
                      domain_filters: list[dict] | None, top_k: int = 10) -> list[dict]:
    try:
        vec_hits = _knn_search(IDX_DOCUMENTS, "summary_embedding", query_emb, top_k, domain_filters)
    except Exception as e:
        print(f"[WARN] kNN 문서 검색 실패, BM25만 사용: {e}")
        vec_hits = []
    try:
        bm25_hits = _bm25_search(
            IDX_DOCUMENTS, query_text,
            ["title^3", "summary^2", "keywords"],
            top_k, domain_filters,
        )
    except Exception as e:
        print(f"[WARN] BM25 문서 검색 실패: {e}")
        bm25_hits = []
    merged = _rrf([vec_hits, bm25_hits])
    return [h["_source"] for h in merged[:top_k]]


def _search_sections(query_emb: list[float], doc_ids: list[str], top_k: int = 10) -> list[dict]:
    """Step 3-2: Index Search — 섹션 요약 임베딩으로 관련 섹션 범위 확정"""
    if not doc_ids:
        return []
    try:
        body = {
            "size": top_k,
            "query": {
                "knn": {
                    "section_summary_embedding": {
                        "vector": query_emb,
                        "k": top_k,
                        "filter": {"terms": {"document_id": doc_ids}},
                    }
                }
            },
            "_source": ["chunk_id", "document_id", "parent_section_id",
                        "section_name", "section_path", "section_depth"],
        }
        resp = _get_client().search(index=IDX_CHUNKS, body=body)
        return [h["_source"] for h in resp["hits"]["hits"]]
    except Exception as e:
        print(f"[WARN] 섹션 검색 실패, 섹션 필터 없이 진행: {e}")
        return []


def _search_chunks(query_emb: list[float], section_ids: list[str],
                   doc_ids: list[str], top_k: int = 10) -> list[dict]:
    """Step 4: 청크 검색 — 섹션 범위 내에서 proposition_embedding 검색"""
    if section_ids:
        filter_body = {"terms": {"parent_section_id": section_ids}}
    elif doc_ids:
        filter_body = {"terms": {"document_id": doc_ids}}
    else:
        filter_body = None

    knn_body: dict = {"vector": query_emb, "k": top_k}
    if filter_body:
        knn_body["filter"] = filter_body

    try:
        resp = _get_client().search(
            index=IDX_CHUNKS,
            body={"size": top_k, "query": {"knn": {"proposition_embedding": knn_body}}},
        )
        return [h["_source"] for h in resp["hits"]["hits"]]
    except Exception as e:
        print(f"[WARN] 청크 kNN 검색 실패, BM25 fallback: {e}")
        # fallback: BM25로 대체
        filter_clause = []
        if section_ids:
            filter_clause.append({"terms": {"parent_section_id": section_ids}})
        elif doc_ids:
            filter_clause.append({"terms": {"document_id": doc_ids}})
        body: dict = {
            "size": top_k,
            "query": {"bool": {
                "must": [{"match_all": {}}],
                "filter": filter_clause,
            }},
        }
        resp = _get_client().search(index=IDX_CHUNKS, body=body)
        return [h["_source"] for h in resp["hits"]["hits"]]


def _get_siblings(parent_section_id: str) -> list[dict]:
    """Auto-Merge: 같은 parent의 모든 형제 청크 반환"""
    resp = _get_client().search(
        index=IDX_CHUNKS,
        body={
            "size": 50,
            "query": {"term": {"parent_section_id": parent_section_id}},
            "sort": [{"_score": "desc"}],
        },
    )
    return [h["_source"] for h in resp["hits"]["hits"]]


async def list_documents(size: int = 100) -> list[dict]:
    return await asyncio.to_thread(_list_documents, size)


async def get_document(document_id: str) -> dict | None:
    return await asyncio.to_thread(_get_document, document_id)


async def count_chunks(document_id: str) -> int:
    return await asyncio.to_thread(_count_chunks, document_id)


async def search_documents(query_emb, query_text, domain_filters, top_k=10):
    return await asyncio.to_thread(_search_documents, query_emb, query_text, domain_filters, top_k)


async def search_sections(query_emb, doc_ids, top_k=10):
    return await asyncio.to_thread(_search_sections, query_emb, doc_ids, top_k)


async def search_chunks(query_emb, section_ids, doc_ids, top_k=10):
    return await asyncio.to_thread(_search_chunks, query_emb, section_ids, doc_ids, top_k)


async def get_siblings(parent_section_id: str) -> list[dict]:
    return await asyncio.to_thread(_get_siblings, parent_section_id)


def _remove_field_from_index(index: str, field: str) -> dict:
    """update_by_query로 인덱스 전체 문서에서 특정 필드 제거"""
    resp = _get_client().update_by_query(
        index=index,
        body={
            "script": {
                "source": f"ctx._source.remove('{field}')",
                "lang": "painless",
            },
            "query": {"match_all": {}},
        },
        params={"conflicts": "proceed"},
    )
    return {
        "updated": resp.get("updated", 0),
        "failures": len(resp.get("failures", [])),
    }


async def remove_field(index: str, field: str) -> dict:
    return await asyncio.to_thread(_remove_field_from_index, index, field)


def get_index_stats() -> dict:
    client = _get_client()
    stats = {}
    for idx in [IDX_DOCUMENTS, IDX_CHUNKS]:
        try:
            resp = client.count(index=idx, body={"query": {"match_all": {}}})
            stats[idx] = resp["count"]
        except Exception:
            stats[idx] = -1
    return stats
