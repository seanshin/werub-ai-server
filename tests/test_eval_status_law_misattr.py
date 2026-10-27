"""/api/eval/status 법령명 오귀속 집계 (v2.125.9) — "0건"과 "미측정"을 가른다.

v2.125.8이 `law_misattributed`를 detail JSON에만 영속해 집계·모니터 어디에도 안 보였다
(배선이 반만 — 같은 릴리즈가 겪은 클래스). 여기서 고정하는 전제:
  - detail 키 부재(구 배치) → None (미측정)
  - detail.law_misattributed = null(무효 처리 배치·08-18 22/40 위양성) → None
  - []                                            → 0건으로 센다
  - ['국민건강보험법']                            → 1건
결측을 0으로 세면 축이 깨끗해 보인다(→ feedback_aggregate_metrics_dont_check_names).
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.activity import _LM_TREND_COLS, _law_misattr  # noqa: E402


def test_helper_distinguishes_missing_null_empty_flagged():
    assert _law_misattr(None) is None
    assert _law_misattr("") is None
    assert _law_misattr(json.dumps({"claims": []})) is None            # 구 배치: 키 없음
    assert _law_misattr(json.dumps({"law_misattributed": None})) is None  # 무효 처리
    assert _law_misattr(json.dumps({"law_misattributed": []})) == []
    assert _law_misattr(json.dumps({"law_misattributed": ["국민건강보험법"]})) == ["국민건강보험법"]
    assert _law_misattr("{broken") is None


def _db_with_batches():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE faithfulness_results(ts TEXT, source TEXT, detail TEXT)")
    rows = []
    # 구 배치: 키 없음
    rows += [("2026-08-01", "medical_law", json.dumps({"claims": []}))] * 3
    # 무효 배치(08-18): null 처리
    rows += [("2026-08-18", "medical_law", json.dumps({"law_misattributed": None}))] * 3
    # 유효 배치: 2/3 플래그
    rows += [("2026-08-19", "medical_law", json.dumps({"law_misattributed": ["의료인법"]})),
             ("2026-08-19", "medical_law", json.dumps({"law_misattributed": ["국민건강보험법"]})),
             ("2026-08-19", "medical_law", json.dumps({"law_misattributed": []}))]
    # A/B 배치는 추세에서 제외돼야 한다
    rows += [("2026-08-19b", "ab-x", json.dumps({"law_misattributed": ["x"]}))]
    db.executemany("INSERT INTO faithfulness_results VALUES(?,?,?)", rows)
    return db


def test_trend_columns_none_for_unmeasured_and_invalid_batches():
    db = _db_with_batches()
    rows = db.execute("SELECT ts, %s FROM faithfulness_results WHERE source NOT LIKE 'ab-%%'"
                      " GROUP BY ts ORDER BY ts" % _LM_TREND_COLS).fetchall()
    got = {ts: (int(lm) if lm_n else None) for ts, lm_n, lm in rows}
    assert got == {"2026-08-01": None, "2026-08-18": None, "2026-08-19": 2}


def test_latest_batch_aggregate_counts_flagged_questions():
    db = _db_with_batches()
    dets = [r[0] for r in db.execute(
        "SELECT detail FROM faithfulness_results WHERE ts='2026-08-19'")]
    lm = [_law_misattr(d) for d in dets]
    agg = sum(1 for v in lm if v) if any(v is not None for v in lm) else None
    assert agg == 2
    dets = [r[0] for r in db.execute(
        "SELECT detail FROM faithfulness_results WHERE ts='2026-08-18'")]
    lm = [_law_misattr(d) for d in dets]
    assert (sum(1 for v in lm if v) if any(v is not None for v in lm) else None) is None


def test_trend_breaks_constant_has_evidence_format_break():
    """v2.125.11 — 근거 라벨 도입(08-19)은 고정 단절점으로 API에 실려야 한다(화면이 그걸 적는다)."""
    from routes.activity import TREND_BREAKS
    kinds = {b["kind"] for b in TREND_BREAKS}
    assert "evidence_format" in kinds and "retrieval_space" in kinds and "verifier" in kinds
    assert all(b["ts"] and b["note"] for b in TREND_BREAKS)
    assert TREND_BREAKS == sorted(TREND_BREAKS, key=lambda b: b["ts"])
