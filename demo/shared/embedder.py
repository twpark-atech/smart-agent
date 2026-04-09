"""임베딩 클라이언트"""
import asyncio
import numpy as np
from openai import OpenAI
from shared.config import EMBEDDING_URL, EMBEDDING_MODEL

_client = OpenAI(base_url=EMBEDDING_URL, api_key="demo")


def _embed_batch(texts: list[str]) -> list[list[float]]:
    resp = _client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed_sync(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    result = []
    for i in range(0, len(texts), batch_size):
        result.extend(_embed_batch(texts[i : i + batch_size]))
    return result


async def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    return await asyncio.to_thread(embed_sync, texts, batch_size)


async def embed_one(text: str) -> list[float]:
    results = await embed([text])
    return results[0]
