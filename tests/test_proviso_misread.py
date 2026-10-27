"""E1 후단·단서 오독 탐지기 통제군 (`core/proviso_misread`) — v2.125.15.

규율: 결함을 세기 전에 탐지기를 라벨 통제군에 건다. 통제군은 오답/정답 2종이
아니라 **실전 패턴의 종류**를 덮는다(v2.125.8 §3 — law_attribution v1이 통제군
7/7을 통과하고도 실전 위양성 4건을 낸 교훈).

패턴 종류:
  ① 오독(양성) — id728② **실물**: 근거 "생략할 수 있다"(허용) ↔ 답변 "(… 제외)"
  ② 정독-배제 정당(음성) — 근거가 실제로 "…는 제외한다"
  ③ 예외조항 정독(음성) — 근거 "그러하지 아니하다"를 "(… 제외)"로 요약한 것은 옳다
  ④ 패러프레이즈(음성) — 허용을 허용으로 옮긴 답변(괄호 배제 부기 없음)
  ⑤ 엔티티 부재(음성) — 근거에 그 대상이 없으면 판정하지 않는다(fail-open)
  ⑥ 배제·허용 혼재(음성) — 근거 한 문장이라도 배제하면 정독으로 본다
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.proviso_misread import misread_exclusions

# ── ① 실물(id728② · 2026-08-18 배치 저장 답변·근거 발췌 그대로) ─────────────
_EVIDENCE_728 = (
    "4. 병원ㆍ한방병원ㆍ치과병원ㆍ의원ㆍ한의원 또는 치과의원의 개설자가 전문의인 경우에는 "
    "그 의료기관의 고유명칭앞에 전문과목 및 전문의를 함께 표시하거나 의료기관의 고유명칭과 "
    "의료기관의 종류 명칭 사이에 인정받은 전문과목을 삽입하여 표시할 수 있다. "
    "이 경우 전문과목에 “치과”가 포함된 치과병원ㆍ치과의원의 경우에는 제1호 전단에도 "
    "불구하고 의료기관의 종류 명칭에서 “치과”를 생략할 수 있다.\n"
    "5. 제32조에 따른 부속 의료기관이 명칭을 표시하는 경우에는 의료기관의 종류에 따르는 "
    "명칭 앞에 그 개설기관의명칭과 “부속”이라는 문자를 붙여 표시하여야 한다."
)
_ANSWER_728 = (
    "3. **전문의 개설자**:\n"
    " - 전문의인 경우 고유명칭 앞에 전문과목 및 전문의 자격을 함께 표시하거나, "
    "의료기관 종류 명칭 사이에 인정받은 전문과목 삽입 가능 (치과병원·치과의원 제외)"
)


def test_real_id728_misread_is_flagged():
    flags = misread_exclusions(_ANSWER_728, _EVIDENCE_728)
    assert {f["entity"] for f in flags} == {"치과병원", "치과의원"}
    # evidence_head는 판독용 문장 머리 — 플래그된 엔티티가 실린 허용 문장이어야 한다
    assert all(f["entity"] in f["evidence_head"] for f in flags)
    assert all("제외" in f["answer_span"] for f in flags)


# ── ② 정독: 근거가 실제로 배제 ─────────────────────────────────────────────
def test_legit_exclusion_not_flagged():
    ev = "의료기관 개설 신고 대상은 의원ㆍ한의원으로 한다. 조산원은 제외한다."
    ans = "개설 신고 대상 기관 (조산원 제외)"
    assert misread_exclusions(ans, ev) == []


# ── ③ 예외조항("그러하지 아니하다")을 제외로 요약한 것은 정독 ──────────────
def test_exception_clause_reading_not_flagged():
    ev = ("의료인이 될 수 없다. 다만, 전문의가 의료인으로서 적합하다고 인정한 "
          "정신질환자는 그러하지 아니하다.")
    ans = "결격사유: 정신질환자 (전문의가 인정한 정신질환자 제외)"
    assert misread_exclusions(ans, ev) == []


# ── ④ 허용을 허용으로 옮긴 패러프레이즈 ────────────────────────────────────
def test_permissive_paraphrase_not_flagged():
    ans = "치과병원·치과의원은 종류 명칭에서 '치과'를 생략할 수 있다."
    assert misread_exclusions(ans, _EVIDENCE_728) == []


# ── ⑤ 엔티티가 근거에 없으면 판정하지 않는다 ───────────────────────────────
def test_absent_entity_fail_open():
    ans = "명칭 표시 가능 (한방병원 제외)"
    ev = "의원과 치과의원은 명칭을 표시할 수 있다."
    assert misread_exclusions(ans, ev) == []


# ── ⑥ 배제·허용 혼재 — 한 문장이라도 배제하면 정독 ─────────────────────────
def test_mixed_sentences_exclusion_wins():
    ev = ("의원은 진단서를 발급할 수 있다.\n"
          "제3조의 적용 대상에서 의원은 제외한다.")
    ans = "적용 대상 기관 (의원 제외)"
    assert misread_exclusions(ans, ev) == []


# ── 경계: 빈 입력·괄호 없음·전각 괄호 ──────────────────────────────────────
def test_empty_inputs():
    assert misread_exclusions("", _EVIDENCE_728) == []
    assert misread_exclusions(_ANSWER_728, "") == []


def test_fullwidth_paren_and_josa_variants():
    ans = "전문과목 삽입 가능 （치과병원·치과의원은 제외）"
    flags = misread_exclusions(ans, _EVIDENCE_728)
    assert {f["entity"] for f in flags} == {"치과병원", "치과의원"}


def test_same_entity_not_duplicated():
    ans = "가능 (치과병원 제외) … 다시 (치과병원 제외)"
    flags = misread_exclusions(ans, _EVIDENCE_728)
    assert [f["entity"] for f in flags] == ["치과병원"]


# ── 배선: score()가 이 축을 싣는다(배선이 반만 되는 사고 방지 — v2.125.9 §7-1) ──
def test_faithfulness_score_carries_proviso_axis():
    from core import faithfulness as F
    out = F.score(_ANSWER_728, _EVIDENCE_728, ask_json=lambda *a, **k: {"claims": []})
    assert {f["entity"] for f in out["proviso_misread"]} == {"치과병원", "치과의원"}
    assert "law_misattributed" in out    # 기존 축도 그대로
