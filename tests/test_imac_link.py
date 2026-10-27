"""routes/imac_link.py + apple_worker 실적 카운터 테스트 — 전 플랫폼 (mock, 네트워크 0)."""
import sys
import types

import pytest
from flask import Flask

from core import apple_worker
from routes.imac_link import imac_link_bp, init_imac_link


@pytest.fixture()
def client(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(imac_link_bp)
    init_imac_link(imac_ollama_base="http://localhost:8586",
                   lightweight_models={"llama3.2:3b"})
    # 위임 config 비활성 기본
    import core.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", lambda: {})
    return app.test_client()


def _mock_requests(monkeypatch, *, ollama_ok=True, worker_ok=True, worker_health=None):
    class Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
        def json(self):
            return self._body

    def get(url, timeout):
        if "8586" in url:
            if not ollama_ok:
                raise ConnectionError("refused")
            return Resp(200, {"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:7b"}]})
        if "/worker/health" in url:
            if not worker_ok:
                raise ConnectionError("refused")
            return Resp(200, worker_health or {
                "ok": True, "version": "2.81.0", "capabilities": ["detect", "mark"],
                "memory": {"pressure": "normal", "free_gb": 12.0},
                "thermal": "nominal", "locks": {"heavy_task": None},
                "expose": "off", "link": {"tunnel_11435_ok": True},
            })
        raise AssertionError(url)

    fake = types.ModuleType("requests")
    fake.get = get
    monkeypatch.setitem(sys.modules, "requests", fake)


def test_status_all_up(client, monkeypatch):
    _mock_requests(monkeypatch)
    d = client.get("/api/imac-link/status").get_json()
    assert d["tunnels"]["8586_ollama"]["ok"] is True
    assert d["tunnels"]["8587_worker"]["ok"] is True
    assert d["tunnels"]["11435_reverse"] is True
    assert d["worker"]["version"] == "2.81.0"
    assert d["delegation"]["enabled"] is False  # config 없음
    assert "apple_worker_token" not in str(d)   # 시크릿 비노출
    assert d["offload"]["models_ready"] == ["llama3.2:3b"]  # iMac 보유 모델과 교집합


def test_status_all_down(client, monkeypatch):
    """터널 전체 다운 — 오류 없이 ok=false 보고 (UI 신호등)."""
    _mock_requests(monkeypatch, ollama_ok=False, worker_ok=False)
    r = client.get("/api/imac-link/status")
    assert r.status_code == 200
    d = r.get_json()
    assert d["tunnels"]["8586_ollama"]["ok"] is False
    assert d["tunnels"]["8587_worker"]["ok"] is False
    assert d["tunnels"]["11435_reverse"] is None
    assert d["worker"]["version"] is None


def test_delegation_stats_counter(monkeypatch):
    """detect 성공 → delegated 증가, 실패 → fallback 증가 + recent 기록."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    import core.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", lambda: {
        "apple_worker_enabled": True, "apple_worker_url": "http://localhost:8587",
        "apple_worker_token": "t", "apple_worker_delegate": ["detect"]})
    apple_worker._health_cache.update({"ts": 0.0, "ok": False, "capabilities": []})
    base = apple_worker.stats()

    class Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
        def json(self):
            return self._body

    fake = types.ModuleType("requests")
    fake.get = lambda url, timeout: Resp(200, {"ok": True, "capabilities": ["detect"]})
    fake.post = lambda url, files, headers, timeout: Resp(200, {"available": True, "ai_probability": 0.5})
    monkeypatch.setitem(sys.modules, "requests", fake)
    assert apple_worker.detect(b"img") is not None
    s = apple_worker.stats()
    assert s["delegated"] == base["delegated"] + 1
    assert s["recent"][0]["kind"] == "delegated"

    fake.post = lambda url, files, headers, timeout: Resp(503, {"error": "busy"})
    assert apple_worker.detect(b"img") is None
    s = apple_worker.stats()
    assert s["fallback"] == base["fallback"] + 1
    assert s["recent"][0]["kind"] == "fallback"
