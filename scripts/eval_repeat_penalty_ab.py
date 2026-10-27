#!/usr/bin/env python3
"""repeat_penalty 1.1 vs 1.0 — 같은 모델·같은 근거·같은 seed, 샘플링 파라미터만 다른 grounded A/B (v2.125.10).

왜: Ollama 0.32.10이 `repeat_penalty` 기본값을 1.1→1.0으로 바꿨다. 우리 33/41 모델은 Modelfile에 값이 없어
엔진 기본값에 의존한다(v2.125.9 §5). 엔진을 올리기 **전에** 값 자체가 어느 쪽이 나은지 **현행 엔진에서** 잰다.
1.1은 한국어 조사·어미 반복까지 벌하는 값이라 1.0이 나을 수도, 반복이 늘 수도 있다 — 추측하지 않는다.

📌 사전 등록(실행 전 고정 · N=40 · qwen2.5:14b · seed 고정 · 양 팔 모두 값을 **명시**해 대칭):
  주 지표  ① 충실도 평균 Δ(1.0−1.1) · 부호검정 p   ② 환각 플래그 Δ   ③ 반복 점수(detect_repetition.score, 높을수록 반복 적음) Δ
  보조    ④ 품질 overall Δ · 지연 · 절단
  판정   - 1.0 채택(=엔진 기본 변화를 따라감·핀 값 1.0):  ①Δ ≥ −0.02 이고 ② Δ ≤ +2 이고 ③ Δ ≥ −3
         - 1.1 유지(=핀 값 1.1):                         위 하나라도 어긋남 **또는 판정 불가**
         - 노이즈 바닥 참고: 같은 답변 재채점 뒤집힘 4/40 양방향(v2.125.8 §7-4)·생성 노이즈는 그보다 크다 → 평균 차가
           작으면 "같다"이지 "낫다"가 아니다. **기본 행동은 현상 유지(1.1)**.
결과 → logs/repeat_penalty_ab_result.json · 체크포인트 logs/repeat_penalty_ab_answers.json (중단 내성)
"""
import json
import math
import os
import sys
import time

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from core import faithfulness as F          # noqa: E402
from core import llm_judge as J             # noqa: E402
from core.korean_humanize import detect_repetition  # noqa: E402
import eval_faithfulness as EF              # noqa: E402

try:
    from routes.medical_law import MEDICAL_LAW_SYSTEM
except Exception as e:  # noqa: BLE001
    print("MEDICAL_LAW_SYSTEM import 실패, 폴백:", e, flush=True)
    MEDICAL_LAW_SYSTEM = "당신은 한국 의료법 전문 AI입니다. 제공된 근거 자료에 기반해서만 답하고, 근거 밖 사항은 추론하지 마세요."

OLLAMA = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
MODEL = os.environ.get("AB_MODEL", "qwen2.5:14b")
ARMS = {"rp1.1": 1.1, "rp1.0": 1.0}
BASE, CAND = "rp1.1", "rp1.0"
SEED = 42
ANS_CKPT = os.path.join(_ROOT, "logs", "repeat_penalty_ab_answers.json")
RESULT = os.path.join(_ROOT, "logs", "repeat_penalty_ab_result.json")
JUDGE_ANS_CAP = 3500

_cfg = json.load(open(os.path.join(_ROOT, "config", "eval_questions.json")))
QUESTIONS = list(dict.fromkeys((_cfg.get("core") or []) + (_cfg.get("pool") or [])))


def gen(rp, q, ev, timeout=900):
    prompt = ("[근거 자료]\n%s\n\n[질문]\n%s\n\n위 근거 자료에 기반하여 한국어로 정확히 답하세요." % (ev, q))
    t0 = time.time()
    r = requests.post(OLLAMA + "/api/generate", json={
        "model": MODEL, "system": MEDICAL_LAW_SYSTEM, "prompt": prompt, "stream": False, "think": False,
        "options": {"num_ctx": 8192, "temperature": 0.3, "num_predict": 2048, "seed": SEED,
                    "repeat_penalty": rp},
    }, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    return (d.get("response") or ""), round(time.time() - t0, 1), d.get("done_reason")


def sign_test_p(wins_a, wins_b):
    """양측 부호검정(동률 제외) — 정확 이항."""
    n = wins_a + wins_b
    if n == 0:
        return 1.0
    k = min(wins_a, wins_b)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n) * 2
    return round(min(1.0, p), 3)


def main():
    print("모델 %s · N=%d · seed=%d · 팔 %s" % (MODEL, len(QUESTIONS), SEED, ARMS), flush=True)
    evid = {q: EF.get_evidence(q) for q in QUESTIONS}
    print("근거 수집 완료 (평균 %d자)" % (sum(len(v) for v in evid.values()) // len(evid)), flush=True)

    answers = {a: {} for a in ARMS}; gen_sec = {a: {} for a in ARMS}; done_reason = {a: {} for a in ARMS}
    if os.path.exists(ANS_CKPT):
        ck = json.load(open(ANS_CKPT))
        answers, gen_sec, done_reason = ck["answers"], ck["gen_sec"], ck["done_reason"]
        print("체크포인트: %s" % {a: len(answers[a]) for a in ARMS}, flush=True)

    def save():
        json.dump({"answers": answers, "gen_sec": gen_sec, "done_reason": done_reason},
                  open(ANS_CKPT, "w"), ensure_ascii=False)

    for arm, rp in ARMS.items():
        print("\n=== 생성 %s (repeat_penalty=%s) ===" % (arm, rp), flush=True)
        for q in QUESTIONS:
            if q in answers[arm]:
                continue
            a, s, dr = gen(rp, q, evid[q])
            answers[arm][q] = a; gen_sec[arm][q] = s; done_reason[arm][q] = dr
            save()
            print("  %.1fs %d자 dr=%s · %s" % (s, len(a), dr, q[:26]), flush=True)

    print("\n=== judge(%s) ===" % F.JUDGE_MODEL, flush=True)
    out = {"model": MODEL, "arms": ARMS, "seed": SEED, "n": len(QUESTIONS), "per_q": []}
    agg = {a: {"faith": [], "halluc": 0, "qual": [], "rep": [], "sec": [], "trunc": 0, "identical": 0}
           for a in ARMS}
    for qi, q in enumerate(QUESTIONS):
        row = {"q": q, "identical": answers[BASE][q] == answers[CAND][q]}
        for arm in ARMS:
            ans = answers[arm][q][:JUDGE_ANS_CAP]
            try:
                fr = F.score(ans, evid[q])
            except Exception as e:  # noqa: BLE001
                print("    ⚠ F.score 실패(%s) %s Q=%s" % (str(e)[:60], arm, q[:20]), flush=True)
                fr = {"faithfulness": None, "hallucination": False}
            try:
                jr = J.judge(q, ans, evid[q]); overall = jr.get("overall") if isinstance(jr, dict) else None
            except Exception as e:  # noqa: BLE001
                print("    ⚠ J.judge 실패(%s) %s" % (str(e)[:60], arm), flush=True); overall = None
            rep = detect_repetition(answers[arm][q]).get("score")
            trunc = done_reason[arm].get(q) == "length"
            row[arm] = {"faith": fr["faithfulness"], "halluc": bool(fr["hallucination"]), "quality": overall,
                        "repetition": rep, "gen_sec": gen_sec[arm][q], "truncated": trunc,
                        "chars": len(answers[arm][q]), "answer": answers[arm][q][:400]}
            if fr["faithfulness"] is not None: agg[arm]["faith"].append(fr["faithfulness"])
            agg[arm]["halluc"] += int(bool(fr["hallucination"]))
            if overall is not None: agg[arm]["qual"].append(overall)
            if rep is not None: agg[arm]["rep"].append(rep)
            agg[arm]["sec"].append(gen_sec[arm][q]); agg[arm]["trunc"] += int(trunc)
        out["per_q"].append(row)
        json.dump(out, open(RESULT, "w"), ensure_ascii=False, indent=1)
        print("  [%d/%d] %s | 1.1 f=%.3f q=%s rep=%s || 1.0 f=%.3f q=%s rep=%s %s" % (
            qi + 1, len(QUESTIONS), q[:18], row[BASE]["faith"] or 0, row[BASE]["quality"], row[BASE]["repetition"],
            row[CAND]["faith"] or 0, row[CAND]["quality"], row[CAND]["repetition"],
            "(동일 답변)" if row["identical"] else ""), flush=True)

    avg = lambda x: round(sum(x) / len(x), 3) if x else None  # noqa: E731
    summ = {a: {"faith_avg": avg(g["faith"]), "halluc": g["halluc"], "quality_avg": avg(g["qual"]),
                "repetition_avg": avg(g["rep"]), "gen_sec_avg": avg(g["sec"]), "truncated": g["trunc"]}
            for a, g in agg.items()}
    wins = {"faith": [0, 0, 0], "rep": [0, 0, 0], "qual": [0, 0, 0]}   # [base, cand, tie]
    for r in out["per_q"]:
        for key, wk in (("faith", "faith"), ("repetition", "rep"), ("quality", "qual")):
            b, c = r[BASE][key], r[CAND][key]
            if b is None or c is None: continue
            if abs(b - c) < 1e-9: wins[wk][2] += 1
            elif b > c: wins[wk][0] += 1
            else: wins[wk][1] += 1
    identical = sum(1 for r in out["per_q"] if r["identical"])
    d_f = (summ[CAND]["faith_avg"] or 0) - (summ[BASE]["faith_avg"] or 0)
    d_h = summ[CAND]["halluc"] - summ[BASE]["halluc"]
    d_r = (summ[CAND]["repetition_avg"] or 0) - (summ[BASE]["repetition_avg"] or 0)
    verdict = "1.0 채택 가능" if (d_f >= -0.02 and d_h <= 2 and d_r >= -3) else "1.1 유지"
    out.update({"summary": summ, "wins_base_cand_tie": wins, "identical_answers": identical,
                "p_sign": {k: sign_test_p(v[0], v[1]) for k, v in wins.items()},
                "delta_cand_minus_base": {"faith": round(d_f, 4), "halluc": d_h, "repetition": round(d_r, 2)},
                "prereg_verdict": verdict})
    json.dump(out, open(RESULT, "w"), ensure_ascii=False, indent=1)
    print("\n===== 요약 (N=%d · 동일 답변 %d) =====" % (len(QUESTIONS), identical), flush=True)
    print(json.dumps(summ, ensure_ascii=False, indent=1), flush=True)
    print("승패[1.1,1.0,동률]:", wins, "p:", out["p_sign"], flush=True)
    print("Δ(1.0−1.1):", out["delta_cand_minus_base"], "→ 사전 등록 판정:", verdict, flush=True)
    print("결과:", RESULT, flush=True)


if __name__ == "__main__":
    main()
