"""X5 — `_model_ok` fail-closed 고정 (2026-08-25).

`embed_model_pinned` 제약이 걸린 capability는 정합 검증 재료(worker embed_models·
컬렉션-모델 맵)가 오케스트레이터에 생기기 전까지 **remote 부적격 = GPU 로컬 100%**.
구 스텁(return True)은 embed를 켜는 순간 벡터 오염 가드가 0으로 동작했다.
실검증 구현 시 이 파일의 기대를 갱신할 것(가드 4종: embed-offload-agreement).
"""
import time

import pytest

from core.orchestrator import nodes, registry, scheduler


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 0
        s = nodes._states[nodes.IMAC]
        s.inflight = 0; s.fails = 0; s.breaker = "closed"; s.breaker_until = 0.0
        s.latency_ewma_ms = {}; s.reachable = True; s.capabilities = ["embed"]
        s.mem_pressure = "normal"; s.thermal = "nominal"; s.locks_heavy = None
        nodes._last_pick.clear()
        nodes._health_ts[nodes.IMAC] = time.time()   # 네트워크 refresh 스킵
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": ["embed"]})
    yield


def test_model_ok_is_fail_closed():
    assert scheduler._model_ok("embed", None) is False


def test_pinned_cap_never_delegates(monkeypatch):
    """embed_model_pinned + iMac 완전 건강 + prefer_imac이어도 GPU 로컬."""
    monkeypatch.setattr(registry, "capability", lambda n: registry.Capability(
        name=n, policy="prefer_imac", constraints={"embed_model_pinned": True}))
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 5   # 로컬 부하로 iMac 우위 유도
    assert scheduler.pick("embed") == nodes.GPU_LOCAL


def test_unpinned_cap_unaffected(monkeypatch):
    """제약 없는 capability의 위임은 fail-closed 전환의 영향을 받지 않는다."""
    monkeypatch.setattr(registry, "capability", lambda n: registry.Capability(
        name=n, policy="prefer_imac"))
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 5
    assert scheduler.pick("embed") == nodes.IMAC
