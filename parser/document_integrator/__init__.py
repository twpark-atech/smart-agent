"""Document Integrator - 문서 전체 요약/키워드 생성 + 임베딩 + 적재"""
from __future__ import annotations
from pathlib import Path

from .llm import generate_summary_and_keywords
from .opensearch import init_index, index_document


def run(job_id: str, structurer_result: dict) -> dict:
    """섹션별 명제를 종합해 문서 요약/키워드 생성 → 임베딩 → OpenSearch + PostgreSQL 적재.

    Args:
        job_id:            워크플로우 job_id
        structurer_result: structurer step 결과 (doc_type, domain_category 포함)

    Returns:
        {
            "summary": str,
            "keywords": [str],
            "embedding_dim": int,
            "opensearch_indexed": bool,
        }
    """
    from section_parser.embedder import embed_one
    import db

    db.init_schema()
    init_index()

    doc_type        = structurer_result.get("doc_type", "")
    domain_category = structurer_result.get("domain_category", "")

    # PostgreSQL에서 섹션별 명제+키워드 로드
    propositions_by_section = _load_propositions(job_id)

    if not propositions_by_section:
        return {"summary": "", "keywords": [], "embedding_dim": 0, "opensearch_indexed": False}

    # 요약 + 키워드 생성
    result = generate_summary_and_keywords(propositions_by_section, doc_type)
    summary  = result["summary"]
    keywords = result["keywords"]

    # 요약문 임베딩
    embedding = embed_one(summary) if summary else []

    # PostgreSQL parser_documents 업데이트
    _update_document(job_id, summary, keywords)

    # OpenSearch 색인
    doc_row = _get_document_meta(job_id)
    source_path = doc_row.get("source_path", "")
    doc_name = _extract_doc_name(source_path, propositions_by_section)
    index_document({
        "document_id":     job_id,
        "source_path":     source_path,
        "doc_name":        doc_name,
        "doc_type":        doc_type,
        "domain_category": domain_category,
        "summary":         summary,
        "keywords":        keywords,
        "embedding":       embedding,
    })

    return {
        "summary":           summary,
        "keywords":          keywords,
        "embedding_dim":     len(embedding),
        "opensearch_indexed": True,
    }


# ── DB 헬퍼 ──────────────────────────────────────────────

def _load_propositions(document_id: str) -> list[dict]:
    """섹션별 명제+키워드를 로드."""
    import db
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.section_path,
                       array_agg(p.proposition ORDER BY p.seq) AS propositions,
                       array_agg(DISTINCT k) FILTER (WHERE k IS NOT NULL) AS keywords
                FROM parser_propositions p
                JOIN parser_sections s ON s.id = p.section_id
                LEFT JOIN LATERAL jsonb_array_elements_text(p.keywords) AS k ON true
                WHERE p.document_id = %s
                GROUP BY s.id, s.section_path, s.seq
                ORDER BY s.seq
                """,
                (document_id,),
            )
            return [
                {
                    "section_path": row[0],
                    "propositions": row[1] or [],
                    "keywords":     row[2] or [],
                }
                for row in cur.fetchall()
            ]


def _update_document(document_id: str, summary: str, keywords: list[str]) -> None:
    import db, json
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE parser_documents
                ADD COLUMN IF NOT EXISTS summary  TEXT,
                ADD COLUMN IF NOT EXISTS keywords JSONB
            """,
        )
        cur.execute(
            "UPDATE parser_documents SET summary = %s, keywords = %s WHERE document_id = %s",
            (summary, json.dumps(keywords, ensure_ascii=False), document_id),
        )
        conn.commit()


def _extract_doc_name(source_path: str, propositions_by_section: list[dict]) -> str:
    """문서명을 추출. 최상위 섹션 경로(목차 첫 번째 항목)를 우선하고, 없으면 파일명 사용."""
    # 최상위 섹션 경로에서 문서 제목 추출 (예: "1장 서론 > 1.1 배경" → "1장 서론")
    if propositions_by_section:
        first_path = propositions_by_section[0].get("section_path", "")
        if first_path:
            top_section = first_path.split(">")[0].strip()
            if top_section:
                return top_section

    # 폴백: 파일명에서 확장자 제거
    if source_path:
        return Path(source_path).stem
    return ""


def _get_document_meta(document_id: str) -> dict:
    import db
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_path, doc_type, domain_category FROM parser_documents WHERE document_id = %s",
                (document_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {"source_path": row[0], "doc_type": row[1], "domain_category": row[2]}
