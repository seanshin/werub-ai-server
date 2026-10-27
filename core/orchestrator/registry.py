"""Capability 레지스트리 — config/orchestrator.json 선언 로드 (Phase 1).

설계 §3. 파일 미존재/로드 실패 시 내장 기본값(detect/mark). mtime 기반 캐시 + reload().
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("ollama-admin")

# core/orchestrator/registry.py → 레포 루트
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _path() -> str:
    return os.path.join(_ROOT, "config", "orchestrator.json")


_DEFAULTS = {
    "capabilities": {
        "detect": {"resource": "light", "worker_route": "/worker/detect/image",
                   "payload_kind": "image", "policy": "auto"},
        "mark": {"resource": "light", "worker_route": "/worker/mark/image",
                 "payload_kind": "image", "policy": "auto"},
    },
    "scheduler": {"w_inflight": 1.0, "w_latency": 0.5, "min_dwell_ms": 2000,
                  "hysteresis_margin": 2.0, "breaker_fails": 3, "breaker_open_ms": 30000},
}


@dataclass(frozen=True)
class Capability:
    name: str
    resource: str = "light"          # light(5s) | heavy(30s) — 기본 타임아웃 등급
    worker_route: str = ""
    payload_kind: str = "image"      # image(multipart 'image') | json | file
    policy: str = "auto"             # auto | prefer_imac | prefer_gpu | gpu_only
    timeout_s: int = 0               # >0이면 resource 기본 대신 이 값 사용(예: stt 장시간)
    constraints: dict = field(default_factory=dict)


_lock = threading.Lock()
_cache: dict = {"mtime": None, "caps": {}, "sched": {}, "loaded": False}


def load(force: bool = False) -> None:
    """config/orchestrator.json 로드 (lazy, mtime 캐시). 실패 시 기본값."""
    p = _path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = None
    with _lock:
        if not force and _cache["loaded"] and _cache["mtime"] == mt:
            return
        if mt is None:
            data = _DEFAULTS
        else:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning("orchestrator.json 로드 실패, 기본값 사용: %s", e)
                data = _DEFAULTS
        caps = {}
        for name, d in (data.get("capabilities") or {}).items():
            caps[name] = Capability(
                name=name, resource=d.get("resource", "light"),
                worker_route=d.get("worker_route", ""),
                payload_kind=d.get("payload_kind", "image"),
                policy=d.get("policy", "auto"),
                timeout_s=int(d.get("timeout_s", 0) or 0),
                constraints=d.get("constraints") or {})
        sched = {**_DEFAULTS["scheduler"], **(data.get("scheduler") or {})}
        for k, dv in _DEFAULTS["scheduler"].items():   # M4: 잘못된 타입은 기본값으로(dispatch 수치 오염 방지)
            try:
                sched[k] = float(sched[k])
            except (TypeError, ValueError):
                logger.warning("orchestrator.json scheduler.%s 타입 오류 → 기본값 %s", k, dv)
                sched[k] = float(dv)
        _cache.update({"mtime": mt, "caps": caps, "sched": sched, "loaded": True})


def capability(name: str) -> Capability | None:
    load()
    return _cache["caps"].get(name)


def all_capabilities() -> dict:
    load()
    return dict(_cache["caps"])


def scheduler_cfg() -> dict:
    load()
    return dict(_cache["sched"])


def reload() -> dict:
    """무재배포 재로드 (localhost only — 전역 미들웨어 보장)."""
    load(force=True)
    return {"capabilities": sorted(_cache["caps"]), "scheduler": _cache["sched"]}
