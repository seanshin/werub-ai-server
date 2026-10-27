"""캡에 잘린 벌칙 조문의 **초점 호를 항 헤더와 함께 되돌린다** (id479, 2026-08-18).

🔴 증상: "의료인의 명찰 착용 의무는?"에 과태료를 **300만원**이라 답한다.
   정답은 제92조**③8호 100만원**이고, 그것도 **시정명령 불이행 시**다.
   항·형량·요건 **셋 다** 틀린다. 발생률 **3/40 = 7.5%**(윌슨 95% [2.6%, 19.9%]).

근본 원인 — 정답이 **캡 23자 밖**에 있다.

    제92조 청크 1699자  ·  생성 캡 1500자  ·  `8. 제4조제5항…` 위치 **1523**

모델이 보는 것은 맨 위의 `① … 300만원`뿐이다. ③8호는 한 글자도 안 보인다.
보이는 유일한 금액을 쓰는 것이다.

📌 v2.124.6이 캡을 600→1500으로 올려 봉합한 그 클래스인데, 1699자짜리가 또 걸렸다.
   **캡 경쟁으로는 안 닫힌다** — 제21조는 4,091자다. 필요한 조각만 되돌린다.

🔑 **숫자를 요약해 주는 게 아니라 원문 조각을 제자리에 되돌려 놓는다.**
   v2.124.94가 기각한 형량 부기는 주석이 금액을 굵게 실어 모델이 따라 쓴 것이 문제였다
   (금지 신호 6/12 → 12/12 악화). 여기서 모델이 읽는 것은 여전히 법령 원문이다.

⚠️ 항 헤더를 반드시 함께 실어야 한다 — 형량은 호가 아니라 **항 머리**에 있다.
   호만 되돌리면 금액이 여전히 안 보여 결함이 그대로 남는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import _focus_ho_tail, _paragraph_head

# 실제 제92조 청크의 구조를 축약한 것(항 머리에 형량, 호에 위반 조항).
BODY = (
    "제92조(과태료) ① 다음 각 호의 어느하나에 해당하는 자에게는 300만원 이하의 과태료를 "
    "부과한다.<개정 2015. 1. 28.>\n"
    "1. 제16조제3항에 따른 교육을 실시하지 아니한 자\n"
    "2. 제37조제1항에 따른 신고를 하지 아니하고 진단용 방사선 발생장치를 설치한 자\n"
    + ("가" * 1200) + "\n"
    "③ 다음 각 호의 어느 하나에 해당하는자에게는 100만원 이하의 과태료를 부과한다.<개정 2020. 3. 4.>\n"
    "7. 제52조의2제6항을 위반하여 대한민국의학한림원과 유사한 명칭을 사용한 자\n"
    "8. 제4조제5항을 위반하여 그 위반행위에 대하여 내려진 제63조에 따른 시정명령을 따르지 아니한 사람\n"
    "④ 제1항부터 제3항까지의 과태료는 대통령령으로 정하는 바에 따라 부과ㆍ징수한다."
)
CAP = 1300  # 8호가 캡 밖에 오도록


def test_restores_the_ho_beyond_cap():
    """정답 호가 캡 밖일 때 되돌려야 한다 — 이게 이 처치의 존재 이유다."""
    out = _focus_ho_tail(BODY, CAP, {"제4조"})
    assert out, "캡 밖 초점 호를 되돌리지 못했다"
    assert "제4조제5항" in out
    assert "시정명령" in out, "요건(시정명령 불이행)이 빠지면 답이 여전히 틀린다"


def test_carries_the_paragraph_head_with_the_amount():
    """🔑 형량은 호가 아니라 **항 머리**에 있다. 함께 싣지 않으면 처치가 무의미하다."""
    out = _focus_ho_tail(BODY, CAP, {"제4조"})
    assert "100만원" in out, "항 헤더(형량)가 안 실렸다"
    assert "③" in out
    # 잘못된 항의 금액을 끌어오면 안 된다
    assert "300만원" not in out


def test_no_output_when_chunk_fits_in_cap():
    """캡 안에 들어가면 되돌릴 것이 없다 — 발동 표면을 좁게 유지한다."""
    assert _focus_ho_tail(BODY, len(BODY) + 10, {"제4조"}) == ""


def test_no_output_when_reference_is_already_visible():
    """이미 보이는 참조는 되돌리지 않는다(중복 부착 방지)."""
    assert _focus_ho_tail(BODY, CAP, {"제16조"}) == ""


def test_no_output_without_focus():
    """초점(검색된 행위 조문)이 없으면 아무것도 하지 않는다 — fail-safe."""
    assert _focus_ho_tail(BODY, CAP, set()) == ""
    assert _focus_ho_tail("", CAP, {"제4조"}) == ""


def test_paragraph_head_picks_the_nearest_preceding_marker():
    pos = BODY.find("8. 제4조제5항")
    head = _paragraph_head(BODY, pos)
    assert "100만원" in head and "③" in head


def test_year_tokens_are_not_parsed_as_ho():
    """`<개정 2015. 1. 28.>`의 숫자를 호 시작으로 읽으면 엉뚱한 조각이 잘려 나온다."""
    out = _focus_ho_tail(BODY, CAP, {"제4조"})
    assert out.strip().splitlines()[-1].lstrip().startswith("8."), \
        "호 경계를 잘못 잡았다: %r" % out


def test_output_is_verbatim_not_a_summary():
    """되돌린 것은 **원문 그대로**여야 한다 — 요약하면 v2.124.94가 기각한 그 부기가 된다."""
    out = _focus_ho_tail(BODY, CAP, {"제4조"})
    ho = "8. 제4조제5항을 위반하여 그 위반행위에 대하여 내려진 제63조에 따른 시정명령을 따르지 아니한 사람"
    assert ho in out
