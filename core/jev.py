"""Jev(TypeSafe AI System One) 어댑터 — **이 레포의 유일한 Jev 호출 지점**.

P6-1 Phase 1. 기획 `docs/01-plan/features/P6-1-Decision-Confidence-And-Jev.plan.md` §6 ·
조사 `docs/01-plan/references/jev-adoption-review-2026-09-22.md`.

Jev는 자유 생성이 아니라 **사전 정의된 선택지에 대한 확률적 판단**만 하는 상용 API다
(`Noul` = 명제 참/거짓 확률). 우리는 **평가 층의 제3 독립 판정자**로만 쓴다 — judge와
교차검증자가 둘 다 qwen2.5 계열이라 **편향을 공유**하기 때문이고, 상관이 낮은 독립
관측은 로컬로 살 수 없다(`config/model_routing.json:55`가 이미 모델 이종성을 설계
목표로 적어 두었다).

🔴 **경계가 이 모듈의 존재 이유다.**

- **PHI 금지**: 의료·상담·STT·스크라이브 경로는 **영구 제외**. 판정에 원문이 필요한
  과제라 비식별 피처만 보내는 설계가 성립하지 않는다. 그 층은 로컬 구조화 출력으로 간다.
  허용 호출자는 `_ALLOWED_CALLERS` 뿐이고 **회귀가 import 자체를 막는다**.
- **fail-open 불변식**: 미설정·미도달·캡 초과·파싱 실패 = **전부 `None`**(호출자 축 null).
  예외를 던지지 않는다. 평가 배치가 외부 서비스 때문에 멈추면 안 된다.
- **무성 실패 금지**: `None`을 돌려줄 때마다 로그를 남긴다(정상 상태인 '미설정'만 info).

⚠️ 공급사 문서가 명시한 것들이 설계를 제약한다:
  - **영어 우선**("English is the primary training language") — 한국어는 관문 A0로 먼저 가른다.
  - **state가 클수록 정확도 하락** → 근거 청크만 보내고 `jev_state_max_chars`로 자른다.
  - **prompt injection 영향받음** → 판정은 **사람 검토 트리거로만**. 자동 삭제·게이트 직결 금지.
  - **`P(noul)` ≠ `1 - P(not noul)`** → 같은 명제를 긍정/부정 두 형태로 묻지 않는다.
  - **레이트 "동적 조정 중"** → 429도 그냥 `None`.

SDK(`typesafe-sdk`)는 **쓰지 않는다**. 계약이 단순해 이득이 없고, 의존성 추가는
arm64·CUDA 분리 이슈를 다시 만든다(조사 §6-1).
"""

from __future__ import annotations

__author__ = "Shin Hyunmook <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import datetime as _dt
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"

#: 일시적이라 재시도할 가치가 있는 상태. 그 외 4xx는 계약 문제라 재시도하지 않는다.
_RETRYABLE = (429, 502, 503, 504)

#: 버전 고정. `jev-latest`는 쓰지 않는다 — 판정자가 조용히 바뀌면 추세선이 끊긴다
#: (judge 교체 때 겪었다). 교체 시 `trend_breaks`에 등록한다.
DEFAULT_MODEL = "jev-1.13.0"

#: 이 모듈을 부를 수 있는 모듈. **평가 층만.** 의료·상담·STT는 영구 제외(PHI).
#: ⚠️ 테스트 모듈은 **일부러 넣지 않는다** — 여기에 `tests.`를 넣으면 목록이 운영
#: 사실과 달라지고, 가드가 실제로 무엇을 막는지 회귀가 증명하지 못한다.
#: 테스트는 `_caller_allowed`를 직접 패치하고, 가드 자체는 따로 검증한다.
_ALLOWED_CALLERS = ("core.faithfulness", "scripts.eval_faithfulness", "scripts.eval_jev_gate")

_DEFAULTS = {
    "jev_enabled": False,          # 기본 off — 켜기 전까지 기존 동작 100%
    "jev_model": DEFAULT_MODEL,
    "jev_state_max_chars": 6000,   # 공급사 약점 #5(state가 클수록 정확도 하락)
    "jev_daily_token_cap": 500000, # ≈$0.021/일. 피해 상한이기도 하다(키 유출 대비)
    "jev_timeout": 10.0,
    # 🔑 **레이트(429/503)와 일일 캡은 다른 것이다.**
    #    레이트는 초 단위 일시 현상이라 짧게 기다렸다 한 번 더 시도하는 게 맞다
    #    (공급사가 레이트를 "동적 조정 중"이라 적었다).
    #    반대로 **일일 캡에서는 기다리지 않는다** — 그건 우리 예산이자 키 유출 시
    #    피해 상한이고, 자정까지 블로킹하면 야간 배치가 통째로 멎는다. 이 레포는
    #    요청 경로 블로킹으로 스레드가 고갈된 이력이 있다(SSE·블로킹 DNS).
    #    캡 초과는 축 null로 두고 **다음 배치가 이어받는다**.
    "jev_retry_on_rate_limit": 1,   # 429·503 재시도 횟수(0=안 함)
    "jev_retry_max_wait": 15.0,     # 한 번에 기다릴 수 있는 상한(초). Retry-After가 이보다 크면 포기
}

_lock = threading.Lock()
_usage = {"day": None, "tokens": 0}


def _cfg(key):
    try:
        from core.config import load_config
        return load_config().get(key, _DEFAULTS[key])
    except Exception:  # noqa: BLE001 — 설정 못 읽으면 기본값(=꺼짐)
        return _DEFAULTS[key]


def api_key():
    """환경변수에서 키. 없으면 None(=미설정 = 정상 상태)."""
    return os.environ.get("TYPESAFE_API_KEY") or None


def enabled() -> bool:
    return bool(_cfg("jev_enabled")) and bool(api_key())


def _caller_allowed() -> bool:
    """호출 모듈 화이트리스트 — 배선 자체가 준수 장치가 되게 한다.

    v2.125.34에서 라이선스 금지 조항 모델을 일반 표면에만 배선한 것과 같은 구조.
    소스 스캔 회귀가 금지 모듈의 import를 따로 막지만, 런타임에서도 한 번 더 막는다
    (스캔은 동적 import를 못 본다).
    """
    import inspect
    for fr in inspect.stack()[2:]:
        mod = fr.frame.f_globals.get("__name__", "")
        if mod.startswith("core.jev"):
            continue
        return any(mod == a or mod.startswith(a) for a in _ALLOWED_CALLERS)
    return False


def _budget_ok(est_tokens: int) -> bool:
    """일일 입력 토큰 캡. 날이 바뀌면 리셋. 초과면 False(→ 축 null)."""
    cap = int(_cfg("jev_daily_token_cap") or 0)
    if cap <= 0:
        return False
    today = _dt.date.today().isoformat()
    with _lock:
        if _usage["day"] != today:
            _usage.update(day=today, tokens=0)
        if _usage["tokens"] + est_tokens > cap:
            return False
        _usage["tokens"] += est_tokens
        return True


def usage() -> dict:
    """오늘 누적 입력 토큰(추정). 진단·캡 조정 근거용."""
    with _lock:
        return dict(_usage)


def _retry_after(resp, attempt: int):
    """다음 시도까지 기다릴 초. **상한을 넘으면 None**(=포기).

    서버가 `Retry-After`를 주면 그것을 따르고, 없으면 1s·2s 백오프.
    🔑 상한(`jev_retry_max_wait`)이 있는 이유: 평가 배치는 **끝나야 하는 작업**이다.
    서버가 "10분 뒤에 오라"고 하면 기다리는 게 아니라 축을 null로 두고 다음 배치가
    이어받는 것이 맞다(fail-open 불변식).
    """
    cap = float(_cfg("jev_retry_max_wait") or 0)
    hdr = None
    try:
        hdr = (getattr(resp, "headers", None) or {}).get("Retry-After")
    except Exception:  # noqa: BLE001
        hdr = None
    if hdr:
        try:
            wait = float(str(hdr).strip())
        except (TypeError, ValueError):
            wait = 2.0 ** attempt      # HTTP-date 형식은 따르지 않는다(파싱 표면 최소화)
    else:
        wait = 2.0 ** attempt
    return None if wait > cap else max(wait, 0.0)


def ask_noul(state: str, questions, *, timeout=None, session=None):
    """근거(state) + 주장별 질문 → `{키: P(지지)}`. **실패는 전부 None.**

    Args:
        state: 근거 텍스트. `jev_state_max_chars`로 잘린다(공급사 약점 #5).
        questions: `{키: 지시문}` — 각 지시문은 **한 방향 명제**여야 한다.
                   같은 명제를 긍정/부정으로 나눠 묻지 말 것(`P(noul)` ≠ `1-P(not noul)`).
        session: 테스트·재사용용 requests 세션.

    Returns:
        `{키: 0.0~1.0}` · 또는 **None**(미설정·미도달·캡 초과·파싱 실패·비허용 호출자).
    """
    if not questions:
        return None
    if not _cfg("jev_enabled"):
        logger.info("[jev] 비활성(jev_enabled=false) — 축 null")
        return None
    key = api_key()
    if not key:
        logger.info("[jev] TYPESAFE_API_KEY 미설정 — 축 null")
        return None
    if not _caller_allowed():
        # 🔴 PHI 경계. 여기 걸리면 **배선이 잘못된 것**이라 크게 남긴다.
        logger.error("[jev] 비허용 호출자 — 거절. 평가 층 외에는 쓰지 않는다(PHI 경계)")
        return None

    limit = int(_cfg("jev_state_max_chars") or 0)
    if limit and len(state or "") > limit:
        logger.warning("[jev] state %d자 → %d자로 자름(약점 #5: state가 크면 정확도 하락)",
                       len(state), limit)
        state = state[:limit]

    # 토큰 추정은 보수적으로(한국어는 문자당 토큰이 크다). 캡은 피해 상한이므로
    # 과대 추정이 안전한 방향이다.
    est = (len(state or "") + sum(len(v) for v in questions.values())) // 2 + 1
    if not _budget_ok(est):
        logger.warning("[jev] 일일 토큰 캡 초과(추정 %d) — 축 null", est)
        return None

    payload = {"state": state,
               "model": _cfg("jev_model") or DEFAULT_MODEL,
               "questions": {k: {"type": "noul", "instructions": v}
                             for k, v in questions.items()}}
    sess = session
    if sess is None:
        try:
            import requests
            sess = requests
        except Exception as e:  # noqa: BLE001
            logger.warning("[jev] requests 없음(%s) — 축 null", e)
            return None

    tries = int(_cfg("jev_retry_on_rate_limit") or 0) + 1
    answers = None
    for attempt in range(tries):
        try:
            r = sess.post(API_URL, json=payload, timeout=timeout or _cfg("jev_timeout"),
                          headers={"Authorization": "Bearer %s" % key,
                                   "Content-Type": "application/json"})
            status = getattr(r, "status_code", None)
            if status == 200:
                answers = (r.json() or {}).get("answers") or {}
                break
            if status in _RETRYABLE and attempt < tries - 1:
                wait = _retry_after(r, attempt)
                if wait is None:
                    logger.warning("[jev] HTTP %s · 대기 상한 초과 — 축 null", status)
                    return None
                logger.warning("[jev] HTTP %s — %.1fs 후 재시도(%d/%d)",
                               status, wait, attempt + 1, tries - 1)
                time.sleep(wait)
                continue
            logger.warning("[jev] HTTP %s — 축 null", status)
            return None
        except Exception as e:  # noqa: BLE001 — 외부 서비스 때문에 배치가 멈추면 안 된다
            logger.warning("[jev] 호출 실패(%s) — 축 null", str(e)[:120])
            return None
    if answers is None:
        return None

    out = {}
    for k in questions:
        v = (answers.get(k) or {}).get("noul")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0:
            out[k] = float(v)
    if not out:
        logger.warning("[jev] 응답에 유효한 noul 없음 — 축 null")
        return None
    return out
