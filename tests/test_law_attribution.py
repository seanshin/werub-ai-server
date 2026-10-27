"""법령명 오귀속 탐지 — **통제군이 실전 분포를 덮는지**가 이 파일의 요점이다.

🔴 v1은 통제군 7/7(오귀속 3 + 정상 4)을 통과하고도 실전에서 **위양성 4건**을 냈다.
   통제군에 **"법령 본문이 다른 법을 정당하게 인용하는 경우"가 한 건도 없었기** 때문이다.
   그 패턴은 실전에만 있었고, 그래서 통과가 보증이 되지 않았다.

⇒ `test_cross_reference_*`가 이 파일의 핵심이다. 지우거나 약화시키면 v1로 돌아간다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.law_attribution import cited_laws, misattributed

# ── 오귀속(양성) — 근거 어디에도 없는 법을 댔다 ─────────────────────────────
# ⚠️ 근거 문자열에는 **법령명 라벨**이 있어야 한다 — 라벨이 없으면 탐지기가
#    판정을 건너뛴다(2026-08-18: 평가 근거가 조문 본문만이라 위양성 22/40).
#    `/ask` 생성 컨텍스트가 붙이는 형태를 그대로 쓴다.
MISATTRIBUTED = [
    ("법률명: 국민건강보험법 / 제58조의3(의료기관 인증기준) ③",
     "[권위:법률] [의료법 법률 제58조의3(의료기관 인증)] 인증 유효기간은 4년으로 한다",
     "국민건강보험법"),
    ("「약사법」 제29조에 따라 처방전을 보존해야 합니다.",
     "[권위:법률] [의료법 시행규칙 제15조(진료기록부 등의 보존)] 처방전 2년",
     "약사법"),
    # 실재하지 않는 법률명 — 기각된 후보가 실제로 낸 답이다
    ("「의료인법」제65조에 따라 면허가 취소될 수 있습니다.",
     "[권위:법률] [의료법 법률 제65조(면허 취소와 재교부)] 보건복지부장관은 …",
     "의료인법"),
]

# ── 정상(음성) — 근거에 있는 법 ─────────────────────────────────────────────
CORRECT = [
    ("「의료법」 제58조의3제3항에 따라 인증 유효기간은 4년이다.",
     "[권위:법률] [의료법 법률 제58조의3(의료기관 인증)] 4년"),
    ("의료법 시행규칙 제15조제1항에 따라 10년간 보존한다.",
     "[권위:법률] [의료법 시행규칙 제15조(진료기록부 등의 보존)] 진료기록부 10년"),
    ("벌칙은 근거에서 확인되지 않습니다.",
     "[권위:법률] [의료법 법률 제21조(기록 열람 등)] …"),
]

# ── 🔑 v1을 뚫었던 패턴 — 법령 본문이 다른 법을 **정당하게** 인용한다 ──────────
CROSS_REFERENCE = [
    ("「장애인복지법」에 따른 시각장애인이어야 한다.",
     "[권위:법률] [의료법 법률 제82조(안마사)] 「장애인복지법」에 따른 시각장애인으로서"),
    ("「전자서명법」에 따른 전자서명이 기재된 전자문서로 작성할 수 있다.",
     "[권위:법률] [의료법 법률 제23조(전자의무기록)] 「전자서명법」에 따른 전자서명"),
    ("「노인복지법」 제34조에 따른 시설의 종사자가 대리수령할 수 있다.",
     "[권위:법률] [의료법 법률 제17조의2(처방전)] 「노인복지법」 제34조에 따른 시설 종사자"),
    ("「초ㆍ중등교육법」에 따른 특수학교에서 안마수련과정을 마친 자",
     "[권위:법률] [의료법 법률 제82조(안마사)] 「초ㆍ중등교육법」에 따른 특수학교"),
]


@pytest.mark.parametrize("answer,evidence,expected", MISATTRIBUTED)
def test_detects_misattribution(answer, evidence, expected):
    """근거에 없는 법을 대면 잡아야 한다 — 이게 이 탐지기의 존재 이유다."""
    assert expected in misattributed(answer, evidence)


@pytest.mark.parametrize("answer,evidence", CORRECT)
def test_correct_citation_is_not_flagged(answer, evidence):
    assert misattributed(answer, evidence) == []


@pytest.mark.parametrize("answer,evidence", CROSS_REFERENCE)
def test_cross_reference_is_not_flagged(answer, evidence):
    """🔑 **v1이 여기서 뚫렸다.** 법령 본문이 다른 법을 인용하는 것은 정상이다.

    이 케이스가 없으면 탐지기가 통제군을 통과하고도 실전에서 위양성을 낸다
    (2026-08-18 실측: 안마사→장애인복지법·초중등교육법, 전자의무기록→전자서명법,
     처방전 대리수령→노인복지법 — 전부 정당한 인용인데 v1이 오귀속으로 셌다).
    """
    assert misattributed(answer, evidence) == [], "정당한 타법 인용을 오귀속으로 셌다"


def test_unlabelled_evidence_is_not_judged():
    """🔴 근거에 **법령명 라벨이 없으면 판정하지 않는다**(2026-08-18 봉쇄).

    평가 근거(`get_evidence()`)는 조문 본문만이라 "의료법"이라는 문자열이 아예 없다
    (실측 15/40). 그 상태로 세면 **정확한 인용이 오귀속으로** 잡힌다 — 기준선 배치에서
    22/40이 잡혔고 전부 위양성이었다. 근거 표현이 라벨을 싣게 되면 자동으로 풀린다.
    """
    answer = "의료법 시행규칙 제15조제1항에 따라 10년간 보존한다."
    plain = "제15조(진료기록부 등의 보존) ① 의료인이나 의료기관 개설자는 …"
    assert misattributed(answer, plain) == [], "라벨 없는 근거를 판정하면 위양성이 난다"
    labelled = "[권위:법률] [의료법 시행규칙 제15조(진료기록부 등의 보존)] 진료기록부 10년"
    assert misattributed(answer, labelled) == []
    assert misattributed("「약사법」 제29조", labelled) == ["약사법"]


def test_no_evidence_means_no_judgement():
    """근거가 없으면 판정하지 않는다(fail-open) — 근거 소실이 오귀속보다 나쁘다."""
    assert misattributed("「약사법」 제29조", "") == []
    assert misattributed("「약사법」 제29조", None) == []


def test_common_nouns_ending_in_law_are_not_cited_laws():
    """`방법`·`요법`·`동법` 같은 일반명사를 법령명으로 세면 전 답변이 오판된다."""
    for t in ("치료 방법을 설명한다", "물리 요법을 시행한다", "동법 제3조에 따라"):
        assert cited_laws(t) == [], t


def test_enforcement_decree_folds_into_parent_law():
    """시행령·시행규칙은 모법으로 접는다 — 별개 법령으로 세면 전부 오귀속이 된다."""
    assert cited_laws("의료법 시행규칙 제15조에 따라") == ["의료법"]
    assert misattributed("의료법 시행규칙 제15조에 따라", "의료법 제21조") == []
