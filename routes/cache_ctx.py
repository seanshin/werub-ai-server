"""요청 단위 캐시 우회 — 게이트가 캐시를 읽지 않게 한다(로컬 요청 한정).

🔑 **캐시 객체에 성질을 넣는다.** 호출 지점마다 `if bypass: ...`를 넣으면 새 지점에서
   빠뜨리고, 빠뜨린 사실은 "빠르게 통과했다"로만 보여 조용하다. 실제로 캐시 읽기/쓰기
   지점은 20곳이 넘고 korean 하나에만 여러 쌍이 있다.

⚠️ **읽기만 건너뛰고 쓰기는 그대로 둔다.** 쓰기까지 막으면 우회 실행이 낡은 값을 그대로
   남겨, 바로 다음 호출이 우회 이전 답을 다시 본다.

판정 자체(`core.cache.bypass_allowed`)는 순수 함수로 core에 있다 — core는 Flask를 모른다.
여기는 그 판정에 요청 맥락을 먹이는 얇은 껍데기다.
"""
from flask import has_request_context, request

from core.cache import BYPASS_HEADER, TTLCache, bypass_allowed

# 프록시 계층이 붙여 보내는 헤더 — 하나라도 있으면 로컬 직결이 아니다
_FWD_HEADERS = ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP")


def no_cache_requested() -> bool:
    """이 요청이 캐시 우회를 요청했는가. 요청 맥락 밖(백그라운드 스레드)이면 False."""
    if not has_request_context():
        return False
    try:
        fwd = next((request.headers.get(h) for h in _FWD_HEADERS if request.headers.get(h)), None)
        return bypass_allowed(request.headers.get(BYPASS_HEADER), request.remote_addr, fwd)
    except Exception:      # noqa: BLE001 — 캐시 판정이 요청을 깨뜨리면 안 된다
        return False


class RequestAwareTTLCache(TTLCache):
    """`X-No-Cache: 1`(로컬 요청)이면 읽기를 건너뛰는 TTL 캐시.

    나머지 동작은 `TTLCache`와 같다 — 헤더가 없으면 기존 동작 100%.
    """

    def get(self, key):
        if no_cache_requested():
            return None
        return super().get(key)
