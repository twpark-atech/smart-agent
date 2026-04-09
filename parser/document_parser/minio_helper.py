"""파서 내부용 MinIO 이미지 업로드 헬퍼"""
import hashlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from minio import Minio
from config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET, MINIO_SECURE


def _client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def upload_image(image_path: str, source_pdf: str, page: int) -> str:
    """이미지를 MinIO에 업로드하고 오브젝트 키를 반환.

    키 형식: images/{YYYYMMDD}/{source_stem}_p{page}_{hash8}.{ext}
    """
    path = Path(image_path)
    date_str = datetime.utcnow().strftime("%Y%m%d")
    file_hash = hashlib.md5(path.read_bytes()).hexdigest()[:8]
    source_stem = Path(source_pdf).stem
    object_key = f"images/{date_str}/{source_stem}_p{page}_{file_hash}{path.suffix}"

    client = _client()
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)

    client.fput_object(
        bucket_name=MINIO_BUCKET,
        object_name=object_key,
        file_path=str(path),
    )
    return object_key
