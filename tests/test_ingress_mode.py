"""외부 유입 경로 점검 모드 회귀 (v2.125.41) — 2026-09-09 은퇴한 터널이 만든 위양성 경보.

라이브에서 관측된 것: example.org의 유입이 Linode nginx→SSH 역터널에서 Cloudflare Tunnel→
Caddy→127.0.0.1:8585로 옮겨가면서 `ollama-tunnel`이 09:50에 중지·비활성화됐고, 종전 점검은
그 유닛 하나만 봤기 때문에 **당일 critical 경보 132건**과 5분 주기 watchdog WARN이 쌓였다.

🔑 처치의 규율은 "검사를 지우지 않는다"이다 — 지우면 진짜로 유입이 끊겼을 때도 조용하다.
그래서 모드를 갈랐고, 이 파일은 **각 모드가 무엇을 근거로 ok를 말하는지**를 고정한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    from routes import system as S
    S._INGRESS_CACHE.update(at=0.0, val=None)
    yield
    S._INGRESS_CACHE.update(at=0.0, val=None)


def _with_cfg(monkeypatch, cfg):
    from routes import system as S
    monkeypatch.setattr(S, "_load_config", lambda: cfg)


def test_default_mode_is_ssh_tunnel(monkeypatch):
    """설정이 없으면 종전 동작(iMac autossh 구성 보호)."""
    from routes import system as S

    _with_cfg(monkeypatch, {})
    ok, detail, mode = S._check_ingress()
    assert mode == "ssh_tunnel"


def test_none_mode_reports_not_monitored_and_never_alerts(monkeypatch):
    from routes import system as S

    _with_cfg(monkeypatch, {"ingress_mode": "none"})
    ok, detail, mode = S._check_ingress()
    assert (ok, mode) == (True, "none")
    assert "not monitored" in detail          # ok=True를 근거 없이 주장하지 않는다


def test_proxy_mode_ok_when_edge_listener_is_up(monkeypatch):
    """리스너가 살아 있으면 ok — 실제 소켓을 하나 열어 확인한다."""
    import socket
    from routes import system as S

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        _with_cfg(monkeypatch, {"ingress_mode": "proxy",
                                "ingress_probe_addr": f"127.0.0.1:{port}"})
        ok, detail, mode = S._check_ingress()
        assert (ok, mode) == (True, "proxy"), detail
        assert str(port) in detail
    finally:
        srv.close()


def test_proxy_mode_down_when_nothing_listens(monkeypatch):
    from routes import system as S

    # 닫힌 포트(바인드 후 즉시 반납) — 유입 경로가 끊긴 상황
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()

    _with_cfg(monkeypatch, {"ingress_mode": "proxy",
                            "ingress_probe_addr": f"127.0.0.1:{port}"})
    ok, detail, mode = S._check_ingress()
    assert ok is False and mode == "proxy"
    assert "unreachable" in detail


def test_proxy_probe_does_not_call_our_own_http_surface():
    """자기 자신에게 HTTP를 되쏘면 /api/health 폴링마다 gthread를 두 배로 먹는다.

    (SSE 스레드 고갈 인시던트와 같은 모양 — TCP 연결까지만 한다.)
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "routes", "system.py")).read()
    i = src.index("def _check_ingress")
    seg = src[i:src.index("@system_bp.route(\"/api/health\")", i)]
    assert "socket.create_connection" in seg
    assert "requests.get" not in seg and "urlopen" not in seg


def test_health_reports_mode_and_suppresses_alert_when_not_monitored(monkeypatch):
    """none 모드에서는 tunnel 경보가 발생하지 않는다."""
    import app as A
    from routes import system as S

    _with_cfg(monkeypatch, {"ingress_mode": "none"})
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda *a, **k: sent.append(a))

    r = A.app.test_client().get("/api/health")
    body = r.get_json()
    assert body["checks"]["tunnel"]["mode"] == "none"
    assert body["checks"]["tunnel"]["ok"] is True
    assert not any("Ingress" in (a[0] or "") or "Tunnel" in (a[0] or "") for a in sent)


def test_watchdog_scripts_respect_ingress_mode():
    """스크립트도 같은 스위치를 본다(코드만 고치면 5분마다 WARN이 계속 쌓인다)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("watchdog.sh", "health_check.sh"):
        src = open(os.path.join(root, "scripts", name)).read()
        assert "ingress_mode" in src, name
        assert "INGRESS_MODE" in src, name


# ── 새 유입 경로가 load-bearing으로 만든 것: local bypass fail-closed ────────
# 2026-09-09부터 엣지(Caddy)가 **같은 호스트**에 있어 외부 요청도 127.0.0.1에서 도착한다.
# 즉 `is_local_request()`의 "포워딩 헤더가 있으면 외부" 규칙 하나가 인터넷 전체와 무인증
# 관리자 표면 사이를 막는다. 엣지 설정에서 X-Forwarded-For를 떼면 전면 우회가 열린다.
@pytest.mark.parametrize("header", ["X-Forwarded-For", "CF-Connecting-IP", "X-Real-IP"])
def test_forwarding_header_from_loopback_is_not_local(header):
    import app as A

    with A.app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                    headers={header: "203.0.113.9"}):
        assert A.is_local_request() is False, header


def test_bare_loopback_is_still_local():
    """진짜 로컬 호출(내부 스크립트·워커)은 종전대로 통과해야 한다."""
    import app as A

    with A.app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert A.is_local_request() is True


def test_admin_surface_401_through_edge_shaped_request():
    """엣지 모양(루프백 + XFF)의 요청은 관리자 표면에서 401 — 라이브 실측과 같은 결과."""
    import app as A

    r = A.app.test_client().get("/api/keys", headers={"X-Forwarded-For": "203.0.113.9"},
                                environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401, r.data[:200]
