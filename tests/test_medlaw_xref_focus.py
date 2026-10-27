"""벌칙 역참조 강화 회귀 (2026-08-04, 35b 기준 강화 ①②).

①게이트 확장 — 금지·의무형 질의에서도 벌칙 역참조가 발동해야 한다.
  실증: "태아 성감별 고지 금지 의무는?"에서 정답 벌칙 조(제88조의2)가 근거에 없어
  모델이 생명윤리법을 창작했다. 정작 그 조문은 코퍼스에 있었고 제20조를 역참조까지 했다.

②주석 초점 필터 — 필요한 대응은 주석 안에 있었으나 16~17개에 묻히고 방향이 반대였다.
  검색된 행위 조문과 교집합만 남기고 `행위조 → 이 벌칙조` 방향으로 뒤집는다.
"""
import pytest

from routes.medical_law import (
    _DUTY_INTENT_TERMS,
    _PENALTY_INTENT_TERMS,
    _penalty_xref_note,
    _xref_focus_articles,
)

# 실제 코퍼스 형태 — 제88조가 위반 조항 다수를 나열
PENALTY_BODY = (
    "제88조(벌칙) 다음 각 호의 어느 하나에 해당하는 자는 3년 이하의 징역이나 "
    "3천만원 이하의 벌금에 처한다. 1. 제19조, 제21조제2항, 제22조제3항, 제27조제3항, "
    "제33조제4항, 제35조제1항, 제38조제3항을 위반한 자"
)


class TestGateTerms:
    def test_duty_terms_cover_the_real_case(self):
        """배치 문항이 게이트에 걸려야 한다 — 이게 안 걸려서 창작이 났다."""
        q = "태아 성감별 고지 금지 의무는?"
        assert not any(t in q for t in _PENALTY_INTENT_TERMS)   # 처벌 어휘 없음
        assert any(t in q for t in _DUTY_INTENT_TERMS)          # 의무형으로는 걸림

    @pytest.mark.parametrize("q", [
        "의료인의 환자 비밀누설 금지 의무는?",
        "진료기록부 작성 의무를 위반하면?",
        "의료광고에서 제한되는 내용은?",
    ])
    def test_duty_forms_match(self, q):
        assert any(t in q for t in _DUTY_INTENT_TERMS)

    def test_plain_question_not_matched(self):
        """무관한 질의까지 발동시키지 않는다(과발동 방지)."""
        q = "진료기록부의 보존기간은?"
        assert not any(t in q for t in _PENALTY_INTENT_TERMS)
        assert not any(t in q for t in _DUTY_INTENT_TERMS)


class TestFocusExtraction:
    def _hit(self, art, title):
        return {"metadata": {"article_number": art, "article_title": title}}

    def test_penalty_articles_excluded_from_focus(self):
        hits = [self._hit("제19조", "비밀 누설 금지"),
                self._hit("제88조", "벌칙"),
                self._hit("제92조", "과태료")]
        assert _xref_focus_articles(hits) == {"제19조"}

    def test_empty_when_only_penalty_chunks(self):
        hits = [self._hit("제88조", "벌칙")]
        assert _xref_focus_articles(hits) == set()

    def test_tolerates_missing_metadata(self):
        assert _xref_focus_articles([{}, {"metadata": None}, None]) == set()
        assert _xref_focus_articles(None) == set()


class TestXrefNote:
    def test_without_focus_lists_all(self):
        note = _penalty_xref_note(PENALTY_BODY)
        assert "제19조" in note and "제33조제4항" in note
        assert note.count(",") >= 5                     # 긴 목록 그대로

    def test_focus_narrows_and_flips_direction(self):
        note = _penalty_xref_note(PENALTY_BODY, focus={"제19조"})
        assert "제19조" in note
        assert "위반**을 처벌한다" in note              # 방향 반전 표기
        assert "제33조제4항" not in note                # 무관 조항 제거
        assert "인용하지 말 것" in note

    def test_focus_keeps_clause_level_token(self):
        """항 번호가 붙은 참조도 조 단위 초점으로 잡힌다(제27조 → 제27조제3항)."""
        note = _penalty_xref_note(PENALTY_BODY, focus={"제27조"})
        assert "제27조제3항" in note
        assert "제19조" not in note

    def test_fail_open_when_focus_misses(self):
        """교집합이 비면 필터링하지 않는다 — 근거를 지우는 것보다 노이즈가 낫다."""
        note = _penalty_xref_note(PENALTY_BODY, focus={"제999조"})
        assert "제19조" in note and "제33조제4항" in note

    def test_self_reference_excluded(self):
        note = _penalty_xref_note(PENALTY_BODY, focus={"제88조"})
        assert "제88조 위반" not in note                # 자기 조번호는 제외

    def test_empty_body(self):
        assert _penalty_xref_note("") == ""
        assert _penalty_xref_note(None) == ""
