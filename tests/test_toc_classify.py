"""
목차 기반 청킹 - 분류까지만

1. 문서 유형 판별 (LLM)
2. 목차 파싱 → 대분류/중분류 확정
3. 중분류 기준으로 본문 분할
4. 결과를 MD로 저장 (# 대분류, ## 중분류)
"""

import re
from openai import OpenAI

from pathlib import Path
_ROOT = Path(__file__).parent.parent
PARSED_MD_PATH = str(_ROOT / "tests" / "parsed_output.md")
DOCUMENT_TYPE_PATH = str(_ROOT / "document_type.md")
OUTPUT_MD_PATH = str(_ROOT / "tests" / "classified_output.md")
LLM_URL = "http://112.163.62.170:8012/v1"
LLM_API_KEY = "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9"
LLM_MODEL = "Qwen3-VL-32B-Instruct-AWQ"

llm_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)


def estimate_tokens(text: str) -> int:
    korean = len(re.findall(r'[가-힣]', text))
    if korean / max(len(text), 1) > 0.1:
        # 한국어 위주: 한글 2자=1토큰, 나머지 4자=1토큰
        other = len(text) - korean
        return int(korean / 2 + other / 4)
    else:
        # 영어/기타 위주: 단어 수 기반 (평균 1.3토큰/단어)
        return int(len(text.split()) * 1.3)


def llm_chat(system: str, user: str, max_tokens: int = 500) -> str:
    resp = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def load_md(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ========================================
# 1단계: 문서 유형 판별
# ========================================
def step1_classify_document(md_text: str, doc_type_md: str) -> str:
    preview = md_text[:2000]
    result = llm_chat(
        "당신은 문서 분류 전문가입니다. 주어진 기준에 따라 문서를 정확히 분류하세요.",
        f"""다음은 문서 유형 분류 기준입니다:
{doc_type_md[:3000]}

---
다음은 파싱된 문서의 앞부분입니다:
{preview}

---
위 문서의 유형을 분류해주세요. 반드시 아래 형식으로만 답하세요:
분류번호: (예: 3-4)
유형명: (예: 매뉴얼/가이드)
구조: (예: 개요 → 설치·설정 → 기능 설명 → 절차 → FAQ → 문제 해결)
목차유무: (있음 또는 없음)""",
        max_tokens=300,
    )
    return result


# ========================================
# 2단계: 목차 파싱
# ========================================
def step2_parse_toc(md_text: str) -> list[dict] | None:
    lines = md_text.split('\n')

    toc_start = None
    for i, line in enumerate(lines):
        if re.match(r'^#+\s*(목\s*차|table\s+of\s+contents|contents|toc)', line, re.IGNORECASE):
            toc_start = i + 1
            break

    if toc_start is None:
        return None

    toc_entries = []
    current_major = None

    for i in range(toc_start, min(toc_start + 100, len(lines))):
        line = lines[i].strip()

        if re.match(r'^#{1,6}\s+', line) and '목' not in line:
            break

        row_match = re.match(r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|', line)
        if not row_match:
            continue

        col1 = row_match.group(1).strip()
        col2 = row_match.group(2).strip()

        if set(col1) <= {'-', '|', ' '}:
            continue

        # 대분류: □
        if col1 == '□':
            title = re.sub(r'[·…\s]+$', '', col2).strip()
            if title and len(title) > 2:
                current_major = title
                toc_entries.append({
                    'depth': 1,
                    'major': current_major,
                    'title': current_major,
                })
            continue

        # 중분류: 숫자.
        num_match = re.match(r'^(\d+)\.?\s*$', col1)
        if num_match and current_major:
            title = re.sub(r'[·…]+.*$', '', col2).strip()
            title = re.sub(r'[\uf000-\uf0ff]', '', title)  # Private Use Area 문자 제거
            title = title.strip()
            toc_entries.append({
                'depth': 2,
                'major': current_major,
                'title': f"{num_match.group(1)}. {title}",
            })

    return toc_entries if toc_entries else None


# ========================================
# 3단계: 목차 기준으로 분할 + MD 저장
# ========================================
def step3_split_and_save(md_text: str, toc_entries: list[dict], output_path: str):
    # 대분류 + 중분류 모두 매칭 (순서대로)
    def normalize(text):
        """공백 제거 + HTML 엔티티 + 특수문자 정규화"""
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = re.sub(r'[\uf000-\uf0ff]', '', text)  # Private Use Area 문자 제거
        return re.sub(r'\s+', '', text)

    matchers = []
    for entry in toc_entries:
        core = re.sub(r'^\d+\.\s*', '', entry['title']).strip()
        # 핵심 키워드: 첫 단어 or 앞 7자 (매칭 정확도 균형)
        words = core.split()
        if words:
            keyword = words[0][:7]
        else:
            keyword = core[:7]
        matchers.append({'entry': entry, 'core': core, 'keyword': keyword})

    # MD를 헤딩으로 분리
    lines = md_text.split('\n')
    sections = []
    current = {'title': '서두', 'lines': []}

    for line in lines:
        match = re.match(r'^#{1,6}\s+(.+)', line)
        if match:
            if current['lines']:
                sections.append(current)
            current = {'title': match.group(1).strip(), 'lines': []}
        else:
            current['lines'].append(line)

    if current['lines']:
        sections.append(current)

    # 목차 기준으로 분류
    chunks = []
    current_chunk = {'major': '서두', 'title': '서두', 'section_contents': []}
    next_match_idx = 0

    for sec in sections:
        title = sec['title']
        content = '\n'.join(sec['lines'])

        # 헤더/푸터 스킵
        if '하이테크 섬유소재' in title and '핵심인력' in title:
            continue
        if re.match(r'^목\s*차$', title):
            continue

        # 목차 항목 매칭: keyword(첫 단어)로 매칭
        title_norm = normalize(title)
        if next_match_idx < len(matchers):
            keyword = matchers[next_match_idx]['keyword']
            keyword_norm = normalize(keyword)
            if keyword_norm and len(keyword_norm) >= 2 and keyword_norm in title_norm:
                if current_chunk['section_contents']:
                    chunks.append(current_chunk)
                entry = matchers[next_match_idx]['entry']
                current_chunk = {
                    'major': entry['major'],
                    'title': entry['title'],
                    'section_contents': [],
                }
                next_match_idx += 1

        # 원본 헤딩 + 본문 보존
        current_chunk['section_contents'].append({
            'heading': title,
            'content': content,
        })

    if current_chunk['section_contents']:
        chunks.append(current_chunk)

    # MD 파일로 저장
    with open(output_path, "w", encoding="utf-8") as f:
        current_major = None

        for chunk in chunks:
            # 대분류 변경 시 # 출력
            if chunk['major'] != current_major:
                current_major = chunk['major']
                f.write(f"\n# {current_major}\n\n")

            # 중분류 ##
            f.write(f"## {chunk['title']}\n\n")

            # 하위 내용 (원본 헤딩은 ### 이하로)
            for sec in chunk['section_contents']:
                heading = sec['heading']
                content = sec['content']

                # 중분류 제목과 같으면 헤딩 생략 (중복 방지)
                if heading != chunk['title'] and heading != current_major:
                    f.write(f"### {heading}\n\n")

                if content.strip():
                    f.write(f"{content.strip()}\n\n")

    # 통계
    print(f"\nMD 저장 완료: {output_path}")
    print(f"\n청크별 토큰 수:")
    total_tokens = 0
    for chunk in chunks:
        text = '\n'.join(s['content'] for s in chunk['section_contents'])
        tokens = estimate_tokens(text)
        total_tokens += tokens
        marker = " ◀ 2048 초과" if tokens > 2048 else ""
        print(f"  [{tokens:6d}tok] [{chunk['major'][:12]}] {chunk['title'][:40]}{marker}")

    print(f"\n총 청크: {len(chunks)}개 | 총 토큰: {total_tokens}")


def main():
    md_text = load_md(PARSED_MD_PATH)
    doc_type_md = load_md(DOCUMENT_TYPE_PATH)
    print(f"MD 로드: {len(md_text)}자\n")

    # 1단계
    print("=" * 60)
    print("1단계: 문서 유형 판별")
    print("=" * 60)
    result = step1_classify_document(md_text, doc_type_md)
    print(result)

    # 2단계
    print(f"\n{'=' * 60}")
    print("2단계: 목차 파싱")
    print("=" * 60)
    toc = step2_parse_toc(md_text)

    if toc:
        print(f"\n목차 항목: {len(toc)}개")
        for entry in toc:
            indent = "  " if entry['depth'] == 2 else ""
            print(f"  {indent}[depth={entry['depth']}] {entry['title']}")
    else:
        print("목차 미발견")
        return

    # 3단계
    print(f"\n{'=' * 60}")
    print("3단계: 분류 + MD 저장")
    print("=" * 60)
    step3_split_and_save(md_text, toc, OUTPUT_MD_PATH)


if __name__ == "__main__":
    main()
