"""히스토리 요약 모델 선택 — 하드코딩 7b 추가 적재 방지 가드(2026-08-04).

요약기가 `qwen2.5:7b`를 하드코딩해 세션이 길어질 때마다 5.2GB를 **추가로** 적재했다.
상주 정책이 '요청 시 탑재'라 기본 모델과 겹치면 VRAM이 15.7GB로 차서 축출을 유발한다.
→ 이미 떠 있는 상주/기본 모델을 재사용한다.
"""
from unittest.mock import patch
from core.chat_memory import _summary_model


def test_uses_resident_model():
    with patch("core.config.load_config", return_value={"default_model": "qwen2.5:14b"}):
        assert _summary_model() == "qwen2.5:14b"


def test_follows_explicit_resident_override():
    """llm_resident_model이 지정되면 그쪽을 따른다(채팅 기본과 상주 분리 가능)."""
    cfg = {"default_model": "qwen2.5:14b", "llm_resident_model": "qwen2.5:7b"}
    with patch("core.config.load_config", return_value=cfg):
        assert _summary_model() == "qwen2.5:7b"


def test_config_failure_falls_back():
    """설정 조회가 깨져도 요약이 멈추면 안 된다."""
    with patch("core.config.load_config", side_effect=RuntimeError("boom")):
        assert _summary_model() == "qwen2.5:7b"
