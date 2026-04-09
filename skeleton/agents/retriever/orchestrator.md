# 역할
전체 에이전트의 실행 흐름을 관리하고, 각 에이전트로부터 수신한 신호를 기반으로 라우팅을 담당하는 Agent에 대한 코드를 작성한다.

## Orchestrator의 역할
- 요청 수신 및 Planner 호출
- Planner가 생성한 Task Queue 수신 및 실행 관리
- 각 에이전트 호출 순서 및 조건 제어
- Aggregator / Supervisor의 재처리 신호 수신 및 라우팅
- 전체 retry 상태 관리 및 실패 응답 처리

## 에이전트 호출 순서 및 조건

```
1. Planner
   - 최초 요청 수신 시 항상 호출
   - Aggregator로부터 RE_SEARCH 신호 수신 시 재호출 (쿼리 재작성 목적)

2. Retriever
   - Planner로부터 Task Queue 수신 후 호출
   - Supervisor로부터 RETRY_RETRIEVAL 신호 수신 시 재호출

3. Aggregator
   - Retriever 검색 완료 후 호출

4. Writer
   - Aggregator 병합/구조화 완료 후 호출
   - Supervisor로부터 RETRY_WRITER 신호 수신 시 재호출

5. Supervisor
   - Writer 답변 작성 완료 후 호출
```

## 재처리 신호 처리

| 신호 출처 | 신호 유형 | Orchestrator 동작 |
|----------|----------|------------------|
| Aggregator | RE_SEARCH | Planner 재호출 (쿼리 재작성) → Retriever 재호출 |
| Supervisor | RETRY_RETRIEVAL | Retriever 재호출 |
| Supervisor | RETRY_WRITER | Writer 재호출 |

## Retry 관리

- Task Queue의 `retrieval_retry` 카운트를 Orchestrator가 관리
- Task Queue의 `supervisor_retry` 카운트를 Orchestrator가 관리
- 각 retry 카운트가 `max_retrieval_retry` / `max_supervisor_retry` 초과 시 실패 응답 반환

```python
# 실패 응답 형식
{
    "status": "failed",
    "reason": "max_retry_exceeded",  # 초과된 retry 유형 명시
    "partial_result": ...,           # 마지막 Aggregator 결과가 있으면 포함, 없으면 null
    "message": "검색 결과가 충분하지 않아 답변을 생성할 수 없습니다."
}
```

## 참고사항
- Orchestrator는 Task Queue를 직접 생성하지 않으며, Planner가 생성한 Task Queue를 수신하여 실행만 담당
- Task Queue의 상태(status) 업데이트는 Orchestrator가 수행
- 에이전트 간 직접 통신은 허용하지 않으며, 모든 라우팅은 Orchestrator를 경유
