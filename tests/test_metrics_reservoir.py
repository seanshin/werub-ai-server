"""성능 모니터링 지연 리저버 회귀 테스트 (순수 모듈 — flask 무관, iMac 실행 가능)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core import metrics_reservoir as mr


@pytest.fixture(autouse=True)
def _clean():
    mr.reset()
    yield
    mr.reset()


def test_empty_quantiles_are_zero():
    q = mr.quantiles()
    assert q["n"] == 0
    assert q["p50"] == 0.0 and q["p95"] == 0.0 and q["p99"] == 0.0 and q["max"] == 0.0
    assert mr.error_rate() == 0.0


def test_percentiles_basic():
    for i in range(1, 101):           # 1..100 ms
        mr.record("/api/x", 200, i)
    q = mr.quantiles()
    assert q["n"] == 100
    assert q["p50"] == 51.0          # index int(100*0.5)=50 -> value 51
    assert q["p95"] == 96.0
    assert q["max"] == 100.0


def test_error_rate_counts_5xx_only():
    for _ in range(8):
        mr.record("/a", 200, 10)
    mr.record("/a", 401, 10)          # 4xx = 클라이언트, 제외
    mr.record("/a", 500, 10)          # 5xx = 서버오류, 포함
    # total=10, 5xx=1 -> 10%
    assert mr.error_rate() == 10.0
    sc = mr.status_counts()
    assert sc["2xx"] == 8 and sc["4xx"] == 1 and sc["5xx"] == 1


def test_cardinality_cap():
    # _TOP_ENDPOINTS 초과 path는 per-path 리저버 미생성(폭주 방지), 전체엔 여전히 집계
    for i in range(mr._TOP_ENDPOINTS + 5):
        mr.record(f"/p{i}", 200, 5)
    assert len(mr._ep_durations) == mr._TOP_ENDPOINTS
    # 전체 리저버엔 전부 반영
    assert mr.quantiles()["n"] == mr._TOP_ENDPOINTS + 5


def test_per_endpoint_quantiles():
    for _ in range(10):
        mr.record("/slow", 200, 1000)
    for _ in range(10):
        mr.record("/fast", 200, 5)
    assert mr.quantiles("/slow")["p50"] == 1000.0
    assert mr.quantiles("/fast")["p50"] == 5.0
    # 미존재 path
    assert mr.quantiles("/nope")["n"] == 0


def test_reservoir_bounded():
    n = mr._MAX + 500
    for i in range(n):
        mr.record("/api/y", 200, i)
    # 전체 리저버는 maxlen으로 제한
    assert mr.quantiles()["n"] == mr._MAX


def test_record_invalid_ms_ignored():
    mr.record("/a", 200, None)
    mr.record("/a", 200, "bad")
    assert mr.quantiles()["n"] == 0


def test_top_endpoints():
    for _ in range(5):
        mr.record("/hot", 200, 1)
    for _ in range(2):
        mr.record("/cold", 200, 1)
    top = mr.top_endpoints(1)
    assert top[0][0] == "/hot" and top[0][1] == 5


def test_snapshot_shape():
    mr.record("/a", 200, 100)
    mr.record("/a", 500, 200)
    s = mr.snapshot()
    # upstream_error_rate_pct 추가(2026-08-31): 원격 노드 프록시 미도달을 이 서버의
    # 5xx와 분리해 노출한다 — tests/test_upstream_proxy_error_isolation.py 참조.
    assert set(s.keys()) == {"latency", "error_rate_pct", "upstream_error_rate_pct",
                             "status", "total", "inflight"}
    assert s["total"] == 2
    assert s["latency"]["n"] == 2


def test_meeting_backlog_threshold_default_is_below_queue_cap():
    """대기열 경보는 상한 도달 **전에** 떠야 의미가 있다(도달하면 이미 접수 거절)."""
    from core.meeting_pipeline import MAX_QUEUE

    default_warn = max(10, MAX_QUEUE // 3)
    assert 0 < default_warn < MAX_QUEUE
