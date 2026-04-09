"""
목차 기반 청킹 전략 테스트

단계:
1. parsed_output.md 사용 (Docling 파싱 결과)
2. 문서 유형 판별 (LLM + document_type.md)
3. 목차 탐색 → 성공 시 목차 파싱, 실패 시 LLM 헤딩 분류
4. 가장 작은 분류 단위로 청킹
5. 2048토큰 초과 시 하위 분할
6. Proposition 추출 + 유사도 검증
"""

import re
import numpy as np
from openai import OpenAI
from scipy import stats

# ── 설정 ──
PARSED_MD_PATH = "/home/atech/Projects/smart-agent/tests/parsed_output.md"
DOCUMENT_TYPE_PATH = "/home/atech/Projects/smart-agent/document_type.md"
EMBEDDING_URL = "http://112.163.62.170:8032/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
LLM_URL = "http://112.163.62.170:8012/v1"
LLM_API_KEY = "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9"
LLM_MODEL = "Qwen2.5-3B-Instruct"
MAX_TOKENS = 2048

embed_client = OpenAI(base_url=EMBEDDING_URL, api_key="test")
llm_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)


def estimate_tokens(text: str) -> int:
    korean = len(re.findall(r'[가-힣]', text))
    other = len(text) - korean
    return int(korean / 2 + other / 4)


def get_embeddings(texts: list[str], batch_size: int = 64) -> np.ndarray:
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = embed_client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([d.embedding for d in resp.data])
    return np.array(all_embeddings)


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


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def load_md(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_headings(md_text: str) -> list[dict]:
    """MD에서 ## 헤딩 추출"""
    headings = []
    for i, line in enumerate(md_text.split('\n')):
        match = re.match(r'^(#{1,6})\s+(.+)', line)
        if match:
            headings.append({
                'line': i,
                'level': len(match.group(1)),
                'title': match.group(2).strip(),
            })
    return headings


def split_md_by_headings(md_text: str) -> list[dict]:
    """MD를 ## 헤딩 기준으로 섹션 분리"""
    lines = md_text.split('\n')
    sections = []
    current = {'title': '서두', 'content_lines': []}

    for line in lines:
        match = re.match(r'^#{1,6}\s+(.+)', line)
        if match:
            if current['content_lines']:
                content = '\n'.join(current['content_lines']).strip()
                if content:
                    sections.append({
                        'title': current['title'],
                        'content': content,
                    })
            current = {'title': match.group(1).strip(), 'content_lines': []}
        else:
            current['content_lines'].append(line)

    if current['content_lines']:
        content = '\n'.join(current['content_lines']).strip()
        if content:
            sections.append({'title': current['title'], 'content': content})

    return sections


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
# 2단계: 목차 탐색 및 파싱
# ========================================
def step2_parse_toc(md_text: str) -> list[dict] | None:
    lines = md_text.split('\n')

    # "목차" 헤딩 찾기
    toc_start = None
    for i, line in enumerate(lines):
        if re.match(r'^#+\s*목\s*차', line):
            toc_start = i + 1
            break

    if toc_start is None:
        return None

    toc_entries = []
    current_major = None

    for i in range(toc_start, min(toc_start + 100, len(lines))):
        line = lines[i].strip()

        # 다음 헤딩이 나오면 목차 끝
        if re.match(r'^#{1,6}\s+', line) and '목' not in line:
            break

        # 표 행 파싱
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
            title = re.sub(r'\s+$', '', title)
            toc_entries.append({
                'depth': 2,
                'major': current_major,
                'title': f"{num_match.group(1)}. {title}",
            })

    return toc_entries if toc_entries else None


# ========================================
# 3단계: 목차 기준으로 MD 분할
# ========================================
def step3_split_by_toc(md_text: str, toc_entries: list[dict]) -> list[dict]:
    # 중분류 제목으로 매칭
    mid_titles = [e for e in toc_entries if e['depth'] == 2]
    if not mid_titles:
        mid_titles = [e for e in toc_entries if e['depth'] == 1]

    # 매칭용 키워드 추출
    matchers = []
    for entry in mid_titles:
        core = re.sub(r'^\d+\.\s*', '', entry['title']).strip()
        core = core[:15] if len(core) > 15 else core
        matchers.append({'entry': entry, 'core': core})

    # MD를 헤딩으로 분리
    all_sections = split_md_by_headings(md_text)

    # 목차 기준 청크 생성
    chunks = []
    current_chunk = {'major': '서두', 'title': '서두', 'sections': []}
    next_match_idx = 0

    for sec in all_sections:
        title = sec['title']

        # 헤더/푸터 스킵
        if '하이테크 섬유소재' in title and '핵심인력' in title:
            continue
        if re.match(r'^목\s*차$', title):
            continue

        # 목차 항목 매칭
        matched = False
        if next_match_idx < len(matchers):
            core = matchers[next_match_idx]['core']
            if core and len(core) >= 3 and core in title:
                if current_chunk['sections']:
                    chunks.append(current_chunk)
                entry = matchers[next_match_idx]['entry']
                current_chunk = {
                    'major': entry['major'],
                    'title': entry['title'],
                    'sections': [],
                }
                next_match_idx += 1
                matched = True

        current_chunk['sections'].append(sec)

    if current_chunk['sections']:
        chunks.append(current_chunk)

    # 텍스트 합치기 + 문단 분리 + 토큰 계산
    for chunk in chunks:
        parts = []
        paragraphs = []
        for sec in chunk['sections']:
            parts.append(sec['content'])
            for para in re.split(r'\n\s*\n', sec['content']):
                para = para.strip()
                if para and len(para) >= 10 and not para.startswith('|') and not para.startswith('<!--'):
                    paragraphs.append(para)

        chunk['text'] = '\n\n'.join(parts)
        chunk['tokens'] = estimate_tokens(chunk['text'])
        chunk['paragraphs'] = paragraphs
        chunk['para_count'] = len(paragraphs)

    return chunks


# ========================================
# 4단계: 초과 청크 하위 분할
# ========================================
def step4_split_oversized(chunks: list[dict]) -> list[dict]:
    final = []

    for chunk in chunks:
        if chunk['tokens'] <= MAX_TOKENS:
            final.append(chunk)
            continue

        # 청크 내 섹션 기준으로 분할
        sub_chunks = []
        current_sub = {
            'major': chunk['major'],
            'title': chunk['title'],
            'text': '',
            'paragraphs': [],
            'tokens': 0,
        }

        for sec in chunk['sections']:
            sec_tokens = estimate_tokens(sec['content'])

            if current_sub['tokens'] + sec_tokens > MAX_TOKENS and current_sub['paragraphs']:
                current_sub['para_count'] = len(current_sub['paragraphs'])
                current_sub['text'] = current_sub['text'].strip()
                sub_chunks.append(current_sub)
                current_sub = {
                    'major': chunk['major'],
                    'title': f"{chunk['title']} > {sec['title'][:30]}",
                    'text': '',
                    'paragraphs': [],
                    'tokens': 0,
                }

            current_sub['text'] += '\n\n' + sec['content']
            current_sub['tokens'] += sec_tokens
            for para in re.split(r'\n\s*\n', sec['content']):
                para = para.strip()
                if para and len(para) >= 10 and not para.startswith('|') and not para.startswith('<!--'):
                    current_sub['paragraphs'].append(para)

        if current_sub['paragraphs']:
            current_sub['para_count'] = len(current_sub['paragraphs'])
            current_sub['text'] = current_sub['text'].strip()
            sub_chunks.append(current_sub)

        if sub_chunks:
            print(f"  [{chunk['title'][:35]}] ({chunk['tokens']}tok) → {len(sub_chunks)}개 서브청크")
            final.extend(sub_chunks)
        else:
            final.append(chunk)

    return final


# ========================================
# 5단계: Proposition + 유사도 검증
# ========================================
def step5_verify(chunks: list[dict]) -> list[dict]:
    target = [c for c in chunks if c['para_count'] >= 2]
    results = []

    for i, chunk in enumerate(target):
        if i % 5 == 0:
            print(f"  진행: {i}/{len(target)}...")

        try:
            proposition = llm_chat(
                "당신은 문서 분석 전문가입니다. 주어진 섹션의 핵심 내용을 하나의 명제(Proposition)로 요약하세요. 반드시 한국어 한 문장으로만 답하세요.",
                f"다음 섹션의 핵심을 하나의 명제로 요약하세요:\n\n{chunk['text'][:2000]}",
                max_tokens=200,
            )

            texts_to_embed = [proposition] + chunk['paragraphs']
            embeddings = get_embeddings(texts_to_embed)
            prop_emb = embeddings[0]
            para_embs = embeddings[1:]
            sims = [cosine_sim(prop_emb, p) for p in para_embs]

            results.append({
                'title': chunk['title'],
                'major': chunk['major'],
                'tokens': chunk['tokens'],
                'para_count': chunk['para_count'],
                'proposition': proposition,
                'sims': sims,
                'mean_sim': np.mean(sims),
                'std_sim': np.std(sims),
            })
        except Exception as e:
            print(f"  청크{i} 실패: {e}")

    return results


# ========================================
# 메인
# ========================================
def main():
    md_text = load_md(PARSED_MD_PATH)
    doc_type_md = load_md(DOCUMENT_TYPE_PATH)
    print(f"MD 로드 완료: {len(md_text)}자\n")

    # ── 1단계 ──
    print("=" * 60)
    print("1단계: 문서 유형 판별")
    print("=" * 60)
    doc_type_result = step1_classify_document(md_text, doc_type_md)
    print(doc_type_result)

    # ── 2단계 ──
    print(f"\n{'=' * 60}")
    print("2단계: 목차 탐색 및 파싱")
    print("=" * 60)
    toc = step2_parse_toc(md_text)

    if toc:
        print(f"\n목차 발견! ({len(toc)}개 항목)")
        for entry in toc:
            indent = "  " if entry['depth'] == 2 else ""
            print(f"  {indent}[depth={entry['depth']}] {entry['title']}")
    else:
        print("\n목차 미발견 → LLM 폴백 필요 (이번 테스트에서는 중단)")
        return

    # ── 3단계 ──
    print(f"\n{'=' * 60}")
    print("3단계: 목차 기준 청킹")
    print("=" * 60)
    chunks = step3_split_by_toc(md_text, toc)

    print(f"\n1차 청크 수: {len(chunks)}")
    for c in chunks:
        marker = " ◀ 분할 필요" if c['tokens'] > MAX_TOKENS else ""
        print(f"  [{c['tokens']:6d}tok, {c['para_count']:3d}문단] [{c['major'][:12]}] {c['title'][:40]}{marker}")

    # ── 4단계 ──
    print(f"\n{'=' * 60}")
    print("4단계: 2048토큰 초과 분할")
    print("=" * 60)
    final_chunks = step4_split_oversized(chunks)

    tokens = [c['tokens'] for c in final_chunks]
    under = sum(1 for t in tokens if t < 512)
    normal = sum(1 for t in tokens if 512 <= t <= 2048)
    over = sum(1 for t in tokens if t > 2048)

    print(f"\n최종 청크 수: {len(final_chunks)}")
    print(f"  평균: {np.mean(tokens):.0f}tok | 중앙: {np.median(tokens):.0f}tok")
    print(f"  < 512: {under}개 ({under/len(tokens)*100:.1f}%) | 512~2048: {normal}개 ({normal/len(tokens)*100:.1f}%) | > 2048: {over}개 ({over/len(tokens)*100:.1f}%)")

    print(f"\n최종 청크:")
    for c in final_chunks:
        print(f"  [{c['tokens']:6d}tok, {c.get('para_count',0):3d}문단] {c['title'][:50]}")

    # ── 5단계 ──
    print(f"\n{'=' * 60}")
    print("5단계: Proposition 검증")
    print("=" * 60)
    results = step5_verify(final_chunks)

    if not results:
        print("검증 대상 없음")
        return

    all_sims = []
    for r in results:
        all_sims.extend(r['sims'])
    all_means = [r['mean_sim'] for r in results]
    all_stds = [r['std_sim'] for r in results]

    print(f"\n[Proposition ↔ 문단 유사도] ({len(all_sims)}쌍)")
    print(f"  평균: {np.mean(all_sims):.4f} | 중앙: {np.median(all_sims):.4f}")

    print(f"\n[청크별 평균] ({len(results)}개)")
    print(f"  평균의 평균: {np.mean(all_means):.4f} | 표준편차: {np.mean(all_stds):.4f}")

    t_stat, p_value = stats.ttest_1samp(all_sims, 0.5)
    print(f"\n[t-test] p-value: {p_value:.2e}")

    # 비교
    print(f"\n{'=' * 60}")
    print("비교")
    print("=" * 60)
    print(f"  문단 Prop ↔ 문장:     0.6181")
    print(f"  섹션 Prop ↔ 문단:     0.5326")
    print(f"  패턴 중목차 (이전):    0.4000")
    print(f"  목차 기반 (현재):      {np.mean(all_sims):.4f}")

    # 토큰 구간별
    tok_groups = {}
    for r in results:
        key = "< 512" if r['tokens'] < 512 else "512~1024" if r['tokens'] < 1024 else "1024~2048" if r['tokens'] < 2048 else "2048+"
        tok_groups.setdefault(key, []).append(r['mean_sim'])

    print(f"\n토큰 구간별:")
    for key in ["< 512", "512~1024", "1024~2048", "2048+"]:
        if key in tok_groups:
            print(f"  {key:>12}: {np.mean(tok_groups[key]):.4f} ({len(tok_groups[key])}개)")

    # 샘플
    sorted_r = sorted(results, key=lambda x: x['mean_sim'], reverse=True)
    print(f"\n[상위 3개]")
    for r in sorted_r[:3]:
        print(f"  {r['mean_sim']:.3f} | {r['tokens']}tok | {r['title'][:40]}")
        print(f"    Prop: {r['proposition'][:80]}")

    print(f"\n[하위 3개]")
    for r in sorted_r[-3:]:
        print(f"  {r['mean_sim']:.3f} | {r['tokens']}tok | {r['title'][:40]}")
        print(f"    Prop: {r['proposition'][:80]}")

    print(f"\n{'=' * 60}")
    print("최종 판정")
    print("=" * 60)
    print(f"  Proposition ↔ 문단 평균 유사도: {np.mean(all_sims):.4f}")
    print(f"  최종 청크 수: {len(final_chunks)}")
    print(f"  적정 범위(512~2048) 비율: {normal/len(tokens)*100:.1f}%")


if __name__ == "__main__":
    main()
