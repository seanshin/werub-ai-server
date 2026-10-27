"""v2.125.45 — dedup_call 대기자가 락을 쥔 채 기다리던 직렬화 봉합.

종전: 같은 키 중복 요청이 `_inflight_lock`을 쥔 채 `future.result(timeout=180)`로
대기 → 그동안 **키가 다른** 모든 요청이 락에서 멈췄다(원 요청이 끝나야 풀림).
"""
import threading
import time

import pytest

from core import llm_resilience as lr


@pytest.fixture(autouse=True)
def _clean_inflight():
    lr._inflight.clear()
    yield
    lr._inflight.clear()


def _start(fn, *args):
    out = {}

    def run():
        try:
            out["result"] = fn(*args)
        except Exception as e:  # noqa: BLE001
            out["error"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def test_other_key_not_blocked_while_duplicate_waits():
    """핵심 회귀: 키 A 중복 대기 중에도 키 B는 즉시 진행한다."""
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "A"

    owner, owner_out = _start(lr.dedup_call, "A", slow)
    assert started.wait(2)
    waiter, waiter_out = _start(lr.dedup_call, "A", lambda: "never")
    time.sleep(0.1)  # 대기자가 대기 상태에 들어가게

    t0 = time.monotonic()
    b_thread, b_out = _start(lr.dedup_call, "B", lambda: "B")
    b_thread.join(1.0)
    elapsed = time.monotonic() - t0
    try:
        assert not b_thread.is_alive(), "키 B가 키 A 대기자에게 막혔다(락 쥔 채 대기)"
        assert b_out["result"] == "B"
        assert elapsed < 1.0
    finally:
        release.set()
        owner.join(2)
        waiter.join(2)

    assert owner_out["result"] == "A"
    assert waiter_out["result"] == "A"  # 대기자는 원 요청 결과를 공유


def test_duplicate_shares_single_execution():
    calls = []
    release = threading.Event()

    def fn():
        calls.append(1)
        release.wait(5)
        return 42

    t1, o1 = _start(lr.dedup_call, "K", fn)
    time.sleep(0.05)
    t2, o2 = _start(lr.dedup_call, "K", fn)
    time.sleep(0.05)
    release.set()
    t1.join(2)
    t2.join(2)
    assert len(calls) == 1
    assert o1["result"] == 42 and o2["result"] == 42


def test_owner_exception_reraised_and_waiter_gets_none(caplog):
    release = threading.Event()

    def boom():
        release.wait(5)
        raise ValueError("x")

    t1, o1 = _start(lr.dedup_call, "E", boom)
    time.sleep(0.05)
    t2, o2 = _start(lr.dedup_call, "E", boom)
    time.sleep(0.05)
    with caplog.at_level("WARNING"):
        release.set()
        t1.join(2)
        t2.join(2)
    assert isinstance(o1.get("error"), ValueError)
    assert o2.get("result") is None
    # 무성 소실 금지 — 대기 실패가 로그에 남는다
    assert any("[dedup] 대기 실패" in r.getMessage() for r in caplog.records)
    assert "E" not in lr._inflight


def test_owner_base_exception_releases_waiters():
    """BaseException으로 빠져도 대기자가 180s를 헛기다리지 않는다."""
    release = threading.Event()

    class Abort(BaseException):
        pass

    def aborting():
        release.wait(5)
        raise Abort()

    def owner_run():
        try:
            lr.dedup_call("Z", aborting)
        except Abort:
            pass
    owner = threading.Thread(target=owner_run, daemon=True)
    owner.start()
    time.sleep(0.05)
    waiter, w_out = _start(lr.dedup_call, "Z", lambda: "never")
    time.sleep(0.05)
    t0 = time.monotonic()
    release.set()
    waiter.join(2)
    owner.join(2)
    assert not waiter.is_alive()
    assert time.monotonic() - t0 < 2
    assert w_out.get("result") is None
    assert "Z" not in lr._inflight


def test_key_released_after_completion():
    assert lr.dedup_call("R", lambda: 1) == 1
    assert "R" not in lr._inflight
    assert lr.dedup_call("R", lambda: 2) == 2  # 캐시가 아니라 진행 중 공유만
