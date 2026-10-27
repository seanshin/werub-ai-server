"""Dispatch — run(cap, payload, local_fn): 단일 진입점 (Phase 1).

설계 §6. pick → 원격 호출 → 실패/미선택 시 local_fn 폴백. **예외 전파 금지**(원격/로컬 모두).
엔드포인트는 bespoke 위임 대신 이 함수만 호출한다.
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

# ---------------------------------------------------------------------------
# ⚠️ 폴백 경계 (신규 capability 배선 전 반드시 읽을 것)
#
# 이 함수는 **예외 / available:false / 비200** 만 실패로 본다. 원격 워커가 **HTTP 200 에
# 이형 페이로드**(키 이름이 다른 정상 응답 등)를 주면 성공으로 간주해 로컬 폴백이 발동하지
# 않고, 소비측이 자기 계약대로 키를 못 찾아 실패한다.
# 2026-07-29 실사례: meeting STT 가 워커의 `text` 를 `full_text` 로 못 읽어 **위임 경로가
# 간헐이 아니라 100% 실패**했다(에러 문자열이 'unknown'이라 6주간 무증상).
# → 새 capability 를 붙일 때는 워커 응답 키를 소비측 계약과 **실제 호출로** 대조할 것.
# ---------------------------------------------------------------------------

import logging
import time

from core.orchestrator import client, metrics, nodes, scheduler

logger = logging.getLogger("ollama-admin")


def _ms(t0: float) -> int:
    return round((time.time() - t0) * 1000)


def run(cap: str, payload, *, local_fn, hints: dict | None = None, remote_call=None):
    """노드 선택 → 원격이면 호출, 실패/미선택 시 local_fn 폴백.

    반환은 항상 local_fn 또는 원격 결과(dict). 예외를 위로 던지지 않는다.
    원격 dict는 engine=노드 id, 로컬 dict는 engine="gpu-local"로 스탬프.
    """
    try:
        nid = scheduler.pick(cap, hints)               # M1: pick 예외도 폴백(never-raise)
    except Exception as e:
        logger.warning("scheduler.pick(%s) 실패 → 로컬: %s", cap, e)
        return _run_local(cap, local_fn)
    if nid is None or nid == nodes.GPU_LOCAL:
        return _run_local(cap, local_fn)

    nodes.on_dispatch_start(nid, cap)
    t0 = time.time()
    res, err = None, None
    try:
        rc = remote_call or client.call
        res = rc(nid, cap, payload, hints)
    except Exception as e:
        err = e
    ms = _ms(t0)
    try:
        nodes.on_dispatch_end(nid, cap, res is not None, ms)   # M3: inflight 항상 정산(가드)
    except Exception as e:
        logger.debug("on_dispatch_end 예외 무시: %s", e)
    if res is not None:
        metrics.record(cap, nid, "delegated", ms)
        if isinstance(res, dict):
            res["engine"] = nid
        return res
    metrics.record(cap, nid, "error" if err else "fallback", ms,
                   detail=(str(err)[:80] if err else ""))
    if err:
        logger.info("dispatch(%s) 원격 실패 → 로컬 폴백: %s", cap, err)
    return _run_local(cap, local_fn)


def _run_local(cap: str, local_fn):
    t0 = time.time()
    try:
        r = local_fn()                                    # local_fn은 self-guard 권장
    except Exception as e:                                 # 폴백도 예외 전파 금지(방어)
        logger.warning("local_fn(%s) raised, error shape 반환: %s", cap, e)
        r = {"available": False, "error": str(e)[:120]}
    ms = _ms(t0)
    if isinstance(r, dict):
        r.setdefault("engine", nodes.GPU_LOCAL)
    # 🔴 로컬 지연도 **노드 상태에** 기록한다. 안 하면 스케줄러가 측정된 원격과
    #    측정 안 된 로컬을 비교하게 되고, 로컬이 항상 '지연 0'으로 이긴다.
    try:
        nodes.record_latency(nodes.GPU_LOCAL, cap, ms)
    except Exception as e:  # noqa: BLE001 — 관측이 실행을 막지 않는다
        logger.debug("record_latency 예외 무시: %s", e)
    metrics.record(cap, nodes.GPU_LOCAL, "local", ms)
    return r
