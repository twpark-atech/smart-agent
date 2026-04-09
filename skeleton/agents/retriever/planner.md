# 역할
사용자의 질문을 검색 친화적 쿼리로 변환하고, 요청 해석/작업 분해 후 실행 계획을 수립하는 Agent에 대한 코드를 작성한다.
Task Queue를 생성하여 Orchestrator에게 전달하며, 재검색 요청 시 쿼리를 재작성하여 Task Queue를 갱신한다.

## Planner의 역할
- 사용자 질의 → 검색 친화적 쿼리 변환
- 요청 해석 및 작업 유형 판단
- 도메인 선택
- 작업 분해 및 실행 계획 수립
- Task Queue 생성 후 Orchestrator에 전달
- Orchestrator로부터 재작성 요청 수신 시 쿼리 재작성 및 Task Queue 갱신

## Task Queue 구조

```python
{
    "task_id": str,
    "query": {
        "original": str,           # 사용자 원본 질의
        "domain_query": str,       # 도메인 검색용 쿼리
        "embedding_query": str,    # 임베딩 검색용 쿼리
        "keyword_query": str,      # BM25 키워드 검색용 쿼리
        "sql_query": str | None,   # 정량적 검색 시 사용 (없으면 None)
    },
    "task_type": str,              # "document" | "web" | "quantitative" | "mixed"
    "domain": str,                 # 선택된 도메인 (domain_type.md 참고)
    "steps": [                     # 작업 단계 목록
        {
            "step_id": str,
            "type": str,           # "domain_search" | "document_search" | "node_search" | "web_search" | "quantitative_search" | "image_search"
            "status": str,         # "pending" | "in_progress" | "done" | "failed"
            "dependency": list[str] # 선행 step_id 목록
        }
    ],
    "retrieval_retry": 0,          # 현재 재검색 횟수
    "max_retrieval_retry": 3,      # 최대 재검색 허용 횟수
    "supervisor_retry": 0,         # 현재 Supervisor 피드백 재처리 횟수
    "max_supervisor_retry": 2,     # 최대 Supervisor 피드백 재처리 허용 횟수
    "created_at": str,
    "updated_at": str
}
```

## 작업 유형 판단 기준

| 유형 | 조건 |
|------|------|
| document | 내부 문서 기반 검색으로 충분한 경우 |
| web | 내부 문서에 없는 최신 정보 또는 외부 지식이 필요한 경우 |
| quantitative | 수치, 통계, 집계 등 정량적 결과가 필요한 경우 |
| mixed | 위 유형이 복합적으로 필요한 경우 |

## 참고사항
- 검색 구조: 도메인 선택 → 문서 선택 → 노드 선택 (계층적 검색)
- 문서 선택: Summary Embedding Vector Search + Keyword BM25 Search 병렬
- 노드 선택: Proposition Embedding Vector Search + Keyword BM25 Search 병렬
- 사용 도메인은 /smart-agent/domain_type.md의 10가지 도메인 중 선택
- Task Queue 생성 후 Orchestrator에게 전달하며, 이후 Task Queue의 상태 관리는 Orchestrator가 담당
- 재작성 요청 수신 시 기존 Task Queue의 query 필드를 갱신하고 retrieval_retry를 증가시켜 반환
