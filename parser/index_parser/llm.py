"""문서 유형 분류 + 목차 추출 LLM 호출"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from config import LLM_URL, LLM_API_KEY, LLM_MODEL, VLM_MODEL

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

# ── document_type.md 파싱 ────────────────────────────────

_DEFAULT_DOC_TYPE_PATH = str(Path(__file__).parent.parent.parent / "document_type.md")

def _load_doc_types(path: str = _DEFAULT_DOC_TYPE_PATH) -> dict[str, dict]:
    """document_type.md를 파싱하여 {소분류명: {category, structure, features}} 반환."""
    doc_types: dict[str, dict] = {}
    current_category = ""
    current_subtype = ""
    structure_lines: list[str] = []
    feature_lines: list[str] = []
    in_structure = False
    in_features = False

    def _flush(name: str) -> None:
        if not name:
            return
        if structure_lines:
            doc_types[name]["structure"] = " → ".join(
                s.strip().lstrip("- ") for s in structure_lines if s.strip().startswith("-")
            )
        if feature_lines:
            doc_types[name]["features"] = ", ".join(
                s.strip().lstrip("- ") for s in feature_lines if s.strip().startswith("-")
            )

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            m2 = re.match(r"^## \d+\.\s+(.+)", line)
            m3 = re.match(r"^### [\d\-]+\.\s+(.+)", line)

            if m2:
                _flush(current_subtype)
                current_category = m2.group(1).strip()
                current_subtype = ""
                structure_lines = []
                feature_lines = []
                in_structure = False
                in_features = False
            elif m3:
                _flush(current_subtype)
                current_subtype = m3.group(1).strip()
                structure_lines = []
                feature_lines = []
                in_structure = False
                in_features = False
                doc_types[current_subtype] = {"category": current_category, "structure": "", "features": ""}
            elif current_subtype:
                stripped = line.strip()
                if stripped.startswith("- 구조"):
                    in_structure = True
                    in_features = False
                elif stripped.startswith("- 특징"):
                    in_features = True
                    in_structure = False
                elif stripped.startswith("- 포맷"):
                    in_structure = False
                    in_features = False
                elif in_structure and line.startswith("    -"):
                    structure_lines.append(line)
                elif in_features and line.startswith("    -"):
                    feature_lines.append(line)
                elif stripped.startswith("- "):
                    in_structure = False
                    in_features = False

    _flush(current_subtype)
    return doc_types


DOC_TYPES = _load_doc_types()
DOC_TYPE_LIST = "\n".join(
    "- {name} ({cat}){detail}".format(
        name=name,
        cat=info["category"],
        detail=(
            ": 구조=[{s}], 특징=[{f}]".format(s=info["structure"], f=info["features"])
            if info.get("structure") or info.get("features")
            else ""
        ),
    )
    for name, info in DOC_TYPES.items()
)


# ── LLM 공통 ─────────────────────────────────────────────

def _chat(system: str, user: str, max_tokens: int = 1024) -> str:
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


# ── 문서 유형 분류 ────────────────────────────────────────

def classify_doc_type(text_preview: str) -> str:
    """문서 앞부분 텍스트로 문서 유형 분류. document_type.md 소분류명 반환.

    LLM이 목록에 없는 값을 반환하면 목록에서 가장 유사한 항목으로 대체.
    """
    valid_types = list(DOC_TYPES.keys())

    result = _chat(
        (
            "문서 분류 전문가입니다. 반드시 아래 목록에 있는 소분류명 그대로 JSON으로만 답하세요. "
            "목록에 없는 값은 절대 사용하지 마세요. "
            "혼동 주의: "
            "논문은 Abstract/초록·References/참고문헌·IMRAD 구조(서론·방법·결과·고찰) 포함. "
            "보고서(행정/업무)는 기관 내부 보고용이며 Abstract·References 없음. "
            "연구보고서는 연구기관·정부기관 발간, 연구책임자·과제번호 명시. "
            "리포트(학술/분석)는 학과 과제·실습 제출물."
        ),
        f"""[문서 유형 목록 (이 중에서만 선택)]
{DOC_TYPE_LIST}

[문서 앞부분]
{text_preview[:3000]}

위 목록에서 가장 적합한 항목 하나를 골라 JSON으로만 답하세요:
{{"doc_type": "소분류명 그대로"}}""",
        max_tokens=100,
    )
    try:
        start, end = result.find("{"), result.rfind("}") + 1
        doc_type = json.loads(result[start:end]).get("doc_type", "")
    except Exception:
        doc_type = ""

    # 유효성 검증: 목록에 없으면 가장 유사한 항목으로 대체
    if doc_type not in valid_types:
        doc_type = _find_closest_type(doc_type, valid_types) or "설계서"

    return doc_type


def _find_closest_type(candidate: str, valid_types: list[str]) -> str | None:
    """후보 문자열과 가장 유사한 유효 타입 반환 (단순 포함 관계 기반)."""
    candidate_lower = candidate.lower()
    # 1순위: 유효 타입이 후보에 포함되거나 후보가 유효 타입에 포함
    for t in valid_types:
        if t in candidate or candidate in t:
            return t
    # 2순위: 단어 단위 교집합
    cand_words = set(re.sub(r"[^\w]", " ", candidate_lower).split())
    best, best_score = None, 0
    for t in valid_types:
        t_words = set(re.sub(r"[^\w]", " ", t.lower()).split())
        score = len(cand_words & t_words)
        if score > best_score:
            best, best_score = t, score
    return best


# ── 목차 추출 ─────────────────────────────────────────────

def extract_toc(text: str) -> dict:
    """앞 20페이지 텍스트에서 목차 추출. 대형 모델(VLM_MODEL) 사용.

    Returns:
        {
            "toc_found": bool,
            "toc": [
                {"title": "...", "level": 1, "page": null, "children": [...]},
                ...
            ]
        }
    """
    prompt_user = f"""다음은 문서의 앞부분입니다. 목차(Table of Contents)를 찾아 계층 구조로 추출하세요.

[문서 앞부분]
{text[:8000]}

규칙:
1. 목차가 존재하면 toc_found=true, 항목을 추출
2. 목차가 없으면 toc_found=false, toc=[]
3. level: 1=장/대분류/Chapter, 2=절/중분류/Section, 3=항/소분류/Subsection
4. page: 목차에 페이지 번호가 있으면 숫자, 없으면 null
5. 번호(1., 1.1, 제1장, Chapter 1 등) 포함하여 title에 기록
6. References/참고문헌/Bibliography 섹션 자체(예: "References")는 TOC 항목으로 추출하되, 해당 섹션 내부의 개별 인용 항목(예: "1. Smith, J. et al. ...", "[1] Author..." 형태의 참고문헌 목록)은 TOC 항목이 아니므로 절대 포함하지 마세요.

JSON으로만 답하세요:
{{
  "toc_found": true,
  "toc": [
    {{
      "title": "1. 개요",
      "level": 1,
      "page": 3,
      "children": [
        {{"title": "1.1 배경", "level": 2, "page": 3, "children": []}}
      ]
    }},
    {{
      "title": "Chapter 1 Introduction",
      "level": 1,
      "page": 5,
      "children": [
        {{"title": "1.1 Background", "level": 2, "page": 5, "children": []}},
        {{"title": "1.2 Scope", "level": 2, "page": 6, "children": []}}
      ]
    }},
    {{
      "title": "Appendix A Reference",
      "level": 1,
      "page": 42,
      "children": []
    }}
  ]
}}"""

    resp = _client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {"role": "system", "content": "문서 구조 분석 전문가입니다. 문서에서 목차를 찾아 계층 구조 JSON으로만 답하세요."},
            {"role": "user", "content": prompt_user},
        ],
        max_tokens=4096,
        temperature=0.0,
    )
    result = resp.choices[0].message.content.strip()

    try:
        start, end = result.find("{"), result.rfind("}") + 1
        return json.loads(result[start:end])
    except Exception:
        return {"toc_found": False, "toc": []}


def infer_toc_from_type(doc_type: str) -> list[dict]:
    """목차가 없을 때 문서 유형 기본 구조로 TOC 생성."""
    info = DOC_TYPES.get(doc_type, {})
    structure = info.get("structure", "")
    if not structure:
        return []

    sections = [s.strip() for s in structure.split("→") if s.strip()]
    return [
        {"title": title, "level": 1, "page": None, "children": []}
        for title in sections
    ]
