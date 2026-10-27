"""core.retrieval_eval 테스트 (judge 모킹 — 실 LLM/GPU 불요).

⚠️설계 이력: 10청크 일괄 번호 판정은 라이브서 정답 청크까지 전량 False 오판
(2026-07-07) → **청크별 개별 boolean 판정**으로 재설계. 판정 불능=None(분모 제외).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import retrieval_eval as R  # noqa: E402


def test_relevance_per_chunk_calls():
    calls = []

    def _ask(system, prompt, schema, model=None):
        calls.append(prompt)
        assert "[문서 조각]" in prompt and "목록" not in prompt   # 개별 호출(일괄 아님)
        # ⚠️ 마커는 청크 본문에만 존재해야 함(지시문에 '관련'이 포함돼 오매칭 교훈)
        return {"relevant": "GOOD" in prompt}
    out = R.judge_chunk_relevance("보존기간?", ["GOOD 청크", "BAD 청크"], ask_json=_ask)
    assert out == [True, False] and len(calls) == 2


def test_relevance_error_and_nonbool_yield_none():
    def _ask(system, prompt, schema, model=None):
        if "실패" in prompt:
            raise RuntimeError("down")
        if "비정형" in prompt:
            return {"relevant": "yes"}                            # bool 아님 → None
        return {"relevant": True}
    out = R.judge_chunk_relevance("q", ["정상", "실패 청크", "비정형 청크"], ask_json=_ask)
    assert out == [True, None, None]


def test_relevance_empty_and_cap():
    assert R.judge_chunk_relevance("q", [], ask_json=lambda *a, **k: {}) == []
    n = {"c": 0}

    def _ask(*a, **k):
        n["c"] += 1
        return {"relevant": True}
    R.judge_chunk_relevance("q", ["c"] * 20, ask_json=_ask)
    assert n["c"] == R.MAX_CHUNKS                                 # 상한


def test_relevance_prompt_carries_direction_note():
    """방향 무관 지시는 **유저 프롬프트**에 있어야 함 — 시스템 프롬프트 지시만으로는
    '~할 수 있는 경우는?' 문형에서 미반영(07-27 id355 실측: 제15조 5/5 False,
    유저 병기 후 5/5 True·디코이 False 유지). 지시문 누락 회귀 가드."""
    prompts = []

    def _ask(system, prompt, schema, model=None):
        prompts.append(prompt)
        return {"relevant": True}
    R.judge_chunk_relevance("거부할 수 있는 경우는?", ["청크"], ask_json=_ask)
    assert R._REL_DIRECTION_NOTE in prompts[0]


def test_sufficiency_prompt_carries_direction_note():
    """sufficiency도 동일 편향(원칙 금지 조문 단독 근거를 no로 오판, 07-27 실측
    no 3/3 → 병기 후 partial 3/3) — 병기 지시 회귀 가드."""
    prompts = []

    def _ask(system, prompt, schema, model=None):
        prompts.append(prompt)
        return {"sufficiency": "partial"}
    R.judge_sufficiency("거부할 수 있는 경우는?", "제15조 …", ask_json=_ask)
    assert "판단 기준을 제공하면 partial" in prompts[0]


def test_sufficiency_mapping():
    for val, expect in (("yes", 1.0), ("partial", 0.5), ("no", 0.0), ("이상값", None)):
        out = R.judge_sufficiency("q", "ev", ask_json=lambda *a, **k: {"sufficiency": val})
        assert out == expect


def test_aggregate_excludes_unjudged():
    a = R.aggregate([True, True, False, None], 0.5)
    assert a["ctx_chunks"] == 4 and a["ctx_relevant"] == 2
    assert a["ctx_precision"] == round(2 / 3, 3)                  # None 분모 제외
    assert R.aggregate([None, None], 1.0)["ctx_precision"] is None
    assert R.aggregate([], None)["ctx_precision"] is None


def test_score_end_to_end_mocked():
    def _ask(system, prompt, schema, model=None):
        if "sufficiency" in str(schema):
            return {"sufficiency": "yes"}
        return {"relevant": "GOOD" in prompt}
    r = R.score("보존기간?", ["GOOD 청크", "BAD 청크"], ask_json=_ask)
    assert r["ctx_precision"] == 0.5 and r["ctx_sufficiency"] == 1.0
    assert r["chunks"][0]["relevant"] is True and r["chunks"][1]["relevant"] is False
    assert len(r["chunks"][0]["excerpt"]) <= 200
