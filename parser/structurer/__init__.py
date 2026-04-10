"""Structurer - TOC 기반 섹션 분리 + 도메인 분류 + PostgreSQL 적재"""
from __future__ import annotations
import re
from pathlib import Path

# 논문·보고서 등에서 자주 나타나는 섹션 헤딩 패턴
_HEADING_RE = re.compile(
    r"^("
    r"\d+\.(?:\d+\.)*\s+\S"                                         # 1. xxx, 1.1. xxx
    r"|\d+\)\s+\S"                                                    # 1) xxx
    r"|[IVXLC]+\.\s+\S"                                              # I. Introduction
    r"|제\s*\d+\s*[장절항편]"                                          # 제1장, 제2절
    r"|Chapter\s+\d"                                                  # Chapter 1
    r"|(?:Abstract|초록|요약|서론|결론|서문|부록|Appendix)$"
    r"|(?:Acknowledgments?|감사의\s*글?)$"
    r"|(?:References?|참고\s*문헌|Bibliography)$"
    r"|(?:Introduction|Conclusions?|Methods?|Results?|Discussion|Background)$"
    r"|(?:Related\s+Work|Experiments?|Evaluation|Future\s+Work)$"
    r")",
    re.IGNORECASE,
)

# References/Bibliography 섹션 헤딩 감지 (컨텍스트 추적용)
# search() 사용: "References", "5. References", "References 목록" 등 모든 형태 감지
_BIBLIO_SECTION_RE = re.compile(
    r"(?:References?|참고\s*문헌|Bibliography)", re.IGNORECASE
)

# 참고문헌 목록 개별 항목 패턴
# - 1. xxx, 2405.21060. xxx (arXiv ID 포함: \d+\.(\d+\.)*\s+)
# - [1] xxx
_BIBLIO_ITEM_RE = re.compile(
    r"^\d+\.(?:\d+\.)*\s+\S|^\[\d+\]\s+\S"
)

# Bibliography 모드를 해제하는 명시적 구조적 헤딩 (번호 없는 고정 키워드 / 장절)
# 이 패턴에 해당해야만 bibliography 모드가 해제됨
_BIBLIO_RESET_RE = re.compile(
    r"^(?:"
    r"Abstract|초록|요약|서론|결론|서문|부록|Appendix"
    r"|Acknowledgments?|감사의\s*글?"
    r"|Introduction|Conclusions?|Methods?|Results?|Discussion|Background"
    r"|Related\s+Work|Experiments?|Evaluation|Future\s+Work"
    r"|제\s*\d+\s*[장절항편]|Chapter\s+\d"
    r")$",
    re.IGNORECASE,
)

# TOC에서 참고문헌 항목 제거: URL/doi/arXiv 포함 항목 및 [숫자] 형식
_BIBLIO_TOC_URL_RE = re.compile(r"arXiv:|doi\.org|https?://", re.IGNORECASE)

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

    toc_found: bool = index_result.get("toc_found", False)

    # 섹션 분리
    if ext in (".csv", ".xlsx"):
        sections = _split_tabular(blocks, toc)
    elif ext == ".docx" and original_path:
        sections = _split_docx(original_path, toc, blocks)
    elif ext == ".pptx" and original_path:
        sections = _split_pptx(blocks)
    else:
        sections = _split_by_toc(toc, blocks, toc_found)

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


# ── CSV / XLSX 분리 ───────────────────────────────────────

def _split_tabular(blocks: list[dict], toc: list[dict]) -> list[Section]:
    """csv/xlsx: table 블록 1개 = 섹션 1개. TOC 제목을 섹션 제목으로 사용."""
    flat = _flatten_toc(toc)
    table_blocks = [b for b in blocks if b.get("block_type") == "table"]

    if not table_blocks:
        return [Section(title="전체", level=1, section_path="전체",
                        domain_category="", blocks=blocks)]

    sections = []
    for i, block in enumerate(table_blocks):
        if i < len(flat):
            title = flat[i]["title"]
            section_path = flat[i]["section_path"]
        else:
            title = f"시트{i + 1}"
            section_path = title
        sections.append(Section(
            title=title, level=1,
            section_path=section_path, domain_category="",
            blocks=[block],
        ))
    return sections


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

def _split_by_toc(
    toc: list[dict], blocks: list[dict], toc_found: bool = True
) -> list[Section]:
    """TOC 타이틀과 블록 첫 줄 매칭으로 섹션 분리.

    toc_found=False(문서 유형 구조에서 추론된 TOC)이면 텍스트 정확 매칭 대신
    실제 헤딩 감지 → 균등 분배 순으로 폴백한다.
    """
    if not toc:
        return [Section(title="전체", level=1, section_path="전체",
                        domain_category="", blocks=blocks)]

    if not toc_found:
        # 추론된 구조: 실제 문서 헤딩 감지 시도
        sections = _split_by_heading_detection(blocks)
        if sections:
            return sections
        # 헤딩 미감지 → 인페이지 구조에 맞춰 균등 분배
        return _split_proportionally(_flatten_toc(toc), blocks)

    # toc_found=True: 원문 목차 존재 → 텍스트 매칭
    flat_toc = _flatten_toc(toc)
    # LLM이 참고문헌 개별 항목을 TOC에 잘못 포함시킨 경우 필터링
    # - [1] xxx 형식 또는 URL/doi/arXiv 포함 항목 제거
    # - 1. xxx 형식은 실제 섹션 번호와 구분 불가하므로 URL 포함 여부로만 판단
    flat_toc = [
        t for t in flat_toc
        if not _BIBLIO_TOC_URL_RE.search(t["title"].strip())
        and not re.match(r"^\[\d+\]\s+\S", t["title"].strip())
    ]
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


def _split_by_heading_detection(blocks: list[dict]) -> list[Section]:
    """블록 내 라인 단위로 헤딩 패턴을 감지하여 섹션 분리.

    헤딩이 2개 미만이면 빈 리스트를 반환(폴백 신호).
    """
    # 모든 텍스트 라인 + 비텍스트 블록을 순서 보존 이벤트로 변환
    events: list[dict] = []
    for block in blocks:
        if block.get("block_type") == "text":
            page = block.get("page", 0)
            for line in block["content"].splitlines():
                stripped = line.strip()
                kind = "heading" if stripped and _HEADING_RE.match(stripped) else "line"
                events.append({"kind": kind, "content": line, "stripped": stripped, "page": page})
        else:
            events.append({"kind": "block", "block": block})

    # References/Bibliography 섹션 이후의 항목은 본문으로 처리
    # - _BIBLIO_SECTION_RE.search(): "References", "5. References" 등 모든 형태 감지
    # - bibliography 모드 해제는 _BIBLIO_RESET_RE(명시적 구조 헤딩)에 해당할 때만
    # - 그 외 모든 헤딩(번호형 arXiv ID 포함)은 본문으로 강등
    in_bibliography = False
    for ev in events:
        if ev["kind"] != "heading":
            continue
        stripped = ev.get("stripped", "")
        if _BIBLIO_SECTION_RE.search(stripped):
            in_bibliography = True
        elif in_bibliography:
            if _BIBLIO_RESET_RE.match(stripped):
                in_bibliography = False  # 명시적 구조 헤딩 → 모드 해제, 헤딩 유지
            else:
                ev["kind"] = "line"  # 그 외 모든 항목 → 본문으로 강등

    heading_indices = [i for i, ev in enumerate(events) if ev["kind"] == "heading"]
    if len(heading_indices) < 2:
        return []

    def _build_section(title: str, page: int | None, ev_slice: list[dict]) -> Section:
        text_lines = [ev["content"] for ev in ev_slice
                      if ev["kind"] in ("line", "heading") and ev.get("stripped")]
        extra_blocks = [ev["block"] for ev in ev_slice if ev["kind"] == "block"]
        sec_blocks: list[dict] = []
        if text_lines:
            sec_blocks.append({
                "block_type": "text", "content": "\n".join(text_lines),
                "page": page, "bbox": None, "minio_key": None, "table_json": None,
            })
        sec_blocks.extend(extra_blocks)
        return Section(title=title, level=1, section_path=title,
                       domain_category="", blocks=sec_blocks)

    sections: list[Section] = []

    # 첫 헤딩 이전 → 문서 헤더
    pre = events[:heading_indices[0]]
    if pre:
        pre_text = "\n".join(ev["content"] for ev in pre
                             if ev["kind"] in ("line", "heading") and ev.get("stripped"))
        pre_extra = [ev["block"] for ev in pre if ev["kind"] == "block"]
        header_blocks: list[dict] = []
        if pre_text.strip():
            header_blocks.append({
                "block_type": "text", "content": pre_text,
                "page": pre[0].get("page"), "bbox": None, "minio_key": None, "table_json": None,
            })
        header_blocks.extend(pre_extra)
        if header_blocks:
            sections.append(Section(
                title="문서 헤더", level=0, section_path="문서 헤더",
                domain_category="", blocks=header_blocks,
            ))

    for k, h_idx in enumerate(heading_indices):
        h_ev = events[h_idx]
        end = heading_indices[k + 1] if k + 1 < len(heading_indices) else len(events)
        body = events[h_idx + 1:end]
        sections.append(_build_section(h_ev["stripped"], h_ev.get("page"), body))

    return sections


def _split_proportionally(flat_toc: list[dict], blocks: list[dict]) -> list[Section]:
    """헤딩을 감지할 수 없을 때 블록을 섹션 수에 맞춰 균등 분배."""
    if not flat_toc:
        return [Section(title="전체", level=1, section_path="전체",
                        domain_category="", blocks=blocks)]
    n, m = len(flat_toc), len(blocks)
    if m == 0:
        return [Section(title=t["title"], level=t["level"], section_path=t["section_path"],
                        domain_category="", blocks=[]) for t in flat_toc]
    size = max(1, m // n)
    sections = []
    for i, t in enumerate(flat_toc):
        start = i * size
        end = start + size if i < n - 1 else m
        sections.append(Section(
            title=t["title"], level=t["level"],
            section_path=t["section_path"], domain_category="",
            blocks=blocks[start:end],
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
