"""
가설: "섹션의 Proposition 하나로 해당 섹션의 의미를 대표할 수 있다."

검증 방법:
1. Docling으로 PDF 파싱 → 섹션(소제목) 단위 추출
2. 각 섹션 텍스트를 LLM에게 전달 → Proposition 1개 추출
3. 섹션 내 각 문단을 개별 임베딩
4. Proposition ↔ 각 문단 cosine similarity 계산
5. 전체 섹션에 대해 반복 → 유사도 통계

비교: 이전 문단 단위 테스트 (평균 0.6181) vs 섹션 단위
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


def analyze():
    # 1. Docling 파싱
    print("=" * 60)
    print("1단계: Docling 파싱 → 섹션 단위 추출")
    print("=" * 60)

    converter = DocumentConverter()
    result = converter.convert(PDF_PATH)
    doc = result.document

    # 섹션별로 문단 모으기
    sections = []
    current_section = "서두"
    current_paragraphs = []

    for item, _level in doc.iterate_items():
        label = item.label
        if label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text:
                if current_paragraphs:
                    sections.append({
                        'title': current_section,
                        'paragraphs': current_paragraphs,
                    })
                current_section = text
                current_paragraphs = []
            continue

        if label in [DocItemLabel.PARAGRAPH, DocItemLabel.TEXT, DocItemLabel.LIST_ITEM]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text and len(text) >= 10:
                current_paragraphs.append(text)

    if current_paragraphs:
        sections.append({
            'title': current_section,
            'paragraphs': current_paragraphs,
        })

    # 문단이 2개 이상인 섹션만 (1개면 비교 의미 없음)
    sections = [s for s in sections if len(s['paragraphs']) >= 2]

    print(f"대상 섹션 수 (문단 2개 이상): {len(sections)}")
    print(f"섹션당 평균 문단 수: {np.mean([len(s['paragraphs']) for s in sections]):.1f}\n")

    # 2. Proposition 추출 + 임베딩 + 유사도 계산
    print("=" * 60)
    print("2단계: 섹션별 Proposition 추출 및 유사도 계산")
    print("=" * 60)

    section_results = []

    for i, sec in enumerate(sections):
        if i % 50 == 0:
            print(f"  진행: {i}/{len(sections)}...")

        try:
            # 섹션 전체 텍스트
            full_text = '\n'.join(sec['paragraphs'])

            # Proposition 추출
            proposition = extract_proposition(full_text)

            # 임베딩: Proposition + 각 문단
            texts_to_embed = [proposition] + sec['paragraphs']
            embeddings = get_embeddings(texts_to_embed)

            prop_emb = embeddings[0]
            para_embs = embeddings[1:]

            # Proposition ↔ 각 문단 유사도
            sims = [cosine_sim(prop_emb, p_emb) for p_emb in para_embs]

            section_results.append({
                'idx': i,
                'title': sec['title'],
                'num_paragraphs': len(sec['paragraphs']),
                'proposition': proposition,
                'sims': sims,
                'mean_sim': np.mean(sims),
                'median_sim': np.median(sims),
                'max_sim': np.max(sims),
                'min_sim': np.min(sims),
                'std_sim': np.std(sims),
            })
        except Exception as e:
            print(f"  섹션{i} 처리 실패: {e}")
            continue

    print(f"\n처리 완료: {len(section_results)}개 섹션\n")

    if not section_results:
        print("결과 없음")
        return

    # 3. 전체 통계
    print("=" * 60)
    print("3단계: 결과")
    print("=" * 60)

    all_means = [r['mean_sim'] for r in section_results]
    all_stds = [r['std_sim'] for r in section_results]

    all_sims = []
    for r in section_results:
        all_sims.extend(r['sims'])

    print(f"\n[전체 Proposition ↔ 문단 유사도] (총 {len(all_sims)}쌍)")
    print(f"  평균:   {np.mean(all_sims):.4f}")
    print(f"  중앙값: {np.median(all_sims):.4f}")
    print(f"  최대:   {np.max(all_sims):.4f}")
    print(f"  최소:   {np.min(all_sims):.4f}")
    print(f"  표준편차: {np.std(all_sims):.4f}")

    print(f"\n[섹션별 평균 유사도 통계] ({len(section_results)}개 섹션)")
    print(f"  평균의 평균:   {np.mean(all_means):.4f}")
    print(f"  평균의 중앙값: {np.median(all_means):.4f}")
    print(f"  평균의 최대:   {np.max(all_means):.4f}")
    print(f"  평균의 최소:   {np.min(all_means):.4f}")

    print(f"\n[섹션별 최대 유사도 통계]")
    all_maxs = [r['max_sim'] for r in section_results]
    print(f"  평균: {np.mean(all_maxs):.4f}")
    print(f"  중앙: {np.median(all_maxs):.4f}")

    print(f"\n[섹션별 최소 유사도 통계]")
    all_mins = [r['min_sim'] for r in section_results]
    print(f"  평균: {np.mean(all_mins):.4f}")
    print(f"  중앙: {np.median(all_mins):.4f}")

    print(f"\n[섹션 내 유사도 편차]")
    print(f"  평균 표준편차: {np.mean(all_stds):.4f}")
    print(f"  → 낮을수록 Proposition이 섹션 전체를 균일하게 대표")

    # t-test
    t_stat, p_value = stats.ttest_1samp(all_sims, 0.5)
    print(f"\n[t-test] Proposition ↔ 문단 유사도 > 0.5 ?")
    print(f"  t-statistic: {t_stat:.4f}")
    print(f"  p-value:     {p_value:.2e}")

    # 4. 이전 문단 단위 결과와 비교
    print(f"\n{'=' * 60}")
    print("문단 단위 vs 섹션 단위 비교")
    print("=" * 60)
    print(f"{'':>25} | {'문단 단위':>10} | {'섹션 단위':>10}")
    print(f"-" * 55)
    print(f"  {'Prop ↔ 하위 유사도 평균':>23} | {'0.6181':>10} | {np.mean(all_sims):>10.4f}")
    print(f"  {'유사도 중앙값':>23} | {'0.6248':>10} | {np.median(all_sims):>10.4f}")
    print(f"  {'섹션 내 표준편차':>23} | {'0.1053':>10} | {np.mean(all_stds):>10.4f}")

    # 5. 유사도 구간별 분포
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

    # 6. 문단 수별 유사도 변화
    print(f"\n{'=' * 60}")
    print("섹션 내 문단 수별 평균 유사도")
    print("=" * 60)

    para_count_groups = {}
    for r in section_results:
        n = r['num_paragraphs']
        key = f"{n}" if n <= 10 else "11+"
        if key not in para_count_groups:
            para_count_groups[key] = []
        para_count_groups[key].append(r['mean_sim'])

    print(f"\n  {'문단 수':>8} | {'섹션 수':>6} | {'평균 유사도':>10} | 분포")
    print(f"  {'-' * 55}")
    for key in sorted(para_count_groups.keys(), key=lambda x: int(x.replace('+', ''))):
        sims_list = para_count_groups[key]
        mean = np.mean(sims_list)
        bar = "█" * int(mean * 30)
        print(f"  {key:>8} | {len(sims_list):>6} | {mean:>10.4f} | {bar}")

    # 7. 샘플
    print(f"\n{'=' * 60}")
    print("샘플 (유사도 높은/낮은 섹션)")
    print("=" * 60)

    sorted_results = sorted(section_results, key=lambda x: x['mean_sim'], reverse=True)

    print("\n[유사도 높은 상위 5개]")
    for r in sorted_results[:5]:
        print(f"  평균={r['mean_sim']:.3f} | 문단수={r['num_paragraphs']} | [{r['title'][:30]}]")
        print(f"    Prop: {r['proposition'][:80]}")

    print("\n[유사도 낮은 하위 5개]")
    for r in sorted_results[-5:]:
        print(f"  평균={r['mean_sim']:.3f} | 문단수={r['num_paragraphs']} | [{r['title'][:30]}]")
        print(f"    Prop: {r['proposition'][:80]}")

    # 8. 시각화
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
        ax.axvline(np.mean(all_sims), color='red', linestyle='--', label=f'Mean: {np.mean(all_sims):.3f}')
        ax.axvline(0.6181, color='green', linestyle='--', alpha=0.7, label=f'Paragraph test: 0.618')
        ax.set_xlabel('Cosine Similarity')
        ax.set_ylabel('Count')
        ax.set_title('Section Proposition ↔ Paragraph Similarity')
        ax.legend()

        # (2) 섹션별 평균 유사도
        ax = axes[0][1]
        ax.hist(all_means, bins=30, color='green', alpha=0.8, edgecolor='white')
        ax.axvline(np.mean(all_means), color='red', linestyle='--', label=f'Mean: {np.mean(all_means):.3f}')
        ax.set_xlabel('Mean Similarity per Section')
        ax.set_ylabel('Count')
        ax.set_title('Per-Section Mean Similarity')
        ax.legend()

        # (3) min/max/mean 범위
        ax = axes[1][0]
        sorted_by_mean = sorted(section_results, key=lambda x: x['mean_sim'])
        x = range(len(sorted_by_mean))
        mins = [r['min_sim'] for r in sorted_by_mean]
        means_plot = [r['mean_sim'] for r in sorted_by_mean]
        maxs = [r['max_sim'] for r in sorted_by_mean]
        ax.fill_between(x, mins, maxs, alpha=0.3, color='steelblue', label='Min-Max range')
        ax.plot(x, means_plot, color='red', linewidth=0.8, label='Mean')
        ax.set_xlabel('Sections (sorted by mean similarity)')
        ax.set_ylabel('Cosine Similarity')
        ax.set_title('Per-Section Similarity Range')
        ax.legend()

        # (4) 문단 수 vs 평균 유사도 산점도
        ax = axes[1][1]
        n_paras = [r['num_paragraphs'] for r in section_results]
        mean_sims = [r['mean_sim'] for r in section_results]
        ax.scatter(n_paras, mean_sims, alpha=0.5, s=20, color='steelblue')
        ax.set_xlabel('Number of Paragraphs in Section')
        ax.set_ylabel('Mean Proposition-Paragraph Similarity')
        ax.set_title('Section Size vs Proposition Representativeness')

        # 추세선
        z = np.polyfit(n_paras, mean_sims, 1)
        p = np.poly1d(z)
        x_line = np.linspace(min(n_paras), max(n_paras), 100)
        ax.plot(x_line, p(x_line), 'r--', alpha=0.7, label=f'Trend (slope={z[0]:.4f})')
        ax.legend()

        plt.suptitle('Section-level Proposition Representativeness Test',
                     fontsize=13, fontweight='bold', y=1.01)
        plt.tight_layout()

        output_path = "/home/atech/Projects/smart-agent/section_proposition_test.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n시각화 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n시각화 실패: {e}")

    # 9. 최종 판정
    print(f"\n{'=' * 60}")
    print("최종 판정")
    print("=" * 60)
    mean_sim = np.mean(all_sims)
    mean_std = np.mean(all_stds)
    prev_mean = 0.6181

    print(f"\n  섹션 Proposition ↔ 문단 평균 유사도: {mean_sim:.4f}")
    print(f"  문단 Proposition ↔ 문장 평균 유사도: {prev_mean:.4f} (이전 테스트)")
    print(f"  차이: {mean_sim - prev_mean:+.4f}")
    print(f"  섹션 내 표준편차: {mean_std:.4f}")

    if mean_sim > 0.55 and mean_std < 0.15:
        print(f"\n  ✓ 섹션 Proposition이 섹션을 충분히 대표")
        print(f"    → 섹션 단위 Proposition 검색 전략 유효")
    elif mean_sim > 0.45:
        print(f"\n  △ 부분적으로 대표")
        print(f"    → 문단 수가 많은 섹션에서 대표성 하락 가능, 보완 필요")
    else:
        print(f"\n  ✗ 대표성 부족")
        print(f"    → 문단 단위 Proposition 유지 권장")


if __name__ == "__main__":
    analyze()
