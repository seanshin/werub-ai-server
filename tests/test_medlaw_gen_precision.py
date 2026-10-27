"""생성 정밀도 규칙 8·9 회귀 (eval id240·id242 폐루프, v2.124.9).

id240: 무면허 의료행위(제27조제1항) 벌칙을 제90조(제27조제2항 위반=500만원 벌금)로
오귀속 — 벌칙 조문의 위반 조항 교차참조를 항 단위로 정합 확인하는 규칙 8.
id242: 제35조 부속 의료기관(원칙=신고·단서로 병원급만 허가)의 단서를 섹션 제목
"개설 허가"로 승격해 본문과 모순 — 제목·라벨 정합 규칙 9.
judge가 id240을 supported로 오판(위양성)했고 MiniCheck이 정확히 플래그 —
불일치=검토 신호 규율의 역방향 실증."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical_law import (  # noqa: E402
    MEDICAL_LAW_SYSTEM, _ask_assemble, _is_penalty_chunk, _penalty_xref_note,
)

_PENALTY_TEXT = (
    "제90조(벌칙) 제16조제1항ㆍ제2항, 제27조제2항, 제33조제1항을 위반한 자는 "
    "500만원 이하의 벌금에 처한다."
)
_NORMAL_TEXT = "제15조(진료거부 금지 등) ① 의료인 또는 의료기관 개설자는 정당한 사유 없이 거부하지 못한다."


def test_rule8_penalty_article_paragraph_match():
    """규칙 8 상주 — 벌칙 인용은 위반 조·항 번호 정확 일치 시에만."""
    assert "조·항·호 번호가 질의 행위의 근거 조항과 정확히 일치" in MEDICAL_LAW_SYSTEM
    assert "같은 조라도 항이 다르면" in MEDICAL_LAW_SYSTEM


def test_rule9_heading_body_consistency():
    """규칙 9 상주 — 단서의 예외를 제목·구분 라벨로 승격 금지."""
    assert "제목·소제목·요약 라벨도 본문 내용과 정합" in MEDICAL_LAW_SYSTEM
    assert "제목·구분 라벨로 승격하지 마세요" in MEDICAL_LAW_SYSTEM


def test_general_rules_retained():
    """번호 밀기 후에도 일반 규칙(확인 필요·한국어·면책) 상주."""
    assert "확인이 필요합니다" in MEDICAL_LAW_SYSTEM
    assert "반드시 한국어로 답변하세요" in MEDICAL_LAW_SYSTEM
    assert "법적 조언이 아닙니다" in MEDICAL_LAW_SYSTEM


# ── 07-20 배치 폐루프 (id250·253·257) — 규칙 2 개정·규칙 4 확장 ──

def test_rule2_penalty_only_from_evidence():
    """규칙 2 — 근거에 없는 벌칙·형량·금액 창작 금지(id250: 5천만원 창작)."""
    assert "근거에 벌칙 조문이 있으면 반드시 포함" in MEDICAL_LAW_SYSTEM
    assert "형량·벌금액을 기억으로 창작하지 말고" in MEDICAL_LAW_SYSTEM


def test_rule4_and_condition_and_inverse_summary():
    """규칙 4 확장 — AND 결합 단서 분리 금지(id257)·반대 방향 총괄 요약 금지(id253)."""
    assert "결합 그대로 옮기고" in MEDICAL_LAW_SYSTEM
    assert "반대 방향 총괄 서술" in MEDICAL_LAW_SYSTEM
    assert "요건 없이 허용" in MEDICAL_LAW_SYSTEM


def test_ask_assemble_truncation_marker():
    """캡 초과 청크엔 절단 마커, 캡 이내 청크엔 없음(무표시 절단=기억 완성 유발 봉합)."""
    def _fake_db():
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        return conn

    from routes.medical_law import _chunk_cap
    cap = _chunk_cap()
    long_text = "제21조(기록 열람 등) ① " + ("가" * (cap + 500))
    hits = [
        {"id": "long", "score": 0.7, "text": long_text,
         "metadata": {"origin": "law3dan", "law_name": "의료법", "law_level": "법률",
                      "article_number": "제21조", "article_title": "기록 열람 등"}},
        {"id": "short", "score": 0.5, "text": _NORMAL_TEXT,
         "metadata": {"origin": "law3dan", "law_name": "의료법", "law_level": "법률",
                      "article_number": "제15조", "article_title": "진료거부 금지 등"}},
    ]
    with patch("routes.medical_law._get_db", return_value=_fake_db()), \
         patch("routes.medical_law._rag_law_search", return_value=hits):
        ctx = _ask_assemble({"question": "진료기록 열람 요건은?"})["context"]
    assert ctx.count("근거 절단") == 1          # 긴 청크 1개만
    assert "기억으로 보완하지 말 것" in ctx


# ── 벌칙 교차참조 주석 (규칙 8의 대조 재료 — 결정적 추출) ──

def test_is_penalty_chunk_by_title_and_head():
    assert _is_penalty_chunk(_PENALTY_TEXT)
    assert _is_penalty_chunk("아무 본문", article_title="벌칙")
    assert _is_penalty_chunk("제92조(과태료) ① …")
    assert not _is_penalty_chunk(_NORMAL_TEXT)


def test_xref_note_extracts_paragraph_level_refs():
    note = _penalty_xref_note(_PENALTY_TEXT)
    assert "제16조제1항" in note and "제27조제2항" in note and "제33조제1항" in note
    assert "항 번호까지 대조" in note
    # 자기 조번호(제90조)는 제외
    assert "제90조" not in note


def test_xref_note_own_article_uij_and_empty():
    # 제87조의2 자체 언급 제외 + 참조 0건이면 빈 문자열
    note = _penalty_xref_note("제87조의2(벌칙) ① 제27조제1항 또는 제5항을 위반한 죄")
    assert "제27조제1항" in note and "제87조의2" not in note
    assert _penalty_xref_note("제88조(벌칙) 위반한 자는 벌금에 처한다.") == ""


def test_xref_note_title_only_on_bare_article_tokens():
    """표제 병기는 조-only 토큰만 — 항 붙은 토큰에 조 표제를 달면 항 구분이 뭉개져
    잘못된 매핑을 정당화(실측: 제27조제2항에 '무면허…' 표제 → 제90조 오귀속 유도)."""
    note = _penalty_xref_note(
        "제87조의2(벌칙) ① 제27조제1항 또는 제26조를 위반한 죄",
        title_map={"제27조": "무면허 의료행위 등 금지", "제26조": "변사체 신고"})
    assert "제27조제1항" in note
    assert "제27조제1항(무면허" not in note          # 항 토큰=번호만
    assert "제26조(변사체 신고)" in note              # 조-only 토큰=표제 병기


def test_ask_assemble_appends_note_for_penalty_chunk():
    """벌칙류 청크 뒤에 교차참조 주석 합류, 일반 조문은 무주석."""
    def _fake_db():
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        return conn

    hits = [
        {"id": "p90", "score": 0.67, "text": _PENALTY_TEXT,
         "metadata": {"origin": "law3dan", "law_name": "의료법", "law_level": "법률",
                      "article_number": "제90조", "article_title": "벌칙"}},
        {"id": "l15", "score": 0.5, "text": _NORMAL_TEXT,
         "metadata": {"origin": "law3dan", "law_name": "의료법", "law_level": "법률",
                      "article_number": "제15조", "article_title": "진료거부 금지 등"}},
    ]
    with patch("routes.medical_law._get_db", return_value=_fake_db()), \
         patch("routes.medical_law._rag_law_search", return_value=hits), \
         patch("routes.medical_law._article_title_map",
               return_value={"제27조": "무면허 의료행위 등 금지"}):
        ctx = _ask_assemble({"question": "무면허 의료행위의 벌칙은?"})["context"]
    assert "위 벌칙 조문이 나열하는 위반 조항" in ctx
    assert "제27조제2항" in ctx
    assert "제27조제2항(무면허" not in ctx  # 항 토큰엔 표제 미병기
    # 주석은 벌칙 청크 1회만(일반 조문 뒤엔 없음)
    assert ctx.count("위 벌칙 조문이 나열하는 위반 조항") == 1


# ── 행위 조문 보강 (_act_context_chunks — 벌칙 역참조의 대칭 방향) ──

import contextlib


@contextlib.contextmanager
def _patched_act(hits, cfg=None):
    with patch("client.rag.search", return_value=hits), \
         patch("routes.medical_law.load_config", return_value=cfg or {}):
        yield


def test_act_context_requires_penalty_intent():
    from routes.medical_law import _act_context_chunks
    with _patched_act([]):
        assert _act_context_chunks("진료기록 보존기간은?", set()) == []


def test_act_context_merges_non_penalty_chunks_only():
    from routes.medical_law import _act_context_chunks
    hits = [
        {"id": "a27", "text": _NORMAL_TEXT.replace("제15조(진료거부 금지 등)",
                                                   "제27조(무면허 의료행위 등 금지)"),
         "metadata": {"article_title": "무면허 의료행위 등 금지"}},
        {"id": "p90", "text": _PENALTY_TEXT, "metadata": {"article_title": "벌칙"}},
        {"id": "dup", "text": _NORMAL_TEXT, "metadata": {"article_title": "진료거부 금지 등"}},
    ]
    with _patched_act(hits):
        out = _act_context_chunks("무면허 의료행위의 벌칙은?", {"dup"})
    ids = [h["id"] for h in out]
    assert ids == ["a27"]                      # 벌칙류 제외·기존 id 제외
    assert all(h.get("act_ctx") for h in out)


def test_act_context_config_toggle_off():
    from routes.medical_law import _act_context_chunks
    with _patched_act([{"id": "x", "text": _NORMAL_TEXT, "metadata": {}}],
                      cfg={"medlaw_act_context": False}):
        assert _act_context_chunks("무면허 의료행위의 벌칙은?", set()) == []
