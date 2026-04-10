"""
테스트 8. 소분류 청킹 방식 비교: 직접 임베딩 vs Proposition 임베딩

방법 1 (직접 임베딩 기반 병합):
  문단 임베딩 → 인접 문단 유사도 → ≥0.5 병합 → 소분류 확정
  → 소분류별 Proposition 추출 & 임베딩

방법 2 (Proposition 기반 병합):
  문단별 Proposition 추출 → Proposition 임베딩 → 인접 유사도 → ≥0.5 병합 → 소분류 확정
  → 통합 Proposition 재추출 & 임베딩

평가:
  OpenSearch에 소분류 내 전체 문장 임베딩 + Proposition 임베딩 적재
  → Proposition ↔ 소분류 내 문장 유사도 평균 비교
"""

import re
import time
import json
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from opensearchpy import OpenSearch

# ── 설정 ──
_ROOT = Path(__file__).parent.parent
PARSED_MD_PATH = str(_ROOT / "tests" / "parsed_output.md")
OUTPUT_DIR = _ROOT / "tests" / "chunking_output"

EMBEDDING_URL = "http://112.163.62.170:8032/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
LLM_URL = "http://112.163.62.170:8012/v1"
LLM_API_KEY = "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9"
LLM_MODEL = "Qwen2.5-3B-Instruct"

OPENSEARCH_HOST = "localhost"
OPENSEARCH_PORT = 9200
SIMILARITY_THRESHOLD = 0.5
EMBEDDING_DIM = 1024

embed_client = OpenAI(base_url=EMBEDDING_URL, api_key="test")
llm_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)
os_client = OpenSearch(
    hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
    use_ssl=False,
)


# ═══════════════════════════════════════════════════════════════
# 유틸리티
# ═══════════════════════════════════════════════════════════════
def get_embeddings(texts: list[str], batch_size: int = 64) -> np.ndarray:
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = embed_client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_embeddings.extend([d.embedding for d in resp.data])
    return np.array(all_embeddings)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def extract_proposition(text: str) -> str:
    resp = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "당신은 문서 분석 전문가입니다. 주어진 텍스트의 핵심 내용을 하나의 명제(Proposition)로 요약하세요. 반드시 한국어 한 문장으로만 답하세요."},
            {"role": "user", "content": f"다음 내용의 핵심을 하나의 명제로 요약하세요:\n\n{text[:2000]}"},
        ],
        max_tokens=200,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def split_sentences(text: str) -> list[str]:
    sents = re.split(r'(?<=[.!?다함임음됨])\s+', text)
    return [s.strip() for s in sents if len(s.strip()) >= 10]


def estimate_tokens(text: str) -> int:
    korean = len(re.findall(r'[가-힣]', text))
    other = len(text) - korean
    return int(korean / 2 + other / 4)


# ═══════════════════════════════════════════════════════════════
# 1단계: parsed_output.md에서 중분류 > 문단 추출
# ═══════════════════════════════════════════════════════════════
def extract_sections_and_paragraphs(path: str) -> list[dict]:
    """## 헤딩(중분류) 기준으로 섹션을 나누고, 내부 문단을 추출"""
    text = Path(path).read_text(encoding="utf-8")
    lines = text.split('\n')

    sections = []
    current_title = ""
    current_paragraphs = []
    buf = []

    def flush_buf():
        nonlocal buf
        para = ' '.join(buf).strip()
        if para and len(para) >= 20:
            current_paragraphs.append(para)
        buf = []

    for line in lines:
        stripped = line.strip()

        if line.startswith('## '):
            flush_buf()
            if current_paragraphs:
                sections.append({
                    'title': current_title,
                    'paragraphs': current_paragraphs,
                })
            current_title = stripped[3:].strip()
            current_paragraphs = []
            continue

        # 스킵: 이미지 마커, 테이블, 빈 줄
        if not stripped or stripped == '<!-- image -->' or stripped.startswith('|'):
            flush_buf()
            continue

        # 목록 항목은 별도 문단으로
        if stripped.startswith('- '):
            flush_buf()
            if len(stripped) >= 20:
                current_paragraphs.append(stripped)
            continue

        buf.append(stripped)

    flush_buf()
    if current_paragraphs:
        sections.append({'title': current_title, 'paragraphs': current_paragraphs})

    # 문단 2개 이상인 섹션만
    return [s for s in sections if len(s['paragraphs']) >= 2]


# ═══════════════════════════════════════════════════════════════
# 2단계: 소분류 생성 — 방법 1 (직접 임베딩 기반 병합)
# ═══════════════════════════════════════════════════════════════
def method1_direct_merge(sections: list[dict]) -> list[dict]:
    """문단 임베딩 → 인접 유사도 ≥ 0.5 병합 → 소분류"""
    print("\n[방법 1] 직접 임베딩 기반 병합")
    print("-" * 50)

    all_subcategories = []
    total_time = 0

    for si, sec in enumerate(sections):
        if si % 30 == 0:
            print(f"  섹션 {si}/{len(sections)}...", flush=True)

        t0 = time.time()
        paragraphs = sec['paragraphs']

        # 문단 임베딩
        para_embs = get_embeddings(paragraphs)

        # 인접 문단 유사도 계산 → 병합
        groups = [[0]]  # 첫 문단부터 시작
        for i in range(1, len(paragraphs)):
            sim = cosine_sim(para_embs[i - 1], para_embs[i])
            if sim >= SIMILARITY_THRESHOLD:
                groups[-1].append(i)
            else:
                groups.append([i])

        # 소분류 생성
        for gi, group in enumerate(groups):
            merged_text = '\n'.join([paragraphs[idx] for idx in group])
            all_subcategories.append({
                'section_title': sec['title'],
                'section_idx': si,
                'subcat_idx': gi,
                'paragraph_indices': group,
                'text': merged_text,
                'num_paragraphs': len(group),
            })

        total_time += time.time() - t0

    print(f"  소분류 수: {len(all_subcategories)}")
    print(f"  임베딩 시간: {total_time:.1f}s")

    # Proposition 추출 (병렬)
    print(f"  Proposition 추출 중 (병렬)...", flush=True)
    t0 = time.time()

    def _extract(sc):
        sc['proposition'] = extract_proposition(sc['text'])
        return sc

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_extract, sc) for sc in all_subcategories]
        for i, f in enumerate(as_completed(futures)):
            if (i + 1) % 50 == 0:
                print(f"    Prop 추출: {i+1}/{len(all_subcategories)}", flush=True)

    prop_time = time.time() - t0
    print(f"  Proposition 추출 시간: {prop_time:.1f}s")

    # Proposition 임베딩
    t0 = time.time()
    props = [sc['proposition'] for sc in all_subcategories]
    prop_embs = get_embeddings(props)
    for i, sc in enumerate(all_subcategories):
        sc['proposition_embedding'] = prop_embs[i].tolist()
    prop_embed_time = time.time() - t0

    return all_subcategories, {
        'embed_time': total_time,
        'prop_extract_time': prop_time,
        'prop_embed_time': prop_embed_time,
        'total_time': total_time + prop_time + prop_embed_time,
    }


# ═══════════════════════════════════════════════════════════════
# 2단계: 소분류 생성 — 방법 2 (Proposition 기반 병합)
# ═══════════════════════════════════════════════════════════════
def method2_proposition_merge(sections: list[dict]) -> list[dict]:
    """문단 Proposition 추출 → Proposition 임베딩 → 인접 유사도 ≥ 0.5 병합"""
    print("\n[방법 2] Proposition 기반 병합")
    print("-" * 50)

    all_subcategories = []
    total_prop_time = 0
    total_embed_time = 0

    for si, sec in enumerate(sections):
        if si % 30 == 0:
            print(f"  섹션 {si}/{len(sections)}...", flush=True)

        paragraphs = sec['paragraphs']

        # 문단별 Proposition 추출 (병렬)
        t0 = time.time()
        para_props = [None] * len(paragraphs)

        def _extract_para(args):
            idx, text = args
            return idx, extract_proposition(text)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_extract_para, (i, p)) for i, p in enumerate(paragraphs)]
            for f in as_completed(futures):
                idx, prop = f.result()
                para_props[idx] = prop

        total_prop_time += time.time() - t0

        # Proposition 임베딩
        t0 = time.time()
        prop_embs = get_embeddings(para_props)
        total_embed_time += time.time() - t0

        # 인접 Proposition 유사도 → 병합
        groups = [[0]]
        for i in range(1, len(paragraphs)):
            sim = cosine_sim(prop_embs[i - 1], prop_embs[i])
            if sim >= SIMILARITY_THRESHOLD:
                groups[-1].append(i)
            else:
                groups.append([i])

        for gi, group in enumerate(groups):
            merged_text = '\n'.join([paragraphs[idx] for idx in group])
            all_subcategories.append({
                'section_title': sec['title'],
                'section_idx': si,
                'subcat_idx': gi,
                'paragraph_indices': group,
                'text': merged_text,
                'num_paragraphs': len(group),
            })

    print(f"  소분류 수: {len(all_subcategories)}")
    print(f"  문단별 Prop 추출 시간: {total_prop_time:.1f}s")
    print(f"  문단별 Prop 임베딩 시간: {total_embed_time:.1f}s")

    # 통합 Proposition 재추출 (병렬)
    print(f"  통합 Proposition 재추출 중 (병렬)...", flush=True)
    t0 = time.time()

    def _extract(sc):
        sc['proposition'] = extract_proposition(sc['text'])
        return sc

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_extract, sc) for sc in all_subcategories]
        for i, f in enumerate(as_completed(futures)):
            if (i + 1) % 50 == 0:
                print(f"    통합 Prop 추출: {i+1}/{len(all_subcategories)}", flush=True)

    merged_prop_time = time.time() - t0
    print(f"  통합 Prop 추출 시간: {merged_prop_time:.1f}s")

    # 통합 Proposition 임베딩
    t0 = time.time()
    props = [sc['proposition'] for sc in all_subcategories]
    prop_embs = get_embeddings(props)
    for i, sc in enumerate(all_subcategories):
        sc['proposition_embedding'] = prop_embs[i].tolist()
    merged_prop_embed_time = time.time() - t0

    return all_subcategories, {
        'para_prop_time': total_prop_time,
        'para_embed_time': total_embed_time,
        'merged_prop_time': merged_prop_time,
        'merged_prop_embed_time': merged_prop_embed_time,
        'total_time': total_prop_time + total_embed_time + merged_prop_time + merged_prop_embed_time,
    }


# ═══════════════════════════════════════════════════════════════
# 3단계: MD 파일 저장
# ═══════════════════════════════════════════════════════════════
def save_subcategories_md(subcats: list[dict], method_name: str):
    out_dir = OUTPUT_DIR / method_name
    out_dir.mkdir(parents=True, exist_ok=True)

    for sc in subcats:
        filename = f"sec{sc['section_idx']:03d}_sub{sc['subcat_idx']:03d}.md"
        content = f"# {sc['section_title']} > 소분류 {sc['subcat_idx'] + 1}\n\n"
        content += f"- 병합 문단 수: {sc['num_paragraphs']}\n"
        content += f"- Proposition: {sc['proposition']}\n\n"
        content += f"---\n\n{sc['text']}\n"
        (out_dir / filename).write_text(content, encoding="utf-8")

    print(f"  MD 저장: {out_dir} ({len(subcats)}개 파일)")


# ═══════════════════════════════════════════════════════════════
# 4단계: OpenSearch 인덱스 생성 & 적재
# ═══════════════════════════════════════════════════════════════
def create_index(index_name: str):
    if os_client.indices.exists(index=index_name):
        os_client.indices.delete(index=index_name)

    os_client.indices.create(
        index=index_name,
        body={
            "settings": {
                "index": {"knn": True},
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "mappings": {
                "properties": {
                    "method": {"type": "keyword"},
                    "section_title": {"type": "text"},
                    "section_idx": {"type": "integer"},
                    "subcat_idx": {"type": "integer"},
                    "doc_type": {"type": "keyword"},  # "sentence" or "proposition"
                    "text": {"type": "text"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": EMBEDDING_DIM,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "lucene",
                        },
                    },
                }
            },
        },
    )


def index_subcategories(index_name: str, subcats: list[dict], method_name: str):
    """소분류 내 문장 임베딩 + Proposition 임베딩을 OpenSearch에 적재"""
    print(f"\n  [{method_name}] OpenSearch 적재 중...", flush=True)
    t0 = time.time()
    doc_count = 0

    for si, sc in enumerate(subcats):
        if si % 50 == 0:
            print(f"    적재: {si}/{len(subcats)}...", flush=True)

        sentences = split_sentences(sc['text'])
        if not sentences:
            continue

        # 문장 임베딩
        sent_embs = get_embeddings(sentences)

        # 문장 적재
        for i, (sent, emb) in enumerate(zip(sentences, sent_embs)):
            os_client.index(
                index=index_name,
                body={
                    "method": method_name,
                    "section_title": sc['section_title'],
                    "section_idx": sc['section_idx'],
                    "subcat_idx": sc['subcat_idx'],
                    "doc_type": "sentence",
                    "text": sent,
                    "embedding": emb.tolist(),
                },
            )
            doc_count += 1

        # Proposition 적재
        os_client.index(
            index=index_name,
            body={
                "method": method_name,
                "section_title": sc['section_title'],
                "section_idx": sc['section_idx'],
                "subcat_idx": sc['subcat_idx'],
                "doc_type": "proposition",
                "text": sc['proposition'],
                "embedding": sc['proposition_embedding'],
            },
        )
        doc_count += 1

    os_client.indices.refresh(index=index_name)
    elapsed = time.time() - t0
    print(f"    적재 완료: {doc_count}개 문서, {elapsed:.1f}s")
    return doc_count


# ═══════════════════════════════════════════════════════════════
# 5단계: Proposition ↔ 소분류 내 문장 유사도 평가
# ═══════════════════════════════════════════════════════════════
def evaluate_representativeness(index_name: str, subcats: list[dict], method_name: str) -> dict:
    """각 소분류의 Proposition 임베딩과 소분류 내 문장 임베딩의 유사도 계산"""
    print(f"\n  [{method_name}] 대표성 평가 중...", flush=True)

    subcat_sims = []

    for si, sc in enumerate(subcats):
        if si % 50 == 0:
            print(f"    평가: {si}/{len(subcats)}...", flush=True)

        prop_emb = sc['proposition_embedding']

        # OpenSearch에서 해당 소분류의 문장들 검색
        query = {
            "size": 200,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"method": method_name}},
                        {"term": {"section_idx": sc['section_idx']}},
                        {"term": {"subcat_idx": sc['subcat_idx']}},
                        {"term": {"doc_type": "sentence"}},
                    ]
                }
            },
            "_source": ["embedding"],
        }

        resp = os_client.search(index=index_name, body=query)
        hits = resp['hits']['hits']

        if not hits:
            continue

        sent_embs = [np.array(h['_source']['embedding']) for h in hits]
        prop_emb_np = np.array(prop_emb)

        sims = [cosine_sim(prop_emb_np, se) for se in sent_embs]

        subcat_sims.append({
            'section_idx': sc['section_idx'],
            'subcat_idx': sc['subcat_idx'],
            'section_title': sc['section_title'],
            'num_sentences': len(sims),
            'num_paragraphs': sc['num_paragraphs'],
            'mean_sim': np.mean(sims),
            'std_sim': np.std(sims),
            'sims': sims,
        })

    all_sims = [s for r in subcat_sims for s in r['sims']]
    means = [r['mean_sim'] for r in subcat_sims]

    return {
        'method': method_name,
        'num_subcats': len(subcat_sims),
        'all_sims': all_sims,
        'means': means,
        'global_mean': np.mean(all_sims) if all_sims else 0,
        'global_median': np.median(all_sims) if all_sims else 0,
        'mean_of_means': np.mean(means) if means else 0,
        'std_of_means': np.std(means) if means else 0,
        'subcat_results': subcat_sims,
    }


# ═══════════════════════════════════════════════════════════════
# 6단계: 비교 출력
# ═══════════════════════════════════════════════════════════════
def print_comparison(eval1: dict, eval2: dict, cost1: dict, cost2: dict):
    print("\n" + "=" * 70)
    print("비교 결과")
    print("=" * 70)

    # 청킹 결과
    print(f"\n{'─' * 70}")
    print(f"  [청킹 결과]")
    print(f"{'─' * 70}")
    print(f"  {'지표':<25} | {'방법1 (직접)':>14} | {'방법2 (Prop)':>14}")
    print(f"  {'-' * 57}")
    print(f"  {'소분류 수':<23} | {eval1['num_subcats']:>14} | {eval2['num_subcats']:>14}")
    print(f"  {'문장-Prop 쌍 수':<22} | {len(eval1['all_sims']):>14} | {len(eval2['all_sims']):>14}")

    # 효율 (유사도)
    print(f"\n{'─' * 70}")
    print(f"  [효율] Proposition ↔ 소분류 내 문장 유사도")
    print(f"{'─' * 70}")
    print(f"  {'지표':<25} | {'방법1 (직접)':>14} | {'방법2 (Prop)':>14} | {'차이':>10}")
    print(f"  {'-' * 67}")

    metrics = [
        ('전체 유사도 평균', eval1['global_mean'], eval2['global_mean']),
        ('전체 유사도 중앙값', eval1['global_median'], eval2['global_median']),
        ('소분류별 평균의 평균', eval1['mean_of_means'], eval2['mean_of_means']),
        ('소분류별 평균의 표준편차', eval1['std_of_means'], eval2['std_of_means']),
    ]
    for name, v1, v2 in metrics:
        print(f"  {name:<22} | {v1:>14.4f} | {v2:>14.4f} | {v2-v1:>+10.4f}")

    # 비용 (시간)
    print(f"\n{'─' * 70}")
    print(f"  [비용] 처리 시간")
    print(f"{'─' * 70}")
    print(f"  {'단계':<30} | {'방법1':>12} | {'방법2':>12}")
    print(f"  {'-' * 58}")

    for key in ['total_time']:
        v1 = cost1.get(key, 0)
        v2 = cost2.get(key, 0)
        print(f"  {'총 처리 시간':<28} | {v1:>10.1f}s | {v2:>10.1f}s")

    if cost1.get('embed_time'):
        print(f"  {'  문단 임베딩':<28} | {cost1['embed_time']:>10.1f}s | {'N/A':>12}")
    if cost1.get('prop_extract_time'):
        print(f"  {'  Prop 추출 (소분류)':<26} | {cost1['prop_extract_time']:>10.1f}s | {'N/A':>12}")
    if cost2.get('para_prop_time'):
        print(f"  {'  Prop 추출 (문단별)':<26} | {'N/A':>12} | {cost2['para_prop_time']:>10.1f}s")
    if cost2.get('merged_prop_time'):
        print(f"  {'  Prop 재추출 (통합)':<26} | {'N/A':>12} | {cost2['merged_prop_time']:>10.1f}s")

    ratio = cost2['total_time'] / cost1['total_time'] if cost1['total_time'] > 0 else 0
    print(f"\n  비용 비율: 방법2 / 방법1 = {ratio:.1f}x")

    # 비용 대비 효율
    sim_diff = eval2['global_mean'] - eval1['global_mean']
    print(f"\n{'─' * 70}")
    print(f"  [비용 대비 효율]")
    print(f"{'─' * 70}")
    print(f"  유사도 차이: {sim_diff:+.4f}")
    print(f"  시간 비율:   {ratio:.1f}x")

    # 결론
    print(f"\n{'=' * 70}")
    print("결론")
    print("=" * 70)
    if sim_diff > 0.02:
        print(f"\n  → 방법 2 (Proposition 기반 병합) 우세")
        print(f"    유사도 +{sim_diff:.4f}, 비용 {ratio:.1f}x")
    elif sim_diff < -0.02:
        print(f"\n  → 방법 1 (직접 임베딩 병합) 우세")
        print(f"    유사도 {sim_diff:+.4f}, 비용 절감 {1/ratio:.1f}x")
    else:
        print(f"\n  → 유사도 차이 미미 ({sim_diff:+.4f})")
        if ratio > 2:
            print(f"    비용 {ratio:.1f}x 증가 대비 효과 부족 → 방법 1 권장")
        else:
            print(f"    비용 차이도 크지 않음 → 상황에 따라 선택")


# ═══════════════════════════════════════════════════════════════
# 시각화
# ═══════════════════════════════════════════════════════════════
def visualize(eval1: dict, eval2: dict, cost1: dict, cost2: dict):
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

        # (1) 유사도 분포
        ax = axes[0][0]
        ax.hist(eval1['all_sims'], bins=40, alpha=0.6, color='steelblue',
                label=f"방법1 (mean={eval1['global_mean']:.3f})", edgecolor='white')
        ax.hist(eval2['all_sims'], bins=40, alpha=0.6, color='coral',
                label=f"방법2 (mean={eval2['global_mean']:.3f})", edgecolor='white')
        ax.set_xlabel('Cosine Similarity')
        ax.set_ylabel('Count')
        ax.set_title('Proposition ↔ 문장 유사도 분포')
        ax.legend()

        # (2) 소분류별 평균 유사도 비교
        ax = axes[0][1]
        ax.hist(eval1['means'], bins=30, alpha=0.6, color='steelblue',
                label=f"방법1 (mean={eval1['mean_of_means']:.3f})", edgecolor='white')
        ax.hist(eval2['means'], bins=30, alpha=0.6, color='coral',
                label=f"방법2 (mean={eval2['mean_of_means']:.3f})", edgecolor='white')
        ax.set_xlabel('소분류별 평균 유사도')
        ax.set_ylabel('Count')
        ax.set_title('소분류별 Proposition 대표성')
        ax.legend()

        # (3) 비용-효율 바 차트
        ax = axes[1][0]
        categories = ['유사도 평균', '처리시간\n(정규화)']
        v1 = [eval1['global_mean'], 1.0]
        v2 = [eval2['global_mean'], cost2['total_time'] / cost1['total_time']]
        x = np.arange(len(categories))
        w = 0.3
        ax.bar(x - w/2, v1, w, label='방법1 (직접)', color='steelblue', alpha=0.8)
        ax.bar(x + w/2, v2, w, label='방법2 (Prop)', color='coral', alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(categories)
        ax.set_title('비용 vs 효율')
        ax.legend()

        # (4) 병합 문단 수 별 유사도
        ax = axes[1][1]
        for eval_data, color, label in [(eval1, 'steelblue', '방법1'), (eval2, 'coral', '방법2')]:
            group_data = {}
            for r in eval_data['subcat_results']:
                n = r['num_paragraphs']
                key = str(n) if n <= 5 else '6+'
                group_data.setdefault(key, []).append(r['mean_sim'])
            keys = sorted(group_data.keys(), key=lambda x: int(x.replace('+', '')))
            vals = [np.mean(group_data[k]) for k in keys]
            ax.plot(keys, vals, 'o-', color=color, label=label, alpha=0.8)
        ax.set_xlabel('병합 문단 수')
        ax.set_ylabel('평균 유사도')
        ax.set_title('병합 크기별 Proposition 대표성')
        ax.legend()

        plt.suptitle('소분류 청킹 비교: 직접 임베딩 vs Proposition 기반',
                     fontsize=13, fontweight='bold', y=1.01)
        plt.tight_layout()

        output_path = str(Path(__file__).parent / "chunking_comparison_test.png")
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"\n시각화 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n시각화 실패: {e}")


# ═══════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 70)
    print("테스트 8. 소분류 청킹 비교: 직접 임베딩 vs Proposition 기반 병합")
    print("=" * 70)

    INDEX_NAME = "chunking-comparison"

    # 1. 섹션/문단 추출
    print("\n[1단계] 중분류 > 문단 추출")
    sections = extract_sections_and_paragraphs(PARSED_MD_PATH)
    total_paras = sum(len(s['paragraphs']) for s in sections)
    print(f"  중분류 수: {len(sections)}")
    print(f"  총 문단 수: {total_paras}")

    # 2. 방법 1 실행
    subcats1, cost1 = method1_direct_merge(sections)

    # 3. 방법 2 실행
    subcats2, cost2 = method2_proposition_merge(sections)

    # 4. MD 저장
    print("\n[4단계] MD 파일 저장")
    save_subcategories_md(subcats1, "method1_direct")
    save_subcategories_md(subcats2, "method2_proposition")

    # 5. OpenSearch 적재
    print("\n[5단계] OpenSearch 적재")
    create_index(INDEX_NAME)
    index_subcategories(INDEX_NAME, subcats1, "method1")
    index_subcategories(INDEX_NAME, subcats2, "method2")

    # 6. 평가
    print("\n[6단계] 대표성 평가")
    eval1 = evaluate_representativeness(INDEX_NAME, subcats1, "method1")
    eval2 = evaluate_representativeness(INDEX_NAME, subcats2, "method2")

    # 7. 비교 출력
    print_comparison(eval1, eval2, cost1, cost2)

    # 8. 시각화
    visualize(eval1, eval2, cost1, cost2)


if __name__ == "__main__":
    main()
