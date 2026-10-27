"""정본 전량 교체 전 **상대 감소 가드** — 절대 하한만으로는 부분 응답을 못 잡는다.

🔴 2026-08-06 실사고(`v2.124.92`): 치료재료 동기화가 91,211 → **59,000**(-35%)을
`status=ok`로 전량 교체했다. 안전 갱신 가드는 있었지만 **절대 하한 `MIN_COUNT=10,000`**만
봐서 59,000을 넉넉히 통과시켰고, **정본 32,377행이 조용히 사라졌다.**

원인(수집 조기 종료)은 따로 고쳤지만, **다음 번 다른 이유로 같은 일이 나는 것**은 가드가
막아야 한다. 절대 하한은 "0에 가까운 응답"만 잡고 **"35% 빠진 응답"은 못 잡는다.**

기준은 메타 테이블의 직전 건수가 아니라 **현재 적재된 실제 행 수**를 쓴다 —
메타는 `source`가 PK라 skip이 한 번 기록되면 직전 ok 건수를 잃기 때문이다.

⚠️ 전부 **fail-open**이다: 첫 적재(현재 0)·조회 실패는 통과시킨다.
가드가 갱신 자체를 막으면 정본이 낡아 가는 다른 손해가 생긴다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 현재 적재량 대비 허용 감소폭. 정본이 실제로 20% 넘게 줄어드는 일은 드물고,
#: 줄더라도 한 번 skip한 뒤 `last_error`를 보고 사람이 판단하는 편이 조용한 손실보다 낫다.
DEFAULT_MAX_DROP_RATIO = 0.20


def max_drop_ratio(key: str = "sync_max_drop_ratio",
                   default: float = DEFAULT_MAX_DROP_RATIO) -> float:
    """config로 무재배포 튜닝(0=감소 불허 … 1=가드 해제). 범위 밖은 클램프."""
    try:
        from core.config import load_config
        return max(0.0, min(float(load_config().get(key, default)), 1.0))
    except Exception:  # noqa: BLE001 — 설정 실패는 기본값으로(가드 유지)
        return default


def drop_too_large(new_count: int, current_count, ratio: float | None = None) -> bool:
    """현재 적재량 대비 **허용 밖 감소**인가(순수).

    첫 적재(current 0/None)와 증가는 항상 통과한다.
    """
    if not current_count or current_count <= 0:
        return False
    if new_count >= current_count:
        return False
    r = DEFAULT_MAX_DROP_RATIO if ratio is None else ratio
    return new_count < current_count * (1 - r)


def drop_reason(new_count: int, current_count, ratio: float) -> str:
    """skip 사유 문구(순수) — 로그·`last_error`에 그대로 실어 사람이 판단하게 한다."""
    pct = 0 if not current_count else round(100 * (current_count - new_count) / current_count)
    return (f"rows {new_count} < 현재 {current_count}의 {int((1 - ratio) * 100)}% "
            f"(-{pct}%) — 부분 응답 의심, 기존 보존")


def safe_count(fn) -> int | None:
    """현재 적재량 조회 — 실패는 None(가드 미적용). 조회 때문에 갱신이 막히면 안 된다."""
    try:
        v = fn()
        return int(v) if v is not None else None
    except Exception as e:  # noqa: BLE001
        logger.info("[sync-guard] 현재 건수 조회 실패 — 가드 미적용: %s", str(e)[:80])
        return None
