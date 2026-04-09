"""Planner Agent - 쿼리 분석, 도메인 선택, Task Queue 생성"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import LLM_URL, LLM_API_KEY, VLM_MODEL
from models import DOMAINS, TaskQueue

logger = logging.getLogger(__name__)

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)

# PostgreSQL 테이블 스키마 (Text-to-SQL용 참고 정보)
_PG_SCHEMA = """
parser_documents  (document_id, source_path, original_ext, doc_type, domain_category)
parser_sections   (id, document_id, title, level, section_path, domain_category, seq)
parser_tables     (id, document_id, block_id, section_id, page, headers JSONB, row_count, table_index)
parser_table_rows (id, table_id, row_index, row_data JSONB)
"""

_PLAN_PROMPT = """당신은 문서 검색 시스템의 Planner입니다.
사용자의 질의를 분석하여 최적의 검색 계획을 수립하세요.
반드시 한국어로만 답하세요. 중국어 등 다른 언어는 절대 사용하지 마세요.

## 도메인 분류 (10개 중 1개 선택)
{domains}

## PostgreSQL 스키마 (정량적 검색 시 참고)
{schema}

## 작업 유형
- document: 내부 문서 기반 검색으로 충분한 경우
- web: 내부 문서에 없는 최신 정보나 외부 지식이 필요한 경우
- quantitative: 수치, 통계, 집계 등 정량적 결과가 필요한 경우
- mixed: document + web 복합 검색이 필요한 경우

## 사용자 질의
{query}

## 지시사항
아래 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "task_type": "document|web|quantitative|mixed",
  "domain": "10개 도메인 중 가장 관련성 높은 1개",
  "domain_query": "도메인 분류 검색을 위한 핵심 키워드 쿼리",
  "embedding_query": "Vector 유사도 검색에 최적화된 자연어 문장 (의미 중심)",
  "keyword_query": "BM25 키워드 검색을 위한 핵심 단어들 (공백 구분)",
  "sql_query": "정량적 검색 시 SELECT 쿼리 (불필요하면 null)",
  "reason": "이 계획을 선택한 이유 (한 문장)"
}}"""

_REWRITE_PROMPT = """당신은 문서 검색 시스템의 Planner입니다.
이전 검색이 실패했습니다. 다른 관점으로 검색 쿼리를 재작성하세요.
반드시 한국어로만 답하세요.

## 원본 질의
{original_query}

## 이전 검색 실패 이유
{reason}

## 이전 검색 쿼리
- embedding_query: {prev_embedding}
- keyword_query: {prev_keyword}

## 지시사항
더 넓거나 다른 관점의 쿼리로 재작성하여 아래 JSON 형식으로만 답하세요.
{{
  "domain_query": "...",
  "embedding_query": "...",
  "keyword_query": "...",
  "sql_query": null
}}"""


def _call_llm(prompt: str, max_tokens: int = 1024, label: str = "") -> str:
    resp = _client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "당신은 문서 검색 전문가입니다. 반드시 한국어로만, JSON 형식으로만 답하세요.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    content = resp.choices[0].message.content.strip()
    tag = f"[Planner:{label}]" if label else "[Planner]"
    logger.info("%s 응답:\n%s", tag, content)
    return content


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}


def plan(query: str) -> TaskQueue:
    """사용자 질의를 분석하여 TaskQueue를 생성."""
    prompt = _PLAN_PROMPT.format(
        domains="\n".join(f"- {d}" for d in DOMAINS),
        schema=_PG_SCHEMA,
        query=query,
    )
    raw = _call_llm(prompt, label="plan")
    data = _parse_json(raw)

    if not data:
        logger.warning("Planner LLM 응답 파싱 실패, 기본값으로 폴백")
        data = {}

    task_type = data.get("task_type", "document")
    if task_type not in ("document", "web", "quantitative", "mixed"):
        task_type = "document"

    domain = data.get("domain", DOMAINS[0])
    if domain not in DOMAINS:
        domain = DOMAINS[0]

    task_queue = TaskQueue.new(
        original_query=query,
        task_type=task_type,
        domain=domain,
        domain_query=data.get("domain_query", query),
        embedding_query=data.get("embedding_query", query),
        keyword_query=data.get("keyword_query", query),
        sql_query=data.get("sql_query"),
    )

    logger.info(
        "Planner 계획 수립 완료 | task_type=%s, domain=%s, task_id=%s",
        task_type, domain, task_queue.task_id,
    )
    return task_queue


def rewrite(task_queue: TaskQueue, reason: str) -> TaskQueue:
    """검색 실패 시 쿼리를 재작성하고 TaskQueue를 갱신."""
    prompt = _REWRITE_PROMPT.format(
        original_query=task_queue.query.original,
        reason=reason,
        prev_embedding=task_queue.query.embedding_query,
        prev_keyword=task_queue.query.keyword_query,
    )
    raw = _call_llm(prompt, label="rewrite")
    data = _parse_json(raw)

    if data:
        task_queue.query.domain_query   = data.get("domain_query",   task_queue.query.domain_query)
        task_queue.query.embedding_query = data.get("embedding_query", task_queue.query.embedding_query)
        task_queue.query.keyword_query  = data.get("keyword_query",  task_queue.query.keyword_query)
        task_queue.query.sql_query      = data.get("sql_query",      task_queue.query.sql_query)

    # 스텝 상태 초기화
    for step in task_queue.steps:
        step.status = "pending"

    task_queue.touch()
    logger.info("Planner 쿼리 재작성 완료 (retry=%d)", task_queue.retrieval_retry)
    return task_queue
