"""judge가 **근거를 답변으로 읽는** 오염 봉합 — 평가 대상 명시 회귀 (2026-08-06).

근거 길이 상위 8문항 × R=5 (오염 = 사유가 근거를 평가 대상으로 지칭하거나 비한국어):

    current                45/120
    label-only(블록 이름만)  45/120   ← 뒤집힘 0. **라벨은 듣지 않는다**
    tail-only(이 문장)        0/120   ← 문항 단위 개선 3·악화 0
    reorder(근거를 앞으로)      0/120   ← 오염은 잡히나 다른 판정이 무너져 기각

`no_prescriptive`는 12항목 중 유일한 부정형이라 프롬프트가 흔들리면 **극성이 뒤집혔다**
(사유 "처방이 없다" + score 5인데 False) → 긍정형 결정 규칙으로 교정.
통제군 5/5 유지 = 실패율 하락이 "전부 통과"가 아니다.
"""
import core.llm_judge as J


def _prompt(evidence, checklist=True):
    seen = {}

    def ask(sys_msg, p, schema, model=None):
        seen.setdefault("all", []).append(p)
        return {}

    J.judge("보존기간은?", "10년입니다.", evidence, ask_json=ask, checklist=checklist)
    return seen["all"]


def test_target_note_present_with_evidence():
    p = _prompt("제15조 근거 원문")[0]
    assert J._TARGET_NOTE in p
    assert p.rstrip().endswith(J._TARGET_NOTE)      # 마지막 지시여야 듣는다


def test_no_target_note_without_evidence():
    """근거가 없으면 가리킬 블록이 없다 — 붙이면 없는 것을 지칭하게 된다.

    fluency 2차 판정이 정확히 이 경로다(v2.124.88)."""
    prompts = _prompt("")
    assert len(prompts) == 1                        # 근거 없으면 보조 호출도 없음
    assert J._TARGET_NOTE not in prompts[0]
    assert "[근거]" not in prompts[0]


def test_fluency_second_pass_has_no_note():
    """근거가 있으면 1차엔 붙고, 근거 없이 도는 2차엔 붙지 않는다."""
    prompts = _prompt("제15조 근거 원문")
    assert len(prompts) == 2
    assert J._TARGET_NOTE in prompts[0]
    assert J._TARGET_NOTE not in prompts[1]


def test_no_prescriptive_is_affirmative():
    """유일한 부정형이었던 항목을 긍정형 결정 규칙으로 — 극성 뒤집힘 방지."""
    d = dict(J.CHECKLIST["safety"])["no_prescriptive"]
    assert "정보 제공 범위를 지키는가" in d
    assert "지시할 때만 false" in d


def test_note_is_single_source():
    """문구는 한 곳에서만 정의한다(프롬프트 문자열에 하드코딩 금지)."""
    import inspect
    src = inspect.getsource(J._judge_once)
    assert "_TARGET_NOTE" in src
    assert "대조용일 뿐" not in src


def test_checklist_ids_unchanged():
    """항목 id 불변 — 시계열 연속성."""
    assert [c for c, _ in J.CHECKLIST["safety"]] == [
        "advisory_disclaimer", "no_prescriptive", "expert_referral"]
