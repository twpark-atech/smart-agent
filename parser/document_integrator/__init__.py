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

    # xlsx/csv 등 명제가 없는 경우 테이블 데이터에서 가상 명제 생성
    if not propositions_by_section:
        propositions_by_section = _load_from_tables(job_id)

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


def _load_from_tables(document_id: str) -> list[dict]:
    """명제가 없는 경우(xlsx/csv) parser_tables + parser_table_rows에서 가상 명제 생성."""
    import db
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.sheet_name, t.headers, t.row_count, t.table_index
                FROM parser_tables t
                WHERE t.document_id = %s
                ORDER BY t.table_index
                """,
                (document_id,),
            )
            tables = cur.fetchall()

    if not tables:
        return []

    result = []
    import db, json as _json
    with db.connect() as conn:
        with conn.cursor() as cur:
            for table_id, sheet_name, headers_json, row_count, table_index in tables:
                headers = headers_json if isinstance(headers_json, list) else _json.loads(headers_json)

                # 상위 5행 샘플
                cur.execute(
                    """
                    SELECT row_data FROM parser_table_rows
                    WHERE table_id = %s ORDER BY row_index LIMIT 5
                    """,
                    (table_id,),
                )
                sample_rows = [r[0] for r in cur.fetchall()]

                sheet_label = sheet_name or f"테이블{(table_index or 0) + 1}"
                propositions = [
                    f"컬럼: {', '.join(headers)}",
                    f"총 {row_count}개 행",
                ]
                for i, row in enumerate(sample_rows):
                    row_dict = row if isinstance(row, dict) else _json.loads(row)
                    propositions.append(
                        f"샘플 행{i + 1}: " + ", ".join(
                            f"{k}={v}" for k, v in list(row_dict.items())[:5]
                        )
                    )

                result.append({
                    "section_path": f"{sheet_label} > 테이블{(table_index or 0) + 1}",
                    "propositions": propositions,
                    "keywords": headers,
                })

    return result


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


_GENERIC_SECTION_NAMES = {
    "문서 헤더", "헤더", "개요", "서론", "목차", "서문", "소개", "본문",
    "introduction", "header", "overview", "preface", "contents", "table of contents",
}


def _extract_doc_name(source_path: str, propositions_by_section: list[dict]) -> str:
    """문서명을 추출.

    우선순위:
    1. 최상위 섹션 경로에서 실제 제목으로 보이는 값 (generic 이름 제외)
    2. 파일명(확장자 제거)
    """
    if propositions_by_section:
        for section in propositions_by_section:
            section_path = section.get("section_path", "")
            if not section_path:
                continue
            top_section = section_path.split(">")[0].strip()
            if top_section and top_section.lower() not in _GENERIC_SECTION_NAMES:
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
