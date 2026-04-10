"""Smart Agent 통합 API

Document Parser Workflow + Retrieval Multi-Agent를 단일 FastAPI 서버로 노출합니다.

실행:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
    (smart-agent 루트에서 실행)

또는:
    cd api && uvicorn main:app --host 0.0.0.0 --port 8000 --reload

UI:      http://localhost:8000/ui
Swagger: http://localhost:8000/docs
"""
import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# 루트 및 api/ parser/ retriever/ 경로를 sys.path에 추가
_ROOT = Path(__file__).parent.parent
_API_DIR = Path(__file__).parent
for _p in (_ROOT, _API_DIR, _ROOT / "parser", _ROOT / "retriever"):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

from log_config import setup_logging

setup_logging(suppress_access_log=True)

from routers.parser import router as parser_router
from routers.retriever import router as retriever_router

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Smart Agent API",
    description=(
        "Document Parser Workflow와 Retrieval Multi-Agent를 통합한 API입니다.\n\n"
        "- **Parser**: 문서 업로드 → 단계별 파싱 워크플로우 실행 (중단/재개 지원)\n"
        "- **Retriever**: 사용자 질의 → Multi-Agent RAG 답변 생성"
    ),
    version="1.0.0",
)

app.include_router(parser_router)
app.include_router(retriever_router)

# Static 파일 서빙 (UI)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/ui", include_in_schema=False)
@app.get("/ui/{rest_of_path:path}", include_in_schema=False)
def serve_ui(rest_of_path: str = ""):
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
