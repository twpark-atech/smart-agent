"""문서 요약 및 키워드 생성 - Qwen3-VL-32B + 중국어 검증"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from openai import OpenAI
from config import LLM_URL, LLM_API_KEY, VLM_MODEL

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
MAX_RETRIES = 2


def _chat(user: str, max_tokens: int = 2048) -> str:
    resp = _client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 문서 분석 전문가입니다. "
                    "반드시 한국어로만 답하세요. 중국어 등 다른 언어는 절대 사용하지 마세요."
                ),
            },
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def _has_chinese(text: str) -> bool:
    return bool(_CHINESE_RE.search(text))


def generate_summary_and_keywords(
    propositions_by_section: list[dict],
    doc_type: str,
) -> dict:
    """섹션별 명제를 종합해 문서 요약과 키워드를 생성.

    Args:
        propositions_by_section: [{"section_path": str, "propositions": [str], "keywords": [str]}, ...]
        doc_type: 문서 유형

    Returns:
        {"summary": str, "keywords": [str]}
    """
    # 명제를 섹션 구조로 직렬화 (토큰 절약을 위해 섹션당 최대 5개 명제)
    prop_text_parts = []
    all_keywords: list[str] = []

    for sec in propositions_by_section:
        props = sec["propositions"][:5]
        if not props:
            continue
        prop_text_parts.append(f"[{sec['section_path']}]\n" + "\n".join(f"- {p}" for p in props))
        all_keywords.extend(sec["keywords"])

    prop_text = "\n\n".join(prop_text_parts)

    # 키워드 후보 (빈도 순 상위 30개)
    from collections import Counter
    kw_counter = Counter(all_keywords)
    kw_candidates = "\n".join(f"- {kw} ({cnt}회)" for kw, cnt in kw_counter.most_common(30))

    prompt = f"""다음은 [{doc_type}] 문서의 섹션별 핵심 명제입니다.

{prop_text[:6000]}

[키워드 후보 (섹션 키워드 집계)]
{kw_candidates}

위 내용을 바탕으로 아래 두 가지를 생성하세요.

1. summary: 문서 전체 내용을 5~8문장으로 요약. 문서의 목적, 핵심 구성, 주요 특징을 포함.
2. keywords: 위 키워드 후보에서 문서를 가장 잘 대표하는 10~15개 선별.

반드시 한국어로 JSON만 답하세요:
{{
  "summary": "5~8문장 요약",
  "keywords": ["키워드1", "키워드2", ...]
}}"""

    for attempt in range(MAX_RETRIES + 1):
        result = _chat(prompt, max_tokens=1500)
        try:
            start, end = result.find("{"), result.rfind("}") + 1
            data = json.loads(result[start:end])
        except Exception:
            data = {}

        summary = data.get("summary", "")
        keywords = [k for k in data.get("keywords", []) if not _has_chinese(k)]

        if summary and not _has_chinese(summary):
            return {"summary": summary, "keywords": keywords}

        if attempt < MAX_RETRIES:
            prompt = "이전 답변에 한국어가 아닌 문자가 포함되었습니다. 반드시 한국어로만 다시 작성하세요.\n\n" + prompt

    return {"summary": summary or "", "keywords": keywords}
