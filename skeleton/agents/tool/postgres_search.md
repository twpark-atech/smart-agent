# postgres_search

Planner가 생성한 SQL 쿼리를 PostgreSQL에서 실행하여 정량적 자료를 검색하는 Tool.

## 사용 Agent
- Retriever

## 입력 파라미터

```python
{
    "sql_query": str,       # Planner가 Text-to-SQL로 생성한 SQL 쿼리
    "params": list | None   # 바인딩 파라미터 (SQL Injection 방지용, 없으면 None)
}
```

## 처리 흐름

```
1. sql_query 유효성 검사 (SELECT만 허용, DDL/DML 차단)
2. PostgreSQL 연결 및 쿼리 실행
3. 결과 반환
```

## 출력 형식

```python
{
    "status": "success" | "error",
    "columns": list[str],   # 컬럼명 목록
    "rows": list[dict],     # 각 row를 {컬럼명: 값} 형태로 반환
    "row_count": int,       # 반환된 row 수
    "source": {
        "table": str,       # 조회된 테이블명 (단일 테이블인 경우)
        "document_id": str | None  # 해당 테이블이 특정 문서에서 파싱된 경우 문서 ID
    },
    "error": str | None     # 오류 발생 시 메시지, 정상이면 None
}
```

## 참고사항
- SELECT 쿼리만 허용하며, INSERT/UPDATE/DELETE/DROP 등 데이터 변경 쿼리는 차단
- 쿼리 실행 실패 시 status: "error"와 error 메시지 반환 (예외를 상위로 전파하지 않음)
- row_count가 0인 경우 빈 rows 반환 (정상 케이스)
- 문서 파싱 단계(structurer)에서 테이블 데이터가 PostgreSQL에 적재된 구조를 전제로 함
