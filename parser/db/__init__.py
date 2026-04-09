"""PostgreSQL 적재 모듈 - 문서/섹션/블록 메타데이터"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

DDL = """
CREATE TABLE IF NOT EXISTS parser_documents (
    document_id     TEXT PRIMARY KEY,       -- job_id와 동일
    source_path     TEXT NOT NULL,
    original_ext    TEXT,
    doc_type        TEXT,
    domain_category TEXT,
    minio_bucket    TEXT,
    minio_key       TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS parser_sections (
    id              SERIAL PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES parser_documents(document_id),
    title           TEXT NOT NULL,
    level           INT,
    section_path    TEXT,
    domain_category TEXT,
    seq             INT                     -- 문서 내 순서
);

CREATE TABLE IF NOT EXISTS parser_propositions (
    id              SERIAL PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES parser_documents(document_id),
    section_id      INT REFERENCES parser_sections(id),
    proposition     TEXT NOT NULL,
    keywords        JSONB,
    seq             INT
);

CREATE TABLE IF NOT EXISTS parser_blocks (
    id              SERIAL PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES parser_documents(document_id),
    section_id      INT REFERENCES parser_sections(id),
    block_type      TEXT NOT NULL,          -- text / image / table
    content         TEXT,
    page            INT,
    bbox            JSONB,
    minio_key       TEXT,                   -- 이미지 원본
    table_json      JSONB,                  -- 표 구조 (raw 전체)
    seq             INT                     -- 섹션 내 순서
);

CREATE TABLE IF NOT EXISTS parser_tables (
    id              SERIAL PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES parser_documents(document_id),
    block_id        INT REFERENCES parser_blocks(id),
    section_id      INT REFERENCES parser_sections(id),
    page            INT,
    headers         JSONB NOT NULL,         -- ["col1", "col2", ...]
    row_count       INT NOT NULL DEFAULT 0,
    table_index     INT                     -- 문서 내 표 순서 (0-indexed)
);

CREATE TABLE IF NOT EXISTS parser_table_rows (
    id              SERIAL PRIMARY KEY,
    table_id        INT NOT NULL REFERENCES parser_tables(id),
    row_index       INT NOT NULL,           -- 0-indexed (헤더 제외)
    row_data        JSONB NOT NULL          -- {"col1": "val1", "col2": "val2"}
);
"""


def connect():
    return psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD,
    )


def init_schema() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()


# ── Document ──────────────────────────────────────────────

def get_job_id(source_path: str) -> str | None:
    """파일 해시로 document_id(=job_id) 반환. 파일이 없으면 None."""
    from pathlib import Path
    p = Path(source_path)
    if not p.exists():
        return None
    from workflow.job_store import file_hash
    return file_hash(p)


def upsert_document(
    document_id: str,
    source_path: str,
    original_ext: str,
    doc_type: str,
    domain_category: str,
    minio_bucket: str = "",
    minio_key: str = "",
) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO parser_documents
                (document_id, source_path, original_ext, doc_type, domain_category, minio_bucket, minio_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET
                doc_type        = EXCLUDED.doc_type,
                domain_category = EXCLUDED.domain_category,
                minio_bucket    = EXCLUDED.minio_bucket,
                minio_key       = EXCLUDED.minio_key
            """,
            (document_id, source_path, original_ext, doc_type, domain_category, minio_bucket, minio_key),
        )
        conn.commit()


# ── Sections + Blocks ─────────────────────────────────────

def save_sections(document_id: str, sections: list[dict]) -> None:
    """섹션 목록과 하위 블록을 저장. 기존 데이터는 삭제 후 재적재."""
    with connect() as conn, conn.cursor() as cur:
        # 기존 데이터 삭제 (재실행 멱등성)
        # FK 순서: propositions → table_rows → tables → blocks → sections
        cur.execute("DELETE FROM parser_propositions WHERE document_id = %s", (document_id,))
        cur.execute(
            "DELETE FROM parser_table_rows WHERE table_id IN "
            "(SELECT id FROM parser_tables WHERE document_id = %s)",
            (document_id,),
        )
        cur.execute("DELETE FROM parser_tables WHERE document_id = %s", (document_id,))
        cur.execute("DELETE FROM parser_blocks WHERE document_id = %s", (document_id,))
        cur.execute("DELETE FROM parser_sections WHERE document_id = %s", (document_id,))
        conn.commit()

        table_index = 0  # 문서 전체 표 순서 카운터

        for seq, section in enumerate(sections):
            cur.execute(
                """
                INSERT INTO parser_sections (document_id, title, level, section_path, domain_category, seq)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (document_id, section["title"], section["level"],
                 section["section_path"], section["domain_category"], seq),
            )
            section_id = cur.fetchone()[0]

            for bseq, block in enumerate(section.get("blocks", [])):
                table_json = block.get("table_json")
                cur.execute(
                    """
                    INSERT INTO parser_blocks
                        (document_id, section_id, block_type, content, page, bbox, minio_key, table_json, seq)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        document_id,
                        section_id,
                        block.get("block_type"),
                        block.get("content"),
                        block.get("page"),
                        json.dumps(block.get("bbox")) if block.get("bbox") else None,
                        block.get("minio_key"),
                        json.dumps(table_json) if table_json else None,
                        bseq,
                    ),
                )
                block_id = cur.fetchone()[0]

                # 표 블록은 행 단위로 정규화 적재
                if block.get("block_type") == "table" and table_json:
                    _insert_table_rows(
                        cur, document_id, block_id, section_id,
                        block.get("page"), table_json, table_index,
                    )
                    table_index += 1

        conn.commit()


def delete_document(document_id: str) -> None:
    """문서 관련 PostgreSQL 데이터 전체 삭제 (FK 순서 준수)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM parser_propositions WHERE document_id = %s", (document_id,))
        cur.execute(
            "DELETE FROM parser_table_rows WHERE table_id IN "
            "(SELECT id FROM parser_tables WHERE document_id = %s)",
            (document_id,),
        )
        cur.execute("DELETE FROM parser_tables WHERE document_id = %s", (document_id,))
        cur.execute("DELETE FROM parser_blocks WHERE document_id = %s", (document_id,))
        cur.execute("DELETE FROM parser_sections WHERE document_id = %s", (document_id,))
        cur.execute("DELETE FROM parser_documents WHERE document_id = %s", (document_id,))
        conn.commit()


def _insert_table_rows(
    cur,
    document_id: str,
    block_id: int,
    section_id: int,
    page: int | None,
    table_json: list[dict],
    table_index: int,
) -> None:
    """표 데이터를 parser_tables / parser_table_rows에 행 단위로 적재."""
    if not table_json:
        return

    headers = list(table_json[0].keys())

    cur.execute(
        """
        INSERT INTO parser_tables
            (document_id, block_id, section_id, page, headers, row_count, table_index)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (document_id, block_id, section_id, page,
         json.dumps(headers, ensure_ascii=False),
         len(table_json), table_index),
    )
    table_id = cur.fetchone()[0]

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO parser_table_rows (table_id, row_index, row_data) VALUES %s",
        [
            (table_id, row_idx, json.dumps(row, ensure_ascii=False))
            for row_idx, row in enumerate(table_json)
        ],
    )
