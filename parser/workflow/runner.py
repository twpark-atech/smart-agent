"""체크포인트 기반 step 실행기

실행 규칙:
- completed  → 결과를 DB에서 로드하고 건너뜀
- running    → 이전 실행이 중단된 것으로 간주, 재실행
- failed     → 재실행
- pending/없음 → 최초 실행
- cancelled  → 다음 step 실행 전 감지하여 중단
"""
import traceback
from pathlib import Path

from . import job_store


class JobCancelledError(Exception):
    """사용자 요청으로 job이 취소됨."""

# ── Step 정의 ─────────────────────────────────────────────
# (step_name, callable) 순서 보장 리스트
# callable 시그니처: (context: dict) -> dict
#   context: 이전 step 결과 누적 dict
#   return:  해당 step의 결과 dict

def _step_format_converter(context: dict) -> dict:
    from format_converter import run
    return run(context["source_path"])


def _step_index_parser(context: dict) -> dict:
    from index_parser import run
    result = run(context["format_converter"])
    return result.to_dict()


def _step_structurer(context: dict) -> dict:
    from structurer import run
    result = run(context["job_id"], context["format_converter"], context["index_parser"])
    # 섹션 데이터는 PostgreSQL에 적재됐으므로 result에는 요약만 저장
    d = result.to_dict()
    d["sections"] = [
        {"title": s["title"], "level": s["level"],
         "section_path": s["section_path"], "block_count": len(s["blocks"])}
        for s in d["sections"]
    ]
    return d


def _step_section_parser(context: dict) -> dict:
    from section_parser import run
    return run(context["job_id"], context["structurer"])


def _step_document_integrator(context: dict) -> dict:
    from document_integrator import run
    return run(context["job_id"], context["structurer"])


STEPS: list[tuple[str, callable]] = [
    ("format_converter",    _step_format_converter),
    ("index_parser",        _step_index_parser),
    ("structurer",          _step_structurer),
    ("section_parser",      _step_section_parser),
    ("document_integrator", _step_document_integrator),
]


# ── Runner ────────────────────────────────────────────────

def run(job_id: str, source_path: str) -> dict:
    """step 순서대로 실행. 완료된 step은 DB 결과를 재사용.

    Returns:
        모든 step 결과가 누적된 context dict
    """
    context: dict = {"source_path": source_path, "job_id": job_id}

    for step_name, step_fn in STEPS:
        # 취소 요청 확인 (step 시작 전)
        if job_store.is_cancelled(job_id):
            print(f"[CANCELLED] job={job_id}, 중단 위치={step_name} 이전")
            raise JobCancelledError(f"job '{job_id}' 취소됨")

        row = job_store.get_step(job_id, step_name)

        if row and row["status"] == job_store.STATUS_COMPLETED:
            # 이미 완료 → DB 결과 재사용
            print(f"[SKIP] {step_name} (already completed)")
            context[step_name] = row["result"] or {}
            continue

        # 미완료(없음 / running / failed) → 실행
        print(f"[RUN ] {step_name}")
        job_store.step_start(job_id, step_name)
        try:
            result = step_fn(context)
            job_store.step_complete(job_id, step_name, result)
            context[step_name] = result
            print(f"[DONE] {step_name}")
        except JobCancelledError:
            raise
        except Exception as e:
            error_msg = traceback.format_exc()
            job_store.step_fail(job_id, step_name, error_msg)
            print(f"[FAIL] {step_name}: {e}")
            raise

    job_store.job_complete(job_id)
    return context
