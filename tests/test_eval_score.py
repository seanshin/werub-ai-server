"""core.eval_score 테스트 — 산식 결정성·재정규화·등급·클램프."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.eval_score import (  # noqa: E402
    INDEX_VERSION, WEIGHTS, grade_of, index_from_batches, quality_index,
)


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_perfect_scores_100_grade_a():
    r = quality_index(faithfulness=1.0, halluc_free=1.0, strict=1.0,
                      overall=5.0, checks_rate=1.0, korean_ratio=1.0)
    assert r["score"] == 100.0 and r["grade"] == "A"
    assert r["version"] == INDEX_VERSION


def test_overall_normalization():
    # overall만 측정: 3/5 → (3-1)/4=0.5 → 50점(재정규화로 단독 100% 가중)
    r = quality_index(overall=3.0)
    assert r["score"] == 50.0 and r["weights_used"] == {"overall": WEIGHTS["overall"]}


def test_renormalization_missing_components():
    # faithfulness=1.0만 측정 → 100점 (0.35/0.35)
    assert quality_index(faithfulness=1.0)["score"] == 100.0
    # faith 1.0 + overall 1(최저=0) → 0.35/(0.35+0.25)*100 = 58.3
    r = quality_index(faithfulness=1.0, overall=1.0)
    assert r["score"] == round(0.35 / 0.60 * 100, 1)


def test_grades():
    assert grade_of(95) == "A" and grade_of(90) == "A"
    assert grade_of(85) == "B" and grade_of(75) == "C" and grade_of(50) == "D"


def test_clamp_and_empty():
    r = quality_index(faithfulness=1.7, halluc_free=-0.2)   # 이상값 클램프
    assert r["components"]["faithfulness"] == 1.0 and r["components"]["halluc_free"] == 0.0
    assert quality_index() is None                           # 전부 미측정


def test_index_from_batches_realistic():
    # 실측 유사값: faith 1.0·환각 0/10·strict 0.8 / overall 4.47·checks 0.8·ko 0.95
    r = index_from_batches(
        {"mean": 1.0, "hallucinations": 0, "n": 10, "strict": 0.8},
        {"overall": 4.47, "checks_rate": 0.8, "korean_ratio": 0.95})
    assert r is not None and 88 <= r["score"] <= 96          # 산식 sanity 대역
    assert set(r["components"]) == set(WEIGHTS)


def test_index_from_batches_faith_only_and_none():
    r = index_from_batches({"mean": 0.9, "hallucinations": 1, "n": 5}, None)
    assert set(r["components"]) == {"faithfulness", "halluc_free"}
    assert r["components"]["halluc_free"] == 0.8
    assert index_from_batches(None, None) is None


# --- judge 자기일관성 통계 (v2.124.0, scripts/eval_judge_consistency) ---------

def test_judge_consistency_stats():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    from eval_judge_consistency import consistency_stats

    def rep(acc_score, cond):
        return {"overall": (acc_score + 15) / 4,
                "scores": {"accuracy": {"score": acc_score,
                                        "checks": {"numbers_match": True, "conditions_kept": cond}},
                           "helpfulness": {"score": 5, "checks": {"direct_answer": True}},
                           "safety": {"score": 5, "checks": {}},
                           "fluency": {"score": 5, "checks": {}}}}
    # 2회 중 accuracy 4→5, conditions_kept True→False로 flip
    s = consistency_stats([rep(4, True), rep(5, False)])
    assert s["score_range_max"] == 1
    assert s["flipped_items"] == ["accuracy.conditions_kept"]
    assert s["checks_flip_rate"] == round(1 / 3, 3)      # 판정된 3항목 중 1 flip
    assert s["overall_std"] > 0
    # 완전 일치 → flip 0·std 0
    s2 = consistency_stats([rep(5, True), rep(5, True), rep(5, True)])
    assert s2["checks_flip_rate"] == 0.0 and s2["overall_std"] == 0.0 and s2["score_range_max"] == 0


def test_latest_index_from_db(tmp_path, monkeypatch):
    """eval.db 최신 배치 → 지수(+TTL 캐시)."""
    import sqlite3
    from core import eval_score as ES
    db = str(tmp_path / "eval.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE faithfulness_results(ts TEXT, faithfulness REAL, "
              "hallucination INTEGER, strict_faithfulness REAL)")
    c.execute("CREATE TABLE quality_results(ts TEXT, overall REAL, checks_rate REAL, "
              "korean_ratio REAL)")
    c.execute("INSERT INTO faithfulness_results VALUES ('t2', 1.0, 0, 0.8)")
    c.execute("INSERT INTO faithfulness_results VALUES ('t2', 0.9, 0, 0.9)")
    c.execute("INSERT INTO quality_results VALUES ('t2', 4.5, 0.8, 0.95)")
    c.commit(); c.close()
    monkeypatch.setitem(ES._LATEST_CACHE, "ts", 0.0)          # 캐시 무효화
    qi = ES.latest_index(db_path=db)
    assert qi and 85 <= qi["score"] <= 100 and qi["grade"] in ("A", "B")
    # TTL 캐시 — DB 지워도 캐시값 반환
    qi2 = ES.latest_index(db_path=str(tmp_path / "gone.db"))
    assert qi2 == qi
    # 캐시 만료 후 부재 DB → None
    monkeypatch.setitem(ES._LATEST_CACHE, "ts", 0.0)
    assert ES.latest_index(db_path=str(tmp_path / "gone.db")) is None
