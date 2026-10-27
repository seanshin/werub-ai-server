"""공개판 — 로컬 우회(local_bypass)를 끄면 127.0.0.1 요청도 인증을 거친다.

앞단 프록시가 전달 헤더를 붙이지 않으면 바깥 요청도 127.0.0.1 로 보이므로, 공개판은 이 우회를 기본으로 끈다.
(테스트 공통 설정은 기존 시험을 위해 AI_LOCAL_BYPASS=1 로 켜 두므로, 여기서는 값을 직접 바꿔 두 경우를 본다.)
"""
import pytest

import app as A


def _client(app, monkeypatch, bypass):
    monkeypatch.setitem(A.DEFAULT_CONFIG, "local_bypass", bypass)
    A._config_cache["data"] = None
    return app.test_client()


def test_local_request_without_bypass_is_not_admin(app, monkeypatch):
    with _client(app, monkeypatch, False) as c:  # 세션 없음 · 전달 헤더 없음 = 프록시가 헤더를 안 붙인 바깥 요청과 같은 모양
        assert c.get("/api/config").status_code in (302, 401, 403)
    A._config_cache["data"] = None


def test_local_request_with_bypass_is_admin(app, monkeypatch):
    with _client(app, monkeypatch, True) as c:
        assert c.get("/api/config").status_code == 200
    A._config_cache["data"] = None
