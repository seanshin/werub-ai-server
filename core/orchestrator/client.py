"""Node Client — remote 워커 route 호출 (Phase 1).

설계 §7. payload_kind 기반 인코딩(타입 추론 금지). 성공 dict / 실패 None.
네트워크 예외는 던진다(dispatch가 잡아 폴백) — 여기서 삼키지 않는다.
"""
from __future__ import annotations

import logging
import os

from core.orchestrator import nodes, registry

logger = logging.getLogger("ollama-admin")

_TIMEOUT = {"light": 5, "heavy": 300}  # STT(heavy): 긴 회의 전사 대비 5분


def _timeout(c) -> int:
    """capability.timeout_s가 지정되면 우선, 아니면 resource 등급 기본(stt 등 장시간 대응)."""
    if getattr(c, "timeout_s", 0):
        return c.timeout_s
    return _TIMEOUT.get(c.resource, 5)


_FIELD_EXT = {"image": "png", "audio": "wav", "file": "bin"}


def _hint_form(hints: dict | None) -> dict:
    """hints → multipart form data(문자열 값만). 워커는 form.get(키)로 읽는다
    (예: /worker/stt의 language). None·비-스칼라(list/dict)는 제외 — form은 문자열만
    싣고, 워커가 못 받는 복합 힌트(hotwords 등)는 상위에서 로컬 강제하는 계약(X7)."""
    if not hints:
        return {}
    out = {}
    for k, v in hints.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = str(v)
        else:
            logger.debug("hint %r 비-스칼라(%s) → 전송 제외", k, type(v).__name__)
    return out


def _encode(payload, kind: str, hints: dict | None = None):
    """payload_kind → (files, data, json_body, fh). fh는 닫아야 할 파일 핸들(없으면 None).

    멀티파트 필드명 = payload_kind (image/audio/file) — 워커 필드명 계약 일치.
    경로 문자열이면 그 시점에 열어 전송(대용량을 원격 선택 시에만 read), bytes면 인메모리.
    호출측이 fh를 닫음(H1). hints는 multipart면 form data로, json이면 body에 병합
    (payload 우선) — 종전엔 hints가 전송되지 않아 워커가 기본값(language=ko)으로 낙착했다(X7).
    """
    if kind in ("image", "audio", "file"):
        ext = _FIELD_EXT.get(kind, "bin")
        data = _hint_form(hints)
        if isinstance(payload, (bytes, bytearray)):
            return {kind: (f"{kind}.{ext}", payload)}, data, None, None
        if isinstance(payload, str) and os.path.exists(payload):
            fh = open(payload, "rb")               # wav 경로 → 파일 열어 전송(문자열 그대로 X)
            return {kind: (os.path.basename(payload), fh)}, data, None, fh
        return {kind: (f"{kind}.{ext}", payload)}, data, None, None
    # json — hints를 body에 병합하되 payload 키가 우선
    body = dict(payload) if isinstance(payload, dict) else {"data": payload}
    for k, v in (hints or {}).items():
        body.setdefault(k, v)
    return None, None, body, None


def call(node_id: str, cap: str, payload, hints: dict | None = None) -> dict | None:
    """remote 노드 워커 호출. ok = HTTP200 AND available!=false → dict, 그 외 None."""
    c = registry.capability(cap)
    conf = nodes.imac_conf()        # Phase 1: remote 노드는 imac-worker 하나
    if c is None or conf is None:
        return None
    import requests
    url = conf["url"] + c.worker_route
    files, data, json_body, fh = _encode(payload, c.payload_kind, hints)
    try:
        r = requests.post(url, files=files, data=data, json=json_body,
                          headers={"X-Worker-Token": conf["token"]},
                          timeout=_timeout(c))
    finally:
        if fh is not None:
            try:
                fh.close()          # H1: requests 예외/타임아웃에도 fd 확실히 해제
            except Exception:
                pass
    if r.status_code == 200:
        d = r.json()
        return d if d.get("available", True) else None
    logger.info("worker %s non-200: %s", c.worker_route, r.status_code)
    return None
