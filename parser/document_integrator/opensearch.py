"""문서 레벨 OpenSearch 적재"""
from __future__ import annotations
import sys
from pathlib import Path

from opensearchpy import OpenSearch

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import OPENSEARCH_HOST, OPENSEARCH_PORT, EMBEDDING_DIM

INDEX_NAME = "parser_documents"

_client: OpenSearch | None = None


def _get_client() -> OpenSearch:
    global _client
    if _client is None:
        _client = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_compress=True,
        )
    return _client


_INDEX_BODY = {
    "settings": {
        "index": {"knn": True},
        "analysis": {
            "analyzer": {
                # 한국어+영어 혼합 analyzer (section_parser/opensearch.py 와 동일 전략)
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
            "source_path":     {"type": "keyword"},
            "doc_type":        {"type": "keyword"},
            "domain_category": {"type": "keyword"},
            # 파일명은 영문 경로이므로 standard 유지
            "doc_name":        {"type": "text", "analyzer": "standard"},
            # LLM이 한국어로 생성하므로 nori 형태소 분석 적용
            "summary":         {"type": "text", "analyzer": "korean"},
            # keyword → text 변경: exact-match 한계 해소
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
    """Args:
        force: True이면 기존 인덱스를 삭제하고 재생성 (매핑 변경 시 사용).
    """
    client = _get_client()
    if client.indices.exists(index=INDEX_NAME):
        if not force:
            return
        client.indices.delete(index=INDEX_NAME)

    client.indices.create(index=INDEX_NAME, body=_INDEX_BODY)


def index_document(doc: dict) -> None:
    """문서 1건을 document_id를 _id로 색인 (upsert)."""
    client = _get_client()
    client.index(
        index=INDEX_NAME,
        id=doc["document_id"],
        body=doc,
    )


def delete_by_document(document_id: str) -> None:
    """문서 단위로 OpenSearch parser_documents 인덱스 삭제."""
    client = _get_client()
    if client.indices.exists(index=INDEX_NAME):
        client.delete(index=INDEX_NAME, id=document_id, ignore=[404])
