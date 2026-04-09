"""flat 포맷 파싱 - .md / .csv / .xlsx"""
import csv
from pathlib import Path

from .models import Block
from .llm import describe_table


def parse(file_path: str) -> list[Block]:
    ext = Path(file_path).suffix.lower()

    if ext == ".md":
        return _parse_md(file_path)
    elif ext == ".csv":
        return _parse_csv(file_path)
    elif ext == ".xlsx":
        return _parse_xlsx(file_path)
    else:
        raise ValueError(f"flat_parser 지원 포맷이 아닙니다: {ext}")


def _parse_md(path: str) -> list[Block]:
    text = Path(path).read_text(encoding="utf-8")
    return [Block(block_type="text", content=text, page=0, bbox=None)]


def _parse_csv(path: str) -> list[Block]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        for row in reader:
            rows.append(dict(row))

    if not rows:
        return []

    md_lines = ["| " + " | ".join(str(h) for h in headers) + " |"]
    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        md_lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    table_md = "\n".join(md_lines)

    description = describe_table(table_md)
    return [Block(
        block_type="table",
        content=f"[표 설명]\n{description}\n\n[표 원문]\n{table_md}",
        page=0,
        bbox=None,
        table_json=rows,
    )]


def _parse_xlsx(path: str) -> list[Block]:
    import openpyxl
    blocks: list[Block] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    for sheet_idx, ws in enumerate(wb.worksheets):
        data = list(ws.values)
        if not data:
            continue

        headers = [str(c) if c is not None else f"col{i}" for i, c in enumerate(data[0])]
        rows = []
        for row in data[1:]:
            rows.append({headers[i]: (str(v) if v is not None else "") for i, v in enumerate(row)})

        md_lines = ["| " + " | ".join(headers) + " |"]
        md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows:
            md_lines.append("| " + " | ".join(row.get(h, "") for h in headers) + " |")
        table_md = "\n".join(md_lines)

        description = describe_table(table_md)
        blocks.append(Block(
            block_type="table",
            content=f"[시트: {ws.title}]\n[표 설명]\n{description}\n\n[표 원문]\n{table_md}",
            page=sheet_idx,
            bbox=None,
            table_json=rows,
        ))

    wb.close()
    return blocks
