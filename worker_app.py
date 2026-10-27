"""iMac M1 Apple Silicon Worker — :8587 경량 Flask.

[apple-silicon-worker Phase 1] 특화 워크로드 전담: AI 판별(MPS) + 마킹/C2PA.
설계: docs/02-design/features/apple-silicon-worker.design.md

활용 2모드 (plan §3-1):
  A. 위임  — GPU 서버가 SSH 터널로 호출, X-Worker-Token 인증 (기본 ON)
  B. 독립  — 외부 클라이언트 직접 호출, Bearer API 키 (WORKER_EXPOSE로 opt-in, 기본 OFF)

실행:
  WORKER_TOKEN=... venv-mac/bin/gunicorn -c gunicorn_worker.conf.py worker_app:app
환경변수:
  WORKER_TOKEN     위임 모드 공유 시크릿 (필수)
  WORKER_EXPOSE    off(기본) | lan | tunnel — 독립 모드 opt-in
  DETECTOR_DEVICE  mps(기본) | cpu
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import base64
import binascii
import hmac
import logging
import os
import platform
from functools import wraps

from flask import Flask, jsonify, request

from core import ai_image_detector, content_marking, worker_guard
from core import c2pa_sign, mlx_stt

VERSION = "2.84.0"
DETECTOR_DEVICE = os.environ.get("DETECTOR_DEVICE", "mps")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")
WORKER_EXPOSE = os.environ.get("WORKER_EXPOSE", "off")  # off | lan | tunnel
C2PA_CERT = os.environ.get("C2PA_CERT", os.path.join(os.path.dirname(__file__), "config", "c2pa", "cert.pem"))
C2PA_KEY = os.environ.get("C2PA_KEY", os.path.join(os.path.dirname(__file__), "config", "c2pa", "key.pem"))
MAX_IMAGE_MB = 10
MAX_AUDIO_MB = 200

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] worker: %(message)s")
logger = logging.getLogger("worker")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = max(MAX_IMAGE_MB * 2, MAX_AUDIO_MB) * 1024 * 1024

CAPABILITIES = ["detect", "mark", "stt"]  # Phase 3: +embed

# 역방향 터널(-L 11435 → GPU :8585) 자가 점검 — 30s 캐시 (imac-link-console Phase A)
_link_cache = {"ts": 0.0, "ok": None}
_REVERSE_TUNNEL_PORT = int(os.environ.get("REVERSE_TUNNEL_PORT", "11435"))


def _check_reverse_tunnel() -> bool | None:
    """iMac→GPU 채널 생존 — GPU /api/health를 localhost:11435으로 1s ping."""
    import time as _t
    if _t.time() - _link_cache["ts"] < 30:
        return _link_cache["ok"]
    ok = None
    try:
        import urllib.request
        r = urllib.request.urlopen(f"http://127.0.0.1:{_REVERSE_TUNNEL_PORT}/api/health", timeout=1)
        ok = r.status in (200, 503)  # 503=GPU unhealthy여도 채널 자체는 생존
    except Exception:
        ok = False
    _link_cache.update({"ts": _t.time(), "ok": ok})
    return ok


def _verify_bearer_key(key: str) -> bool:
    """독립 모드 Bearer 검증 — iMac 로컬 queue.db api_keys 직접 조회.

    app.py의 verify_api_key와 동일 스키마(SHA-256 key_hash, active, expires_at)를
    경량 워커에서 app.py(9.7K줄) import 없이 재현. 키 발급/관리는 기존 Admin(:8585)에서.
    """
    if not key:
        return False
    import hashlib
    import sqlite3
    from datetime import datetime, timezone
    db_path = os.environ.get("WORKER_QUEUE_DB") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "queue.db")
    if not os.path.exists(db_path):
        logger.warning("queue.db 없음 — 독립 모드 Bearer 검증 불가 (Admin 최초 기동 필요)")
        return False
    h = hashlib.sha256(key.encode()).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")  # 토큰 검증 경로 — BUSY 즉시실패 방어
        try:
            row = conn.execute(
                "SELECT id FROM api_keys WHERE key_hash=? AND active=1 "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (h, now_iso),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception as e:
        logger.warning("bearer 검증 실패: %s", e)
        return False


def worker_auth(f):
    """이중 인증: X-Worker-Token(위임) → Bearer(독립, opt-in) 순서로 검사."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Worker-Token", "")
        if WORKER_TOKEN and token and hmac.compare_digest(token, WORKER_TOKEN):
            return f(*args, **kwargs)
        if request.headers.get("Authorization", "").startswith("Bearer "):
            if WORKER_EXPOSE == "off":
                return jsonify({"error": "standalone mode disabled (WORKER_EXPOSE=off)"}), 403
            if _verify_bearer_key(request.headers["Authorization"][7:]):
                return f(*args, **kwargs)
            return jsonify({"error": "invalid api key"}), 401
        return jsonify({"error": "unauthorized"}), 401
    return wrapper


def guard(kind: str = "light"):
    """가드 데코레이터 (Flask 의존부 — core/worker_guard는 순수 로직만, 컨벤션 준수)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if kind == "heavy":
                denied = worker_guard.check_heavy(getattr(f, "__name__", "heavy"))
                if denied:
                    return jsonify(denied), 503
                try:
                    return f(*args, **kwargs)
                finally:
                    worker_guard.release_heavy()
            denied = worker_guard.check_light()
            if denied:
                return jsonify(denied), 503
            return f(*args, **kwargs)
        return wrapper
    return deco


def _get_image_bytes() -> bytes | None:
    """multipart 'image' 또는 JSON {"image_b64"} → bytes. 한도 초과/형식 오류 시 None."""
    if "image" in request.files:
        data = request.files["image"].read()
    else:
        body = request.get_json(silent=True) or {}
        b64 = body.get("image_b64", "")
        if not b64:
            return None
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return None
    if not data or len(data) > MAX_IMAGE_MB * 1024 * 1024:
        return None
    return data


@app.route("/worker/health")
def health():
    mem = worker_guard.memory_state()
    return jsonify({
        "ok": True,
        "version": VERSION,
        "platform": platform.machine(),
        "capabilities": CAPABILITIES,
        "memory": mem,
        "thermal": worker_guard.thermal_state(),
        "locks": {"heavy_task": worker_guard.current_heavy()},
        "detector": {"device": DETECTOR_DEVICE, "available": ai_image_detector.is_available()},
        "stt": {"available": mlx_stt.is_available(), "model": mlx_stt.MLX_WHISPER_MODEL,
                "ffmpeg": mlx_stt.ffmpeg_available()},
        "c2pa": c2pa_sign.available(C2PA_CERT, C2PA_KEY),
        "expose": WORKER_EXPOSE,
        "link": {"tunnel_11435_ok": _check_reverse_tunnel()},
    })


@app.route("/worker/detect/image", methods=["POST"])
@worker_auth
@guard("light")
def detect_image():
    """AI 생성 이미지 판별 (MPS) — GPU 서버 detect ML 단계와 동일 스키마."""
    data = _get_image_bytes()
    if data is None:
        return jsonify({"error": f"image required (multipart 'image' or image_b64, ≤{MAX_IMAGE_MB}MB)"}), 400
    import time
    t0 = time.time()
    result = ai_image_detector.classify(data, device=DETECTOR_DEVICE)
    result["device"] = DETECTOR_DEVICE
    result["duration_ms"] = round((time.time() - t0) * 1000)
    status = 200 if result.get("available") else 503
    return jsonify(result), status


@app.route("/worker/mark/image", methods=["POST"])
@worker_auth
@guard("light")
def mark_image():
    """가시 워터마크 + PNG iTXt 마킹 (+선택 C2PA 서명) — content_marking 재사용."""
    data = _get_image_bytes()
    if data is None:
        return jsonify({"error": f"image required (image_b64, ≤{MAX_IMAGE_MB}MB)"}), 400
    body = request.get_json(silent=True) or {}
    import time
    t0 = time.time()
    marked = content_marking.mark_image_bytes(
        data,
        generator=body.get("generator", "WeRU.B AI"),
        model=body.get("model"),
        position=body.get("position", "br"),
        force=bool(body.get("force", False)),
    )
    c2pa_applied = False
    if body.get("c2pa", False):
        signed = c2pa_sign.sign_image_bytes(
            marked, cert_path=C2PA_CERT, key_path=C2PA_KEY,
            generator=body.get("generator", "WeRU.B AI"), model=body.get("model"),
            version=VERSION,
        )
        c2pa_applied = signed is not marked
        marked = signed
    return jsonify({
        "image_b64": base64.b64encode(marked).decode(),
        "marked": True,
        "c2pa_signed": c2pa_applied,
        "duration_ms": round((time.time() - t0) * 1000),
    })


@app.route("/worker/stt", methods=["POST"])
@worker_auth
@guard("heavy")
def worker_stt():
    """Apple Silicon STT (mlx-whisper) — audio → 텍스트 전사.

    입력 (택1):
      - multipart: 'audio' 파일 필드 (wav/mp3/mp4/m4a/flac), form 'language' 옵션
      - JSON: {"audio_b64": "<base64>", "filename": "audio.mp4", "language": "ko"}
    출력: {"text", "segments", "language", "model", "duration_ms", "available": true}
    """
    if not mlx_stt.is_available():
        return jsonify({"error": "mlx-whisper not installed", "available": False}), 503

    if "audio" in request.files:
        f = request.files["audio"]
        audio_bytes = f.read()
        filename = f.filename or "audio.bin"
        language = request.form.get("language", "ko")
    else:
        body = request.get_json(silent=True) or {}
        b64 = body.get("audio_b64", "")
        if not b64:
            return jsonify({"error": "audio required (multipart 'audio' or audio_b64)"}), 400
        try:
            audio_bytes = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return jsonify({"error": "invalid base64"}), 400
        filename = body.get("filename", "audio.bin")
        language = body.get("language", "ko")

    if not audio_bytes:
        return jsonify({"error": "empty audio"}), 400
    if len(audio_bytes) > MAX_AUDIO_MB * 1024 * 1024:
        return jsonify({"error": f"audio too large (>{MAX_AUDIO_MB}MB)"}), 413

    try:
        result = mlx_stt.transcribe(audio_bytes, filename=filename, language=language)
    except Exception as e:
        logger.exception("STT transcribe failed")
        return jsonify({"available": False, "error": str(e)}), 503
    result["available"] = True
    return jsonify(result)


@app.route("/")
def index():
    return jsonify({"service": "WeRU.B Apple Silicon Worker", "version": VERSION,
                    "endpoints": ["/worker/health", "/worker/detect/image",
                                  "/worker/mark/image", "/worker/stt"]})


if __name__ == "__main__":
    if not WORKER_TOKEN:
        logger.warning("WORKER_TOKEN 미설정 — 위임 모드 인증 불가")
    app.run(host="127.0.0.1", port=8587, threaded=True)
