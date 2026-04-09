"""LLM 클라이언트 - 모든 LLM 호출을 여기서 관리"""
import json
import asyncio
from openai import OpenAI
from shared.config import LLM_URL, LLM_API_KEY, LLM_MODEL, DOMAIN_TAXONOMY, DOCUMENT_TAXONOMY, DOMAIN_CATEGORIES

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)


def _chat(system: str, user: str, max_tokens: int = 512) -> str:
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


async def _achat(system: str, user: str, max_tokens: int = 512) -> str:
    return await asyncio.to_thread(_chat, system, user, max_tokens)


# ── 인덱싱용 ──────────────────────────────────────────────

async def classify_document(text: str) -> dict:
    """문서 유형 + 도메인 분류. {doc_type, domain_category} 반환"""
    preview = text[:2000]
    result = await _achat(
        "문서 분류 전문가입니다. 반드시 아래 분류 체계에 있는 값으로만 JSON 답변하세요.",
        f"""[문서 유형 분류 체계] (doc_type은 소분류명 사용)
{DOCUMENT_TAXONOMY}

[도메인 분류 체계] (domain_category는 대분류명 사용)
{DOMAIN_TAXONOMY}

문서 앞부분:
{preview}

위 분류 체계에서 가장 적합한 항목을 골라 JSON으로만 답하세요:
{{
  "doc_type": "소분류명 그대로",
  "domain_category": "대분류명 그대로"
}}""",
        max_tokens=150,
    )
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        return json.loads(result[start:end])
    except Exception:
        return {"doc_type": "기타", "domain_category": "기타"}


async def generate_section_summary(text: str) -> str:
    """섹션 요약 2~3문장 생성 (Index Search용)"""
    return await _achat(
        "문서 분석 전문가입니다. 섹션 내용을 2~3문장으로 요약하세요. 요약문만 출력하세요.",
        f"다음 섹션을 2~3문장으로 요약하세요:\n\n{text[:2000]}",
        max_tokens=200,
    )


async def extract_proposition(text: str) -> str:
    """핵심 명제 1문장 추출 (청크 레벨 검색용)"""
    return await _achat(
        "문서 분석 전문가입니다. 핵심 내용을 한 문장 명제로 요약하세요. 반드시 한국어 한 문장으로만 답하세요.",
        f"다음 내용의 핵심을 명제 한 문장으로:\n\n{text[:2000]}",
        max_tokens=150,
    )


async def generate_doc_summary(text: str) -> tuple[str, list[str]]:
    """문서 전체 요약 + 키워드 추출. (summary, keywords) 반환"""
    result = await _achat(
        "문서 분석 전문가입니다. 아래 형식 JSON으로만 답하세요.",
        f"""다음 문서를 분석하세요:
{text[:4000]}

JSON 형식으로만 답하세요:
{{
  "summary": "3~5문장 요약",
  "keywords": ["키워드1", "키워드2", ..., "키워드10"]
}}""",
        max_tokens=400,
    )
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        data = json.loads(result[start:end])
        return data.get("summary", ""), data.get("keywords", [])
    except Exception:
        return text[:200], []


# ── 검색용 ────────────────────────────────────────────────

async def rewrite_query(query: str) -> str:
    """Query Rewriter Agent: 사용자 질의를 검색에 최적화된 명제 단위 쿼리로 변환.
    - 구어체·질문형 → 서술형 명제
    - 핵심 개념·키워드 명시
    - 모호한 지시어 제거
    원본 쿼리가 이미 명확한 명제형이면 그대로 반환."""
    result = await _achat(
        "당신은 검색 쿼리 최적화 전문가입니다. "
        "사용자의 자연어 질의를 벡터 검색에 최적화된 명제 단위 쿼리로 변환하세요. "
        "변환된 쿼리 텍스트만 출력하세요. 설명이나 부가 문장은 절대 포함하지 마세요.",
        f"""다음 질의를 검색 최적화 명제로 변환하세요.

규칙:
1. 질문형("~이 뭐야?", "~어떻게?") → 서술형 명제("~은 ...이다.", "~의 방법은 ...이다.")
2. 핵심 개념과 키워드를 명시적으로 포함
3. 모호한 지시어("이것", "그거", "알아봐줘") 제거
4. 이미 명확한 명제형이면 원문 그대로 반환
5. 한 문장으로 출력

질의: {query}
변환된 쿼리:""",
        max_tokens=150,
    )
    rewritten = result.strip().strip('"').strip("'")
    return rewritten if rewritten else query


async def classify_query(query: str, top_domain: int = 3) -> dict:
    """쿼리 분析: 도메인 후보(top_domain개) + 복합 여부 판단.
    {domain_candidates, is_complex, sub_queries} 반환
    domain_candidates: [{"domain_category": ...}, ...]  관련도 높은 순, 정확히 top_domain개"""
    domain_list = "\n".join(
        f"{i+1}. {c}" for i, c in enumerate(DOMAIN_CATEGORIES)
    ) if DOMAIN_CATEGORIES else DOMAIN_TAXONOMY

    result = await _achat(
        "검색 전문가입니다. 반드시 아래 도메인 목록에 있는 값으로만 JSON 답변하세요.",
        f"""[도메인 목록]
{domain_list}

쿼리: "{query}"

지시사항:
1. 위 도메인 목록에서 쿼리와 관련도 높은 순으로 정확히 {top_domain}개를 선택하세요.
2. 관련도가 낮더라도 반드시 {top_domain}개를 채워야 합니다.
3. JSON으로만 답하세요. 설명 없이 JSON만 출력하세요.

출력 형식:
{{
  "domain_candidates": [
    {{"domain_category": "도메인명"}},
    {{"domain_category": "도메인명"}},
    {{"domain_category": "도메인명"}}
  ],
  "is_complex": true/false,
  "sub_queries": []
}}

is_complex: 여러 도메인에 걸치거나 다단계 추론이 필요하면 true.
is_complex=true이면 sub_queries에 분해된 서브쿼리를 작성하세요.""",
        max_tokens=400,
    )
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        parsed = json.loads(result[start:end])
    except Exception:
        parsed = {"domain_candidates": [], "is_complex": False, "sub_queries": []}

    # LLM이 top_domain개 미만 반환 시 나머지를 도메인 목록에서 채움
    candidates = parsed.get("domain_candidates", [])
    existing = {c["domain_category"] for c in candidates if c.get("domain_category")}
    for cat in DOMAIN_CATEGORIES:
        if len(candidates) >= top_domain:
            break
        if cat not in existing:
            candidates.append({"domain_category": cat})
            existing.add(cat)
    parsed["domain_candidates"] = candidates[:top_domain]
    return parsed


async def write_answer(query: str, chunks: list[dict]) -> str:
    """Writer Agent: 검색된 청크를 바탕으로 답변 작성"""
    context = "\n\n---\n\n".join(
        f"[출처: {c.get('section_path', c.get('section_name', ''))}]\n{c['content']}"
        for c in chunks[:5]
    )
    return await _achat(
        "당신은 전문 답변 작성 에이전트입니다. 제공된 참고 자료만을 근거로 질문에 답하세요. "
        "답변에 출처 섹션을 명시하세요. 참고 자료에 없는 내용은 추측하지 마세요.",
        f"질문: {query}\n\n참고 자료:\n{context}",
        max_tokens=800,
    )


async def validate_answer(query: str, answer: str, chunks: list[dict]) -> dict:
    """Validator Agent: 근거 충실성·완전성·일관성 검증.
    {valid, score, message} 반환"""
    context_preview = "\n".join(
        c['content'][:300] for c in chunks[:3]
    )
    result = await _achat(
        "답변 검증 전문가입니다. 아래 기준으로 검증 후 JSON으로만 답하세요.",
        f"""질문: {query}

답변:
{answer}

참고 자료 (일부):
{context_preview}

검증 기준:
1. 근거 충실성: 답변의 주요 내용이 참고 자료에 기반하는가
2. 완전성: 질문의 모든 부분에 답했는가
3. 일관성: 답변 내 상충하는 내용이 없는가

JSON으로만 답하세요:
{{
  "valid": true/false,
  "score": 0.0~1.0,
  "message": "검증 결과 한 줄 설명"
}}""",
        max_tokens=200,
    )
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        return json.loads(result[start:end])
    except Exception:
        return {"valid": True, "score": 0.7, "message": "검증 파싱 실패 - 기본 통과"}
