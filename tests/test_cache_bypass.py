"""요청 단위 캐시 우회 — 게이트가 캐시를 읽지 않게 한다.

🔴 왜 있는가 (2026-08-12 실측): 계약 스모크는 "실제 모델을 호출한다"고 선언해 놓고
재실행하면 캐시를 읽었다. medlaw·control·predict·adverse-drug **43체크가
0.001~0.015초**로 지나갔고(한 글자·한 살만 바꾸면 4.3~6.4초) 모델을 부르지 않았다.
배포 경로는 HUP로 워커가 새로 떠서 콜드지만, "흔들리나 보려고 단독 재실행"하는
순간 그 43체크는 판정력을 잃는다.

⚠️ 우회는 **로컬 직결 요청에서만**. 외부에 열면 같은 질의를 반복해 매번 생성시킬 수
   있어 캐시가 완충 역할을 못 한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import BYPASS_HEADER, bypass_allowed                       # noqa: E402


# ── 순수 판정 ───────────────────────────────────────────────────────────────

def test_local_request_with_header_bypasses():
    assert bypass_allowed("1", "127.0.0.1") is True
    assert bypass_allowed("true", "::1") is True
    assert bypass_allowed("ON", "127.0.0.1") is True


def test_no_header_is_normal_caching():
    """헤더가 없으면 기존 동작 100% — 이게 기본이다."""
    assert bypass_allowed(None, "127.0.0.1") is False
    assert bypass_allowed("", "127.0.0.1") is False
    assert bypass_allowed("0", "127.0.0.1") is False
    assert bypass_allowed("no", "127.0.0.1") is False


def test_external_request_cannot_bypass():
    """외부에 열면 반복 질의로 매번 생성시킬 수 있다(캐시가 완충을 못 한다)."""
    assert bypass_allowed("1", "203.0.113.9") is False


def test_proxy_headers_mean_not_local():
    """프록시 헤더가 있으면 로컬로 보지 않는다 — v2.109.2 H1과 같은 규율.

    nginx·CF를 지나온 요청은 remote_addr이 127.0.0.1로 보일 수 있다. 그걸 로컬로
    인정하면 외부가 우회 헤더를 붙여 보낼 수 있다.
    """
    assert bypass_allowed("1", "127.0.0.1", forwarded="203.0.113.9") is False
    assert bypass_allowed("1", "127.0.0.1", forwarded="10.0.0.1") is False


# ── 캐시 객체 동작 ──────────────────────────────────────────────────────────
#
# 🔑 판정을 **캐시 객체에 넣은** 이유: 읽기/쓰기 지점이 20곳이 넘고(korean 하나에만
#    여러 쌍) 호출 지점마다 조건을 넣으면 새 지점에서 빠뜨린다. 빠뜨린 사실은
#    "빠르게 통과했다"로만 보여 조용하다.

def _cache():
    from routes.cache_ctx import RequestAwareTTLCache
    return RequestAwareTTLCache(maxsize=8, ttl=60)


def _app():
    from flask import Flask
    return Flask(__name__)


def test_cache_hits_normally():
    c = _cache()
    c.set("k", "v")
    with _app().test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert c.get("k") == "v"


def test_cache_read_skipped_when_bypass_requested():
    c = _cache()
    c.set("k", "v")
    with _app().test_request_context("/", headers={BYPASS_HEADER: "1"},
                                     environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert c.get("k") is None


def test_bypass_does_not_block_writes():
    """쓰기까지 막으면 우회 실행이 낡은 값을 남겨, 다음 호출이 우회 이전 답을 다시 본다."""
    c = _cache()
    with _app().test_request_context("/", headers={BYPASS_HEADER: "1"},
                                     environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        c.set("k", "fresh")
    with _app().test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert c.get("k") == "fresh"


def test_external_header_is_ignored_by_cache():
    c = _cache()
    c.set("k", "v")
    with _app().test_request_context("/", headers={BYPASS_HEADER: "1"},
                                     environ_base={"REMOTE_ADDR": "203.0.113.9"}):
        assert c.get("k") == "v"


def test_forwarded_header_is_ignored_by_cache():
    c = _cache()
    c.set("k", "v")
    with _app().test_request_context(
            "/", headers={BYPASS_HEADER: "1", "X-Forwarded-For": "203.0.113.9"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert c.get("k") == "v"


def test_outside_request_context_is_normal_caching():
    """백그라운드 스레드(요청 맥락 없음)에서도 안전해야 한다."""
    c = _cache()
    c.set("k", "v")
    assert c.get("k") == "v"
