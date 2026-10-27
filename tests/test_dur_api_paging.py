"""DUR 오픈 API 페이지네이션 — 중간 빈 페이지를 '끝'으로 읽지 않는다 (2026-08-06).

🔴 같은 클래스의 실사고가 치료재료 수집에서 났다: 중간 페이지가 **200 OK + 빈 배열**을
돌려주는데 `if not rows: break`가 그것을 끝으로 읽어 59,000/91,377행에서 멈췄고
**정본 32,377행이 조용히 사라졌다.** 같은 형태가 이 모듈에도 있었다.

⚠️ 이 경로는 병용금기 등 **의약품 안전 정본**이라 조용한 누락의 대가가 크다.
"""
import io
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import dur_api  # noqa: E402


def _page(items, total):
    return json.dumps({"body": {"items": items, "totalCount": total}}).encode()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _fake_urlopen(pages, calls=None):
    """pageNo → 응답 바이트. 호출 순서를 calls에 기록."""
    def opener(url, timeout=None):
        page = int(url.split("pageNo=")[1].split("&")[0])
        if calls is not None:
            calls.append(page)
        return _Resp(pages.get(page, _page([], 0)))
    return opener


def test_empty_middle_page_does_not_stop(monkeypatch):
    """totalCount가 남아 있는데 빈 페이지 = 끝이 아니라 구멍."""
    pages = {
        1: _page([{"item": {"a": 1}}], 400),
        2: _page([], 400),                       # 🔴 구멍
        3: _page([{"item": {"a": 3}}], 400),
        4: _page([{"item": {"a": 4}}], 400),
    }
    calls = []
    monkeypatch.setattr(dur_api.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    out = dur_api.fetch_all("op", "key", num_rows=100)
    assert 4 in calls, calls                     # 구멍에서 멈추지 않고 끝까지 갔다
    assert len(out) == 3                         # 1·3·4에서 각 1건(2는 비어 있음)


def test_hole_is_retried_and_recovered(monkeypatch):
    """구멍은 재시도한다 — 일과성이면 회복된다."""
    state = {"n": 0}

    def opener(url, timeout=None):
        page = int(url.split("pageNo=")[1].split("&")[0])
        if page == 2:
            state["n"] += 1
            if state["n"] == 1:
                return _Resp(_page([], 300))     # 첫 방문만 비어 있음
            return _Resp(_page([{"item": {"a": 2}}], 300))
        return _Resp(_page([{"item": {"a": page}}], 300))

    monkeypatch.setattr(dur_api.urllib.request, "urlopen", opener)
    out = dur_api.fetch_all("op", "key", num_rows=100)
    assert len(out) == 3                         # 1·3 + 재시도로 회복한 2
    assert state["n"] == 2                       # 실제로 재시도했다


def test_unknown_total_still_terminates(monkeypatch):
    """totalCount가 없으면 빈 페이지를 끝으로 본다 — 무한 순회 방지."""
    pages = {1: _page([{"item": {"a": 1}}], 0)}
    calls = []
    monkeypatch.setattr(dur_api.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    out = dur_api.fetch_all("op", "key", num_rows=100, max_pages=50)
    assert len(out) == 1
    assert max(calls) <= 2, calls                # 곧바로 종료


def test_normal_completion_unchanged(monkeypatch):
    """정상 종료(totalCount 도달)는 그대로 — 가드가 수집을 늘리지 않는다."""
    pages = {1: _page([{"item": {"a": i}} for i in range(100)], 200),
             2: _page([{"item": {"a": i}} for i in range(100)], 200)}
    calls = []
    monkeypatch.setattr(dur_api.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    out = dur_api.fetch_all("op", "key", num_rows=100)
    assert len(out) == 200
    assert max(calls) == 2, calls                # 3페이지를 부르지 않는다


def test_retry_failure_is_non_blocking(monkeypatch):
    """구멍 **재시도**가 예외를 던져도 이미 모은 것은 살린다.

    (본 순회의 예외는 기존대로 전파된다 — 부분 수집으로 정본을 교체하지 않기 위해서다.)
    """
    state = {"n": 0}

    def opener(url, timeout=None):
        page = int(url.split("pageNo=")[1].split("&")[0])
        if page == 2:
            state["n"] += 1
            if state["n"] == 1:
                return _Resp(_page([], 300))     # 본 순회에선 빈 응답(예외 아님)
            raise OSError("reset")               # 재시도에서 연결 오류
        return _Resp(_page([{"item": {"a": page}}], 300))

    monkeypatch.setattr(dur_api.urllib.request, "urlopen", opener)
    out = dur_api.fetch_all("op", "key", num_rows=100)   # 예외가 새어 나오면 실패
    assert len(out) == 2                                  # page 1·3
    assert state["n"] == 2                                # 재시도는 실제로 했다
