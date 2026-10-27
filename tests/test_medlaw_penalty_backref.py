"""벌칙 역참조 보강 회귀 (eval id199 진료기록부 거짓 작성 처벌 폐루프, v2.124.8).

벌칙·과태료·자격정지 조문은 조번호 교차참조 목록이라 자연어 질의와 임베딩
유사도가 낮아 top-K 진입 실패 → 처벌 의도 질의에서 검색된 의무 조문을 역참조하는
벌칙류 조문을 결정적으로 근거에 합류(_penalty_backref_chunks)."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import (  # noqa: E402
    _BACKREF_CACHE,
    _penalty_backref_chunks,
    _rag_law_search,
)


def _legal_hit(id_, law, art, score=0.6, text="본문"):
    return {"id": id_, "score": score, "text": text,
            "metadata": {"origin": "law3dan", "law_name": law,
                         "article_number": art}}


def _penalty_chunk(id_, art_title, text):
    return {"id": id_, "text": text, "score": None, "backref": True,
            "metadata": {"origin": "law3dan", "law_name": "의료법",
                         "article_number": "제88조", "article_title": art_title}}


P88 = _penalty_chunk("p88", "벌칙",
                     "제88조(벌칙) 다음 각 호 … 제19조, 제21조제2항, 제22조제3항, "
                     "제27조제3항 … 3년 이하의 징역이나 3천만원 이하의 벌금")
P92 = _penalty_chunk("p92", "과태료", "제92조(과태료) … 제16조제3항 …")
P66 = _penalty_chunk("p66", "자격정지 등",
                     "제66조(자격정지 등) … 진료기록부등을 거짓으로 작성 … 제22조제3항")


def setup_function(_fn):
    _BACKREF_CACHE.clear()


# ── _penalty_backref_chunks 매칭 규칙 ──
def test_backref_finds_penalty_article():
    """id199 실증: 제22조 검색 + 처벌 질의 → 제22조제3항 역참조 벌칙(제88조) 합류."""
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88, P92]):
        out = _penalty_backref_chunks(
            "진료기록부를 거짓으로 작성하면 어떤 처벌을 받는가?", legal)
    assert [h["id"] for h in out] == ["p88"]  # 제92조는 제22조 미참조 → 제외


def test_backref_requires_penalty_intent():
    """처벌 의도 없는 질의는 역참조 자체가 없음(기존 동작 불변)."""
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88]) as mock_fetch:
        out = _penalty_backref_chunks("진료기록부 기재 사항은?", legal)
    assert out == []
    mock_fetch.assert_not_called()


def test_backref_article_boundary():
    """제22조 질의가 '제22조의2'만 참조하는 조문에 오매칭되지 않음."""
    only_ui2 = _penalty_chunk("pX", "벌칙", "제100조(벌칙) … 제22조의2를 위반한 자 …")
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[only_ui2, P88]):
        out = _penalty_backref_chunks("거짓 작성 벌칙은?", legal)
    assert [h["id"] for h in out] == ["p88"]


def test_backref_cap_and_rank_order():
    """cap=2 상한 + 검색 랭크 상위 조문의 역참조 우선."""
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88, P66, _penalty_chunk(
                   "p3", "벌칙", "제89조(벌칙) 제22조제1항 위반 …")]):
        out = _penalty_backref_chunks("거짓 작성 시 처벌은?", legal)
    assert [h["id"] for h in out] == ["p88", "p66"]  # cap=2


def test_backref_dedups_already_retrieved():
    """이미 검색된 벌칙 조문은 재합류하지 않음."""
    legal = [_legal_hit("l22", "의료법", "제22조"),
             {**P88, "score": 0.5}]  # 벌칙이 이미 검색 hit에 존재
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88]):
        out = _penalty_backref_chunks("거짓 작성 처벌은?", legal)
    assert out == []


def test_backref_config_disable():
    """config `medlaw_penalty_backref: false`면 무동작."""
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law.load_config",
               return_value={"medlaw_penalty_backref": False}), \
         patch("routes.medical_law._penalty_article_chunks", return_value=[P88]):
        assert _penalty_backref_chunks("거짓 작성 처벌은?", legal) == []


def test_backref_fetch_failure_degrades():
    """벌칙 조문 조회 실패는 빈 리스트로 강등(예외 전파 0)."""
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law._penalty_article_chunks",
               side_effect=RuntimeError("chroma down")):
        assert _penalty_backref_chunks("거짓 작성 처벌은?", legal) == []


def test_backref_skips_hits_without_article():
    """조번호 없는 hit(고시 등)은 건너뜀."""
    legal = [{"id": "kn1", "score": 0.5, "text": "고시",
              "metadata": {"origin": "khidi_notice"}}]
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88]) as mock_fetch:
        assert _penalty_backref_chunks("처벌은?", legal) == []
    mock_fetch.assert_not_called()


# ── _rag_law_search 통합(테일 병합·floor 불변·dedup) ──
def _mk(id_, score, text, law="의료법", art=None):
    md = {"origin": "law3dan", "law_name": law}
    if art:
        md["article_number"] = art
    return {"id": id_, "score": score, "text": text, "metadata": md}


def test_search_backref_joins_tail_keeps_floor():
    """벌칙 역참조 hit이 테일 슬롯에 합류하고 floor(법령 상위 2)는 불변."""
    hits = [_mk("l14", 0.63, "제14조(기재 사항) …", art="제14조"),
            _mk("l22", 0.62, "제22조(진료기록부 등) … 거짓으로 작성 금지", art="제22조"),
            _mk("l21", 0.61, "제21조(기록 열람) …", art="제21조"),
            _mk("l15", 0.60, "제15조(보존) …", art="제15조"),
            _mk("l30", 0.59, "제30조 …", art="제30조")]

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        if collection_name == "medical_pub_knowledge":
            return []
        return list(hits)

    with patch("client.rag.search", side_effect=fake_search), \
         patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88]), \
         patch("routes.medical_law._act_context_chunks", return_value=[]):
        out = _rag_law_search("진료기록부를 거짓으로 작성하면 어떤 처벌을 받는가?",
                              top_k=5)
    ids = [h["id"] for h in out]
    assert ids[:2] == ["l14", "l22"]   # floor 불변
    assert "p88" in ids                 # 역참조 합류
    assert len(out) == 5                # top_k 유지(행위 조문 보강은 별도 테스트)


def test_search_dedups_identical_chunk_texts():
    """동일 원문 중복(3단비교 이중 색인)이 top-K 슬롯을 이중 점유하지 않음."""
    dup_text = "제14조(진료기록부 등의 기재 사항) …"
    hits = [_mk("d1", 0.63, dup_text, art="제14조"),
            _mk("d2", 0.63, dup_text, art="제14조"),
            _mk("l22", 0.62, "제22조 …", art="제22조")]

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        if collection_name == "medical_pub_knowledge":
            return []
        return list(hits)

    with patch("client.rag.search", side_effect=fake_search):
        out = _rag_law_search("진료기록부 기재 사항은?", top_k=5)
    ids = [h["id"] for h in out]
    assert "d2" not in ids and ids[:2] == ["d1", "l22"]


# ── eval 근거 수집 정합(생성/판정 근거 비대칭 방지) ──
def test_eval_evidence_applies_backref():
    """eval get_evidence_chunks도 /ask와 동일하게 벌칙 역참조 적용."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "eval_faithfulness", os.path.join(root, "scripts", "eval_faithfulness.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    l22 = _mk("l22", 0.62, "제22조(진료기록부 등) …", art="제22조")

    def fake_search(query, n_results=5, collection_name=None, min_score=0.4, where=None):
        if collection_name == "medical_law_knowledge":
            return [l22]
        return []

    with patch("client.rag.search", side_effect=fake_search), \
         patch("routes.medical_law._penalty_article_chunks",
               return_value=[P88]):
        chunks = mod.get_evidence_chunks(
            "진료기록부를 거짓으로 작성하면 어떤 처벌을 받는가?")
    assert any("제88조" in c for c in chunks)


# ── 씨앗 제한: 벌칙 → 벌칙 확산 차단 (v2.124.94) ──
# 08-10 실측: "무면허 의료행위의 벌칙은?"(의료법 제27조)에 **약사법 제93조(벌칙)**가
# 검색 노이즈로 딸려 오자 그것을 씨앗으로 **약사법 제95조**까지 합류했고, 답변이 그
# 형량을 의료법 제90조의 것인 양 썼다("제90조 … 1년 이하의 징역 또는 1천만원 이하의
# 벌금" = 약사법 제95조의 형량). 조번호는 법령을 가로질러 충돌한다.
def _pharm_penalty(id_, art, text):
    return {"id": id_, "text": text, "score": None, "backref": True,
            "metadata": {"origin": "law3dan", "law_name": "약사법",
                         "article_number": art, "article_title": "벌칙"}}


def test_backref_seed_skips_penalty_articles():
    """검색된 **벌칙** 조문은 씨앗이 아니다 — 같은 법의 다른 벌칙으로 번지는 경로 차단."""
    seed = {"id": "ph93", "score": 0.6, "text": "제93조(벌칙) …",
            "metadata": {"origin": "law3dan", "law_name": "약사법",
                         "article_number": "제93조", "article_title": "벌칙"}}
    p95 = _pharm_penalty("ph95", "제95조", "제95조(벌칙) … 제93조 … 1천만원 이하의 벌금")
    with patch("routes.medical_law._penalty_article_chunks",
               return_value=[p95]) as mock_fetch:
        out = _penalty_backref_chunks("무면허 의료행위의 벌칙은?", [seed])
    assert out == []
    mock_fetch.assert_not_called()      # 조회 자체를 하지 않는다


def test_backref_seed_skips_all_penalty_titles():
    for title in ("벌칙", "과태료", "자격정지 등"):
        seed = {"id": "s", "score": 0.6, "text": "…",
                "metadata": {"law_name": "의료법", "article_number": "제88조",
                             "article_title": title}}
        with patch("routes.medical_law._penalty_article_chunks", return_value=[P88]):
            assert _penalty_backref_chunks("벌칙은?", [seed]) == [], title


def test_backref_still_seeds_from_act_article():
    """통제군 — 행위 조문 씨앗은 그대로 동작해야 한다(완화가 아니라 좁히기)."""
    legal = [_legal_hit("l22", "의료법", "제22조")]
    with patch("routes.medical_law._penalty_article_chunks", return_value=[P88, P92]):
        out = _penalty_backref_chunks("거짓 작성 벌칙은?", legal)
    assert [h["id"] for h in out] == ["p88"]


def test_backref_seed_untitled_article_still_allowed():
    """표제 메타가 없는 조문(발간물 등)은 벌칙으로 단정하지 않는다(fail-open)."""
    legal = [{"id": "l22", "score": 0.6, "text": "…",
              "metadata": {"law_name": "의료법", "article_number": "제22조"}}]
    with patch("routes.medical_law._penalty_article_chunks", return_value=[P88]):
        out = _penalty_backref_chunks("거짓 작성 벌칙은?", legal)
    assert [h["id"] for h in out] == ["p88"]


def test_backref_mixed_hits_seed_only_from_act_article():
    """벌칙 조문이 상위에 있어도 아래의 행위 조문에서 정상 확장된다."""
    hits = [
        {"id": "p90", "score": 0.7, "text": "제90조(벌칙) …",
         "metadata": {"law_name": "의료법", "article_number": "제90조",
                      "article_title": "벌칙"}},
        _legal_hit("l22", "의료법", "제22조", score=0.5),
    ]
    with patch("routes.medical_law._penalty_article_chunks", return_value=[P88]):
        out = _penalty_backref_chunks("거짓 작성 벌칙은?", hits)
    assert [h["id"] for h in out] == ["p88"]
