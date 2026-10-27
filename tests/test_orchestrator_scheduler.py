"""scheduler — pick() 하드 게이트 + 점수 + 폴백 (Phase 1)."""
import time

import pytest

from core.orchestrator import nodes, registry, scheduler


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 0
        s = nodes._states[nodes.IMAC]
        s.inflight = 0; s.fails = 0; s.breaker = "closed"; s.breaker_until = 0.0
        s.latency_ewma_ms = {}; s.reachable = True; s.capabilities = ["detect"]
        s.mem_pressure = "normal"; s.thermal = "nominal"; s.locks_heavy = None
        nodes._last_pick.clear()
        nodes._health_ts[nodes.IMAC] = time.time()   # 네트워크 refresh 스킵
    # 기본: imac 활성 + detect 위임
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": ["detect"]})
    yield


def _imac(**kw):
    with nodes._LOCK:
        s = nodes._states[nodes.IMAC]
        for k, v in kw.items():
            setattr(s, k, v)


def test_unknown_cap_local():
    assert scheduler.pick("no_such") == nodes.GPU_LOCAL


def test_gpu_only_policy(monkeypatch):
    monkeypatch.setattr(registry, "capability",
                        lambda n: registry.Capability(name=n, policy="gpu_only"))
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_not_in_delegate_local(monkeypatch):
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": []})   # detect 미위임
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_unreachable_local():
    _imac(reachable=False)
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_cap_not_in_manifest_local():
    _imac(capabilities=["mark"])      # health manifest에 detect 없음
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_mem_critical_gate():
    _imac(mem_pressure="critical")
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_thermal_throttled_gate():
    _imac(thermal="throttled")
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_thermal_unknown_passes():
    # 신호 부재는 게이트하지 않음 + 로컬 부하로 imac 우위 유도
    _imac(thermal="unknown")
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 5
    assert scheduler.pick("detect") == nodes.IMAC


def test_breaker_open_gate():
    _imac(breaker="open", breaker_until=time.time() + 60)
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_breaker_expired_probe():
    _imac(breaker="open", breaker_until=time.time() - 1)   # 만료 → probe 허용
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 5
    assert scheduler.pick("detect") == nodes.IMAC


def test_prefer_imac_chosen():
    _imac()
    with nodes._LOCK:
        nodes._states[nodes.IMAC].inflight = 0
        nodes._states[nodes.GPU_LOCAL].inflight = 3   # 로컬 부하 → imac 우위
    assert scheduler.pick("detect") == nodes.IMAC


def test_darwin_no_remote(monkeypatch):
    monkeypatch.setattr(nodes, "imac_conf", lambda: None)   # Darwin/미설정
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def _force_auto(monkeypatch):
    # hysteresis는 부하기반 동률 근처에서만 의미 → 정책을 auto로 고정해 정책 독립 검증
    monkeypatch.setattr(registry, "capability",
                        lambda n: registry.Capability(name=n, policy="auto", worker_route="/w"))


def test_hysteresis_holds_previous(monkeypatch):
    _force_auto(monkeypatch)
    # imac 약간 열위(inflight=1 → 점수 -1.0, 로컬 0). 차 1.0 < margin 2.0, dwell 이내 → 직전(IMAC) 유지
    _imac(inflight=1)
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 0
        nodes._last_pick["detect"] = (nodes.IMAC, time.time())
    assert scheduler.pick("detect") == nodes.IMAC


def test_hysteresis_switches_when_margin_exceeded(monkeypatch):
    _force_auto(monkeypatch)
    # imac 크게 열위(inflight=5 → 점수 -5.0). 차 5.0 > margin 2.0 → 전환(gpu-local)
    _imac(inflight=5)
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 0
        nodes._last_pick["detect"] = (nodes.IMAC, time.time())
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_stt_dormant_local():
    # stt가 delegate에 없으면(현 운영 기본 [detect,mark]) 로컬 (Phase 2 휴면)
    assert scheduler.pick("stt") == nodes.GPU_LOCAL


def test_stt_routes_when_advertised(monkeypatch):
    # delegate에 stt 추가 + 워커 caps에 stt + 로컬 부하 → imac 위임 (워커 구현 후 활성)
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": ["detect", "mark", "stt"]})
    _imac(capabilities=["detect", "mark", "stt"])
    with nodes._LOCK:
        nodes._states[nodes.GPU_LOCAL].inflight = 3
    assert scheduler.pick("stt") == nodes.IMAC


def test_stt_heavy_lock_gate(monkeypatch):
    # stt는 heavy — 워커가 heavy 작업 점유 중이면 제외 → 로컬
    monkeypatch.setattr(nodes, "imac_conf", lambda: {
        "url": "http://x", "token": "t", "delegate": ["detect", "mark", "stt"]})
    _imac(capabilities=["detect", "mark", "stt"], locks_heavy="stt")
    assert scheduler.pick("stt") == nodes.GPU_LOCAL
