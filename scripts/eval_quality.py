#!/usr/bin/env python3
"""답변 가치/품질 배치 측정 (LLM 평가 Phase 3 러너).

의료법 RAG 질의 → 답변 → ①LLM-judge 루브릭(정확성·도움됨·안전성·자연성, core.llm_judge)
②한국어 형식 메트릭(core.korean.korean_quality_check) → eval.db `quality_results`.
judge=상주 qwen2.5:14b(스왑 0)·외부 API 0. Phase 4 야간배치가 본 스크립트+eval_faithfulness를 호출.

사용법:
  scripts/eval_quality.py
  scripts/eval_quality.py --questions q.txt
"""
import argparse, os, sqlite3, sys, time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import llm_judge as J
from core.korean import korean_quality_check

API = os.environ.get("ADMIN_API", "http://127.0.0.1:8585")
EVAL_DB = os.environ.get("EVAL_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db"))

# v2.122.0: 질의는 config/eval_questions.json(core+pool 일자 회전) — faithfulness와 동일 세트
from core.eval_pool import load_pool, questions_for_day  # noqa: E402

DEFAULT_QUESTIONS = load_pool()["core"]


def init_db():
    c = sqlite3.connect(EVAL_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS quality_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, question TEXT,
        accuracy INTEGER, helpfulness INTEGER, safety INTEGER, fluency INTEGER,
        overall REAL, korean_ratio REAL, judge_model TEXT, sec REAL)""")
    # v2.120.0 체크리스트 컬럼 + v2.121.0 상세 영속(답변·차원 사유·체크 항목 JSON)
    for col, typ in (("checks_rate", "REAL"), ("checks_failed", "TEXT"),
                     ("answer", "TEXT"), ("detail", "TEXT")):
        try:
            c.execute(f"ALTER TABLE quality_results ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    c.commit()
    return c


# 근거 수집은 eval_faithfulness와 단일 소스 공유(시드 조문 포함 로직 중복 방지)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_faithfulness import get_evidence  # noqa: E402


def load_stored_answers(after_ts=""):
    """이전 배치의 저장 답변 {질문: 답변} — 답변을 고정한 재채점용.

    🔴 왜 필요한가(2026-08-05 실측): 이 러너는 매 실행마다 **답변을 새로 생성**한다.
    그래서 처치 전후를 비교하면 판정 변화와 **생성 변동이 섞인다**. 실제로 코드 변경 없이
    같은 40문항을 두 번 돌렸더니 `conditions_kept`이 30% ↔ 57%로 흔들렸고
    (답변 40/40 전부 상이), 그날 아침 보고한 "45%→30% 개선"이 근거를 잃었다.

    judge 자기일관성 R=5에서도 `conditions_kept`은 5문항 중 4개에서 뒤집힌다
    (temperature는 이미 0 — 런타임 비결정성, v2.124.20).

    ⇒ **처치 효과는 답변을 고정하고 재채점해서만** 읽을 수 있다.
    """
    c = sqlite3.connect(EVAL_DB, timeout=10)
    if after_ts:
        rows = c.execute("SELECT question, answer FROM quality_results "
                         "WHERE answer IS NOT NULL AND ts > ? ORDER BY id", (after_ts,)).fetchall()
    else:
        rows = c.execute("SELECT question, answer FROM quality_results "
                         "WHERE answer IS NOT NULL ORDER BY id").fetchall()
    # id 오름차순이라 같은 질문은 **가장 최근 답변**이 남는다
    return {q: a for q, a in rows if a}


def run(questions, stored=None):
    db = init_db()
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    overalls = []
    print("=== 답변 가치/품질 (judge=%s%s) ==="
          % (J.JUDGE_MODEL, " · 저장 답변 재채점" if stored else ""))
    for q in questions:
        t0 = time.time()
        if stored is not None:
            ans = stored.get(q) or ""
            if not ans:
                print("  스킵(저장 답변 없음): %s" % q)
                continue
        else:
            try:
                ans = requests.post(API + "/api/medical-law/ask",
                                    json={"question": q}, timeout=180).json().get("answer", "")
            except Exception as e:
                print("  스킵(%s): %s" % (q, e))
                continue
        if not ans:
            print("  스킵(빈 답변): %s" % q)
            continue
        r = J.judge(q, ans, evidence=get_evidence(q), checklist=True)
        _, ratio, _ = korean_quality_check(ans)
        sec = time.time() - t0
        s = r["scores"]

        def g(dim):
            return s.get(dim, {}).get("score")
        # 상세 영속 — 드릴다운 검토용(차원별 점수+사유+체크 12항목 개별 결과)
        import json as _json
        detail = _json.dumps({"scores": s}, ensure_ascii=False)
        db.execute("INSERT INTO quality_results(ts,source,question,accuracy,helpfulness,"
                   "safety,fluency,overall,korean_ratio,judge_model,sec,checks_rate,checks_failed,"
                   "answer,detail)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ts, "medical_law", q, g("accuracy"), g("helpfulness"), g("safety"),
                    g("fluency"), r["overall"], round(ratio, 3), r["judge_model"], round(sec, 1),
                    r.get("checks_rate"), ",".join(r.get("checks_failed") or []),
                    ans, detail))
        db.commit()
        if r["overall"]:
            overalls.append(r["overall"])
        print("  overall=%.2f acc=%s help=%s safe=%s flu=%s ko=%.2f checks=%s/%s%s %.0fs  Q=%s"
              % (r["overall"] or 0, g("accuracy"), g("helpfulness"), g("safety"),
                 g("fluency"), ratio, r.get("checks_passed"), r.get("checks_total"),
                 (" 실패:" + ",".join(r["checks_failed"])) if r.get("checks_failed") else "",
                 sec, q))
    if overalls:
        print("\n평균 overall=%.2f/5 → eval.db 저장" % (sum(overalls) / len(overalls)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="")
    ap.add_argument("--all", action="store_true", help="풀 전체(core+pool) 일괄 측정")
    ap.add_argument("--core-only", action="store_true", help="core 5문항만(회전 없이)")
    ap.add_argument("--reuse-answers", nargs="?", const="", default=None, metavar="AFTER_TS",
                    help="답변을 생성하지 않고 **저장된 답변을 재채점**한다(처치 전후 비교용). "
                         "값을 주면 그 ts 이후 배치의 답변만 사용")
    a = ap.parse_args()
    if a.questions:
        qs = [l.strip() for l in open(a.questions, encoding="utf-8") if l.strip()]
    elif a.all:
        p = load_pool()
        qs = p["core"] + p["pool"]
    elif a.core_only:
        qs = DEFAULT_QUESTIONS
    else:
        from datetime import date
        qs = questions_for_day(date.today().toordinal())
    stored = load_stored_answers(a.reuse_answers) if a.reuse_answers is not None else None
    if stored is not None and not stored:
        sys.exit("저장 답변이 없습니다 — 먼저 일반 실행으로 배치를 만드세요")
    run(qs, stored)
