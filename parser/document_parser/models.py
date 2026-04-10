"""파싱 결과 데이터 모델"""
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Block:
    """문서에서 추출된 단일 블록.

    block_type:
        - "text"  : 본문 텍스트
        - "image" : 이미지 (description + MinIO 참조)
        - "table" : 표 (description + JSON + PostgreSQL 적재 예정)
    """
    block_type: Literal["text", "image", "table"]
    content: str            # 텍스트 본문 또는 description
    page: int               # 0-indexed 페이지 번호
    bbox: list[float] | None = None   # [x0, y0, x1, y1] (포인트 단위)
    # image 전용
    minio_key: str | None = None      # MinIO 오브젝트 키
    # table 전용
    table_json: list[dict] | None = None  # 표 원본 JSON (행 단위 dict 리스트)
    sheet_name: str | None = None         # XLSX 시트명 / CSV 파일명
    header_depth: int = 1                 # 다중 헤더 행 수
    description: str | None = None       # LLM 생성 표 설명

    def to_dict(self) -> dict:
        return {
            "block_type": self.block_type,
            "content": self.content,
            "page": self.page,
            "bbox": self.bbox,
            "minio_key": self.minio_key,
            "table_json": self.table_json,
            "sheet_name": self.sheet_name,
            "header_depth": self.header_depth,
            "description": self.description,
        }


@dataclass
class ParsedDocument:
    """문서 파싱 결과 전체"""
    source_path: str
    extension: str
    pdf_type: Literal["text", "image", "none"] = "none"  # PDF인 경우만 의미 있음
    blocks: list[Block] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "extension": self.extension,
            "pdf_type": self.pdf_type,
            "blocks": [b.to_dict() for b in self.blocks],
        }
