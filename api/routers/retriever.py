"""Retrieval Multi-Agent API Router

엔드포인트:
    POST /retriever/query  - 사용자 질의 → RAG 답변 반환
"""
from __future__ import annotations

import json
import queue
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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

@router.post("/query/stream", summary="스트리밍 검색 질의 (SSE)")
def query_stream(body: QueryRequest):
    """
    질의를 SSE(Server-Sent Events) 스트림으로 실행합니다.

    - `data: {"type":"progress","agent":"planner","label":"계획 수립","message":"..."}` — 에이전트 진행 이벤트
    - `data: {"type":"done","result":{...}}` — 최종 결과 (QueryResponse 형식)
    - `data: {"type":"error","detail":"..."}` — 오류 발생
    """
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query는 비어 있을 수 없습니다.")

    event_queue: queue.SimpleQueue = queue.SimpleQueue()

    def _run():
        try:
            result = orchestrator.run(body.query, progress_cb=event_queue.put)
            event_queue.put({"type": "done", "result": result})
        except Exception as e:
            event_queue.put({"type": "error", "detail": str(e)})

    threading.Thread(target=_run, daemon=True).start()

    def _generate():
        while True:
            ev = event_queue.get()
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev.get("type") in ("done", "error"):
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
