"""인메모리 지연/상태 리저버 — 성능 모니터링 (P1).

워커당 인메모리(gunicorn workers=1이라 단일 프로세스=일관). 워커 recycle
(~37-40min, max_requests) 시 리셋되지만 **최근 트래픽 percentile**이 목적이라 수용.
누적·장기 추세는 stats_history(DB, _collect_stats가 5분마다 박제)가 보유한다.

설계 근거(P0 라이브 측정 2026-06-30):
- api_activity ~110 req/min·2000행=18.5분치 → _MAX=2048(~18min) 적정.
- distinct path 7개뿐 → 라벨 카디널리티 가드는 단순 cap(_TOP_ENDPOINTS)으로 충분.
- 평시 에러 0(전부 200) → error_rate는 5xx 기준(4xx=인증 401 등 정상 제외).

순수 모듈(Flask-free) — 단위테스트 가능. 모든 공개 함수는 _LOCK 보호.
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque

_LOCK = threading.Lock()

_MAX = 2048            # 전체 지연 리저버 크기 (~18min @ 110req/min, P0 측정)
_EP_MAX = 512          # 엔드포인트별 리저버 크기
_TOP_ENDPOINTS = 8     # per-path 리저버 유지 상한 (카디널리티 가드, P0 distinct=7)

_durations: deque = deque(maxlen=_MAX)   # 전체 지연 ms
_status: dict = defaultdict(int)          # "2xx"/"4xx"/"5xx"... -> 누적 count (recycle까지)
_total = 0
_ep_durations: dict = {}                  # path -> deque(maxlen=_EP_MAX) (상위 엔드포인트만)
_ep_count: dict = defaultdict(int)        # path -> 누적 호출수

# ── 동시 처리 중 요청 수 (in-flight, v2.124.94) ──
# 스레드 고갈의 실제 신호. worker_threads(생성된 스레드 수)로는 잴 수 없다 —
# ThreadPoolExecutor는 한 번 만든 스레드를 유지하므로 "다 만들어졌다"만 말하고
# "다 바쁘다"는 말하지 못한다(30일 실측: threads 구간별 p95 중앙 575/578/575ms로 무차이).
_inflight = 0
_inflight_peak = 0


def record(path: str, status: int, ms: float, upstream: bool = False) -> None:
    """요청 1건 기록. after_request 훅에서 호출(이미 계산된 path/status/duration).

    upstream=True는 **원격 노드 프록시**의 실패(이 서버가 관찰만 하는 노드가
    미도달)임을 뜻한다. 이 서버 자신의 고장이 아니므로 5xx를 `5xx_upstream`
    별도 클래스로 세어 `error_rate()`(=서버 건전성 신호)에서 제외한다.
    버리지 않고 별도 계급으로 남기는 이유는 `upstream_error_rate()`·
    `status_counts()`로 여전히 보여야 하기 때문이다(무성 소실 금지).

    근거(2026-08-31 실측): iMac 미도달 54h 동안 `/api/mac/{stats,health}`가
    502를 31,117건 반환했고, 그 전량이 이 서버의 5xx로 세어져 error_rate가
    33%로 올라 Error Rate Spike 경보가 43시간 연속(534건) 울었다. 그 기간
    이 서버 자신의 진짜 5xx는 그 바닥에 묻힌다.
    """
    global _total
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return
    with _LOCK:
        _durations.append(ms)
        _total += 1
        cls = f"{status // 100}xx" if status else "0xx"
        if upstream and cls in ("5xx", "0xx"):
            cls += "_upstream"
        _status[cls] += 1
        _ep_count[path] += 1
        dq = _ep_durations.get(path)
        if dq is None:
            # 상위 엔드포인트 슬롯이 남을 때만 per-path 리저버 신설 (카디널리티 폭주 방지)
            if len(_ep_durations) < _TOP_ENDPOINTS:
                dq = _ep_durations[path] = deque(maxlen=_EP_MAX)
            else:
                return
        dq.append(ms)


def enter() -> None:
    """요청 처리 시작. before_request 훅에서 호출."""
    global _inflight, _inflight_peak
    with _LOCK:
        _inflight += 1
        if _inflight > _inflight_peak:
            _inflight_peak = _inflight


def leave() -> None:
    """요청 처리 종료. teardown_request 훅에서 호출(예외 시에도 반드시 실행되는 자리)."""
    global _inflight
    with _LOCK:
        if _inflight > 0:      # 비대칭 호출(enter 없이 leave)에도 음수로 가지 않음
            _inflight -= 1


def inflight(reset_peak: bool = False) -> dict:
    """동시 처리 중 요청 수. peak은 마지막 reset 이후 최대(샘플 사이 버스트 포착).

    reset_peak=True는 소비자가 하나일 때만 쓸 것(수집기 전용) — 여러 소비자가
    리셋하면 서로의 관측 구간을 지운다.
    """
    global _inflight_peak
    with _LOCK:
        cur, peak = _inflight, _inflight_peak
        if reset_peak:
            _inflight_peak = cur
    return {"current": cur, "peak": peak}


def _pct(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p))
    return float(sorted_vals[idx])


def quantiles(path: str | None = None) -> dict:
    """전체(path=None) 또는 특정 엔드포인트 지연 percentile."""
    with _LOCK:
        vals = list(_ep_durations.get(path, ())) if path else list(_durations)
    s = sorted(vals)
    return {
        "n": len(s),
        "p50": _pct(s, 0.50),
        "p95": _pct(s, 0.95),
        "p99": _pct(s, 0.99),
        "max": float(s[-1]) if s else 0.0,
    }


def error_rate() -> float:
    """5xx 비율(%). **이 서버 자신의** 5xx만 센다 — 서버 건전성 신호.

    제외 대상 둘: 4xx(인증 401 등 클라이언트 오류)와 `5xx_upstream`
    (원격 노드 프록시 미도달 — `record(upstream=True)` 참조).
    분모는 전체 요청 수 그대로다(프록시 요청도 스레드를 똑같이 점유했다).
    """
    with _LOCK:
        total = _total
        errors = _status.get("5xx", 0)
    return round(errors / total * 100, 2) if total else 0.0


def upstream_error_rate() -> float:
    """원격 노드 프록시 실패 비율(%). error_rate와 달리 **관찰 대상 노드**의 상태다."""
    with _LOCK:
        total = _total
        errors = _status.get("5xx_upstream", 0) + _status.get("0xx_upstream", 0)
    return round(errors / total * 100, 2) if total else 0.0


def status_counts() -> dict:
    with _LOCK:
        return dict(_status)


def top_endpoints(n: int = 8) -> list:
    """호출수 상위 엔드포인트 [(path, count), ...]."""
    with _LOCK:
        items = sorted(_ep_count.items(), key=lambda kv: kv[1], reverse=True)
    return items[:n]


def snapshot() -> dict:
    """수집기/metrics용 단일 스냅샷."""
    return {
        "latency": quantiles(),
        "error_rate_pct": error_rate(),
        "upstream_error_rate_pct": upstream_error_rate(),
        "status": status_counts(),
        "total": _total,
        "inflight": inflight(),
    }


def reset() -> None:
    """테스트용."""
    global _total, _inflight, _inflight_peak
    with _LOCK:
        _durations.clear()
        _status.clear()
        _ep_durations.clear()
        _ep_count.clear()
        _total = 0
        _inflight = 0
        _inflight_peak = 0
