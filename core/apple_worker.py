"""GPU 서버 → iMac Apple Silicon 워커 위임 클라이언트.

[apple-silicon-worker Phase 1] 설계: docs/02-design/features/apple-silicon-worker.design.md §3

활성 조건 (전부 충족해야 위임 — 하나라도 빠지면 완전 휴면, 기존 동작 100%):
  1. Linux에서 실행 중 (Darwin 가드 — iMac에서는 config가 있어도 위임 불가)
  2. config.json에 apple_worker_enabled=true + apple_worker_url + apple_worker_token
  3. 해당 기능이 apple_worker_delegate 목록에 포함 (예: ["detect"])
  4. 워커 헬스체크 통과 (30초 캐시 — 기존 iMac 오프로드 패턴)

폴백 계약: 모든 메서드는 실패(타임아웃/5xx/토큰 오류/미설정) 시 None을 반환한다.
호출측은 None이면 기존 로컬 경로를 실행한다. 예외를 위로 던지지 않는다.

GPU 서버 config.json 설정 예:
  "apple_worker_enabled": true,
  "apple_worker_url": "http://localhost:8587",     ← SSH 터널 (iMac :8587)
  "apple_worker_token": "<iMac ~/.werub_worker_token 값>",
  "apple_worker_delegate": ["detect"]
"""
from __future__ import annotations

import logging
import platform
import time

logger = logging.getLogger("ollama-admin")

IS_DARWIN = platform.system() == "Darwin"  # iMac 자신은 절대 위임하지 않음

_health_cache = {"ts": 0.0, "ok": False, "capabilities": []}
HEALTH_TTL = 30  # 기존 iMac 오프로드 헬스체크 캐시와 동일

# 위임 실적 카운터 (imac-link-console Phase A — 인메모리, 프로세스 기동 후 누적)
from collections import deque as _deque
_stats = {"delegated": 0, "fallback": 0, "recent": _deque(maxlen=10)}


def _record(kind: str, capability: str, detail: str = "", duration_ms: int | None = None):
    _stats[kind] = _stats.get(kind, 0) + 1
    _stats["recent"].appendleft({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
        "capability": capability, "detail": detail, "duration_ms": duration_ms,
    })


def stats() -> dict:
    """위임 실적 — imac-link 상태 API용."""
    return {"delegated": _stats["delegated"], "fallback": _stats["fallback"],
            "recent": list(_stats["recent"])}

DETECT_TIMEOUT = 5
EMBED_TIMEOUT = 30
STT_SUBMIT_TIMEOUT = 10


def _conf() -> dict | None:
    """위임 설정 로드. 비활성/미설정/Darwin이면 None."""
    if IS_DARWIN:
        return None
    try:
        from core.config import load_config
        cfg = load_config()
    except Exception:
        return None
    if not cfg.get("apple_worker_enabled"):
        return None
    url = cfg.get("apple_worker_url", "")
    token = cfg.get("apple_worker_token", "")
    if not url or not token:
        return None
    return {"url": url.rstrip("/"), "token": token,
            "delegate": cfg.get("apple_worker_delegate", [])}


def available(capability: str) -> bool:
    """위임 가능 여부 — config + delegate 목록 + 헬스(30s 캐시)."""
    conf = _conf()
    if conf is None or capability not in conf["delegate"]:
        return False
    now = time.time()
    if now - _health_cache["ts"] < HEALTH_TTL:
        return _health_cache["ok"] and capability in _health_cache["capabilities"]
    ok, caps = False, []
    try:
        import requests
        r = requests.get(f"{conf['url']}/worker/health", timeout=3)
        if r.status_code == 200:
            d = r.json()
            ok = bool(d.get("ok"))
            caps = d.get("capabilities", [])
    except Exception as e:
        logger.debug("apple_worker health failed: %s", e)
    _health_cache.update({"ts": now, "ok": ok, "capabilities": caps})
    return ok and capability in caps


def detect(image_bytes: bytes) -> dict | None:
    """AI 판별 위임. 성공 시 classify()와 동일 스키마(dict), 실패/미설정 시 None."""
    if not image_bytes or not available("detect"):
        return None
    conf = _conf()
    if conf is None:
        return None
    t0 = time.time()
    try:
        import requests
        r = requests.post(
            f"{conf['url']}/worker/detect/image",
            files={"image": ("image.png", image_bytes)},
            headers={"X-Worker-Token": conf["token"]},
            timeout=DETECT_TIMEOUT,
        )
        if r.status_code == 200:
            result = r.json()
            if result.get("available"):
                result["engine"] = "imac"
                _record("delegated", "detect", duration_ms=round((time.time() - t0) * 1000))
                return result
        logger.info("apple_worker detect non-200/unavailable: %s", r.status_code)
        _record("fallback", "detect", detail=f"status={r.status_code}")
    except Exception as e:
        logger.info("apple_worker detect failed, fallback to local: %s", e)
        _record("fallback", "detect", detail=str(e)[:80])
        _health_cache["ts"] = 0.0  # 다음 호출 시 헬스 재확인
    return None
