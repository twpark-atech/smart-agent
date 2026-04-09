"""PostgreSQL 정량적 검색 Tool (SELECT 전용)"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from datetime import datetime

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

# SELECT만 허용 (DDL/DML 차단)
_UNSAFE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|MERGE)\b",
    re.IGNORECASE,
)


def _connect():
    return psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD,
    )


def search(sql_query: str, params: list | None = None) -> dict:
    """SQL 쿼리를 실행하고 결과를 반환.

    Args:
        sql_query: Planner가 생성한 SELECT 쿼리
        params: 바인딩 파라미터 (SQL Injection 방지)

    Returns:
        {
            "status": "success" | "error",
            "columns": [...],
            "rows": [...],
            "row_count": int,
            "source": {"table": str, "document_id": str | None},
            "error": str | None,
        }
    """
    if _UNSAFE_PATTERN.search(sql_query):
        return {
            "status": "error",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "source": {"table": "", "document_id": None},
            "error": "SELECT 쿼리만 허용됩니다.",
        }

    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql_query, params or [])
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description] if cur.description else []

        table = _extract_table(sql_query)
        return {
            "status": "success",
            "columns": columns,
            "rows": [dict(row) for row in rows],
            "row_count": len(rows),
            "source": {
                "table": table,
                "document_id": None,
                "accessed_at": datetime.now().isoformat(),
            },
            "error": None,
        }

    except Exception as e:
        return {
            "status": "error",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "source": {"table": "", "document_id": None},
            "error": str(e),
        }


def _extract_table(sql: str) -> str:
    """FROM 절에서 첫 번째 테이블명 추출 (단순 파싱)."""
    match = re.search(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
    return match.group(1) if match else ""
