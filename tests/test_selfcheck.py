"""SelfCheck 무참조 환각 검출 회귀 테스트 — 샘플링/judge mock (순수 집계 검증)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import selfcheck as S


def V(*verdicts):
    return [{"claim": "c%d" % i, "verdict": v} for i, v in enumerate(verdicts)]


def test_aggregate_full_consistency():
    per_sample = [V("supported", "supported")] * 3
    r = S.aggregate(per_sample, 2)
    assert r["consistency"] == 1.0
    assert r["suspect_claims"] == []
    assert r["hallucination_risk"] is False


def test_aggregate_divergent_claim_flagged():
    # 주장0=3/3 지지, 주장1=1/3 지지(발산=환각 의심)
    per_sample = [V("supported", "supported"),
                  V("supported", "unsupported"),
                  V("supported", "contradicted")]
    r = S.aggregate(per_sample, 2)
    assert r["claim_consistency"] == [1.0, 0.333]
    assert r["suspect_claims"] == [1]
    assert r["hallucination_risk"] is True


def test_score_batch_false_positive_reverted():
    """일괄 판정의 위양성은 개별 재판정으로 되돌아온다 — 환각 과다 경보 방지."""
    calls = {"n": 0}

    def ask(system, prompt, schema, model=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"claims": ["서울은 한국의 수도다.", "부산은 항구도시다."]}
        if "\n2. " not in prompt:            # 개별로 물으면 지지
            return {"verdicts": [{"i": 1, "verdict": "supported"}]}
        return {"verdicts": [{"i": 1, "verdict": "supported"},
                             {"i": 2, "verdict": "contradicted"}]}
    r = S.score("서울은 한국의 수도다. 부산은 항구도시다.", ["s1", "s2"], ask_json=ask)
    assert r["consistency"] == 1.0
    assert r["hallucination_risk"] is False


def test_aggregate_empty():
    assert S.aggregate([], 0)["consistency"] is None
    assert S.aggregate([], 3)["consistency"] is None
    r = S.aggregate([V("supported")], 0)
    assert r["hallucination_risk"] is False


def test_aggregate_threshold():
    per_sample = [V("supported"), V("unsupported")]  # 일관성 0.5
    assert S.aggregate(per_sample, 1, threshold=0.5)["suspect_claims"] == []   # 0.5 >= 0.5
    assert S.aggregate(per_sample, 1, threshold=0.6)["suspect_claims"] == [0]


def test_score_end_to_end_mocked():
    """2026-08-04: 비지지 판정은 **개별 재판정**을 거친다(일괄 판정 오염 봉합).
    mock도 단독 호출을 구분해야 한다 — 일괄 프롬프트에만 '2.' 항목이 있다."""
    calls = {"n": 0, "solo": 0}

    def ask(system, prompt, schema, model=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:   # extract_claims
            return {"claims": ["서울은 한국의 수도다.", "인구는 3억명이다."]}
        if "\n2. " not in prompt:            # 개별 재판정 — 일괄 판정을 확인해준다
            calls["solo"] += 1
            return {"verdicts": [{"i": 1, "verdict": "unsupported"}]}
        # judge_claims × 샘플 2회 — 주장1은 전 샘플 지지, 주장2는 전 샘플 미지지
        return {"verdicts": [{"i": 1, "verdict": "supported"},
                             {"i": 2, "verdict": "unsupported"}]}
    # 답변 원문에 주장의 수치가 실려 있어야 한다 — 분해가 답변에 없는 수치를 만들면
    # 그 주장은 답변을 대변하지 못해 제외된다(v2.124.58 수치 충실성 가드).
    r = S.score("서울은 한국의 수도다. 인구는 3억명이다.", ["샘플1", "샘플2"], ask_json=ask)
    assert r["consistency"] == 0.5
    assert r["suspect_claims"] == [1]
    assert r["claims"][1]["consistency"] == 0.0
    assert r["hallucination_risk"] is True
    assert calls["solo"] == 2          # 샘플 2개 × 플래그 주장 1개


def test_score_no_claims():
    def ask(system, prompt, schema, model=None, timeout=None):
        return {"claims": []}
    r = S.score("면책 문구뿐.", ["s1"], ask_json=ask)
    assert r["n_claims"] == 0 and r["hallucination_risk"] is False
