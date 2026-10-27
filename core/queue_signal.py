"""공정 큐 대기 신호 — 호출자가 "왜 느린지" 알 수 있게 한다.

🔴 2026-07-30 Cerno 실측: 단일 API 키로 동시 요청을 던지면 `max_per_user=1` 때문에 완전히
직렬화되는데, **대기열(20슬롯·60초)이 흡수해 429가 뜨지 않는다.** 호출자에게는 "그냥 느린
서버"로만 보이고, 그쪽 표현대로 **"장애로 보이지 않아 원인을 찾기 어렵다."**

서버는 대기 시간을 알고 있으면서 응답에 싣지 않았다(`acquire()`가 waited를 돌려주는데
버려짐). 지연을 **호출자 문제와 큐 대기로 분리**할 수 있게 헤더로 노출한다.

헤더(부가 정보라 기존 계약 불변):
  X-Queue-Waited-Ms  대기열에서 기다린 시간(ms) — 0이면 즉시 처리
  X-Queue-Position   대기 진입 시 순번(대기했을 때만)
  X-Queue-Active     헤더 발급 시점의 전체 활성 요청 수
"""


def wait_headers(waited_ms, position=0, active=None) -> dict:
    """대기 신호 헤더. 음수·None은 0으로 정규화(헤더에 이상값을 내보내지 않는다)."""
    try:
        ms = max(0, int(waited_ms or 0))
    except (TypeError, ValueError):
        ms = 0
    out = {"X-Queue-Waited-Ms": str(ms)}
    try:
        pos = int(position or 0)
    except (TypeError, ValueError):
        pos = 0
    if pos > 0:
        out["X-Queue-Position"] = str(pos)
    if active is not None:
        try:
            out["X-Queue-Active"] = str(max(0, int(active)))
        except (TypeError, ValueError):
            pass
    return out
