import os

# LLM
LLM_URL = os.getenv("LLM_URL", "http://112.163.62.170:8012/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen2.5-3B-Instruct")
VLM_MODEL = os.getenv("VLM_MODEL", "Qwen3-VL-32B-Instruct-AWQ")
OCR_MODEL = os.getenv("OCR_MODEL", "DeepSeek-OCR")

# Embedding
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "http://112.163.62.170:8032/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# OpenSearch
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))

# PostgreSQL
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "parser")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "documents")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Web Search (Serper)
SERPER_URL     = os.getenv("SERPER_URL",     "https://google.serper.dev/search")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "d41c3ccde018940a69f22f46f12b4368a605cfe7")