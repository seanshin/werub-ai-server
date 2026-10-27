"""nodes — Health Plane: EWMA, breaker, inflight 동시성, health 매핑 (Phase 1)."""
import sys
import threading
import time
import types

import pytest

from core.orchestrator import nodes


@pytest.fixture(autouse=True)
def _reset():
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 0
        s = nodes._states[nodes.IMAC]
        s.inflight = 0; s.fails = 0; s.breaker = "closed"; s.breaker_until = 0.0
        s.latency_ewma_ms = {}; s.reachable = False; s.capabilities = []
        s.mem_pressure = "normal"; s.thermal = "nominal"; s.locks_heavy = None
        nodes._last_pick.clear()
        nodes._health_ts[nodes.IMAC] = time.time()   # state()에서 네트워크 refresh 스킵
    yield


def test_imac_conf_darwin_guard(monkeypatch):
    monkeypatch.setattr(nodes, "IS_DARWIN", True)
    assert nodes.imac_conf() is None


def test_imac_conf_from_config(monkeypatch):
    monkeypatch.setattr(nodes, "IS_DARWIN", False)
    import core.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", lambda: {
        "apple_worker_enabled": True, "apple_worker_url": "http://x:8587/",
        "apple_worker_token": "t", "apple_worker_delegate": ["detect"]})
    conf = nodes.imac_conf()
    assert conf["url"] == "http://x:8587" and conf["delegate"] == ["detect"]


def test_imac_conf_disabled(monkeypatch):
    monkeypatch.setattr(nodes, "IS_DARWIN", False)
    import core.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", lambda: {"apple_worker_enabled": False})
    assert nodes.imac_conf() is None


def test_ewma_updates():
    nodes.on_dispatch_end(nodes.IMAC, "detect", True, 100)
    a = nodes.state(nodes.IMAC).latency_ewma_ms["detect"]
    nodes.on_dispatch_end(nodes.IMAC, "detect", True, 200)
    b = nodes.state(nodes.IMAC).latency_ewma_ms["detect"]
    assert a < b <= 200


def test_breaker_opens_after_fails():
    for _ in range(3):                       # breaker_fails 기본 3
        nodes.on_dispatch_end(nodes.IMAC, "detect", False, 5)
    st = nodes.state(nodes.IMAC)
    assert st.breaker == "open" and st.breaker_until > time.time()


def test_breaker_closes_on_success():
    for _ in range(3):
        nodes.on_dispatch_end(nodes.IMAC, "detect", False, 5)
    assert nodes.state(nodes.IMAC).breaker == "open"
    nodes.on_dispatch_end(nodes.IMAC, "detect", True, 5)   # probe 성공
    st = nodes.state(nodes.IMAC)
    assert st.breaker == "closed" and st.fails == 0


def test_inflight_concurrency_no_leak():
    def worker():
        nodes.on_dispatch_start(nodes.IMAC, "detect")
        nodes.on_dispatch_end(nodes.IMAC, "detect", True, 5)
    ts = [threading.Thread(target=worker) for _ in range(64)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert nodes.state(nodes.IMAC).inflight == 0


def test_health_mapping_nested_and_unknown(monkeypatch):
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": ["detect"]})

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "capabilities": ["detect", "mark"],
                    "memory": {"pressure": "warn"}, "thermal": "throttled",
                    "locks": {"heavy_task": "stt"}}
    fake = types.SimpleNamespace(get=lambda *a, **k: _Resp())
    monkeypatch.setitem(sys.modules, "requests", fake)

    nodes.refresh(nodes.IMAC)
    st = nodes.state(nodes.IMAC)
    assert st.reachable is True
    assert st.mem_pressure == "warn"           # memory.pressure 중첩 매핑
    assert st.thermal == "throttled"
    assert st.locks_heavy == "stt"
    assert "detect" in st.capabilities


def test_detect_gated_on_detector_available(monkeypatch):
    """워커 detector.available=False면 detect capability 제외(503 churn 방지), mark는 유지."""
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": ["detect", "mark"]})

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "capabilities": ["detect", "mark"],
                    "detector": {"available": False, "device": "mps"}}
    fake = types.SimpleNamespace(get=lambda *a, **k: _Resp())
    monkeypatch.setitem(sys.modules, "requests", fake)

    nodes.refresh(nodes.IMAC)
    st = nodes.state(nodes.IMAC)
    assert "detect" not in st.capabilities   # ML 미가용 → detect 제외
    assert "mark" in st.capabilities          # Pillow 기반 → 유지
