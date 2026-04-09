"""인덱싱 파이프라인
chunking_strategy.md 기준:
  [1] 문서 파싱 (Docling PDF / MD 직접)
  [2] 문서 유형·도메인 분류 (LLM)
  [3] 목차 기반 계층 분리
  [3-5] 섹션 단위 요약 생성 (LLM) → section_summary_embedding
  [4] 토큰 크기 조정 (2048 초과 분할)
  [5] Proposition 추출 (LLM) → proposition_embedding
  [6] Contextual Chunking 접두어 생성
  [7] 임베딩 + OpenSearch 적재
"""
import re
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from shared import llm, embedder, opensearch_client as os_client
from shared.config import MAX_TOKENS, MIN_TOKENS


# ─── 토큰 추정 ───────────────────────────────────────────

def _tokens(text: str) -> int:
    korean = len(re.findall(r"[가-힣]", text))
    other = len(text) - korean
    return int(korean / 2 + other / 4)


# ─── [1] 문서 파싱 ────────────────────────────────────────

def _parse_md(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse_docling(path: str) -> str:
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    result = converter.convert(path)
    return result.document.export_to_markdown()


def parse_document(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in (".md", ".txt"):
        return _parse_md(path)
    return _parse_docling(path)


# ─── [3] 목차 파싱 + 섹션 분리 ───────────────────────────

def _split_by_headings(md_text: str) -> list[dict]:
    lines = md_text.split("\n")
    sections = []
    current: dict = {"title": "서두", "lines": []}
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            if current["lines"]:
                content = "\n".join(current["lines"]).strip()
                if content:
                    sections.append({"title": current["title"], "content": content})
            current = {"title": m.group(2).strip(), "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"]:
        content = "\n".join(current["lines"]).strip()
        if content:
            sections.append({"title": current["title"], "content": content})
    return sections


def _parse_toc(md_text: str) -> list[dict] | None:
    lines = md_text.split("\n")
    toc_start = None
    for i, line in enumerate(lines):
        if re.match(r"^#+\s*목\s*차", line):
            toc_start = i + 1
            break
    if toc_start is None:
        return None

    entries = []
    current_major = None
    for line in lines[toc_start: toc_start + 120]:
        line = line.strip()
        if re.match(r"^#+\s+", line) and "목" not in line:
            break
        row = re.match(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if not row:
            continue
        col1, col2 = row.group(1).strip(), row.group(2).strip()
        if set(col1) <= {"-", "|", " "}:
            continue
        if col1 == "□":
            title = re.sub(r"[·…\s]+$", "", col2).strip()
            if title and len(title) > 2:
                current_major = title
                entries.append({"depth": 1, "major": current_major, "title": current_major})
        elif re.match(r"^\d+\.?\s*$", col1) and current_major:
            num = re.match(r"^(\d+)", col1).group(1)
            title = re.sub(r"[·…]+.*$", "", col2).strip()
            entries.append({"depth": 2, "major": current_major, "title": f"{num}. {title}"})
    return entries or None


def _split_by_toc(md_text: str, toc: list[dict]) -> list[dict]:
    targets = [e for e in toc if e["depth"] == 2] or [e for e in toc if e["depth"] == 1]
    matchers = []
    for e in targets:
        core = re.sub(r"^\d+\.\s*", "", e["title"]).strip()[:15]
        matchers.append({"entry": e, "core": core})

    all_secs = _split_by_headings(md_text)
    chunks, current = [], {"major": "서두", "title": "서두", "secs": []}
    idx = 0

    for sec in all_secs:
        if re.match(r"^목\s*차$", sec["title"]):
            continue
        if idx < len(matchers):
            core = matchers[idx]["core"]
            if core and len(core) >= 3 and core in sec["title"]:
                if current["secs"]:
                    chunks.append(current)
                e = matchers[idx]["entry"]
                current = {"major": e["major"], "title": e["title"], "secs": []}
                idx += 1
        current["secs"].append(sec)

    if current["secs"]:
        chunks.append(current)

    result = []
    for chunk in chunks:
        text = "\n\n".join(s["content"] for s in chunk["secs"])
        paras = [
            p.strip() for s in chunk["secs"]
            for p in re.split(r"\n\s*\n", s["content"])
            if p.strip() and len(p.strip()) >= 10
            and not p.startswith("|") and not p.startswith("<!--")
        ]
        result.append({
            "title": chunk["title"],
            "major": chunk.get("major", ""),
            "secs": chunk["secs"],
            "text": text,
            "tokens": _tokens(text),
            "paragraphs": paras,
        })
    return result


def _split_sections_only(md_text: str) -> list[dict]:
    """목차 없음 — 헤딩 기반으로 섹션 묶기"""
    secs = _split_by_headings(md_text)
    result = []
    for sec in secs:
        text = sec["content"]
        paras = [
            p.strip() for p in re.split(r"\n\s*\n", text)
            if p.strip() and len(p.strip()) >= 10
        ]
        result.append({
            "title": sec["title"],
            "major": "",
            "secs": [sec],
            "text": text,
            "tokens": _tokens(text),
            "paragraphs": paras,
        })
    return result


# ─── [4] 토큰 크기 조정 ──────────────────────────────────

def _adjust_token_size(sections: list[dict]) -> list[dict]:
    final = []
    for sec in sections:
        if sec["tokens"] <= MAX_TOKENS:
            final.append(sec)
            continue
        # 분할 서브청크는 원본 section_id를 parent_section_id로 보존
        parent_section_id = sec.get("section_id", "")
        sub_buf: dict = {
            "title": sec["title"], "major": sec.get("major", ""),
            "text": "", "tokens": 0, "paragraphs": [],
            "parent_section_id": parent_section_id,
        }
        for sub_sec in sec["secs"]:
            t = _tokens(sub_sec["content"])
            if sub_buf["tokens"] + t > MAX_TOKENS and sub_buf["paragraphs"]:
                final.append({**sub_buf, "secs": []})
                sub_buf = {
                    "title": f"{sec['title']} > {sub_sec['title'][:25]}",
                    "major": sec.get("major", ""),
                    "text": "", "tokens": 0, "paragraphs": [],
                    "parent_section_id": parent_section_id,
                }
            sub_buf["text"] += "\n\n" + sub_sec["content"]
            sub_buf["tokens"] += t
            sub_buf["paragraphs"] += [
                p.strip() for p in re.split(r"\n\s*\n", sub_sec["content"])
                if p.strip() and len(p.strip()) >= 10
            ]
        if sub_buf["paragraphs"]:
            sub_buf["text"] = sub_buf["text"].strip()
            final.append({**sub_buf, "secs": []})
    return final


# ─── 메인 파이프라인 ──────────────────────────────────────

async def run(file_path: str, file_name: str, status: dict) -> str:
    doc_id = str(uuid.uuid4())
    status.update({"status": "indexing", "document_id": doc_id})

    try:
        # [1] 파싱
        status["step"] = "파싱 중..."
        md_text = await asyncio.to_thread(parse_document, file_path)

        # [2] 문서 유형·도메인 분류
        status["step"] = "문서 분류 중..."
        doc_info = await llm.classify_document(md_text)

        # [3] 목차 기반 섹션 분리
        status["step"] = "목차 분석 중..."
        toc = await asyncio.to_thread(_parse_toc, md_text)
        if toc:
            sections = await asyncio.to_thread(_split_by_toc, md_text, toc)
        else:
            sections = await asyncio.to_thread(_split_sections_only, md_text)

        # [3-5] 섹션 단위 요약 생성 (Index Search용)
        status["step"] = "섹션 요약 생성 중..."
        for sec in sections:
            sec["summary"] = await llm.generate_section_summary(sec["text"])
            sec["section_id"] = str(uuid.uuid4())

        # [4] 토큰 크기 조정
        chunks_raw = await asyncio.to_thread(_adjust_token_size, sections)

        # [5] Proposition 추출
        status["step"] = "명제 추출 중..."
        for chunk in chunks_raw:
            chunk["proposition"] = await llm.extract_proposition(chunk["text"])
            if "section_id" not in chunk:
                # 분할된 서브청크는 부모 section_id를 이어받아야 하는데,
                # _adjust_token_size에서 이미 넘겨짐
                chunk["section_id"] = str(uuid.uuid4())

        # [6] Contextual Chunking 접두어
        title = Path(file_name).stem
        domain_prefix = f"[도메인: {doc_info.get('domain_category', '')}]\n"
        doc_prefix = f"[문서: {title}]\n"

        # 문서 요약 + 키워드
        status["step"] = "문서 요약 생성 중..."
        doc_summary, doc_keywords = await llm.generate_doc_summary(md_text)

        # [7] 임베딩 생성
        status["step"] = "임베딩 생성 중..."

        # 섹션 요약 임베딩 (섹션 단위)
        sec_summaries = [s["summary"] for s in sections]
        sec_embs = await embedder.embed(sec_summaries)
        sec_emb_map = {s["section_id"]: emb for s, emb in zip(sections, sec_embs)}

        # Proposition 임베딩 (청크 단위)
        contextual_props = []
        for chunk in chunks_raw:
            section_path = (
                f"{chunk.get('major', '')} > {chunk['title']}"
                if chunk.get("major")
                else chunk["title"]
            )
            cp = (
                f"{domain_prefix}{doc_prefix}"
                f"[섹션: {section_path}]\n{chunk['proposition']}"
            )
            chunk["contextual_proposition"] = cp
            chunk["section_path"] = section_path
            contextual_props.append(cp)

        prop_embs = await embedder.embed(contextual_props)

        # 문서 요약 임베딩
        doc_summary_emb = await embedder.embed_one(doc_summary or title)

        # [7] OpenSearch 적재
        status["step"] = "OpenSearch 적재 중..."

        # Layer 2: 문서 인덱스
        await os_client.index_document({
            "document_id": doc_id,
            "title": title,
            "summary": doc_summary,
            "keywords": doc_keywords,
            "summary_embedding": doc_summary_emb,
            "domain_category": doc_info.get("domain_category", ""),
            "doc_type": doc_info.get("doc_type", ""),
            "file_name": file_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        # Layer 3: 청크 인덱스
        for chunk, prop_emb in zip(chunks_raw, prop_embs):
            # 섹션 요약 임베딩: 분할 서브청크도 부모 section_id 임베딩 사용
            sec_id = chunk.get("section_id", "")
            s_emb = sec_emb_map.get(sec_id, prop_emb)  # fallback

            # parent_section_id: 섹션에서 분할된 경우 부모 section_id
            parent_id = chunk.get("parent_section_id", sec_id)

            await os_client.index_chunk({
                "chunk_id": str(uuid.uuid4()),
                "document_id": doc_id,
                "parent_section_id": parent_id,
                "section_name": chunk["title"],
                "section_depth": 2 if chunk.get("major") else 1,
                "section_path": chunk["section_path"],
                "section_summary": chunk.get("summary", ""),
                "section_summary_embedding": s_emb,
                "proposition": chunk["proposition"],
                "contextual_proposition": chunk["contextual_proposition"],
                "proposition_embedding": prop_emb,
                "content": chunk["text"],
                "token_count": chunk["tokens"],
                "keywords": doc_keywords[:5],
            })

        status.update({
            "status": "completed",
            "step": "완료",
            "chunk_count": len(chunks_raw),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        status.update({"status": "failed", "step": f"오류: {e}"})
        raise

    return doc_id
