"""image-based PDF 파싱 - 페이지를 이미지로 렌더링 후 VLM/OCR 추출"""
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

from .models import Block
from .llm import extract_page_with_vlm
from .minio_helper import upload_image

# 렌더링 DPI (300dpi 수준)
RENDER_SCALE = 300 / 72


def parse(pdf_path: str) -> list[Block]:
    """image-based PDF 파싱.

    각 페이지를 PNG로 렌더링 → MinIO 적재 → VLM으로 전체 내용 추출.
    VLM 출력(마크다운)을 단일 text 블록으로 반환.
    """
    blocks: list[Block] = []
    doc = pdfium.PdfDocument(pdf_path)

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        bitmap = page.render(scale=RENDER_SCALE, rotation=0)
        pil_image = bitmap.to_pil()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        pil_image.save(tmp_path)

        try:
            minio_key = upload_image(tmp_path, source_pdf=pdf_path, page=page_idx)
            extracted = extract_page_with_vlm(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        page_width = page.get_width()
        page_height = page.get_height()

        blocks.append(Block(
            block_type="text",
            content=extracted,
            page=page_idx,
            bbox=[0.0, 0.0, page_width, page_height],
            minio_key=minio_key,  # 페이지 이미지 참조
        ))

    doc.close()
    return blocks
