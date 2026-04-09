"""OCR / VLM / LLM 클라이언트"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from config import LLM_URL, LLM_API_KEY, LLM_MODEL, OCR_MODEL, VLM_MODEL

_client = OpenAI(base_url=LLM_URL, api_key=LLM_API_KEY)


def _chat(model: str, messages: list[dict], max_tokens: int = 1024) -> str:
    resp = _client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return resp.choices[0].message.content.strip()


def _encode_image(image_path: str | Path) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ── OCR ───────────────────────────────────────────────────

def ocr_image(image_path: str | Path) -> str:
    """이미지에서 텍스트를 OCR로 추출."""
    b64 = _encode_image(image_path)
    ext = Path(image_path).suffix.lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    return _chat(
        model=OCR_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "이 이미지의 텍스트를 그대로 추출하세요."},
                ],
            }
        ],
        max_tokens=2048,
    )


# ── VLM ───────────────────────────────────────────────────

def describe_image(image_path: str | Path) -> str:
    """이미지 내용을 VLM으로 설명."""
    b64 = _encode_image(image_path)
    ext = Path(image_path).suffix.lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    return _chat(
        model=VLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {
                        "type": "text",
                        "text": (
                            "이 이미지를 상세히 설명하세요. "
                            "차트·그래프라면 수치와 추세를, 다이어그램이라면 구조와 흐름을, "
                            "사진이라면 피사체와 맥락을 포함하세요. 한국어로 답하세요."
                        ),
                    },
                ],
            }
        ],
        max_tokens=1024,
    )


def extract_page_with_vlm(image_path: str | Path) -> str:
    """이미지 기반 PDF 페이지 전체를 VLM으로 파싱 (텍스트 + 구조 복원)."""
    b64 = _encode_image(image_path)
    ext = Path(image_path).suffix.lstrip(".").lower()
    mime = f"image/{ext}"

    return _chat(
        model=VLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {
                        "type": "text",
                        "text": (
                            "이 문서 페이지의 모든 내용을 추출하세요. "
                            "제목, 본문, 표, 이미지 설명을 포함하여 "
                            "문서 구조를 마크다운 형식으로 복원하세요."
                        ),
                    },
                ],
            }
        ],
        max_tokens=4096,
    )


# ── LLM ───────────────────────────────────────────────────

def describe_table(table_text: str) -> str:
    """표 원문(마크다운 또는 CSV 형태)을 LLM으로 설명."""
    return _chat(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "문서 분석 전문가입니다. 표의 내용을 2~4문장으로 요약 설명하세요. 설명문만 출력하세요.",
            },
            {
                "role": "user",
                "content": f"다음 표를 설명하세요:\n\n{table_text}",
            },
        ],
        max_tokens=300,
    )
