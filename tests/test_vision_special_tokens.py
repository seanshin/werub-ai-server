"""의료 영상 판독 특수 토큰 누출 봉합 회귀 (2026-08-04).

실측: MedGemma 1.5가 `recommendations` 끝에 `<start_of_image>`를 붙여 내보냈다.
트윈이 그대로 표시하면 파트너 UI에 모델 내부 토큰이 노출된다.
"""
import pytest

from routes.vision import _strip_special_tokens


class TestStrip:
    def test_removes_the_observed_token(self):
        """실제로 관측된 그 토큰."""
        t = ("- 이 보고서는 교육/참조 목적이며, 전문의 해석을 대체하지 않습니다. "
             "<start_of_image>")
        out = _strip_special_tokens(t)
        assert "<start_of_image>" not in out
        assert "대체하지 않습니다" in out

    @pytest.mark.parametrize("tok", [
        "<start_of_image>", "<end_of_image>", "<image>",
        "<start_of_turn>", "<end_of_turn>", "<bos>", "<eos>", "<pad>", "<s>", "</s>",
    ])
    def test_removes_known_token_family(self, tok):
        assert _strip_special_tokens("소견 %s 끝" % tok).find(tok) == -1

    def test_case_and_space_tolerant(self):
        assert "<" not in _strip_special_tokens("a < START_OF_IMAGE > b")
        assert "<" not in _strip_special_tokens("a <Start_Of_Image> b")

    def test_keeps_medical_content(self):
        """판독 본문을 지우면 안 된다 — 부등호가 든 수치도 보존."""
        t = "심흉곽비 < 0.5로 정상이며, 늑막삼출 없음."
        assert _strip_special_tokens(t) == t

    def test_cleans_residual_whitespace(self):
        out = _strip_special_tokens("소견 있음   \n\n\n\n권고 사항 <eos>")
        assert "\n\n\n" not in out
        assert not out.endswith(" ")
        assert out.endswith("권고 사항")

    def test_empty_and_none(self):
        assert _strip_special_tokens("") == ""
        assert _strip_special_tokens(None) is None

    def test_only_token_becomes_empty(self):
        assert _strip_special_tokens("<start_of_image>") == ""


class TestDepartmentTerms:
    """진료과 명칭 교정 — 판독 소견이 아니라 **안내 문구**의 오류다.
    실측 N=6에서 "방사선 종양학과"가 2/6(33%) 반복됐다. 방사선종양학과는 암 방사선
    치료과이고 영상 판독은 영상의학과 소관이라, 환자를 다른 과로 보내는 안내가 된다."""

    def test_fixes_the_observed_error(self):
        from routes.vision import _fix_department_terms
        t = "이 보고서는 전문 방사선 종양학과 진료사의 해석을 대체하지 않습니다."
        out = _fix_department_terms(t)
        assert "영상의학과" in out
        assert "방사선 종양학과" not in out

    def test_spacing_variants(self):
        from routes.vision import _fix_department_terms
        for v in ["방사선종양학과", "방사선 종양학과", "방사선  종양 과"]:
            assert "영상의학과" in _fix_department_terms("전문 %s 상담" % v)

    def test_bare_radiology_dept(self):
        from routes.vision import _fix_department_terms
        assert "영상의학과 전문의" in _fix_department_terms("방사선과 전문의에게 문의")

    def test_keeps_clinical_findings(self):
        """판독 소견의 의학 용어는 건드리지 않는다."""
        from routes.vision import _fix_department_terms
        t = "늑막삼출 없음. 기흉 없음. 심장은 정상 크기입니다."
        assert _fix_department_terms(t) == t

    def test_does_not_touch_radiation_therapy_context(self):
        """방사선 '치료' 맥락의 단어는 남긴다 — 과 이름만 대상."""
        from routes.vision import _fix_department_terms
        t = "방사선 치료 이력이 있습니다."
        assert _fix_department_terms(t) == t

    def test_empty(self):
        from routes.vision import _fix_department_terms
        assert _fix_department_terms("") == ""
        assert _fix_department_terms(None) is None
