"""core/apple_worker.py 폴백 계약 테스트 — 전 플랫폼 (mock 기반, 네트워크 0).

핵심 보증: 어떤 실패 상황에서도 None 반환 (예외 전파 금지) → GPU 서버 기존 경로 보존.
"""
import sys
import types

import pytest

from core import apple_worker


@pytest.fixture(autouse=True)
def reset_cache():
    apple_worker._health_cache.update({"ts": 0.0, "ok": False, "capabilities": []})
    yield
    apple_worker._health_cache.update({"ts": 0.0, "ok": False, "capabilities": []})


def _set_config(monkeypatch, cfg: dict):
    import core.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)


FULL_CFG = {
    "apple_worker_enabled": True,
    "apple_worker_url": "http://localhost:8587",
    "apple_worker_token": "tok",
    "apple_worker_delegate": ["detect"],
}


def test_config_absent_returns_none(monkeypatch):
    """config 미설정 = 완전 휴면 (기존 GPU 동작 100%)."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    _set_config(monkeypatch, {})
    assert apple_worker.detect(b"img") is None
    assert apple_worker.available("detect") is False


def test_darwin_guard(monkeypatch):
    """iMac(Darwin)에서는 config가 있어도 위임 불가 — 자기 위임 차단."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", True)
    _set_config(monkeypatch, FULL_CFG)
    assert apple_worker.detect(b"img") is None
    assert apple_worker.available("detect") is False


def test_capability_not_delegated(monkeypatch):
    """delegate 목록에 없는 기능은 위임 안 함."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    _set_config(monkeypatch, {**FULL_CFG, "apple_worker_delegate": ["embed"]})
    assert apple_worker.available("detect") is False


def test_health_failure_returns_none(monkeypatch):
    """워커 다운(연결 거부) → None (예외 전파 금지)."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    _set_config(monkeypatch, FULL_CFG)

    fake_requests = types.ModuleType("requests")
    def _raise(*a, **k):
        raise ConnectionError("refused")
    fake_requests.get = _raise
    fake_requests.post = _raise
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    assert apple_worker.detect(b"img") is None


def test_successful_delegation(monkeypatch):
    """정상 경로: health OK → detect 200 → engine=imac 부착."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    _set_config(monkeypatch, FULL_CFG)

    class Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
        def json(self):
            return self._body

    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda url, timeout: Resp(200, {"ok": True, "capabilities": ["detect", "mark"]})
    fake_requests.post = lambda url, files, headers, timeout: Resp(
        200, {"available": True, "ai_probability": 0.97, "label": "artificial",
              "model": "Organika/sdxl-detector", "device": "mps"})
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    result = apple_worker.detect(b"img")
    assert result is not None
    assert result["engine"] == "imac"
    assert result["ai_probability"] == 0.97


def test_worker_503_returns_none(monkeypatch):
    """워커 503(메모리 가드) → None → 호출측 로컬 폴백."""
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    _set_config(monkeypatch, FULL_CFG)

    class Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
        def json(self):
            return self._body

    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda url, timeout: Resp(200, {"ok": True, "capabilities": ["detect"]})
    fake_requests.post = lambda url, files, headers, timeout: Resp(503, {"error": "memory critical"})
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    assert apple_worker.detect(b"img") is None


def test_empty_image_returns_none(monkeypatch):
    monkeypatch.setattr(apple_worker, "IS_DARWIN", False)
    _set_config(monkeypatch, FULL_CFG)
    assert apple_worker.detect(b"") is None
