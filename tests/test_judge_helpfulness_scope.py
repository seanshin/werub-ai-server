"""`complete`·`actionable`의 판정 **범위** 회귀 (2026-08-06).

08-05 배치 40문항에서 두 항목이 checks 실패 2·3위였다(35%·30%). 저장된 judge 사유
23건을 전수로 읽으니 답변 결함이 아니었다:

  · 질문이 묻지 않은 항목의 부재를 감점 9건 — 그중 **5건은 답변의
    "벌칙은 근거에서 확인되지 않습니다"를 감점**(생성 규칙2를 지킨 문장)
  · 사유가 루브릭 문구 되풀이 6건 — 요구한 예시가 `(예: 평가 기준)`류의 **행동 지침**
  · 지적한 문구가 답변에 실재 5건(id456 "고소가 있어야 한다"는 인용문 안에 그대로)

두 항목에 범위 교정을 만들어 저장 답변 40건으로 재채점(생성 변동 0) 비교했다:

    변형   actionable 실패   complete 실패
    old        15 (38%)        15 (38%)
    new         4 (10%)        19 (48%)

부호검정 → actionable 개선 12·악화 1(p=0.003) **채택** /
complete 개선 5·악화 9(p=0.42) **기각·문구 되돌림**.
"""
import core.llm_judge as J


def _desc(dim, cid):
    return dict(J.CHECKLIST[dim])[cid]


def test_complete_wording_stays_unmeasured_free():
    """🔴 `complete` 범위 교정은 **측정으로 기각**됐다 — 문구를 되돌린 상태를 고정한다.

    저장 답변 40건 재채점: 개선 5 · 악화 9(p=0.42)로 순효과가 없고 방향은 오히려 나빴다.
    판정은 14/40이나 뒤집혔지만 양방향으로 흩어졌다. 효과가 확인되지 않은 처치를
    "말이 되니까" 남겨 두면 다음 측정이 그 위에서 흔들린다(v2.124.63 교훈).
    """
    assert _desc("helpfulness", "complete") == "질문의 핵심 하위 요소를 빠뜨리지 않는가"


def test_actionable_is_specificity_not_advice():
    """법령 조회 과제이므로 행동 지침이 아니라 구체 정보를 본다."""
    d = _desc("helpfulness", "actionable")
    assert "조문 번호" in d and "요건" in d
    assert "행동 지침" in d  # 요구하지 않는다는 명시


def test_scope_wording_reaches_prompt():
    """설명은 체크리스트 프롬프트에 그대로 실려야 효력이 있다."""
    seen = {}

    def ask(sys_msg, prompt, schema, model=None):
        seen["p"] = prompt
        return {}

    J.judge("질문", "답변", "", ask_json=ask, checklist=True)
    p = seen["p"]
    assert "helpfulness.complete" in p and "helpfulness.actionable" in p
    assert "행동 지침" in p


def test_checklist_ids_unchanged():
    """항목 id는 추세 연속성을 위해 불변 — 이름이 바뀌면 시계열이 끊긴다."""
    assert [cid for cid, _ in J.CHECKLIST["helpfulness"]] == [
        "direct_answer", "actionable", "complete"]


def test_helpfulness_not_deterministic():
    """이 둘은 순수 의미 판정이라 결정적 이관 대상이 아니다(2026-08-05 판정).

    형식 3항목만 덮어쓰고 helpfulness는 judge 값이 그대로 남아야 한다."""
    scores = {"helpfulness": {"score": 3, "reason": "",
                              "checks": {"direct_answer": True, "actionable": False,
                                         "complete": False}}}
    out = J._apply_deterministic_checks(scores, "답변", "근거")
    assert out["helpfulness"]["checks"]["actionable"] is False
    assert out["helpfulness"]["checks"]["complete"] is False
