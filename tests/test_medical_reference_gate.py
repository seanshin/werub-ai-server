"""근거 관련성 게이트 — 무관 입력 오인용 방지(트윈 request4 S2).

hybrid_search의 score는 가중 RRF(순위 융합)라 유사도가 아니다 → 벡터 코사인으로 게이트.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.medical import _drop_weak_matches  # noqa: E402


def _hit(vs, ks=0.0, i="d"):
    return {"id": i, "text": "t", "vector_score": vs, "keyword_score": ks}


def test_drops_weak_vector_only_matches():
    """무의미 입력 클래스(코사인 0.35~0.39·키워드 매칭 없음)는 제거."""
    out = _drop_weak_matches([_hit(0.386), _hit(0.346)], min_sim=0.45)
    assert out == []


def test_keeps_clinical_matches():
    """임상 질의 클래스(0.57~0.69)는 전부 유지."""
    hits = [_hit(0.691), _hit(0.570), _hit(0.639)]
    assert len(_drop_weak_matches(hits, min_sim=0.45)) == 3


def test_keeps_exact_keyword_match_despite_low_vector():
    """용어가 문서에 실제 존재(FTS 매칭)하면 벡터 점수가 낮아도 유지 — 근거 소실 방지."""
    out = _drop_weak_matches([_hit(0.0, ks=0.7)], min_sim=0.45)
    assert len(out) == 1


def test_threshold_zero_disables_gate():
    hits = [_hit(0.1), _hit(0.2)]
    assert len(_drop_weak_matches(hits, min_sim=0)) == 2


def test_empty_and_none_safe():
    assert _drop_weak_matches(None, min_sim=0.45) == []
    assert _drop_weak_matches([], min_sim=0.45) == []
