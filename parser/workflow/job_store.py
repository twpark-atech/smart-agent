"""PostgreSQL 기반 Job/Step 상태 관리

schema:
    parser_jobs       - 파일 단위 job 등록 및 전체 상태
    parser_job_steps  - step 단위 실행 상태 및 결과 저장
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

# Job 전체 상태
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

DDL = """
CREATE TABLE IF NOT EXISTS parser_jobs (
    job_id      TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    file_hash   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS parser_job_steps (
    id            SERIAL PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES parser_jobs(job_id),
    step_name     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    result        JSONB,
    error         TEXT,
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    UNIQUE (job_id, step_name)
);
"""


def _connect():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def init_schema() -> None:
    """테이블이 없으면 생성."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()


def file_hash(file_path: str | Path) -> str:
    """파일 MD5 해시 (job 식별자로 사용)."""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_or_create_job(source_path: str | Path) -> str:
    """파일 해시로 기존 job을 조회하거나 새로 생성. job_id 반환."""
    path = Path(source_path)
    fhash = file_hash(path)
    job_id = fhash  # 파일 해시를 그대로 job_id로 사용 (멱등성 보장)

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO parser_jobs (job_id, source_path, file_hash, status)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE
                SET source_path = EXCLUDED.source_path,
                    updated_at  = NOW()
            """,
            (job_id, str(path), fhash, STATUS_PENDING),
        )
        conn.commit()

    return job_id


def get_step(job_id: str, step_name: str) -> dict | None:
    """step 조회. 없으면 None."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM parser_job_steps WHERE job_id = %s AND step_name = %s",
                (job_id, step_name),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def step_start(job_id: str, step_name: str) -> None:
    """step을 running 상태로 upsert."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO parser_job_steps (job_id, step_name, status, started_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (job_id, step_name) DO UPDATE
                SET status     = %s,
                    started_at = NOW(),
                    error      = NULL
            """,
            (job_id, step_name, STATUS_RUNNING, STATUS_RUNNING),
        )
        # job 전체 상태도 running으로
        cur.execute(
            "UPDATE parser_jobs SET status = %s, updated_at = NOW() WHERE job_id = %s",
            (STATUS_RUNNING, job_id),
        )
        conn.commit()


def step_complete(job_id: str, step_name: str, result: dict) -> None:
    """step을 completed 상태로 업데이트하고 결과 저장."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE parser_job_steps
            SET status = %s, result = %s, completed_at = NOW()
            WHERE job_id = %s AND step_name = %s
            """,
            (STATUS_COMPLETED, json.dumps(result, ensure_ascii=False, default=str), job_id, step_name),
        )
        conn.commit()


def step_fail(job_id: str, step_name: str, error: str) -> None:
    """step을 failed 상태로 업데이트."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE parser_job_steps
            SET status = %s, error = %s, completed_at = NOW()
            WHERE job_id = %s AND step_name = %s
            """,
            (STATUS_FAILED, error, job_id, step_name),
        )
        cur.execute(
            "UPDATE parser_jobs SET status = %s, updated_at = NOW() WHERE job_id = %s",
            (STATUS_FAILED, job_id),
        )
        conn.commit()


def job_complete(job_id: str) -> None:
    """모든 step 완료 후 job 전체를 completed로 업데이트."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE parser_jobs SET status = %s, updated_at = NOW() WHERE job_id = %s",
            (STATUS_COMPLETED, job_id),
        )
        conn.commit()


def cancel_job(job_id: str) -> None:
    """실행 중인 job을 취소 요청 상태로 변경. runner가 다음 step 전에 감지."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE parser_jobs SET status = %s, updated_at = NOW() WHERE job_id = %s",
            (STATUS_CANCELLED, job_id),
        )
        conn.commit()


def is_cancelled(job_id: str) -> bool:
    """job이 취소 요청 상태인지 확인."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM parser_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            return row is not None and row[0] == STATUS_CANCELLED


def delete_job(job_id: str) -> None:
    """job 및 step 이력을 삭제."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM parser_job_steps WHERE job_id = %s", (job_id,))
        cur.execute("DELETE FROM parser_jobs WHERE job_id = %s", (job_id,))
        conn.commit()


def reset_step(job_id: str, step_name: str) -> None:
    """특정 step 및 이후 step을 pending으로 초기화 (재실행 허용)."""
    with _connect() as conn, conn.cursor() as cur:
        # 해당 step 삭제 (upsert로 재생성됨)
        cur.execute(
            "DELETE FROM parser_job_steps WHERE job_id = %s AND step_name = %s",
            (job_id, step_name),
        )
        cur.execute(
            "UPDATE parser_jobs SET status = %s, updated_at = NOW() WHERE job_id = %s",
            (STATUS_PENDING, job_id),
        )
        conn.commit()


def list_jobs(status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """전체 job 목록 조회. status로 필터링 가능."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if status:
                cur.execute(
                    "SELECT job_id, source_path, status, created_at, updated_at "
                    "FROM parser_jobs WHERE status = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                    (status, limit, offset),
                )
            else:
                cur.execute(
                    "SELECT job_id, source_path, status, created_at, updated_at "
                    "FROM parser_jobs ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def get_job_status(job_id: str) -> dict | None:
    """job 상태 및 step 목록 조회."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM parser_jobs WHERE job_id = %s", (job_id,))
            job = cur.fetchone()
            if not job:
                return None
            cur.execute(
                "SELECT step_name, status, result, error, started_at, completed_at "
                "FROM parser_job_steps WHERE job_id = %s ORDER BY id",
                (job_id,),
            )
            steps = cur.fetchall()
            return {
                "job": dict(job),
                "steps": [dict(s) for s in steps],
            }
