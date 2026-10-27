"""iMac M1 워커 가드 — 메모리/thermal 보호 + heavy 작업 상호배제.

[apple-silicon-worker Phase 1] macOS 전용 (Linux에서 import해도 무해 — 미사용).
16GB Unified Memory에서 STT↔임베딩 동시 실행으로 인한 swap 붕괴를 차단한다.
설계: docs/02-design/features/apple-silicon-worker.design.md §6
"""
from __future__ import annotations

import logging
import subprocess
import threading

logger = logging.getLogger("worker")

# heavy 작업(stt/embed) 상호배제 — 단순 선착순, 공평 큐 없음 (실패 시 503 → 폴백)
_heavy_lock = threading.Lock()
_heavy_task: str | None = None
_state_lock = threading.Lock()

MIN_FREE_GB_FOR_HEAVY = 2.0  # mlx-whisper large-v3 실사용 ~1.5GB; heavy 동시실행은 lock으로 차단


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def memory_state() -> dict:
    """memory_pressure 기반 상태. {"free_gb", "free_pct", "pressure"}"""
    free_pct = None
    out = _run(["memory_pressure", "-Q"])
    for line in out.splitlines():
        if "free percentage" in line:
            try:
                free_pct = float(line.split(":")[1].strip().rstrip("%"))
            except ValueError:
                pass
    lvl = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    pressure = {"1": "normal", "2": "warn", "4": "critical"}.get(lvl, "unknown")
    total_b = _run(["sysctl", "-n", "hw.memsize"]).strip()
    free_gb = None
    if free_pct is not None and total_b.isdigit():
        free_gb = round(int(total_b) / 1024**3 * free_pct / 100, 1)
    return {"free_gb": free_gb, "free_pct": free_pct, "pressure": pressure}


def thermal_state() -> str:
    """pmset -g therm → nominal | throttled(CPU_Speed_Limit<100) | unknown"""
    out = _run(["pmset", "-g", "therm"])
    for line in out.splitlines():
        if "CPU_Speed_Limit" in line:
            try:
                return "throttled" if int(line.split("=")[1].strip()) < 100 else "nominal"
            except ValueError:
                pass
    return "nominal" if out else "unknown"


def current_heavy() -> str | None:
    return _heavy_task


def acquire_heavy(task: str) -> bool:
    """heavy 작업(stt/embed) 락 + 메모리 확인. 성공 시 True (release_heavy 필수)."""
    global _heavy_task
    if not _heavy_lock.acquire(blocking=False):
        logger.info("heavy lock busy (current=%s, requested=%s)", _heavy_task, task)
        return False
    mem = memory_state()
    if mem["pressure"] in ("warn", "critical") or (
        mem["free_gb"] is not None and mem["free_gb"] < MIN_FREE_GB_FOR_HEAVY
    ):
        _heavy_lock.release()
        logger.warning("heavy rejected by memory guard: %s", mem)
        return False
    with _state_lock:
        _heavy_task = task
    return True


def release_heavy():
    global _heavy_task
    with _state_lock:
        _heavy_task = None
    try:
        _heavy_lock.release()
    except RuntimeError:
        pass


def check_light() -> dict | None:
    """light 작업 가드 — memory critical이면 거부 사유 dict, 통과면 None.
    (Flask 무의존 — 컨벤션: core/는 프레임워크 독립. 데코레이터는 worker_app.py)"""
    mem = memory_state()
    if mem["pressure"] == "critical":
        return {"error": "memory critical", "memory": mem, "retry_after": 300}
    return None


def check_heavy(task: str) -> dict | None:
    """heavy 작업 가드 — 락+메모리 획득 실패 시 거부 사유 dict (release 불필요),
    통과면 None (호출측이 finally에서 release_heavy 필수)."""
    if not acquire_heavy(task):
        return {"error": "busy or low memory", "current": current_heavy(),
                "memory": memory_state(), "retry_after": 600}
    return None
