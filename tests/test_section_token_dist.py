"""
Docling 파싱 결과의 섹션(소제목) 단위 토큰 수 분포 확인
"""

import re
import numpy as np
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DocItemLabel

PDF_PATH = "/mnt/d/스마트제조혁신기술개발/data/7.기능성 가공 기술 실무.pdf"


def estimate_tokens(text: str) -> int:
    korean = len(re.findall(r'[가-힣]', text))
    other = len(text) - korean
    return int(korean / 2 + other / 4)


converter = DocumentConverter()
result = converter.convert(PDF_PATH)
doc = result.document

# 섹션별로 문단 모아서 토큰 수 계산
sections = []
current_section = "서두"
current_texts = []

for item, _level in doc.iterate_items():
    label = item.label
    if label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]:
        text = item.text.strip() if hasattr(item, 'text') else ""
        if text:
            # 이전 섹션 저장
            if current_texts:
                merged = '\n'.join(current_texts)
                sections.append({
                    'title': current_section,
                    'text': merged,
                    'tokens': estimate_tokens(merged),
                    'para_count': len(current_texts),
                })
            current_section = text
            current_texts = []
        continue

    if label in [DocItemLabel.PARAGRAPH, DocItemLabel.TEXT, DocItemLabel.LIST_ITEM]:
        text = item.text.strip() if hasattr(item, 'text') else ""
        if text:
            current_texts.append(text)

# 마지막 섹션
if current_texts:
    merged = '\n'.join(current_texts)
    sections.append({
        'title': current_section,
        'text': merged,
        'tokens': estimate_tokens(merged),
        'para_count': len(current_texts),
    })

tokens = [s['tokens'] for s in sections]
arr = np.array(tokens)

print(f"전체 섹션 수: {len(sections)}")
print(f"\n토큰 수 통계:")
print(f"  평균:   {np.mean(arr):.0f}")
print(f"  중앙값: {np.median(arr):.0f}")
print(f"  최소:   {np.min(arr)}")
print(f"  최대:   {np.max(arr)}")

print(f"\n케이스별 분포:")
under = sum(1 for t in tokens if t < 512)
normal = sum(1 for t in tokens if 512 <= t <= 2048)
over = sum(1 for t in tokens if t > 2048)
print(f"  < 512 토큰 (짧음):     {under:4d}개 ({under/len(tokens)*100:.1f}%)")
print(f"  512~2048 토큰 (적정):  {normal:4d}개 ({normal/len(tokens)*100:.1f}%)")
print(f"  > 2048 토큰 (긴):     {over:4d}개 ({over/len(tokens)*100:.1f}%)")

print(f"\n세분화 분포:")
bins = [(0, 50), (50, 100), (100, 200), (200, 300), (300, 512),
        (512, 768), (768, 1024), (1024, 1536), (1536, 2048),
        (2048, 4096), (4096, 8192), (8192, 99999)]
labels = ["0~50", "50~100", "100~200", "200~300", "300~512",
          "512~768", "768~1024", "1024~1536", "1536~2048",
          "2048~4096", "4096~8192", "8192+"]

for (low, high), label in zip(bins, labels):
    cnt = sum(1 for t in tokens if low <= t < high)
    pct = cnt / len(tokens) * 100
    bar = "█" * int(pct)
    print(f"  {label:>10} | {cnt:4d}개 ({pct:5.1f}%) {bar}")

# 적정 범위 (512~2048) 샘플
print(f"\n적정 범위 섹션 샘플 (512~2048 토큰):")
normal_secs = [s for s in sections if 512 <= s['tokens'] <= 2048][:10]
for s in normal_secs:
    print(f"  [{s['tokens']:5d}tok, {s['para_count']:2d}문단] {s['title'][:50]}")

# 짧은 섹션 샘플
print(f"\n짧은 섹션 샘플 (< 100 토큰):")
short = [s for s in sections if s['tokens'] < 100][:10]
for s in short:
    print(f"  [{s['tokens']:3d}tok, {s['para_count']:2d}문단] {s['title'][:50]}")

# 긴 섹션 샘플
print(f"\n긴 섹션 샘플 (> 2048 토큰):")
long_secs = sorted([s for s in sections if s['tokens'] > 2048], key=lambda x: -x['tokens'])[:10]
for s in long_secs:
    print(f"  [{s['tokens']:5d}tok, {s['para_count']:2d}문단] {s['title'][:50]}")
