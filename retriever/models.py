"""Retrieval Multi-Agent 데이터 모델"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── 도메인 분류 (domain_type.md 기준 10개 최상위 도메인) ──────────────
DOMAINS = [
    "공공/행정",
    "경제/금융",
    "산업/제조",
    "과학기술/IT",
    "법제/사법",
    "의료/복지",
    "교육",
    "환경/에너지",
    "사회/문화",
    "국방/외교",
]

# ── Task 유형 ─────────────────────────────────────────────────────────
TASK_TYPES = ["document", "web", "quantitative", "mixed"]

# ── Step 유형 ─────────────────────────────────────────────────────────
STEP_TYPES = [
    "domain_search",
    "document_search",
    "node_search",
    "web_search",
    "quantitative_search",
    "image_search",
]

# ── Step 상태 ─────────────────────────────────────────────────────────
STATUS_PENDING     = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE        = "done"
STATUS_FAILED      = "failed"

# ── Supervisor / Aggregator 신호 ──────────────────────────────────────
SIGNAL_PASS            = "PASS"
SIGNAL_RE_SEARCH       = "RE_SEARCH"
SIGNAL_RETRY_RETRIEVAL = "RETRY_RETRIEVAL"
SIGNAL_RETRY_WRITER    = "RETRY_WRITER"


@dataclass
class QueryPlan:
    original: str
    domain_query: str
    embedding_query: str
    keyword_query: str
    sql_query: Optional[str] = None


@dataclass
class TaskStep:
    step_id: str
    type: str
    status: str = STATUS_PENDING
    dependency: list[str] = field(default_factory=list)


@dataclass
class TaskQueue:
    task_id: str
    query: QueryPlan
    task_type: str        # document | web | quantitative | mixed
    domain: str
    steps: list[TaskStep]
    retrieval_retry: int      = 0
    max_retrieval_retry: int  = 3
    supervisor_retry: int     = 0
    max_supervisor_retry: int = 2
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()

    @staticmethod
    def new(
        original_query: str,
        task_type: str,
        domain: str,
        domain_query: str,
        embedding_query: str,
        keyword_query: str,
        sql_query: Optional[str] = None,
    ) -> "TaskQueue":
        steps = _build_steps(task_type)
        return TaskQueue(
            task_id=str(uuid.uuid4()),
            query=QueryPlan(
                original=original_query,
                domain_query=domain_query,
                embedding_query=embedding_query,
                keyword_query=keyword_query,
                sql_query=sql_query,
            ),
            task_type=task_type,
            domain=domain,
            steps=steps,
        )


def _build_steps(task_type: str) -> list[TaskStep]:
    """task_type에 맞는 기본 step 목록 생성."""
    if task_type == "document":
        return [
            TaskStep("s1", "domain_search"),
            TaskStep("s2", "document_search", dependency=["s1"]),
            TaskStep("s3", "node_search",     dependency=["s2"]),
        ]
    if task_type == "web":
        return [
            TaskStep("s1", "web_search"),
        ]
    if task_type == "quantitative":
        return [
            TaskStep("s1", "domain_search"),
            TaskStep("s2", "quantitative_search", dependency=["s1"]),
        ]
    if task_type == "mixed":
        return [
            TaskStep("s1", "domain_search"),
            TaskStep("s2", "document_search", dependency=["s1"]),
            TaskStep("s3", "node_search",     dependency=["s2"]),
            TaskStep("s4", "web_search"),     # s2와 병렬
        ]
    return []


@dataclass
class SearchResult:
    """단일 검색 결과 표준 형식."""
    id: str
    score: float
    content: str
    keywords: list[str]
    source_type: str   # "document" | "node" | "web" | "quantitative" | "image"
    source: dict       # 출처 정보


@dataclass
class StructuredSection:
    section: str
    content: str
    sources: list[str]


@dataclass
class AggregatedContent:
    structured_content: list[StructuredSection]


@dataclass
class WriterOutput:
    answer: str
    sources: list[str]


@dataclass
class SupervisorOutput:
    signal: str        # PASS | RETRY_RETRIEVAL | RETRY_WRITER
    reason: str = ""


@dataclass
class RetrieverOutput:
    signal: str        # RE_SEARCH | ok
    reason: str = ""
    aggregated: Optional[AggregatedContent] = None
