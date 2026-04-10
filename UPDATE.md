# 작업 내용

## .xlsx, .csv 문서 추출 워크플로우

### 개요

```
[1] Input (.xlsx / .csv)
[2] Excel Parser (구조 + 스타일 + merge 추출)
[3] Region 생성 (merge-aware)
[4] Layout 보정 (셀 크기 자동 확장)
[5] HTML 변환 (colspan/rowspan 유지)
[6] 이미지 렌더링 (Playwright, 고해상도)
[7] VLM (Qwen) → Key-Value 관계 bbox 추출
[8] bbox → region 매핑
[9] region → 실제 값 매핑 (Excel 데이터 사용)
[10] JSON 생성
```

### 처리 단계별 상세
[1] Input 처리
    - .xlsx → openpyxl
    - .csv → pandas
    - 주의: CSV는 merge/스타일 없음 → 후처리 필요

[2] Excel 파싱
- 추출 데이터
```JSON
{
    "cell": "A1",
    "row": 1,
    "col": 1,
    "text": "홍길동",
    "style": {
        "font_size": 11,
        "bold": true,
        "bg_color": "#FFFFFF"
    }
}
```

- 추가로 반드시 추출
    - merged_cells.ranges
    - column width
    - row height

[3] Region 생성
- 규칙
    1. merge 영역 → 하나의 region
    2. 일반 셀 → 하나의 region

- 결과 구조
```JSON
{
    "region_id": "R1",
    "text": "홍길동",
    "cells", ["A1"],
    "bbox": null
}
```
or
```JSON
{
    "region_id": "R2",
    "text": "총 매출",
    "cells": ["A1", "A2", "B1", "B2"]
}
```

[4] Layout 보정
- 목적: 텍스트 잘림 방지 → OCR/VLM 인식률 확보
- 처리 로직
    1) 컬럼 width 자동 확장
        width = max(text length) * font_ratio
    2) 줄바꿈 강제
        white-spcae: pre-wrap
        word-break: break-word
    3) row height 증가
        줄 수 기반 height 계산
    4) 최소 폰트 보정
        min font-size: 14px

[5] HTML 변환
- 핵심 조건
```HTML
<table>
    <tr>
        <td colspan="2" rowspan="2">총 매출</td>
    </tr>
</table>
```

- 스타일 필수 적용
```CSS
td {
    padding: 8px;
    font-size: 16px;
    white-space: pre-wrap;
    word-break: break-word;
}
```

[6] 이미지 렌더링
- Playwright 기준
    - scale: 2~3
    - background: white
- 효과
    - OCR 정확도 ↑
    - bbox 정밀도 ↑

[7] VLM 역할
- 입력
    - 렌더링된 이미지
- 출력
```JSON
[
    {
        "key_bbox": [x1,y1,x2,y2],
        "value_bbox": [x1,y1,x2,y2],
    }
]
```
- 절대 텍스트 읽지 않게 제한

[8] bbox → region 매핑
- 알고리즘
```
for each bbox:
    region과 IoU 계산
    가장 overlap 높은 region 선택
```

또는

```
centroid distance 기반
```

[9] region → 값 매핑
```
region_id → Excel text
```
- 최종 KV
```JSON
{
    "이름": "홍길동",
    "주소": "서울시 강남구"
}
```

### 설계 포인트 정리

| 요소 | 역할 |
| --- | --- |
| Excel Parser | 정확한 값 |
| Region | 구조 추상화 |
| HTML | 레이아웃 유지 |
| Image | VLM 입력 |
| VLM | 관계 추론 |
| bbox 매핑 | 연결 핵심 |

### 실패 방지 체크리스트
- merge → region 변환했는가
- 텍스트 잘림 없는가
- font-size 충분한가 (>= 14px)
- 이미지 해상도 충분한가
- bbox 좌표계 일치하는가

## .pptx, 이미지 문서 추출 워크플로우

### 반영 사항

- document_type.md에서 11번 콘텐츠 항목 추가
- 이미지나 PPT처럼 렌더링되어 OCR이나 VLM으로 처리해야하는 내용에 한함
- XLSX나 CSV는 '테이블 기반 콘텐츠'가 아닌 '데이터/분석 문서'로 진행

### 처리 문서별 상세

1. 테이블 기반 콘텐츠
    1) Table Detection
        - 표 영역 탐지 (Bounding Box)
    2) OCR
        - 셀 단위 텍스트 추출
        - 좌표 확보
    3) Table Structure Recognition
        - row / column clustering
        - 셀 grouping
    4) VLM 보정
        - 병합 셀 추론
        - 헤더 구조 이해
    5) Table Reconstruction
        - grid 생성
        - merged cell 확장
    6) Key-Value 변환
    - 핵심 포인트
        - parser 없음
        - 구조 복원 문제

2. 레이아웃 기반 콘텐츠
    1) Layout Detection
        - 제목 / 본문 / 표 영역 분리
    2) OCR
        - 영역별 텍스트 추출
    3) VLM
        - 문단 구조 이해
        - 섹션 관계 파악
    4) LLM 구조화
    ```JSON
    {
        "제목": "...",
        "본문": "...",
        "요약": "..."
    }
    ```
    - 핵심 포인트
        - 구조는 있지만 Key-Value는 없음
        - 의미 기반 구조화

3. 자유 텍스트 콘텐츠
    1) OCR
        - 전체 텍스트 추출
    2) LLM
        - 요약 / 키워드 추출
    - 핵심 포인트
        - 구조화 최소화
        - 의미 추출 중심

4. 시각 중심 콘텐츠
    1) VLM
        - 객체 인식
        - 관계 추출
    2) OCR
        - 텍스트 라벨 추출
    3) Graph 구조 생성
    ```JSON
    {
        "nodes": [...],
        "edges": [...]
    }
    ```
    - 핵심 포인트
        - OCR 단독으로 해결 불가
        - VLM 중심