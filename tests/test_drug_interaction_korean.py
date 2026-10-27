"""약물 상호작용 검출 한글명 누락 봉합 회귀 (2026-08-04).

`check_drug_interaction_warnings`가 영문 토큰만 추출해(`[A-Z][a-z]+`) 한국어 약물명이
통째로 빠졌다. 한국 의료 환경 API인데 `와파린`·`파록세틴`을 못 읽어 DUR 조회가 빈 목록으로
돌았다 — `/api/medical/dur/check`는 같은 입력을 정확히 잡는데 이 경로만 0건이었다.
"""
from unittest.mock import patch

import core.medical_validator as MV


def _run(payload, dur_result=None):
    """clinic DB와 DUR을 모킹해 **약물명 추출**만 검사한다.

    ⚠️ `sys.modules` 패치는 쓰지 않는다 — `core.dur`가 이미 임포트돼 있으면
    `from core import dur`가 패키지 속성으로 해석돼 모킹을 우회한다(전체 스위트에서만
    실패하는 오염). 실제 함수를 직접 패치해야 단독·전체 실행 결과가 같다.
    """
    import core.dur as _real_dur
    seen = {}

    def _fake_check(drug_set, **kw):
        seen["drugs"] = list(drug_set)
        return dur_result or {"contraindications": []}

    with patch.object(MV, "_get_clinic_db", return_value=None), \
         patch.object(_real_dur, "check", _fake_check):
        result = MV.check_drug_interaction_warnings(payload)
    return seen.get("drugs", []), result


class TestKoreanDrugNames:
    def test_korean_names_reach_dur(self):
        """한글 약물명이 DUR 조회 목록에 들어가야 한다 — 이게 빠져서 0건이었다."""
        drugs, _ = _run({"medications": ["파록세틴", "셀레길린"]})
        assert "파록세틴" in drugs
        assert "셀레길린" in drugs

    def test_dosage_suffix_preserved(self):
        """용량이 붙어 있어도 그대로 넘긴다 — 해석은 DUR의 _resolve_one 책임이다."""
        drugs, _ = _run({"medications": ["와파린 5mg", "아미오다론 200mg"]})
        assert "와파린 5mg" in drugs

    def test_english_names_still_work(self):
        """기존 영문 추출 경로는 유지 — 회귀 방지."""
        drugs, _ = _run({"medications": [], "note": "Warfarin and Amiodarone"})
        assert any("Warfarin" in d for d in drugs)

    def test_string_medications_accepted(self):
        drugs, _ = _run({"medications": "파록세틴"})
        assert "파록세틴" in drugs

    def test_no_duplicates(self):
        drugs, _ = _run({"medications": ["파록세틴", "파록세틴"]})
        assert drugs.count("파록세틴") == 1

    def test_empty_and_missing(self):
        assert _run({"medications": []})[0] == []
        assert _run({})[0] == []

    def test_non_string_entries_skipped(self):
        drugs, _ = _run({"medications": ["파록세틴", None, 123, ""]})
        assert drugs == ["파록세틴"]


class TestDurMerge:
    def test_contraindication_surfaces(self):
        dur = {"contraindications": [{
            "drug_a": "파록세틴", "drug_b": "셀레길린염산염",
            "reason": "세로토닌성 증후군"}]}
        _, result = _run({"medications": ["파록세틴", "셀레길린"]}, dur_result=dur)
        ints = (result[2] or {}).get("interactions") or []
        assert len(ints) == 1
        assert ints[0]["source"] == "식약처DUR"
        assert ints[0]["severity"] == "contraindicated"
