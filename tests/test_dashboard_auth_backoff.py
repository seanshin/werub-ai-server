"""대시보드 401 폴링 폭주 가드 (v2.125.39) — 소스 동결.

🔴 실측(2026-09-03): 세션 만료된 대시보드 탭이 원격 노드 엔드포인트 4개를 5초마다
영원히 폴링 — 2시간에 401 **5,760건**(단일 IP), api_activity 2000행 창이 28분으로
좁아져 포렌식 가치를 깎았다. 처치=연속 401 시 폴링 5분 백오프+재로그인 안내+
재인증 시 자동 복구(클라이언트 3면: dashboard·dashboard_all·monitor 원격 폴러).

JS는 pytest로 실행 검증이 안 되므로 이 테스트는 **가드의 존재와 배선점**을 동결한다
(제거·우회 리팩터링이 조용히 가드를 떨구는 것 방지).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    return open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()


def test_dashboard_fetch_goes_through_auth_check():
    src = _read("dashboard.html")
    assert "_authCheck(await fetch(url" in src, "fetchCached가 _authCheck를 우회한다"
    assert "300000" in src and "_authSlowMode" in src


def test_dashboard_all_both_fetches_guarded():
    src = _read("dashboard_all.html")
    assert src.count("_authCheck(r).json()") == 2, "stats·health 두 fetch 모두 가드 경유해야"
    assert "_authSlowMode" in src


def test_monitor_remote_poller_gated():
    src = _read("monitor.html")
    assert "refreshRemoteServer" in src
    i = src.index("async function refreshRemoteServer")
    body = src[i:i + 900]
    assert "_auth401" in body and "301000" not in body and "300000" in body, \
        "refreshRemoteServer 진입 게이트(5분 백오프) 누락"
    assert "status === 401" in body


def test_recovery_resets_counter():
    """재인증 성공 시 자동 복구 — 카운터 리셋이 세 파일 모두에 있어야 한다."""
    for name in ("dashboard.html", "dashboard_all.html", "monitor.html"):
        src = _read(name)
        assert "_auth401 = 0" in src, f"{name}: 성공 경로의 카운터 리셋 누락(복구 불가)"
