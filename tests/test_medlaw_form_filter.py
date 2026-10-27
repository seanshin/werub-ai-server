"""서식/양식 청크 억제 필터 회귀 (id95 진료비확인 양식 오염 봉합, v2.124.x).
_is_form_chunk 정밀도(서식만 True·실질 조문 False) + _drop_form_chunks fail-safe."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import _is_form_chunk, _drop_form_chunks


# ── 서식으로 판정돼야 하는 것 (True) ──
def test_form_consent_checkbox():
    assert _is_form_chunk("위와 같은 사실에 동의합니다. [ ] 동의함 [ ] 동의안함 서명 또는 인")


def test_form_checkbox_density():
    assert _is_form_chunk("[ ] 유 [ ] 무 [ ] 해당없음 [ ] 남 [ ] 여 항목")


def test_form_application_header():
    assert _is_form_chunk("접수번호 접수일 처리기간 15일 성명 주민등록번호 주소 요청인")


def test_form_delegation():
    assert _is_form_chunk("본서류 제공 동의서 포함 위임자와 수임자의 관계 위임장")


def test_form_paper_spec_header():
    assert _is_form_chunk("건강보험심사평가원장 귀하 210㎜×297㎜ 접수번호 신청인")


# ── 실질 조문은 통과해야 함 (False) ──
def test_real_law_article_not_form():
    assert not _is_form_chunk(
        "제21조(기록 열람 등) ① 환자는 의료인, 의료기관의 장 및 의료기관 종사자에게 "
        "본인에 관한 기록의 전부 또는 일부에 대하여 열람 또는 그 사본의 발급 등을 요청할 수 있다.")


def test_retention_provision_not_form():
    assert not _is_form_chunk("제15조(진료기록부 등의 보존) 의료인이나 의료기관 개설자는 진료기록부를 10년간 보존하여야 한다.")


def test_single_checkbox_not_form():
    # [ ] 1~2개만 있는 정상 열거는 서식 아님
    assert not _is_form_chunk("다음 각 호[ ]의 어느 하나에 해당하면 처리기간 내에 통지한다.")


# ── _drop_form_chunks: 필터 + fail-safe ──
def test_drop_filters_forms():
    hits = [{"text": "제21조 환자는 사본 발급을 요청할 수 있다"},
            {"text": "[ ] 동의함 [ ] 동의안함 서명 또는 인"}]
    kept = _drop_form_chunks(hits)
    assert len(kept) == 1 and "제21조" in kept[0]["text"]


def test_drop_failsafe_all_forms():
    # 전량 서식이면 근거 소실 방지 위해 원본 유지 (legal 경로 보호)
    hits = [{"text": "[ ] 동의함 [ ] 동의안함"}, {"text": "접수번호 처리기간 신청인"}]
    assert _drop_form_chunks(hits) == hits


def test_drop_empty():
    assert _drop_form_chunks([]) == []
