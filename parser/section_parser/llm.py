"""명제 및 키워드 추출 - Qwen3-VL-32B 사용 + 중국어 혼입 검증"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from openai import OpenAI
from config import LLM_URL, LLM_API_KEY, VLM_MODEL

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

# 중국어 유니코드 범위
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

MAX_RETRIES = 2


def _chat(user: str, max_tokens: int = 1024) -> str:
    resp = _client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 문서 분석 전문가입니다. "
                    "반드시 한국어로만 답하세요. 중국어, 일본어 등 다른 언어는 절대 사용하지 마세요."
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


def _validate_propositions(items: list[str]) -> list[str]:
    """중국어가 포함된 명제 제거."""
    return [p for p in items if not _has_chinese(p) and len(p.strip()) > 5]


def extract_propositions_and_keywords(
    section_title: str,
    section_content: str,
) -> dict:
    """섹션 내용에서 명제 목록과 키워드를 추출.

    Returns:
        {
            "propositions": ["명제1", "명제2", ...],
            "keywords": ["키워드1", ...],
        }
    """
    content_preview = section_content[:4000]

    prompt = f"""다음은 문서의 [{section_title}] 섹션입니다.

{content_preview}

위 섹션에서 아래 두 가지를 추출하세요.

1. 명제 (propositions): 이 섹션의 핵심 사실이나 주장을 담은 독립적인 한 문장들.
   - 각 명제는 다른 문서 없이도 이해 가능한 완전한 문장이어야 합니다.
   - 섹션 내용을 충분히 커버할 수 있도록 여러 개(최소 3개, 최대 10개) 추출하세요.
   - 반드시 한국어로 작성하세요.

2. 키워드 (keywords): 이 섹션을 검색할 때 유용한 핵심 단어 5~10개.

JSON 형식으로만 답하세요:
{{
  "propositions": ["명제1", "명제2", "명제3"],
  "keywords": ["키워드1", "키워드2", "키워드3"]
}}"""

    for attempt in range(MAX_RETRIES + 1):
        result = _chat(prompt, max_tokens=1500)
        try:
            start, end = result.find("{"), result.rfind("}") + 1
            data = json.loads(result[start:end])
        except Exception:
            data = {"propositions": [], "keywords": []}

        propositions = _validate_propositions(data.get("propositions", []))
        keywords = [k for k in data.get("keywords", []) if not _has_chinese(k)]

        if propositions:
            return {"propositions": propositions, "keywords": keywords}

        # 중국어 혼입으로 명제가 모두 제거된 경우 재시도
        if attempt < MAX_RETRIES:
            prompt = (
                "이전 답변에 한국어가 아닌 문자가 포함되었습니다. "
                "반드시 한국어로만 다시 작성하세요.\n\n" + prompt
            )

    return {"propositions": [], "keywords": keywords if keywords else []}
