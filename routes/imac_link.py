"""iMac 연계 상태 API — GPU↔iMac 터널/워커/위임 통합 가시화.

[imac-link-console Phase A] 설계: docs/01-plan/features/imac-link-console.plan.md
읽기 전용 — 설정 변경(Phase B)은 별도. 토큰 등 시크릿 미노출.
"""
from __future__ import annotations

import logging
import time

from flask import Blueprint, jsonify

logger = logging.getLogger("ollama-admin")

imac_link_bp = Blueprint("imac_link", __name__)

# app.py 주입 (init 패턴 — activity.py와 동일)
_IMAC_OLLAMA_BASE = "http://localhost:8586"
_LIGHTWEIGHT_MODELS: set = set()


def init_imac_link(*, imac_ollama_base: str | None = None, lightweight_models=None):
    global _IMAC_OLLAMA_BASE, _LIGHTWEIGHT_MODELS
    if imac_ollama_base:
        _IMAC_OLLAMA_BASE = imac_ollama_base
    if lightweight_models:
        _LIGHTWEIGHT_MODELS = set(lightweight_models)


def _ping_8586() -> dict:
    """L1/L2 채널 — iMac ollama-admin (터널 8586→iMac:8585).
    프로브=/api/status: 8586 뒤 서버는 raw Ollama가 아니라 ollama-admin이라 GET /api/tags는 405.
    /api/status가 200+모델목록을 주므로 이걸로 판정(모델 키는 name/model 양쪽 호환)."""
    try:
        import requests
        t0 = time.time()
        r = requests.get(f"{_IMAC_OLLAMA_BASE}/api/status", timeout=2)
        models = ([(m.get("name") or m.get("model")) for m in r.json().get("models", [])]
                  if r.status_code == 200 else [])
        return {"ok": r.status_code == 200, "latency_ms": round((time.time() - t0) * 1000),
                "models": [m for m in models if m]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def _ping_worker() -> dict:
    """L3/L5 채널 — iMac 워커 (터널 8587). health는 무인증."""
    from core import apple_worker
    conf = apple_worker._conf()
    url = (conf["url"] if conf else "http://localhost:8587")
    try:
        import requests
        t0 = time.time()
        r = requests.get(f"{url}/worker/health", timeout=2)
        if r.status_code == 200:
            h = r.json()
            return {"ok": True, "latency_ms": round((time.time() - t0) * 1000), "health": h}
        return {"ok": False, "error": f"status={r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def _delegation_stats() -> dict:
    """위임 실적 — **오케스트레이터 metrics**에서 집계 (v2.124.95).

    🔴 종전에는 `apple_worker.stats()`를 읽었는데, 위임 경로가 v2.85.0에서
    `dispatch.run()`으로 이관되면서 `apple_worker.delegate_*`는 **호출되지 않는다**
    (레포 전수: `apple_worker`는 이 파일의 stats 조회 한 곳에서만 참조된다).
    ⇒ 카운터가 영구히 0이었고 대시보드 "실적" 줄이 항상 `위임 0 · 폴백 0`이었다.
    08-10 실측: detect 3회 중 2회가 실제 위임됐는데(`detect|imac-worker|delegated: 2`)
    대시보드는 0을 표시했다.

    프론트 계약(`kind`·`capability`)은 그대로 두고 매핑한다 — 화면 수정 없이 값이 산다.
    `local`은 신규 필드다: 워커 재생성 직후 노드 상태가 차기 전 첫 요청이 로컬로 가는
    창(설계상 비차단 폴백)을 눈으로 구분하기 위한 것.
    """
    try:
        from core.orchestrator import metrics
        snap = metrics.snapshot()
    except Exception:  # noqa: BLE001 — 실적은 부가 정보, 상태 조회를 막지 않는다
        return {"delegated": 0, "fallback": 0, "local": 0, "recent": []}
    agg = {"delegated": 0, "fallback": 0, "local": 0, "error": 0}
    for key, n in (snap.get("counts") or {}).items():
        decision = key.rsplit("|", 1)[-1]
        if decision in agg:
            agg[decision] += n
    recent = [{"kind": r.get("decision"), "capability": r.get("cap"),
               "node": r.get("node"), "ms": r.get("ms"), "ts": r.get("ts")}
              for r in (snap.get("recent") or [])[:10]]
    return {**agg, "recent": recent}


@imac_link_bp.route("/api/imac-link/status")
def imac_link_status():
    """GPU↔iMac 연계 통합 상태 (터널 채널 + 워커 + 위임 config/실적).
    ---
    tags: [iMac Link]
    responses:
      200:
        description: 연계 상태
        schema:
          type: object
          properties:
            tunnels: {type: object}
            worker: {type: object}
            delegation: {type: object}
            offload: {type: object}
            checked_at: {}
    """
    from core import apple_worker
    from core.config import load_config

    cfg = load_config()
    ch_8586 = _ping_8586()
    ch_worker = _ping_worker()
    worker_health = ch_worker.get("health", {})

    return jsonify({
        "tunnels": {
            "8586_ollama": {k: v for k, v in ch_8586.items() if k != "models"},
            "8587_worker": {k: v for k, v in ch_worker.items() if k != "health"},
            # L4 역방향(-L 11435 → GPU :8585) — iMac 워커가 자가 점검해 health.link로 보고
            "11435_reverse": worker_health.get("link", {}).get("tunnel_11435_ok"),
        },
        "worker": {
            "version": worker_health.get("version"),
            "capabilities": worker_health.get("capabilities", []),
            "memory": worker_health.get("memory"),
            "thermal": worker_health.get("thermal"),
            "locks": worker_health.get("locks"),
            "expose": worker_health.get("expose"),
            "detector": worker_health.get("detector"),
        },
        "delegation": {
            "enabled": bool(cfg.get("apple_worker_enabled")),
            "delegate": cfg.get("apple_worker_delegate", []),
            "url": cfg.get("apple_worker_url", ""),
            "token_set": bool(cfg.get("apple_worker_token")),  # 값은 비노출
            "darwin_guard": apple_worker.IS_DARWIN,            # True면 이 호스트에선 위임 불가
            "stats": _delegation_stats(),
        },
        "offload": {
            "lightweight_models": sorted(_LIGHTWEIGHT_MODELS),
            "imac_models": ch_8586.get("models", []),
            "models_ready": [m for m in sorted(_LIGHTWEIGHT_MODELS)
                             if m in (ch_8586.get("models") or [])],
        },
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


@imac_link_bp.route("/api/orchestrator/nodes")
def orchestrator_nodes():
    """Node Orchestrator 노드×capability×health×라우팅 결정 (node-orchestrator Phase 1).
    ---
    tags: [iMac Link]
    responses:
      200:
        description: 오케스트레이터 노드/메트릭 상태
        schema:
          type: object
          properties:
            capabilities: {type: object}
            nodes: {}
            metrics: {}
            darwin_guard: {}
            checked_at: {}
    """
    from core.orchestrator import metrics, nodes, registry

    caps = registry.all_capabilities()
    out_nodes = {}
    for nid in (nodes.GPU_LOCAL, nodes.IMAC):
        st = nodes.state(nid)
        if st is None:
            continue
        # gpu-local은 모든 선언 capability를 처리하는 폴백 노드 → 표시상 전체 capability 노출
        # (스케줄러는 local을 항상 적격 처리하므로 기능 영향 없음, 콘솔 가독성 개선)
        node_caps = list(caps.keys()) if st.kind == "local" else st.capabilities
        out_nodes[nid] = {
            "kind": st.kind, "reachable": st.reachable, "capabilities": node_caps,
            "mem_pressure": st.mem_pressure, "thermal": st.thermal, "inflight": st.inflight,
            "breaker": st.breaker, "latency_ewma_ms": st.latency_ewma_ms,
        }
    return jsonify({
        "capabilities": {k: {"resource": c.resource, "policy": c.policy,
                             "worker_route": c.worker_route} for k, c in caps.items()},
        "nodes": out_nodes,
        "metrics": metrics.snapshot(),
        "darwin_guard": nodes.IS_DARWIN,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


@imac_link_bp.route("/api/orchestrator/registry/reload", methods=["POST"])
def orchestrator_registry_reload():
    """config/orchestrator.json 무재배포 재로드 (localhost only — 전역 미들웨어).
    ---
    tags: [iMac Link]
    responses:
      200:
        description: 재로드 결과
        schema:
          type: object
          properties:
            ok: {type: boolean}
            reloaded: {}
    """
    from core.orchestrator import registry
    return jsonify({"ok": True, "reloaded": registry.reload()})
