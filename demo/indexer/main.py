"""인덱서 API 서버 — port 8001

Endpoints:
  POST /documents          파일 업로드 + 인덱싱 시작
  GET  /documents          인덱싱된 문서 목록
  GET  /documents/{id}     문서 상세 + 청크 수
  GET  /documents/{id}/status  인덱싱 진행 상태
  DELETE /documents/{id}   문서 삭제
  GET  /health             서버 상태
"""
import os
import asyncio
import aiofiles
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from shared import opensearch_client as os_client
from indexer import pipeline

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Smart Agent Indexer", version="1.0.0")

# 인덱싱 상태 관리 (demo용 in-memory)
_status_store: dict[str, dict] = {}


@app.on_event("startup")
async def startup():
    await asyncio.to_thread(os_client.ensure_indices)


# ── 헬스체크 ──────────────────────────────────────────────

@app.get("/health")
async def health():
    stats = await asyncio.to_thread(os_client.get_index_stats)
    return {"status": "ok", "index_stats": stats}


# ── 문서 업로드 + 인덱싱 ──────────────────────────────────

@app.post("/documents", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    allowed = {".pdf", ".docx", ".hwpx", ".md", ".txt"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"지원 포맷: {', '.join(allowed)}")

    # 파일 저장
    save_path = UPLOAD_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{file.filename}"
    async with aiofiles.open(save_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # 상태 초기화
    status: dict = {
        "status": "queued",
        "step": "대기 중",
        "file_name": file.filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunk_count": 0,
    }
    doc_id_placeholder = str(save_path)  # 임시 키 (pipeline이 실제 doc_id 설정)
    _status_store[doc_id_placeholder] = status

    # 백그라운드 인덱싱
    async def _run():
        try:
            doc_id = await pipeline.run(str(save_path), file.filename, status)
            # 실제 doc_id로 키 이동
            _status_store[doc_id] = status
            _status_store.pop(doc_id_placeholder, None)
        except Exception as e:
            status["status"] = "failed"
            status["step"] = str(e)

    background_tasks.add_task(_run)

    return {
        "message": "인덱싱이 시작되었습니다. /documents/{document_id}/status 로 진행 상황을 확인하세요.",
        "file_name": file.filename,
        "tip": "document_id는 인덱싱 완료 후 /documents 에서 확인 가능합니다.",
    }


# ── 문서 목록 ─────────────────────────────────────────────

@app.get("/documents")
async def list_documents():
    docs = await os_client.list_documents()
    # 청크 수를 병렬로 조회
    chunk_counts = await asyncio.gather(
        *[os_client.count_chunks(d["document_id"]) for d in docs],
        return_exceptions=True,
    )
    for doc, cnt in zip(docs, chunk_counts):
        doc["chunk_count"] = cnt if isinstance(cnt, int) else 0
    return {"count": len(docs), "documents": docs}


# ── 문서 상세 ─────────────────────────────────────────────

@app.get("/documents/{document_id}")
async def get_document(document_id: str):
    doc = await os_client.get_document(document_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    chunk_count = await os_client.count_chunks(document_id)
    return {**doc, "chunk_count": chunk_count}


# ── 인덱싱 상태 ───────────────────────────────────────────

@app.get("/documents/{document_id}/status")
async def get_status(document_id: str):
    if document_id in _status_store:
        return _status_store[document_id]
    doc = await os_client.get_document(document_id)
    if doc:
        return {"status": "completed", "document_id": document_id}
    raise HTTPException(404, "문서 또는 진행 상태를 찾을 수 없습니다.")


# ── 문서 삭제 ─────────────────────────────────────────────

@app.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    doc = await os_client.get_document(document_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    await os_client.delete_document(document_id)
    _status_store.pop(document_id, None)
    return {"message": f"문서 {document_id} 삭제 완료"}
