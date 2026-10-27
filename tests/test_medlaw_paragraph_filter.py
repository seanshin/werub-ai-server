"""항 불일치 벌칙 근거 제외 — 순수 판정 회귀 (v2.124.94).

08-10 검색 진단: "무면허 의료행위의 벌칙은?"에서 **제90조가 벡터 1순위**(0.6703),
정답 제87조의2가 2위(0.6466)로 격차 0.023(노이즈 수준)이었다. 제90조를 끌어올린 것은
꼬리 한 구절 "의료기관 개설자가 될 수 없는 자에게 **고용되어 의료행위를 한 자**"
(단독 0.6881 = 전문 0.6699보다 높다) — **사무장병원** 조항인데 임베딩이 무면허와
구분하지 못한다. 정답 조문에는 "무면허"라는 말이 아예 없고 `제27조제1항`이라는
조번호로만 지칭돼 있어 표면 어휘로는 임베딩·재랭커 어느 쪽도 못 고친다.
가릴 수 있는 정보는 **어느 항을 처벌하는가** 하나뿐이다.

🔑 안전 규율: **양성 매칭이 남을 때만 버린다.** 이 파일은 그 불변식을 고정한다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import pytest

from routes.medical_law import (
    _drop_paragraph_mismatch,
    _listed_paragraphs,
    _paragraph_blocks,
    _target_paragraphs,
)

ART27 = ("제27조(무면허 의료행위 등 금지) ① 의료인이 아니면 누구든지 의료행위를 할 수 없으며 "
         "의료인도 면허된 것 이외의 의료행위를 할 수 없다.\n"
         "② 의료인이 아니면 의사ㆍ치과의사ㆍ한의사ㆍ조산사 또는 간호사 명칭이나 이와 비슷한 "
         "명칭을 사용하지 못한다.\n"
         "③ 누구든지 본인부담금을 면제하거나 할인하는 행위 등 영리를 목적으로 환자를 "
         "의료기관이나 의료인에게 소개ㆍ알선ㆍ유인하는 행위를 하여서는 아니 된다.\n"
         "⑤ 누구든지 의료인이 아닌 자에게 의료행위를 하게 하여서는 아니 된다.")

P90 = "제90조(벌칙) 제16조제1항, 제26조, 제27조제2항, 제41조를 위반한 자는 500만원 이하의 벌금에 처한다."
P87_2 = ("제87조의2(벌칙) ① … 2. 제27조제1항 또는 제5항을 위반한 죄\n"
         "② … 2. 제12조제2항, 제27조제1항을 위반한 자는 5년 이하의 징역이나 5천만원 이하의 벌금")
P88 = "제88조(벌칙) … 제19조, 제27조제3항ㆍ제4항을 위반한 자는 3년 이하의 징역"
P89 = "제89조(벌칙) 제15조제1항, 제17조제1항을 위반한 자는 1년 이하의 징역"


def _hit(art, text, title="벌칙", law="의료법"):
    return {"id": art, "text": text, "score": 0.6,
            "metadata": {"law_name": law, "article_number": art, "article_title": title}}


ACT = _hit("제27조", ART27, title="무면허 의료행위 등 금지")


# ── 항 분해 ──
def test_paragraph_blocks_numbering():
    nums = [n for n, _ in _paragraph_blocks(ART27)]
    assert nums == [1, 2, 3, 5]          # ④ 없는 조문도 마크 기준으로 센다


def test_paragraph_blocks_none_when_no_marks():
    assert _paragraph_blocks("제87조(벌칙) 단항 조문이다.") == []


# ── 벌칙 조문이 열거하는 항 ──
def test_listed_single_paragraph():
    assert _listed_paragraphs(P90, "제27조") == (True, {2})


def test_listed_multiple_paragraphs():
    assert _listed_paragraphs(P87_2, "제27조") == (True, {1, 5})


def test_listed_enumerated_with_separator():
    assert _listed_paragraphs(P88, "제27조") == (True, {3, 4})


def test_listed_article_not_mentioned():
    assert _listed_paragraphs(P89, "제27조") == (False, set())


def test_listed_article_without_paragraph():
    """조만 열거하면 항 집합은 비고, 그래서 항으로 가릴 수 없다."""
    assert _listed_paragraphs("제88조(벌칙) 제19조를 위반한 자", "제19조") == (True, set())


def test_listed_respects_article_boundary():
    """제27조가 제27조의2 참조에 오매칭되지 않는다."""
    assert _listed_paragraphs("제100조(벌칙) 제27조의2제1항을 위반한 자", "제27조") == (False, set())


# ── 대상 항 판별 ──
def _fake_embed(vecs):
    """텍스트 → 고정 벡터. 질의는 ①⑤와 가깝고 ②와 멀게 만든다."""
    def _e(t):
        for key, v in vecs.items():
            if key in t:
                return v
        return [0.0, 0.0, 1.0]
    return _e


def test_target_paragraphs_picks_semantically_close():
    vecs = {"무면허 의료행위의 벌칙": [1.0, 0.0, 0.0],
            "의료인이 아니면 누구든지": [1.0, 0.0, 0.0],      # ①
            "명칭이나 이와 비슷한": [0.0, 1.0, 0.0],           # ②
            "소개ㆍ알선": [0.0, 1.0, 0.0],                     # ③
            "의료인이 아닌 자에게": [1.0, 0.0, 0.0]}           # ⑤
    with patch("client.rag.embed_text", side_effect=_fake_embed(vecs)):
        out = _target_paragraphs("무면허 의료행위의 벌칙은?", ART27, {1, 2, 3, 5})
    assert out == {1, 5}


def test_target_paragraphs_none_when_single_candidate():
    assert _target_paragraphs("q", ART27, {2}) is None


def test_target_paragraphs_none_on_embed_failure():
    with patch("client.rag.embed_text", side_effect=RuntimeError("down")):
        assert _target_paragraphs("q", ART27, {1, 2}) is None


# ── 필터 본체 ──
@pytest.fixture
def _vecs():
    return {"무면허 의료행위의 벌칙": [1.0, 0.0, 0.0],
            "의료인이 아니면 누구든지": [1.0, 0.0, 0.0],
            "명칭이나 이와 비슷한": [0.0, 1.0, 0.0],
            "소개ㆍ알선": [0.0, 1.0, 0.0],
            "의료인이 아닌 자에게": [1.0, 0.0, 0.0]}


def test_drops_penalty_for_other_paragraph(_vecs):
    """제90조는 제27조제2항(명칭)만 처벌 → 무면허 질의 근거에서 제외."""
    hits = [_hit("제90조", P90), _hit("제87조의2", P87_2), ACT]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    arts = [(h["metadata"] or {}).get("article_number") for h in out]
    assert "제90조" not in arts
    assert "제87조의2" in arts and "제27조" in arts


def test_drops_multiple_mismatches(_vecs):
    hits = [_hit("제90조", P90), _hit("제88조", P88), _hit("제87조의2", P87_2), ACT]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    arts = [(h["metadata"] or {}).get("article_number") for h in out]
    assert arts == ["제87조의2", "제27조"]


# ── 🔑 안전 불변식: 양성 매칭이 없으면 아무것도 버리지 않는다 ──
def test_keeps_everything_when_no_positive_match(_vecs):
    """대상 항을 처벌하는 벌칙이 하나도 없으면 근거를 건드리지 않는다.
    (근거 소실은 오귀속보다 나쁘다 — 근거가 비는 경우가 원리적으로 없어야 한다.)"""
    hits = [_hit("제90조", P90), _hit("제88조", P88), ACT]   # ②·③④뿐, ①⑤ 없음
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    assert out == hits


def test_never_empties_penalty_evidence(_vecs):
    """버린 뒤에도 벌칙 근거가 반드시 하나 이상 남는다."""
    hits = [_hit("제90조", P90), _hit("제88조", P88), _hit("제87조의2", P87_2), ACT]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    pens = [h for h in out
            if "벌칙" in str((h["metadata"] or {}).get("article_title") or "")]
    assert len(pens) >= 1


# ── 미적용 조건(fail-open) ──
def test_no_act_article_no_filter(_vecs):
    hits = [_hit("제90조", P90), _hit("제87조의2", P87_2)]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        assert _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?") == hits


def test_article_only_reference_is_untouched(_vecs):
    """조만 열거하는 벌칙(항 정보 없음)은 가릴 수 없으므로 유지."""
    only_art = _hit("제88조의2", "제88조의2(벌칙) 제27조를 위반한 자는 …")
    hits = [only_art, _hit("제87조의2", P87_2), ACT]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    assert only_art in out


def test_unrelated_penalty_untouched(_vecs):
    """행위 조문을 아예 열거하지 않는 벌칙은 **이 필터**의 대상이 아니다.
    조 단위 무관 판정은 `_drop_unrelated_penalty`가 따로 맡는다(관심사 분리)."""
    hits = [_hit("제89조", P89), _hit("제90조", P90), _hit("제87조의2", P87_2), ACT]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    arts = [(h["metadata"] or {}).get("article_number") for h in out]
    assert "제89조" in arts and "제90조" not in arts


def test_other_law_penalty_not_used_as_act(_vecs):
    """행위 조문 판정은 의료법 조문만 본다(타법 노이즈가 기준이 되면 안 된다)."""
    hits = [_hit("제93조", "약사법 …", law="약사법"),
            _hit("제90조", P90), _hit("제87조의2", P87_2), ACT]
    with patch("client.rag.embed_text", side_effect=_fake_embed(_vecs)):
        out = _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?")
    arts = [(h["metadata"] or {}).get("article_number") for h in out]
    assert "제93조" in arts and "제90조" not in arts


def test_config_toggle_off(_vecs):
    hits = [_hit("제90조", P90), _hit("제87조의2", P87_2), ACT]
    with patch("routes.medical_law.load_config",
               return_value={"medlaw_paragraph_filter": False}):
        assert _drop_paragraph_mismatch(hits, "무면허 의료행위의 벌칙은?") == hits


def test_empty_hits():
    assert _drop_paragraph_mismatch([], "q") == []


# ── 조 단위: 어느 행위 조문도 처벌하지 않는 벌칙 제외 (v2.124.94) ──
# 실측(41문항·벌칙 청크 28): top-1 행위조문만 보면 미열거 **12**, 전 행위조문 기준 **5**.
# 7건은 "다른 행위 조문은 열거하는데 top-1만 안 하는" 경우 → 전 행위조문을 봐야 한다.
from routes.medical_law import _drop_unrelated_penalty  # noqa: E402

ART19 = "제19조(정보 누설 금지) ① 의료인이나 의료기관 종사자는 … 누설하여서는 아니 된다."
ART32 = "제32조(감독) 보건복지부장관은 … 필요한 지도와 명령을 할 수 있다."
P88_19 = "제88조(벌칙) … 제19조, 제21조제2항을 위반한 자는 3년 이하의 징역"


def _act(art, text, title="의무"):
    return _hit(art, text, title=title)


def test_drops_penalty_that_lists_no_act_article():
    hits = [_act("제19조", ART19), _hit("제88조", P88_19), _hit("제89조", P89)]
    out = _drop_unrelated_penalty(hits)
    arts = [(h["metadata"] or {}).get("article_number") for h in out]
    assert "제89조" not in arts and "제88조" in arts and "제19조" in arts


def test_considers_every_act_article_not_just_the_first():
    """top-1(제32조)은 아무 벌칙도 열거되지 않지만 제19조를 처벌하는 제88조는 남아야 한다."""
    hits = [_act("제32조", ART32), _act("제19조", ART19),
            _hit("제88조", P88_19), _hit("제89조", P89)]
    out = _drop_unrelated_penalty(hits)
    arts = [(h["metadata"] or {}).get("article_number") for h in out]
    assert "제88조" in arts and "제89조" not in arts


# ── 🔑 안전 불변식 ──
def test_keeps_all_when_nothing_would_remain():
    """실측 사례 — "의료인의 결격사유는?"은 벌칙이 제66조 하나뿐이고 그것이 제8조를
    열거하지 않는다. 이 가드가 없으면 벌칙 근거가 통째로 사라진다."""
    hits = [_act("제8조", "제8조(결격사유 등) 다음 각 호의 어느 하나에 해당하는 자는 …"),
            _hit("제66조", "제66조(자격정지 등) … 제22조제1항 …", title="자격정지 등"),
            _hit("제65조", "제65조(면허 취소) … 제8조 각 호 …", title="면허 취소")]
    # 벌칙류는 제66조 하나(제65조 표제는 벌칙류 아님) → 후보 2개 미만이라 무개입
    assert _drop_unrelated_penalty(hits) == hits


def test_keeps_all_when_every_penalty_is_unrelated():
    hits = [_act("제8조", "제8조(결격사유 등) …"),
            _hit("제66조", "제66조(자격정지 등) … 제22조제1항 …", title="자격정지 등"),
            _hit("제89조", P89)]
    assert _drop_unrelated_penalty(hits) == hits


def test_ordinance_act_article_causes_no_op():
    """행위 조문이 시행규칙이면 법률 벌칙은 원리적으로 그 조번호를 열거하지 않는다
    → 열거하는 벌칙 0 → 불변식이 자동으로 무개입을 만든다(별도 분기 불요)."""
    hits = [_act("제2조의2", "제2조의2(명찰의 표시 내용 등) ① 법 제4조제5항 본문에 따라 …"),
            _hit("제92조", "제92조(과태료) … 제4조제5항 …", title="과태료"),
            _hit("제89조", P89)]
    assert _drop_unrelated_penalty(hits) == hits


def test_no_act_article_no_op():
    """행위 조문이 없으면 기준 자체가 없으므로 무개입."""
    hits = [_hit("제88조", P88_19), _hit("제89조", P89)]
    assert _drop_unrelated_penalty(hits) == hits


def test_single_penalty_no_op():
    hits = [_act("제19조", ART19), _hit("제89조", P89)]
    assert _drop_unrelated_penalty(hits) == hits


def test_other_law_penalty_is_not_judged():
    """타법 벌칙은 의료법 조번호 체계가 아니므로 이 필터의 대상이 아니다."""
    pharm = _hit("제93조", "제93조(벌칙) 약사법 …", law="약사법")
    hits = [_act("제19조", ART19), pharm, _hit("제88조", P88_19), _hit("제89조", P89)]
    out = _drop_unrelated_penalty(hits)
    assert pharm in out


def test_unrelated_config_toggle_off():
    hits = [_act("제19조", ART19), _hit("제88조", P88_19), _hit("제89조", P89)]
    with patch("routes.medical_law.load_config",
               return_value={"medlaw_unrelated_penalty_filter": False}):
        assert _drop_unrelated_penalty(hits) == hits


def test_unrelated_empty_hits():
    assert _drop_unrelated_penalty([]) == []
