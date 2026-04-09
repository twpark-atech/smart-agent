"""Retrieval Multi-Agent API Router

엔드포인트:
    POST /retriever/query  - 사용자 질의 → RAG 답변 반환
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# retriever 모듈 경로 주입
_RETRIEVER_DIR = Path(__file__).parent.parent.parent / "retriever"
if str(_RETRIEVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RETRIEVER_DIR))

from agents import orchestrator

router = APIRouter(prefix="/retriever", tags=["retriever"])


# ── 요청/응답 모델 ────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    status: str
    answer: Optional[str] = None
    sources: Optional[list[str]] = None
    reason: Optional[str] = None
    detail: Optional[str] = None
    partial_result: Optional[str] = None


# ── 엔드포인트 ────────────────────────────────────────────────────────────

@router.post("/query", response_model=QueryResponse, summary="검색 질의 실행")
def query(body: QueryRequest):
    """
    사용자 질의를 받아 Retrieval Multi-Agent 파이프라인을 실행하고 답변을 반환합니다.

    흐름: Planner → Retriever → Aggregator → Writer → Supervisor

    - `status: "success"` → `answer`, `sources` 포함
    - `status: "failed"` → `reason`, `detail` 포함, `partial_result` 있을 수 있음
    """
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query는 비어 있을 수 없습니다.")

    try:
        result = orchestrator.run(body.query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return QueryResponse(**result)
