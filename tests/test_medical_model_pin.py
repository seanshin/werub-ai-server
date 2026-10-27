"""규제 라우팅 가드 회귀 — 자동 라우팅의 자체 파인튜닝 모델 배제 (opt-in).

식약처 LLM 가이드라인 대응: 동일 법인 확정(2026-07-19)으로 자체 모델(werub*)이
허가 대상 기능 경로에 관여하지 않음을 설정 드리프트와 무관하게 보장하는 스위치.
기본 off=기존 동작 100% 불변."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routes.medical as medical  # noqa: E402
from routes.medical import _get_medical_model, _pin_public, is_custom_model  # noqa: E402


def _with_cfg(cfg):
    return patch.object(medical, "_load_config", lambda: cfg)


def test_is_custom_model():
    assert is_custom_model("werubmedv2")
    assert is_custom_model("werub-medical:7b")
    assert is_custom_model("WERUBMEDV2:latest")   # 대소문자 무관
    assert not is_custom_model("qwen2.5:14b")
    assert not is_custom_model(None)


def test_pin_off_by_default_unchanged():
    """config 미설정=기존 동작 불변(werub 그대로)."""
    with _with_cfg({}):
        assert _pin_public("werubmedv2") == "werubmedv2"
        assert _pin_public("qwen2.5:14b") == "qwen2.5:14b"


def test_pin_on_substitutes_custom_only():
    with _with_cfg({"medical_pin_public_models": True}):
        assert _pin_public("werubmedv2") == "qwen2.5:14b"
        assert _pin_public("werub-medical:7b") == "qwen2.5:14b"
        assert _pin_public("thewindmom/llama3-med42-8b") == "thewindmom/llama3-med42-8b"


def test_pin_substitute_config_override():
    with _with_cfg({"medical_pin_public_models": True,
                    "medical_public_substitute": "qwen2.5:7b"}):
        assert _pin_public("werubmedv2") == "qwen2.5:7b"


def test_pin_config_failure_degrades_to_unchanged():
    def _boom():
        raise RuntimeError("config unavailable")
    with patch.object(medical, "_load_config", _boom):
        assert _pin_public("werubmedv2") == "werubmedv2"


def test_get_medical_model_auto_routing_pinned():
    """heavy 슬롯이 werubmedv2여도 가드 on이면 공개 모델 반환."""
    with _with_cfg({"medical_pin_public_models": True}), \
         patch.object(medical, "_load_active_model_config",
                      lambda: {"heavy_model": "werubmedv2", "light_model": "qwen2.5:7b"}):
        assert _get_medical_model("summarize") == "qwen2.5:14b"
        assert _get_medical_model("reference") == "qwen2.5:7b"  # light=공개, 무대체


def test_get_medical_model_explicit_user_model_respected():
    """명시 지정은 가드 대상 아님(추적 가능한 의도적 선택)."""
    with _with_cfg({"medical_pin_public_models": True}):
        assert _get_medical_model("summarize", user_model="werubmedv2") == "werubmedv2"
