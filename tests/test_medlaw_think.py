"""의료법 RAG 경로 think 주입 — thinking 모델 빈 응답 방지 가드(2026-08-03).

`qwen3.6:35b-a3b`를 `/api/medical-law/ask`에 주면 think 미전달 시 **빈 응답**이 온다.
반대로 비-thinking 모델에 think를 실으면 Ollama가 요청 전체를 거절한다(v2.124.59).
→ capabilities 확인 후 지원 모델에만 넣는다.
"""
from unittest.mock import patch
from routes import medical_law as ML


def test_thinking_model_gets_think_false():
    with patch("routes.ollama.model_supports_thinking", return_value=True):
        assert ML._think_off("qwen3.6:35b-a3b") == {"think": False}


def test_non_thinking_model_gets_nothing():
    """비-thinking 모델에 think를 실으면 Ollama가 요청을 거절한다."""
    with patch("routes.ollama.model_supports_thinking", return_value=False):
        assert ML._think_off("qwen2.5:14b") == {}


def test_capability_lookup_failure_is_safe():
    """확인 실패 시엔 넣지 않는다 = 기존 동작 보존(fail-safe)."""
    with patch("routes.ollama.model_supports_thinking", side_effect=RuntimeError("down")):
        assert ML._think_off("anything") == {}
