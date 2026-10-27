"""core.medical_validator 단위 테스트 (의료 환각 감지 — 안전 관련).

순수 검증 가능 범위:
- check_dosage_range: 완전 순수(정규식) — 비정상 고용량 감지
- check_icd_consistency: 비-dict/코드없음 순수 분기
- check_drug_names / check_drug_interaction_warnings: DB 미존재 시 graceful 경로
- validate_medical_response: dict 입력(force_json 미호출) 시 오케스트레이션 로직

DB(clinic_codes.db) 의존 본 로직은 제외.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import medical_validator as mv  # noqa: E402


class TestDosageRange:
    """check_dosage_range — 순수, 비정상 고용량 감지."""

    def test_normal_ok(self):
        passed, msg, _ = mv.check_dosage_range("타이레놀 500mg 복용")
        assert passed is True

    def test_high_mg_warns(self):
        passed, msg, details = mv.check_dosage_range("이상한 약 6000mg 처방")
        assert passed is False
        assert details["dosage_warnings"]

    def test_mg_boundary_5000_ok(self):
        # >5000만 경고, 5000은 정상
        passed, _, _ = mv.check_dosage_range("5000mg")
        assert passed is True

    def test_high_g_warns(self):
        assert mv.check_dosage_range("11g")[0] is False

    def test_g_boundary_10_ok(self):
        assert mv.check_dosage_range("10g")[0] is True

    def test_high_mcg_warns(self):
        # mcg > 1000 → mg 혼동 의심
        assert mv.check_dosage_range("1001mcg")[0] is False

    def test_iu_no_rule_ok(self):
        # IU/mL은 상한 규칙 없음
        assert mv.check_dosage_range("100 IU 투여")[0] is True

    def test_case_insensitive(self):
        assert mv.check_dosage_range("6000MG")[0] is False

    def test_float_dosage(self):
        assert mv.check_dosage_range("10.5g")[0] is False

    def test_dict_input(self):
        passed, _, _ = mv.check_dosage_range({"plan": "6000mg q.d."})
        assert passed is False

    def test_no_dosage_ok(self):
        assert mv.check_dosage_range("약물 복용 안내")[0] is True


class TestIcdConsistencyPure:
    """check_icd_consistency — DB 불필요 분기."""

    def test_non_dict(self):
        passed, msg, _ = mv.check_icd_consistency("그냥 문자열")
        assert passed is True
        assert "not a dict" in msg

    def test_no_codes(self):
        passed, msg, _ = mv.check_icd_consistency({"summary": {}})
        assert passed is True
        assert "no ICD" in msg


class TestDbUnavailableGraceful:
    """DB(clinic_codes.db) 없을 때 검증기가 graceful 통과하는지."""

    def setup_method(self, _):
        # clinic_codes.db 없는 임시 디렉터리로 init → _get_clinic_db()가 None
        import tempfile
        mv.init_medical_validator(tempfile.mkdtemp())

    def test_drug_names_db_unavailable(self):
        passed, msg, _ = mv.check_drug_names({"plan": "Aspirin 처방"})
        assert passed is True
        assert "DB unavailable" in msg

    def test_interaction_db_unavailable(self):
        # v2.96.x: clinic DB 미가용이어도 식약처 DUR 정본으로 graceful 동작.
        # Warfarin+Aspirin은 병용금기 목록 비해당 → 경고 없음(no major interactions).
        passed, msg, _ = mv.check_drug_interaction_warnings({"plan": "Warfarin Aspirin"})
        assert passed is True
        assert "no major interactions" in msg


class TestValidateMedicalResponse:
    """validate_medical_response — dict 입력(force_json 미호출) 오케스트레이션."""

    def setup_method(self, _):
        import tempfile
        mv.init_medical_validator(tempfile.mkdtemp())

    def test_explain_no_validators(self):
        r = mv.validate_medical_response({"text": "설명"}, action="explain")
        assert r["passed"] is True
        assert r["checks"] == []

    def test_analyze_runs_validators(self):
        r = mv.validate_medical_response({"text": "정상 응답"}, action="analyze")
        assert r["passed"] is True
        names = {c["name"] for c in r["checks"]}
        assert "check_dosage_range" in names

    def test_analyze_catches_bad_dosage(self):
        r = mv.validate_medical_response({"plan": "6000mg 처방"}, action="analyze")
        assert r["passed"] is False
        assert r["warnings"]

    def test_unknown_action_uses_default(self):
        r = mv.validate_medical_response({"text": "x"}, action="unknown_xyz")
        names = {c["name"] for c in r["checks"]}
        assert "check_icd_consistency" in names and "check_dosage_range" in names

    def test_result_shape(self):
        r = mv.validate_medical_response({"text": "x"}, action="explain")
        assert set(r.keys()) == {"passed", "checks", "warnings"}
