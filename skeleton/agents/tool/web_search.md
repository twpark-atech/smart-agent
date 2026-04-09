# web_search

Serper API를 활용하여 외부 웹에서 정보를 검색하는 Tool. 내부 문서에 없는 최신 정보나 외부 지식이 필요한 경우에 사용.

## 사용 Agent
- Retriever (task_type이 "web" 또는 "mixed"인 경우에만 호출)

## 호출 조건
- Planner가 task_type을 "web" 또는 "mixed"로 판단한 경우에만 Retriever가 호출
- Retriever가 자체적으로 웹 검색 필요 여부를 판단하지 않음

## Serper API 정보
- URL: https://serper.dev
- API Key: d41c3ccde018940a69f22f46f12b4368a605cfe7

## 입력 파라미터

```python
{
    "query": str,       # 검색 쿼리 (Planner의 keyword_query 사용)
    "top_k": int        # 반환할 최대 결과 수 (기본값: 5)
}
```

## 처리 흐름

```
1. Serper API 엔드포인트에 POST 요청
   - Header: { "X-API-KEY": API_KEY, "Content-Type": "application/json" }
   - Body: { "q": query, "num": top_k }
2. 응답에서 organic 결과 파싱
3. top_k 개 반환
```

## 출력 형식

```python
[
    {
        "title": str,       # 검색 결과 제목
        "url": str,         # 출처 URL
        "snippet": str,     # 본문 요약 (Serper 제공 snippet)
        "source": {
            "url": str,
            "accessed_at": str  # 검색 시각 (ISO 8601)
        }
    }
]
```

## 참고사항
- 웹 검색 결과는 내부 문서 검색 결과보다 신뢰도가 낮을 수 있으므로, Aggregator가 관련성 판단 시 출처 유형(내부/외부)을 구분하여 평가
- 결과가 0건인 경우 빈 리스트 반환
- mixed 유형의 경우 opensearch_search와 병렬 실행
- Serper API 오류(4xx/5xx) 발생 시 빈 리스트 반환 후 오류 로그 기록
