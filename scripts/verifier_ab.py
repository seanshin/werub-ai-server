#!/usr/bin/env python3
"""교차검증자 이종화 A/B 하네스 — 사전 등록 `docs/01-plan/references/verifier-heterogeneity-ab-2026-09-28.md`.

모델별로 통제군 48쌍을 **연속 실행**(모델 교체 적재가 지연을 오염하지 않게)하고, 각 모델 첫
호출 1회는 워밍업으로 버린다. 판정 경로는 전 모델 동일 — `faithfulness._qwen_entail_check_ex`.

사용: ./venv/bin/python scripts/verifier_ab.py [--models qwen2.5:7b,granite4.2:8b,qwen2.5:14b]
       [--ref qwen2.5:14b] [--v0 qwen2.5:7b] [--v1 granite4.2:8b] [--out logs/verifier_ab.json]
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import faithfulness as F  # noqa: E402

HARD = {"proviso_reversal", "condition_drop", "scope_overgeneral"}


def run_model(model, pairs, evidence):
    # 워밍업(적재) — 지연 통계에서 제외
    try:
        F._qwen_entail_check_ex(evidence[pairs[0]["ev"]], pairs[0]["claim"], model=model)
    except Exception as e:  # noqa: BLE001
        print(f"[warmup] {model}: {type(e).__name__}: {e}")
    rows = []
    for p in pairs:
        t0 = time.perf_counter()
        try:
            v, prob = F._qwen_entail_check_ex(evidence[p["ev"]], p["claim"], model=model)
            err = None
        except Exception as e:  # noqa: BLE001 — 판독 불능은 오답이 아니라 결측으로 센다
            v, prob, err = None, None, f"{type(e).__name__}: {e}"
        ms = (time.perf_counter() - t0) * 1000
        rows.append({"id": p["id"], "type": p["type"], "label": p["label"], "verdict": v,
                     "prob": prob, "ms": round(ms, 1), "err": err})
    return rows


def summarize(rows):
    n = len(rows)
    answered = [r for r in rows if r["verdict"] is not None]
    correct = [r for r in rows if r["verdict"] is not None and r["verdict"] == r["label"]]
    hard = [r for r in rows if r["type"] in HARD]
    return {
        "n": n,
        "answered": len(answered),
        "accuracy": round(len(correct) / n, 4),           # 결측은 오답으로(보수적)
        "prob_readable": round(sum(r["prob"] is not None for r in rows) / n, 4),
        "latency_ms_median": round(statistics.median(r["ms"] for r in rows), 1),
        "hard_accuracy": round(sum(r["verdict"] == r["label"] for r in hard) / len(hard), 4),
        "wrong_ids": sorted(r["id"] for r in rows if r["verdict"] != r["label"]),
        "errors": [r for r in rows if r["err"]][:5],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="config/entail_verifier_control_set.json")
    ap.add_argument("--models", default="qwen2.5:7b,granite4.2:8b,qwen2.5:14b")
    ap.add_argument("--ref", default="qwen2.5:14b")
    ap.add_argument("--v0", default="qwen2.5:7b")
    ap.add_argument("--v1", default="granite4.2:8b")
    ap.add_argument("--out", default="logs/verifier_ab_20260928.json")
    a = ap.parse_args()

    data = json.load(open(a.set, encoding="utf-8"))
    pairs, evidence = data["pairs"], data["evidence"]
    res = {}
    for m in a.models.split(","):
        t0 = time.time()
        rows = run_model(m, pairs, evidence)
        res[m] = {"rows": rows, "summary": summarize(rows), "sec": round(time.time() - t0, 1)}
        s = res[m]["summary"]
        print(f"{m:18s} acc={s['accuracy']:.3f} hard={s['hard_accuracy']:.3f} "
              f"prob={s['prob_readable']:.2f} med={s['latency_ms_median']}ms "
              f"answered={s['answered']}/{s['n']} wrong={s['wrong_ids']}")

    s0, s1 = res[a.v0]["summary"], res[a.v1]["summary"]
    ref_wrong = set(res[a.ref]["summary"]["wrong_ids"]) if a.ref in res else set()
    rescue0 = len(ref_wrong - set(s0["wrong_ids"]))
    rescue1 = len(ref_wrong - set(s1["wrong_ids"]))
    verdict = {
        "A1": s1["accuracy"] >= 0.85 and s1["accuracy"] >= s0["accuracy"] - 0.04,
        "A2": s1["prob_readable"] >= 0.95,
        "A3": rescue1 >= rescue0,
        "A3_informative": len(ref_wrong) > 0,
        "A4": s1["latency_ms_median"] <= 2 * s0["latency_ms_median"],
        "A5": s1["hard_accuracy"] >= s0["hard_accuracy"],
        "ref_wrong": sorted(ref_wrong), "rescue_v0": rescue0, "rescue_v1": rescue1,
    }
    verdict["ADOPT"] = all(verdict[k] for k in ("A1", "A2", "A3", "A4", "A5"))
    print(json.dumps(verdict, ensure_ascii=False))
    res["_verdict"] = verdict
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("→", a.out)


if __name__ == "__main__":
    main()
