"""Workflow 진입점 - 중단/재개 가능한 Document Parser Pipeline"""
import logging
from pathlib import Path

from . import job_store, runner

logger = logging.getLogger(__name__)


def run(file_path: str | Path) -> dict:
    """파일에 대한 Document Parser Workflow를 실행.

    - 최초 실행: job 생성 후 모든 step 순차 실행
    - 재실행:    완료된 step은 DB 결과를 재사용, 미완료 step부터 재개

    Args:
        file_path: 처리할 파일 경로

    Returns:
        모든 step 결과가 누적된 context dict
    """
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {source}")

    job_store.init_schema()
    job_id = job_store.get_or_create_job(source)
    logger.info("[JOB] job_id=%s", job_id)

    return runner.run(job_id, str(source))


def reset(file_path: str | Path, step_name: str) -> None:
    """특정 step을 초기화하여 다음 run 시 재실행되도록 함."""
    source = Path(file_path)
    job_store.init_schema()
    job_id = job_store.file_hash(source)
    job_store.reset_step(job_id, step_name)


def status(file_path: str | Path) -> dict | None:
    """파일의 현재 job 상태를 조회.

    Returns:
        {"job": {...}, "steps": [...]} 또는 None (job 없음)
    """
    source = Path(file_path)
    job_store.init_schema()
    job_id = job_store.file_hash(source)
    return job_store.get_job_status(job_id)
