"""Scheduler — pick(cap, hints): 후보 필터(하드 게이트) → 점수 → 선택 (Phase 1).

설계 §5. 적격 원격 없음/전부 부적격 → gpu-local 폴백. Darwin이면 remote 후보 없음.
"""
from __future__ import annotations

import time

from core.orchestrator import nodes, registry


def pick(cap: str, hints: dict | None = None) -> str | None:
    c = registry.capability(cap)
    if c is None:
        return nodes.GPU_LOCAL          # 미선언 cap → 로컬(caller가 local_fn 보유)
    if c.policy == "gpu_only":
        return nodes.GPU_LOCAL          # 즉시 무력화 스위치

    sched = registry.scheduler_cfg()
    cands: list[tuple[str, float]] = []
    for nid in _candidate_ids(cap):
        st = nodes.state(nid)
        if st is None or not _eligible(st, cap, c, hints):
            continue
        cands.append((nid, _score(st, cap, c, sched)))

    if not cands:
        return nodes.GPU_LOCAL
    # 🔴 **동점은 정책으로 가른다 — 목록 순서로 가르면 안 된다.**
    # 2026-08-12 실측: iMac이 `mem_pressure=warn`(free 7.2GB·45% — macOS의 평상 상태)일 때
    # 소프트 패널티 5.0이 remote+prefer_imac 보너스 5.0과 **정확히 상쇄**돼 두 노드가
    # 10.00 동점이 됐다. 게다가 로컬 실행은 `on_dispatch_end`를 타지 않아
    # gpu-local의 `latency_ewma_ms`가 **영원히 비어 있어**(페널티 0) 동점이 풀리지도 않았다.
    # 안정 정렬이 gpu-local(후보 목록 첫 항목)을 남겨 **`prefer_imac`이 조용히
    # '절대 imac 아님'으로 뒤집혔다** — 터널·워커·설정이 전부 정상이라 화면은 초록이었다.
    cands.sort(key=lambda x: (x[1], _policy_rank(x[0], c)), reverse=True)
    return _apply_hysteresis(cap, cands, sched)


def _policy_rank(node_id: str, c) -> int:
    """동점 시 정책이 선호하는 노드에 1. 정책이 없으면 0(기존 순서 유지)."""
    if c.policy == "prefer_imac":
        return 1 if node_id == nodes.IMAC else 0
    if c.policy == "prefer_gpu":
        return 1 if node_id == nodes.GPU_LOCAL else 0
    return 0


def _candidate_ids(cap: str) -> list[str]:
    """gpu-local(항상) + imac-worker(활성+delegate 포함 시)."""
    ids = [nodes.GPU_LOCAL]
    conf = nodes.imac_conf()
    if conf and cap in conf.get("delegate", []):
        ids.append(nodes.IMAC)
    return ids


def _eligible(st, cap: str, c, hints) -> bool:
    if st.kind == "local":
        return True                                  # 로컬은 항상 적격(폴백 보장)
    if not st.reachable:
        return False
    if cap not in st.capabilities:                    # health manifest = 가용 진실
        return False
    if st.breaker == "open" and time.time() < st.breaker_until:
        return False
    if st.mem_pressure == "critical":                 # 하드 게이트
        return False
    if st.thermal == "throttled":                     # 하드 게이트(worker 어휘)
        return False
    if c.resource == "heavy" and st.locks_heavy:      # heavy 점유 시 heavy cap 제외
        return False
    if c.constraints.get("embed_model_pinned") and not _model_ok(cap, hints):
        return False
    return True
    # mem/thermal == "unknown" 은 게이트하지 않음(신호 부재 ≠ 위험)


def _model_ok(cap: str, hints) -> bool:
    # fail-closed(2026-08-25·X5): 정합을 검증할 재료(worker health의 embed_models·
    # 컬렉션-모델 맵)가 아직 오케스트레이터에 없다 → 미정합 취급 = remote 부적격
    # = GPU 로컬 100%(벡터 오염 0 — p2-embed 기획 불변식). 실검증은
    # embed-offload-agreement 가드 4종이 갖춰진 뒤 이 함수를 교체한다.
    return False


def _score(st, cap: str, c, sched: dict) -> float:
    base = {"prefer_imac": 10.0, "prefer_gpu": -10.0}.get(c.policy, 0.0)
    if st.kind == "remote" and c.policy == "prefer_imac":
        base += 5.0
    if st.kind == "local" and c.policy == "prefer_gpu":
        base += 5.0
    pen = float(sched["w_inflight"]) * st.inflight
    pen += float(sched["w_latency"]) * (st.latency_ewma_ms.get(cap, 0.0) / 1000.0)
    if st.mem_pressure == "warn":                     # 소프트 패널티
        pen += 5.0
    return base - pen


def _apply_hysteresis(cap: str, cands: list[tuple[str, float]], sched: dict) -> str:
    """직전 선택이 여전히 후보이고 점수차가 margin 미만·dwell 이내면 유지(진동 방지)."""
    best_id, best_score = cands[0]
    score_by = dict(cands)
    now = time.time()
    margin = float(sched.get("hysteresis_margin", 2.0))
    dwell = float(sched.get("min_dwell_ms", 2000)) / 1000.0
    with nodes._LOCK:
        last = nodes._last_pick.get(cap)
        if (last and last[0] != best_id and (now - last[1]) < dwell
                and last[0] in score_by and (best_score - score_by[last[0]]) < margin):
            return last[0]                            # 직전 노드 유지(ts 보존)
        nodes._last_pick[cap] = (best_id, now)
        return best_id
