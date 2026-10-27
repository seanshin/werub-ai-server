"""CSRF Origin 방어심층(L4) — 세션 쿠키 요청의 Origin 화이트리스트 검증.

SameSite=Lax 주 방어의 이중화. API 클라(Bearer)는 세션 분기 미도달이라 영향 없음.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ctx(headers=None, local=False):
    import flask
    import app as A
    app_obj = flask.Flask(__name__)
    A._app_origin_local = local
    return app_obj.test_request_context("/api/keys/1/toggle", method="POST",
                                        headers=headers or {})


def test_origin_allowed_passes(monkeypatch):
    import app as A
    monkeypatch.setattr(A, "is_local_request", lambda: False)
    monkeypatch.setattr(A, "load_config",
                        lambda: {"allowed_origins": ["https://example.org", "http://localhost:8585"]})
    with _ctx({"Origin": "https://example.org"}):
        assert A._csrf_origin_ok() is True


def test_cross_origin_rejected(monkeypatch):
    import app as A
    monkeypatch.setattr(A, "is_local_request", lambda: False)
    monkeypatch.setattr(A, "load_config", lambda: {"allowed_origins": ["https://example.org"]})
    with _ctx({"Origin": "https://evil.example.com"}):
        assert A._csrf_origin_ok() is False


def test_missing_origin_passes(monkeypatch):
    """Origin/Referer 부재(same-origin 일부·구형)는 통과 — SameSite가 주 방어."""
    import app as A
    monkeypatch.setattr(A, "is_local_request", lambda: False)
    monkeypatch.setattr(A, "load_config", lambda: {"allowed_origins": ["https://example.org"]})
    with _ctx({}):
        assert A._csrf_origin_ok() is True


def test_local_request_bypass(monkeypatch):
    import app as A
    monkeypatch.setattr(A, "is_local_request", lambda: True)
    monkeypatch.setattr(A, "load_config", lambda: {"allowed_origins": []})
    with _ctx({"Origin": "https://anything.com"}):
        assert A._csrf_origin_ok() is True


def test_referer_fallback(monkeypatch):
    """Origin 없고 Referer만 있으면 Referer로 검증."""
    import app as A
    monkeypatch.setattr(A, "is_local_request", lambda: False)
    monkeypatch.setattr(A, "load_config", lambda: {"allowed_origins": ["https://example.org"]})
    with _ctx({"Referer": "https://example.org/ollama/admin"}):
        assert A._csrf_origin_ok() is True
    with _ctx({"Referer": "https://evil.com/x"}):
        assert A._csrf_origin_ok() is False
