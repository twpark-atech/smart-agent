"""
가설: "같은 섹션 내 문단 간 임베딩 유사도는 다른 섹션 문단 간 유사도보다 높을 것이다."

테스트:
1. Docling으로 PDF 파싱 → 섹션/문단 구조 추출
2. 문단 전체를 하나의 텍스트로 임베딩
3. 같은 섹션 내 인접 문단 유사도 vs 다른 섹션 인접 문단 유사도 비교
4. 유사도 분포 시각화
"""

import numpy as np
from openai import OpenAI
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DocItemLabel

# ── 설정 ──
PDF_PATH = "/mnt/d/스마트제조혁신기술개발/data/7.기능성 가공 기술 실무.pdf"
EMBEDDING_URL = "http://112.163.62.170:8032/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"

client = OpenAI(base_url=EMBEDDING_URL, api_key="test")


def get_embeddings(texts: list[str], batch_size: int = 64) -> np.ndarray:
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([d.embedding for d in resp.data])
        if (i // batch_size) % 10 == 0 and i > 0:
            print(f"  {i}/{len(texts)} 완료...")
    return np.array(all_embeddings)


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

    # 구조 추출
    items = []
    current_section = "서두"
    section_idx = 0

    for item, _level in doc.iterate_items():
        label = item.label

        if label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text:
                current_section = text
                section_idx += 1
            continue

        if label in [DocItemLabel.PARAGRAPH, DocItemLabel.TEXT, DocItemLabel.LIST_ITEM]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text and len(text) >= 20:  # 최소 20자 이상
                items.append({
                    'text': text,
                    'section': current_section,
                    'section_idx': section_idx,
                })

    print(f"문단 수: {len(items)}")
    print(f"섹션 수: {section_idx + 1}\n")

    # 2. 문단 임베딩
    print("=" * 60)
    print("2단계: 문단 임베딩")
    print("=" * 60)

    texts = [it['text'] for it in items]
    embeddings = get_embeddings(texts)
    print(f"임베딩 완료: {embeddings.shape}\n")

    # 3. 인접 문단 유사도 계산
    print("=" * 60)
    print("3단계: 인접 문단 유사도 비교")
    print("=" * 60)

    same_section_sims = []     # 같은 섹션 내 인접 문단
    diff_section_sims = []     # 다른 섹션 간 인접 문단

    for i in range(len(items) - 1):
        sim = cosine_sim(embeddings[i], embeddings[i + 1])
        if items[i]['section_idx'] == items[i + 1]['section_idx']:
            same_section_sims.append(sim)
        else:
            diff_section_sims.append(sim)

    same_arr = np.array(same_section_sims)
    diff_arr = np.array(diff_section_sims)

    print(f"\n[같은 섹션 내 인접 문단 유사도]")
    print(f"  평균:   {np.mean(same_arr):.4f}")
    print(f"  중앙값: {np.median(same_arr):.4f}")
    print(f"  표준편차: {np.std(same_arr):.4f}")
    print(f"  표본:   {len(same_section_sims)}쌍")

    print(f"\n[다른 섹션 간 인접 문단 유사도]")
    print(f"  평균:   {np.mean(diff_arr):.4f}")
    print(f"  중앙값: {np.median(diff_arr):.4f}")
    print(f"  표준편차: {np.std(diff_arr):.4f}")
    print(f"  표본:   {len(diff_section_sims)}쌍")

    gap = np.mean(same_arr) - np.mean(diff_arr)
    print(f"\n[차이] 같은 섹션 - 다른 섹션 = {gap:+.4f}")

    # 통계적 유의성 (t-test)
    from scipy import stats
    t_stat, p_value = stats.ttest_ind(same_section_sims, diff_section_sims)
    print(f"\n[t-test]")
    print(f"  t-statistic: {t_stat:.4f}")
    print(f"  p-value:     {p_value:.2e}")
    if p_value < 0.001:
        print(f"  → 통계적으로 매우 유의미한 차이 (p < 0.001)")
    elif p_value < 0.05:
        print(f"  → 통계적으로 유의미한 차이 (p < 0.05)")
    else:
        print(f"  → 통계적으로 유의미하지 않음")

    # 4. 유사도 구간별 분포
    print(f"\n{'=' * 60}")
    print("4단계: 유사도 구간별 분포")
    print("=" * 60)

    bins = [(0, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]

    print(f"\n{'구간':>10} | {'같은 섹션':>10} | {'다른 섹션':>10} | 분포")
    print("-" * 70)
    for low, high in bins:
        same_cnt = np.sum((same_arr >= low) & (same_arr < high))
        diff_cnt = np.sum((diff_arr >= low) & (diff_arr < high))
        same_pct = same_cnt / len(same_arr) * 100 if len(same_arr) > 0 else 0
        diff_pct = diff_cnt / len(diff_arr) * 100 if len(diff_arr) > 0 else 0
        bar_same = "█" * int(same_pct / 2)
        bar_diff = "░" * int(diff_pct / 2)
        print(f"  {low:.1f}~{high:.1f} | {same_pct:8.1f}% | {diff_pct:8.1f}% | {bar_same}{bar_diff}")

    # 5. 시각화
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

        # (1) 히스토그램 비교
        ax = axes[0][0]
        ax.hist(same_section_sims, bins=30, alpha=0.7, label=f'Same section (n={len(same_section_sims)})', color='green', density=True)
        ax.hist(diff_section_sims, bins=30, alpha=0.7, label=f'Diff section (n={len(diff_section_sims)})', color='red', density=True)
        ax.axvline(np.mean(same_arr), color='darkgreen', linestyle='--', label=f'Same mean: {np.mean(same_arr):.3f}')
        ax.axvline(np.mean(diff_arr), color='darkred', linestyle='--', label=f'Diff mean: {np.mean(diff_arr):.3f}')
        ax.set_xlabel('Cosine Similarity')
        ax.set_ylabel('Density')
        ax.set_title('Adjacent Paragraph Similarity Distribution')
        ax.legend(fontsize=8)

        # (2) 인접 문단 유사도 흐름 (앞 150개)
        ax = axes[0][1]
        limit = min(150, len(items) - 1)
        x_vals = range(limit)
        y_vals = []
        colors = []
        for i in range(limit):
            sim = cosine_sim(embeddings[i], embeddings[i + 1])
            y_vals.append(sim)
            if items[i]['section_idx'] != items[i + 1]['section_idx']:
                colors.append('red')
            else:
                colors.append('green')
        ax.bar(x_vals, y_vals, color=colors, width=1.0, alpha=0.7)
        ax.set_xlabel('Paragraph index')
        ax.set_ylabel('Cosine Similarity')
        ax.set_title('Adjacent Paragraph Similarity Flow\n(green=same section, red=boundary)')
        ax.set_ylim(0, 1)

        # (3) 박스플롯
        ax = axes[1][0]
        bp = ax.boxplot([same_section_sims, diff_section_sims],
                        labels=['Same Section', 'Diff Section'],
                        patch_artist=True)
        bp['boxes'][0].set_facecolor('lightgreen')
        bp['boxes'][1].set_facecolor('lightsalmon')
        ax.set_ylabel('Cosine Similarity')
        ax.set_title('Similarity Distribution (Box Plot)')

        # (4) 히트맵 (앞 100개 문단)
        ax = axes[1][1]
        hlimit = min(100, len(items))
        from sklearn.metrics.pairwise import cosine_similarity as cs
        sim_matrix = cs(embeddings[:hlimit])
        im = ax.imshow(sim_matrix, cmap='RdYlGn', vmin=0, vmax=1)

        section_ids = [it['section_idx'] for it in items[:hlimit]]
        for idx in range(1, hlimit):
            if section_ids[idx] != section_ids[idx - 1]:
                ax.axhline(y=idx - 0.5, color='blue', linewidth=1.5, alpha=0.7)
                ax.axvline(x=idx - 0.5, color='blue', linewidth=1.5, alpha=0.7)

        plt.colorbar(im, ax=ax, label='Cosine Similarity')
        ax.set_title('Paragraph Similarity Heatmap\n(blue=section boundary)')

        plt.suptitle('Hypothesis: Paragraphs in same section have higher embedding similarity',
                     fontsize=13, fontweight='bold', y=1.01)
        plt.tight_layout()

        output_path = "/home/atech/Projects/smart-agent/paragraph_similarity_test.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n시각화 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n시각화 실패: {e}")

    # 6. 최종 판정
    print(f"\n{'=' * 60}")
    print("최종 판정")
    print("=" * 60)
    print(f"\n  가설: '같은 섹션 내 인접 문단은 다른 섹션 인접 문단보다 유사도가 높다'")
    print(f"\n  같은 섹션 평균: {np.mean(same_arr):.4f}")
    print(f"  다른 섹션 평균: {np.mean(diff_arr):.4f}")
    print(f"  차이:          {gap:+.4f}")
    print(f"  p-value:       {p_value:.2e}")

    if gap > 0.05 and p_value < 0.05:
        print(f"\n  ✓ 가설 지지")
        print(f"    → 문단 임베딩 유사도로 섹션 경계 탐지 가능")
        print(f"    → 다만 차이({gap:.4f})가 크지 않아 임계값 설정 주의 필요")
    elif gap > 0 and p_value < 0.05:
        print(f"\n  △ 약한 지지")
        print(f"    → 통계적으로 유의미하나 실용적 차이 미미")
        print(f"    → 임베딩 단독으로 병합 판단하기엔 부족, LLM 보조 필요")
    else:
        print(f"\n  ✗ 가설 기각")


if __name__ == "__main__":
    analyze()
