"""
Docling 파싱 결과의 문단별 토큰 수 분포 확인
"""

import re
import numpy as np
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DocItemLabel

PDF_PATH = "/mnt/d/스마트제조혁신기술개발/data/7.기능성 가공 기술 실무.pdf"

# 간이 토큰 추정 (한국어: ~2자/토큰, 영어: ~4자/토큰)
def estimate_tokens(text: str) -> int:
    korean = len(re.findall(r'[가-힣]', text))
    other = len(text) - korean
    return int(korean / 2 + other / 4)


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
        if text and len(text) >= 10:
            tokens = estimate_tokens(text)
            paragraphs.append({
                'text': text,
                'section': current_section,
                'tokens': tokens,
                'chars': len(text),
            })

tokens = [p['tokens'] for p in paragraphs]
arr = np.array(tokens)

print(f"전체 문단 수: {len(paragraphs)}")
print(f"\n토큰 수 통계:")
print(f"  평균:   {np.mean(arr):.0f}")
print(f"  중앙값: {np.median(arr):.0f}")
print(f"  최소:   {np.min(arr)}")
print(f"  최대:   {np.max(arr)}")

print(f"\n케이스별 분포:")
under = sum(1 for t in tokens if t < 512)
normal = sum(1 for t in tokens if 512 <= t <= 2048)
over = sum(1 for t in tokens if t > 2048)

print(f"  < 512 토큰 (짧음):      {under:4d}개 ({under/len(tokens)*100:.1f}%)")
print(f"  512~2048 토큰 (적정):   {normal:4d}개 ({normal/len(tokens)*100:.1f}%)")
print(f"  > 2048 토큰 (긴):      {over:4d}개 ({over/len(tokens)*100:.1f}%)")

print(f"\n세분화 분포:")
bins = [(0, 50), (50, 100), (100, 200), (200, 300), (300, 512),
        (512, 768), (768, 1024), (1024, 1536), (1536, 2048),
        (2048, 4096), (4096, 99999)]
labels = ["0~50", "50~100", "100~200", "200~300", "300~512",
          "512~768", "768~1024", "1024~1536", "1536~2048",
          "2048~4096", "4096+"]

for (low, high), label in zip(bins, labels):
    cnt = sum(1 for t in tokens if low <= t < high)
    pct = cnt / len(tokens) * 100
    bar = "█" * int(pct)
    print(f"  {label:>10} | {cnt:4d}개 ({pct:5.1f}%) {bar}")

# 짧은 문단 샘플
print(f"\n짧은 문단 샘플 (< 100 토큰):")
short = [p for p in paragraphs if p['tokens'] < 100][:10]
for p in short:
    print(f"  [{p['tokens']:3d}tok] [{p['section'][:25]}] {p['text'][:60]}")

# 긴 문단 샘플
print(f"\n긴 문단 샘플 (> 2048 토큰):")
long_paras = sorted([p for p in paragraphs if p['tokens'] > 2048], key=lambda x: -x['tokens'])[:10]
for p in long_paras:
    print(f"  [{p['tokens']:5d}tok] [{p['section'][:25]}] {p['text'][:60]}")
