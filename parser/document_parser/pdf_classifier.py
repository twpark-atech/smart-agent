"""PDF 분류 - text-based vs image-based 판별"""
import pypdfium2 as pdfium

# 페이지당 추출 문자 수가 이 임계값 미만이면 image-based로 판단
TEXT_RATIO_THRESHOLD = 20


def classify(pdf_path: str) -> str:
    """PDF가 텍스트 기반인지 이미지 기반인지 판별.

    Returns:
        "text"  : 텍스트 기반 PDF (pypdfium2로 직접 추출 가능)
        "image" : 이미지 기반 PDF (OCR/VLM 필요)
    """
    doc = pdfium.PdfDocument(pdf_path)
    total_chars = 0
    page_count = len(doc)

    sample_pages = min(page_count, 5)  # 최대 5페이지만 샘플링
    for i in range(sample_pages):
        page = doc[i]
        text_page = page.get_textpage()
        text = text_page.get_text_range()
        total_chars += len(text.strip())

    doc.close()

    avg_chars = total_chars / sample_pages if sample_pages > 0 else 0
    return "text" if avg_chars >= TEXT_RATIO_THRESHOLD else "image"
