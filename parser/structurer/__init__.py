"""Structurer - TOC 기반 섹션 분리 + 도메인 분류 + PostgreSQL 적재"""
from __future__ import annotations
import re
from pathlib import Path

from .models import Section, StructuredDocument
from .llm import classify_domain


def run(job_id: str, format_result: dict, index_result: dict) -> StructuredDocument:
    """섹션 분리 → 도메인 분류 → PostgreSQL 적재.

    섹션 분리 우선순위:
      1. 원본이 .docx → python-docx 단락 단위 Heading 감지 (가장 정확)
      2. 원본이 .pptx → 슬라이드 단위 섹션
      3. 그 외 → TOC 타이틀과 블록 텍스트 매칭 (근사)

    Returns:
        StructuredDocument (섹션/블록 포함)
    """
    import db

    db.init_schema()

    doc_type: str = index_result.get("doc_type", "기타")
    toc: list[dict] = index_result.get("toc", [])
    original_path: str = format_result.get("original_path", "")
    ext: str = format_result.get("extension", "").lower()
    blocks: list[dict] = format_result.get("parsed", {}).get("blocks", [])
    minio: dict = format_result.get("minio", {})

    # 섹션 분리
    if ext == ".docx" and original_path:
        sections = _split_docx(original_path, toc, blocks)
    elif ext == ".pptx" and original_path:
        sections = _split_pptx(blocks)
    else:
        sections = _split_by_toc(toc, blocks)

    # 도메인 분류 (문서 전체 기준)
    preview = "\n\n".join(
        b["content"] for b in blocks[:20] if b.get("block_type") == "text"
    )
    domain_category = classify_domain(preview)

    for section in sections:
        section.domain_category = domain_category

    # PostgreSQL 적재
    db.upsert_document(
        document_id=job_id,
        source_path=original_path,
        original_ext=ext,
        doc_type=doc_type,
        domain_category=domain_category,
        minio_bucket=minio.get("bucket", ""),
        minio_key=minio.get("object_key", ""),
    )
    db.save_sections(job_id, [s.to_dict() for s in sections])

    return StructuredDocument(
        doc_type=doc_type,
        domain_category=domain_category,
        sections=sections,
    )


# ── docx 직접 분리 ────────────────────────────────────────

def _heading_level(style_name: str) -> int | None:
    m = re.match(r"[Hh]eading\s+(\d)", style_name or "")
    if m:
        return min(int(m.group(1)), 3)
    m2 = re.match(r"제목\s*(\d)", style_name or "")
    if m2:
        return min(int(m2.group(1)), 3)
    return None


def _split_docx(
    docx_path: str,
    toc: list[dict],
    pdf_blocks: list[dict],
) -> list[Section]:
    """python-docx로 단락+테이블을 문서 순서대로 읽어 Heading 기준으로 섹션 분리.

    body 요소를 순서대로 순회하여 <w:p>(단락)와 <w:tbl>(테이블)을 모두 처리.
    이미지 블록은 PDF 추출 결과에서 가져와 page 비율로 섹션에 배치.
    """
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from index_parser.heading_extractor import _is_toc_heading

    flat_toc = _flatten_toc(toc)
    toc_path_map = {_normalize(t["title"]): t["section_path"] for t in flat_toc}

    doc = Document(docx_path)
    sections: list[Section] = []
    current: Section | None = None

    def _tbl_to_block(tbl: Table) -> dict:
        """Table 객체 → table 블록 (raw JSON + 마크다운)."""
        rows = tbl.rows
        if not rows:
            return {}
        headers = [cell.text.strip() or f"col{i}" for i, cell in enumerate(rows[0].cells)]
        rows_data = []
        for row in rows[1:]:
            rows_data.append({headers[i]: cell.text.strip() for i, cell in enumerate(row.cells)})

        md = ["| " + " | ".join(headers) + " |",
              "| " + " | ".join(["---"] * len(headers)) + " |"]
        for row in rows_data:
            md.append("| " + " | ".join(row.get(h, "") for h in headers) + " |")

        return {
            "block_type": "table",
            "content": "\n".join(md),
            "page": None, "bbox": None,
            "minio_key": None, "table_json": rows_data,
        }

    # body 요소를 단락/테이블 순서대로 순회 (python-docx 고수준 객체 사용)
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            style_name = para.style.name if para.style else ""
            level = _heading_level(style_name)
            text = para.text.strip()

            if level is not None:
                if not text or _is_toc_heading(text):
                    continue
                if current is not None:
                    sections.append(current)
                norm = _normalize(text)
                section_path = toc_path_map.get(norm, text)
                current = Section(
                    title=text, level=level,
                    section_path=section_path, domain_category="", blocks=[],
                )
            elif text and current is not None:
                current.blocks.append({
                    "block_type": "text", "content": text,
                    "page": None, "bbox": None,
                    "minio_key": None, "table_json": None,
                })

        elif isinstance(child, CT_Tbl) and current is not None:
            tbl = Table(child, doc)
            block = _tbl_to_block(tbl)
            if block:
                current.blocks.append(block)

    if current is not None:
        sections.append(current)

    # PDF 이미지 블록을 page 비율로 섹션에 배치
    _attach_non_text_blocks(sections, pdf_blocks, types=("image",))

    return sections if sections else [Section(
        title="전체", level=1, section_path="전체", domain_category="", blocks=[]
    )]


# ── PPTX 분리 ─────────────────────────────────────────────

def _split_pptx(blocks: list[dict]) -> list[Section]:
    """슬라이드(page) 단위로 섹션 구성."""
    page_map: dict[int, list[dict]] = {}
    for block in blocks:
        page = block.get("page", 0)
        page_map.setdefault(page, []).append(block)

    sections = []
    for page, page_blocks in sorted(page_map.items()):
        title = next(
            (b["content"].split("\n")[0][:60] for b in page_blocks if b.get("block_type") == "text"),
            f"슬라이드 {page + 1}",
        )
        sections.append(Section(
            title=title, level=1,
            section_path=title, domain_category="",
            blocks=page_blocks,
        ))
    return sections


# ── TOC 텍스트 매칭 분리 (폴백) ───────────────────────────

def _split_by_toc(toc: list[dict], blocks: list[dict]) -> list[Section]:
    """TOC 타이틀과 블록 첫 줄 매칭으로 섹션 분리."""
    if not toc:
        return [Section(title="전체", level=1, section_path="전체",
                        domain_category="", blocks=blocks)]

    flat_toc = _flatten_toc(toc)
    toc_map = {_normalize(t["title"]): t for t in flat_toc}

    sections: list[Section] = []
    current: Section | None = None
    unmatched: list[dict] = []

    for block in blocks:
        matched = None
        if block.get("block_type") == "text":
            first_line = _normalize(block["content"].split("\n")[0][:100])
            matched = toc_map.get(first_line)

        if matched:
            if current is not None:
                sections.append(current)
            current = Section(
                title=matched["title"], level=matched["level"],
                section_path=matched["section_path"], domain_category="",
                blocks=[],
            )
        else:
            (unmatched if current is None else current.blocks).append(block)

    if current is not None:
        sections.append(current)

    if unmatched:
        sections.insert(0, Section(
            title="문서 헤더", level=0, section_path="문서 헤더",
            domain_category="", blocks=unmatched,
        ))
    return sections


# ── 비텍스트 블록 배치 ────────────────────────────────────

def _attach_non_text_blocks(
    sections: list[Section],
    blocks: list[dict],
    types: tuple[str, ...] = ("image", "table"),
) -> None:
    """지정 타입 블록을 page 비율 기준으로 섹션에 배치."""
    non_text = [b for b in blocks if b.get("block_type") in types]
    if not non_text or not sections:
        return

    total_pages = max((b.get("page") or 0 for b in blocks), default=0) + 1
    sec_count = len(sections)

    for block in non_text:
        page = block.get("page") or 0
        idx = min(int(page / total_pages * sec_count), sec_count - 1)
        sections[idx].blocks.append(block)


# ── 유틸 ─────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[\s\W]", "", text).lower()


def _flatten_toc(nodes: list[dict], parent_path: str = "") -> list[dict]:
    flat = []
    for node in nodes:
        title = node.get("title", "")
        level = node.get("level", 1)
        path = f"{parent_path} > {title}" if parent_path else title
        flat.append({"title": title, "level": level, "section_path": path})
        flat.extend(_flatten_toc(node.get("children", []), path))
    return flat
