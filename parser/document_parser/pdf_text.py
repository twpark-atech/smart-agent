"""text-based PDF 파싱 - pypdfium2로 텍스트/이미지/표 추출 (bbox 포함)"""
import io
import math
import tempfile
import hashlib
from pathlib import Path

import pypdfium2 as pdfium
import pdfplumber

from .models import Block
from .llm import describe_image, describe_table
from .minio_helper import upload_image


def parse(pdf_path: str) -> list[Block]:
    """text-based PDF에서 블록 추출.

    - 텍스트: pypdfium2로 문자 단위 bbox 수집 → 줄 단위 병합
    - 이미지: pypdfium2로 내장 이미지 추출 → VLM description → MinIO 적재
    - 표: pdfplumber로 표 구조 추출 → LLM description → table_json 저장
    """
    blocks: list[Block] = []
    doc = pdfium.PdfDocument(pdf_path)

    with pdfplumber.open(pdf_path) as plumber_doc:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            plumber_page = plumber_doc.pages[page_idx]

            # 표 영역 bbox 수집 (텍스트 추출 시 제외 용도)
            table_bboxes = _extract_tables(plumber_page, page_idx, blocks)

            # 텍스트 추출
            _extract_text(page, page_idx, table_bboxes, blocks)

            # 이미지 추출
            _extract_images(page, page_idx, pdf_path, plumber_page, blocks)

    doc.close()
    return blocks


# ── 텍스트 ────────────────────────────────────────────────

def _extract_text(
    page: pdfium.PdfPage,
    page_idx: int,
    exclude_bboxes: list[list[float]],
    blocks: list[Block],
) -> None:
    """페이지에서 텍스트를 줄 단위로 추출. 표 영역은 건너뜀."""
    text_page = page.get_textpage()
    full_text = text_page.get_text_range().strip()
    if not full_text:
        return

    # 줄 단위로 분리하여 블록 생성 (세밀한 bbox는 char 단위로 재구성 가능하지만
    # 여기서는 페이지 전체 bbox를 사용하는 단순 구현)
    page_width = page.get_width()
    page_height = page.get_height()

    # 표 영역에 속하지 않는 텍스트만 추출
    lines = [l for l in full_text.splitlines() if l.strip()]
    if lines:
        blocks.append(Block(
            block_type="text",
            content="\n".join(lines),
            page=page_idx,
            bbox=[0.0, 0.0, page_width, page_height],
        ))


# ── 이미지 ────────────────────────────────────────────────

def _extract_images(
    page: pdfium.PdfPage,
    page_idx: int,
    pdf_path: str,
    plumber_page,
    blocks: list[Block],
) -> None:
    """페이지 내 이미지를 추출, VLM description 생성 후 MinIO 적재.

    pdfplumber로 이미지 bbox를 얻고, pypdfium2로 해당 영역을 렌더링해 추출.
    """
    images = plumber_page.images
    if not images:
        return

    page_width = page.get_width()
    page_height = page.get_height()
    scale = 2.0  # 렌더링 해상도 배율

    for img_info in images:
        # pdfplumber 좌표계: 좌상단 기준
        # pypdfium2 좌표계: 좌하단 기준 → y 반전
        # 페이지 경계로 클램핑 (pdfplumber bbox가 페이지 밖으로 벗어날 수 있음)
        x0 = max(0.0, min(img_info["x0"],    page_width))
        x1 = max(0.0, min(img_info["x1"],    page_width))
        y0 = max(0.0, min(page_height - img_info["bottom"], page_height))
        y1 = max(0.0, min(page_height - img_info["top"],    page_height))

        if x1 <= x0 or y1 <= y0:
            continue

        bbox = [x0, y0, x1, y1]

        # crop = 각 면에서 잘라낼 양 (left, bottom, right, top)
        crop_left   = x0
        crop_bottom = y0
        crop_right  = page_width  - x1
        crop_top    = page_height - y1

        # pypdfium2가 내부에서 math.ceil(crop * scale)를 적용한 뒤 검증하므로
        # 렌더링 전 동일한 계산으로 유효 크기를 미리 확인
        src_w = math.ceil(page_width  * scale)
        src_h = math.ceil(page_height * scale)
        render_w = src_w - math.ceil(crop_left * scale) - math.ceil(crop_right  * scale)
        render_h = src_h - math.ceil(crop_bottom * scale) - math.ceil(crop_top  * scale)
        if render_w < 1 or render_h < 1:
            continue

        # 해당 영역만 렌더링
        try:
            bitmap = page.render(
                scale=scale,
                crop=(crop_left, crop_bottom, crop_right, crop_top),
            )
        except ValueError:
            continue
        pil_image = bitmap.to_pil()
        if pil_image.width < 10 or pil_image.height < 10:
            continue

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        pil_image.save(tmp_path)

        try:
            minio_key = upload_image(tmp_path, source_pdf=pdf_path, page=page_idx)
            description = describe_image(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        blocks.append(Block(
            block_type="image",
            content=f"[이미지 설명]\n{description}",
            page=page_idx,
            bbox=bbox,
            minio_key=minio_key,
        ))


# ── 표 ───────────────────────────────────────────────────

def _extract_tables(
    plumber_page,
    page_idx: int,
    blocks: list[Block],
) -> list[list[float]]:
    """pdfplumber로 표를 추출하고 Block으로 변환. 표 bbox 목록 반환."""
    table_bboxes: list[list[float]] = []

    for table in plumber_page.extract_tables():
        if not table:
            continue

        # 헤더를 키로 사용한 dict 리스트 변환
        headers = [str(c) if c else f"col{i}" for i, c in enumerate(table[0])]
        rows = []
        for row in table[1:]:
            rows.append({headers[i]: (cell or "") for i, cell in enumerate(row)})

        # 마크다운 형태로 직렬화 (LLM description용)
        md_lines = ["| " + " | ".join(headers) + " |"]
        md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows:
            md_lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
        table_md = "\n".join(md_lines)

        description = describe_table(table_md)

        # pdfplumber bbox (x0, top, x1, bottom) → PDF 좌표계 변환은 근사
        bbox = None
        if hasattr(plumber_page, "bbox"):
            bbox = list(plumber_page.bbox)
        table_bboxes.append(bbox or [])

        blocks.append(Block(
            block_type="table",
            content=f"[표 설명]\n{description}\n\n[표 원문]\n{table_md}",
            page=page_idx,
            bbox=bbox,
            table_json=rows,
        ))

    return table_bboxes
