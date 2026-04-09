"""Aggregator Agent - 검색 결과 품질 검토 및 구조화 (멀티모달 지원)"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import LLM_URL, LLM_API_KEY, VLM_MODEL
from models import (
    TaskQueue, AggregatedContent, StructuredSection,
    SIGNAL_RE_SEARCH,
)

logger = logging.getLogger(__name__)

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

_MAX_CONTENT_CHARS   = 8000  # 텍스트 결과 최대 길이
_MAX_IMAGES_TOTAL    = 5     # VLM에 전달할 최대 이미지 수 (전체)

_AGGREGATE_PROMPT = """당신은 문서 검색 결과를 평가하고 구조화하는 전문가입니다.
반드시 한국어로만 답하세요.

## 사용자 질의
{query}

## 검색 결과
{results}

## 지시사항
1. 검색 결과(텍스트 및 첨부 이미지 포함)가 사용자 질의에 답하기에 충분한지 판단하세요.
2. 충분하다면: 관련 내용을 선별·병합하여 섹션 단위로 구조화하세요.
   - 이미지에서 파악한 정보도 content에 포함하세요.
3. 불충분하다면: RE_SEARCH 신호를 반환하세요.

아래 JSON 형식으로만 답하세요:

충분한 경우:
{{
  "quality": "ok",
  "structured_content": [
    {{
      "section": "섹션 제목",
      "content": "해당 섹션 내용 (텍스트 + 이미지에서 파악한 정보 포함, 한국어로)",
      "sources": ["출처1", "출처2"]
    }}
  ]
}}

불충분한 경우:
{{
  "quality": "insufficient",
  "reason": "재검색이 필요한 이유 (한 문장)"
}}"""


def _build_user_content(prompt: str, images: list[dict]) -> list[dict] | str:
    """이미지가 있으면 멀티모달 content 리스트, 없으면 텍스트 문자열 반환."""
    if not images:
        return prompt

    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{img['mime']};base64,{img['base64']}"
            },
        })
    return content


def _call_llm(prompt: str, images: list[dict] | None = None) -> str:
    user_content = _build_user_content(prompt, images or [])
    has_images = bool(images)

    resp = _client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "당신은 문서 분석 전문가입니다. 반드시 한국어로만, JSON 형식으로만 답하세요.",
            },
            {"role": "user", "content": user_content},
        ],
        max_tokens=3000,
        temperature=0.0,
    )
    content = resp.choices[0].message.content.strip()
    img_tag = f" (이미지 {len(images)}장 포함)" if has_images else ""
    logger.info("[Aggregator%s] 응답:\n%s", img_tag, content)
    return content


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}


def _format_results(results: list[dict]) -> str:
    """검색 결과를 LLM 프롬프트용 텍스트로 변환."""
    lines = []
    for i, r in enumerate(results, 1):
        content = r.get("content", "")[:500]
        source  = r.get("source", {})
        source_str = (
            source.get("section_title") or
            source.get("file_name") or
            source.get("url") or
            f"source_{i}"
        )
        source_type = r.get("source_type", "unknown")
        img_count = len(r.get("images") or [])
        img_note = f" [이미지 {img_count}장 첨부]" if img_count else ""
        lines.append(f"[{i}] ({source_type}){img_note} {source_str}\n{content}\n")
    return "\n".join(lines)[:_MAX_CONTENT_CHARS]


def _collect_images(results: list[dict]) -> list[dict]:
    """검색 결과 전체에서 이미지를 수집. 총 _MAX_IMAGES_TOTAL개 제한."""
    images = []
    for r in results:
        for img in r.get("images") or []:
            images.append(img)
            if len(images) >= _MAX_IMAGES_TOTAL:
                return images
    return images


def aggregate(task_queue: TaskQueue, results: list[dict]) -> dict:
    """검색 결과 품질 평가 및 구조화.

    섹션에 이미지가 있으면 VLM에 base64 이미지를 함께 전달하여 멀티모달 분석.

    Returns:
        성공: {"signal": "ok", "aggregated": AggregatedContent}
        실패: {"signal": "RE_SEARCH", "reason": str}
    """
    if not results:
        logger.info("검색 결과 없음 → RE_SEARCH 신호")
        return {"signal": SIGNAL_RE_SEARCH, "reason": "검색 결과가 없습니다."}

    results_text = _format_results(results)
    images       = _collect_images(results)

    if images:
        logger.info("Aggregator 멀티모달 호출: 이미지 %d장", len(images))

    prompt = _AGGREGATE_PROMPT.format(
        query=task_queue.query.original,
        results=results_text,
    )

    raw  = _call_llm(prompt, images=images)
    data = _parse_json(raw)

    if not data:
        logger.warning("Aggregator LLM 파싱 실패 → RE_SEARCH 신호")
        return {"signal": SIGNAL_RE_SEARCH, "reason": "검색 결과 분석에 실패했습니다."}

    if data.get("quality") == "insufficient":
        reason = data.get("reason", "검색 결과가 충분하지 않습니다.")
        logger.info("Aggregator 품질 미달 → RE_SEARCH: %s", reason)
        return {"signal": SIGNAL_RE_SEARCH, "reason": reason}

    sections = [
        StructuredSection(
            section=s.get("section", f"섹션 {i+1}"),
            content=s.get("content", ""),
            sources=s.get("sources", []),
        )
        for i, s in enumerate(data.get("structured_content", []))
    ]

    if not sections:
        return {"signal": SIGNAL_RE_SEARCH, "reason": "구조화된 내용이 비어 있습니다."}

    aggregated = AggregatedContent(structured_content=sections)
    logger.info("Aggregator 구조화 완료: %d개 섹션", len(sections))
    return {"signal": "ok", "aggregated": aggregated}
