"""의료법 생성 경로 이관 — **동등성** 회귀 (기획 Phase 1-d 1단계).

🔑 이관의 유일한 결정적 근거는 **두 경로가 같은 요청을 만든다**는 것이다.
"돌아간다"는 증거가 아니다 — 조용히 달라진 필드 하나가 무성 결함이 된다
(§1-1 num_ctx 미전달·§1-5 think 오전달이 전부 그 클래스였다).

이 파일이 고정하는 것:
  ① 라우터 경로와 구 경로가 **동일한 Ollama 페이로드**를 만든다
     — 2026-08-24 D2(v2.125.14)부터 예외 하나: 라우터 경로 options에만
     `repeat_penalty 1.1` 핀이 합류한다(core/engine_defaults · 의도된 변경).
     구 경로(`_router_enabled()` 폴백=잔재 6)는 핀이 없다 — 문서화된 갭.
  ② `model_router_enabled=false`면 **구 경로 그대로**(1-e 롤백)
  ③ 모델 선택은 이관하지 않았다 — 호출자가 준 모델이 그대로 쓰인다
  ④ 실패 처리·후처리 위치가 종전과 같다
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import pytest

from routes import medical_law as ml


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _Capture:
    """두 경로가 무엇을 보내는지 붙잡는다."""

    def __init__(self, payload=None):
        self.calls = []
        self._p = payload or {"response": "답변입니다", "eval_count": 12,
                              "total_duration": 3_000_000}

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _Resp(self._p)


def _run(enabled, capture, **kw):
    """한 경로를 태우고 보낸 페이로드를 돌려준다.

    ⚠️ `_post`를 패치하지 **않는다** — 그러면 어느 전송로를 쓰는지 못 본다.
    `_ollama_session`만 갈아끼워서 **라우터가 그 세션을 실제로 쓰는지**까지 확인한다.
    """
    with patch.object(ml, "load_config", return_value={"model_router_enabled": enabled}), \
         patch.object(ml, "_think_off", return_value=kw.get("think_off", {})), \
         patch.object(ml, "_keep_alive_for", return_value=kw.get("keep_alive", {})), \
         patch.object(ml, "_ollama_session", capture):
        out = ml._generate("qwen2.5:14b", "질문", "시스템", kw.get("options"))
    return out, capture.calls[-1]["json"]


# ── ① 동등성 (핀 제외) ─────────────────────────────────────────────────────
def _pop_pin(routed: dict) -> dict:
    """라우터 페이로드에서 D2 핀을 검증하며 떼어낸다 — 핀 외 동등성만 남긴다."""
    assert routed["options"].pop("repeat_penalty") == 1.1, \
        "D2 핀(engine_defaults)이 라우터 경로에 실려야 한다"
    return routed


def test_router_and_legacy_build_identical_payload():
    _, legacy = _run(False, _Capture())
    _, routed = _run(True, _Capture())
    assert _pop_pin(routed) == legacy, \
        "핀 외에는 이관 전과 동일해야 한다 — 페이로드가 달라지면 무성 결함이 된다"


def test_identical_with_think_and_keep_alive():
    kw = {"think_off": {"think": False}, "keep_alive": {"keep_alive": "-1"}}
    _, legacy = _run(False, _Capture(), **kw)
    _, routed = _run(True, _Capture(), **kw)
    assert _pop_pin(routed) == legacy
    assert routed["think"] is False and routed["keep_alive"] == "-1"


def test_identical_with_custom_options():
    kw = {"options": {"temperature": 0.3, "num_predict": 1200}}
    _, legacy = _run(False, _Capture(), **kw)
    _, routed = _run(True, _Capture(), **kw)
    assert _pop_pin(routed) == legacy
    assert routed["options"]["num_predict"] == 1200


def test_default_options_preserved():
    _, routed = _run(True, _Capture())
    assert routed["options"] == {"temperature": 0.3, "num_predict": 1024,
                                 "repeat_penalty": 1.1}   # D2 핀(engine_defaults)


def test_stream_is_false_and_system_present():
    _, routed = _run(True, _Capture())
    assert routed["stream"] is False and routed["system"] == "시스템"


# ── ③ 모델 선택은 이관하지 않았다 ──────────────────────────────────────────
def test_caller_model_wins_over_routing_table():
    """`_default_model()`이 상주 모델을 따라간다 — 표의 고정값으로 바꾸면
    quality 모드에서 매 요청 축출이 재발한다(2026-08-04 실측)."""
    cap = _Capture()
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", cap):
        ml._generate("qwen3.6:35b-a3b", "q", "s")
    assert cap.calls[-1]["json"]["model"] == "qwen3.6:35b-a3b"


# ── ② 롤백 경로 ────────────────────────────────────────────────────────────
def test_disabled_uses_legacy_session():
    """토글을 끄면 **구 경로 객체**로 나간다 — 라우터를 아예 안 탄다."""
    cap = _Capture()
    with patch.object(ml, "load_config", return_value={"model_router_enabled": False}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", cap), \
         patch("core.model_router.generate",
               side_effect=AssertionError("라우터를 타면 안 된다")):
        ml._generate("m", "q", "s")
    assert cap.calls and cap.calls[-1]["url"].endswith("/api/generate")


def test_default_is_enabled():
    with patch.object(ml, "load_config", return_value={}):
        assert ml._router_enabled() is True


def test_config_failure_defaults_to_enabled():
    with patch.object(ml, "load_config", side_effect=RuntimeError("boom")):
        assert ml._router_enabled() is True


# ── ④ 실패·후처리 ──────────────────────────────────────────────────────────
def test_router_failure_returns_gen_error_like_legacy():
    """실패 반환 shape이 달라지면 `_gen_failed` 판정이 깨지고 502가 캐시된다."""
    class Boom:
        def post(self, *a, **k):
            raise RuntimeError("engine down")
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", Boom()):
        text, tokens, ms = ml._generate("m", "q", "s")
    assert text == ml._GEN_ERROR and tokens == 0 and ms == 0


def test_router_import_failure_falls_back_to_legacy():
    """라우터 자체가 죽어도 생성은 계속돼야 한다(강등)."""
    cap = _Capture()
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", cap), \
         patch("core.model_router.generate", side_effect=RuntimeError("router broken")):
        text, _, _ = ml._generate("m", "q", "s")
    assert text and text != ml._GEN_ERROR, "구 경로로 강등돼야 한다"
    assert cap.calls, "구 경로가 실제로 호출돼야 한다"


def test_korean_postprocess_stays_in_caller():
    """`force_korean`은 seam **위**다 — §1-6에서 필터가 내용을 지운 사고가 있었다."""
    called = {}

    def fake_fk(t):
        called["hit"] = True
        return t + "[fk]"

    cap = _Capture()
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "force_korean", fake_fk), \
         patch.object(ml, "_ollama_session", cap):
        text, _, _ = ml._generate("m", "q", "s")
    assert called.get("hit") and text.endswith("[fk]")


def test_tokens_and_duration_mapping_unchanged():
    cap = _Capture({"response": "x", "eval_count": 42, "total_duration": 7_000_000})
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", cap):
        _, tokens, ms = ml._generate("m", "q", "s")
    assert tokens == 42 and ms == 7


# ── 정책은 task 기준(모델을 바꿔도 안 풀린다) ───────────────────────────────
def test_policy_is_task_scoped_not_model_scoped():
    from core import model_router as mr
    class Remote(mr.OllamaAdapter):
        is_local = False
    with pytest.raises(mr.PolicyViolation):
        mr.generate("medical_law.answer", "q", model="gpt-4o", adapter=Remote())


# ── 🔴 전송로 — Fair Queue를 우회하면 안 된다 ──────────────────────────────
def test_router_uses_caller_session_not_its_own():
    """`_ollama_session`은 단순 세션이 아니라 **`_GatedOllamaSession`**이다 —
    `ChatFairQueue(max_global=3, max_per_user=1)` 슬롯을 잡는다.
    어댑터가 자기 세션을 만들면 **큐 제어가 통째로 빠지고** keep-alive 풀도 잃는다.

    2026-08-10 실측: 이걸 빠뜨린 채 배포하려다 `deploy.sh` 3단계에서 회귀 3건이 잡았다
    (실패 주입 테스트에 **실제 LLM 응답**이 돌아왔다 = 주입이 우회됨).
    페이로드만 비교하는 테스트로는 못 잡는다 — **전송로도 봐야 한다**.
    """
    cap = _Capture()
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_ollama_session", cap):
        ml._generate("m", "q", "s")
    assert cap.calls, "라우터가 호출자의 세션을 쓰지 않았다 = Fair Queue 우회"


def test_router_uses_caller_base_url():
    """base도 호출자 것을 써야 한다 — 어댑터 기본값(env)으로 새면 사이드카·터널
    구성에서 다른 엔진을 부른다. (테스트에선 init 미주입이라 `_OLLAMA_BASE`가 None일 수
    있으므로 명시 주입하고 확인한다.)"""
    cap = _Capture()
    with patch.object(ml, "load_config", return_value={"model_router_enabled": True}), \
         patch.object(ml, "_think_off", return_value={}), \
         patch.object(ml, "_keep_alive_for", return_value={}), \
         patch.object(ml, "_OLLAMA_BASE", "http://engine.test:9999"), \
         patch.object(ml, "_ollama_session", cap):
        ml._generate("m", "q", "s")
    assert cap.calls[-1]["url"] == "http://engine.test:9999/api/generate"
