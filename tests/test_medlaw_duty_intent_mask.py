"""금지·의무형 게이트 — **`의무기록`에 오발동하지 않는다** (2026-08-18 실결함).

🔴 증상: "전자의무기록으로 진료기록부를 대체할 수 있는가?"에 대해 모델이 **묻지도 않은
   벌칙**을 덧붙이며 제23조③(제87조의2 · 5년/5천만)과 ④(제90조 · 500만원)를 혼동했다.
   재현 **2/5**.

근본 원인: `_DUTY_INTENT_TERMS`의 `의무`가 **전자의무기록**의 부분문자열이라
게이트가 이 질의를 "의무를 묻는 질의"로 읽었다.

    질의: "전자의무기록으로 진료기록부를 대체할 수 있는가?"
            ^^^^  ← '의무' 매칭 위치 2, 문맥 '전자의무기록으로'

연쇄는 이렇다 — 게이트 오발동 → `_penalty_backref_chunks`가 벌칙 조문을 근거에 붙임
→ 질의가 어느 항도 묻지 않으므로 `_drop_paragraph_mismatch`는 **설계대로** 아무것도
버리지 않음(양성 매칭이 없을 때 fail-open) → 모델이 근거에 있는 벌칙을 자발적으로
인용하며 조번호만 보고 잘못된 항의 벌칙을 고름.

📌 **단어 경계 없는 부분문자열 매칭** 클래스다. 약물명에서 두 번 고쳤다
   (v2.101/102: `이소트레티노인`의 `노인` 오인, 연결어 `와`가 `와파린`에 오매칭).

🔑 처치는 **좁히는 방향**이다 — 도메인 복합어를 지운 뒤 의도 어휘를 센다.
   게이트가 원래 잡던 "~할 의무는?"·"~금지 의무는?"은 그대로 발동해야 하고,
   이 파일이 그 불변식을 양방향으로 고정한다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import _DUTY_MASK_RE, _has_duty_intent

# 게이트가 **발동해야** 하는 질의 — 금지·의무형(역참조 벌칙 근거가 필요하다)
DUTY_QUESTIONS = [
    "의료인의 명찰 착용 의무는?",
    "태아 성감별 고지 금지 의무는?",          # v2.124.81이 게이트를 넓힌 그 실증 문항
    "의료인의 환자 비밀누설 금지 의무와 위반 시 벌칙은?",
    "의료기관 개설자의 준수사항은?",
    "진료거부 금지 규정은?",
    "의료광고에서 금지되는 내용은 무엇인가?",
    "의료인이 하여서는 아니 되는 행위는?",
    "의료기관의 명칭 표시 제한은?",
]

# 게이트가 **발동하면 안 되는** 질의 — `의무기록` 도메인 용어이지 의무를 묻는 게 아니다
RECORD_QUESTIONS = [
    "전자의무기록으로 진료기록부를 대체할 수 있는가?",   # 🔴 실결함 재현 문항
    "의무기록 보존기간은?",
    "전자의무기록시스템 인증 요건은?",
    "전자의무기록의 표준화 대상은?",
    "의무기록사의 업무 범위는?",
]

# 의도 어휘가 아예 없는 질의 — 원래도 발동하지 않았다(회귀 가드)
NEUTRAL_QUESTIONS = [
    "진료기록부 보존기간은?",
    "상급종합병원 지정 요건은?",
    "의료법인 설립 절차는?",
]


@pytest.mark.parametrize("q", DUTY_QUESTIONS)
def test_duty_intent_still_fires(q):
    """좁히기가 원래 잡던 것까지 죽이면 안 된다 — 커버리지 축소는 창작을 부른다."""
    assert _has_duty_intent(q), f"금지·의무형인데 게이트 미발동: {q}"


@pytest.mark.parametrize("q", RECORD_QUESTIONS)
def test_record_terms_do_not_fire(q):
    """`의무기록`은 도메인 용어다. 여기서 발동하면 묻지 않은 벌칙이 근거에 붙는다."""
    assert not _has_duty_intent(q), f"의무기록 질의에 게이트 오발동: {q}"


@pytest.mark.parametrize("q", NEUTRAL_QUESTIONS)
def test_neutral_questions_do_not_fire(q):
    assert not _has_duty_intent(q)


def test_mask_removes_only_record_compounds():
    """마스크가 지우는 범위를 고정한다 — 넓히면 진짜 의무 질의를 삼킨다."""
    assert _DUTY_MASK_RE.sub(" ", "전자의무기록") .strip() == ""
    assert _DUTY_MASK_RE.sub(" ", "의무기록").strip() == ""
    assert _DUTY_MASK_RE.sub(" ", "전자의무기록시스템").strip() == ""
    # 진짜 의무는 지워지지 않는다
    assert "의무" in _DUTY_MASK_RE.sub(" ", "착용 의무는?")
    assert "의무" in _DUTY_MASK_RE.sub(" ", "신고 의무가 있는가?")


def test_regression_the_exact_defect_question():
    """전제 고정 — 마스크 없이 세면 이 질의가 발동한다는 것이 이 처치의 근거다.

    마스크를 걷어냈을 때도 발동하지 않게 되면(예: 어휘 목록에서 `의무`가 빠지면)
    이 테스트가 먼저 빨개져서 **처치의 근거가 사라졌음**을 알린다.
    """
    from routes.medical_law import _DUTY_INTENT_TERMS
    q = "전자의무기록으로 진료기록부를 대체할 수 있는가?"
    assert any(t in q for t in _DUTY_INTENT_TERMS), "마스크 없는 원래 판정이 이미 안 걸린다"
    assert not _has_duty_intent(q), "마스크가 듣지 않는다"
