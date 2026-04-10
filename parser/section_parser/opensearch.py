"""OpenSearch 인덱스 생성 및 명제 적재"""
from __future__ import annotations
import sys
from pathlib import Path

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    OPENSEARCH_HOST, OPENSEARCH_PORT, OPENSEARCH_SSL, OPENSEARCH_VERIFY_CERTS,
    OPENSEARCH_USER, OPENSEARCH_PASSWORD, EMBEDDING_DIM,
)

INDEX_NAME = "parser_propositions"

_client: OpenSearch | None = None


def _get_client() -> OpenSearch:
    global _client
    if _client is None:
        _client = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_compress=True,
            use_ssl=OPENSEARCH_SSL,
            verify_certs=OPENSEARCH_VERIFY_CERTS,
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD) if OPENSEARCH_USER else None,
        )
    return _client


_INDEX_BODY = {
    "settings": {
        "index": {"knn": True},
        "analysis": {
            "analyzer": {
                # 한국어+영어 혼합 analyzer
                # - nori_tokenizer: 한글 형태소 분리, 영어는 ASCII 연속열로 토큰화
                # - nori_part_of_speech: 조사·어미 제거 → 어형 변화 무관 매칭
                # - lowercase + english_stemmer: 영어 대소문자·어간 통일
                #   (searching/searched/search → search)
                "korean": {
                    "type": "custom",
                    "tokenizer": "nori_tokenizer",
                    "filter": ["nori_part_of_speech", "lowercase", "english_stemmer"],
                },
            },
            "filter": {
                "nori_part_of_speech": {
                    "type": "nori_part_of_speech",
                    "stoptags": ["E", "IC", "J", "MAG", "MAJ", "MM",
                                 "SP", "SSC", "SSO", "SC", "SE",
                                 "XPN", "XSA", "XSN", "XSV", "UNA", "NA", "VSV"],
                },
                "english_stemmer": {
                    "type": "stemmer",
                    "language": "english",
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "document_id":     {"type": "keyword"},
            "section_id":      {"type": "integer"},
            # section_path / doc_name 은 영문 경로·파일명이므로 standard 유지
            "section_path":    {"type": "text", "analyzer": "standard"},
            "doc_name":        {"type": "text", "analyzer": "standard"},
            "doc_type":        {"type": "keyword"},
            "domain_category": {"type": "keyword"},
            # LLM이 한국어로 생성하므로 nori 형태소 분석 적용
            "proposition":     {"type": "text", "analyzer": "korean"},
            # keyword → text 변경: exact-match 한계 해소, 형태소 분석으로 부분 매칭 가능
            "keywords":        {"type": "text", "analyzer": "korean"},
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBEDDING_DIM,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                },
            },
        }
    },
}


def init_index(force: bool = False) -> None:
    """인덱스가 없으면 생성.

    Args:
        force: True이면 기존 인덱스를 삭제하고 재생성 (매핑 변경 시 사용).
    """
    client = _get_client()
    if client.indices.exists(index=INDEX_NAME):
        if not force:
            return
        client.indices.delete(index=INDEX_NAME)

    client.indices.create(index=INDEX_NAME, body=_INDEX_BODY)


def index_propositions(docs: list[dict]) -> int:
    """명제 문서 목록을 bulk 색인. 성공 건수 반환.

    docs 각 항목:
        {
            document_id, section_id, section_path,
            doc_type, domain_category,
            proposition, keywords, embedding
        }
    """
    client = _get_client()
    actions = [
        {"_index": INDEX_NAME, "_source": doc}
        for doc in docs
    ]
    success, _ = bulk(client, actions, raise_on_error=False)
    return success


def delete_by_document(document_id: str) -> None:
    """문서 단위로 기존 명제 삭제 (재실행 멱등성)."""
    client = _get_client()
    client.delete_by_query(
        index=INDEX_NAME,
        body={"query": {"term": {"document_id": document_id}}},
    )
