"""C2PA Content Credentials 서명 (A4) — 2026 업계표준(OpenAI/Google 채택).

생성 이미지에 C2PA 매니페스트(c2pa.created + trainedAlgorithmicMedia)를 서명·임베드.
서명 인증서는 self-hosted 2단 체인(자체 Root CA → end-entity). 외부 CA/API 의존 0.
[[feedback_no_external_api]]

기본 비활성(config ai_marking_c2pa=false). 인증서 미존재 시 자동 passthrough(무중단).
인증서 생성: scripts/gen_c2pa_cert.sh

⚠️ self-signed 체인이라 판독기는 'untrusted'로 표시(트러스트 리스트 미등록) — 정상.
   매니페스트 존재·서명·변조탐지는 유효. 트러스트 체인은 별개 문제.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import warnings

logger = logging.getLogger("ollama-admin")

# IPTC digital source type — 학습 알고리즘 생성물(생성형 AI)
DST_TRAINED = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"

_signer = None
_signer_key = None          # (cert_path, key_path) 캐시 무효화용
_lock = threading.Lock()    # c2pa 네이티브 호출 직렬화(스레드 안전)
_settings_loaded = False


def is_c2pa_installed() -> bool:
    try:
        import c2pa  # noqa: F401
        return True
    except Exception:
        return False


def available(cert_path: str, key_path: str) -> bool:
    """c2pa 설치 + 인증서/키 파일 존재 여부."""
    return bool(cert_path and key_path
                and is_c2pa_installed()
                and os.path.exists(cert_path) and os.path.exists(key_path))


def _ensure_settings():
    global _settings_loaded
    if _settings_loaded:
        return
    import c2pa
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            # self-signed 체인 서명 후 트러스트 검증 실패로 막히지 않도록
            c2pa.load_settings('{"verify":{"verify_after_sign":false}}')
        except Exception as e:
            logger.debug("c2pa load_settings: %s", e)
    _settings_loaded = True


def _get_signer(cert_path: str, key_path: str):
    global _signer, _signer_key
    if _signer is not None and _signer_key == (cert_path, key_path):
        return _signer
    import c2pa
    with open(cert_path, "rb") as f:
        cert = f.read()
    with open(key_path, "rb") as f:
        key = f.read()
    info = c2pa.C2paSignerInfo(alg=b"es256", sign_cert=cert, private_key=key, ta_url=b"x")
    info.ta_url = None  # NULL = TSA(타임스탬프) 미사용
    _signer = c2pa.Signer.from_info(info)
    _signer_key = (cert_path, key_path)
    return _signer


def sign_image_bytes(data: bytes, *, cert_path: str, key_path: str,
                     generator: str = "WeRU.B AI", model: str | None = None,
                     version: str = "", title: str = "AI-generated image") -> bytes:
    """PNG 바이트에 C2PA 매니페스트 서명·임베드. 실패/미가용 시 원본 반환.

    입력은 PNG 가정(우리 마킹 파이프라인은 PNG로 정규화됨).
    """
    if not data:
        return data
    if not available(cert_path, key_path):
        return data
    try:
        import c2pa
        _ensure_settings()
        cg = f"WeRU.B-AI/{version}" if version else "WeRU.B-AI"
        manifest = {
            "claim_generator": cg,
            "title": title,
            "assertions": [
                {"label": "c2pa.actions",
                 "data": {"actions": [{"action": "c2pa.created",
                                       "digitalSourceType": DST_TRAINED}]}},
            ],
        }
        if model:
            manifest["assertions"].append(
                {"label": "com.werub.generation",
                 "data": {"generator": str(generator), "model": str(model)}})
        with _lock:
            signer = _get_signer(cert_path, key_path)
            builder = c2pa.Builder(manifest)
            dest = io.BytesIO()
            builder.sign(signer, "image/png", io.BytesIO(data), dest)
            out = dest.getvalue()
        return out if out else data
    except Exception as e:
        logger.warning("c2pa sign failed (passthrough): %s", e)
        return data


def read_manifest(data: bytes, mime: str = "image/png") -> dict | None:
    """이미지에서 C2PA 매니페스트 판독. 없으면 None."""
    if not data or not is_c2pa_installed():
        return None
    try:
        import c2pa
        _ensure_settings()
        reader = c2pa.Reader(mime, io.BytesIO(data))
        return json.loads(reader.json())
    except Exception:
        return None
