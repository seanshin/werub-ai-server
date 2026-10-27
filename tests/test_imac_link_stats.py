"""iMac 연계 대시보드 위임 실적 — 죽은 카운터 회귀 (v2.124.95).

🔴 배경: 위임 경로가 v2.85.0에서 `dispatch.run()`으로 이관됐는데 대시보드는
`apple_worker.stats()`를 계속 읽었다. `apple_worker.delegate_*`는 이관 후 **한 번도
호출되지 않으므로** 카운터가 영구히 0이었고, 화면은 늘 `위임 0 · 폴백 0`이었다.

08-10 실측: detect 3회 중 2회가 실제 위임(`detect|imac-worker|delegated: 2`)됐는데
대시보드는 0을 표시했다. 이 파일은 **집계 소스가 오케스트레이터라는 것**을 고정한다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.orchestrator import metrics
from routes.imac_link import _delegation_stats


@pytest.fixture(autouse=True)
def _clean():
    metrics.reset()
    yield
    metrics.reset()


def test_empty_is_zero_not_missing():
    s = _delegation_stats()
    assert s["delegated"] == 0 and s["fallback"] == 0 and s["local"] == 0
    assert s["recent"] == []


def test_counts_delegation_from_orchestrator():
    metrics.record("detect", "imac-worker", "delegated", 509)
    metrics.record("detect", "imac-worker", "delegated", 1781)
    metrics.record("detect", "gpu-local", "local", 5953)
    s = _delegation_stats()
    assert s["delegated"] == 2
    assert s["local"] == 1
    assert s["fallback"] == 0


def test_counts_fallback_and_error_separately():
    metrics.record("stt", "imac-worker", "fallback", 12)
    metrics.record("stt", "imac-worker", "error", 3)
    s = _delegation_stats()
    assert s["fallback"] == 1 and s["error"] == 1 and s["delegated"] == 0


def test_aggregates_across_capabilities():
    metrics.record("detect", "imac-worker", "delegated", 100)
    metrics.record("stt", "imac-worker", "delegated", 200)
    assert _delegation_stats()["delegated"] == 2


def test_recent_keeps_frontend_contract():
    """화면(main.js)은 `kind`·`capability`를 읽는다 — 계약을 깨면 '최근'이 사라진다."""
    metrics.record("detect", "imac-worker", "delegated", 509)
    r = _delegation_stats()["recent"][0]
    assert r["kind"] == "delegated"
    assert r["capability"] == "detect"
    assert r["node"] == "imac-worker" and r["ms"] == 509 and r["ts"]


def test_recent_is_capped():
    for i in range(30):
        metrics.record("detect", "imac-worker", "delegated", i)
    assert len(_delegation_stats()["recent"]) <= 10


def test_unknown_decision_does_not_crash():
    metrics.record("detect", "imac-worker", "weird-decision", 1)
    s = _delegation_stats()
    assert s["delegated"] == 0            # 모르는 값은 세지 않는다(조용히 무시)


def test_node_id_with_separator_is_parsed_by_last_field():
    """키는 `cap|node|decision` — decision은 **마지막** 필드로 읽어야 한다."""
    metrics.record("detect", "imac|worker", "delegated", 1)
    assert _delegation_stats()["delegated"] == 1


def test_metrics_failure_is_graceful(monkeypatch):
    """실적은 부가 정보 — 실패해도 상태 조회를 막지 않는다."""
    monkeypatch.setattr(metrics, "snapshot", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    s = _delegation_stats()
    assert s == {"delegated": 0, "fallback": 0, "local": 0, "recent": []}


def test_legacy_apple_worker_is_no_longer_the_source():
    """레거시 카운터를 되살려도 대시보드 값은 오케스트레이터를 따른다(회귀 가드)."""
    from core import apple_worker
    apple_worker._stats["delegated"] = 999
    try:
        metrics.record("detect", "imac-worker", "delegated", 1)
        assert _delegation_stats()["delegated"] == 1
    finally:
        apple_worker._stats["delegated"] = 0
