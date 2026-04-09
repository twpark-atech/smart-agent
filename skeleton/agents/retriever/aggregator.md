# 역할
검색 결과의 품질을 검토하고, 사용 가능한 내용을 병합·구조화하는 Agent에 대한 코드를 작성한다.
품질 미달 시 쿼리 재작성 없이 Orchestrator에 재검색 신호를 전달한다.

## Aggregator의 역할
- 검색 결과 품질 검토
- 사용할 내용 선별 및 병합
- 병합된 내용 구조화
- 품질 미달 시 Orchestrator에 RE_SEARCH 신호 전달

## 품질 검토 기준
- 사용자 질의와 검색 결과의 관련성 판단 (LLM 자체 판단)
- 관련 내용의 충분성 판단 (결과가 질의에 답하기에 충분한지)
- 중복 내용 제거 및 모순 내용 식별

## 처리 흐름

```
검색 결과 수신
    ↓
품질 검토
    ├─ 충분: 병합 → 구조화 → Writer로 전달
    └─ 미달: RE_SEARCH 신호 → Orchestrator 전달
               (쿼리 재작성은 Planner가 담당)
```

## RE_SEARCH 신호 형식

```python
{
    "signal": "RE_SEARCH",
    "reason": str,  # 재검색 이유 (예: "관련 결과 부족", "질의와 관련성 낮음")
}
```

## 구조화 출력 형식

```python
{
    "status": "success",
    "structured_content": [
        {
            "section": str,       # 구조화된 섹션 제목
            "content": str,       # 해당 섹션 내용
            "sources": list[str]  # 출처 목록
        }
    ]
}
```

## 참고사항
- 쿼리 재작성은 Aggregator의 역할이 아니며, RE_SEARCH 신호만 Orchestrator에 전달
- Orchestrator가 retrieval_retry 한도를 확인하여 Planner 재호출 여부를 결정
- 구조화된 내용에는 반드시 출처를 포함하여 Writer가 인용할 수 있도록 함
