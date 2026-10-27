"""기본 LLM 상주 정책 — 상주(pin) vs 요청 시 탑재(on-demand).

기본 모델(`default_model`, 예: qwen2.5:14b ~10.5GB)은 그동안 `keep_alive=-1`로 **영구 상주**
시켰다. 첫 토큰 지연을 없애려는 선택이었지만, 16GB 카드에서 10.5GB를 상시 점유하므로
**파인튜닝·대형 모델 실험처럼 VRAM을 크게 쓰는 작업과 양립할 수 없다**.

`llm_resident=false`로 두면 예열을 하지 않는다. 요청이 오면 Ollama가 그때 적재하고
`OLLAMA_KEEP_ALIVE`(현 5m) 후 스스로 반납한다 — 즉 **놀고 있을 때 VRAM이 비어 있다**.

트레이드오프는 정직하게: 콜드 상태의 첫 요청은 모델 적재 시간(14b 기준 수~십수 초)이 붙는다.
되돌리기는 config 한 줄이다.
"""

# 상주 시 사용하는 keep_alive — Ollama에서 음수는 "무기한 유지".
RESIDENT_KEEP_ALIVE = -1


def is_resident(cfg) -> bool:
    """기본 모델을 상주시킬지 — config `llm_resident`(기본 True=기존 동작).

    문자열 "false"/"0"/"off"/"no"도 거짓으로 받는다(운영자가 콘솔·JSON에서 문자열로
    넣는 실수를 조용한 오작동으로 만들지 않기 위해).
    """
    if not isinstance(cfg, dict):
        return True
    v = cfg.get("llm_resident", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "off", "no", "")
    return bool(v)


def resident_model(cfg, fallback=None):
    """사전 적재할 모델명 — config `llm_resident_model`, 없으면 `default_model`.

    채팅 기본 모델과 **분리**한 이유: 품질은 14b로 쓰되 상주는 7b(4.7GB)로 두는 식의
    선택이 16GB 카드에서 실제로 의미가 있다. 미지정이면 기존 동작(기본 모델 예열) 그대로.
    """
    if not isinstance(cfg, dict):
        return fallback or ""
    return (cfg.get("llm_resident_model") or cfg.get("default_model")
            or fallback or "").strip()


def keep_alive_for(cfg):
    """예열 호출에 쓸 keep_alive. 비상주면 None(=예열하지 않음).

    비상주 모드에서 굳이 예열 값을 정하지 않는 이유: 예열 자체를 안 하므로 적재 시점의
    유지 시간은 Ollama 전역 설정(`OLLAMA_KEEP_ALIVE`)이 결정한다 — 정책 지점이 하나다.
    """
    return RESIDENT_KEEP_ALIVE if is_resident(cfg) else None


# ── 상주 복원 유예 (v2.125.48) ────────────────────────────────────────────────
# 큐 워커는 놀 때 2초마다 `_ensure_default_model()`을 불러, 상주 모델이 없으면 **즉시**
# 다시 적재했다. 16GB VRAM에 14b(9.9GB)와 다른 모델이 함께 못 올라가므로(유효 설정은
# MAX_LOADED_MODELS=3 — `/etc/default/ollama`가 drop-in의 1을 덮어씀·병목은 VRAM) 다른 모델을 연달아 쓰는 흐름(평가 교차검증 7b ×N,
# 상담 2단, 비전, 의료 모델)의 **호출 사이 틈마다 14b가 끼어들어** 그 모델을 내쫓았다
# — 2026-09-28 평가 배치 8.6분에 적재 125회(14b 62 = 예열 63과 일치).
# 처치: 다른 모델이 **쓰이는 동안**(ps의 expires_at이 앞으로 움직이는 동안)은 복원하지
# 않고, 마지막 활동 후 유예(기본 30s)가 지나야 복원한다(빈 상태도 동일 — 교체 중 빈 순간).
DEFAULT_RESTORE_GRACE_S = 30.0


def restore_grace_s(cfg) -> float:
    """config `resident_restore_grace_s`(기본 30). 음수·비숫자는 기본값."""
    try:
        v = float((cfg or {}).get("resident_restore_grace_s", DEFAULT_RESTORE_GRACE_S))
        return v if v >= 0 else DEFAULT_RESTORE_GRACE_S
    except (TypeError, ValueError):
        return DEFAULT_RESTORE_GRACE_S


def restore_due(ps_models, resident: str, state: dict, now: float, grace: float) -> bool:
    """상주 모델을 지금 다시 적재해야 하는가(순수 판정 — 호출자가 state를 유지한다).

    - 상주 모델이 이미 적재됨 → False(상태 초기화)
    - 그 외(다른 모델 적재 **또는 빈 상태**) → 적재 목록의 (이름, expires_at) 서명이 바뀐
      시각을 '마지막 활동'으로 보고, 그로부터 grace가 지났을 때만 True

    ⚠️ 빈 상태도 유예 대상이다: 모델 교체 중에는 이전 모델이 내려가고 다음 모델이 아직
    올라오지 않은 **빈 순간**이 있다. v2.125.48 1차는 빈 상태를 "아무도 안 씀 → 즉시 복원"으로
    봐서 바로 그 순간(7b 적재 시작 ~0.3s 후)마다 14b가 끼어들었다 — 사전 등록 기준
    (적재 ≤40) 미달(125→99)로 드러남. 부팅 직후 예열은 grace만큼 늦어진다(요청이 오면
    Ollama가 어차피 적재한다).
    """
    names = [m.get("name", "") for m in (ps_models or [])]
    if resident in names:
        state.clear()
        return False
    sig = tuple(sorted((m.get("name", ""), str(m.get("expires_at", ""))) for m in (ps_models or [])))
    if state.get("sig") != sig:
        state["sig"] = sig
        state["last_activity"] = now
        return grace <= 0
    return now - state.get("last_activity", now) >= grace
