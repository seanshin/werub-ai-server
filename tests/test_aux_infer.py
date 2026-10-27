"""보조 추론 사이드카 클라이언트 회귀 테스트 — core/aux_infer.py.

기본 off=기존 동작 불변, 미도달/예외=graceful None(호출자 폴백), engine 강등,
OpenAI 응답 파싱. 사이드카 미기동에서도 전부 안전함을 보장.

실행: venv/bin/python -m pytest tests/test_aux_infer.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import aux_infer  # noqa: E402


class _Resp:
    def __init__(self, status=200, json_data=None):
        self.status_code = status
        self._json = json_data or {}

    def json(self):
        return self._json


@pytest.fixture
def cfg(monkeypatch):
    """load_config 를 제어하는 헬퍼."""
    store = {}
    monkeypatch.setattr(aux_infer, "load_config", lambda: store)
    return store


# ── 기본 off = 기존 동작 불변 ────────────────────────────────────────────────

def test_disabled_by_default(cfg):
    assert aux_infer.enabled() is False
    assert aux_infer._conf() is None
    assert aux_infer.engine() == "ollama"


def test_engine_downgrade_when_disabled(cfg):
    cfg["medical_vision_engine"] = "dual"  # 사이드카 비활성인데 dual 요청
    assert aux_infer.engine() == "ollama"   # 안전 강등


def test_invalid_engine_value(cfg):
    cfg.update({"aux_infer_enabled": True, "medical_vision_engine": "bogus"})
    assert aux_infer.engine() == "ollama"


def test_engine_sidecar_when_enabled(cfg):
    cfg.update({"aux_infer_enabled": True, "medical_vision_engine": "sidecar"})
    assert aux_infer.engine() == "sidecar"
    assert aux_infer.enabled() is True


# ── status: 절대 raise 안 함 ─────────────────────────────────────────────────

def test_status_disabled(cfg):
    assert aux_infer.status() == {"enabled": False, "reachable": False}


def test_status_unreachable_graceful(cfg, monkeypatch):
    cfg["aux_infer_enabled"] = True
    import requests

    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", _boom)
    s = aux_infer.status()
    assert s["enabled"] is True and s["reachable"] is False and "error" in s


def test_status_reachable(cfg, monkeypatch):
    cfg.update({"aux_infer_enabled": True, "aux_infer_url": "http://x:8191"})
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _Resp(200, {"data": [{"id": "qwen3-vl"}]}))
    s = aux_infer.status()
    assert s["reachable"] is True and s["models"] == ["qwen3-vl"]


# ── medical_vision_read: 실패는 전부 None(폴백) ──────────────────────────────

def test_read_none_when_disabled(cfg):
    assert aux_infer.medical_vision_read("aGVsbG8=", "판독") is None


def test_read_none_on_empty_input(cfg):
    cfg["aux_infer_enabled"] = True
    assert aux_infer.medical_vision_read("", "판독") is None
    assert aux_infer.medical_vision_read("aGVsbG8=", "  ") is None


def test_read_none_on_exception(cfg, monkeypatch):
    cfg["aux_infer_enabled"] = True
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
    assert aux_infer.medical_vision_read("aGVsbG8=", "판독") is None


def test_read_none_on_non_200(cfg, monkeypatch):
    cfg["aux_infer_enabled"] = True
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(503, {}))
    assert aux_infer.medical_vision_read("aGVsbG8=", "판독") is None


def test_read_none_on_empty_content(cfg, monkeypatch):
    cfg["aux_infer_enabled"] = True
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(
        200, {"choices": [{"message": {"content": "   "}}]}))
    assert aux_infer.medical_vision_read("aGVsbG8=", "판독") is None


def test_read_success(cfg, monkeypatch):
    cfg["aux_infer_enabled"] = True
    import requests
    captured = {}

    def _post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp(200, {"choices": [{"message": {"content": "소견: 정상"}}]})

    monkeypatch.setattr(requests, "post", _post)
    out = aux_infer.medical_vision_read("aGVsbG8=", "이 영상 판독", system="의료 AI")
    assert out == "소견: 정상"
    # OpenAI 멀티모달 계약 검증
    assert captured["url"].endswith("/v1/chat/completions")
    msgs = captured["payload"]["messages"]
    assert msgs[0]["role"] == "system"
    content = msgs[1]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
