"""MinIO 업로드 모듈"""
import hashlib
from datetime import datetime
from pathlib import Path

from minio import Minio
from minio.error import S3Error

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    MINIO_ENDPOINT,
    MINIO_ACCESS_KEY,
    MINIO_SECRET_KEY,
    MINIO_BUCKET,
    MINIO_SECURE,
)


def _get_client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _object_key(file_path: Path, prefix: str = "") -> str:
    """MinIO 오브젝트 키 생성.
    형식: {prefix}/{YYYYMMDD}/{stem}_{hash8}{suffix}
    """
    date_str = datetime.utcnow().strftime("%Y%m%d")
    file_hash = hashlib.md5(file_path.name.encode()).hexdigest()[:8]
    name = f"{file_path.stem}_{file_hash}{file_path.suffix}"
    parts = [p for p in [prefix, date_str, name] if p]
    return "/".join(parts)


def upload(
    file_path: str | Path,
    prefix: str = "documents",
    bucket: str | None = None,
) -> dict:
    """파일을 MinIO에 업로드.

    Args:
        file_path: 업로드할 파일 경로
        prefix: 오브젝트 키 접두어 (폴더 역할)
        bucket: 버킷명. None이면 config의 MINIO_BUCKET 사용.

    Returns:
        {
            "bucket": str,
            "object_key": str,
            "size": int,
            "etag": str,
        }
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    target_bucket = bucket or MINIO_BUCKET
    client = _get_client()
    _ensure_bucket(client, target_bucket)

    object_key = _object_key(path, prefix)

    result = client.fput_object(
        bucket_name=target_bucket,
        object_name=object_key,
        file_path=str(path),
    )

    return {
        "bucket": target_bucket,
        "object_key": object_key,
        "size": path.stat().st_size,
        "etag": result.etag,
    }
