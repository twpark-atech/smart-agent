# 역할
포맷을 확인하고 포맷별로 텍스트, 이미지, 표를 추출하는 코드를 작성한다.

## 처리 가능 포맷
- .pdf (텍스트 기반, 이미지 기반)
- .pptx
- .md
- .csv
- .xlsx
- 이미지 (jpg, png 등)

## PDF 분기
- PDF는 텍스트 기반과 이미지 기반으로 분류됨
- 텍스트 기반 PDF는 pypdfium2, PyPDF2 라이브러리를 활용하여 추출
- 이미지 기반은 OCR 또는 VLM Model을 활용하여 추출

## 추출시 참고사항
- 최종 문서 검색 과정에서 증거용으로 bbox가 필요함
- 문서 추출 과정에서 bbox를 함께 추출해야 함 (PDF, PPTX 등)
- 이미지의 경우 원본 이미지는 MinIO에 함께 저장
- 이미지의 경우 OCR 또는 VLM Model을 활용하여 Image Description 작성 후 원본 이미지 자리에 삽입
- 표의 경우 Table Transformer를 활용하여 표 구조를 추출
- 표 구조를 복원하여 LLM 기반 Table Description 작성 후 원본 테이블 자리에 삽입
- 표 구조를 복원하여 json 형태로 재구조화하여 PostgreSQL에 적재