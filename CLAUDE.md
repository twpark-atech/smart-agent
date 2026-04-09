# 프로젝트 개요
문서 파서
- 문서 포맷별(PDF, DOCX, HWPX, PPTX, IMG 등), 문서 유형별(document_type.md), 도메인별(domain_type.md) 분류

## 기술 스택
- Python (3.10.12)
- OpenSearch
- PostgreSQL
- Neo4j

## LLM 모델
- url: 112.163.62.170:8012/v1
- api_key: 3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9
- OCR Model: DeepSeek-OCR
- LLM Model: Qwen2.5-3B-Instruct
- Multi-modal LLM Model: Qwen3-VL-32B-Instruct-AWQ

## 파싱 규칙
- Text는 문서 유형별 청킹 전략을 사용하여 유동적으로 청킹. Embedding 후 Vector DB + Graph DB에 적재
- Image
    - Image Description 내용을 원문에 추가. Embedding 후 Vector DB + Graph DB에 적재
    - VLM 활용 Image Embedding 후 Vector DB에 적재
    - 원본 데이터는 MinIO에 저장
- Table
    - Table Description 내용을 원문에 추가. Embdding 후 Vector DB + Graph DB에 적재
    - Table Raw 파싱 후 RDB에 적