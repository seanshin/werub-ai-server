"""저장 답변 재채점 모드 회귀 — 처치 효과는 답변을 고정해야 읽을 수 있다.

🔴 2026-08-05 실측: `eval_quality.py`는 매 실행마다 답변을 새로 생성한다.
코드 변경 없이 같은 40문항을 두 번 돌렸더니 답변이 **40/40 전부 상이**했고
`accuracy.conditions_kept`이 **30% ↔ 57%**로 흔들렸다. 그날 아침 보고한
"45%→30% 개선"은 이 변동폭 안에 들어가 근거를 잃었다.

judge 자기일관성 R=5에서도 같은 답변에 대해 `conditions_kept`이 5문항 중 4개에서
뒤집힌다(temperature는 이미 0 — 런타임 비결정성, v2.124.20).

⇒ 판정 변화만 보려면 **생성을 빼야 한다**. 이 모드가 그 통제군을 만든다.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "eval.db"
    monkeypatch.setenv("EVAL_DB", str(p))
    import importlib
    import eval_quality as EQ
    importlib.reload(EQ)
    EQ.init_db()                       # 실제 스키마로 만든다(축약 스키마는 INSERT에서 깨짐)
    return EQ, sqlite3.connect(str(p))


def test_loads_latest_answer_per_question(db):
    EQ, c = db
    c.execute("INSERT INTO quality_results(ts,question,answer) VALUES('t1','Q','옛 답변')")
    c.execute("INSERT INTO quality_results(ts,question,answer) VALUES('t2','Q','새 답변')")
    c.commit()
    assert EQ.load_stored_answers()["Q"] == "새 답변"


def test_after_ts_filter(db):
    EQ, c = db
    c.execute("INSERT INTO quality_results(ts,question,answer) VALUES('2026-08-05T01','Q','이전')")
    c.execute("INSERT INTO quality_results(ts,question,answer) VALUES('2026-08-05T03','R','이후')")
    c.commit()
    got = EQ.load_stored_answers("2026-08-05T02")
    assert got == {"R": "이후"}


def test_null_answers_skipped(db):
    EQ, c = db
    c.execute("INSERT INTO quality_results(ts,question,answer) VALUES('t','Q',NULL)")
    c.execute("INSERT INTO quality_results(ts,question,answer) VALUES('t','R','')")
    c.commit()
    assert EQ.load_stored_answers() == {}


def test_run_uses_stored_answer_and_never_generates(db, monkeypatch):
    """재채점 모드는 `/ask`를 호출하지 않는다 — 호출하면 통제가 깨진다."""
    EQ, c = db

    def boom(*a, **kw):
        raise AssertionError("재채점 모드에서 답변을 생성하면 안 된다")
    monkeypatch.setattr(EQ.requests, "post", boom)
    monkeypatch.setattr(EQ, "get_evidence", lambda q: "근거")
    monkeypatch.setattr(EQ.J, "judge", lambda *a, **kw: {
        "scores": {}, "overall": 4.0, "judge_model": "m",
        "checks_rate": 1.0, "checks_failed": [], "checks_passed": 12, "checks_total": 12})
    monkeypatch.setattr(EQ, "korean_quality_check", lambda t: (t, 0.9, {}))
    seen = {}
    monkeypatch.setattr(EQ.J, "judge", lambda q, ans, **kw: seen.setdefault("ans", ans) and None or {
        "scores": {}, "overall": 4.0, "judge_model": "m",
        "checks_rate": 1.0, "checks_failed": [], "checks_passed": 12, "checks_total": 12})
    EQ.run(["Q"], stored={"Q": "고정된 답변"})
    assert seen["ans"] == "고정된 답변"


def test_missing_stored_answer_is_skipped(db, monkeypatch):
    EQ, c = db
    monkeypatch.setattr(EQ.requests, "post",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("생성 금지")))
    EQ.run(["없는질문"], stored={"다른질문": "답변"})     # 예외 없이 스킵
