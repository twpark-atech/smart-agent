"""Retriever Agent - Task Queue 기반 검색 실행 (Small-to-Big Retrieval)"""
from __future__ import annotations

import base64
import io
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2
import psycopg2.extras
from minio import Minio
from minio.error import S3Error

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
    MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET, MINIO_SECURE,
)
from models import TaskQueue, BlockRef, STATUS_IN_PROGRESS, STATUS_DONE, STATUS_FAILED
from tools import opensearch_search
from tools import postgres_search
from tools import web_search
from tools import image_search

logger = logging.getLogger(__name__)

_TOP_K_DOMAIN      = 3   # 도메인 검색 결과 수
_TOP_K_DOCUMENT    = 10  # 문서 검색 결과 수
_TOP_K_NODE        = 15  # 노드(명제) 검색 결과 수 - 섹션 전체로 확장되므로 과다하지 않게
_TOP_K_WEB         = 5   # 웹 검색 결과 수
_TOP_K_IMAGE       = 5   # 이미지 검색 결과 수
_MAX_IMAGES_PER_SECTION = 3  # 섹션당 전달할 최대 이미지 수 (VLM 부하 제한)

_minio_client: Minio | None = None


def _get_minio() -> Minio:
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
    return _minio_client


# ── PostgreSQL 섹션 전체 내용 조회 ─────────────────────────────────────

def _pg_connect():
    return psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD,
    )


def _fetch_section_content(section_id: int) -> str:
    """section_id에 해당하는 모든 블록 내용을 하나의 텍스트로 결합.

    text / image(설명) / table 블록을 순서대로 결합하여 섹션 전체 컨텍스트 반환.
    """
    try:
        with _pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT block_type, content FROM parser_blocks "
                    "WHERE section_id = %s ORDER BY seq",
                    (section_id,),
                )
                parts = []
                for block_type, content in cur.fetchall():
                    if content and content.strip():
                        parts.append(content.strip())
                return "\n\n".join(parts)
    except Exception as e:
        logger.warning("섹션 내용 조회 실패 (section_id=%d): %s", section_id, e)
        return ""


def _fetch_section_images(section_id: int) -> list[dict]:
    """section_id의 image 블록 minio_key를 조회하고 MinIO에서 base64로 다운로드.

    Returns:
        [{"minio_key": str, "mime": str, "base64": str}, ...]
        이미지가 없거나 다운로드 실패 시 빈 리스트 반환
    """
    try:
        with _pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT minio_key FROM parser_blocks "
                    "WHERE section_id = %s AND block_type = 'image' "
                    "AND minio_key IS NOT NULL ORDER BY seq LIMIT %s",
                    (section_id, _MAX_IMAGES_PER_SECTION),
                )
                minio_keys = [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.warning("이미지 블록 조회 실패 (section_id=%d): %s", section_id, e)
        return []

    if not minio_keys:
        return []

    images = []
    client = _get_minio()
    for key in minio_keys:
        try:
            resp = client.get_object(MINIO_BUCKET, key)
            data = resp.read()
            resp.close()
            resp.release_conn()

            ext = Path(key).suffix.lstrip(".").lower()
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
            b64 = base64.b64encode(data).decode("utf-8")
            images.append({"minio_key": key, "mime": mime, "base64": b64})
        except S3Error as e:
            logger.warning("MinIO 이미지 다운로드 실패 (key=%s): %s", key, e)

    if images:
        logger.info("섹션 이미지 로드: section_id=%d, %d건", section_id, len(images))
    return images


def _fetch_section_ref(section_id: int) -> BlockRef:
    """섹션의 첫/마지막 페이지를 조회하여 섹션 단위 BlockRef 반환."""
    try:
        with _pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MIN(page), MAX(page) FROM parser_blocks "
                    "WHERE section_id = %s AND page IS NOT NULL",
                    (section_id,),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    return BlockRef(section_id=section_id, first_page=row[0], last_page=row[1])
    except Exception as e:
        logger.warning("섹션 참조 조회 실패 (section_id=%d): %s", section_id, e)
    return BlockRef(section_id=section_id)


def _fetch_section_meta(section_id: int) -> dict:
    """section_id에 해당하는 섹션 메타 정보 조회."""
    try:
        with _pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT s.title, s.section_path, d.source_path "
                    "FROM parser_sections s "
                    "JOIN parser_documents d ON d.document_id = s.document_id "
                    "WHERE s.id = %s",
                    (section_id,),
                )
                row = cur.fetchone()
                if row:
                    return {"title": row[0], "section_path": row[1], "source_path": row[2]}
    except Exception as e:
        logger.warning("섹션 메타 조회 실패 (section_id=%d): %s", section_id, e)
    return {}


def _expand_node_results(node_results: list[dict]) -> list[dict]:
    """노드(명제) 검색 결과를 섹션 전체 내용으로 확장 (Small-to-Big).

    동일 section_id의 중복을 제거하고, 각 섹션의 전체 블록 내용을 PostgreSQL에서 조회.
    score는 해당 섹션에서 가장 높은 명제 점수를 사용.
    """
    # section_id 기준으로 최고 점수 결과 대표 선택 (중복 제거)
    best_by_section: dict[int, dict] = {}
    for result in node_results:
        section_id = result.get("section_id")
        if section_id is None:
            # section_id가 없으면 OpenSearch _source에서 추출 시도
            continue
        if section_id not in best_by_section or result["score"] > best_by_section[section_id]["score"]:
            best_by_section[section_id] = result

    expanded = []
    for section_id, rep in best_by_section.items():
        full_content = _fetch_section_content(section_id)
        meta         = _fetch_section_meta(section_id)
        images       = _fetch_section_images(section_id)
        block_ref    = _fetch_section_ref(section_id)

        if not full_content:
            full_content = rep.get("content", "")

        expanded.append({
            "id":                  rep["id"],
            "score":               rep["score"],
            "document_id":         rep.get("document_id", ""),
            "document_name":       meta.get("source_path", rep.get("document_name", "")),
            "section":             meta.get("section_path", rep.get("section", "")),
            "content":             full_content,
            "matched_proposition": rep.get("content", ""),
            "keywords":            rep.get("keywords", []),
            "images":              images,   # [{"minio_key", "mime", "base64"}, ...]
            "block_ref":           block_ref,  # 섹션 단위 BlockRef (1개)
            "source_type":         "node",
            "source": {
                "file_name":     meta.get("source_path", ""),
                "section_path":  meta.get("section_path", ""),
                "section_title": meta.get("title", ""),
            },
        })

    logger.info(
        "Small-to-Big 확장: 명제 %d건 → 섹션 %d건",
        len(node_results), len(expanded),
    )
    return expanded


# ── Step 실행 ──────────────────────────────────────────────────────────

def _run_step(step, task_queue: TaskQueue) -> list[dict]:
    """단일 step 실행. 결과 리스트 반환."""
    q = task_queue.query
    step.status = STATUS_IN_PROGRESS

    try:
        if step.type == "domain_search":
            results = opensearch_search.search(
                index_type="document",
                embedding_query=q.domain_query,
                keyword_query=q.domain_query,
                top_k=_TOP_K_DOMAIN,
            )

        elif step.type == "document_search":
            # domain_search가 이미 문서를 좁혀놨으므로 domain_filter 미적용
            # Planner 도메인 선택과 파서 분류 도메인의 불일치 방지
            domain_doc_ids = _collect_doc_ids(task_queue, "domain_search")
            results = opensearch_search.search(
                index_type="document",
                embedding_query=q.embedding_query,
                keyword_query=q.keyword_query,
                document_filter=domain_doc_ids or None,
                top_k=_TOP_K_DOCUMENT,
            )

        elif step.type == "node_search":
            # document_search 결과 우선, 없으면 domain_search 결과로 폴백
            doc_doc_ids = _collect_doc_ids(task_queue, "document_search")
            if not doc_doc_ids:
                doc_doc_ids = _collect_doc_ids(task_queue, "domain_search")
                logger.info("node_search: document_search 결과 없음, domain_search doc_ids로 폴백")
            raw_results = opensearch_search.search(
                index_type="node",
                embedding_query=q.embedding_query,
                keyword_query=q.keyword_query,
                document_filter=doc_doc_ids or None,
                top_k=_TOP_K_NODE,
            )
            # Small-to-Big: 명제 → 섹션 전체 내용으로 확장
            results = _expand_node_results(raw_results)

        elif step.type == "web_search":
            results = web_search.search(q.keyword_query, top_k=_TOP_K_WEB)

        elif step.type == "quantitative_search":
            if not q.sql_query:
                logger.warning("sql_query가 없음, quantitative_search 스킵")
                results = []
            else:
                raw = postgres_search.search(q.sql_query)
                results = [_pg_to_result(raw)] if raw["status"] == "success" else []

        elif step.type == "image_search":
            results = image_search.search(
                query_type="text",
                text_query=q.embedding_query,
                domain_filter=task_queue.domain,
                top_k=_TOP_K_IMAGE,
            )

        else:
            logger.warning("알 수 없는 step type: %s", step.type)
            results = []

        step.status = STATUS_DONE
        logger.info("Step 완료: %s (%d건)", step.step_id, len(results))
        return results

    except Exception as e:
        step.status = STATUS_FAILED
        logger.error("Step 실패: %s - %s", step.step_id, e)
        return []


def _collect_doc_ids(task_queue: TaskQueue, from_step_type: str) -> list[str]:
    """이미 완료된 특정 step_type의 결과에서 document_id 목록 추출."""
    ids = []
    for step in task_queue.steps:
        if step.type == from_step_type and hasattr(step, "_results"):
            ids.extend(r.get("document_id", "") for r in step._results if r.get("document_id"))
    return list(set(ids))


def _pg_to_result(pg_result: dict) -> dict:
    """PostgreSQL 결과를 표준 SearchResult 형식으로 변환."""
    rows_text = "\n".join(str(row) for row in pg_result.get("rows", []))
    return {
        "id": f"pg_{pg_result['source'].get('table', 'unknown')}",
        "score": 1.0,
        "content": rows_text,
        "keywords": pg_result.get("columns", []),
        "source_type": "quantitative",
        "source": pg_result.get("source", {}),
    }


# ── Runner ─────────────────────────────────────────────────────────────

def run(task_queue: TaskQueue) -> list[dict]:
    """Task Queue의 모든 step을 순서대로 실행 (의존성 고려).

    병렬 실행 가능한 step(dependency 없는 것)은 ThreadPoolExecutor로 동시 실행.

    Returns:
        전체 검색 결과 합산 리스트
    """
    all_results: list[dict] = []

    steps_no_dep   = [s for s in task_queue.steps if not s.dependency]
    steps_with_dep = [s for s in task_queue.steps if s.dependency]

    # 1. 의존성 없는 step 병렬 실행
    if len(steps_no_dep) > 1:
        with ThreadPoolExecutor(max_workers=len(steps_no_dep)) as ex:
            futures = {ex.submit(_run_step, s, task_queue): s for s in steps_no_dep}
            for future, step in futures.items():
                results = future.result()
                step._results = results  # type: ignore[attr-defined]
                all_results.extend(results)
    elif steps_no_dep:
        step = steps_no_dep[0]
        results = _run_step(step, task_queue)
        step._results = results  # type: ignore[attr-defined]
        all_results.extend(results)

    # 2. 의존성 있는 step 순차 실행
    done_ids = {s.step_id for s in steps_no_dep if s.status == STATUS_DONE}
    for step in steps_with_dep:
        if all(dep in done_ids for dep in step.dependency):
            results = _run_step(step, task_queue)
            step._results = results  # type: ignore[attr-defined]
            all_results.extend(results)
            if step.status == STATUS_DONE:
                done_ids.add(step.step_id)
        else:
            logger.warning(
                "Step %s 스킵: 의존 step 미완료 %s",
                step.step_id, step.dependency,
            )

    logger.info("Retriever 완료: 총 %d건 수집", len(all_results))
    return all_results
