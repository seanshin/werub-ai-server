"""충실도(groundedness) 채점 회귀 테스트 — LLM judge는 mock (순수 로직 검증)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import faithfulness as F


def fake_ask(responses):
    """호출 순서대로 응답을 돌려주는 mock ask_json."""
    it = iter(responses)

    def ask(system, prompt, schema, model=None, timeout=None):
        return next(it)
    return ask


def test_aggregate_all_supported():
    judged = [{"claim": "a", "verdict": "supported"}] * 4
    r = F.aggregate(judged)
    assert r["faithfulness"] == 1.0
    assert r["hallucination"] is False
    assert r["n_supported"] == 4 and r["n_contradicted"] == 0


def test_aggregate_contradicted_flags_hallucination():
    judged = [{"claim": "a", "verdict": "supported"},
              {"claim": "b", "verdict": "contradicted"}]
    r = F.aggregate(judged)
    assert r["hallucination"] is True
    assert r["n_contradicted"] == 1


def test_aggregate_low_support_flags_hallucination():
    judged = [{"claim": "a", "verdict": "supported"},
              {"claim": "b", "verdict": "unsupported"},
              {"claim": "c", "verdict": "unsupported"}]
    r = F.aggregate(judged)  # 1/3 < 0.5
    assert r["faithfulness"] == 0.333
    assert r["hallucination"] is True


def test_aggregate_empty_is_not_hallucination():
    r = F.aggregate([])
    assert r["n_claims"] == 0
    assert r["faithfulness"] is None
    assert r["hallucination"] is False


def test_aggregate_threshold_configurable():
    judged = [{"claim": "a", "verdict": "supported"},
              {"claim": "b", "verdict": "unsupported"}]  # 0.5
    assert F.aggregate(judged, threshold=0.5)["hallucination"] is False   # 0.5 >= 0.5
    assert F.aggregate(judged, threshold=0.6)["hallucination"] is True


def test_judge_claims_missing_verdict_defaults_unsupported():
    ask = fake_ask([{"verdicts": [{"i": 1, "verdict": "supported"}]}])  # 2번 누락
    judged = F.judge_claims(["c1", "c2"], "ev", ask_json=ask)
    assert judged[0]["verdict"] == "supported"
    assert judged[1]["verdict"] == "unsupported"


def test_judge_claims_invalid_index_and_verdict_ignored():
    ask = fake_ask([{"verdicts": [
        {"i": 99, "verdict": "supported"},        # 범위 밖
        {"i": "x", "verdict": "supported"},        # 비정수
        {"i": 1, "verdict": "maybe"},              # 이상 verdict
        {"i": 2, "verdict": "contradicted"},
    ]}])
    judged = F.judge_claims(["c1", "c2"], "ev", ask_json=ask)
    assert judged[0]["verdict"] == "unsupported"
    assert judged[1]["verdict"] == "contradicted"


def test_extract_claims_filters_blank():
    ask = fake_ask([{"claims": ["주장1", "  ", "", "주장2", 42]}])
    claims = F.extract_claims("답변", ask_json=ask)
    assert claims == ["주장1", "주장2"]


def test_score_end_to_end_mocked():
    ask = fake_ask([
        {"claims": ["진료기록부 보존기간은 10년이다.", "보존 근거는 의료법 시행규칙이다."]},
        {"verdicts": [{"i": 1, "verdict": "supported"}, {"i": 2, "verdict": "supported"}]},
    ])
    r = F.score("진료기록부 보존기간은 10년이다. 근거는 의료법 시행규칙이다.", "근거 텍스트", ask_json=ask)
    assert r["faithfulness"] == 1.0
    assert r["hallucination"] is False
    assert len(r["claims"]) == 2


def test_score_no_claims():
    ask = fake_ask([{"claims": []}])
    r = F.score("전문의와 상담하세요.", "근거", ask_json=ask)
    assert r["n_claims"] == 0 and r["hallucination"] is False


# --- MiniCheck 교차검증 (v2.120.0) ------------------------------------------

def test_minicheck_verify_failure_yields_none():
    from core.faithfulness import minicheck_verify

    def _checker(ev, claim, model=None):
        if "실패" in claim:
            raise RuntimeError("model missing")
        return "지지" in claim
    out = minicheck_verify(["지지되는 주장", "실패하는 주장", "다른 주장"], "근거", checker=_checker)
    assert out == [True, None, False]


def test_merge_cross_check_agreement_and_strict():
    from core.faithfulness import merge_cross_check
    judged = [{"claim": "a", "verdict": "supported"},
              {"claim": "b", "verdict": "supported"},
              {"claim": "c", "verdict": "unsupported"},
              {"claim": "d", "verdict": "contradicted"}]
    # a=양측 supported, b=judge만 supported(불일치), c=양측 not(일치), d=검증불가
    agg = merge_cross_check(judged, [True, False, False, None])
    assert agg["mc_checked"] == 3
    assert agg["mc_agree"] == round(2 / 3, 3)
    assert agg["mc_conflicts"] == 1
    assert agg["strict_faithfulness"] == 0.25          # 4주장 중 양측 supported=1
    assert judged[0]["minicheck"] == "supported"
    assert judged[1]["minicheck"] == "not_supported"   # 리뷰 트리거
    assert judged[3]["minicheck"] is None


def test_merge_cross_check_all_unchecked():
    from core.faithfulness import merge_cross_check
    judged = [{"claim": "a", "verdict": "supported"}]
    agg = merge_cross_check(judged, [None])
    assert agg["mc_agree"] is None and agg["strict_faithfulness"] is None


def test_cross_check_updates_score_result():
    from core.faithfulness import cross_check
    result = {"claims": [{"claim": "a", "verdict": "supported"}],
              "faithfulness": 1.0}
    out = cross_check(result, "근거", checker=lambda ev, c, model=None: True)
    assert out["mc_agree"] == 1.0 and out["mc_conflicts"] == 0
    assert out["faithfulness"] == 1.0                  # 주 KPI 불변


def test_split_evidence_paragraph_boundaries():
    from core.faithfulness import _split_evidence
    short = "짧은 근거"
    assert _split_evidence(short) == [short]
    paras = "\n".join("문단%d " % i + "가" * 500 for i in range(6))   # 문단당 ~505자
    chunks = _split_evidence(paras, max_chars=1500)
    assert all(len(c) <= 1500 for c in chunks) and len(chunks) >= 2
    assert "".join(chunks).replace("\n", "") == paras.replace("\n", "")  # 내용 무손실
    hard = "가" * 4000                                                   # 문단 경계 없음 → 하드 컷
    assert all(len(c) <= 1500 for c in _split_evidence(hard, max_chars=1500))


def test_minicheck_verify_max_over_chunks():
    from core.faithfulness import minicheck_verify
    evidence = ("A" * 1400) + "\n" + "진료기록부는 10년간 보존한다." + ("B" * 1400)

    def _checker(chunk, claim, model=None):
        return "10년" in chunk                     # 두 번째 청크만 지지
    out = minicheck_verify(["진료기록부 10년 보존"], evidence, checker=_checker)
    assert out == [True]                           # max-over-chunks

    def _all_no(chunk, claim, model=None):
        return False
    assert minicheck_verify(["x"], evidence, checker=_all_no) == [False]

    def _boom(chunk, claim, model=None):
        raise RuntimeError("bad")
    assert minicheck_verify(["x"], evidence, checker=_boom) == [None]   # 전 청크 불능


# ── 교차검증 백엔드 교체 (v2.124.13 — bespoke-minicheck NC → qwen-entail Apache-2.0) ──

def test_default_checker_backend_selection(monkeypatch):
    import core.faithfulness as F
    monkeypatch.setattr(F, "VERIFIER_BACKEND", "qwen-entail")
    assert F._default_checker() is F._qwen_entail_check
    monkeypatch.setattr(F, "VERIFIER_BACKEND", "minicheck")   # 레거시 명시 opt-in
    assert F._default_checker() is F._default_minicheck


# ⚠️ v2.124.96 이관 — 전송이 `requests` 직접 호출에서 `core/model_router`로 옮겨졌다.
# 세션을 갈아끼워 **라우터가 실제로 그 전송로를 쓰는지**까지 확인한다(페이로드만 보면
# 통로 변경을 놓친다 — 2026-08-10 Fair Queue 우회 실측).
def _entail_session(fn):
    import json as _json
    from unittest.mock import MagicMock

    class _S:
        def post(self, url, json=None, timeout=None):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"response": _json.dumps(fn(json))}
            return r
    return _S()


def test_qwen_entail_check_parsing(monkeypatch):
    import core.faithfulness as F
    from core import model_router as mr
    sess = _entail_session(lambda p: {"supported": p["prompt"].count("지지주장") > 0})
    monkeypatch.setattr(mr, "get_adapter",
                        lambda *a, **k: mr.OllamaAdapter(base="http://t", session=sess))
    assert F._qwen_entail_check("근거", "지지주장") is True
    assert F._qwen_entail_check("근거", "다른 주장") is False


def test_qwen_entail_check_malformed_raises(monkeypatch):
    import pytest
    import core.faithfulness as F
    from core import model_router as mr
    sess = _entail_session(lambda p: {})
    monkeypatch.setattr(mr, "get_adapter",
                        lambda *a, **k: mr.OllamaAdapter(base="http://t", session=sess))
    with pytest.raises(ValueError):
        F._qwen_entail_check("근거", "주장")


# ── 메타 진술 제외 + 언어 드리프트 재추출 (v2.124.16, 폐루프 12) ──────────────

def test_meta_claim_excluded_but_real_negation_kept():
    """근거에 대한 메타 진술만 제외 — 실세계 부정 주장은 판정 대상으로 남는다."""
    assert F._is_meta_claim("벌칙에 대한 정보는 확인할 수 없습니다.")
    assert F._is_meta_claim("해당 내용은 근거 문서에 명시되어 있지 않습니다.")
    assert not F._is_meta_claim("간호기록부는 보존 의무가 없습니다.")   # 실세계 주장
    assert not F._is_meta_claim("진료기록부는 10년 보존해야 한다.")


def test_extract_claims_drops_meta_claim():
    answer = "간호기록부는 5년 보존해야 합니다. 벌칙은 근거에서 확인되지 않습니다."

    def _ask(system, prompt, schema, model=None):
        return {"claims": ["간호기록부는 5년 보존해야 합니다.",
                           "벌칙은 근거에서 확인되지 않습니다."]}
    claims = F.extract_claims(answer, ask_json=_ask)
    assert claims == ["간호기록부는 5년 보존해야 합니다."]


def test_extract_claims_retries_on_language_drift():
    """주장이 중국어로 이탈하면 1회 재추출해 한국어 결과를 채택(실측 클래스)."""
    calls = []

    def _ask(system, prompt, schema, model=None):
        calls.append(1)
        if len(calls) == 1:
            return {"claims": ["放射照片的保存期限为5年。"]}
        return {"claims": ["방사선 사진의 보존기간은 5년이다."]}
    claims = F.extract_claims("방사선 사진은 5년 보존한다.", ask_json=_ask)
    assert claims == ["방사선 사진의 보존기간은 5년이다."] and len(calls) == 2


def test_extract_claims_no_retry_when_korean():
    calls = []

    def _ask(system, prompt, schema, model=None):
        calls.append(1)
        return {"claims": ["진료기록부는 10년 보존한다."]}
    F.extract_claims("진료기록부는 10년 보존한다.", ask_json=_ask)
    assert len(calls) == 1                    # 정상 경로는 추가 호출 없음


# ── _is_korean 비율 판정 + 잔존 드리프트 제외 (07-22 id275 실측) ─────────────

def test_is_korean_ratio_catches_mixed_drift():
    """한글 몇 자로 시작하는 혼합 드리프트는 존재-검사를 통과하던 구멍 — 비율로 차단."""
    # 07-22 id275 실제 유출 주장(한글 3자 + 중국어/일본어 우세)
    drift = "대리수蛉は回答中的的法律引用和解释，提取了关于处方药代领的相关规定"
    assert not F._is_korean(drift)
    assert not F._is_korean("放射照片的保存期限为5年。")          # 순수 중국어(기존 검출 유지)
    assert F._is_korean("의료법(醫療法) 제27조에 따라 금지된다.")  # 한자 병기=한글 우세 통과
    assert F._is_korean("진료기록부는 10년 보존한다.")
    assert F._is_korean("supported")                              # 한자·가나 없음=통과


def test_extract_claims_drops_residual_drift():
    """재추출로도 개선 안 되면 드리프트 주장은 최종 제외(검증 불능 노이즈)."""
    def _ask(system, prompt, schema, model=None):
        return {"claims": ["진료기록부는 10년 보존한다.",
                           "대리수蛉は回答中的的法律引用和解释，提取了相关规定"]}
    claims = F.extract_claims("진료기록부는 10년 보존한다.", ask_json=_ask)
    assert claims == ["진료기록부는 10년 보존한다."]


# ── 수치 충실성 가드 (07-30 id399 실측: 답변 "3천만원"→주장 "7천만원" 변조) ────

def test_num_values_normalizes_korean_scales():
    assert F._num_values("3천만원 이하의 벌금") == {3e7}
    assert F._num_values("3,000만원") == {3e7}                    # 표기 차이=같은 값
    assert F._num_values("10년 보존, 3천만원") == {10.0, 3e7}
    assert F._num_values("3년 이하·주장1·제5조") == set()          # 맨숫자 1자리=노이즈 제외
    assert F._num_values("벌금에 처한다") == set()

def test_extract_claims_drops_mutated_number():
    """분해가 답변에 없는 수치를 만들면 그 주장만 제외(정상 주장은 유지)."""
    answer = "제23조의5를 위반한 자는 3년 이하의 징역이나 3천만원 이하의 벌금에 처한다."

    def _ask(system, prompt, schema, model=None):
        return {"claims": ["제23조의5를 위반한 자는 3년 이하의 징역이나 7천만원 이하의 벌금에 처한다.",
                           "취득한 경제적 이익은 몰수한다."]}
    claims = F.extract_claims(answer, ask_json=_ask)
    assert claims == ["취득한 경제적 이익은 몰수한다."]

def test_extract_claims_keeps_answer_error_number():
    """답변 자체의 오수치는 주장에 그대로 실려 통과 — 실제 환각 검출은 불변."""
    answer = "벌칙은 7천만원 이하의 벌금이다."                     # 답변이 틀린 경우

    def _ask(system, prompt, schema, model=None):
        return {"claims": ["벌칙은 7천만원 이하의 벌금이다."]}
    claims = F.extract_claims(answer, ask_json=_ask)
    assert claims == ["벌칙은 7천만원 이하의 벌금이다."]          # 유지→judge가 판정

def test_extract_claims_retries_on_mutated_number():
    """수치 변조도 드리프트와 같은 1회 재추출 경로를 탄다(개선 시 채택)."""
    answer = "제19조 위반 시 3천만원 이하의 벌금에 처한다."
    calls = []

    def _ask(system, prompt, schema, model=None):
        calls.append(1)
        if len(calls) == 1:
            return {"claims": ["제19조 위반 시 7천만원 이하의 벌금에 처한다."]}
        return {"claims": ["제19조 위반 시 3천만원 이하의 벌금에 처한다."]}
    claims = F.extract_claims(answer, ask_json=_ask)
    assert claims == ["제19조 위반 시 3천만원 이하의 벌금에 처한다."] and len(calls) == 2
