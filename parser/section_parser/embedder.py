"""명제 임베딩 - Qwen3-Embedding"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from openai import OpenAI
from config import EMBEDDING_URL, EMBEDDING_MODEL

_client = OpenAI(base_url=EMBEDDING_URL, api_key="na")

BATCH_SIZE = 32


def embed(texts: list[str]) -> list[list[float]]:
    """텍스트 목록을 임베딩 벡터 목록으로 변환. 배치 처리."""
    if not texts:
        return []

    vectors = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i: i + BATCH_SIZE]
        resp = _client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        vectors.extend([item.embedding for item in resp.data])
    return vectors


def embed_one(text: str) -> list[float]:
    result = embed([text])
    return result[0] if result else []
