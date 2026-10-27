"""X7 — meeting `language` 정규화·검증·위임 로컬 강제 (2026-08-26).

배경: `language:"auto"`(기획서 문법·정본 문서엔 ko만)가 무검증으로 faster-whisper에
직결돼 ①로컬 경로=다운로드·추출 후 transcribe에서야 ValueError ②위임 경로=클라이언트가
language를 싣지 않아 **모든 비-ko 값이 조용히 ko로 낙착**(prompt/hotwords 로컬 강제
처치에서 language만 누락). 처치=auto→None(자동 감지)·접수 400·비-ko 로컬 강제.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import meeting_stt  # noqa: E402
from core.meeting_stt import normalize_language  # noqa: E402


@pytest.fixture()
def _codes(monkeypatch):
    """코드 목록을 결정적으로 고정(설치 환경 무관)."""
    monkeypatch.setitem(meeting_stt._LANG_CODES_CACHE, "loaded", True)
    monkeypatch.setitem(meeting_stt._LANG_CODES_CACHE, "codes", {"ko", "en", "ja"})


# ── normalize_language ─────────────────────────────────────────────────────

def test_default_is_ko(_codes):
    assert normalize_language(None) == ("ko", None)


def test_auto_and_empty_mean_autodetect(_codes):
    assert normalize_language("auto") == (None, None)
    assert normalize_language("AUTO ") == (None, None)
    assert normalize_language("") == (None, None)


def test_valid_code_passes(_codes):
    assert normalize_language("en") == ("en", None)
    assert normalize_language(" Ja ") == ("ja", None)


def test_invalid_code_rejected(_codes):
    lang, err = normalize_language("xx")
    assert lang is None and "unsupported" in err


def test_non_string_rejected(_codes):
    lang, err = normalize_language(3)
    assert lang is None and err


def test_fail_open_without_codes(monkeypatch):
    """코드 목록 미가용(라이브러리 미설치·내부 배치 변경) 시 현행(무검증)과 동일 통과."""
    monkeypatch.setitem(meeting_stt._LANG_CODES_CACHE, "loaded", True)
    monkeypatch.setitem(meeting_stt._LANG_CODES_CACHE, "codes", None)
    assert normalize_language("xx") == ("xx", None)
    assert normalize_language("auto") == (None, None)   # auto 매핑은 목록 무관


# ── stt_options 배선 ───────────────────────────────────────────────────────

def test_stt_options_auto_maps_to_none(_codes):
    from core.meeting_pipeline import stt_options
    lang, prompt, hot, rep = stt_options({"language": "auto"})
    assert lang is None


def test_stt_options_default_and_valid_unchanged(_codes):
    from core.meeting_pipeline import stt_options
    assert stt_options({})[0] == "ko"
    assert stt_options({"language": "en"})[0] == "en"


def test_stt_options_invalid_passthrough(_codes):
    """접수 검증(400) 이전에 저장된 구 잡의 무효 값은 조용한 ko 저하 없이 그대로 —
    종전대로 transcribe에서 크게 실패한다(무성 실패 금지)."""
    from core.meeting_pipeline import stt_options
    assert stt_options({"language": "xx"})[0] == "xx"


# ── 위임 로컬 강제 ─────────────────────────────────────────────────────────

def test_local_only_conditions():
    from core.meeting_pipeline import _stt_local_only
    assert _stt_local_only("ko", None, None) is False       # 기본 = 위임 허용
    assert _stt_local_only("en", None, None) is True        # 비-ko = 무성 소실 방지
    assert _stt_local_only(None, None, None) is True        # 자동 감지 = 로컬만 지원
    assert _stt_local_only("ko", "프롬프트", None) is True   # 기존 처치 유지
    assert _stt_local_only("ko", None, "용어1, 용어2") is True
