"""포맷 변환 모듈 - hwpx/docx → PDF (LibreOffice 활용)"""
import subprocess
import shutil
import tempfile
from pathlib import Path


def _libreoffice_available() -> bool:
    return shutil.which("libreoffice") is not None or shutil.which("soffice") is not None


def _get_libreoffice_bin() -> str:
    for name in ("libreoffice", "soffice"):
        path = shutil.which(name)
        if path:
            return path
    raise EnvironmentError(
        "LibreOffice가 설치되어 있지 않습니다. "
        "sudo apt-get install libreoffice 로 설치하세요."
    )


def convert_to_pdf(file_path: str | Path, output_dir: str | Path | None = None) -> Path:
    """hwpx 또는 docx 파일을 PDF로 변환.

    Args:
        file_path: 원본 파일 경로
        output_dir: 변환된 PDF 저장 디렉토리. None이면 임시 디렉토리 사용.

    Returns:
        변환된 PDF 파일 경로
    """
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {source}")

    ext = source.suffix.lower()
    if ext not in (".hwpx", ".docx"):
        raise ValueError(f"변환 지원 포맷이 아닙니다: {ext} (지원: .hwpx, .docx)")

    libreoffice = _get_libreoffice_bin()

    # output_dir이 없으면 임시 디렉토리 생성 (호출자가 정리 책임)
    if output_dir is None:
        out_dir = Path(tempfile.mkdtemp(prefix="parser_convert_"))
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        libreoffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(out_dir),
        str(source),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice 변환 실패 (returncode={result.returncode})\n"
            f"stderr: {result.stderr.strip()}"
        )

    converted = out_dir / (source.stem + ".pdf")
    if not converted.exists():
        # LibreOffice가 다른 이름으로 저장하는 경우 탐색
        pdfs = list(out_dir.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError(f"변환된 PDF를 찾을 수 없습니다: {out_dir}")
        converted = pdfs[0]

    return converted
