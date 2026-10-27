"""S3-compatible Storage — 이미지/파일 업로드 + Presigned URL + 관리

2026-09-09: Wasabi 유료 해제로 사내 MinIO(werub-minio)로 전환. boto3 S3 API 그대로.

config.json 설정:
{
    "s3_enabled": true,
    "s3_endpoint": "http://127.0.0.1:9000",          # 내부 호출용(업로드/목록/삭제)
    "s3_public_endpoint": "https://example.org",       # presigned URL 서명 호스트(외부 도달용)
    "s3_access_key": "...",
    "s3_secret_key": "...",
    "s3_bucket": "werub-ai-images",
    "s3_region": "ap-northeast-1",
    "s3_prefix": "images/",
    "s3_presigned_ttl": 3600,
    "s3_addressing_style": "path"                     # MinIO는 path 필수(가상호스트=DNS 없음)
}

🔑 s3_public_endpoint가 필요한 이유: MinIO는 127.0.0.1:9000 바인딩이라 그 주소로 서명한
presigned URL은 서버 밖에서 열리지 않는다. SigV4는 Host 헤더를 서명에 포함하므로 **외부에서
접속할 호스트로 서명해야** 한다(엣지가 Host를 보존한 채 MinIO로 프록시하면 검증 통과).
미설정 시 종전과 동일하게 s3_endpoint로 서명한다(내부 전용).
"""
import io
import logging
import os
from datetime import datetime

logger = logging.getLogger("ollama-admin")

_client = None
_signer = None   # presigned URL 전용(공개 엔드포인트로 서명). 미설정 시 _client와 동일
_config = {}


def init_s3(config: dict):
    """S3 클라이언트 초기화. config.json에서 호출."""
    global _client, _signer, _config
    _client = None
    _signer = None
    if not config.get("s3_enabled"):
        logger.info("S3 storage disabled")
        return False

    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        logger.warning("S3 storage: boto3 not installed — pip install boto3")
        return False

    _config = {
        "endpoint": config.get("s3_endpoint", "https://s3.ap-northeast-2.wasabisys.com"),
        "access_key": config.get("s3_access_key", ""),
        "secret_key": config.get("s3_secret_key", ""),
        "bucket": config.get("s3_bucket", "werub-ai-images"),
        "region": config.get("s3_region", "ap-northeast-2"),
        "prefix": config.get("s3_prefix", "generated/"),
        "presigned_ttl": config.get("s3_presigned_ttl", 3600),
        # 공개 엔드포인트(외부 도달용). 빈 값이면 내부 엔드포인트로 서명(종전 동작).
        "public_endpoint": (config.get("s3_public_endpoint") or "").rstrip("/"),
        # MinIO는 가상호스트 스타일을 쓸 수 없다(버킷.host DNS 부재) → path 기본값.
        "addressing_style": config.get("s3_addressing_style", "path"),
    }

    if not _config["access_key"] or not _config["secret_key"]:
        logger.warning("S3 storage: access_key/secret_key not configured")
        return False

    def _mk(endpoint):
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=_config["access_key"],
            aws_secret_access_key=_config["secret_key"],
            region_name=_config["region"],
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": _config["addressing_style"]},
            ),
        )

    _client = _mk(_config["endpoint"])
    # presigned URL은 외부가 여는 주소로 서명해야 한다(SigV4가 Host를 서명에 포함).
    _signer = _mk(_config["public_endpoint"]) if _config["public_endpoint"] else _client
    logger.info("S3 storage initialized — bucket=%s endpoint=%s public=%s style=%s",
                _config["bucket"], _config["endpoint"],
                _config["public_endpoint"] or "(=endpoint)", _config["addressing_style"])
    return True


def is_enabled():
    return _client is not None


def upload_bytes(data: bytes, filename: str, content_type: str = "image/png",
                 metadata: dict = None) -> dict:
    """바이트 데이터를 S3에 업로드. Presigned URL 반환."""
    if not _client:
        return {"error": "S3 not configured"}

    key = _config["prefix"] + filename
    extra = {"ContentType": content_type}
    if metadata:
        extra["Metadata"] = {k: str(v) for k, v in metadata.items()}

    try:
        _client.put_object(
            Bucket=_config["bucket"],
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata=metadata or {},
        )
        url = get_presigned_url(key)
        logger.info("S3 uploaded: %s (%d bytes)", key, len(data))
        return {
            "key": key,
            "bucket": _config["bucket"],
            "url": url,
            "size": len(data),
            "content_type": content_type,
        }
    except Exception as e:
        logger.error("S3 upload error: %s", e)
        return {"error": str(e)}


def upload_file(file_path: str, filename: str = None,
                content_type: str = "image/png", metadata: dict = None) -> dict:
    """로컬 파일을 S3에 업로드."""
    if not os.path.exists(file_path):
        return {"error": f"file not found: {file_path}"}

    if not filename:
        filename = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        return upload_bytes(f.read(), filename, content_type, metadata)


def key_allowed(key: str) -> bool:
    """사용자 제공 key가 구성된 prefix 범위 내인지 검증(버킷 임의객체 presign/삭제 차단).

    보안검토 M8: /api/image/s3/url·/delete가 user key를 verbatim 전달하던 갭 봉합.
    내부 호출(upload/list)은 항상 prefix 하위 key라 영향 없음.
    """
    pfx = _config.get("prefix") or ""
    return bool(key) and key.startswith(pfx)


def get_presigned_url(key: str, ttl: int = None) -> str:
    """S3 객체의 Presigned URL 생성(공개 엔드포인트 설정 시 그 호스트로 서명)."""
    if not _signer:
        return ""
    try:
        return _signer.generate_presigned_url(
            "get_object",
            Params={"Bucket": _config["bucket"], "Key": key},
            ExpiresIn=ttl or _config["presigned_ttl"],
        )
    except Exception as e:
        logger.error("S3 presigned URL error: %s", e)
        return ""


def list_objects_ex(prefix: str = None, max_keys: int = 100) -> dict:
    """객체 목록 + 오류를 함께 반환 — {"items": [...], "error": str|None}.

    🔑 `list_objects()`는 실패해도 빈 리스트를 주므로 호출자가 "0건"과 "조회 실패"를 구분할 수
    없다(200+빈 결과=무성 실패 클래스). 사용자에게 결과를 보이는 경로는 이 함수를 쓴다.
    폴백 경로(`/api/image/view` → 디스크)는 종전대로 `list_objects()`를 쓴다.
    """
    if not _client:
        return {"items": [], "error": "S3 not configured"}
    try:
        resp = _client.list_objects_v2(
            Bucket=_config["bucket"],
            Prefix=prefix or _config["prefix"],
            MaxKeys=max_keys,
        )
        items = []
        for obj in resp.get("Contents", []):
            items.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
                "url": get_presigned_url(obj["Key"]),
            })
        return {"items": items, "error": None}
    except Exception as e:
        logger.error("S3 list error: %s", e)
        return {"items": [], "error": str(e)}


def object_exists(key: str):
    """객체 존재 여부 — True/False, 저장소 오류면 None(v2.125.70).

    `/api/image/s3/url`이 존재 확인 없이 presign해 **200 + 열면 NoSuchKey인 URL**을 주던 것의 처치에 쓴다
    (2026-10-07 실측: Wasabi 폐지로 사라진 06-30 키를 node 클라이언트가 9회 받아 갔다).
    """
    if not _client:
        return None
    try:
        _client.head_object(Bucket=_config["bucket"], Key=key)
        return True
    except Exception as e:  # noqa: BLE001 — botocore ClientError를 import 없이 판별
        code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        logger.error("S3 head error: %s", e)
        return None


def find_image_key(filename: str, prefix: str = None, max_pages: int = 10):
    """업로드 규칙(`{prefix}{YYYYmmdd_HHMMSS}_{filename}`)으로 올라간 키 중 **파일명이 정확히 같은** 최신 키.

    반환 (key|None, error|None). 🔴 종전 `/api/image/view`는 `list_objects(max_keys=200)`에서
    `filename in key` **부분 일치**로 찾았다 — ①ComfyUI 파일 카운터가 재설치 때 `00001`부터 다시 시작해
    같은 파일명이 다른 날짜 키로 재등장하면 **목록상 먼저 나온 엉뚱한 이미지**로 리다이렉트되고
    ②`_00001_.png`가 `_000010_.png` 같은 다른 이름에도 걸리며 ③200개를 넘으면 오래된 이미지를 못 찾았다.
    페이지당 1000키 × max_pages(기본 10 = 보관 상한 image_max_count 5000의 2배)까지 훑는다.
    """
    if not _client:
        return None, "S3 not configured"
    pfx = prefix if prefix is not None else _config["prefix"]
    want = "_" + filename
    best = None
    token = None
    try:
        for _ in range(max_pages):
            kw = {"Bucket": _config["bucket"], "Prefix": pfx, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            resp = _client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                base = k[len(pfx):] if k.startswith(pfx) else k.rsplit("/", 1)[-1]
                # 정확 일치: "{15자 타임스탬프}_{filename}" 또는 타임스탬프 없는 동일 이름
                if base == filename or (base.endswith(want) and len(base) == 15 + len(want)):
                    if best is None or obj["LastModified"] > best["LastModified"]:
                        best = obj
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return (best["Key"] if best else None), None
    except Exception as e:  # noqa: BLE001
        logger.error("S3 find error: %s", e)
        return None, str(e)


def list_objects(prefix: str = None, max_keys: int = 100) -> list:
    """S3 버킷의 객체 목록(실패 시 빈 리스트 — 폴백 호출자 전용)."""
    if not _client:
        return []
    try:
        resp = _client.list_objects_v2(
            Bucket=_config["bucket"],
            Prefix=prefix or _config["prefix"],
            MaxKeys=max_keys,
        )
        items = []
        for obj in resp.get("Contents", []):
            items.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
                "url": get_presigned_url(obj["Key"]),
            })
        return items
    except Exception as e:
        logger.error("S3 list error: %s", e)
        return []


def delete_object(key: str) -> bool:
    """S3 객체 삭제."""
    if not _client:
        return False
    try:
        _client.delete_object(Bucket=_config["bucket"], Key=key)
        logger.info("S3 deleted: %s", key)
        return True
    except Exception as e:
        logger.error("S3 delete error: %s", e)
        return False


def get_status() -> dict:
    """S3 스토리지 상태."""
    if not _client:
        return {"enabled": False, "reason": "not configured"}
    try:
        resp = _client.list_objects_v2(
            Bucket=_config["bucket"],
            Prefix=_config["prefix"],
            MaxKeys=1,
        )
        count = resp.get("KeyCount", 0)
        return {
            "enabled": True,
            "bucket": _config["bucket"],
            "endpoint": _config["endpoint"],
            "public_endpoint": _config.get("public_endpoint") or None,
            "addressing_style": _config.get("addressing_style"),
            "prefix": _config["prefix"],
            "presigned_ttl": _config["presigned_ttl"],
            "accessible": True,
            "object_count_sample": count,
        }
    except Exception as e:
        return {"enabled": True, "accessible": False, "error": str(e)}
