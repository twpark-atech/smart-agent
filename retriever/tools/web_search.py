"""Serper API 웹 검색 Tool"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SERPER_URL, SERPER_API_KEY

logger = logging.getLogger(__name__)

_HEADERS = {
    "X-API-KEY": SERPER_API_KEY,
    "Content-Type": "application/json",
}
_TIMEOUT = 10  # seconds


def search(query: str, top_k: int = 5) -> list[dict]:
    """Serper API로 웹 검색을 수행하고 결과를 반환.

    Args:
        query: 검색 쿼리
        top_k: 반환할 최대 결과 수

    Returns:
        [{"title", "url", "snippet", "source_type", "source"}, ...]
        실패 시 빈 리스트 반환
    """
    payload = {"q": query, "num": top_k}

    try:
        resp = requests.post(
            SERPER_URL,
            headers=_HEADERS,
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("Serper API 요청 실패: %s", e)
        return []
    except ValueError as e:
        logger.error("Serper API 응답 파싱 실패: %s", e)
        return []

    accessed_at = datetime.now().isoformat()
    results = []
    for item in data.get("organic", [])[:top_k]:
        results.append({
            "id": item.get("link", ""),
            "score": 1.0 / (len(results) + 1),  # 순위 기반 점수
            "content": item.get("snippet", ""),
            "keywords": [],
            "source_type": "web",
            "title": item.get("title", ""),
            "source": {
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "accessed_at": accessed_at,
            },
        })

    return results
