"""HR Law 보안 회귀 (2026-07-21 감사).

H-1 테넌트 자가 등록(인가 누락)·쿼터 상한 · M-1 키 해싱·재열람 금지 ·
M-2 쿼리스트링 키 수용 제거 · admin 판정 fail-closed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routes.hr_law as H  # noqa: E402


class _Req:
    def __init__(self, headers=None, args=None):
        self.headers = headers or {}
        self.args = args or {}


def test_admin_check_fails_closed_when_not_injected(monkeypatch):
    """주입 실패가 조용한 허용이 되면 안 된다(과거 NameError→500 클래스)."""
    monkeypatch.setattr(H, "_is_admin_request", None)
    assert H.is_admin_request() is False


def test_admin_check_uses_injected(monkeypatch):
    monkeypatch.setattr(H, "_is_admin_request", lambda: True)
    assert H.is_admin_request() is True
    monkeypatch.setattr(H, "_is_admin_request", lambda: False)
    assert H.is_admin_request() is False


def test_tenant_key_hashed_not_reversible():
    k = "hr-" + "a" * 32
    h = H._hash_tenant_key(k)
    assert h != k and len(h) == 64 and h == H._hash_tenant_key(k)


def test_resolve_tenant_rejects_query_string_key(monkeypatch):
    """M-2: URL 쿼리스트링 키는 더 이상 수용하지 않는다(로그·히스토리 잔류)."""
    called = {"db": 0}

    def _fake_db():
        called["db"] += 1
        raise AssertionError("헤더 없이 DB 조회가 일어나면 안 된다")

    monkeypatch.setattr(H, "_get_hr_law_db", _fake_db)
    assert H._resolve_tenant(_Req(args={"tenant_key": "hr-leaked"})) is None
    assert called["db"] == 0


def test_resolve_tenant_none_without_header(monkeypatch):
    monkeypatch.setattr(H, "_get_hr_law_db",
                        lambda: (_ for _ in ()).throw(AssertionError("호출 금지")))
    assert H._resolve_tenant(_Req()) is None


def test_usage_aggregate_requires_admin(monkeypatch):
    """M-3: 테넌트 해석 실패 시 전체 사용량으로 떨어지던 fail-open 차단.

    Blueprint는 앱에 이미 등록돼 재등록이 불가하므로 뷰 함수를 직접 호출한다.
    """
    import flask
    monkeypatch.setattr(H, "_resolve_tenant", lambda req: None)
    monkeypatch.setattr(H, "_is_admin_request", lambda: False)
    app = flask.Flask(__name__)
    with app.test_request_context("/api/hr-law/tenant/usage",
                                  headers={"X-Tenant-Key": "hr-bogus"}):
        resp = H.hr_law_tenant_usage.__wrapped__() \
            if hasattr(H.hr_law_tenant_usage, "__wrapped__") else H.hr_law_tenant_usage()
    assert resp[1] == 401


def test_usage_aggregate_allowed_for_admin(monkeypatch):
    """관리자에게는 전체 사용량이 계속 열려 있어야 한다(기능 회귀 방지)."""
    import flask
    monkeypatch.setattr(H, "_resolve_tenant", lambda req: None)
    monkeypatch.setattr(H, "_is_admin_request", lambda: True)

    class _C:
        def execute(self, *a, **k):
            return self

        def fetchall(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(H, "_get_hr_law_db", lambda: _C())
    app = flask.Flask(__name__)
    with app.test_request_context("/api/hr-law/tenant/usage"):
        fn = getattr(H.hr_law_tenant_usage, "__wrapped__", H.hr_law_tenant_usage)
        resp = fn()
    assert not isinstance(resp, tuple)          # 200 경로(상태코드 튜플 아님)
