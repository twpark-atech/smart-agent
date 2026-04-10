"""Section Parser - 섹션별 명제/키워드 추출 + 임베딩 + OpenSearch 적재"""
from __future__ import annotations

from .llm import extract_propositions_and_keywords
from .embedder import embed
from .opensearch import init_index, index_propositions, delete_by_document


def run(job_id: str, structurer_result: dict) -> dict:
    """섹션별 명제/키워드 추출 → 임베딩 → OpenSearch 적재 → PostgreSQL 저장.

    Args:
        job_id:            워크플로우 job_id (= document_id)
        structurer_result: structurer step 결과 (sections 요약 포함)

    Returns:
        {
            "total_sections": int,
            "processed_sections": int,
            "total_propositions": int,
            "skipped_sections": [섹션명, ...],   # 내용 없어 건너뜀
        }
    """
    original_ext = structurer_result.get("original_ext", "")
    if original_ext in (".xlsx", ".csv"):
        return _run_table_index(job_id, structurer_result)

    import db

    db.init_schema()
    init_index()

    doc_type        = structurer_result.get("doc_type", "")
    domain_category = structurer_result.get("domain_category", "")

    # PostgreSQL에서 섹션 + 블록 조회
    sections = _load_sections_from_db(job_id)
    total = len(sections)
    processed = 0
    skipped = []

    # 기존 OpenSearch 데이터 삭제 (재실행 멱등성)
    delete_by_document(job_id)

    # PostgreSQL 명제 테이블 초기화
    _reset_propositions(job_id)

    os_docs = []

    for section in sections:
        content = _section_content_text(section)
        if not content.strip():
            skipped.append(section["title"])
            continue

        # 명제/키워드 추출
        extracted = extract_propositions_and_keywords(section["title"], content)
        propositions = extracted["propositions"]
        keywords     = extracted["keywords"]

        if not propositions:
            skipped.append(section["title"])
            continue

        # 임베딩
        vectors = embed(propositions)

        # OpenSearch 문서 준비
        for prop, vec in zip(propositions, vectors):
            os_docs.append({
                "document_id":     job_id,
                "section_id":      section["id"],
                "section_path":    section["section_path"],
                "doc_type":        doc_type,
                "domain_category": domain_category,
                "proposition":     prop,
                "keywords":        keywords,
                "embedding":       vec,
            })

        # PostgreSQL 명제 저장
        _save_propositions_to_db(job_id, section["id"], propositions, keywords)

        processed += 1
        print(f"  [OK] {section['title']} → 명제 {len(propositions)}개")

    # OpenSearch bulk 색인
    indexed = index_propositions(os_docs) if os_docs else 0

    return {
        "total_sections":     total,
        "processed_sections": processed,
        "total_propositions": len(os_docs),
        "opensearch_indexed": indexed,
        "skipped_sections":   skipped,
    }


# ── DB 헬퍼 ──────────────────────────────────────────────

def _load_sections_from_db(document_id: str) -> list[dict]:
    """parser_sections + parser_blocks를 섹션별로 로드."""
    import db
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, level, section_path "
                "FROM parser_sections WHERE document_id = %s ORDER BY seq",
                (document_id,),
            )
            sections = [
                {"id": r[0], "title": r[1], "level": r[2],
                 "section_path": r[3], "blocks": []}
                for r in cur.fetchall()
            ]
            for sec in sections:
                cur.execute(
                    "SELECT block_type, content, table_json "
                    "FROM parser_blocks WHERE section_id = %s ORDER BY seq",
                    (sec["id"],),
                )
                sec["blocks"] = [
                    {"block_type": r[0], "content": r[1], "table_json": r[2]}
                    for r in cur.fetchall()
                ]
    return sections


def _section_content_text(section: dict) -> str:
    """섹션 블록에서 텍스트·이미지 설명·테이블 내용을 하나의 문자열로 결합."""
    parts = []
    for block in section["blocks"]:
        content = block.get("content") or ""
        if not content.strip():
            continue
        if block["block_type"] in ("text", "table"):
            parts.append(content.strip())
        elif block["block_type"] == "image":
            # VLM이 생성한 이미지 설명을 원문에 포함
            parts.append(content.strip())
    return "\n\n".join(parts)


def _reset_propositions(document_id: str) -> None:
    """parser_propositions 테이블의 기존 데이터 삭제."""
    import db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM parser_propositions WHERE document_id = %s",
            (document_id,),
        )
        conn.commit()


def _save_propositions_to_db(
    document_id: str,
    section_id: int,
    propositions: list[str],
    keywords: list[str],
) -> None:
    import db, json
    with db.connect() as conn, conn.cursor() as cur:
        for seq, prop in enumerate(propositions):
            cur.execute(
                """
                INSERT INTO parser_propositions
                    (document_id, section_id, proposition, keywords, seq)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (document_id, section_id, prop, json.dumps(keywords, ensure_ascii=False), seq),
            )
        conn.commit()


def _run_table_index(job_id: str, structurer_result: dict) -> dict:
    """xlsx/csv: 테이블 행 전체를 OpenSearch 명제로 색인.

    각 행을 '시트명: col1=val1, col2=val2, ...' 형태의 명제로 변환하여
    임베딩 + OpenSearch(parser_propositions) + PostgreSQL(parser_propositions) 에 저장.
    이를 통해 벡터/키워드 검색 경로에서도 특정 셀 값을 검색할 수 있게 한다.
    """
    import db, json as _json

    db.init_schema()
    init_index()

    doc_type        = structurer_result.get("doc_type", "")
    domain_category = structurer_result.get("domain_category", "")

    # 기존 OpenSearch 명제 삭제 (재실행 멱등성)
    delete_by_document(job_id)
    _reset_propositions(job_id)

    # parser_tables + parser_table_rows + parser_sections JOIN 조회
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.section_id, t.sheet_name, t.headers
            FROM parser_tables t
            WHERE t.document_id = %s
            ORDER BY t.table_index
            """,
            (job_id,),
        )
        tables = cur.fetchall()

    if not tables:
        return {"total_sections": 0, "processed_sections": 0, "total_propositions": 0, "skipped": True}

    # section_id별 section_path 조회
    section_paths: dict[int, str] = {}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, section_path FROM parser_sections WHERE document_id = %s",
            (job_id,),
        )
        for sid, spath in cur.fetchall():
            section_paths[sid] = spath or ""

    os_docs: list[dict] = []
    total_propositions = 0

    for table_id, section_id, sheet_name, headers_json in tables:
        headers = headers_json if isinstance(headers_json, list) else _json.loads(headers_json or "[]")
        sheet_label = sheet_name or f"테이블{table_id}"
        section_path = section_paths.get(section_id, sheet_label)

        # 해당 테이블의 모든 행 조회
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT row_index, row_data FROM parser_table_rows "
                "WHERE table_id = %s ORDER BY row_index",
                (table_id,),
            )
            rows = cur.fetchall()

        propositions: list[str] = []
        for row_index, row_data in rows:
            if isinstance(row_data, str):
                row_dict = _json.loads(row_data)
            else:
                row_dict = row_data or {}

            # 빈 행 건너뜀
            if not any(str(v).strip() for v in row_dict.values()):
                continue

            # '시트명: col1=val1, col2=val2, ...' 형태의 명제 생성
            pairs = ", ".join(
                f"{k}={v}" for k, v in row_dict.items() if str(v).strip()
            )
            prop = f"{sheet_label}: {pairs}"
            propositions.append(prop)

        if not propositions:
            continue

        # 임베딩
        vectors = embed(propositions)

        for prop, vec in zip(propositions, vectors):
            os_docs.append({
                "document_id":     job_id,
                "section_id":      section_id,
                "section_path":    section_path,
                "doc_type":        doc_type,
                "domain_category": domain_category,
                "proposition":     prop,
                "keywords":        headers,
                "embedding":       vec,
            })

        # PostgreSQL 명제 저장
        _save_propositions_to_db(job_id, section_id, propositions, headers)

        total_propositions += len(propositions)
        print(f"  [OK] 시트 '{sheet_label}' → 행 명제 {len(propositions)}개")

    # OpenSearch bulk 색인
    indexed = index_propositions(os_docs) if os_docs else 0

    return {
        "total_sections":     len(tables),
        "processed_sections": len(tables),
        "total_propositions": total_propositions,
        "opensearch_indexed": indexed,
    }
