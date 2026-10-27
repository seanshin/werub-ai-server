"""원격 노드 프록시 실패가 **이 서버의** 건전성 지표를 오염시키지 않는다.

실측 배경(2026-08-31 라이브 로그 판독):
  iMac이 08-29 03:09 ~ 08-31 09:36 KST(54시간) 미도달인 동안 `/api/mac/stats`·
  `/api/mac/health`가 502를 **31,117건** 반환했다. `_cached_proxy`가 원격 미도달을
  502로 돌려주는데 그 502가 `metrics_reservoir`에 이 서버 자신의 5xx로 기록돼
  `error_rate()`가 **33%**로 올랐고, `Error Rate Spike` 경보가 **43시간 연속 534건**
  울었다(`_perf_alerts`는 `/api/health` 핸들러에서 평가되고 대시보드가 5초마다 폴링 —
  300s 쿨다운은 정상 작동했다. 결함은 쿨다운이 아니라 **조건 자체가 위양성**인 것).

  진짜 비용은 소음이 아니라 **그 43시간 동안 이 서버의 진짜 5xx가 33% 바닥에 묻힌다**는 것.
  오케스트레이터 불변식("노드 미도달 = 기존 동작 100%")과도 정면 충돌한다.

이 파일이 고정하는 것:
  ① `record(upstream=True)`의 5xx는 `error_rate()`에서 빠지되 **사라지지는 않는다**
     (`5xx_upstream` 계급 + `upstream_error_rate()` — 무성 소실 금지)
  ② 이 서버 자신의 5xx는 그대로 잡힌다(탐지기를 죽이지 않았다는 음성 통제)
  ③ 표시는 `_cached_proxy` **한 곳**에 있다 — gpu·prod·his·mac 4계열 8엔드포인트가
     같은 헬퍼를 쓰므로 반쪽 처치가 되지 않는다(같은 클래스 전 경로)
  ④ 캐시 히트분도 표시된다 — 예외 분기가 아니라 최종 status에서 판정하기 때문
  ⑤ 상태 코드 계약(502)은 **그대로**다 — 지표만 고치고 계약은 건드리지 않는다
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import metrics_reservoir as mr


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def _clean():
    mr.reset()
    yield
    mr.reset()


# ── ① upstream 5xx는 자기 건전성에서 빠지되 사라지지 않는다 ──────────────────
def test_upstream_5xx_excluded_from_own_error_rate():
    for _ in range(8):
        mr.record("/api/queue/status", 200, 10)
    mr.record("/api/mac/stats", 502, 5, upstream=True)
    mr.record("/api/mac/health", 502, 5, upstream=True)
    assert mr.error_rate() == 0.0, "원격 노드 미도달은 이 서버의 고장이 아니다"


def test_upstream_5xx_still_visible():
    """버리면 iMac이 죽은 것을 아무도 못 본다 — 별도 계급으로 남는다."""
    for _ in range(8):
        mr.record("/api/queue/status", 200, 10)
    for _ in range(2):
        mr.record("/api/mac/health", 502, 5, upstream=True)
    sc = mr.status_counts()
    assert sc.get("5xx_upstream") == 2
    assert sc.get("5xx", 0) == 0
    assert mr.upstream_error_rate() == 20.0     # 2/10
    assert mr.snapshot()["upstream_error_rate_pct"] == 20.0


def test_incident_shape_would_not_have_alerted():
    """실측 형상 재현: 트래픽 1/3이 원격 프록시 502였던 그 구간.

    종전이면 error_rate=33.33%로 임계(기본 10%)를 넘겨 경보가 울었다.
    """
    for _ in range(2000):
        mr.record("/api/queue/status", 200, 10)
    for _ in range(1000):
        mr.record("/api/mac/stats", 502, 5, upstream=True)
    assert mr.error_rate() == 0.0
    assert mr.upstream_error_rate() == 33.33
    assert mr.error_rate() < 10, "기본 임계 error_rate_pct=10 미만이어야 경보가 안 운다"


def test_upstream_requests_still_count_in_total_and_latency():
    """프록시 요청도 스레드를 똑같이 점유했다 — 분모·지연에서 빼지 않는다."""
    mr.record("/api/mac/stats", 502, 40, upstream=True)
    mr.record("/api/queue/status", 200, 10)
    assert mr.snapshot()["total"] == 2
    assert mr.quantiles()["n"] == 2
    assert mr.quantiles()["max"] == 40.0


def test_upstream_flag_does_not_touch_2xx_or_4xx():
    """정상 응답한 프록시는 평범한 2xx다(분류가 번지지 않는다)."""
    mr.record("/api/mac/stats", 200, 10, upstream=True)
    mr.record("/api/mac/stats", 404, 10, upstream=True)
    sc = mr.status_counts()
    assert sc.get("2xx") == 1 and sc.get("4xx") == 1
    assert "2xx_upstream" not in sc and "4xx_upstream" not in sc


# ── ② 음성 통제: 이 서버 자신의 5xx는 여전히 잡힌다 ─────────────────────────
def test_own_5xx_still_counted():
    """탐지기를 무디게 만든 게 아니라는 통제군 — 이게 깨지면 처치가 결함이다."""
    for _ in range(8):
        mr.record("/api/queue/status", 200, 10)
    mr.record("/api/rag/query", 500, 10)          # 자기 고장
    mr.record("/api/mac/stats", 502, 5, upstream=True)
    assert mr.error_rate() == 10.0                # 1/10 — upstream은 분모에만
    assert mr.status_counts().get("5xx") == 1


def test_default_is_own_error():
    """upstream을 안 넘기면 종전과 동일 — 기본값이 안전한 쪽이다."""
    mr.record("/api/x", 500, 10)
    assert mr.error_rate() == 100.0


# ── ③④⑤ 배선: 표시가 공유 헬퍼 한 곳에 있고 계약은 그대로다 ────────────────
def test_mark_lives_in_shared_proxy_helper():
    """gpu·prod·his·mac 8엔드포인트가 모두 `_cached_proxy`를 쓴다 — 한 곳에 두면 전부 덮인다."""
    s = _src("routes/system.py")
    block = s[s.index("def _cached_proxy"):s.index('@system_bp.route("/api/gpu/stats")')]
    assert "g.upstream_proxy_error = True" in block, "표시는 공유 헬퍼에 있어야 한다"
    # 개별 라우트가 따로 표시하고 있지 않은지(반쪽 처치 방지)
    rest = s[s.index('@system_bp.route("/api/gpu/stats")'):]
    assert "upstream_proxy_error" not in rest


def test_all_remote_proxy_routes_use_the_helper():
    s = _src("routes/system.py")
    for route in ("/api/gpu/stats", "/api/gpu/health", "/api/prod/stats", "/api/prod/health",
                  "/api/his/stats", "/api/his/health", "/api/mac/stats", "/api/mac/health",
                  "/api/mac/status", "/api/mac/image/models"):
        assert '@system_bp.route("%s")' % route in s, route
    head = s[s.index("def gpu_server_stats"):s.index("def server_stats")]
    assert "requests.get" not in head and "urllib.request.urlopen" not in head


def test_mark_is_decided_on_final_status_not_exception_branch():
    """캐시 히트분(TTL 4s 내 재요청)도 502를 돌려준다 — 예외 분기에만 두면 놓친다."""
    s = _src("routes/system.py")
    block = s[s.index("def _cached_proxy"):s.index('@system_bp.route("/api/gpu/stats")')]
    mark = block.index("g.upstream_proxy_error = True")
    resolve = block.index('data, status, etag = ent["data"]')
    assert resolve < mark, "캐시 해석 이후(=최종 status)에서 판정해야 한다"


def test_status_code_contract_unchanged():
    """지표만 고친다 — 502 계약을 200으로 바꾸지 않았다(외부 소비자 계약 보존)."""
    s = _src("routes/system.py")
    block = s[s.index("def _cached_proxy"):s.index('@system_bp.route("/api/gpu/stats")')]
    assert "status = 502" in block


def test_activity_log_passes_the_flag():
    """표시만 하고 안 넘기면 아무 효과가 없다(릴리즈 노트가 코드보다 앞서는 클래스)."""
    s = _src("app.py")
    m = re.search(r"metrics_reservoir\.record\((.{0,200}?)\)\n", s, re.S)
    assert m, "record 호출부를 찾지 못했다"
    assert "upstream_proxy_error" in m.group(1)


def test_metrics_endpoint_exposes_upstream_rate():
    """iMac이 죽은 것은 여전히 보여야 한다 — 다른 이름으로."""
    s = _src("routes/activity.py")
    assert "ollama_http_upstream_error_rate_pct" in s
    assert "upstream_error_rate()" in s


def test_perf_dict_exposes_upstream_rate():
    s = _src("routes/system.py")
    assert 'perf["upstream_error_rate_pct"]' in s
