"""Ollama 가용성 판정 — 순수 상태머신 (HTTP 호출과 분리, 의존성 0).

get_ollama_status()에서 추출. 단발 블립(앱측 스레드 포화/스테일 keep-alive)으로 인한
'Engine Down' 오탐을 막기 위해 N회 연속 실패해야 다운으로 확정한다.
순수 함수라 flask/requests 없이 단위 테스트 가능. (v2.72.0 P1-B)
"""


def evaluate_status(ok, state, fail_threshold=2):
    """연속 실패 기반 가용성 판정 (단발 블립 흡수).

    Args:
        ok: 이번 raw 체크 성공 여부 (GET / == 200).
        state: {"value": bool, "fails": int} — in-place 갱신되는 캐시 상태.
        fail_threshold: 다운 확정에 필요한 연속 실패 횟수 (기본 2).

    Returns:
        확정된 가용 상태(bool). 첫 실패는 직전 상태를 낙관 유지,
        fail_threshold회 연속 실패 시 False 확정. 성공 시 즉시 True + 카운터 리셋.
    """
    if ok:
        state["fails"] = 0
        result = True
    else:
        state["fails"] = state.get("fails", 0) + 1
        result = False if state["fails"] >= fail_threshold else state.get("value", False)
    state["value"] = result
    return result
