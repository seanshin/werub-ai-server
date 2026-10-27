"""core.medical_router 순수 단위테스트.

의료 정본 도구 결정적 우선 라우팅(LLM/워크플로우 우회)이 명확한 의도+엔티티만 라우팅하는지.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.medical_router import medical_direct_route as _route  # noqa: E402
from core.medical_router import summarize_medical_route  # noqa: E402


def test_substitution_route():
    r = _route("제품코드 651900330 더 저렴한 대체조제 약 알려줘")
    assert r and r[0] == "drug_substitution" and r[2]["code"] == "651900330"


def test_resolve_by_barcode():
    r = _route("표준코드 8806587001001 무슨 약이야")
    assert r and r[0] == "drug_resolve" and r[2]["code"] == "8806587001001"


def test_resolve_by_product_code_with_intent():
    r = _route("645302132 이거 무슨 약인지 성분 알려줘")
    assert r and r[0] == "drug_resolve" and r[2]["code"] == "645302132"


def test_medical_law_route():
    assert _route("진료기록부 보존기간 알려줘")[0] == "medical_law_ask"
    assert _route("의료법상 신고의무 뭐야?")[0] == "medical_law_ask"


def test_claims_route():
    r = _route("초음파 동일일 중복 산정 삭감 위험 있어?")
    assert r and r[0] == "notice_search"


def test_no_route_for_generic():
    assert _route("오늘 날씨 어때?") is None
    assert _route("환자 접수해줘") is None


def test_substitution_priority_over_resolve():
    assert _route("651900330 대체약 있어?")[0] == "drug_substitution"


def test_summarize_law():
    assert summarize_medical_route("medical_law_ask", {"answer": "10년 보존"}) == "10년 보존"


def test_summarize_substitution():
    s = summarize_medical_route("drug_substitution", {
        "found": True, "product": "루나팜정", "code": "651900330", "eligible": True,
        "alternatives_count": 1, "cheaper_alternatives_count": 0,
        "alternatives": [{"product": "라제팜정", "price": 60}]})
    assert "루나팜정" in s and "장려금 대상: 예" in s


def test_summarize_error():
    assert "문제" in summarize_medical_route("drug_resolve", {"error": "boom"})


# ── DUR / e약은요 결정적 라우팅 (v2.102.0) ──
def test_dur_route_two_drugs():
    r = _route("와파린이랑 아스피린 같이 처방해도 되는지 병용금기 확인")
    assert r and r[0] == "dur_check"
    assert set(r[2]["drugs"]) == {"와파린", "아스피린"}


def test_dur_route_pregnant_single_drug():
    r = _route("임신부에게 프로게스테론 DUR 금기 확인해줘")
    assert r and r[0] == "dur_check"
    assert "프로게스테론" in r[2]["drugs"] and r[2]["pregnant"] is True


def test_dur_route_elderly_infers_age():
    r = _route("고령 환자에게 졸피뎀 처방 DUR 확인")
    assert r and r[0] == "dur_check" and r[2].get("age") == 75


def test_dur_single_drug_no_context_falls_through():
    # 단일 약물 + 환자컨텍스트 없음 → None(tool-calling 폴백)
    assert _route("아스피린 병용금기 알려줘") is None


def test_drug_info_route_product_form():
    r = _route("타이레놀정 부작용이랑 복용법 알려줘")
    assert r and r[0] == "drug_info" and "타이레놀정" in r[2]["query"]


def test_drug_info_route_brand():
    r = _route("활명수 효능 알려줘")
    assert r and r[0] == "drug_info" and r[2]["query"] == "활명수"


def test_dur_priority_over_drug_info():
    # 병용금기 의도가 부작용보다 우선
    r = _route("와파린이랑 아스피린 부작용이랑 병용금기 알려줘")
    assert r[0] == "dur_check"


def test_no_false_elderly_in_drug_name():
    # '이소트레티노인'의 '노인'을 고령으로 오인하지 않음 (단어경계)
    r = _route("임신 20주인데 이소트레티노인 먹어도 되나요")
    assert r and r[0] == "dur_check"
    assert r[2]["drugs"] == ["이소트레티노인"] and r[2].get("age") is None
    assert r[2]["pregnant"] is True
    # 부작용 질의(노인 substring)는 drug_info, age 추론 안 함
    r2 = _route("이소트레티노인 부작용 알려줘")
    assert r2[0] == "drug_info" and r2[2]["query"] == "이소트레티노인"


def test_no_digit_token_as_drug():
    # 75세/20주 등 숫자 토큰은 약물로 추출 안 함
    r = _route("75세 환자에게 졸피뎀 처방 DUR 확인")
    assert r[2]["drugs"] == ["졸피뎀"] and r[2]["age"] == 75


def test_summarize_dur_and_info():
    s = summarize_medical_route("dur_check", {"contraindications": [{"drug_a": "A", "drug_b": "B"}],
                                              "pregnancy_warnings": [{}], "elderly_warnings": [],
                                              "age_warnings": [], "duplicate_therapy": [], "split_cautions": []})
    assert "병용금기" in s and "임부금기 1건" in s
    s2 = summarize_medical_route("drug_info", {"matches": [{"name": "타이레놀", "side_effect": "간손상"}]})
    assert "타이레놀" in s2 and "부작용" in s2
