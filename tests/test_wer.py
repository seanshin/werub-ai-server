"""core/wer — WER/CER 자체 구현 검증 (X10 준비).

측정 도구부터 라벨 통제군으로 검증한다(feedback_validate_the_detector) —
편집거리가 틀리면 WER 게이트 판정이 통째로 틀린다.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.wer import wer, cer, normalize, wer_report  # noqa: E402


# ── 통제군: 알려진 편집거리 ────────────────────────────────────────────────

def test_identical_is_zero():
    assert wer("환자 진료 기록", "환자 진료 기록") == 0.0
    assert cer("환자 진료 기록", "환자 진료 기록") == 0.0


def test_one_substitution():
    # 3어절 중 1개 치환 = 1/3
    assert abs(wer("환자 진료 기록", "환자 진료 차트") - 1 / 3) < 1e-9


def test_one_deletion_and_insertion():
    assert abs(wer("가 나 다 라", "가 나 다") - 1 / 4) < 1e-9        # 삭제 1/4
    assert abs(wer("가 나 다", "가 나 다 라") - 1 / 3) < 1e-9        # 삽입 1/3


def test_all_wrong_is_one():
    assert wer("가 나 다", "x y z") == 1.0


def test_empty_reference():
    assert wer("", "") == 0.0
    assert wer("", "무언가") == 1.0          # 참조 비었는데 가설 있음 = 전부 오류


# ── 정규화 ─────────────────────────────────────────────────────────────────

def test_punctuation_and_whitespace_normalized():
    # 문장부호·연속공백·낫표는 WER에 영향 없어야
    assert wer("환자, 진료  기록.", "환자 진료 기록") == 0.0
    assert normalize("「의료법」 제8조!") == "의료법 제8조"


def test_english_case_folding():
    assert wer("Hello World", "hello world") == 0.0


def test_cer_catches_josa_difference():
    # 어절 단위(WER)는 통째 치환이지만 문자 단위(CER)는 조사 1자만 다름
    r, h = "환자는 왔다", "환자가 왔다"
    assert wer(r, h) == 0.5                    # 2어절 중 1치환
    assert 0.0 < cer(r, h) < 0.3               # 6자 중 1자


# ── 2%p 열화 판정 시나리오(사양) ───────────────────────────────────────────

def test_degradation_delta_semantics():
    ref = "오늘 회의 안건은 예산 심의 입니다"
    gpu = "오늘 회의 안건은 예산 심의 입니다"      # WER 0.0 (완벽)
    imac = "오늘 회의 안건은 예산 심사 입니다"      # 1치환 = 1/6 ≈ 0.1667
    w_gpu, w_imac = wer(ref, gpu), wer(ref, imac)
    delta_pp = (w_imac - w_gpu) * 100
    assert w_gpu == 0.0
    assert abs(delta_pp - 100 / 6) < 1e-6         # 16.67%p 열화 = 게이트 실패(>2%p)


def test_report_shape():
    rep = wer_report("가 나 다", "가 나 라")
    assert set(rep) == {"wer", "cer", "ref_words", "hyp_words"}
    assert rep["ref_words"] == 3 and rep["hyp_words"] == 3
