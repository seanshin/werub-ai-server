"""Node Health Plane — 노드 실시간 상태 + EWMA + circuit breaker (Phase 1).

설계 §4. 동시성: 모든 변이를 _LOCK으로 보호(gthread 96 — `inflight += 1`은 비원자).
imac-worker 런타임 설정은 config.json apple_worker_* 재사용(활성화 스크립트·imac_link 정합).
"""
from __future__ import annotations

import logging
import platform
import threading
import time
from dataclasses import dataclass, field

from core.orchestrator import registry

logger = logging.getLogger("ollama-admin")

IS_DARWIN = platform.system() == "Darwin"   # iMac 자신은 remote 위임 개시 안 함

GPU_LOCAL = "gpu-local"
IMAC = "imac-worker"
HEALTH_TTL = 30  # 기존 apple_worker 헬스 캐시와 동일


@dataclass
class NodeState:
    id: str
    kind: str                                    # local | remote
    reachable: bool = True                        # local은 항상 True
    capabilities: list = field(default_factory=list)
    mem_pressure: str = "normal"                  # normal|warn|critical|unknown
    thermal: str = "nominal"                      # nominal|throttled|unknown
    locks_heavy: object = None                     # heavy 작업 점유(있으면 heavy cap 제외)
    inflight: int = 0
    latency_ewma_ms: dict = field(default_factory=dict)   # {cap: ms}
    breaker: str = "closed"                        # closed|open|half_open
    breaker_until: float = 0.0
    fails: int = 0


_LOCK = threading.Lock()
_states: dict = {
    GPU_LOCAL: NodeState(id=GPU_LOCAL, kind="local"),
    IMAC: NodeState(id=IMAC, kind="remote", reachable=False),
}
_health_ts: dict = {IMAC: 0.0}
_refresh_inflight: bool = False   # 헬스 백그라운드 갱신 단일화(스탬피드 방지)
_last_pick: dict = {}   # cap -> (node_id, ts)  scheduler hysteresis (동일 _LOCK 공유)


def imac_conf() -> dict | None:
    """imac-worker 런타임 설정 — config.json apple_worker_* 재사용. Darwin/미설정 시 None.

    Darwin(iMac)에서도 apple_worker_self_delegate=true면 localhost 워커 위임 허용.
    """
    if IS_DARWIN:
        try:
            from core.config import load_config
            cfg = load_config()
        except Exception:
            return None
        if not cfg.get("apple_worker_self_delegate"):
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


def _ewma(prev: float, sample: float, alpha: float = 0.3) -> float:
    return alpha * sample + (1 - alpha) * prev


def _refresh_imac() -> None:
    """워커 /worker/health 폴링 → NodeState 매핑 (네트워크는 _LOCK 밖)."""
    conf = imac_conf()
    if conf is None:
        with _LOCK:
            st = _states[IMAC]
            st.reachable = False
            st.capabilities = []
        return
    ok, caps, mem, thermal, heavy = False, [], "unknown", "unknown", None
    try:
        import requests
        r = requests.get(f"{conf['url']}/worker/health", timeout=3)
        if r.status_code == 200:
            d = r.json()
            ok = bool(d.get("ok"))
            caps = d.get("capabilities", []) or []
            # detect는 워커 ML 런타임(transformers/torch) 의존 — detector.available=False면
            # 광고해도 503만 반환하므로 capability에서 제외(스케줄러 라우팅 차단 → breaker 트립 churn 방지).
            # mark는 Pillow 기반이라 detector 무관. [node-orchestrator hardening]
            if "detect" in caps and not (d.get("detector") or {}).get("available", True):
                caps = [c for c in caps if c != "detect"]
            mem = (d.get("memory") or {}).get("pressure", "unknown") or "unknown"
            thermal = d.get("thermal", "unknown") or "unknown"
            heavy = (d.get("locks") or {}).get("heavy_task")
    except Exception as e:
        logger.debug("imac-worker health 실패: %s", e)
    with _LOCK:
        st = _states[IMAC]
        st.reachable = ok
        st.capabilities = caps
        st.mem_pressure = mem
        st.thermal = thermal
        st.locks_heavy = heavy


def _bg_refresh() -> None:
    """백그라운드 헬스 갱신 — 네트워크가 요청 스레드를 막지 않도록(M5/SSE 인시던트 패턴 회피)."""
    global _refresh_inflight
    try:
        _refresh_imac()
    finally:
        with _LOCK:
            _health_ts[IMAC] = time.time()
            _refresh_inflight = False


def state(node_id: str) -> NodeState | None:
    """노드 상태 스냅샷(복사). remote는 TTL 경과 시 **비차단** 백그라운드 갱신 트리거.

    요청 스레드는 절대 네트워크로 블로킹되지 않는다(현재 캐시값 즉시 반환).
    갱신 직후 첫 호출은 약간 stale일 수 있으나(폴백 안전), gthread 고갈 트리거를 제거한다.
    """
    global _refresh_inflight
    if node_id == IMAC:
        spawn = False
        now = time.time()
        with _LOCK:
            if not _refresh_inflight and now - _health_ts.get(IMAC, 0.0) >= HEALTH_TTL:
                _refresh_inflight = True
                spawn = True
        if spawn:
            threading.Thread(target=_bg_refresh, name="imac-health", daemon=True).start()
    with _LOCK:
        st = _states.get(node_id)
        if st is None:
            return None
        return NodeState(
            id=st.id, kind=st.kind, reachable=st.reachable,
            capabilities=list(st.capabilities), mem_pressure=st.mem_pressure,
            thermal=st.thermal, locks_heavy=st.locks_heavy, inflight=st.inflight,
            latency_ewma_ms=dict(st.latency_ewma_ms), breaker=st.breaker,
            breaker_until=st.breaker_until, fails=st.fails)


def refresh(node_id: str) -> None:
    """동기(블로킹) 강제 갱신 — 명시적/테스트용. 요청 경로에서는 state()(비차단)를 쓸 것."""
    if node_id == IMAC:
        _refresh_imac()
        with _LOCK:
            _health_ts[IMAC] = time.time()


def on_dispatch_start(node_id: str, cap: str) -> None:
    with _LOCK:
        st = _states.get(node_id)
        if st:
            st.inflight += 1


def record_latency(node_id: str, cap: str, ms: float) -> None:
    """지연만 기록한다(inflight·breaker 미개입) — **로컬 실행용**.

    🔴 2026-08-12 실측: 로컬 실행은 `on_dispatch_end`를 타지 않아 gpu-local의
    `latency_ewma_ms`가 **영원히 비어 있었다**. 그래서 스케줄러가
    **측정된 원격 노드**와 **측정 안 된 로컬 노드**(지연 0으로 취급 = 무한히 빠름)를
    비교했다. 비교의 한쪽만 재면 그 비교는 판정이 아니라 편향이다.
    """
    with _LOCK:
        st = _states.get(node_id)
        if not st:
            return
        prev = st.latency_ewma_ms.get(cap, ms)
        st.latency_ewma_ms[cap] = _ewma(prev, ms)


def on_dispatch_end(node_id: str, cap: str, ok: bool, ms: float) -> None:
    sched = registry.scheduler_cfg()
    with _LOCK:
        st = _states.get(node_id)
        if not st:
            return
        st.inflight = max(0, st.inflight - 1)   # finally 경로 보장(dispatch)
        prev = st.latency_ewma_ms.get(cap, ms)
        st.latency_ewma_ms[cap] = _ewma(prev, ms)
        if ok:
            st.fails = 0
            st.breaker = "closed"               # 성공 = probe 성공 포함 닫힘
        else:
            st.fails += 1
            if st.fails >= int(sched["breaker_fails"]):
                st.breaker = "open"
                st.breaker_until = time.time() + float(sched["breaker_open_ms"]) / 1000.0
