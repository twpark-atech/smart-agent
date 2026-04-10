"""Orchestrator Agent - 전체 에이전트 흐름 제어 및 라우팅"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
)
from models import (
    BlockRef,
    SIGNAL_RE_SEARCH, SIGNAL_PASS,
    SIGNAL_RETRY_RETRIEVAL, SIGNAL_RETRY_WRITER,
)
from agents import planner
from agents import retriever
from agents import aggregator
from agents import writer
from agents import supervisor

logger = logging.getLogger(__name__)


def _resolve_block_refs(block_refs: list[BlockRef]) -> list[str]:
    """블록 참조를 출처 문자열로 변환.

    각 BlockRef에서 section_id → document source_path를 조회하여
    'filename.pdf, p.15 #2' 형식으로 반환.
    block_refs가 없으면 빈 리스트 반환.
    """
    if not block_refs:
        return []

    # section_id 목록으로 한 번에 조회
    section_ids = list({ref.section_id for ref in block_refs})
    section_meta: dict[int, str] = {}  # section_id → 파일명(basename)

    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST, port=POSTGRES_PORT,
            dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT s.id, d.source_path "
                    "FROM parser_sections s "
                    "JOIN parser_documents d ON d.document_id = s.document_id "
                    "WHERE s.id = ANY(%s)",
                    (section_ids,),
                )
                for sec_id, source_path in cur.fetchall():
                    section_meta[sec_id] = Path(source_path).name if source_path else ""
        conn.close()
    except Exception as e:
        logger.warning("[Orchestrator] 출처 조회 실패: %s", e)
        return []

    sources: list[str] = []
    for ref in block_refs:
        doc_name = section_meta.get(ref.section_id, "")
        if ref.first_page is not None:
            if ref.last_page is not None and ref.last_page != ref.first_page:
                page_str = f"pp.{ref.first_page}-{ref.last_page}"
            else:
                page_str = f"p.{ref.first_page}"
            sources.append(f"{doc_name}, {page_str}")
        else:
            sources.append(doc_name)

    return sources


def _failure_response(reason: str, partial_answer: str | None = None) -> dict:
    return {
        "status": "failed",
        "reason": "max_retry_exceeded",
        "detail": reason,
        "partial_result": partial_answer,
        "message": "검색 결과가 충분하지 않아 답변을 생성할 수 없습니다.",
    }


def run(query: str, progress_cb=None) -> dict:
    """사용자 질의를 받아 최종 답변을 반환.

    흐름:
        Planner → Retriever → Aggregator
            └─ RE_SEARCH → Planner(재작성) → Retriever (retrieval_retry 제한)
        → Writer → Supervisor
            ├─ PASS → 최종 반환
            ├─ RETRY_RETRIEVAL → Retriever → Aggregator → Writer (supervisor_retry 제한)
            └─ RETRY_WRITER → Writer (supervisor_retry 제한)

    Args:
        query: 사용자 질의 문자열
        progress_cb: 진행 이벤트 콜백 fn({"type":"progress","agent":str,"label":str,"message":str})

    Returns:
        성공: {"status": "success", "answer": str, "sources": list}
        실패: {"status": "failed", "reason": str, "detail": str, ...}
    """
    def emit(agent: str, label: str, message: str = "") -> None:
        if progress_cb:
            progress_cb({"type": "progress", "agent": agent, "label": label, "message": message})

    # ── 1. Planner: Task Queue 생성 ────────────────────────────────────
    emit("planner", "계획 수립", f'"{query[:80]}"')
    logger.info("[Orchestrator] Planner 호출")
    task_queue = planner.plan(query)

    # ── 2. Retrieval 루프 ────────────────────────────────────────────────
    aggregated_content = None

    for attempt in range(task_queue.max_retrieval_retry + 1):
        emit(
            "retriever", "데이터 수집",
            f"검색 유형: {task_queue.task_type} | 도메인: {task_queue.domain}",
        )
        logger.info(
            "[Orchestrator] Retriever 호출 (retrieval_retry=%d/%d)",
            task_queue.retrieval_retry, task_queue.max_retrieval_retry,
        )
        results = retriever.run(task_queue)

        emit("aggregator", "데이터 검증", f"검색 결과 {len(results)}건 분석 중...")
        logger.info("[Orchestrator] Aggregator 호출")
        agg_result = aggregator.aggregate(task_queue, results)

        if agg_result["signal"] != SIGNAL_RE_SEARCH:
            aggregated_content = agg_result["aggregated"]
            break

        # RE_SEARCH 처리
        task_queue.retrieval_retry += 1
        if task_queue.retrieval_retry > task_queue.max_retrieval_retry:
            logger.warning("[Orchestrator] retrieval_retry 한도 초과")
            return _failure_response(
                f"retrieval_retry 한도({task_queue.max_retrieval_retry}) 초과: "
                f"{agg_result.get('reason', '')}",
            )

        re_reason = agg_result.get("reason", "검색 결과 불충분")
        emit("aggregator", "재검색 요청", re_reason)
        logger.info(
            "[Orchestrator] RE_SEARCH 신호 수신, Planner 재작성 호출: %s", re_reason,
        )
        task_queue = planner.rewrite(task_queue, re_reason)

    if aggregated_content is None:
        return _failure_response("Aggregator에서 유효한 결과를 얻지 못했습니다.")

    # ── 3. Supervisor 루프 ───────────────────────────────────────────────
    last_writer_output = None

    for attempt in range(task_queue.max_supervisor_retry + 1):
        emit("writer", "답변 작성", "검색된 내용 기반으로 답변 생성 중...")
        logger.info("[Orchestrator] Writer 호출 (supervisor_retry=%d)", task_queue.supervisor_retry)
        writer_output = writer.write(query, aggregated_content)
        last_writer_output = writer_output

        emit("supervisor", "품질 검증", "사실성·완전성 검토 중...")
        logger.info("[Orchestrator] Supervisor 호출")
        sup_result = supervisor.verify(query, aggregated_content, writer_output)

        if sup_result.signal == SIGNAL_PASS:
            logger.info("[Orchestrator] Supervisor 통과 → 최종 답변 반환")
            sources = _resolve_block_refs(writer_output.block_refs) + writer_output.web_refs
            return {
                "status": "success",
                "answer": writer_output.answer,
                "sources": sources,
            }

        # RETRY 처리
        task_queue.supervisor_retry += 1
        if task_queue.supervisor_retry > task_queue.max_supervisor_retry:
            logger.warning("[Orchestrator] supervisor_retry 한도 초과 → 마지막 답변 반환")
            return _failure_response(
                f"supervisor_retry 한도({task_queue.max_supervisor_retry}) 초과: "
                f"{sup_result.reason}",
                partial_answer=writer_output.answer,
            )

        if sup_result.signal == SIGNAL_RETRY_RETRIEVAL:
            emit("supervisor", "재검색 지시", sup_result.reason)
            logger.info(
                "[Orchestrator] RETRY_RETRIEVAL 수신, Retriever 재호출: %s",
                sup_result.reason,
            )
            results = retriever.run(task_queue)
            agg_result = aggregator.aggregate(task_queue, results)
            if agg_result["signal"] != SIGNAL_RE_SEARCH:
                aggregated_content = agg_result["aggregated"]

        elif sup_result.signal == SIGNAL_RETRY_WRITER:
            emit("supervisor", "재작성 지시", sup_result.reason)
            logger.info(
                "[Orchestrator] RETRY_WRITER 수신, Writer 재호출: %s",
                sup_result.reason,
            )
            # aggregated_content 유지, Writer만 재실행 (루프 계속)

    # 루프 정상 종료 불가 → 마지막 결과 반환
    partial = last_writer_output.answer if last_writer_output else None
    return _failure_response("Supervisor 루프 비정상 종료", partial_answer=partial)
