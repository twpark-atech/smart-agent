"""Format Converter - 포맷 감지, PDF 변환, MinIO 적재, 문서 파싱"""
import tempfile
import shutil
from pathlib import Path

from .detector import detect
from .converter import convert_to_pdf
from .uploader import upload
import document_parser


def run(file_path: str | Path) -> dict:
    """포맷 확인 → (필요 시) PDF 변환 → MinIO 적재 → 문서 파싱.

    처리 흐름:
        1. 포맷 감지 및 지원 여부 확인
        2. .hwpx, .docx → PDF 변환 (나머지는 원본 유지)
        3. 변환본(또는 원본) MinIO 적재
        4. document_parser로 텍스트/이미지/표 블록 추출

    Args:
        file_path: 처리할 파일 경로

    Returns:
        {
            "original_path": str,       # 입력 파일 경로
            "extension": str,           # 원본 확장자
            "converted": bool,          # PDF 변환 여부
            "minio": dict,              # MinIO 업로드 결과
            "parsed": dict,             # ParsedDocument.to_dict() 결과
        }

    Raises:
        ValueError: 지원하지 않는 포맷
        FileNotFoundError: 파일 없음
        RuntimeError: 변환 실패
    """
    source = Path(file_path)
    info = detect(source)

    if not info["supported"]:
        raise ValueError(
            f"지원하지 않는 포맷입니다: {info['extension']}\n"
            f"지원 포맷: .pdf .hwpx .docx .pptx .md .csv .xlsx .jpg .jpeg .png ..."
        )

    converted = False
    upload_path = source
    tmp_dir = None

    try:
        if info["needs_conversion"]:
            tmp_dir = tempfile.mkdtemp(prefix="parser_fc_")
            upload_path = convert_to_pdf(source, output_dir=tmp_dir)
            converted = True

        minio_result = upload(upload_path)
        parsed = document_parser.run(upload_path)

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "original_path": str(source),
        "extension": info["extension"],
        "converted": converted,
        "minio": minio_result,
        "parsed": parsed.to_dict(),
    }
