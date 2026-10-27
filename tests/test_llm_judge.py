"""LLM-judge 가치/품질 평점 회귀 테스트 — judge mock (순수 로직 검증)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import llm_judge as J


def test_normalize_valid():
    raw = {k: {"score": 4, "reason": "ok"} for k in J.RUBRIC}
    s = J.normalize(raw)
    assert set(s) == set(J.RUBRIC)
    assert all(v["score"] == 4 for v in s.values())


def test_normalize_drops_invalid():
    raw = {"accuracy": {"score": 9},          # 범위 밖
           "helpfulness": {"score": "x"},      # 비정수
           "safety": "bad",                    # dict 아님
           "fluency": {"score": 3, "reason": "r" * 500}}
    s = J.normalize(raw)
    assert set(s) == {"fluency"}
    assert len(s["fluency"]["reason"]) == 200  # 사유 길이 캡


def test_overall_mean_and_empty():
    assert J.overall({"a": {"score": 5}, "b": {"score": 3}}) == 4.0
    assert J.overall({}) is None


def test_judge_end_to_end_mocked():
    def ask(system, prompt, schema, model=None, timeout=None):
        assert "[질문]" in prompt and "[답변]" in prompt
        return {k: {"score": 5, "reason": "good"} for k in J.RUBRIC}
    r = J.judge("질문", "답변", ask_json=ask)
    assert r["overall"] == 5.0
    assert set(r["scores"]) == set(J.RUBRIC)


def test_judge_includes_evidence_when_given():
    """근거는 **본 판정**에 실린다.

    fluency는 근거 없이 한 번 더 채점하므로(judge가 법령 원문의 깨진 한국어를 답변의
    것으로 오판하던 실측 — `test_judge_fluency_scope.py`) 마지막 프롬프트만 보면
    근거가 없다. 그래서 **호출 전체**를 모아서 확인한다.
    """
    prompts = []

    def ask(system, prompt, schema, model=None, timeout=None):
        prompts.append(prompt)
        return {k: {"score": 3} for k in J.RUBRIC}
    J.judge("q", "a", evidence="근거텍스트", ask_json=ask)
    assert any("[근거]" in p and "근거텍스트" in p for p in prompts)
    prompts.clear()
    J.judge("q", "a", ask_json=ask)
    assert len(prompts) == 1 and "[근거]" not in prompts[0]   # 근거 없으면 1회만


def test_judge_partial_scores():
    def ask(system, prompt, schema, model=None, timeout=None):
        return {"accuracy": {"score": 4}, "safety": {"score": 2}}
    r = J.judge("q", "a", ask_json=ask)
    assert r["overall"] == 3.0
    assert set(r["scores"]) == {"accuracy", "safety"}


# --- 체크리스트 분해 (v2.120.0, CheckEval-style) ------------------------------

def test_schema_checklist_adds_boolean_checks():
    from core.llm_judge import _schema, CHECKLIST
    s = _schema(checklist=True)
    acc = s["properties"]["accuracy"]
    assert "checks" in acc["properties"] and "checks" in acc["required"]
    assert set(acc["properties"]["checks"]["required"]) == {c for c, _ in CHECKLIST["accuracy"]}
    assert "checks" not in _schema()["properties"]["accuracy"]["properties"]  # 기본 불변


def test_normalize_keeps_valid_checks_only():
    from core.llm_judge import normalize
    raw = {"accuracy": {"score": 4, "reason": "r",
                        "checks": {"numbers_match": True, "없는항목": True}},
           "helpfulness": {"score": 5}, "safety": {"score": 5}, "fluency": {"score": 5}}
    out = normalize(raw)
    assert out["accuracy"]["checks"] == {"numbers_match": True}   # 미정의 항목 제외
    assert "checks" not in out["helpfulness"]


def test_check_summary_failed_items():
    from core.llm_judge import check_summary
    scores = {"accuracy": {"score": 4, "checks": {"numbers_match": True, "conditions_kept": False}},
              "safety": {"score": 5, "checks": {"no_prescriptive": True}}}
    s = check_summary(scores)
    assert s["checks_total"] == 3 and s["checks_passed"] == 2
    assert s["checks_failed"] == ["accuracy.conditions_kept"]     # 실패=개선 지점
    assert s["checks_rate"] == round(2 / 3, 3)


def test_judge_checklist_end_to_end_mocked():
    from core.llm_judge import judge

    def _ask(system, prompt, schema, model=None):
        assert "체크리스트" in prompt
        return {d: {"score": 5, "reason": "ok",
                    "checks": {c: True for c in schema["properties"][d]["properties"]["checks"]["required"]}}
                for d in schema["properties"]}
    # 형식 항목(consistent_register·expert_referral·advisory_disclaimer)은 judge 값이 아니라
    # **답변 텍스트**로 결정적 판정된다(2026-08-04·08-05) → mock이 전부 true를 내도
    # 답변이 요건을 갖춰야 통과한다. 면책 고지가 없으면 12/12가 나오지 않는다.
    good = ("보존기간은 5년입니다. 이 정보는 법률 정보 제공이며, 법적 조언이 아닙니다. "
            "구체적 사안은 법률 전문가와 상담하세요.")
    r = judge("질문", good, ask_json=_ask, checklist=True)
    assert r["overall"] == 5.0
    assert r["checks_total"] == 12 and r["checks_passed"] == 12 and r["checks_failed"] == []

    # 안내 문구·면책 고지가 없는 답변이면 judge가 true를 내도 결정적 판정이 이긴다
    r2 = judge("질문", "답변", ask_json=_ask, checklist=True)
    assert sorted(r2["checks_failed"]) == ["safety.advisory_disclaimer",
                                           "safety.expert_referral"]
