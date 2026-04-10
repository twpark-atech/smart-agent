"""Index Parser - 문서 유형 분류 + 목차 추출"""
from __future__ import annotations

from .models import TocNode, IndexResult
from .llm import classify_doc_type, extract_toc, infer_toc_from_type

# 앞부분 샘플링 페이지 수
PREVIEW_PAGES = 20


def _blocks_to_text(blocks: list[dict], max_pages: int = PREVIEW_PAGES) -> str:
    """블록 리스트에서 텍스트만 추출. max_pages 이내 페이지만 사용."""
    lines = []
    for block in blocks:
        if block.get("page", 0) >= max_pages:
            break
        if block.get("block_type") == "text":
            lines.append(block["content"])
    return "\n\n".join(lines)


def _build_toc_nodes(raw: list[dict]) -> list[TocNode]:
    """LLM 반환 dict 리스트를 TocNode 트리로 변환."""
    def _convert(item: dict) -> TocNode:
        return TocNode(
            title=item.get("title", ""),
            level=int(item.get("level", 1)),
            page=item.get("page"),
            children=[_convert(c) for c in item.get("children", [])],
        )
    return [_convert(item) for item in raw]


def run(parsed_result: dict) -> IndexResult:
    """format_converter 결과(parsed_result)를 받아 문서 유형 + 목차를 반환.

    TOC 추출 우선순위:
      1. 원본이 csv/xlsx → 시트(table 블록) 단위 TOC 직접 생성 (LLM 불필요)
      2. 원본이 docx → Heading 스타일 직접 추출 (LLM 불필요)
      3. 원본이 pptx → 슬라이드 제목 직접 추출
      4. 그 외 → LLM으로 앞 20페이지 분석
      5. 모두 실패 → 문서 유형 기본 구조로 대체

    Args:
        parsed_result: format_converter step의 result dict

    Returns:
        IndexResult
    """
    from .heading_extractor import extract_from_docx, extract_from_pptx

    original_path: str = parsed_result.get("original_path", "")
    ext = parsed_result.get("extension", "").lower()
    blocks: list[dict] = parsed_result.get("parsed", {}).get("blocks", [])

    # csv/xlsx: 표 문서이므로 LLM 없이 시트(블록) 단위로 TOC 직접 구성
    if ext in (".csv", ".xlsx"):
        table_blocks = [b for b in blocks if b.get("block_type") == "table"]
        if not table_blocks:
            raw_toc = [{"title": "전체", "level": 1, "children": []}]
        elif ext == ".csv":
            raw_toc = [{"title": "데이터", "level": 1, "children": []}]
        else:  # xlsx: 시트명을 제목으로 사용
            raw_toc = []
            for b in table_blocks:
                # content 첫 줄에서 "[시트: ...]" 추출
                first_line = b.get("content", "").splitlines()[0] if b.get("content") else ""
                import re as _re
                m = _re.match(r"\[시트:\s*(.+?)\]", first_line)
                title = m.group(1).strip() if m else f"시트{b.get('page', 0) + 1}"
                raw_toc.append({"title": title, "level": 1, "children": []})

        return IndexResult(
            doc_type="스프레드시트",
            toc_found=True,
            toc=_build_toc_nodes(raw_toc),
        )

    preview_text = _blocks_to_text(blocks, max_pages=PREVIEW_PAGES)

    # 1. 문서 유형 분류
    doc_type = classify_doc_type(preview_text)

    # 2. TOC 추출
    toc_found = False
    raw_toc: list[dict] = []

    if ext == ".docx" and original_path:
        raw_toc = extract_from_docx(original_path)
        toc_found = bool(raw_toc)

    elif ext == ".pptx" and original_path:
        raw_toc = extract_from_pptx(original_path)
        toc_found = bool(raw_toc)

    if not toc_found:
        # LLM으로 텍스트 기반 추출 시도
        toc_data = extract_toc(preview_text)
        toc_found = toc_data.get("toc_found", False)
        raw_toc = toc_data.get("toc", [])

    # 3. 여전히 없으면 문서 유형 기본 구조로 대체
    if not raw_toc:
        raw_toc = infer_toc_from_type(doc_type)
        toc_found = False

    toc_nodes = _build_toc_nodes(raw_toc)

    return IndexResult(
        doc_type=doc_type,
        toc_found=toc_found,
        toc=toc_nodes,
    )
