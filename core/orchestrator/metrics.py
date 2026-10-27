"""관측성 — 노드×capability×decision 카운터/지연/최근결정 (Phase 1).

설계 §9. 인메모리(_LOCK 보호). Prometheus 노출은 app.py /metrics에서 snapshot 사용.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_LOCK = threading.Lock()
_counts: dict = defaultdict(int)      # (cap, node, decision) -> n
_latency: dict = {}                   # (cap, node) -> ewma ms
_recent: deque = deque(maxlen=20)


def record(cap: str, node: str, decision: str, ms: float, detail: str = "") -> None:
    """decision ∈ delegated | fallback | error | local."""
    with _LOCK:
        _counts[(cap, node, decision)] += 1
        k = (cap, node)
        prev = _latency.get(k, ms)
        _latency[k] = 0.3 * ms + 0.7 * prev
        _recent.appendleft({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cap": cap, "node": node,
            "decision": decision, "ms": ms, "detail": detail,
        })


def snapshot() -> dict:
    with _LOCK:
        counts = {f"{c}|{n}|{d}": v for (c, n, d), v in _counts.items()}
        lat = {f"{c}|{n}": round(v, 1) for (c, n), v in _latency.items()}
        return {"counts": counts, "latency_ewma_ms": lat, "recent": list(_recent)}


def reset() -> None:
    """테스트용."""
    with _LOCK:
        _counts.clear()
        _latency.clear()
        _recent.clear()
