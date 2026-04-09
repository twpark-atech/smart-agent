"""Supervisor Agent - 답변 품질 검증 (Qwen3-VL-32B)"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import LLM_URL, LLM_API_KEY, VLM_MODEL
from models import (
    AggregatedContent, WriterOutput, SupervisorOutput,
    SIGNAL_PASS, SIGNAL_RETRY_RETRIEVAL, SIGNAL_RETRY_WRITER,
)

logger = logging.getLogger(__name__)

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

_VERIFY_PROMPT = """당신은 AI 답변의 품질을 검증하는 전문 검토자입니다.
반드시 한국어로만 답하세요.

## 사용자 질의
{query}

## 검색된 근거 자료 (정답 기준)
{ground_truth}

## AI 작성 답변
{answer}

## 검증 항목
1. 사실성: 답변 내용이 근거 자료와 일치하는가?
2. 논리성: 답변이 사용자 질의에 논리적으로 응답하는가?
3. 완전성: 질의의 핵심을 충분히 다루었는가?

## 지시사항
아래 JSON 형식으로만 답하세요:

통과 시:
{{
  "verdict": "PASS",
  "reason": "통과 이유 (한 문장)"
}}

데이터 부족 시 (근거 자료 자체가 부족한 경우):
{{
  "verdict": "RETRY_RETRIEVAL",
  "reason": "재검색이 필요한 이유 (한 문장)"
}}

답변 품질 저하 시 (데이터는 있으나 답변이 잘못된 경우):
{{
  "verdict": "RETRY_WRITER",
  "reason": "재작성이 필요한 이유 (한 문장)"
}}"""


def _call_llm(prompt: str) -> str:
    resp = _client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "당신은 AI 답변 품질 검증 전문가입니다. 반드시 한국어로만, JSON 형식으로만 답하세요.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=512,
        temperature=0.0,
    )
    content = resp.choices[0].message.content.strip()
    logger.info("[Supervisor] 응답:\n%s", content)
    return content


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}


def _format_ground_truth(aggregated: AggregatedContent) -> str:
    lines = []
    for section in aggregated.structured_content:
        lines.append(f"[{section.section}]\n{section.content}")
    return "\n\n".join(lines)[:5000]


def verify(
    query: str,
    aggregated: AggregatedContent,
    writer_output: WriterOutput,
) -> SupervisorOutput:
    """작성된 답변을 검증하고 신호를 반환.

    Returns:
        SupervisorOutput(signal, reason)
        signal: PASS | RETRY_RETRIEVAL | RETRY_WRITER
    """
    ground_truth = _format_ground_truth(aggregated)
    prompt = _VERIFY_PROMPT.format(
        query=query,
        ground_truth=ground_truth,
        answer=writer_output.answer[:3000],
    )

    raw = _call_llm(prompt)
    data = _parse_json(raw)

    if not data:
        logger.warning("Supervisor LLM 파싱 실패 → PASS 처리")
        return SupervisorOutput(signal=SIGNAL_PASS, reason="검증 파싱 실패, 현재 답변 반환")

    verdict = data.get("verdict", SIGNAL_PASS)
    reason = data.get("reason", "")

    if verdict == SIGNAL_PASS:
        logger.info("Supervisor 검증 통과")
        return SupervisorOutput(signal=SIGNAL_PASS, reason=reason)

    if verdict == SIGNAL_RETRY_RETRIEVAL:
        logger.info("Supervisor → RETRY_RETRIEVAL: %s", reason)
        return SupervisorOutput(signal=SIGNAL_RETRY_RETRIEVAL, reason=reason)

    if verdict == SIGNAL_RETRY_WRITER:
        logger.info("Supervisor → RETRY_WRITER: %s", reason)
        return SupervisorOutput(signal=SIGNAL_RETRY_WRITER, reason=reason)

    # 알 수 없는 verdict → PASS 처리
    logger.warning("Supervisor 알 수 없는 verdict: %s → PASS 처리", verdict)
    return SupervisorOutput(signal=SIGNAL_PASS, reason=reason)
