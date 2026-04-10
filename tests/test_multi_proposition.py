"""
가설: "문단 5개 이상 섹션에서 Proposition 2개를 추출하면 대표성이 향상된다."

비교:
- Proposition 1개 (이전 테스트: 문단 5개 이상 섹션 평균 ~0.50)
- Proposition 2개 (각 문단에 대해 더 유사한 Proposition의 유사도 사용)
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


def extract_single_proposition(text: str) -> str:
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


def extract_two_propositions(text: str) -> list[str]:
    resp = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "당신은 문서 분석 전문가입니다. 주어진 섹션의 핵심 내용을 2개의 서로 다른 관점의 명제(Proposition)로 요약하세요. 반드시 한국어로 답하세요."},
            {"role": "user", "content": f"다음 섹션의 핵심을 2개의 명제로 요약하세요. 각 명제는 서로 다른 관점이어야 합니다.\n\n형식:\n1. (첫 번째 명제)\n2. (두 번째 명제)\n\n섹션 내용:\n{text[:2000]}"}
        ],
        max_tokens=300,
        temperature=0.0,
    )
    content = resp.choices[0].message.content.strip()
    # 파싱: "1. ..." "2. ..." 형태
    props = []
    for line in content.split('\n'):
        line = line.strip()
        line = re.sub(r'^[12][.)\]]\s*', '', line)
        if line and len(line) >= 10:
            props.append(line)
    # 2개 못 추출한 경우 대비
    if len(props) == 0:
        props = [content]
    elif len(props) == 1:
        props = [props[0], props[0]]
    else:
        props = props[:2]
    return props


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

    # 문단 5개 이상인 섹션만
    target_sections = [s for s in sections if len(s['paragraphs']) >= 5]
    print(f"문단 5개 이상 섹션: {len(target_sections)}개\n")

    # 2. 각 섹션에 대해 1개 vs 2개 Proposition 비교
    print("=" * 60)
    print("2단계: Proposition 1개 vs 2개 비교")
    print("=" * 60)

    results = []

    for i, sec in enumerate(target_sections):
        if i % 20 == 0:
            print(f"  진행: {i}/{len(target_sections)}...")

        try:
            full_text = '\n'.join(sec['paragraphs'])

            # Proposition 1개 추출
            prop_single = extract_single_proposition(full_text)

            # Proposition 2개 추출
            props_double = extract_two_propositions(full_text)

            # 임베딩: [prop_single, prop_double_1, prop_double_2, 문단1, 문단2, ...]
            texts_to_embed = [prop_single] + props_double + sec['paragraphs']
            embeddings = get_embeddings(texts_to_embed)

            emb_single = embeddings[0]
            emb_double_1 = embeddings[1]
            emb_double_2 = embeddings[2]
            para_embs = embeddings[3:]

            # 1개 Proposition: 각 문단과의 유사도
            sims_single = [cosine_sim(emb_single, p) for p in para_embs]

            # 2개 Proposition: 각 문단에 대해 더 유사한 쪽의 유사도 (max)
            sims_double_max = []
            for p in para_embs:
                s1 = cosine_sim(emb_double_1, p)
                s2 = cosine_sim(emb_double_2, p)
                sims_double_max.append(max(s1, s2))

            results.append({
                'title': sec['title'],
                'num_paragraphs': len(sec['paragraphs']),
                'prop_single': prop_single,
                'props_double': props_double,
                'single_mean': np.mean(sims_single),
                'single_min': np.min(sims_single),
                'double_mean': np.mean(sims_double_max),
                'double_min': np.min(sims_double_max),
                'improvement': np.mean(sims_double_max) - np.mean(sims_single),
                'sims_single': sims_single,
                'sims_double': sims_double_max,
            })
        except Exception as e:
            print(f"  섹션{i} 실패: {e}")
            continue

    print(f"\n처리 완료: {len(results)}개 섹션\n")

    if not results:
        print("결과 없음")
        return

    # 3. 전체 통계
    print("=" * 60)
    print("3단계: 결과")
    print("=" * 60)

    all_single = []
    all_double = []
    for r in results:
        all_single.extend(r['sims_single'])
        all_double.extend(r['sims_double'])

    single_means = [r['single_mean'] for r in results]
    double_means = [r['double_mean'] for r in results]
    improvements = [r['improvement'] for r in results]

    print(f"\n[전체 유사도 비교] (총 {len(all_single)}쌍)")
    print(f"{'':>25} | {'1개 Prop':>10} | {'2개 Prop':>10} | {'차이':>8}")
    print(f"  {'-' * 60}")
    print(f"  {'전체 평균':>23} | {np.mean(all_single):>10.4f} | {np.mean(all_double):>10.4f} | {np.mean(all_double)-np.mean(all_single):>+8.4f}")
    print(f"  {'전체 중앙값':>23} | {np.median(all_single):>10.4f} | {np.median(all_double):>10.4f} | {np.median(all_double)-np.median(all_single):>+8.4f}")
    print(f"  {'전체 최소':>23} | {np.min(all_single):>10.4f} | {np.min(all_double):>10.4f} |")

    print(f"\n[섹션별 평균 유사도 비교] ({len(results)}개 섹션)")
    print(f"  {'1개 Prop 평균':>23}: {np.mean(single_means):.4f}")
    print(f"  {'2개 Prop 평균':>23}: {np.mean(double_means):.4f}")
    print(f"  {'개선폭 평균':>23}: {np.mean(improvements):+.4f}")
    print(f"  {'개선폭 중앙값':>23}: {np.median(improvements):+.4f}")
    print(f"  {'개선된 섹션 비율':>23}: {sum(1 for x in improvements if x > 0)/len(improvements)*100:.1f}%")

    # t-test: 2개가 1개보다 유의미하게 높은지
    t_stat, p_value = stats.ttest_rel(double_means, single_means)
    print(f"\n[paired t-test] 2개 Prop > 1개 Prop ?")
    print(f"  t-statistic: {t_stat:.4f}")
    print(f"  p-value:     {p_value:.2e}")

    # 4. 문단 수별 개선폭
    print(f"\n{'=' * 60}")
    print("문단 수별 개선폭")
    print("=" * 60)

    groups = {}
    for r in results:
        n = r['num_paragraphs']
        if n <= 7:
            key = str(n)
        elif n <= 10:
            key = "8~10"
        elif n <= 20:
            key = "11~20"
        else:
            key = "21+"
        if key not in groups:
            groups[key] = {'single': [], 'double': [], 'imp': []}
        groups[key]['single'].append(r['single_mean'])
        groups[key]['double'].append(r['double_mean'])
        groups[key]['imp'].append(r['improvement'])

    print(f"\n  {'문단 수':>8} | {'섹션':>4} | {'1개 Prop':>8} | {'2개 Prop':>8} | {'개선폭':>8}")
    print(f"  {'-' * 55}")
    for key in sorted(groups.keys(), key=lambda x: int(x.split('~')[0].replace('+', ''))):
        g = groups[key]
        print(f"  {key:>8} | {len(g['imp']):>4} | {np.mean(g['single']):>8.4f} | {np.mean(g['double']):>8.4f} | {np.mean(g['imp']):>+8.4f}")

    # 5. 유사도 구간별 분포 비교
    print(f"\n{'=' * 60}")
    print("유사도 구간별 분포 비교")
    print("=" * 60)

    arr_s = np.array(all_single)
    arr_d = np.array(all_double)
    bins = [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]

    print(f"\n{'구간':>10} | {'1개 Prop':>8} | {'2개 Prop':>8}")
    print("-" * 40)
    for low, high in bins:
        s_pct = np.sum((arr_s >= low) & (arr_s < high)) / len(arr_s) * 100
        d_pct = np.sum((arr_d >= low) & (arr_d < high)) / len(arr_d) * 100
        print(f"  {low:.1f}~{high:.1f} | {s_pct:6.1f}% | {d_pct:6.1f}%")

    # 6. 샘플
    print(f"\n{'=' * 60}")
    print("샘플 (개선폭 큰/작은 섹션)")
    print("=" * 60)

    sorted_by_imp = sorted(results, key=lambda x: x['improvement'], reverse=True)

    print("\n[개선폭 큰 상위 5개]")
    for r in sorted_by_imp[:5]:
        print(f"  +{r['improvement']:.3f} | 1개={r['single_mean']:.3f} → 2개={r['double_mean']:.3f} | 문단수={r['num_paragraphs']} | [{r['title'][:30]}]")
        print(f"    1개: {r['prop_single'][:70]}")
        print(f"    2개: {r['props_double'][0][:70]}")
        print(f"         {r['props_double'][1][:70]}")

    print("\n[개선폭 작은(또는 하락) 하위 5개]")
    for r in sorted_by_imp[-5:]:
        print(f"  {r['improvement']:+.3f} | 1개={r['single_mean']:.3f} → 2개={r['double_mean']:.3f} | 문단수={r['num_paragraphs']} | [{r['title'][:30]}]")

    # 7. 시각화
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
        ax.hist(all_single, bins=30, alpha=0.6, label=f'1 Prop (mean={np.mean(all_single):.3f})', color='red')
        ax.hist(all_double, bins=30, alpha=0.6, label=f'2 Props (mean={np.mean(all_double):.3f})', color='blue')
        ax.set_xlabel('Cosine Similarity')
        ax.set_ylabel('Count')
        ax.set_title('1 Proposition vs 2 Propositions')
        ax.legend()

        # (2) 섹션별 1개 vs 2개 산점도
        ax = axes[0][1]
        ax.scatter(single_means, double_means, alpha=0.5, s=20, color='steelblue')
        lims = [min(min(single_means), min(double_means)), max(max(single_means), max(double_means))]
        ax.plot(lims, lims, 'r--', alpha=0.5, label='y=x (no improvement)')
        ax.set_xlabel('1 Proposition Mean Similarity')
        ax.set_ylabel('2 Propositions Mean Similarity')
        ax.set_title('Per-Section: 1 Prop vs 2 Props')
        ax.legend()

        # (3) 개선폭 히스토그램
        ax = axes[1][0]
        ax.hist(improvements, bins=30, color='green', alpha=0.8, edgecolor='white')
        ax.axvline(0, color='red', linestyle='--')
        ax.axvline(np.mean(improvements), color='blue', linestyle='--', label=f'Mean: {np.mean(improvements):+.3f}')
        ax.set_xlabel('Improvement (2 Props - 1 Prop)')
        ax.set_ylabel('Count')
        ax.set_title('Improvement Distribution')
        ax.legend()

        # (4) 문단 수 vs 개선폭
        ax = axes[1][1]
        n_paras = [r['num_paragraphs'] for r in results]
        ax.scatter(n_paras, improvements, alpha=0.5, s=20, color='steelblue')
        ax.axhline(0, color='red', linestyle='--', alpha=0.5)
        z = np.polyfit(n_paras, improvements, 1)
        p = np.poly1d(z)
        x_line = np.linspace(min(n_paras), max(n_paras), 100)
        ax.plot(x_line, p(x_line), 'r-', alpha=0.7, label=f'Trend (slope={z[0]:.4f})')
        ax.set_xlabel('Number of Paragraphs')
        ax.set_ylabel('Improvement')
        ax.set_title('Section Size vs Improvement')
        ax.legend()

        plt.suptitle('Multi-Proposition Test: 1 vs 2 Propositions (sections with 5+ paragraphs)',
                     fontsize=13, fontweight='bold', y=1.01)
        plt.tight_layout()

        output_path = str(Path(__file__).parent / "multi_proposition_test.png")
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n시각화 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n시각화 실패: {e}")

    # 8. 최종 판정
    print(f"\n{'=' * 60}")
    print("최종 판정")
    print("=" * 60)
    imp = np.mean(improvements)
    imp_pct = sum(1 for x in improvements if x > 0) / len(improvements) * 100
    print(f"\n  평균 개선폭: {imp:+.4f}")
    print(f"  개선된 섹션: {imp_pct:.1f}%")
    print(f"  p-value:    {p_value:.2e}")

    if imp > 0.03 and p_value < 0.05 and imp_pct > 60:
        print(f"\n  ✓ 2개 Proposition이 유의미하게 효과적")
        print(f"    → 문단 5개 이상 섹션에서 Proposition 2개 추출 전략 채택")
    elif imp > 0 and imp_pct > 50:
        print(f"\n  △ 약간의 개선 있으나 일관적이지 않음")
    else:
        print(f"\n  ✗ 2개 Proposition의 효과 미미")


if __name__ == "__main__":
    analyze()
