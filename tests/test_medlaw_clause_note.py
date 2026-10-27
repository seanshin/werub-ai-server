"""단서 부기(conditions_kept 폐루프 16) 회귀.

핵심 규율 3가지를 고정한다:
  ①조 귀속은 **괄호 제목이 붙은 헤딩**으로만 — 본문 교차참조("법 제22조제2항")를 헤딩으로
    오인하면 단서가 엉뚱한 조에 붙어 게이트가 통째로 무발동한다(실측 사고).
  ②판정은 단서별 **내용 커버리지** — 답변 전체의 '다만' 어휘 유무로 보면 다른 단서를
    빠뜨린 경우를 놓친다(재현율 0.50→0.70).
  ③부기는 **근거 원문 그대로** — 창작 0.
"""
import pytest

from routes.medical_law import (
    _append_clause_note,
    _danseo_by_article,
    _evidence_text,
    _missing_clauses,
)

# 실제 코퍼스 원문(간호기록부 문항 근거 청크 선두) — 단서 앞에 교차참조가 있는 형태
EV_15 = (
    "제15조(진료기록부 등의 보존) ① 의료인이나 의료기관 개설자는 법 제22조제2항에 따른 "
    "진료기록부등을 다음 각 호에 정하는 기간 동안 보존하여야 한다. "
    "다만, 계속적인 진료를 위하여 필요한 경우에는 1회에 한정하여 다음 각 호에 정하는 "
    "기간의 범위에서 그 기간을 연장하여 보존할 수 있다.\n"
    "1. 환자 명부 : 5년\n2. 진료기록부 : 10년\n7. 간호기록부 : 5년\n"
)


def test_heading_only_attribution():
    """단서는 헤딩 제15조에 붙어야 한다 — 본문의 '법 제22조제2항'에 붙으면 안 됨."""
    dmap = _danseo_by_article(EV_15)
    assert "제15조" in dmap
    assert "1회에 한정" in dmap["제15조"][0]
    assert "제22조" not in dmap  # 교차참조는 헤딩이 아니다


def test_fires_when_answer_drops_clause():
    ans = "간호기록부의 보존기간은 의료법 시행규칙 제15조에 따라 5년입니다."
    miss = _missing_clauses(ans, EV_15)
    assert len(miss) == 1
    assert miss[0][0] == "제15조"
    out = _append_clause_note(ans, EV_15)
    assert "[인용 조문의 단서(근거 원문)]" in out
    assert "1회에 한정하여" in out
    assert out.startswith(ans)  # 원 답변은 훼손하지 않는다


def test_quiet_when_answer_already_has_clause():
    ans = ("간호기록부는 제15조에 따라 5년 보존하며, 다만 계속적인 진료를 위하여 필요한 "
           "경우에는 1회에 한정하여 그 기간의 범위에서 연장하여 보존할 수 있습니다.")
    assert _missing_clauses(ans, EV_15) == []
    assert _append_clause_note(ans, EV_15) == ans


def test_quiet_when_article_not_cited():
    """답변이 인용하지 않은 조의 단서는 부기하지 않는다(무관한 단서 오염 방지)."""
    ans = "해당 내용은 제99조에 따릅니다."
    assert _missing_clauses(ans, EV_15) == []


def test_no_citation_no_fire():
    assert _missing_clauses("보존기간은 5년입니다.", EV_15) == []


def test_limit_caps_payload():
    ev = EV_15 + (
        "제20조(기록 열람) ① 열람을 허용하여야 한다. "
        "다만, 환자가 지정하는 대리인이 신분증을 제출하는 경우에는 그러하지 아니하다. "
        "다만, 응급환자로서 동의를 받을 수 없는 특별한 사정이 있으면 사후에 갈음할 수 있다.\n"
    )
    ans = "제15조와 제20조에 따릅니다."
    assert len(_missing_clauses(ans, ev, limit=1)) == 1
    assert len(_missing_clauses(ans, ev, limit=2)) == 2


def test_coverage_threshold_respected():
    ans = "간호기록부는 제15조에 따라 5년이며, 계속적인 진료를 위하여 1회에 한정하여 연장할 수 있습니다."
    assert _missing_clauses(ans, EV_15, coverage=0.4) == []      # 내용이 실렸다
    assert _missing_clauses(ans, EV_15, coverage=1.0) != []      # 완전일치 요구 시엔 발동


def test_evidence_text_prefers_full_chunk_text():
    hits = [{"text": EV_15}, {"text": ""}, {}]
    ev = _evidence_text(hits)
    assert "1회에 한정" in ev


def test_evidence_text_tolerates_bad_articles():
    """시드 조문 row가 이형이어도 예외 없이 RAG 본문만으로 동작(무성 실패 금지)."""
    ev = _evidence_text([{"text": EV_15}], articles=[{"nope": 1}, None])
    assert "제15조" in ev


@pytest.mark.parametrize("bad", ["", None])
def test_empty_inputs_are_safe(bad):
    assert _missing_clauses(bad, EV_15) == []
    assert _missing_clauses("제15조", bad) == []
    assert _append_clause_note(bad, EV_15) == bad
