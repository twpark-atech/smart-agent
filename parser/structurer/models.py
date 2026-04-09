"""구조화 결과 데이터 모델"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Section:
    """TOC 단위로 분리된 섹션."""
    title: str                          # TOC 항목 제목
    level: int                          # 1=대분류, 2=중분류, 3=소분류
    section_path: str                   # 예) "4. 애플리케이션 아키텍처 > 4.1. 전체 시스템 구성도"
    domain_category: str                # domain_type.md 대분류명
    blocks: list[dict] = field(default_factory=list)   # 해당 섹션에 속한 블록 목록

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "level": self.level,
            "section_path": self.section_path,
            "domain_category": self.domain_category,
            "blocks": self.blocks,
        }


@dataclass
class StructuredDocument:
    """structurer 결과."""
    doc_type: str
    domain_category: str                # 문서 전체 도메인
    sections: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "domain_category": self.domain_category,
            "sections": [s.to_dict() for s in self.sections],
        }
