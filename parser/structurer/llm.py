"""도메인 분류 LLM 호출"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from config import LLM_URL, LLM_API_KEY, LLM_MODEL

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

# ── domain_type.md 파싱 ───────────────────────────────────

def _load_domain_categories(
    path: str = "/home/atech/Projects/smart-agent/domain_type.md",
) -> list[str]:
    """domain_type.md에서 대분류명(## 레벨) 목록만 추출."""
    categories = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^## \d+\.\s+(.+)", line.strip())
            if m:
                categories.append(m.group(1).strip())
    return categories


DOMAIN_CATEGORIES = _load_domain_categories()
DOMAIN_LIST = "\n".join(f"- {c}" for c in DOMAIN_CATEGORIES)


# ── LLM ──────────────────────────────────────────────────

def _chat(system: str, user: str, max_tokens: int = 200) -> str:
    resp = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def classify_domain(text: str) -> str:
    """텍스트를 domain_type.md 10개 대분류 중 하나로 분류."""
    result = _chat(
        "도메인 분류 전문가입니다. 반드시 아래 목록에 있는 대분류명 그대로 JSON으로만 답하세요.",
        f"""[도메인 대분류 목록]
{DOMAIN_LIST}

[문서 내용 일부]
{text[:3000]}

위 목록에서 가장 적합한 대분류 하나를 선택해 JSON으로만 답하세요:
{{"domain_category": "대분류명 그대로"}}""",
        max_tokens=100,
    )
    try:
        start, end = result.find("{"), result.rfind("}") + 1
        category = json.loads(result[start:end]).get("domain_category", "")
    except Exception:
        category = ""

    return category if category in DOMAIN_CATEGORIES else DOMAIN_CATEGORIES[0]
