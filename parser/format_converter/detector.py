"""포맷 감지 모듈 - 확장자 및 magic bytes 기반"""
import mimetypes
from pathlib import Path

# 처리 가능 포맷
SUPPORTED_EXTENSIONS = {
    ".pdf", ".hwp", ".hwpx", ".docx", ".pptx",
    ".md", ".csv", ".xlsx",
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp",
}

# 변환이 필요한 포맷 (→ PDF)
CONVERT_TO_PDF = {".hwp", ".hwpx", ".docx"}

# 이미지 포맷
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def detect(file_path: str | Path) -> dict:
    """파일 포맷을 감지하고 처리 방향을 반환.

    Returns:
        {
            "extension": str,       # 소문자 확장자 (예: ".pdf")
            "mime_type": str,       # MIME 타입
            "supported": bool,      # 처리 가능 여부
            "needs_conversion": bool,  # PDF 변환 필요 여부
            "is_image": bool,       # 이미지 여부
        }
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    mime_type, _ = mimetypes.guess_type(str(path))

    return {
        "extension": ext,
        "mime_type": mime_type or "application/octet-stream",
        "supported": ext in SUPPORTED_EXTENSIONS,
        "needs_conversion": ext in CONVERT_TO_PDF,
        "is_image": ext in IMAGE_EXTENSIONS,
    }
