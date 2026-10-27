"""공정 큐 대기 신호 헤더 — "조용히 느려지는" 것을 호출자가 알게 한다.

🔴 2026-07-30 Cerno 실측: 단일 API 키로 동시 요청을 던지면 `max_per_user=1`로 완전 직렬화
되는데(N=8에서 마지막 응답 10.15초), **대기열이 흡수해 429가 뜨지 않는다.** 호출자에게는
"그냥 느린 서버"로만 보였다 — 서버는 대기 시간을 알면서 응답에 싣지 않았다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.queue_signal import wait_headers                              # noqa: E402


def test_immediate_request_reports_zero():
    """즉시 처리도 0으로 명시한다 — 헤더 부재와 '대기 없음'을 구분할 수 있어야 한다."""
    h = wait_headers(0)
    assert h["X-Queue-Waited-Ms"] == "0"
    assert "X-Queue-Position" not in h


def test_waited_request_reports_ms_and_position():
    h = wait_headers(3210, position=2)
    assert h["X-Queue-Waited-Ms"] == "3210" and h["X-Queue-Position"] == "2"


def test_active_included_when_given():
    assert wait_headers(0, active=3)["X-Queue-Active"] == "3"
    assert "X-Queue-Active" not in wait_headers(0)


def test_values_are_strings():
    """헤더 값은 문자열이어야 한다(정수를 그대로 넣으면 WSGI에서 터진다)."""
    for v in wait_headers(120, position=1, active=2).values():
        assert isinstance(v, str)


def test_abnormal_values_are_normalized():
    """음수·None·문자열이 헤더로 새어 나가지 않게 한다."""
    assert wait_headers(-5)["X-Queue-Waited-Ms"] == "0"
    assert wait_headers(None)["X-Queue-Waited-Ms"] == "0"
    assert wait_headers("bad")["X-Queue-Waited-Ms"] == "0"
    assert "X-Queue-Position" not in wait_headers(10, position=-3)
    assert "X-Queue-Position" not in wait_headers(10, position=None)
    assert "X-Queue-Active" not in wait_headers(10, active="bad")
    assert wait_headers(10, active=-1)["X-Queue-Active"] == "0"


def test_float_ms_truncated():
    assert wait_headers(1500.9)["X-Queue-Waited-Ms"] == "1500"


# ── 큐가 성공 경로에서도 순번을 돌려주는지 (헤더가 죽지 않도록) ───────────────

def test_acquire_returns_position_after_waiting():
    """대기 후 성공하면 진입 순번을 돌려줘야 한다 — 0으로 버리면 헤더가 항상 빈다."""
    import threading
    import time as _t
    from app import ChatFairQueue                      # noqa: PLC0415 — Flask 앱 임포트 비용

    q = ChatFairQueue(max_global=1, max_per_user=1, max_wait_queue=5, wait_timeout=5)
    assert q.acquire("A")[0] is True                    # 슬롯 점유

    got = {}

    def _second():
        got["r"] = q.acquire("B")                       # 대기열 진입

    t = threading.Thread(target=_second)
    t.start()
    _t.sleep(0.2)
    q.release("A")                                      # 깨우기
    t.join(timeout=5)

    acquired, waited, position, _est = got["r"]
    assert acquired is True and waited is True
    assert position == 1, "진입 순번이 보존돼야 한다"
    q.release("B")
