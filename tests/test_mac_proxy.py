"""iMac 프록시 라우트 — 대시보드/모니터가 이 서버만 부르게 하는 계약.

배경(2026-08-12 iMac팀 요청): 화면이 iMac을 **직접**(`/ollama-mac` nginx 경로) 부르고
있었다. 브라우저가 여러 오리진을 직접 때리면 CORS·혼합 콘텐츠·터널 노출이 각각 문제가
되고, 무엇보다 **'한 곳만 열려 있으면 되는' 구조**가 깨진다(gpu·prod·his는 이미 프록시다).

🔑 **주소를 실측으로 확정했다**: iMac Flask admin은 **8586**이다.
   8587은 **워커 전용**이라 `/api/health`·`/api/server/stats`가 **404**다 —
   거기로 붙였으면 조용히 404만 받았을 것이다.

이 파일이 고정하는 것:
  ① 4개 라우트가 존재하고 기존 프록시와 **같은 패턴**(TTL 캐시·ETag)을 쓴다
  ② 업스트림이 8586(admin)이지 8587(워커)이 아니다
  ③ 화면 어디에도 `MAC_BASE`/`ollama-mac` 직접 호출이 남아 있지 않다
"""
import os
import re

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── ① 라우트 존재 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("route", [
    "/api/mac/stats", "/api/mac/health", "/api/mac/status", "/api/mac/image/models",
])
def test_route_declared(route):
    assert '@system_bp.route("%s")' % route in _src("routes/system.py")


def test_routes_use_shared_proxy_helper():
    """기존 prod/his와 같은 헬퍼를 써야 TTL 캐시·ETag·304가 따라온다.
    직접 requests로 짜면 그 셋이 조용히 빠진다."""
    s = _src("routes/system.py")
    block = s[s.index("def mac_server_stats"):s.index("def server_stats")]
    assert block.count("_cached_proxy") == 4, "4개 라우트가 모두 헬퍼를 써야 한다"
    assert "requests.get" not in block


def test_health_and_status_declare_failure_shape():
    """업스트림이 죽었을 때 화면이 무엇을 받을지 정해 둔다(빈 응답이면 화면이 깨진다)."""
    s = _src("routes/system.py")
    block = s[s.index("def mac_health_check"):s.index("def server_stats")]
    assert '"healthy": False' in block
    assert '"models": []' in block


# ── ② 업스트림 주소 ─────────────────────────────────────────────────────────
def test_upstream_is_admin_port_not_worker():
    """🔴 8587은 워커라 두 경로가 404다 — 실측으로 8586을 확정했다."""
    s = _src("routes/system.py")
    m = re.search(r'MAC_STATS_URL\s*=\s*os\.environ\.get\(\s*"MAC_STATS_URL"\s*,\s*"([^"]+)"', s)
    assert m, "MAC_STATS_URL이 env 오버라이드 가능해야 한다"
    assert m.group(1).endswith(":8586"), "워커(8587)가 아니라 admin(8586)"


def test_upstream_paths_match_imac_api():
    """프록시 대상 경로가 iMac 실제 API와 같아야 한다(오타는 조용한 404가 된다)."""
    s = _src("routes/system.py")
    for path in ("/api/server/stats", "/api/health", "/api/status", "/api/image/models"):
        assert 'MAC_STATS_URL}%s"' % path in s, path


# ── ③ 화면에 직접 호출 잔재 없음 ────────────────────────────────────────────
@pytest.mark.parametrize("page", [
    "templates/dashboard.html", "templates/dashboard_all.html", "templates/monitor.html",
])
def test_no_direct_imac_origin_in_pages(page):
    """한 곳만 고치면 나머지 화면이 계속 `/ollama-mac`에 매달린다 — 전 화면을 본다."""
    s = _src(page)
    assert "MAC_BASE" not in s
    assert "ollama-mac" not in s


@pytest.mark.parametrize("page,needle", [
    ("templates/dashboard.html", "BASE+'/api/mac/stats'"),
    ("templates/dashboard_all.html", "BASE+'/api/mac/stats'"),
    ("templates/monitor.html", "BASE + '/api/mac/status'"),
])
def test_pages_call_proxy(page, needle):
    assert needle in _src(page)
