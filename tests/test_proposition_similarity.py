"""
가설: "문단의 Proposition 하나로 해당 문단의 의미를 대표할 수 있다."

검증 방법:
1. Docling으로 PDF 파싱 → 문단 추출
2. 각 문단을 LLM에게 전달 → Proposition 1개 추출
3. Proposition 임베딩 + 문단 내 각 문장 임베딩
4. Proposition ↔ 각 문장 cosine similarity 계산
5. 전체 문단에 대해 반복 → 유사도 통계
"""

import re
import numpy as np
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


def split_sentences(text: str) -> list[str]:
    """문단을 문장 단위로 분리"""
    sentences = re.split(r'(?<=[.!?다요음임])\s+', text)
    result = []
    for s in sentences:
        sub = s.split('\n')
        result.extend(sub)
    result = [s.strip() for s in result if s.strip() and len(s.strip()) >= 10]
    return result


def get_embeddings(texts: list[str], batch_size: int = 64) -> np.ndarray:
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = embed_client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([d.embedding for d in resp.data])
    return np.array(all_embeddings)


def extract_proposition(paragraph: str) -> str:
    """LLM으로 문단의 핵심 Proposition 1개 추출"""
    resp = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "당신은 문서 분석 전문가입니다. 주어진 문단의 핵심 내용을 하나의 명제(Proposition)로 요약하세요. 반드시 한 문장으로만 답하세요."},
            {"role": "user", "content": f"다음 문단의 핵심을 하나의 명제로 요약하세요:\n\n{paragraph[:1000]}"}
        ],
        max_tokens=200,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def analyze():
    # 1. Docling 파싱
    print("=" * 60)
    print("1단계: Docling 파싱")
    print("=" * 60)

    converter = DocumentConverter()
    result = converter.convert(PDF_PATH)
    doc = result.document

    paragraphs = []
    current_section = "서두"

    for item, _level in doc.iterate_items():
        label = item.label
        if label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text:
                current_section = text
            continue

        if label in [DocItemLabel.PARAGRAPH, DocItemLabel.TEXT, DocItemLabel.LIST_ITEM]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            sentences = split_sentences(text)
            # 문장이 2개 이상인 문단만 (1문장이면 비교 의미 없음)
            if len(sentences) >= 2:
                paragraphs.append({
                    'text': text,
                    'section': current_section,
                    'sentences': sentences,
                })

    print(f"대상 문단 수 (문장 2개 이상): {len(paragraphs)}")
    print(f"문단당 평균 문장 수: {np.mean([len(p['sentences']) for p in paragraphs]):.1f}\n")

    # 2. Proposition 추출 + 임베딩 + 유사도 계산
    print("=" * 60)
    print("2단계: Proposition 추출 및 유사도 계산")
    print("=" * 60)

    para_results = []  # 각 문단별 결과

    for i, para in enumerate(paragraphs):
        if i % 50 == 0:
            print(f"  진행: {i}/{len(paragraphs)}...")

        try:
            # Proposition 추출
            proposition = extract_proposition(para['text'])

            # 임베딩: Proposition + 각 문장
            texts_to_embed = [proposition] + para['sentences']
            embeddings = get_embeddings(texts_to_embed)

            prop_emb = embeddings[0]
            sentence_embs = embeddings[1:]

            # Proposition ↔ 각 문장 유사도
            sims = [cosine_sim(prop_emb, s_emb) for s_emb in sentence_embs]

            para_results.append({
                'idx': i,
                'section': para['section'],
                'num_sentences': len(para['sentences']),
                'proposition': proposition,
                'sims': sims,
                'mean_sim': np.mean(sims),
                'median_sim': np.median(sims),
                'max_sim': np.max(sims),
                'min_sim': np.min(sims),
                'std_sim': np.std(sims),
            })
        except Exception as e:
            print(f"  문단{i} 처리 실패: {e}")
            continue

    print(f"\n처리 완료: {len(para_results)}개 문단\n")

    if not para_results:
        print("결과 없음")
        return

    # 3. 전체 통계
    print("=" * 60)
    print("3단계: 결과")
    print("=" * 60)

    all_means = [r['mean_sim'] for r in para_results]
    all_medians = [r['median_sim'] for r in para_results]
    all_maxs = [r['max_sim'] for r in para_results]
    all_mins = [r['min_sim'] for r in para_results]
    all_stds = [r['std_sim'] for r in para_results]

    # 개별 문장 유사도 전체
    all_sims = []
    for r in para_results:
        all_sims.extend(r['sims'])

    print(f"\n[전체 Proposition ↔ 문장 유사도] (총 {len(all_sims)}쌍)")
    print(f"  평균:   {np.mean(all_sims):.4f}")
    print(f"  중앙값: {np.median(all_sims):.4f}")
    print(f"  최대:   {np.max(all_sims):.4f}")
    print(f"  최소:   {np.min(all_sims):.4f}")
    print(f"  표준편차: {np.std(all_sims):.4f}")

    print(f"\n[문단별 평균 유사도 통계] ({len(para_results)}개 문단)")
    print(f"  평균의 평균:   {np.mean(all_means):.4f}")
    print(f"  평균의 중앙값: {np.median(all_means):.4f}")
    print(f"  평균의 최대:   {np.max(all_means):.4f}")
    print(f"  평균의 최소:   {np.min(all_means):.4f}")

    print(f"\n[문단별 최대 유사도 통계]")
    print(f"  평균: {np.mean(all_maxs):.4f}")
    print(f"  중앙: {np.median(all_maxs):.4f}")

    print(f"\n[문단별 최소 유사도 통계]")
    print(f"  평균: {np.mean(all_mins):.4f}")
    print(f"  중앙: {np.median(all_mins):.4f}")

    print(f"\n[문단 내 유사도 편차 (Proposition ↔ 문장들 간 분산)]")
    print(f"  평균 표준편차: {np.mean(all_stds):.4f}")
    print(f"  → 낮을수록 Proposition이 문단 전체를 균일하게 대표")

    # t-test: Proposition 유사도가 0.5보다 유의미하게 높은지
    t_stat, p_value = stats.ttest_1samp(all_sims, 0.5)
    print(f"\n[t-test] Proposition ↔ 문장 유사도 > 0.5 ?")
    print(f"  t-statistic: {t_stat:.4f}")
    print(f"  p-value:     {p_value:.2e}")

    # 4. 유사도 구간별 분포
    print(f"\n{'=' * 60}")
    print("유사도 구간별 분포")
    print("=" * 60)

    arr = np.array(all_sims)
    bins = [(0, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]

    print(f"\n{'구간':>10} | {'비율':>8} | 분포")
    print("-" * 50)
    for low, high in bins:
        cnt = np.sum((arr >= low) & (arr < high))
        pct = cnt / len(arr) * 100
        bar = "█" * int(pct / 2)
        print(f"  {low:.1f}~{high:.1f} | {pct:6.1f}% | {bar}")

    # 5. 샘플 출력
    print(f"\n{'=' * 60}")
    print("샘플 (유사도 높은/낮은 문단)")
    print("=" * 60)

    sorted_results = sorted(para_results, key=lambda x: x['mean_sim'], reverse=True)

    print("\n[유사도 높은 상위 5개]")
    for r in sorted_results[:5]:
        print(f"  평균={r['mean_sim']:.3f} | 문장수={r['num_sentences']} | [{r['section'][:25]}]")
        print(f"    Prop: {r['proposition'][:80]}")

    print("\n[유사도 낮은 하위 5개]")
    for r in sorted_results[-5:]:
        print(f"  평균={r['mean_sim']:.3f} | 문장수={r['num_sentences']} | [{r['section'][:25]}]")
        print(f"    Prop: {r['proposition'][:80]}")

    # 6. 시각화
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

        # (1) Proposition ↔ 문장 유사도 히스토그램
        ax = axes[0][0]
        ax.hist(all_sims, bins=40, color='steelblue', alpha=0.8, edgecolor='white')
        ax.axvline(np.mean(all_sims), color='red', linestyle='--', label=f'Mean: {np.mean(all_sims):.3f}')
        ax.axvline(np.median(all_sims), color='orange', linestyle='--', label=f'Median: {np.median(all_sims):.3f}')
        ax.set_xlabel('Cosine Similarity')
        ax.set_ylabel('Count')
        ax.set_title('Proposition ↔ Sentence Similarity Distribution')
        ax.legend()

        # (2) 문단별 평균 유사도 히스토그램
        ax = axes[0][1]
        ax.hist(all_means, bins=30, color='green', alpha=0.8, edgecolor='white')
        ax.axvline(np.mean(all_means), color='red', linestyle='--', label=f'Mean: {np.mean(all_means):.3f}')
        ax.set_xlabel('Mean Similarity per Paragraph')
        ax.set_ylabel('Count')
        ax.set_title('Per-Paragraph Mean Similarity')
        ax.legend()

        # (3) 문단별 min/max/mean 범위
        ax = axes[1][0]
        sorted_by_mean = sorted(para_results, key=lambda x: x['mean_sim'])
        x = range(len(sorted_by_mean))
        mins = [r['min_sim'] for r in sorted_by_mean]
        means = [r['mean_sim'] for r in sorted_by_mean]
        maxs = [r['max_sim'] for r in sorted_by_mean]
        ax.fill_between(x, mins, maxs, alpha=0.3, color='steelblue', label='Min-Max range')
        ax.plot(x, means, color='red', linewidth=0.8, label='Mean')
        ax.set_xlabel('Paragraphs (sorted by mean similarity)')
        ax.set_ylabel('Cosine Similarity')
        ax.set_title('Per-Paragraph Similarity Range')
        ax.legend()

        # (4) 문단 내 표준편차 분포
        ax = axes[1][1]
        ax.hist(all_stds, bins=30, color='coral', alpha=0.8, edgecolor='white')
        ax.axvline(np.mean(all_stds), color='red', linestyle='--', label=f'Mean std: {np.mean(all_stds):.3f}')
        ax.set_xlabel('Std of Similarities within Paragraph')
        ax.set_ylabel('Count')
        ax.set_title('Intra-Paragraph Similarity Variance\n(lower = Proposition represents all sentences equally)')
        ax.legend()

        plt.suptitle('Hypothesis: A single Proposition can represent an entire paragraph',
                     fontsize=13, fontweight='bold', y=1.01)
        plt.tight_layout()

        output_path = str(Path(__file__).parent / "proposition_similarity_test.png")
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n시각화 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n시각화 실패: {e}")

    # 7. 최종 판정
    print(f"\n{'=' * 60}")
    print("최종 판정")
    print("=" * 60)
    mean_sim = np.mean(all_sims)
    mean_std = np.mean(all_stds)
    print(f"\n  Proposition ↔ 문장 평균 유사도: {mean_sim:.4f}")
    print(f"  문단 내 유사도 표준편차:        {mean_std:.4f}")

    if mean_sim > 0.6 and mean_std < 0.15:
        print(f"\n  ✓ Proposition이 문단을 강하게 대표")
        print(f"    → 문단 전체 임베딩 대신 Proposition 임베딩으로 대체 가능")
    elif mean_sim > 0.5:
        print(f"\n  △ Proposition이 문단을 어느 정도 대표")
        print(f"    → 대체 가능하나, 일부 문단에서 정보 손실 존재")
    else:
        print(f"\n  ✗ Proposition 대표성 부족")
        print(f"    → 문단 전체 임베딩이 필요")


if __name__ == "__main__":
    analyze()
