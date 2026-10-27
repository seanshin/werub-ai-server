"""registry — capability 선언 로드/reload (node-orchestrator Phase 1)."""
from core.orchestrator import registry


def test_defaults_have_detect_mark():
    caps = registry.all_capabilities()
    assert "detect" in caps and "mark" in caps


def test_capability_fields():
    c = registry.capability("detect")
    assert c.resource == "light"
    assert c.worker_route == "/worker/detect/image"
    assert c.payload_kind == "image"
    assert c.policy in ("auto", "prefer_imac", "prefer_gpu", "gpu_only")


def test_unknown_capability_none():
    assert registry.capability("no_such_cap") is None


def test_scheduler_cfg_keys():
    s = registry.scheduler_cfg()
    for k in ("w_inflight", "w_latency", "min_dwell_ms", "breaker_fails", "breaker_open_ms"):
        assert k in s


def test_reload_returns_summary():
    out = registry.reload()
    assert "detect" in out["capabilities"]
    assert "breaker_fails" in out["scheduler"]
