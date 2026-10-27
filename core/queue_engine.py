"""ChatFairQueue — 공정 큐 게이트"""
import threading
import time


class ChatFairQueue:
    """글로벌 + 유저별 동시 제한 + FIFO 대기열 (라운드로빈)"""

    def __init__(self, max_global=3, max_per_user=1, max_wait_queue=20, wait_timeout=60):
        self._lock = threading.Lock()
        self._max_global = max_global
        self._max_per_user = max_per_user
        self._max_wait_queue = max_wait_queue
        self._wait_timeout = wait_timeout
        self._active = {}
        self._active_total = 0
        self._waiters = []

    @property
    def active_total(self):
        return self._active_total

    @property
    def queue_length(self):
        return len(self._waiters)

    def try_acquire(self, key):
        with self._lock:
            if self._active_total >= self._max_global:
                return False
            if self._active.get(key, 0) >= self._max_per_user:
                return False
            self._active[key] = self._active.get(key, 0) + 1
            self._active_total += 1
            return True

    def acquire(self, key):
        if self.try_acquire(key):
            return True, False, 0, 0
        with self._lock:
            if len(self._waiters) >= self._max_wait_queue:
                return False, False, len(self._waiters), -1
            evt = threading.Event()
            entry = (key, evt, time.time())
            self._waiters.append(entry)
            position = len(self._waiters)
        acquired = evt.wait(timeout=self._wait_timeout)
        if acquired:
            return True, True, 0, 0
        with self._lock:
            self._waiters = [(k, e, t) for k, e, t in self._waiters if e is not evt]
        return False, True, position, -1

    def release(self, key):
        with self._lock:
            self._active[key] = max(0, self._active.get(key, 0) - 1)
            if self._active[key] == 0:
                del self._active[key]
            self._active_total = max(0, self._active_total - 1)
            if not self._waiters:
                return
            next_idx = None
            for i, (wk, we, wt) in enumerate(self._waiters):
                if wk not in self._active:
                    next_idx = i
                    break
            if next_idx is None:
                next_idx = 0
            wk, we, wt = self._waiters.pop(next_idx)
            self._active[wk] = self._active.get(wk, 0) + 1
            self._active_total += 1
            we.set()

    def stats(self):
        with self._lock:
            return {
                "active_total": self._active_total,
                "max_global": self._max_global,
                "per_user": dict(self._active),
                "max_per_user": self._max_per_user,
                "wait_queue": len(self._waiters),
                "max_wait_queue": self._max_wait_queue,
            }
