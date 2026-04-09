"""단독 이미지 파싱 - VLM description + MinIO 적재"""
from pathlib import Path

from .models import Block
from .llm import describe_image
from .minio_helper import upload_image


def parse(image_path: str) -> list[Block]:
    """단독 이미지 파일을 MinIO에 적재하고 VLM description을 생성."""
    minio_key = upload_image(image_path, source_pdf=image_path, page=0)
    description = describe_image(image_path)

    return [Block(
        block_type="image",
        content=f"[이미지 설명]\n{description}",
        page=0,
        bbox=None,
        minio_key=minio_key,
    )]
