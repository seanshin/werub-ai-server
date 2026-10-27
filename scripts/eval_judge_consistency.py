#!/usr/bin/env python3
"""judge 자기일관성 meta-eval — 같은 답변 반복 채점의 분산/flip rate (v2.124.0).

근거: LLM-judge의 절대 점수는 반복·시드에 불안정(Reliability without Validity 2026 —
kappa deflation 3~4×·IRT 진단 2026) + 우리 실증(conditions_kept 정의 미추종).
같은 (질문,답변)을 시드만 바꿔 R회 채점 → **점수 분산·체크 항목 flip rate** =
judge 신뢰도 지표(품질 지수의 신뢰 구간 해석 근거·K-HALU meta-eval의 로컬 보완).

측정: core 질문별 답변 1회 취득 → judge(checklist) R회(seed 1..R·저온) →
  - overall_std / score_range(차원별 최대-최소)
  - checks_flip_rate: R회 중 판정이 갈린 체크 항목 비율(항목별 다수결 대비)
저장: eval.db judge_consistency. 권장 주기=주 1회(cron 일요일 04:30, 야간 배치와 분리).

사용법:
  scripts/eval_judge_consistency.py            # core 5문항 × R=4
  scripts/eval_judge_consistency.py --runs 5 --questions q.txt
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import llm_judge as J  # noqa: E402
from core.eval_pool import load_pool  # noqa: E402

API = os.environ.get("ADMIN_API", "http://127.0.0.1:8585")
EVAL_DB = os.environ.get("EVAL_DB", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db"))
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def init_db():
    c = sqlite3.connect(EVAL_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS judge_consistency(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, question TEXT, runs INTEGER,
        overall_mean REAL, overall_std REAL, score_range_max INTEGER,
        checks_flip_rate REAL, flipped_items TEXT, judge_model TEXT, sec REAL)""")
    c.commit()
    return c


def _seeded_ask(seed: int):
    """시드 고정 judge 호출자 — 반복 간 변동을 시드로 유도(저온)."""
    def ask(system, prompt, schema, model=None, timeout=180):
        import requests
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json={
            "model": model or J.JUDGE_MODEL, "system": system, "prompt": prompt,
            "stream": False, "format": schema,
            "options": {"temperature": 0.3, "seed": seed, "num_predict": 1024},
        }, timeout=timeout)
        r.raise_for_status()
        return json.loads(r.json().get("response") or "{}")
    return ask


def consistency_stats(results: list) -> dict:
    """R회 judge 리포트 → 일관성 통계(순수 로직)."""
    overalls = [r["overall"] for r in results if r.get("overall") is not None]
    n = len(overalls)
    mean = sum(overalls) / n if n else None
    std = (sum((x - mean) ** 2 for x in overalls) / n) ** 0.5 if n else None
    # 차원별 점수 범위(최대-최소)의 최댓값
    rng = 0
    for dim in J.RUBRIC:
        vals = [r["scores"][dim]["score"] for r in results if dim in r.get("scores", {})]
        if len(vals) >= 2:
            rng = max(rng, max(vals) - min(vals))
    # 체크 항목 flip: R회 판정이 갈린 항목(전원일치 아님)
    flipped, total = [], 0
    for dim in J.RUBRIC:
        for cid, _ in J.CHECKLIST[dim]:
            vals = [r["scores"][dim].get("checks", {}).get(cid)
                    for r in results if dim in r.get("scores", {})]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                total += 1
                if len(set(vals)) > 1:
                    flipped.append(f"{dim}.{cid}")
    return {"overall_mean": round(mean, 2) if mean is not None else None,
            "overall_std": round(std, 3) if std is not None else None,
            "score_range_max": rng,
            "checks_flip_rate": round(len(flipped) / total, 3) if total else None,
            "flipped_items": flipped}


def run(questions, runs=4):
    import requests
    db = init_db()
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from eval_faithfulness import get_evidence
    flips, stds = [], []
    print("=== judge 자기일관성 (judge=%s, R=%d) ===" % (J.JUDGE_MODEL, runs))
    for q in questions:
        t0 = time.time()
        try:
            ans = requests.post(API + "/api/medical-law/ask",
                                json={"question": q}, timeout=180).json().get("answer", "")
            ev = get_evidence(q)
        except Exception as e:
            print("  스킵(%s): %s" % (q, e))
            continue
        if not ans:
            continue
        results = []
        for seed in range(1, runs + 1):
            try:
                results.append(J.judge(q, ans, evidence=ev,
                                       ask_json=_seeded_ask(seed), checklist=True))
            except Exception as e:  # noqa: BLE001
                print("  (run %d 실패: %s)" % (seed, str(e)[:60]))
        if len(results) < 2:
            print("  스킵(유효 run<2): %s" % q)
            continue
        s = consistency_stats(results)
        sec = time.time() - t0
        db.execute("INSERT INTO judge_consistency(ts,question,runs,overall_mean,overall_std,"
                   "score_range_max,checks_flip_rate,flipped_items,judge_model,sec)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (ts, q, len(results), s["overall_mean"], s["overall_std"],
                    s["score_range_max"], s["checks_flip_rate"],
                    ",".join(s["flipped_items"]), J.JUDGE_MODEL, round(sec, 1)))
        db.commit()
        if s["checks_flip_rate"] is not None:
            flips.append(s["checks_flip_rate"])
        if s["overall_std"] is not None:
            stds.append(s["overall_std"])
        print("  overall %.2f±%.3f range=%d flip=%.0f%%%s %.0fs  Q=%s"
              % (s["overall_mean"] or 0, s["overall_std"] or 0, s["score_range_max"],
                 (s["checks_flip_rate"] or 0) * 100,
                 (" [" + ",".join(s["flipped_items"][:4]) + "]") if s["flipped_items"] else "",
                 sec, q))
    if flips:
        print("\n평균 flip rate=%.1f%% · overall std=%.3f → eval.db 저장 "
              "(flip 높음=judge 불안정 항목=정의 개선 대상)"
              % (sum(flips) / len(flips) * 100, sum(stds) / len(stds)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--questions", default="")
    a = ap.parse_args()
    qs = ([l.strip() for l in open(a.questions, encoding="utf-8") if l.strip()]
          if a.questions else load_pool()["core"])
    run(qs, runs=max(2, min(a.runs, 8)))
