"""검색 파이프라인
hierarchical_rag.md 기준:
  Track A (간단):
    [Step 1] 쿼리 임베딩
    [Step 2] 도메인 분류 (임베딩 매칭 → 확실하면 Track A)
    [Step 3] 문서 검색 (Hybrid: Vector + BM25 + RRF)
    [Step 3-2] 섹션 검색 (Index Search — section_summary_embedding)
    [Step 4] 청크 검색 (proposition_embedding) + Auto-Merge
    [Step 5] Writer Agent
    [Step 6] Validator Agent (필수)

  Track B (복합):
    [Step 2] LLM으로 서브 쿼리 분해
    [Step 3] 서브 쿼리별 병렬 문서 검색
    [Step 3-2] 서브 쿼리별 병렬 섹션 검색
    이후 Track A와 동일
"""
import asyncio
from shared import llm, embedder, opensearch_client as os_client
from shared.config import MIN_TOKENS


async def _auto_merge(chunks: list[dict], query_emb: list[float]) -> list[dict]:
    """같은 parent를 가진 청크가 2개 이상이면 형제 청크를 모두 병합.
    결과 토큰이 MIN_TOKENS 미만이면 추가 형제 포함."""
    from collections import Counter

    parent_counts = Counter(c.get("parent_section_id") for c in chunks if c.get("parent_section_id"))
    merge_parents = {p for p, cnt in parent_counts.items() if cnt >= 2}

    if not merge_parents:
        return chunks

    # 병합 대상 교체
    merged_ids = set()
    result = []
    for chunk in chunks:
        pid = chunk.get("parent_section_id")
        if pid in merge_parents and pid not in merged_ids:
            siblings = await os_client.get_siblings(pid)
            result.extend(siblings)
            merged_ids.add(pid)
        elif pid not in merge_parents:
            result.append(chunk)

    return result


async def _track_a(query: str, query_emb: list[float], domain_candidates: list[dict], top_k: int) -> dict:
    domain_fallback = False

    # [Step 3] 문서 검색 — 도메인 후보 OR 필터 적용, 결과 없으면 필터 해제 후 재검색
    docs = await os_client.search_documents(
        query_emb, query, domain_candidates or None, top_k=top_k
    )
    if not docs and domain_candidates:
        domain_fallback = True
        docs = await os_client.search_documents(query_emb, query, None, top_k=top_k)
    if not docs:
        return {"answer": "관련 문서를 찾지 못했습니다.", "chunks": [], "track": "A", "docs": [],
                "domain_candidates": domain_candidates, "domain_fallback": domain_fallback}

    doc_ids = [d["document_id"] for d in docs]

    # [Step 3-2] 섹션 검색 (Index Search)
    sections = await os_client.search_sections(query_emb, doc_ids, top_k=top_k)
    section_ids = [s["parent_section_id"] for s in sections if s.get("parent_section_id")]

    # fallback: 섹션 없으면 문서 전체 범위
    search_section_ids = section_ids if section_ids else []

    # [Step 4] 청크 검색
    chunks = await os_client.search_chunks(query_emb, search_section_ids, doc_ids, top_k=top_k)
    if not chunks:
        return {"answer": "관련 내용을 찾지 못했습니다.", "chunks": [], "track": "A", "docs": docs}

    # Auto-Merge
    chunks = await _auto_merge(chunks, query_emb)

    # [Step 5] Writer Agent
    answer = await llm.write_answer(query, chunks)

    # [Step 6] Validator Agent (필수)
    validation = await llm.validate_answer(query, answer, chunks)
    if not validation.get("valid", True) and len(chunks) < top_k * 2:
        # Top-K 확장 후 재생성 (최대 1회)
        chunks_ext = await os_client.search_chunks(query_emb, search_section_ids, doc_ids, top_k=top_k * 2)
        chunks_ext = await _auto_merge(chunks_ext, query_emb)
        answer = await llm.write_answer(query, chunks_ext)
        validation = await llm.validate_answer(query, answer, chunks_ext)
        chunks = chunks_ext

    return {
        "answer": answer,
        "validation": validation,
        "track": "A",
        "domain_candidates": domain_candidates,
        "domain_fallback": domain_fallback,
        "docs": [{"document_id": d["document_id"], "title": d["title"]} for d in docs],
        "chunks": [
            {
                "section_path": c.get("section_path", ""),
                "proposition": c.get("proposition", ""),
                "content_preview": c.get("content", "")[:200],
                "token_count": c.get("token_count", 0),
            }
            for c in chunks[:5]
        ],
    }


async def _track_b(query: str, query_emb: list[float], sub_queries: list[dict], top_k: int) -> dict:
    # [Step 3] 서브 쿼리별 병렬 문서 검색
    async def _search_sub(sq: dict):
        sub_emb = await embedder.embed_one(sq["query"])
        # 서브쿼리의 단일 도메인을 필터로 사용
        sq_filter = []
        f = {"domain_category": sq["domain_category"]} if sq.get("domain_category") else {}
        if f:
            sq_filter = [f]
        docs = await os_client.search_documents(
            sub_emb, sq["query"], sq_filter or None, top_k=top_k
        )
        if not docs and sq_filter:
            docs = await os_client.search_documents(sub_emb, sq["query"], None, top_k=top_k)
        # [Step 3-2] 섹션 검색
        doc_ids = [d["document_id"] for d in docs]
        sections = await os_client.search_sections(sub_emb, doc_ids, top_k=top_k)
        section_ids = [s["parent_section_id"] for s in sections if s.get("parent_section_id")]
        return docs, doc_ids, section_ids

    results = await asyncio.gather(*[_search_sub(sq) for sq in sub_queries])

    all_docs, all_doc_ids, all_section_ids = [], [], []
    seen_doc_ids = set()
    for docs, doc_ids, section_ids in results:
        for doc in docs:
            if doc["document_id"] not in seen_doc_ids:
                all_docs.append(doc)
                seen_doc_ids.add(doc["document_id"])
        all_doc_ids.extend(doc_ids)
        all_section_ids.extend(section_ids)

    if not all_docs:
        return {"answer": "관련 문서를 찾지 못했습니다.", "chunks": [], "track": "B", "docs": []}

    # [Step 4] 통합 청크 검색
    chunks = await os_client.search_chunks(
        query_emb, list(set(all_section_ids)), list(set(all_doc_ids)), top_k=top_k
    )
    if not chunks:
        return {"answer": "관련 내용을 찾지 못했습니다.", "chunks": [], "track": "B", "docs": all_docs}

    chunks = await _auto_merge(chunks, query_emb)

    # [Step 5] Writer Agent
    answer = await llm.write_answer(query, chunks)

    # [Step 6] Validator Agent (필수)
    validation = await llm.validate_answer(query, answer, chunks)
    if not validation.get("valid", True) and len(chunks) < top_k * 2:
        chunks_ext = await os_client.search_chunks(
            query_emb, list(set(all_section_ids)), list(set(all_doc_ids)), top_k=top_k * 2
        )
        chunks_ext = await _auto_merge(chunks_ext, query_emb)
        answer = await llm.write_answer(query, chunks_ext)
        validation = await llm.validate_answer(query, answer, chunks_ext)
        chunks = chunks_ext

    return {
        "answer": answer,
        "validation": validation,
        "track": "B",
        "sub_queries": [
            {
                "query": sq["query"],
                "domain_category": sq.get("domain_category", ""),
            }
            for sq in sub_queries
        ],
        "docs": [{"document_id": d["document_id"], "title": d["title"]} for d in all_docs],
        "chunks": [
            {
                "section_path": c.get("section_path", ""),
                "proposition": c.get("proposition", ""),
                "content_preview": c.get("content", "")[:200],
                "token_count": c.get("token_count", 0),
            }
            for c in chunks[:5]
        ],
    }


async def search(query: str, top_k: int = 5, top_domain: int = 3) -> dict:
    # [Step 0] Query Rewriter Agent
    rewritten_query = await llm.rewrite_query(query)
    search_query = rewritten_query if rewritten_query != query else query

    # [Step 1] 쿼리 임베딩 (변환된 쿼리로)
    query_emb = await embedder.embed_one(search_query)

    # [Step 2] 도메인 분류 + Track 결정 (변환된 쿼리로, top_domain개 후보)
    domain_info = await llm.classify_query(search_query, top_domain=top_domain)
    domain_candidates = domain_info.get("domain_candidates", [])

    if domain_info.get("is_complex") and domain_info.get("sub_queries"):
        result = await _track_b(search_query, query_emb, domain_info["sub_queries"], top_k)
    else:
        result = await _track_a(search_query, query_emb, domain_candidates, top_k)

    result["original_query"] = query
    result["rewritten_query"] = rewritten_query
    return result
