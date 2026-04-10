"""
청킹 전략 검증 테스트

파이프라인:
1. Docling 파싱
2. 후처리 (헤더/푸터 필터링, 섹션 계층 재구성)
3. 중목차 단위 청킹
4. Proposition 추출 + 임베딩
5. Proposition ↔ 청크 내 문단 유사도 검증
"""

import re
import numpy as np
from collections import Counter
from openai import OpenAI
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DocItemLabel
from scipy import stats

# ── 설정 ──
PDF_PATH = "/mnt/d/스마트제조혁신기술개발/data/7.기능성 가공 기술 실무.pdf"
EMBEDDING_URL = "http://112.163.62.170:8032/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
LLM_URL = "http://112.163.62.170:8012/v1"
LLM_API_KEY = "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9"
LLM_MODEL = "Qwen2.5-3B-Instruct"

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


def extract_proposition(text: str) -> str:
    resp = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "당신은 문서 분석 전문가입니다. 주어진 섹션의 핵심 내용을 하나의 명제(Proposition)로 요약하세요. 반드시 한국어 한 문장으로만 답하세요."},
            {"role": "user", "content": f"다음 섹션의 핵심을 하나의 명제로 요약하세요:\n\n{text[:2000]}"}
        ],
        max_tokens=200,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


# ── 섹션 계층 분류 규칙 ──
def classify_depth(title: str) -> int:
    """섹션 제목으로 depth 판별"""
    t = title.strip()
    # depth=1: □ 기호 패턴 (대목차)
    if re.match(r'^[□■◆]', t):
        return 1
    # depth=2: "숫자. 제목" 패턴 (중목차)
    if re.match(r'^\d+\.\s', t):
        return 2
    # depth=3: 소목차 패턴들
    if re.match(r'^\d+\.\d+', t):  # 1.1, 2.3 등
        return 3
    if re.match(r'^\(\d+\)', t):   # (1), (2) 등
        return 3
    if re.match(r'^\d+\)', t):     # 1), 2) 등
        return 3
    if re.match(r'^[①-⑳]', t):    # ①, ② 등
        return 3
    if re.match(r'^[가-힣][.)\s]', t):  # 가., 나. 등
        return 3
    # 기본: depth=3으로 처리
    return 3


def is_header_footer(title: str, repeated_titles: set) -> bool:
    """헤더/푸터 패턴 판별"""
    return title.strip() in repeated_titles


def analyze():
    # ========================================
    # 1단계: Docling 파싱
    # ========================================
    print("=" * 60)
    print("1단계: Docling 파싱")
    print("=" * 60)

    converter = DocumentConverter()
    result = converter.convert(PDF_PATH)
    doc = result.document

    # 원본 섹션 추출
    raw_sections = []
    current_title = "서두"
    current_paragraphs = []

    for item, _level in doc.iterate_items():
        label = item.label
        if label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text:
                if current_paragraphs:
                    raw_sections.append({
                        'title': current_title,
                        'paragraphs': [p for p in current_paragraphs],
                    })
                current_title = text
                current_paragraphs = []
            continue
        if label in [DocItemLabel.PARAGRAPH, DocItemLabel.TEXT, DocItemLabel.LIST_ITEM]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text and len(text) >= 10:
                current_paragraphs.append(text)

    if current_paragraphs:
        raw_sections.append({'title': current_title, 'paragraphs': current_paragraphs})

    print(f"Docling 원본 섹션 수: {len(raw_sections)}")

    # ========================================
    # 2단계: 후처리
    # ========================================
    print(f"\n{'=' * 60}")
    print("2단계: 후처리 (헤더/푸터 필터링 + 섹션 계층 재구성)")
    print("=" * 60)

    # 2-1. 헤더/푸터 필터링: 5회 이상 반복되는 제목
    title_counts = Counter(s['title'] for s in raw_sections)
    repeated_titles = {t for t, c in title_counts.items() if c >= 5}
    print(f"헤더/푸터 패턴 ({len(repeated_titles)}종):")
    for t in repeated_titles:
        print(f"  [{title_counts[t]}회] {t[:50]}")

    # 필터링: 반복 제목 섹션의 문단은 직전 유효 섹션에 병합
    filtered_sections = []
    for sec in raw_sections:
        if is_header_footer(sec['title'], repeated_titles):
            # 직전 섹션에 문단 병합
            if filtered_sections:
                filtered_sections[-1]['paragraphs'].extend(sec['paragraphs'])
        else:
            filtered_sections.append(sec)

    print(f"\n필터링 후 섹션 수: {len(filtered_sections)}")

    # 2-2. 섹션 계층 분류
    for sec in filtered_sections:
        sec['depth'] = classify_depth(sec['title'])

    depth_counts = Counter(s['depth'] for s in filtered_sections)
    print(f"\n계층별 섹션 수:")
    print(f"  depth=1 (대목차): {depth_counts.get(1, 0)}개")
    print(f"  depth=2 (중목차): {depth_counts.get(2, 0)}개")
    print(f"  depth=3 (소목차): {depth_counts.get(3, 0)}개")

    # 2-3. 중목차 단위로 청킹 (소목차는 상위 중목차에 병합)
    chunks = []
    current_chunk = None
    current_depth1 = "서두"

    for sec in filtered_sections:
        if sec['depth'] == 1:
            # 대목차: 이전 청크 확정, 새 대목차 시작
            if current_chunk and current_chunk['paragraphs']:
                chunks.append(current_chunk)
            current_depth1 = sec['title']
            current_chunk = {
                'depth1': current_depth1,
                'depth2': sec['title'],
                'title': sec['title'],
                'paragraphs': list(sec['paragraphs']),
                'sub_sections': [],
            }
        elif sec['depth'] == 2:
            # 중목차: 이전 청크 확정, 새 청크 시작
            if current_chunk and current_chunk['paragraphs']:
                chunks.append(current_chunk)
            current_chunk = {
                'depth1': current_depth1,
                'depth2': sec['title'],
                'title': sec['title'],
                'paragraphs': list(sec['paragraphs']),
                'sub_sections': [],
            }
        else:
            # 소목차: 현재 청크에 병합
            if current_chunk is None:
                current_chunk = {
                    'depth1': current_depth1,
                    'depth2': "서두",
                    'title': sec['title'],
                    'paragraphs': [],
                    'sub_sections': [],
                }
            current_chunk['paragraphs'].extend(sec['paragraphs'])
            current_chunk['sub_sections'].append(sec['title'])

    if current_chunk and current_chunk['paragraphs']:
        chunks.append(current_chunk)

    # 토큰 수 계산
    for chunk in chunks:
        full_text = '\n'.join(chunk['paragraphs'])
        chunk['text'] = full_text
        chunk['tokens'] = estimate_tokens(full_text)
        chunk['para_count'] = len(chunk['paragraphs'])

    print(f"\n{'=' * 60}")
    print("3단계: 청킹 결과")
    print("=" * 60)

    print(f"\n청크 수: {len(chunks)}")
    tokens = [c['tokens'] for c in chunks]
    print(f"토큰 통계:")
    print(f"  평균:   {np.mean(tokens):.0f}")
    print(f"  중앙값: {np.median(tokens):.0f}")
    print(f"  최소:   {np.min(tokens)}")
    print(f"  최대:   {np.max(tokens)}")

    under = sum(1 for t in tokens if t < 512)
    normal = sum(1 for t in tokens if 512 <= t <= 2048)
    over = sum(1 for t in tokens if t > 2048)
    print(f"\n  < 512 토큰:    {under}개 ({under/len(tokens)*100:.1f}%)")
    print(f"  512~2048 토큰: {normal}개 ({normal/len(tokens)*100:.1f}%)")
    print(f"  > 2048 토큰:   {over}개 ({over/len(tokens)*100:.1f}%)")

    # 청크 샘플
    print(f"\n청크 샘플 (상위 15개):")
    for i, c in enumerate(chunks[:15]):
        subs = f" (소목차: {len(c['sub_sections'])}개)" if c['sub_sections'] else ""
        print(f"  [{c['tokens']:5d}tok, {c['para_count']:3d}문단] [{c['depth1'][:15]}] > {c['depth2'][:30]}{subs}")

    # ========================================
    # 4단계: Proposition 추출 + 유사도 검증
    # ========================================
    # 문단 2개 이상인 청크만 대상
    target_chunks = [c for c in chunks if c['para_count'] >= 2]

    print(f"\n{'=' * 60}")
    print(f"4단계: Proposition 검증 (대상: {len(target_chunks)}개 청크)")
    print("=" * 60)

    results = []
    for i, chunk in enumerate(target_chunks):
        if i % 10 == 0:
            print(f"  진행: {i}/{len(target_chunks)}...")

        try:
            proposition = extract_proposition(chunk['text'])
            texts_to_embed = [proposition] + chunk['paragraphs']
            embeddings = get_embeddings(texts_to_embed)

            prop_emb = embeddings[0]
            para_embs = embeddings[1:]

            sims = [cosine_sim(prop_emb, p) for p in para_embs]

            results.append({
                'title': chunk['depth2'],
                'depth1': chunk['depth1'],
                'tokens': chunk['tokens'],
                'para_count': chunk['para_count'],
                'sub_count': len(chunk['sub_sections']),
                'proposition': proposition,
                'sims': sims,
                'mean_sim': np.mean(sims),
                'median_sim': np.median(sims),
                'max_sim': np.max(sims),
                'min_sim': np.min(sims),
                'std_sim': np.std(sims),
            })
        except Exception as e:
            print(f"  청크{i} 실패: {e}")
            continue

    print(f"\n처리 완료: {len(results)}개 청크\n")

    if not results:
        print("결과 없음")
        return

    # ========================================
    # 5단계: 결과
    # ========================================
    print("=" * 60)
    print("5단계: 결과")
    print("=" * 60)

    all_sims = []
    for r in results:
        all_sims.extend(r['sims'])

    all_means = [r['mean_sim'] for r in results]
    all_stds = [r['std_sim'] for r in results]

    print(f"\n[전체 Proposition ↔ 문단 유사도] (총 {len(all_sims)}쌍)")
    print(f"  평균:   {np.mean(all_sims):.4f}")
    print(f"  중앙값: {np.median(all_sims):.4f}")
    print(f"  최대:   {np.max(all_sims):.4f}")
    print(f"  최소:   {np.min(all_sims):.4f}")
    print(f"  표준편차: {np.std(all_sims):.4f}")

    print(f"\n[청크별 평균 유사도 통계] ({len(results)}개 청크)")
    print(f"  평균의 평균:   {np.mean(all_means):.4f}")
    print(f"  평균의 중앙값: {np.median(all_means):.4f}")
    print(f"  평균의 최대:   {np.max(all_means):.4f}")
    print(f"  평균의 최소:   {np.min(all_means):.4f}")

    print(f"\n[청크 내 유사도 편차]")
    print(f"  평균 표준편차: {np.mean(all_stds):.4f}")

    t_stat, p_value = stats.ttest_1samp(all_sims, 0.5)
    print(f"\n[t-test] 유사도 > 0.5 ?")
    print(f"  t-statistic: {t_stat:.4f}")
    print(f"  p-value:     {p_value:.2e}")

    # 이전 테스트 비교
    print(f"\n{'=' * 60}")
    print("이전 테스트 비교")
    print("=" * 60)
    print(f"{'':>30} | {'유사도':>8} | {'표준편차':>8}")
    print(f"  {'-' * 55}")
    print(f"  {'문단 Prop ↔ 문장 (이전)':>28} | {'0.6181':>8} | {'0.1053':>8}")
    print(f"  {'섹션 Prop ↔ 문단 (이전)':>28} | {'0.5326':>8} | {'0.1115':>8}")
    print(f"  {'중목차 Prop ↔ 문단 (현재)':>28} | {np.mean(all_sims):>8.4f} | {np.mean(all_stds):>8.4f}")

    # 토큰 구간별 유사도
    print(f"\n{'=' * 60}")
    print("토큰 구간별 유사도")
    print("=" * 60)

    tok_groups = {}
    for r in results:
        t = r['tokens']
        if t < 200:
            key = "< 200"
        elif t < 512:
            key = "200~512"
        elif t < 1024:
            key = "512~1024"
        elif t < 2048:
            key = "1024~2048"
        else:
            key = "2048+"
        if key not in tok_groups:
            tok_groups[key] = []
        tok_groups[key].append(r['mean_sim'])

    order = ["< 200", "200~512", "512~1024", "1024~2048", "2048+"]
    print(f"\n  {'토큰 구간':>12} | {'청크 수':>6} | {'평균 유사도':>10}")
    print(f"  {'-' * 40}")
    for key in order:
        if key in tok_groups:
            vals = tok_groups[key]
            print(f"  {key:>12} | {len(vals):>6} | {np.mean(vals):>10.4f}")

    # 유사도 구간별 분포
    print(f"\n{'=' * 60}")
    print("유사도 구간별 분포")
    print("=" * 60)
    arr = np.array(all_sims)
    bins = [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
    print(f"\n{'구간':>10} | {'비율':>8} | 분포")
    print("-" * 50)
    for low, high in bins:
        cnt = np.sum((arr >= low) & (arr < high))
        pct = cnt / len(arr) * 100
        bar = "█" * int(pct / 2)
        print(f"  {low:.1f}~{high:.1f} | {pct:6.1f}% | {bar}")

    # 샘플
    print(f"\n{'=' * 60}")
    print("샘플")
    print("=" * 60)
    sorted_results = sorted(results, key=lambda x: x['mean_sim'], reverse=True)

    print("\n[유사도 높은 상위 5개]")
    for r in sorted_results[:5]:
        print(f"  평균={r['mean_sim']:.3f} | {r['tokens']}tok, {r['para_count']}문단, 소목차{r['sub_count']}개 | [{r['title'][:35]}]")
        print(f"    Prop: {r['proposition'][:80]}")

    print("\n[유사도 낮은 하위 5개]")
    for r in sorted_results[-5:]:
        print(f"  평균={r['mean_sim']:.3f} | {r['tokens']}tok, {r['para_count']}문단, 소목차{r['sub_count']}개 | [{r['title'][:35]}]")
        print(f"    Prop: {r['proposition'][:80]}")

    # 시각화
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        font_path = '/usr/share/fonts/truetype/nanum/NanumSquareRoundR.ttf'
        try:
            plt.rcParams['font.family'] = font_manager.FontProperties(fname=font_path).get_name()
        except:
            pass
        plt.rcParams['axes.unicode_minus'] = False

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))

        # (1) 유사도 히스토그램
        ax = axes[0][0]
        ax.hist(all_sims, bins=40, color='steelblue', alpha=0.8, edgecolor='white')
        ax.axvline(np.mean(all_sims), color='red', linestyle='--', label=f'Current: {np.mean(all_sims):.3f}')
        ax.axvline(0.5326, color='green', linestyle='--', alpha=0.7, label='Section test: 0.533')
        ax.axvline(0.6181, color='orange', linestyle='--', alpha=0.7, label='Paragraph test: 0.618')
        ax.set_xlabel('Cosine Similarity')
        ax.set_ylabel('Count')
        ax.set_title('Chunk Proposition ↔ Paragraph Similarity')
        ax.legend(fontsize=8)

        # (2) 청크별 평균 유사도
        ax = axes[0][1]
        ax.hist(all_means, bins=30, color='green', alpha=0.8, edgecolor='white')
        ax.axvline(np.mean(all_means), color='red', linestyle='--', label=f'Mean: {np.mean(all_means):.3f}')
        ax.set_xlabel('Mean Similarity per Chunk')
        ax.set_ylabel('Count')
        ax.set_title('Per-Chunk Mean Similarity')
        ax.legend()

        # (3) 토큰 수 vs 유사도
        ax = axes[1][0]
        tok_vals = [r['tokens'] for r in results]
        mean_vals = [r['mean_sim'] for r in results]
        ax.scatter(tok_vals, mean_vals, alpha=0.5, s=20, color='steelblue')
        z = np.polyfit(tok_vals, mean_vals, 1)
        p = np.poly1d(z)
        x_line = np.linspace(min(tok_vals), max(tok_vals), 100)
        ax.plot(x_line, p(x_line), 'r--', alpha=0.7, label=f'Trend (slope={z[0]:.6f})')
        ax.set_xlabel('Chunk Token Count')
        ax.set_ylabel('Mean Proposition-Paragraph Similarity')
        ax.set_title('Chunk Size vs Representativeness')
        ax.legend()

        # (4) 토큰 분포
        ax = axes[1][1]
        ax.hist(tokens, bins=40, color='coral', alpha=0.8, edgecolor='white')
        ax.axvline(512, color='green', linestyle='--', label='Min: 512')
        ax.axvline(1024, color='blue', linestyle='--', label='Target: 1024')
        ax.axvline(2048, color='red', linestyle='--', label='Max: 2048')
        ax.set_xlabel('Token Count')
        ax.set_ylabel('Count')
        ax.set_title('Chunk Token Distribution')
        ax.legend()

        plt.suptitle('Chunking Strategy Test: Post-processed Docling → 중목차 Chunks',
                     fontsize=13, fontweight='bold', y=1.01)
        plt.tight_layout()

        output_path = str(Path(__file__).parent / "chunking_strategy_test.png")
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n시각화 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n시각화 실패: {e}")

    # 최종 판정
    print(f"\n{'=' * 60}")
    print("최종 판정")
    print("=" * 60)
    mean_sim = np.mean(all_sims)
    mean_std = np.mean(all_stds)
    in_range = sum(1 for t in tokens if 512 <= t <= 2048)
    in_range_pct = in_range / len(tokens) * 100

    print(f"\n  Proposition ↔ 문단 평균 유사도: {mean_sim:.4f}")
    print(f"  청크 내 표준편차:              {mean_std:.4f}")
    print(f"  적정 토큰 범위(512~2048) 비율: {in_range_pct:.1f}%")
    print(f"  전체 청크 수:                  {len(chunks)}")


if __name__ == "__main__":
    analyze()
