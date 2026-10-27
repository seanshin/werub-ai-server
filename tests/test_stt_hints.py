"""STT 용어 힌트 캡 — whisper 프롬프트 창(224토큰) 초과로 전사가 파괴되던 결함 회귀.

edu 실측(2026-07-30·181초 음원): 힌트 30개(168자)면 `completed`인데 단어가 311→94(−70%)로
사라지고, 38개(207자)면 `The maximum decoding length must be > 0`로 실패했다.
원인은 캡이 **600자**(≈720토큰)라 창을 3배 넘긴 것. 캡을 토큰 기준으로 되돌린다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stt_hints import (DEFAULT_MAX_HINT_TOKENS, WHISPER_PROMPT_WINDOW_TOKENS,  # noqa: E402
                            cap_hints, estimate_tokens, hint_report)

# edu가 실제로 보낸 형태의 의료 용어(길이 특성이 같아야 회귀 의미가 있다)
TERMS = ["요로감염", "재원일수", "항균력", "중심정맥관", "클로르헥시딘", "메티실린",
         "포도알균", "코호트 격리", "손위생", "카테터", "혈류감염", "다제내성균",
         "격리해제", "접촉주의", "비말주의", "공기주의", "표준주의", "환경소독",
         "멸균", "소독제", "역가", "잔류효과", "알코올농도", "발관시점",
         "삽입부위", "드레싱", "감시배양", "유행조사", "역학조사", "감염관리실",
         "항생제내성", "감수성검사", "배양검사", "그람염색", "혈액배양", "소변배양",
         "객담배양", "창상감염"]                      # 38개 = edu 기본값(실패 조합)


def test_window_constants():
    """창 224토큰 · 힌트 상한은 그보다 충분히 작아야 한다(디코딩 자리를 남긴다)."""
    assert WHISPER_PROMPT_WINDOW_TOKENS == 224
    assert DEFAULT_MAX_HINT_TOKENS < WHISPER_PROMPT_WINDOW_TOKENS / 2


def test_estimate_tokens_korean_is_heavier():
    """한국어는 자당 ~1.2토큰 — 문자수 캡이 위험했던 이유."""
    assert estimate_tokens("요로감염") > 4
    assert estimate_tokens("abcd") < estimate_tokens("가나다라")
    assert estimate_tokens("") == 0


def test_edu_failure_case_is_capped():
    """edu 실패 조합(38개)이 창 안으로 잘려야 한다."""
    capped, used, dropped = cap_hints(", ".join(TERMS))
    assert used + dropped == len(TERMS)
    assert dropped > 0                                   # 38개는 물리적으로 못 들어간다
    assert estimate_tokens(capped) <= DEFAULT_MAX_HINT_TOKENS


def test_edu_degradation_case_is_capped():
    """30개(168자·단어 −70% 유발)도 상한 아래로 잘린다."""
    capped, used, _ = cap_hints(", ".join(TERMS[:30]))
    assert estimate_tokens(capped) <= DEFAULT_MAX_HINT_TOKENS
    assert used < 30


def test_short_list_untouched():
    """짧은 목록은 그대로 — 정상 사용을 막지 않는다."""
    short = ["요로감염", "재원일수", "중심정맥관"]
    capped, used, dropped = cap_hints(", ".join(short))
    assert capped == ", ".join(short) and used == 3 and dropped == 0


def test_cut_only_at_item_boundary():
    """항목 중간을 자르면 없는 토막을 바이어스해 오히려 오인식을 만든다."""
    capped, _, _ = cap_hints(", ".join(TERMS))
    for part in capped.split(", "):
        assert part in TERMS


def test_single_overlong_item_survives():
    """첫 항목 하나가 상한을 넘어도 통째로 버리지 않는다(호출자 의도 소실 방지)."""
    long_term = "가" * 300
    capped, used, dropped = cap_hints(long_term)
    assert capped == long_term and used == 1 and dropped == 0


def test_empty_inputs():
    assert cap_hints(None) == (None, 0, 0)
    assert cap_hints("   ") == (None, 0, 0)
    assert cap_hints(", , ") == (None, 0, 0)


def test_hint_report_explains_drop():
    r = hint_report(38, 12, 26)
    assert r["sent"] == 38 and r["used"] == 12 and r["dropped"] == 26
    assert "reason" in r and "224" in r["reason"]
    assert "reason" not in hint_report(3, 3, 0)          # 안 잘렸으면 사유 없음


def test_meeting_stt_options_applies_cap():
    """파이프라인 진입점에서 캡이 실제로 걸리는지(배선 검증)."""
    from core.meeting_pipeline import stt_options
    lang, prompt, hot, rep = stt_options({"vocabulary": TERMS})
    assert lang == "ko" and prompt is None
    assert estimate_tokens(hot) <= DEFAULT_MAX_HINT_TOKENS
    assert rep["sent"] == 38 and rep["dropped"] > 0

    # 힌트를 안 보내면 보고도 없다(기존 계약 불변)
    assert stt_options({}) == ("ko", None, None, None)
