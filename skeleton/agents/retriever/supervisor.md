# 역할
Writer가 작성한 최종 답변을 검증하고, 재처리가 필요한 경우 Orchestrator에 신호를 전달하는 Agent에 대한 코드를 작성한다.

## Supervisor의 역할
- 작성된 답변에 대한 사실성 검증
- 작성된 답변에 대한 논리 검증
- 작성된 답변에 대한 품질 평가
- 재처리 필요 시 Orchestrator에 신호 전달

## 검증 항목

| 항목 | 설명 |
|------|------|
| 사실성 | 답변 내용이 구조화된 검색 결과와 일치하는지 확인 |
| 논리성 | 답변의 흐름이 질의에 논리적으로 응답하는지 확인 |
| 품질 | 답변이 질의를 충분히 다루고 있는지 평가 |

## 처리 흐름

```
답변 검증
    ├─ 통과: 최종 답변 반환
    ├─ 데이터 부족: RETRY_RETRIEVAL 신호 → Orchestrator 전달
    └─ 답변 품질 저하: RETRY_WRITER 신호 → Orchestrator 전달
```

## Orchestrator에 전달하는 신호 형식

```python
# 재검색이 필요한 경우
{
    "signal": "RETRY_RETRIEVAL",
    "reason": str  # 예: "답변에 사용된 근거 데이터가 부족함"
}

# 재작성이 필요한 경우
{
    "signal": "RETRY_WRITER",
    "reason": str  # 예: "질의에 대한 직접적인 답변이 누락됨"
}
```

## 참고사항
- Supervisor는 Retriever나 Writer로 직접 요청하지 않으며, 반드시 Orchestrator를 경유
- Orchestrator가 supervisor_retry 카운트를 확인하여 재처리 여부를 결정
- supervisor_retry가 max_supervisor_retry 초과 시 Orchestrator가 마지막 답변을 실패 응답으로 반환
- 사용 모델: Qwen3-VL-32B-Instruct-AWQ (사실성·논리 검증은 고품질 추론 필요)
