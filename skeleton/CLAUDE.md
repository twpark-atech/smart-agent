# 프로젝트 개요
Document Parser Workflow 및 Retrieval Multi-Agent 개발

## 기술 스택
- python 3.10.12
- OpenSearch 3.6.0
- MinIO
- PostgreSQL

## LLM 모델
- url: 112.163.62.170:8012/v1
- api_key: 3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9
- OCR Model: DeepSeek-OCR
- LLM Model: Qwen2.5-3B-Instruct
- Multi-modal LLM Model: Qwen3-VL-32B-Instruct-AWQ

## Embedding 모델
- url: http://112.163.62.170:8032/v1
- Model: Qwen/Qwen3-Embedding-0.6B
- Dimension: 1024

## To-Do
- 각 단계에 해당하는 md 파일만을 참고
- /home/atech/Projects/smart-agent/skeleton/agents 참고
- parser: /home/atech/Projects/smart-agent/parser에 작성
- retriever: /home/atech/Projects/smart-agent/retriever에 작성
- 기존 Workflow 작업 단계가 있으면 그 다음 단계로 연계
- 추출하는 Workflow가 중단되어도 재개될 수 있는 형태로 개발

### Document Parser Workflow
1. format_converter.md: 포맷별 PDF로 변환
2. parser.md: 문서 추출
3. index_parser.md: 목차 추출
4. structurer.md: 목차별 구조화(원문 복원) 및 도메인 분류
5. section_parser.md: 목차별 명제 및 키워드 추출
6. document_integrator.md: 문서별 요약 및 키워드 추출

### Retrieval Multi-Agent
1. orchestrator.md: 전체 진행 관리 Agent
2. planner.md: 작업 계획 생성 및 검색 친화적 쿼리 변환 Agent
3. retriever.md: 검색 Agent
4. aggregator.md: 검색 결과 품질 검토 및 결과 병합 Agent
5. writer.md: 검색 내용 기반 작성 Agent
6. supervisor.md: 최종 산출물 품질 검증 Agent

### Tool List
| Tool | 담당 Agent | 설명 |
|------|-----------|------|
| opensearch_search | Retriever | OpenSearch Vector/BM25 검색 |
| postgres_search | Retriever | 정량적 자료 검색 (Text-to-SQL) |
| web_search | Retriever | 외부 웹 검색 |
| image_search | Retriever | 이미지 유사도 검색 |

### 에이전트별 LLM 모델
| Agent | 모델 | 이유 |
|-------|------|------|
| Orchestrator | Qwen2.5-3B-Instruct | 라우팅/상태 관리 단순 판단 |
| Planner | Qwen3-VL-32B-Instruct-AWQ | 복합 쿼리 해석 및 작업 분해 |
| Retriever | Qwen2.5-3B-Instruct | 검색 실행 (판단 최소화) |
| Aggregator | Qwen3-VL-32B-Instruct-AWQ | 관련성 판단 및 구조화 |
| Writer | Qwen2.5-3B-Instruct | 정형화된 구조 기반 작성 |
| Supervisor | Qwen3-VL-32B-Instruct-AWQ | 사실성·논리 검증 |