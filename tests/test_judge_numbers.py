"""`numbers_match` 결정적 판정 회귀.

2026-08-05 전수 측정(40문항)이 근거다:
  · judge `numbers_match` 실패 **62%**(이전 38%에서 24%p 악화)
  · 결정적 판정과 어긋난 25건이 **전부 judge 쪽 실패** · 결정적만 실패 **0건**
  · 그중 **13건(52%)은 답변에 수치가 아예 없었고**, judge 사유가 스스로
    `"답변에서 제시한 수치가 없어 이를 확인할 수 없어 감점"`이라 적혀 있었다

v2.124.73이 기준 문구로 그 행동을 금지했는데도 악화됐다 →
**"무엇이 실제로 거기 있나"는 문구로 교정되지 않는다**(v2.124.74와 같은 결론).
"""
import pytest

from core.llm_judge import _apply_deterministic_checks, _figures, numbers_grounded

EV = ("제15조(진료기록부 등의 보존) ① … 2. 진료기록부 : 10년 … 7. 간호기록부 : 5년 "
      "다만, 계속적인 진료를 위하여 필요한 경우에는 1회에 한정하여 연장할 수 있다. "
      "제88조(벌칙) 3년 이하의 징역이나 3천만원 이하의 벌금에 처한다.")


def test_no_figures_is_true():
    """대조할 수치가 없으면 실패가 아니다 — 실측 위양성 13건의 정체."""
    assert numbers_grounded("의료인은 진료 요청을 거부하지 못합니다.", EV)
    assert numbers_grounded("", EV)


def test_grounded_figures_pass():
    assert numbers_grounded("진료기록부는 10년, 간호기록부는 5년 보존합니다.", EV)
    assert numbers_grounded("3년 이하의 징역이나 3천만원 이하의 벌금입니다.", EV)


def test_ghost_figure_fails():
    assert not numbers_grounded("진료기록부는 7년 보존합니다.", EV)
    assert not numbers_grounded("500만원 이하의 벌금입니다.", EV)


def test_article_numbers_are_not_figures():
    """제15조·제88조·개정일자는 '수치 일치' 대상이 아니다."""
    assert _figures("제15조와 제88조의2를 참고하세요") == set()
    assert _figures("개정 2021. 6. 30.") == set()
    # 조문만 인용한 답변은 근거와 무관하게 통과해야 한다
    assert numbers_grounded("제37조에 따라 신고합니다.", EV)


def test_unit_normalization():
    """3년간 ↔ 3년, 6달 ↔ 6개월."""
    assert numbers_grounded("10년간 보존합니다.", EV)
    assert _figures("6달") == _figures("6개월")


def test_comma_variants():
    assert numbers_grounded("3,000만원 이하의 벌금", "3000만원 이하의 벌금에 처한다")


def test_overwrites_judge_value():
    scores = {"accuracy": {"score": 3, "checks": {"numbers_match": False,
                                                  "cites_evidence": True,
                                                  "conditions_kept": True}}}
    _apply_deterministic_checks(scores, "진료기록부는 10년 보존합니다.", EV)
    assert scores["accuracy"]["checks"]["numbers_match"] is True
    # 다른 항목은 건드리지 않는다
    assert scores["accuracy"]["checks"]["cites_evidence"] is True


def test_no_evidence_keeps_judge_value():
    """근거가 없으면 대조가 불가능하므로 judge 값을 유지한다(무근거 덮어쓰기 금지)."""
    scores = {"accuracy": {"score": 3, "checks": {"numbers_match": False}}}
    _apply_deterministic_checks(scores, "10년입니다.", "")
    assert scores["accuracy"]["checks"]["numbers_match"] is False


def test_missing_check_key_is_safe():
    scores = {"accuracy": {"score": 4, "checks": {"cites_evidence": True}}}
    _apply_deterministic_checks(scores, "10년", EV)          # 예외 없이 통과
    assert "numbers_match" not in scores["accuracy"]["checks"]


@pytest.mark.parametrize("bad", [None, {}, {"accuracy": None}, {"accuracy": {"checks": None}}])
def test_malformed_scores_no_crash(bad):
    _apply_deterministic_checks(bad if isinstance(bad, dict) else {}, "10년", EV)
