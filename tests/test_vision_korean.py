"""routes.vision._ensure_korean 단위테스트 (P1 한글 네이티브 옵션).

핵심: force_korean은 '필터'(한글 없는 라인 삭제)라 영어중심 출력에 적용 시 내용 파괴 →
한국어 우세=force_korean 정리, 영어 우세=translate_core(의료 번역), 실패=폴백(비파괴), 비-ko=원문.
translate_core는 mock(네트워크/Ollama 불요).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import vision  # noqa: E402


def test_non_korean_passthrough():
    s = "Patchy opacity in the right lower lobe."
    assert vision._ensure_korean(s, "en") == s          # language!=ko → 원문 보존


def test_empty_passthrough():
    assert vision._ensure_korean("", "ko") == ""
    assert vision._ensure_korean(None, "ko") is None


def test_korean_dominant_uses_filter_not_translate(monkeypatch):
    import routes.writing as w
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return {"translation": {"ko": "SHOULD_NOT_BE_USED"}}

    monkeypatch.setattr(w, "translate_core", spy)
    s = "우측 하엽에 폐 음영이 다수 관찰되며 정상 소견입니다 추가 병변 없음"
    out = vision._ensure_korean(s, "ko")
    assert called["n"] == 0                              # 번역 미호출(필터 경로)
    assert "폐 음영" in out                               # 한국어 보존


def test_english_dominant_translates(monkeypatch):
    import routes.writing as w
    monkeypatch.setattr(w, "translate_core",
                        lambda text, targets, **k: {"translation": {"ko": "한글번역결과"}})
    s = "Findings: opacity in the right lower lobe. Impression: pneumonia likely."
    assert vision._ensure_korean(s, "ko") == "한글번역결과"   # 영어우세 → 번역(내용 보존)


def test_translate_failure_falls_back_nonfatal(monkeypatch):
    import routes.writing as w

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(w, "translate_core", boom)
    s = "Findings: opacity right lower lobe pneumonia likely consolidation nodule"
    out = vision._ensure_korean(s, "ko")                 # 예외 → force_korean 폴백
    assert isinstance(out, str)                          # 절대 raise 안 함(비파괴)


# ---------------------------------------------------------------------------
# 필터가 내용을 지우는 클래스 (그룹웨어 명함 OCR 실패 경로)
# ---------------------------------------------------------------------------

def test_keep_content_returns_original_when_filter_empties():
    """force_korean이 영문 우세 출력을 통째로 지우면 원문을 유지해야 한다."""
    from routes.vision import _keep_content

    assert _keep_content("", "LEE SOO KYUNG 010-1234-5678") == "LEE SOO KYUNG 010-1234-5678"
    assert _keep_content("   ", "INVOICE 77281") == "INVOICE 77281"


def test_keep_content_prefers_filtered_when_present():
    from routes.vision import _keep_content

    assert _keep_content("한국어 설명", "orig") == "한국어 설명"


def test_english_dominant_ocr_is_not_wiped(monkeypatch):
    """번역까지 실패해도 200 + 빈 문자열이 나가면 안 된다(호출자가 인식 실패로 오인)."""
    import routes.vision as V

    monkeypatch.setattr(V, "force_korean", lambda t: "")          # 필터가 전부 제거하는 상황
    monkeypatch.setattr(V, "translate_core", None, raising=False)  # 번역 경로 미사용
    out = V._ensure_korean("LEE SOO KYUNG DAESUNG 010-1234-5678", "ko")
    assert out.strip(), "빈 결과를 돌려주면 그룹웨어가 겪은 '인식되지 않았습니다'가 재현된다"


def test_symbol_only_filter_result_is_treated_as_empty():
    """기호만 남은 결과('* *')도 정보가 0이라 원문을 유지해야 한다."""
    from routes.vision import _keep_content

    assert _keep_content("* *", "INVOICE 77281") == "INVOICE 77281"
    assert _keep_content("...", "LEE SOO KYUNG") == "LEE SOO KYUNG"
    assert _keep_content("환자 상태 양호", "orig") == "환자 상태 양호"
    assert _keep_content("BP 140/90", "orig") == "BP 140/90"
