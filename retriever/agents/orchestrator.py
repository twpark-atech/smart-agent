"""Orchestrator Agent - 전체 에이전트 흐름 제어 및 라우팅"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models import (
    SIGNAL_RE_SEARCH, SIGNAL_PASS,
    SIGNAL_RETRY_RETRIEVAL, SIGNAL_RETRY_WRITER,
)
from agents import planner
from agents import retriever
from agents import aggregator
from agents import writer
from agents import supervisor

logger = logging.getLogger(__name__)


def _failure_response(reason: str, partial_answer: str | None = None) -> dict:
    return {
        "status": "failed",
        "reason": "max_retry_exceeded",
        "detail": reason,
        "partial_result": partial_answer,
        "message": "검색 결과가 충분하지 않아 답변을 생성할 수 없습니다.",
    }


def run(query: str) -> dict:
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

    Returns:
        성공: {"status": "success", "answer": str, "sources": list}
        실패: {"status": "failed", "reason": str, "detail": str, ...}
    """
    # ── 1. Planner: Task Queue 생성 ────────────────────────────────────
    logger.info("[Orchestrator] Planner 호출")
    task_queue = planner.plan(query)

    # ── 2. Retrieval 루프 ────────────────────────────────────────────────
    aggregated_content = None

    for attempt in range(task_queue.max_retrieval_retry + 1):
        logger.info(
            "[Orchestrator] Retriever 호출 (retrieval_retry=%d/%d)",
            task_queue.retrieval_retry, task_queue.max_retrieval_retry,
        )
        results = retriever.run(task_queue)

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

        logger.info(
            "[Orchestrator] RE_SEARCH 신호 수신, Planner 재작성 호출: %s",
            agg_result.get("reason", ""),
        )
        task_queue = planner.rewrite(task_queue, agg_result.get("reason", ""))

    if aggregated_content is None:
        return _failure_response("Aggregator에서 유효한 결과를 얻지 못했습니다.")

    # ── 3. Supervisor 루프 ───────────────────────────────────────────────
    last_writer_output = None

    for attempt in range(task_queue.max_supervisor_retry + 1):
        logger.info("[Orchestrator] Writer 호출 (supervisor_retry=%d)", task_queue.supervisor_retry)
        writer_output = writer.write(query, aggregated_content)
        last_writer_output = writer_output

        logger.info("[Orchestrator] Supervisor 호출")
        sup_result = supervisor.verify(query, aggregated_content, writer_output)

        if sup_result.signal == SIGNAL_PASS:
            logger.info("[Orchestrator] Supervisor 통과 → 최종 답변 반환")
            return {
                "status": "success",
                "answer": writer_output.answer,
                "sources": writer_output.sources,
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
            logger.info(
                "[Orchestrator] RETRY_RETRIEVAL 수신, Retriever 재호출: %s",
                sup_result.reason,
            )
            results = retriever.run(task_queue)
            agg_result = aggregator.aggregate(task_queue, results)
            if agg_result["signal"] != SIGNAL_RE_SEARCH:
                aggregated_content = agg_result["aggregated"]
            # else: 기존 aggregated_content 유지하고 Writer 재시도

        elif sup_result.signal == SIGNAL_RETRY_WRITER:
            logger.info(
                "[Orchestrator] RETRY_WRITER 수신, Writer 재호출: %s",
                sup_result.reason,
            )
            # aggregated_content 유지, Writer만 재실행 (루프 계속)

    # 루프 정상 종료 불가 → 마지막 결과 반환
    partial = last_writer_output.answer if last_writer_output else None
    return _failure_response("Supervisor 루프 비정상 종료", partial_answer=partial)
