"""Retriever 설정 - parser/config.py를 절대 경로로 로드하여 확장"""
import importlib.util
import os
from pathlib import Path

# parser/config.py를 절대 경로로 로드 (이름 충돌 방지)
_parser_config_path = Path(__file__).parent.parent / "parser" / "config.py"
_spec = importlib.util.spec_from_file_location("parser_config", _parser_config_path)
_parser_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser_config)

# LLM
LLM_URL     = _parser_config.LLM_URL
LLM_API_KEY = _parser_config.LLM_API_KEY
LLM_MODEL   = _parser_config.LLM_MODEL
VLM_MODEL   = _parser_config.VLM_MODEL
OCR_MODEL   = _parser_config.OCR_MODEL

# Embedding
EMBEDDING_URL   = _parser_config.EMBEDDING_URL
EMBEDDING_MODEL = _parser_config.EMBEDDING_MODEL
EMBEDDING_DIM   = _parser_config.EMBEDDING_DIM

# OpenSearch
OPENSEARCH_HOST = _parser_config.OPENSEARCH_HOST
OPENSEARCH_PORT = _parser_config.OPENSEARCH_PORT

# PostgreSQL
POSTGRES_HOST     = _parser_config.POSTGRES_HOST
POSTGRES_PORT     = _parser_config.POSTGRES_PORT
POSTGRES_DB       = _parser_config.POSTGRES_DB
POSTGRES_USER     = _parser_config.POSTGRES_USER
POSTGRES_PASSWORD = _parser_config.POSTGRES_PASSWORD

# MinIO
MINIO_ENDPOINT   = _parser_config.MINIO_ENDPOINT
MINIO_ACCESS_KEY = _parser_config.MINIO_ACCESS_KEY
MINIO_SECRET_KEY = _parser_config.MINIO_SECRET_KEY
MINIO_BUCKET     = _parser_config.MINIO_BUCKET
MINIO_SECURE     = _parser_config.MINIO_SECURE

# Web Search (Serper)
SERPER_URL     = os.getenv("SERPER_URL",     "https://google.serper.dev/search")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "d41c3ccde018940a69f22f46f12b4368a605cfe7")
