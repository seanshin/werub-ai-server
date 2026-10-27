"""엔진 기본값 핀 — 우리가 의존하던 Ollama 기본값을 우리 설정으로 명시한다 (D2 · v2.125.14).

🔑 **왜**: Ollama 0.32.10이 `repeat_penalty` 기본값을 1.1→1.0으로 바꿨다. 우리 모델
33/41이 Modelfile에 값 없이 **엔진 기본값에 의존**하고 있어, 엔진 업그레이드가 곧
전 모델 생성 파라미터 변경이 된다. 사전 등록 A/B(N=40·v2.125.9 §5-b)는 **1.1 유지**
판정 — 반복 점수만 1.0에서 임계(−3) 초과 악화(78.6→74.5). 원자료
`docs/01-plan/references/repeat-penalty-ab-n40-result.json`.

**어디**: `core/model_router.OllamaAdapter._payload`/`_chat_payload` — v2.125.13에서
router 이관 대상이 0이 되어 이 두 페이로드 빌더가 라우팅 경유 전 경로의 단일 통로다.
직접 호출 **허용 목록 25곳**(패스스루 프록시·heavy·openai_compat·인프라 프로브·
`medical.py` 바이트 패스스루)과 잔재 6곳(`_router_enabled()` 폴백)은 **의도적으로
핀 대상이 아니다** — 프록시는 클라이언트 페이로드를 바꾸면 안 되고, heavy/openai_compat은
자기 options 규율이 있고, 프로브는 생성 품질과 무관하다.
인벤토리: `docs/01-plan/references/direct-ollama-calls-2026-08-19.md`.

## 규율

- **호출자가 명시한 값이 이긴다**(setdefault 의미론) — A/B 하네스가 양 팔 값을
  명시하는 경로를 핀이 덮어쓰면 측정이 깨진다.
- **모델 Modelfile에 자체 값이 있으면 핀하지 않는다**(qwen3:8b=1 · medgemma=1.25 등
  8/41) — 블랭킷 setdefault는 그 모델들의 의도된 값을 바꾼다(기획서 §6 함정 D2).
  판별은 `/api/show`의 `parameters` 텍스트.
- **`/api/show` 실패 시 핀 생략**(fail-safe) — 엔진 기본값으로 동작한다. 현행
  0.32.5의 기본값이 곧 1.1이라 생략해도 동작이 같고, 조회가 실패할 정도면
  Ollama 자체가 죽어 있어 생성도 안 된다.
- 조회는 모델별 캐시(성공 1h · 실패 60s) — 요청마다 /api/show를 때리지 않는다.
- embed 경로는 핀 대상이 아니다(repeat_penalty는 생성 전용 파라미터).

⚠️ 테스트: `tests/conftest.py`의 autouse 픽스처가 `_fetch_modelfile_params`를
"자체 값 없음"으로 스텁한다(단위 테스트 네트워크 금지·핀 발동은 결정적).
핀 **로직**은 살아 있어 페이로드 동일성 회귀가 핀 포함으로 검증된다.
이 모듈 자체 테스트는 그 위에 자기 스텁을 다시 얹는다(`tests/test_engine_defaults.py`).
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import logging
import os
import time

logger = logging.getLogger("ollama-admin")

# A/B 판정값(v2.125.9 §5-b) — 바꾸려면 같은 급의 사전 등록 A/B가 선행이다.
REPEAT_PENALTY = 1.1

_TTL_OK = 3600.0    # Modelfile 값은 모델 re-pull에서나 바뀐다
_TTL_FAIL = 60.0    # 실패를 오래 캐시하면 Ollama 복구 후에도 핀이 빠진다
_cache: dict[str, tuple[bool | None, float]] = {}


def _fetch_modelfile_params(model: str, base: str | None = None) -> str:
    """`/api/show`의 `parameters` 텍스트(Modelfile 파라미터·줄 단위 "key value").

    실패는 **던진다** — 캐시 계층(`_has_own_repeat_penalty`)이 실패를 None으로
    변환해 짧게 캐시한다. 여기서 삼키면 실패와 "파라미터 없음"이 구분되지 않는다.
    """
    import requests
    b = base or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    r = requests.post(f"{b}/api/show", json={"model": model}, timeout=5)
    r.raise_for_status()
    return str((r.json() or {}).get("parameters") or "")


def _parse_has_repeat_penalty(params_text: str) -> bool:
    """`parameters` 텍스트에 repeat_penalty 줄이 있는가 — 순수 함수(테스트 직접 대상)."""
    for line in params_text.splitlines():
        parts = line.split()
        if parts and parts[0] == "repeat_penalty":
            return True
    return False


def _has_own_repeat_penalty(model: str, base: str | None = None) -> bool | None:
    """모델 Modelfile에 자체 repeat_penalty가 있는가. None = 조회 실패(핀 생략)."""
    now = time.monotonic()
    hit = _cache.get(model)
    if hit is not None and hit[1] > now:
        return hit[0]
    try:
        val: bool | None = _parse_has_repeat_penalty(_fetch_modelfile_params(model, base))
        _cache[model] = (val, now + _TTL_OK)
    except Exception as e:  # noqa: BLE001 — 조회 실패가 생성을 막으면 안 된다
        logger.warning("[engine_defaults] /api/show 실패 model=%s: %s — 핀 생략", model, e)
        val = None
        _cache[model] = (None, now + _TTL_FAIL)
    return val


def pin_options(model: str, options: dict | None, *, base: str | None = None) -> dict:
    """options에 엔진 기본값 핀을 적용해 **새 dict**로 돌려준다.

    항상 복사한다 — 호출자가 라우팅 표의 options dict를 그대로 넘기는 경로가 있어
    제자리 수정은 표를 오염시킨다(캐시 키 완전성 클래스의 사촌).
    """
    opts = dict(options) if options else {}
    if "repeat_penalty" in opts:            # 호출자 명시가 이긴다
        return opts
    if model and _has_own_repeat_penalty(model, base) is False:
        opts["repeat_penalty"] = REPEAT_PENALTY
    return opts
