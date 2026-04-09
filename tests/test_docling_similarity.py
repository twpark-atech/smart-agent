"""
Docling 기반 문단 임베딩 유사도 테스트

Docling이 자동 인식한 구조(섹션/문단)를 기반으로:
1. 문단별 임베딩
2. 문단 내(같은 섹션) vs 문단 간(다른 섹션) 유사도 비교
3. 인접 문단 유사도 흐름 분석
"""

import numpy as np
from openai import OpenAI
from sklearn.metrics.pairwise import cosine_similarity
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
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


def analyze():
    # 1. Docling 파싱
    print(f"Docling 파싱 중: {PDF_PATH}")
    print("(시간이 걸릴 수 있습니다...)\n")

    converter = DocumentConverter()
    result = converter.convert(PDF_PATH)
    doc = result.document

    # 2. 문서 구조 추출
    items = []
    current_section = "서두"
    section_idx = 0

    for item, _level in doc.iterate_items():
        label = item.label

        # 섹션 제목 갱신
        if label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text:
                current_section = text
                section_idx += 1
            continue

        # 텍스트 문단 수집
        if label in [DocItemLabel.PARAGRAPH, DocItemLabel.TEXT, DocItemLabel.LIST_ITEM]:
            text = item.text.strip() if hasattr(item, 'text') else ""
            if text and len(text) >= 10:
                items.append({
                    'text': text,
                    'section': current_section,
                    'section_idx': section_idx,
                    'label': str(label),
                })

    print(f"추출된 문단 수: {len(items)}")
    print(f"섹션 수: {section_idx + 1}")

    # 섹션별 문단 수 통계
    section_counts = {}
    for it in items:
        sec = it['section']
        section_counts[sec] = section_counts.get(sec, 0) + 1

    print(f"\n섹션별 문단 수 (상위 15개):")
    for sec, cnt in sorted(section_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"  [{cnt:3d}개] {sec[:50]}")
    print()

    if len(items) < 10:
        print("문단이 너무 적어 분석 불가")
        return

    # 3. 문단별 임베딩
    texts = [it['text'] for it in items]
    print(f"임베딩 생성 중... ({len(texts)}문단)")
    embeddings = get_embeddings(texts)
    print(f"임베딩 완료: {embeddings.shape}\n")

    # 4. 유사도 계산
    n = len(items)
    section_ids = [it['section_idx'] for it in items]

    intra_sims = []  # 같은 섹션 내
    inter_sims = []  # 다른 섹션 간

    # 샘플링 (문단 수가 많으면)
    max_pairs = 500000
    if n * (n - 1) // 2 <= max_pairs:
        pairs_i, pairs_j = np.triu_indices(n, k=1)
    else:
        np.random.seed(42)
        pairs_i = np.random.randint(0, n, max_pairs * 2)
        pairs_j = np.random.randint(0, n, max_pairs * 2)
        mask = pairs_i < pairs_j
        pairs_i = pairs_i[mask][:max_pairs]
        pairs_j = pairs_j[mask][:max_pairs]

    # 벡터 내적으로 cosine similarity
    emb_i = embeddings[pairs_i]
    emb_j = embeddings[pairs_j]
    norms_i = np.linalg.norm(emb_i, axis=1)
    norms_j = np.linalg.norm(emb_j, axis=1)
    sims = np.sum(emb_i * emb_j, axis=1) / (norms_i * norms_j + 1e-10)

    for idx in range(len(pairs_i)):
        if section_ids[pairs_i[idx]] == section_ids[pairs_j[idx]]:
            intra_sims.append(sims[idx])
        else:
            inter_sims.append(sims[idx])

    # 5. 결과
    print("=" * 60)
    print("결과: 문단 단위 임베딩 유사도")
    print("=" * 60)

    if intra_sims:
        arr = np.array(intra_sims)
        print(f"\n[같은 섹션 내 문단 유사도]")
        print(f"  평균: {np.mean(arr):.4f}")
        print(f"  중앙: {np.median(arr):.4f}")
        print(f"  표본: {len(intra_sims):,}쌍")

    if inter_sims:
        arr = np.array(inter_sims)
        print(f"\n[다른 섹션 간 문단 유사도]")
        print(f"  평균: {np.mean(arr):.4f}")
        print(f"  중앙: {np.median(arr):.4f}")
        print(f"  표본: {len(inter_sims):,}쌍")

    if intra_sims and inter_sims:
        diff = np.mean(intra_sims) - np.mean(inter_sims)
        print(f"\n[차이] 섹션 내 - 섹션 간 = {diff:+.4f}")
        if diff > 0.05:
            print("  → 가설 지지: 같은 섹션 내 문단이 유의미하게 유사")
        elif diff > 0:
            print("  → 약한 지지")
        else:
            print("  → 가설 기각")

    # 6. 인접 문단 유사도 + 경계 분석
    print(f"\n{'=' * 60}")
    print("인접 문단 경계 분석")
    print("=" * 60)

    boundary_sims = []
    non_boundary_sims = []

    for i in range(n - 1):
        sim = np.dot(embeddings[i], embeddings[i+1]) / (
            np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i+1]) + 1e-10)
        if section_ids[i] != section_ids[i + 1]:
            boundary_sims.append(sim)
        else:
            non_boundary_sims.append(sim)

    if boundary_sims:
        print(f"\n  섹션 경계 인접 유사도: 평균 {np.mean(boundary_sims):.4f} ({len(boundary_sims)}건)")
    if non_boundary_sims:
        print(f"  비경계 인접 유사도:   평균 {np.mean(non_boundary_sims):.4f} ({len(non_boundary_sims)}건)")
    if boundary_sims and non_boundary_sims:
        d = np.mean(non_boundary_sims) - np.mean(boundary_sims)
        print(f"\n  비경계 - 경계 = {d:+.4f}")
        if d > 0.05:
            print("  → 섹션 경계에서 유사도 하락 뚜렷 (임베딩 기반 경계 탐지 유효)")

    # 7. 인접 문단 유사도 흐름 (앞 50개)
    print(f"\n[인접 문단 유사도 흐름 (앞 50개)]")
    print("-" * 70)
    for i in range(min(50, n - 1)):
        sim = np.dot(embeddings[i], embeddings[i+1]) / (
            np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i+1]) + 1e-10)
        boundary = " ◀ 섹션 경계" if section_ids[i] != section_ids[i+1] else ""
        bar = "█" * int(sim * 30)
        sec = items[i]['section'][:20]
        print(f"  P{i+1:3d}↔P{i+2:3d} | {sim:.4f} {bar}{boundary}  [{sec}]")

    # 8. 히트맵
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

        limit = min(100, n)
        sim_matrix = cosine_similarity(embeddings[:limit])

        fig, ax = plt.subplots(figsize=(14, 12))
        im = ax.imshow(sim_matrix, cmap='RdYlGn', vmin=0, vmax=1)

        # 섹션 경계선
        for idx in range(1, limit):
            if section_ids[idx] != section_ids[idx - 1]:
                ax.axhline(y=idx - 0.5, color='blue', linewidth=2)
                ax.axvline(x=idx - 0.5, color='blue', linewidth=2)

        plt.colorbar(im, ax=ax, label='Cosine Similarity')
        ax.set_title('Paragraph Embedding Similarity (Docling parsed)\nblue = section boundary')
        plt.tight_layout()

        output_path = "/home/atech/Projects/smart-agent/similarity_heatmap_docling.png"
        plt.savefig(output_path, dpi=150)
        print(f"\n히트맵 저장: {output_path}")
        plt.close()
    except Exception as e:
        print(f"\n히트맵 생성 실패: {e}")


if __name__ == "__main__":
    analyze()
