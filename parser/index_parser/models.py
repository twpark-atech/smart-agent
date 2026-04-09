"""목차 파싱 결과 데이터 모델"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class TocNode:
    """목차 단일 항목."""
    title: str
    level: int              # 1=대분류, 2=중분류, 3=소분류
    page: int | None        # 원문에 명시된 페이지 번호 (없으면 None)
    children: list[TocNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "level": self.level,
            "page": self.page,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class IndexResult:
    """index_parser 결과."""
    doc_type: str                       # 분류된 문서 유형 (document_type.md 소분류명)
    toc_found: bool                     # 원문에 목차가 존재했는지 여부
    toc: list[TocNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "toc_found": self.toc_found,
            "toc": [node.to_dict() for node in self.toc],
        }
