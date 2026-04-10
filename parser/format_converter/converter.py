"""포맷 변환 모듈 - hwp/hwpx/docx → PDF (LibreOffice / hwp5html 활용)"""
import logging
import subprocess
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_libreoffice_bin() -> str:
    for name in ("libreoffice", "soffice"):
        path = shutil.which(name)
        if path:
            return path
    raise EnvironmentError(
        "LibreOffice가 설치되어 있지 않습니다. "
        "sudo apt-get install libreoffice 로 설치하세요."
    )


def _get_hwp5html_bin() -> str | None:
    return shutil.which("hwp5html")


def _libreoffice_convert(source: Path, out_dir: Path) -> Path | None:
    """LibreOffice로 source → PDF 변환. 성공 시 PDF 경로, 실패/미생성 시 None."""
    libreoffice = _get_libreoffice_bin()
    cmd = [
        libreoffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(out_dir),
        str(source),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.warning(
            "[Converter] LibreOffice 변환 실패 (rc=%d): %s",
            result.returncode, result.stderr.strip(),
        )
        return None

    converted = out_dir / (source.stem + ".pdf")
    if converted.exists():
        return converted

    pdfs = list(out_dir.glob("*.pdf"))
    if pdfs:
        return pdfs[0]

    logger.warning("[Converter] LibreOffice 실행 성공이나 PDF 미생성: %s", result.stderr.strip())
    return None


def _hwp_to_pdf(source: Path, out_dir: Path) -> Path:
    """구형 .hwp → hwp5html → HTML → LibreOffice → PDF 변환."""
    hwp5html = _get_hwp5html_bin()
    if not hwp5html:
        raise EnvironmentError(
            "hwp5html이 설치되어 있지 않습니다. "
            "pip install pyhwp 로 설치하세요."
        )

    html_path = out_dir / (source.stem + ".html")

    # 1단계: hwp → HTML
    r1 = subprocess.run(
        [hwp5html, "--html", "--output", str(html_path), str(source)],
        capture_output=True, text=True, timeout=60,
    )
    if r1.returncode != 0 or not html_path.exists():
        raise RuntimeError(
            f"hwp5html 변환 실패 (rc={r1.returncode})\n"
            f"stderr: {r1.stderr.strip()}"
        )

    # 2단계: HTML → PDF (LibreOffice)
    pdf = _libreoffice_convert(html_path, out_dir)
    if pdf is None:
        raise RuntimeError(
            f"HTML→PDF 변환 실패 (LibreOffice가 HTML을 처리하지 못했습니다): {html_path}"
        )
    return pdf


def convert_to_pdf(file_path: str | Path, output_dir: str | Path | None = None) -> Path:
    """hwp/hwpx/docx 파일을 PDF로 변환.

    변환 경로:
        .hwp  → hwp5html → HTML → LibreOffice → PDF
        .hwpx / .docx → LibreOffice → PDF

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
    if ext not in (".hwp", ".hwpx", ".docx"):
        raise ValueError(f"변환 지원 포맷이 아닙니다: {ext} (지원: .hwp, .hwpx, .docx)")

    if output_dir is None:
        out_dir = Path(tempfile.mkdtemp(prefix="parser_convert_"))
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    if ext == ".hwp":
        return _hwp_to_pdf(source, out_dir)

    # .hwpx / .docx: LibreOffice 직접 변환
    pdf = _libreoffice_convert(source, out_dir)
    if pdf is None:
        raise RuntimeError(
            f"변환된 PDF를 찾을 수 없습니다: {out_dir}\n"
            f"LibreOffice가 {ext} 포맷을 지원하지 않을 수 있습니다."
        )
    return pdf
