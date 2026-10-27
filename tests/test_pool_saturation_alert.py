"""스레드 풀 포화 경보 — in-flight 계측 + 순수 판정 회귀 (v2.124.94).

배경: 종전 `Worker Threads High` 경보가 2026-06-30~08-10 **41일간 6,354건**
(전체 경보의 95%) 발화하고 true positive 0이었다. 원인은 임계(80)가 아니라 **지표**다 —
gthread 풀은 ThreadPoolExecutor라 한 번 만든 스레드를 유지하므로 `worker_threads`는
"풀이 다 만들어졌다"만 말하고 "풀이 다 바쁘다"는 말하지 못한다(30일 실측: threads
구간별 p95 중앙 575/578/575ms 무차이·sse_subscribers 내내 0).

이 파일은 **완화 방향 변경의 통제군**이다: 노이즈가 사라졌는지가 아니라
**실제 포화를 여전히 검출하는지**를 고정한다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading

import pytest

from core import metrics_reservoir as mr
from routes.system import _pool_size, _saturation_finding


@pytest.fixture(autouse=True)
def _clean():
    mr.reset()
    yield
    mr.reset()


# ── in-flight 계측 ──
def test_inflight_enter_leave_symmetric():
    assert mr.inflight()["current"] == 0
    mr.enter(); mr.enter()
    assert mr.inflight()["current"] == 2
    mr.leave(); mr.leave()
    assert mr.inflight()["current"] == 0


def test_inflight_peak_survives_drain():
    """peak은 요청이 다 빠져도 남아야 한다 — 5분 샘플 사이 버스트를 놓치지 않기 위함."""
    for _ in range(7):
        mr.enter()
    for _ in range(7):
        mr.leave()
    f = mr.inflight()
    assert f["current"] == 0 and f["peak"] == 7


def test_inflight_peak_reset_is_opt_in():
    for _ in range(4):
        mr.enter()
    mr.leave()
    assert mr.inflight()["peak"] == 4          # 읽기만 해서는 리셋되지 않음
    assert mr.inflight(reset_peak=True)["peak"] == 4
    assert mr.inflight()["peak"] == 3          # 리셋 후엔 현재값이 새 바닥(진행 중 요청 보존)


def test_leave_without_enter_never_goes_negative():
    """비대칭 호출(before_request가 앞선 훅에 막혀 건너뛴 경우)에도 음수 금지."""
    mr.leave(); mr.leave()
    assert mr.inflight()["current"] == 0


def test_inflight_is_threadsafe():
    def worker():
        for _ in range(200):
            mr.enter()
            mr.leave()
    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert mr.inflight()["current"] == 0       # 누수·중복 반납 없음


def test_snapshot_includes_inflight():
    mr.enter()
    assert mr.snapshot()["inflight"]["current"] == 1


# ── 풀 크기 해석 ──
def test_pool_size_prefers_env(monkeypatch):
    monkeypatch.setenv("GUNICORN_THREADS", "96")
    assert _pool_size({"worker_thread_pool": 32}) == 96


def test_pool_size_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("GUNICORN_THREADS", raising=False)
    assert _pool_size({"worker_thread_pool": 32}) == 32


def test_pool_size_unknown_when_absent(monkeypatch):
    monkeypatch.delenv("GUNICORN_THREADS", raising=False)
    assert _pool_size({}) is None


def test_pool_size_ignores_garbage(monkeypatch):
    monkeypatch.setenv("GUNICORN_THREADS", "not-a-number")
    assert _pool_size({}) is None


# ── 통제군: 실제 포화는 여전히 잡아야 한다 ──
def test_full_pool_is_critical():
    f = _saturation_finding(96, 96)
    assert f is not None and f[2] == "critical"
    assert "96/96" in f[1]


def test_over_pool_is_critical():
    assert _saturation_finding(120, 96)[2] == "critical"


def test_near_pool_warns():
    f = _saturation_finding(77, 96)            # 80.2% ≥ 80%
    assert f is not None and f[2] == "warning"


def test_warn_ratio_boundary_is_inclusive():
    assert _saturation_finding(80, 100, 0.8) is not None
    assert _saturation_finding(79, 100, 0.8) is None


# ── 노이즈였던 상황은 이제 조용해야 한다 ──
def test_warm_pool_with_idle_threads_is_silent():
    """41일간 6,354건을 만든 그 상태: 스레드 96개가 전부 살아 있지만 실제 동시 요청은 2건."""
    assert _saturation_finding(2, 96) is None


def test_moderate_load_is_silent():
    assert _saturation_finding(40, 96) is None


# ── 판정 불가 구간은 침묵(임의 절대 임계로 되돌아가지 않음) ──
def test_unknown_pool_does_not_alert():
    assert _saturation_finding(500, None) is None


def test_missing_sample_does_not_alert():
    assert _saturation_finding(None, 96) is None
