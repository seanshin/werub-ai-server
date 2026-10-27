"""dispatch — run() 폴백 불변식 + engine 스탬프 + 동시성 + detect 파리티 (Phase 1)."""
import threading
import time

import pytest

from core.orchestrator import dispatch, metrics, nodes, scheduler


@pytest.fixture(autouse=True)
def _reset():
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 0
        s = nodes._states[nodes.IMAC]
        s.inflight = 0; s.fails = 0; s.breaker = "closed"; s.breaker_until = 0.0
        s.latency_ewma_ms = {}
        nodes._last_pick.clear()
        nodes._health_ts[nodes.IMAC] = time.time()
    metrics.reset()
    yield


def _pick_local(monkeypatch):
    monkeypatch.setattr(scheduler, "pick", lambda cap, hints=None: nodes.GPU_LOCAL)


def _pick_imac(monkeypatch):
    monkeypatch.setattr(scheduler, "pick", lambda cap, hints=None: nodes.IMAC)


def test_node_zero_runs_local(monkeypatch):
    _pick_local(monkeypatch)
    r = dispatch.run("detect", b"x", local_fn=lambda: {"available": True, "ai_probability": 0.1})
    assert r["engine"] == "gpu-local" and r["available"] is True


def test_remote_success_engine(monkeypatch):
    _pick_imac(monkeypatch)
    fake = lambda nid, cap, payload, hints: {"available": True, "ai_probability": 0.9}
    r = dispatch.run("detect", b"x", local_fn=lambda: {"available": True}, remote_call=fake)
    assert r["engine"] == "imac-worker" and r["ai_probability"] == 0.9
    assert nodes.state(nodes.IMAC).inflight == 0


def test_remote_none_falls_back(monkeypatch):
    _pick_imac(monkeypatch)
    fake = lambda *a, **k: None
    r = dispatch.run("detect", b"x", local_fn=lambda: {"available": True, "src": "local"},
                     remote_call=fake)
    assert r["engine"] == "gpu-local" and r["src"] == "local"


def test_remote_raises_falls_back(monkeypatch):
    _pick_imac(monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("tunnel down")
    r = dispatch.run("detect", b"x", local_fn=lambda: {"available": False},
                     remote_call=boom)
    assert r["engine"] == "gpu-local"          # 예외 전파 없이 폴백


def test_local_fn_raises_no_propagation(monkeypatch):
    _pick_local(monkeypatch)
    def boom():
        raise ValueError("classify exploded")
    r = dispatch.run("detect", b"x", local_fn=boom)   # 예외 전파 금지(W4)
    assert r["available"] is False and "error" in r and r["engine"] == "gpu-local"


def test_metrics_recorded(monkeypatch):
    _pick_imac(monkeypatch)
    dispatch.run("detect", b"x", local_fn=lambda: {"available": True},
                 remote_call=lambda *a, **k: {"available": True})
    snap = metrics.snapshot()
    assert any("detect|imac-worker|delegated" == k for k in snap["counts"])


def test_concurrent_dispatch_no_inflight_leak(monkeypatch):
    _pick_imac(monkeypatch)
    def fake(nid, cap, payload, hints):
        return {"available": True}
    def worker():
        dispatch.run("detect", b"x", local_fn=lambda: {"available": True}, remote_call=fake)
    ts = [threading.Thread(target=worker) for _ in range(64)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert nodes.state(nodes.IMAC).inflight == 0


def test_pick_failure_falls_back_local(monkeypatch):
    # M1: scheduler.pick이 예외를 던져도 never-raise → 로컬 폴백
    def _boom(cap, hints=None):
        raise RuntimeError("pick boom")
    monkeypatch.setattr(scheduler, "pick", _boom)
    r = dispatch.run("detect", b"x", local_fn=lambda: {"available": True})
    assert r["engine"] == "gpu-local" and r["available"] is True


def test_inflight_balanced_on_remote_exception(monkeypatch):
    # M3: 원격 예외가 반복돼도 inflight 누수 0
    _pick_imac(monkeypatch)
    def _boom(nid, cap, payload, hints):
        raise RuntimeError("net down")
    for _ in range(10):
        dispatch.run("detect", b"x", local_fn=lambda: {"available": True}, remote_call=_boom)
    assert nodes.state(nodes.IMAC).inflight == 0


def test_detect_parity_local(monkeypatch):
    _pick_local(monkeypatch)
    expected = {"available": True, "ai_probability": 0.42,
                "label": "human", "scores": [{"label": "human", "score": 0.58}]}
    r = dispatch.run("detect", b"x", local_fn=lambda: dict(expected))
    # 스키마/값 보존 + 로컬 engine 스탬프만 추가
    for k, v in expected.items():
        assert r[k] == v
    assert r["engine"] == "gpu-local"
