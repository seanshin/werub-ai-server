"""fluency는 근거 없이 채점한다 — 프롬프트 **범위** 교정 회귀.

🔴 2026-08-05 실측(같은 답변 40건을 근거 있이/없이 채점):

    no_broken_text  25% → 0%
    natural_korean  20% → 2%
    conditions_kept 30% → 72%   ← 근거 의존이 설계인 항목은 정반대

judge가 프롬프트에 실린 **법령 원문의 깨진 한국어**(띄어쓰기 결손·OCR 잔재)를 답변의
한국어로 오판하고 있었다. 같은 답변을 근거 개수만 1/3/5로 바꿔도 `natural_korean`이
2/6 뒤집혔다(결정적으로 이관한 3항목은 0/6).

문구가 아니라 **범위**로 가른다 — 문구 교정은 v2.124.73·74에서 두 번 실패했다.
"""
import core.llm_judge as J


def _fake_ask(seen):
    """근거 포함 여부에 따라 다른 fluency를 돌려주는 가짜 judge."""
    def ask(sys_msg, prompt, schema, model=None):
        has_ev = "[근거]" in prompt
        seen.append(has_ev)
        return {
            "accuracy": {"score": 4, "reason": "", "checks": {
                "cites_evidence": True, "numbers_match": True, "conditions_kept": True}},
            "helpfulness": {"score": 4, "reason": "", "checks": {
                "direct_answer": True, "actionable": True, "complete": True}},
            "safety": {"score": 5, "reason": "", "checks": {
                "advisory_disclaimer": True, "no_prescriptive": True, "expert_referral": True}},
            # 근거가 있으면 한국어를 나쁘게 본다 = 실측된 오염을 흉내
            "fluency": {"score": 2 if has_ev else 5, "reason": "", "checks": {
                "natural_korean": not has_ev, "no_broken_text": not has_ev,
                "consistent_register": True}},
        }
    return ask


def test_fluency_taken_from_evidence_free_pass():
    seen = []
    out = J.judge("질문", "답변입니다. 전문가와 상담하세요.", "제15조 근거 원문",
                  ask_json=_fake_ask(seen), checklist=True)
    assert seen == [True, False]                     # 두 번 호출: 근거 있이 → 없이
    flu = out["scores"]["fluency"]
    assert flu["score"] == 5                         # 오염된 2점이 아니라 5점
    assert flu["checks"]["natural_korean"] is True
    assert flu["checks"]["no_broken_text"] is True


def test_other_dimensions_keep_evidence_judgment():
    """근거 의존이 설계인 항목은 첫 판정(근거 있이)을 유지한다."""
    seen = []
    out = J.judge("질문", "답변", "근거", ask_json=_fake_ask(seen), checklist=True)
    assert out["scores"]["accuracy"]["checks"]["conditions_kept"] is True
    assert out["scores"]["accuracy"]["score"] == 4


def test_no_extra_call_without_evidence():
    """근거가 없으면 추가 호출을 하지 않는다(비용)."""
    seen = []
    J.judge("질문", "답변", "", ask_json=_fake_ask(seen), checklist=True)
    assert seen == [False]


def test_second_call_failure_is_non_blocking():
    """보조 판정이 실패해도 평가는 살아 있어야 한다."""
    calls = []

    def flaky(sys_msg, prompt, schema, model=None):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("judge down")
        return _fake_ask([])(sys_msg, prompt, schema, model)

    out = J.judge("질문", "답변", "근거", ask_json=flaky, checklist=True)
    assert out["scores"]["fluency"]["score"] == 2     # 첫 판정 유지
    assert out["overall"] is not None


def test_toggle_off_restores_single_call(monkeypatch):
    monkeypatch.setattr(J, "FLUENCY_WITHOUT_EVIDENCE", False)
    seen = []
    J.judge("질문", "답변", "근거", ask_json=_fake_ask(seen), checklist=True)
    assert seen == [True]
