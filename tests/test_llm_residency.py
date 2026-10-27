"""기본 LLM 상주 정책 — 상주(pin) vs 요청 시 탑재(on-demand).

파인튜닝처럼 VRAM을 크게 쓰는 작업과 공존하려면 기본 모델(~10.5GB)이 상시 점유하면 안 된다.
기본값은 **기존 동작(상주)** 이어야 하고, 끄는 것은 명시적 설정이어야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_residency import (RESIDENT_KEEP_ALIVE, is_resident,          # noqa: E402
                                keep_alive_for, resident_model)


def test_default_is_resident():
    """설정이 없으면 기존 동작 유지 — 이 변경으로 다른 배포가 조용히 바뀌면 안 된다."""
    assert is_resident({}) is True
    assert is_resident({"default_model": "qwen2.5:14b"}) is True


def test_explicit_off():
    assert is_resident({"llm_resident": False}) is False


def test_string_values_are_honoured():
    """운영자가 JSON·콘솔에서 문자열로 넣는 실수를 조용한 오작동으로 만들지 않는다."""
    for falsy in ("false", "False", "0", "off", "no", ""):
        assert is_resident({"llm_resident": falsy}) is False, falsy
    for truthy in ("true", "1", "on", "yes"):
        assert is_resident({"llm_resident": truthy}) is True, truthy


def test_non_dict_config_is_safe():
    assert is_resident(None) is True
    assert is_resident("garbage") is True


def test_keep_alive_value():
    assert keep_alive_for({}) == RESIDENT_KEEP_ALIVE == -1
    assert keep_alive_for({"llm_resident": False}) is None      # 예열 안 함


# ── 사전 적재 대상 모델 (v2.124.62) ─────────────────────────────────────────

def test_resident_model_defaults_to_chat_default():
    assert resident_model({"default_model": "qwen2.5:14b"}) == "qwen2.5:14b"


def test_resident_model_can_differ_from_chat_default():
    """품질은 14b로 쓰되 상주는 7b로 두는 선택 — 16GB에서 실제로 의미가 있다."""
    cfg = {"default_model": "qwen2.5:14b", "llm_resident_model": "qwen2.5:7b"}
    assert resident_model(cfg) == "qwen2.5:7b"


def test_resident_model_blank_falls_back():
    assert resident_model({"default_model": "d", "llm_resident_model": ""}) == "d"
    assert resident_model({"default_model": "d", "llm_resident_model": None}) == "d"
    assert resident_model({}, fallback="f") == "f"
    assert resident_model(None) == ""
    assert resident_model({"llm_resident_model": " qwen2.5:7b "}) == "qwen2.5:7b"
