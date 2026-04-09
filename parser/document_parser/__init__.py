"""Document Parser - 포맷별 텍스트/이미지/표 추출"""
from pathlib import Path

from .models import ParsedDocument, Block
from .pdf_classifier import classify as classify_pdf

# 지원 포맷
_PDF_EXTS = {".pdf"}
_PPTX_EXTS = {".pptx"}
_FLAT_EXTS = {".md", ".csv", ".xlsx"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

SUPPORTED_EXTENSIONS = _PDF_EXTS | _PPTX_EXTS | _FLAT_EXTS | _IMAGE_EXTS


def run(file_path: str | Path) -> ParsedDocument:
    """파일을 읽어 블록 리스트(텍스트/이미지/표)를 반환.

    처리 흐름:
        PDF  → text-based / image-based 분기
        PPTX → 슬라이드별 텍스트/이미지/표
        md/csv/xlsx → flat 파싱
        이미지 → VLM description + MinIO

    Returns:
        ParsedDocument (blocks 포함)

    Raises:
        ValueError: 지원하지 않는 포맷
        FileNotFoundError: 파일 없음
    """
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {source}")

    ext = source.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"지원하지 않는 포맷: {ext}\n"
            f"지원 포맷: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    doc = ParsedDocument(source_path=str(source), extension=ext)

    if ext in _PDF_EXTS:
        pdf_type = classify_pdf(str(source))
        doc.pdf_type = pdf_type
        if pdf_type == "text":
            from .pdf_text import parse
        else:
            from .pdf_image import parse
        doc.blocks = parse(str(source))

    elif ext in _PPTX_EXTS:
        from .pptx_parser import parse
        doc.blocks = parse(str(source))

    elif ext in _FLAT_EXTS:
        from .flat_parser import parse
        doc.blocks = parse(str(source))

    elif ext in _IMAGE_EXTS:
        from .image_parser import parse
        doc.blocks = parse(str(source))

    return doc
