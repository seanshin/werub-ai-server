"""평가 judge 모델 — **단일 소스 + 고정(pin)**.

🔴 왜 필요한가: judge가 피평가 모델과 같으면(둘 다 `qwen2.5:14b`) **모델을 바꿀 때
측정 기준도 같이 바뀐다**. 그러면 "모델이 좋아진 것"과 "judge가 달라진 것"을 분리할 수 없다.
이 프로젝트는 이미 같은 함정을 겪었다 — 08-06 `complete` 기각(판정이 14/40 뒤집혔는데
양방향으로 흩어짐), 08-10 품질 기준선 오귀속(v2.124.91의 몫을 오늘로 돌릴 뻔).

규율:
  - judge 교체는 **별도 릴리즈**로만. 모델 A/B 중에는 절대 바꾸지 않는다.
  - judge가 바뀌면 **추세를 잇지 않는다** — `eval.db`의 `judge_model`로 단절점을 표시한다.
  - 우선순위: config `eval_judge_model` → env(레거시) → 기본값.
    config를 위에 둔 것은 **무재배포 전환**과 **명시적 관리**를 위해서다.

⚠️ judge를 외부(프론티어) 모델로 올리는 선택지가 있으나 **PHI가 judge에 들어간다** —
   의료 도메인은 불가. 비의료 도메인 한정으로만 검토할 것.

순수 모듈(네트워크 무관). 기본값은 종전과 동일해 **동작이 바뀌지 않는다**.
기획: docs/01-plan/features/model-decoupling-and-eval-driven-development.plan.md §8-3a
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import os

DEFAULT_JUDGE = "qwen2.5:14b"

# 역할별 레거시 env 이름(기존 배포·스크립트 호환)
_LEGACY_ENV = {
    "faithfulness": "FAITH_JUDGE_MODEL",
    "quality": "QUALITY_JUDGE_MODEL",
}


def judge_model(role: str = "quality", cfg: dict | None = None) -> str:
    """이 역할의 judge 모델. config → env → 기본값.

    cfg를 주면 그것을 쓴다(테스트·호출자 주입). 안 주면 config를 읽되 실패해도
    죽지 않는다 — 평가가 config 문제로 멈추면 안 된다.
    """
    if cfg is None:
        try:
            from core.config import load_config
            cfg = load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    per_role = (cfg or {}).get("eval_judge_models") or {}
    val = per_role.get(role) or (cfg or {}).get("eval_judge_model")
    if val:
        return str(val)
    env = _LEGACY_ENV.get(role)
    if env and os.environ.get(env):
        return os.environ[env]
    return DEFAULT_JUDGE


def is_pinned(cfg: dict | None = None) -> bool:
    """config로 명시 고정됐는가. env·기본값에만 의존하면 False(= 고정 안 됨)."""
    if cfg is None:
        try:
            from core.config import load_config
            cfg = load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    return bool((cfg or {}).get("eval_judge_model") or (cfg or {}).get("eval_judge_models"))


def trend_breaks(rows) -> list:
    """배치 시계열에서 **judge가 바뀐 지점**의 ts 목록.

    rows: [{"ts":..., "judge_model":...}] (시간 오름차순)
    judge가 다르면 그 앞뒤 추세를 이어 읽으면 안 된다 — 화면이 그것을 표시하도록
    단절점을 돌려준다. judge_model이 없는(구) 배치는 **판정 불가**라 단절로 세지 않는다.
    """
    breaks, prev = [], None
    for r in rows or []:
        jm = (r or {}).get("judge_model")
        if not jm:
            continue
        if prev is not None and jm != prev:
            breaks.append({"ts": r.get("ts"), "from": prev, "to": jm})
        prev = jm
    return breaks
