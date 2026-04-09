"""Writer Agent - 구조화된 내용 기반 최종 답변 작성 (Qwen2.5-3B)"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import LLM_URL, LLM_API_KEY, LLM_MODEL
from models import AggregatedContent, WriterOutput

logger = logging.getLogger(__name__)

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

_WRITE_PROMPT = """당신은 검색된 정보를 바탕으로 사용자 질의에 답변을 작성하는 전문가입니다.
반드시 한국어로만 답하세요. 제공된 내용 외의 확인되지 않은 정보는 절대 사용하지 마세요.

## 사용자 질의
{query}

## 참고할 내용
{content}

## 지시사항
- 위 내용만 사용하여 사용자 질의에 명확하게 답변하세요.
- 내용이 없거나 불충분한 경우 그렇다고 솔직하게 밝히세요.
- 답변 형식은 질의 유형에 맞게 조정하세요 (요약형 / 상세형 / 목록형 등).
- 답변 마지막에 "## 출처" 섹션을 추가하고 사용한 출처를 나열하세요.

답변:"""


def _format_content(aggregated: AggregatedContent) -> tuple[str, list[str]]:
    """구조화된 내용을 프롬프트용 텍스트로 변환."""
    lines = []
    all_sources: list[str] = []

    for section in aggregated.structured_content:
        lines.append(f"### {section.section}")
        lines.append(section.content)
        lines.append("")
        all_sources.extend(section.sources)

    return "\n".join(lines), list(dict.fromkeys(all_sources))  # 중복 제거


def write(query: str, aggregated: AggregatedContent) -> WriterOutput:
    """구조화된 내용을 바탕으로 답변을 작성.

    Args:
        query: 원본 사용자 질의
        aggregated: Aggregator가 구조화한 내용

    Returns:
        WriterOutput(answer, sources)
    """
    content_text, sources = _format_content(aggregated)

    prompt = _WRITE_PROMPT.format(
        query=query,
        content=content_text[:6000],
    )

    resp = _client.chat.completions.create(
        model=LLM_MODEL,  # Qwen2.5-3B-Instruct
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 문서 기반 답변 작성 전문가입니다. "
                    "반드시 한국어로만, 제공된 내용만 사용하여 답변하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=2000,
        temperature=0.1,
    )

    answer = resp.choices[0].message.content.strip()
    logger.info("[Writer] 응답:\n%s", answer)
    return WriterOutput(answer=answer, sources=sources)
