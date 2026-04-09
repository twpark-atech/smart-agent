"""검색 API 서버 — port 8002

Endpoints:
  POST /search    쿼리 → 답변 + 출처
  GET  /health    서버 상태 + 인덱스 통계
"""
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared import opensearch_client as os_client
from searcher import pipeline

app = FastAPI(title="Smart Agent Searcher", version="1.0.0")


@app.on_event("startup")
async def startup():
    await asyncio.to_thread(os_client.ensure_indices)


# ── 헬스체크 ──────────────────────────────────────────────

@app.get("/health")
async def health():
    stats = await asyncio.to_thread(os_client.get_index_stats)
    return {"status": "ok", "index_stats": stats}


# ── 검색 ──────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    top_domain: int = 3


class SearchResponse(BaseModel):
    query: str
    answer: str
    track: str
    original_query: str | None = None
    rewritten_query: str | None = None
    domain_candidates: list[dict] = []
    domain_fallback: bool = False
    validation: dict | None = None
    docs: list[dict] = []
    chunks: list[dict] = []
    sub_queries: list[dict] | None = None


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    if not req.query.strip():
        raise HTTPException(400, "쿼리가 비어 있습니다.")
    if req.top_k < 1 or req.top_k > 20:
        raise HTTPException(400, "top_k는 1~20 범위여야 합니다.")
    if req.top_domain < 1 or req.top_domain > 5:
        raise HTTPException(400, "top_domain은 1~5 범위여야 합니다.")

    result = await pipeline.search(req.query, req.top_k, req.top_domain)
    return SearchResponse(query=req.query, **result)
