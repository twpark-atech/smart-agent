import os
import re
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트의 .env 로드 (환경 변수가 이미 설정된 경우 덮어쓰지 않음)
load_dotenv(Path(__file__).parent / ".env", override=False)

# ── LLM ───────────────────────────────────────────────────────────────────
LLM_URL     = os.getenv("LLM_URL",     "http://112.163.62.170:8012/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9")
LLM_MODEL   = os.getenv("LLM_MODEL",   "Qwen2.5-3B-Instruct")
VLM_MODEL   = os.getenv("VLM_MODEL",   "Qwen3-VL-32B-Instruct-AWQ")
OCR_MODEL   = os.getenv("OCR_MODEL",   "DeepSeek-OCR")

# ── Embedding ──────────────────────────────────────────────────────────────
EMBEDDING_URL   = os.getenv("EMBEDDING_URL",   "http://112.163.62.170:8032/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
EMBEDDING_DIM   = int(os.getenv("EMBEDDING_DIM", "1024"))

# ── OpenSearch ─────────────────────────────────────────────────────────────
OPENSEARCH_HOST     = os.getenv("OPENSEARCH_HOST",     "localhost")
OPENSEARCH_PORT     = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_SSL          = os.getenv("OPENSEARCH_SSL",          "false").lower() == "true"
OPENSEARCH_VERIFY_CERTS = os.getenv("OPENSEARCH_VERIFY_CERTS", "false").lower() == "true"
OPENSEARCH_USER         = os.getenv("OPENSEARCH_USER",         "")
OPENSEARCH_PASSWORD     = os.getenv("OPENSEARCH_PASSWORD",     "")

# ── OpenSearch Index names ─────────────────────────────────────────────────
IDX_DOCUMENTS = "smart_agent_documents"
IDX_CHUNKS    = "smart_agent_chunks"

# ── PostgreSQL ─────────────────────────────────────────────────────────────
POSTGRES_HOST     = os.getenv("POSTGRES_HOST",     "localhost")
POSTGRES_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB       = os.getenv("POSTGRES_DB",       "parser")
POSTGRES_USER     = os.getenv("POSTGRES_USER",     "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

# ── MinIO ──────────────────────────────────────────────────────────────────
MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT",   "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET     = os.getenv("MINIO_BUCKET",     "documents")
MINIO_SECURE     = os.getenv("MINIO_SECURE",     "false").lower() == "true"

# ── Web Search (Serper) ────────────────────────────────────────────────────
SERPER_URL     = os.getenv("SERPER_URL",     "https://google.serper.dev/search")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "d41c3ccde018940a69f22f46f12b4368a605cfe7")

# ── API ────────────────────────────────────────────────────────────────────
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/smart-agent-uploads")

# ── Chunking ───────────────────────────────────────────────────────────────
MAX_TOKENS = 2048
MIN_TOKENS = 512

# ── Taxonomy ───────────────────────────────────────────────────────────────
DOMAIN_TYPE_PATH   = os.getenv("DOMAIN_TYPE_PATH",   "/app/domain_type.md")
DOCUMENT_TYPE_PATH = os.getenv("DOCUMENT_TYPE_PATH", "/app/document_type.md")


def _parse_taxonomy(path: str) -> tuple[list[str], list[str]]:
    """md 파일에서 (대분류 목록, 소분류 목록) 파싱.
    ## 줄 → 대분류, ### 줄 → 소분류. 번호 접두어 제거.
    """
    categories, subcategories = [], []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m2 = re.match(r"^##\s+\d+[\.\-]?\s*(.+)", line)
                m3 = re.match(r"^###\s+[\d\-]+[\.\-]?\s*(.+)", line)
                if m2:
                    categories.append(m2.group(1).strip())
                elif m3:
                    subcategories.append(m3.group(1).strip())
    except FileNotFoundError:
        print(f"[WARN] taxonomy 파일 없음: {path}")
    return categories, subcategories


def _build_taxonomy_prompt(path: str) -> str:
    """프롬프트 주입용 압축 텍스트 생성.
    대분류별로 소분류를 묶어서 한 줄로 출력. (document_type용)
    """
    lines: list[str] = []
    current_cat: str | None = None
    current_subs: list[str] = []

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m2 = re.match(r"^##\s+\d+[\.\-]?\s*(.+)", line)
                m3 = re.match(r"^###\s+[\d\-]+[\.\-]?\s*(.+)", line)
                if m2:
                    if current_cat and current_subs:
                        lines.append(f"{current_cat}: {', '.join(current_subs)}")
                    elif current_cat:
                        lines.append(current_cat)
                    current_cat = m2.group(1).strip()
                    current_subs = []
                elif m3 and current_cat:
                    current_subs.append(m3.group(1).strip())
        if current_cat:
            if current_subs:
                lines.append(f"{current_cat}: {', '.join(current_subs)}")
            else:
                lines.append(current_cat)
    except FileNotFoundError:
        pass

    return "\n".join(lines)


def _build_domain_categories_prompt(path: str) -> str:
    """프롬프트 주입용 도메인 대분류 목록만 출력."""
    categories: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m2 = re.match(r"^##\s+\d+[\.\-]?\s*(.+)", line)
                if m2:
                    categories.append(m2.group(1).strip())
    except FileNotFoundError:
        print(f"[WARN] taxonomy 파일 없음: {path}")
    return "\n".join(f"- {c}" for c in categories)


# 모듈 로드 시 1회 파싱
DOMAIN_TAXONOMY  = _build_domain_categories_prompt(DOMAIN_TYPE_PATH)
DOCUMENT_TAXONOMY = _build_taxonomy_prompt(DOCUMENT_TYPE_PATH)

# 대분류 목록 (유효성 검증용)
DOMAIN_CATEGORIES, _          = _parse_taxonomy(DOMAIN_TYPE_PATH)
_,                 DOCUMENT_SUBCATEGORIES = _parse_taxonomy(DOCUMENT_TYPE_PATH)
