"""LLM 어댑터 계약 — `DEVELOPER_GUIDE §1`의 11항목을 회귀로 고정 (기획 Phase 0-3).

🔑 **왜 seam보다 먼저인가**: 어떤 추상화를 넣든(자체 seam·LiteLLM·사이드카) 이 11개를
깨뜨리면 안 된다. 이건 취향이 아니라 **전부 실제 사고에서 나온 것**이다.
seam 도입 **전에** 만들어야 도입 후 깨진 것을 잡을 수 있다.

**파라미터화가 요점이다.** 나중에 어댑터 후보(LiteLLM 등)를 검증할 때
전용 하네스를 새로 만들지 않는다 — 그러면 두 하네스가 갈라진다.

    pytest tests/test_llm_adapter_contract.py                    # 순수(기본)
    pytest tests/test_llm_adapter_contract.py --live             # 라이브 Ollama 대조
    pytest tests/test_llm_adapter_contract.py --live --adapter=litellm

여기 테스트는 두 종류다:
  - **순수**: 페이로드 빌더·정규화 함수를 직접 호출(모델 불요, 항상 실행)
  - **라이브**(`--live`): 실제 엔진이 그 필드를 **정말 존중하는지** 확인.
    문서를 근거로 채택하지 않는다 — [[feedback_measure_the_real_thing]]

기획: docs/01-plan/features/model-decoupling-and-eval-driven-development.plan.md §5
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import pytest

pytestmark = pytest.mark.contract


# ──────────────────────────────────────────────────────────────────────────
# §1-1 전역 num_ctx가 요청을 조용히 자른다 → 요청별 주입
# ──────────────────────────────────────────────────────────────────────────
def test_c1_openai_path_injects_num_ctx():
    """`/v1`을 Ollama 패스스루로 만들지 않은 이유 그 자체.
    패스스루면 전역 8192에 묶여 대형 시스템 프롬프트가 **에러 없이** 잘린다."""
    from routes.openai_compat import build_ollama_payload
    p = build_ollama_payload({"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                             num_ctx=16384)
    assert p["options"]["num_ctx"] == 16384, "요청별 num_ctx가 없으면 무성 절단이 돌아온다"


def test_c1_generate_path_raises_ctx_for_large_prompt():
    from routes.ollama import _estimate_tokens, _auto_num_ctx
    big = "가" * 12000                       # 한글 ≈1.2 tok/자 → 약 14K 토큰
    assert _estimate_tokens(big) > 8192
    assert _auto_num_ctx(big) is not None and _auto_num_ctx(big) > 8192


def test_c1_small_prompt_is_untouched():
    """작은 요청에 주입하면 요청마다 모델이 리로드돼 오실레이션이 생긴다."""
    from routes.ollama import _auto_num_ctx
    assert _auto_num_ctx("안녕") is None


def test_c1_estimator_biases_to_overestimate():
    """과소추정하면 잘린다 — 한글은 1.2 tok/자로 넉넉히 잡는다."""
    from routes.ollama import _estimate_tokens
    assert _estimate_tokens("가" * 100) >= 100


# ──────────────────────────────────────────────────────────────────────────
# §1-2 임베딩까지 전역 ctx를 먹어 OOM
# ──────────────────────────────────────────────────────────────────────────
def test_c2_embed_pins_small_ctx():
    """bge-m3가 전역 8192를 먹으면 KV 4.5GB → 상주 14b와 겹쳐 RAG가 500이 됐다.

    ⚠️ 소스 문자열이 아니라 **나가는 페이로드**를 본다 — 문자열 검사는 이관 때
    거동이 그대로인데도 깨지고(2026-08-10 §1-9 실측), 정작 거동은 검증하지 못한다.
    """
    from unittest.mock import patch
    from client import rag

    seen = {}

    def _fake_post(url, json=None, timeout=None):
        seen.update(json or {})

        class _R:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"embeddings": [[0.1, 0.2]]}
        return _R()

    with patch.object(rag.requests, "post", _fake_post):
        rag.embed_text("임베딩 계약 확인용 고유 문장 %s" % id(seen))
    assert seen.get("options", {}).get("num_ctx"), "임베딩에 num_ctx가 없으면 OOM이 돌아온다"
    assert seen["options"]["num_ctx"] <= 4096, "전역 8192를 먹으면 KV가 폭주한다"


# ──────────────────────────────────────────────────────────────────────────
# §1-3 OpenAI ↔ Ollama tool_calls 포맷
# ──────────────────────────────────────────────────────────────────────────
def test_c3_inbound_tool_args_string_to_object():
    """변환 없이 넘기면 멀티턴 에이전트 루프가 1스텝에 죽는다."""
    from routes.openai_compat import normalize_inbound_messages
    msgs = normalize_inbound_messages([
        {"role": "assistant", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "f", "arguments": '{"a": 1}'}}]}])
    args = msgs[0]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, dict) and args["a"] == 1


def test_c3_outbound_tool_args_object_to_string():
    from routes.openai_compat import ollama_final_to_openai
    out = ollama_final_to_openai(
        {"message": {"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}]}},
        cmpl_id="x", created=0, model="m")
    tc = out["choices"][0]["message"]["tool_calls"][0]
    assert isinstance(tc["function"]["arguments"], str)
    assert json.loads(tc["function"]["arguments"]) == {"a": 1}
    assert out["choices"][0]["finish_reason"] == "tool_calls"


# ──────────────────────────────────────────────────────────────────────────
# §1-4 멀티모달 입력 경로
# ──────────────────────────────────────────────────────────────────────────
def test_c4_data_uri_prefix_is_stripped():
    """Ollama는 raw base64만 받는다. 접두가 남으면 **조용히 무시**된다."""
    from routes.ollama import _normalize_images
    out = _normalize_images(["data:image/png;base64,AAAA"])
    assert out == ["AAAA"]


def test_c4_raw_base64_is_passthrough():
    from routes.ollama import _normalize_images
    assert _normalize_images(["AAAA"]) == ["AAAA"]


def test_c4_remote_url_is_rejected_not_fetched():
    """요청 경로에서 임의 URL fetch = SSRF 표면. 받아오지 않고 거절한다."""
    from routes.openai_compat import _convert_multimodal_content, UnsupportedImageURL
    with pytest.raises(UnsupportedImageURL):
        _convert_multimodal_content({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "http://169.254.169.254/x"}}]})


# ──────────────────────────────────────────────────────────────────────────
# §1-5 thinking 모델은 response가 빈다 → capability 확인 후 전달
# ──────────────────────────────────────────────────────────────────────────
def test_c5_think_only_sent_to_capable_models():
    """비-thinking 모델에 `think`를 주면 **요청 전체가 거절**된다
    (`"qwen2.5:14b" does not support thinking`)."""
    from routes.ollama import chat_payload
    p = chat_payload("m", [], data={"think": False}, supports_thinking=False)
    assert "think" not in p


def test_c5_think_sent_when_supported():
    from routes.ollama import chat_payload
    p = chat_payload("m", [], data={"think": False}, supports_thinking=True)
    assert p.get("think") is False


def test_c5_capability_gate_exists():
    """모델명 하드코딩이 아니라 `/api/show` capabilities로 판정해야 한다."""
    from routes.ollama import model_supports_thinking
    assert callable(model_supports_thinking)


# ──────────────────────────────────────────────────────────────────────────
# §1-6 후처리가 내용을 지운 사고 — 한국어화보다 내용 보존이 우선
# ──────────────────────────────────────────────────────────────────────────
def test_c6_filter_never_empties_content():
    from routes.vision import _keep_content
    assert _keep_content("", "Chest X-ray shows consolidation") == \
        "Chest X-ray shows consolidation"


def test_c6_symbols_only_result_falls_back():
    from routes.vision import _keep_content
    assert _keep_content(" .,-", "original text") == "original text"


def test_c6_good_filter_result_is_kept():
    from routes.vision import _keep_content
    assert _keep_content("흉부 X선 판독 결과입니다", "original") == "흉부 X선 판독 결과입니다"


# ──────────────────────────────────────────────────────────────────────────
# §1-7 모델 스왑(16GB 천장) — keep_alive를 안 보내면 상주가 풀린다
# ──────────────────────────────────────────────────────────────────────────
def test_c7_keep_alive_is_sent_with_requests():
    """상주 예열은 -1인데 이후 요청이 keep_alive 없이 오면 Ollama가 만료를 5분으로
    **재설정**해 고정이 풀린다(v2.124.82 실측)."""
    from routes import medical_law as ml
    assert hasattr(ml, "_keep_alive_for"), "keep_alive 정책 동반 전송이 없으면 상주가 풀린다"


# ──────────────────────────────────────────────────────────────────────────
# §1-9 구조화 출력(format) — 평가·분류의 기반
# ──────────────────────────────────────────────────────────────────────────
# ⚠️ 이 두 계약은 **소스 문자열**로 검사하고 있었다(`'"format"' in src`).
# v2.124.96 이관에서 호출부가 `schema=`로 넘기고 어댑터가 `format`으로 바꾸자
# 거동은 그대로인데 테스트만 깨졌다 — **문자열 검사는 리팩터링에 깨지면서
# 정작 거동은 검증하지 못한다**. 실제로 나가는 페이로드를 본다.
def _captured_judge_payload():
    """judge를 한 번 태우고 Ollama로 나간 페이로드를 돌려준다."""
    from unittest.mock import patch
    from core import faithfulness
    from core import model_router as mr

    seen = {}

    class _S:
        def post(self, url, json=None, timeout=None):
            seen.update(json or {})

            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {"response": "{}"}
            return _R()

    with patch.object(mr, "get_adapter",
                      lambda *a, **k: mr.OllamaAdapter(base="http://t", session=_S())):
        faithfulness._default_ask_json("sys", "prompt", {"type": "object"})
    return seen


def test_c9_format_schema_is_passed_through():
    """`format`이 전달되지 않으면 평가 하네스의 파싱 실패가 0에서 무너진다."""
    assert _captured_judge_payload().get("format") == {"type": "object"}


def test_c9_judge_uses_deterministic_decoding():
    """judge가 온도를 타면 같은 답변을 다르게 채점한다 — 추세가 무의미해진다."""
    assert _captured_judge_payload().get("options", {}).get("temperature") == 0


# ──────────────────────────────────────────────────────────────────────────
# §1-10 안전 계층 · §1-11 노드 폴백 경계
# ──────────────────────────────────────────────────────────────────────────
def test_c10_num_predict_is_capped():
    """인증 클라이언트의 초장문 요청이 단일 워커를 타임아웃까지 점유하는 것을 막는다."""
    from routes.openai_compat import build_ollama_payload, _MAX_NUM_PREDICT
    p = build_ollama_payload({"model": "m", "messages": [], "max_tokens": 10 ** 9},
                             num_ctx=16384)
    assert p["options"]["num_predict"] <= _MAX_NUM_PREDICT


def test_c11_dispatch_never_raises():
    """폴백이 예외를 전파하면 노드 하나가 죽을 때 엔드포인트가 같이 죽는다."""
    from core.orchestrator import dispatch
    # 🔑 local_fn은 **인자 없이** 호출된다 — 페이로드는 클로저로 캡처하는 규약이다
    #    (`_run_local`: `r = local_fn()`). 새 capability 배선 시 틀리기 쉬운 지점.
    out = dispatch.run("detect", b"x", local_fn=lambda: {"ok": True})
    assert out.get("ok") is True          # engine 스탬프가 붙으므로 부분 비교
    assert out.get("engine") == "gpu-local"


def test_c11_local_fn_failure_is_contained():
    from core.orchestrator import dispatch

    def boom():
        raise RuntimeError("local blew up")

    try:
        out = dispatch.run("detect", b"x", local_fn=boom)
    except RuntimeError:
        pytest.fail("dispatch가 예외를 전파하면 안 된다(폴백 경계)")
    assert out.get("available") is False and "error" in out


# ──────────────────────────────────────────────────────────────────────────
# 라이브 대조 — 엔진이 정말 존중하는가 (`--live`)
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.live
def test_live_format_constrains_output(live_adapter):
    """V2 — 이게 깨지면 어댑터 **즉시 기각**(구조화 출력 전 경로 붕괴)."""
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    out = live_adapter.ask_json("정수 하나만 답하라.", "1+1은?", schema)
    assert isinstance(out, dict) and isinstance(out.get("n"), int)


@pytest.mark.live
def test_live_num_ctx_actually_applies(live_adapter):
    """V1 — 이게 깨지면 어댑터 **즉시 기각**(무성 절단 재발).

    🔑 "HTTP 200이면 통과"로는 못 잡는다 — 어댑터가 num_ctx를 **조용히 무시해도**
    200이 오고 그게 정확히 §1-1의 사고다. `/api/ps`의 `context_length`도 요청별 값을
    반영하지 않는다(실측). 절단 구간의 `prompt_eval_count`가 유일한 결정적 관측점이다.

    2026-08-10 기준선(ollama·qwen2.5:14b): 8192→**4,098** · 16384→**8,194**.
    """
    small, large, scaled = live_adapter.num_ctx_scales()
    assert scaled, (f"num_ctx가 적용되지 않는다 — 절단 토큰이 안 변함 "
                    f"(8192→{small}, 16384→{large}). 무성 절단 위험.")
