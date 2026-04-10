"""
LLM 기반 파싱 결과 전처리

1단계: 규칙 기반 전처리 (반복 헤더, HTML 엔티티, 연속 이미지 마커 등)
2단계: LLM 기반 교정 (한국어 오타, 띄어쓰기, OCR 깨진 텍스트)
"""

import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ── 설정 ──
_ROOT = Path(__file__).parent.parent
PARSED_MD_PATH = str(_ROOT / "tests" / "parsed_output.md")
OUTPUT_PATH = str(_ROOT / "tests" / "parsed_output_corrected.md")
LLM_URL = "http://112.163.62.170:8012/v1"
LLM_API_KEY = "3c035ed9e73c0453a0b0dabe54823ca095857f77a87dfea5b5f50013f18320d9"
LLM_MODEL = "Qwen3-VL-32B-Instruct-AWQ"
CHUNK_SIZE = 2000  # 청크당 대략 글자 수
MAX_WORKERS = 10   # 병렬 처리 워커 수

llm_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)


# ── 1단계: 규칙 기반 전처리 ──
def rule_based_preprocess(text: str) -> str:
    # 반복 페이지 헤더 제거
    text = re.sub(
        r'## l l 하이테크 섬유소재 핵심인력 양성사업 \(DYETEC\) l l\n*',
        '', text
    )

    # HTML 엔티티 변환
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&amp;', '&')

    # 연속 이미지 마커 → 단일로
    text = re.sub(r'(<!-- image -->\n*){2,}', '<!-- image -->\n\n', text)

    # 구두점 앞 불필요한 공백 제거 (한국어 문장)
    text = re.sub(r'\s+\.(\s)', r'.\1', text)
    text = re.sub(r'\s+,(\s)', r',\1', text)

    # 3줄 이상 연속 빈 줄 → 2줄로
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    return text


# ── 2단계: 텍스트를 섹션 단위로 청킹 ──
def split_into_chunks(text: str) -> list[str]:
    """마크다운 헤딩(##) 기준으로 섹션을 나누되, CHUNK_SIZE 이내로 유지"""
    sections = re.split(r'(^## .+$)', text, flags=re.MULTILINE)

    chunks = []
    current = ""

    for part in sections:
        if len(current) + len(part) > CHUNK_SIZE and current:
            chunks.append(current)
            current = part
        else:
            current += part

    if current.strip():
        chunks.append(current)

    return chunks


# ── 3단계: LLM 교정 ──
SYSTEM_PROMPT = """OCR 파싱된 한국어 기술 문서의 오타와 띄어쓰기만 최소한으로 교정하세요.

[필수 규칙]
- 오타만 수정 (예: 수지가공체→수지가공제, 딸서→따라서, 널이→널리, 침도도→침투도)
- 불필요한 공백만 제거 (예: "과정이 다 ." → "과정이다.")
- OCR로 인한 깨진 텍스트(의미 없는 영문/한자 조합)는 제거
- 문장 구조, 어미, 문체는 절대 변경 금지
- 내용 추가/삭제 금지
- 마크다운 기호(##, |, -, <!-- -->)는 그대로 유지
- 전문 용어(화학명, 영문 약어 등)는 변경 금지
- 교정된 텍스트만 출력. 설명 금지"""


def correct_chunk(chunk: str, index: int, total: int) -> str:
    """LLM으로 청크 교정"""
    # 빈 청크나 이미지/표만 있는 청크는 스킵
    text_only = re.sub(r'<!-- image -->', '', chunk)
    text_only = re.sub(r'\|.*\|', '', text_only)
    text_only = re.sub(r'#+ .*', '', text_only)
    if len(text_only.strip()) < 30:
        return chunk

    try:
        resp = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": chunk},
            ],
            max_tokens=4096,
            temperature=0.0,
        )
        corrected = resp.choices[0].message.content
        print(f"  [{index+1}/{total}] 교정 완료 ({len(chunk)}자 → {len(corrected)}자)", flush=True)
        return corrected
    except Exception as e:
        print(f"  [{index+1}/{total}] 교정 실패: {e}", flush=True)
        return chunk


def main():
    print("=" * 60)
    print("LLM 기반 파싱 결과 전처리")
    print("=" * 60)

    # 원본 읽기
    raw = Path(PARSED_MD_PATH).read_text(encoding="utf-8")
    print(f"\n원본: {len(raw):,}자, {raw.count(chr(10)):,}줄")

    # 1단계: 규칙 기반 전처리
    print("\n[1단계] 규칙 기반 전처리...")
    preprocessed = rule_based_preprocess(raw)
    print(f"  규칙 전처리 후: {len(preprocessed):,}자 ({len(raw) - len(preprocessed):,}자 감소)")

    # 2단계: 청킹
    print("\n[2단계] 섹션 단위 청킹...")
    chunks = split_into_chunks(preprocessed)
    print(f"  총 {len(chunks)}개 청크 (평균 {len(preprocessed) // len(chunks):,}자)")

    # 3단계: LLM 병렬 교정
    print(f"\n[3단계] LLM 병렬 교정 ({LLM_MODEL}, workers={MAX_WORKERS})...")
    total = len(chunks)
    corrected_chunks = [None] * total
    done_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(correct_chunk, chunk, i, total): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            idx = futures[future]
            corrected_chunks[idx] = future.result()
            done_count += 1
            elapsed = time.time() - start_time
            avg = elapsed / done_count
            remaining = avg * (total - done_count)
            print(f"  진행: {done_count}/{total} ({done_count/total*100:.0f}%) "
                  f"| 경과: {elapsed:.0f}s | 예상 잔여: {remaining:.0f}s", flush=True)

    # 결과 조합 및 저장
    result = "\n".join(corrected_chunks)
    Path(OUTPUT_PATH).write_text(result, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"완료!")
    print(f"  원본: {len(raw):,}자")
    print(f"  교정 후: {len(result):,}자")
    print(f"  저장: {OUTPUT_PATH}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
