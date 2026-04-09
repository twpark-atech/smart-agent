"""Document Parser Workflow API Router

엔드포인트:
    GET    /parser/jobs                          - 전체 job 목록 조회
    POST   /parser/jobs                          - 파일 업로드 후 워크플로우 백그라운드 실행
    GET    /parser/jobs/{job_id}                 - job 상태 조회
    DELETE /parser/jobs/{job_id}                 - 문서 삭제 (모든 저장소)
    POST   /parser/jobs/{job_id}/cancel          - 추출 중단 요청
    POST   /parser/jobs/{job_id}/run             - 워크플로우 재실행
    POST   /parser/jobs/{job_id}/reset           - 특정 step 초기화
    GET    /parser/jobs/{job_id}/sections        - 섹션 목록 조회
    GET    /parser/jobs/{job_id}/sections/{seq}  - 특정 섹션 상세 조회
"""
from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# api/config.py를 절대 경로로 로드 (parser/config.py와 이름 충돌 방지)
_api_config_path = Path(__file__).parent.parent / "config.py"
_spec = importlib.util.spec_from_file_location("api_config", _api_config_path)
_api_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_api_config)
UPLOAD_DIR: str = _api_config.UPLOAD_DIR

# parser 모듈 경로 주입
_PARSER_DIR = Path(__file__).parent.parent.parent / "parser"
if str(_PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(_PARSER_DIR))

import workflow
import workflow.job_store as job_store
import db as parser_db

router = APIRouter(prefix="/parser", tags=["parser"])

# 업로드 디렉토리 생성
Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


# ── 요청/응답 모델 ────────────────────────────────────────────────────────

class ResetRequest(BaseModel):
    step: str


class StepSummary(BaseModel):
    step_name: str
    status: str
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]


class JobStatusResponse(BaseModel):
    job_id: str
    source_path: str
    status: str
    created_at: str
    updated_at: str
    steps: list[StepSummary]


class JobSummary(BaseModel):
    job_id: str
    source_path: str
    status: str
    created_at: str
    updated_at: str


# ── 백그라운드 실행 ────────────────────────────────────────────────────────

def _run_workflow(file_path: str) -> None:
    try:
        workflow.run(file_path)
    except workflow.runner.JobCancelledError:
        pass  # 취소는 정상 흐름, job 상태는 이미 cancelled로 기록됨
    except Exception:
        pass  # 오류는 job_store에 기록됨


# ── 엔드포인트 ────────────────────────────────────────────────────────────

@router.get("/jobs", response_model=list[JobSummary], summary="전체 job 목록 조회")
def list_jobs(status: Optional[str] = None, limit: int = 100, offset: int = 0):
    """DB에 등록된 전체 job 목록을 반환합니다. status로 필터링 가능합니다."""
    job_store.init_schema()
    try:
        rows = job_store.list_jobs(status=status, limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return [
        JobSummary(
            job_id=r["job_id"],
            source_path=r["source_path"],
            status=r["status"],
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]


@router.post("/jobs", summary="파일 업로드 후 파싱 워크플로우 시작")
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    파일을 업로드하고 Document Parser Workflow를 백그라운드에서 실행합니다.

    - 동일 파일(MD5 해시 기준) 재업로드 시 완료된 step은 건너뜁니다.
    - 상태는 `GET /parser/jobs/{job_id}` 로 폴링하세요.
    """
    upload_path = Path(UPLOAD_DIR) / file.filename
    content = await file.read()
    upload_path.write_bytes(content)

    # job 등록 (멱등성: 동일 파일은 동일 job_id)
    job_store.init_schema()
    parser_db.init_schema()
    job_id = job_store.get_or_create_job(upload_path)

    background_tasks.add_task(_run_workflow, str(upload_path))

    return {"job_id": job_id, "filename": file.filename, "status": "started"}


@router.delete("/jobs/{job_id}", summary="문서 삭제")
def delete_job(job_id: str):
    """
    문서와 관련된 모든 데이터를 삭제합니다.

    - PostgreSQL: parser_documents, parser_sections, parser_blocks, parser_propositions, parser_tables, parser_table_rows
    - OpenSearch: parser_propositions, parser_documents 인덱스
    - MinIO: 변환 파일
    - Job 이력: parser_jobs, parser_job_steps

    실행 중인 job은 먼저 `/cancel`을 호출한 후 삭제하세요.
    """
    job_store.init_schema()
    result = job_store.get_job_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")

    # MinIO 파일 삭제
    try:
        from minio import Minio
        from config import (
            MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
            MINIO_BUCKET, MINIO_SECURE,
        )
        with parser_db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT minio_key FROM parser_documents WHERE document_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        if row and row[0]:
            minio_client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=MINIO_SECURE,
            )
            minio_client.remove_object(MINIO_BUCKET, row[0])
    except Exception:
        pass  # MinIO 삭제 실패는 무시하고 계속 진행

    # OpenSearch 삭제
    try:
        from section_parser.opensearch import delete_by_document as delete_propositions
        from document_integrator.opensearch import delete_by_document as delete_doc_index
        delete_propositions(job_id)
        delete_doc_index(job_id)
    except Exception:
        pass

    # PostgreSQL 문서 데이터 삭제
    try:
        parser_db.init_schema()
        parser_db.delete_document(job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PostgreSQL 삭제 실패: {e}")

    # job 이력 삭제
    try:
        job_store.delete_job(job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"job 이력 삭제 실패: {e}")

    return {"job_id": job_id, "deleted": True}


@router.post("/jobs/{job_id}/cancel", summary="추출 중단 요청")
def cancel_job(job_id: str):
    """
    실행 중인 워크플로우 추출을 중단 요청합니다.

    현재 실행 중인 step이 완료된 후 다음 step 시작 전에 중단됩니다.
    이미 완료된 step의 결과는 보존됩니다. 재개하려면 `/run`을 호출하세요.
    """
    job_store.init_schema()
    result = job_store.get_job_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")

    current_status = result["job"]["status"]
    if current_status in (job_store.STATUS_COMPLETED, job_store.STATUS_CANCELLED):
        return {"job_id": job_id, "status": current_status, "message": "이미 종료된 job입니다"}

    job_store.cancel_job(job_id)
    return {"job_id": job_id, "status": "cancelled", "message": "현재 step 완료 후 중단됩니다"}


@router.get("/jobs/{job_id}", response_model=JobStatusResponse, summary="job 상태 조회")
def get_job_status(job_id: str):
    """job 전체 상태 및 각 step의 진행 상황을 반환합니다."""
    job_store.init_schema()
    result = job_store.get_job_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")

    job = result["job"]
    steps = [
        StepSummary(
            step_name=s["step_name"],
            status=s["status"],
            started_at=str(s["started_at"]) if s.get("started_at") else None,
            completed_at=str(s["completed_at"]) if s.get("completed_at") else None,
            error=s.get("error"),
        )
        for s in result["steps"]
    ]
    return JobStatusResponse(
        job_id=job["job_id"],
        source_path=job["source_path"],
        status=job["status"],
        created_at=str(job["created_at"]),
        updated_at=str(job["updated_at"]),
        steps=steps,
    )


@router.post("/jobs/{job_id}/run", summary="워크플로우 재실행")
def run_job(job_id: str, background_tasks: BackgroundTasks):
    """기존 job의 워크플로우를 백그라운드에서 재실행합니다. 완료된 step은 건너뜁니다."""
    job_store.init_schema()
    result = job_store.get_job_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")

    source_path = result["job"]["source_path"]
    if not Path(source_path).exists():
        raise HTTPException(status_code=400, detail=f"원본 파일을 찾을 수 없습니다: {source_path}")

    background_tasks.add_task(_run_workflow, source_path)
    return {"job_id": job_id, "status": "started", "source_path": source_path}


@router.post("/jobs/{job_id}/reset", summary="특정 step 초기화 (재실행 허용)")
def reset_step(job_id: str, body: ResetRequest):
    """
    지정한 step을 초기화합니다. 다음 워크플로우 실행 시 해당 step부터 재실행됩니다.

    step 이름: `format_converter` | `index_parser` | `structurer` | `section_parser` | `document_integrator`
    """
    job_store.init_schema()
    result = job_store.get_job_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 없음")

    job_store.reset_step(job_id, body.step)
    return {"job_id": job_id, "reset_step": body.step, "message": "다음 run 시 해당 step부터 재실행됩니다"}


@router.get("/jobs/{job_id}/sections", summary="섹션 목록 조회")
def list_sections(job_id: str):
    """파싱 완료된 문서의 섹션 목록(seq, level, title, block 수)을 반환합니다."""
    parser_db.init_schema()
    try:
        with parser_db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT seq, level, title, "
                    "(SELECT COUNT(*) FROM parser_blocks b WHERE b.section_id = s.id) AS block_count "
                    "FROM parser_sections s WHERE document_id = %s ORDER BY seq",
                    (job_id,),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        raise HTTPException(status_code=404, detail=f"섹션 없음 (job_id='{job_id}'). 파싱이 완료됐는지 확인하세요.")

    return [
        {"seq": seq, "level": level, "title": title, "block_count": int(block_count)}
        for seq, level, title, block_count in rows
    ]


@router.get("/jobs/{job_id}/sections/{seq}", summary="특정 섹션 상세 조회")
def get_section(job_id: str, seq: int):
    """
    특정 섹션의 블록(text/image/table) 및 명제 목록을 반환합니다.

    `seq`는 `/parser/jobs/{job_id}/sections` 에서 확인한 seq 값입니다.
    """
    parser_db.init_schema()
    try:
        with parser_db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, section_path FROM parser_sections "
                    "WHERE document_id = %s AND seq = %s",
                    (job_id, seq),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"섹션 없음 (seq={seq})")

                sec_id, title, section_path = row

                cur.execute(
                    "SELECT seq, block_type, content, page, minio_key "
                    "FROM parser_blocks WHERE section_id = %s ORDER BY seq",
                    (sec_id,),
                )
                blocks = [
                    {
                        "seq": bseq,
                        "block_type": btype,
                        "content": content,
                        "page": page,
                        "minio_key": mkey,
                    }
                    for bseq, btype, content, page, mkey in cur.fetchall()
                ]

                cur.execute(
                    "SELECT seq, proposition, keywords "
                    "FROM parser_propositions WHERE section_id = %s ORDER BY seq",
                    (sec_id,),
                )
                propositions = [
                    {
                        "seq": pseq,
                        "proposition": prop,
                        "keywords": json.loads(kw) if isinstance(kw, str) else (kw or []),
                    }
                    for pseq, prop, kw in cur.fetchall()
                ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "job_id": job_id,
        "seq": seq,
        "title": title,
        "section_path": section_path,
        "blocks": blocks,
        "propositions": propositions,
    }


@router.get("/images/{minio_key:path}", summary="MinIO 이미지 프록시")
def get_image(minio_key: str):
    """MinIO에 저장된 이미지를 스트리밍으로 반환합니다."""
    try:
        from minio import Minio
        from config import (
            MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
            MINIO_BUCKET, MINIO_SECURE,
        )
        client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        response = client.get_object(MINIO_BUCKET, minio_key)
        ext = minio_key.rsplit(".", 1)[-1].lower()
        media_type = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp",
        }.get(ext, "application/octet-stream")
        return StreamingResponse(response, media_type=media_type)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"이미지를 찾을 수 없습니다: {e}")
