#!/usr/bin/env python3
"""Grounded A/B (확대판 N=40) — qwen2.5:14b(상주) vs qwen3.6:35b-a3b(하이브리드).

개선점(N=5 → N=40 견고화):
- 질의 = core 5 + pool 35 전체(config/eval_questions.json).
- num_predict 2048 → 잘림 제거(N=5서 qwen3.6 3건 1024 절단이 불리하게 작용한 캐비엇 봉합).
- done_reason 추적 → truncated 플래그(집계와 분리 관측).
- 체크포인트: 생성 answers를 디스크 저장(judge 중 크래시해도 재생성 방지).
- 증분 저장: 문항별 judge 결과 즉시 append(중단 내성).
스왑 최소화: qwen2.5 전질의 생성 → qwen3.6 전질의 생성(1스왑) → judge 일괄(1스왑복원).
결과 → /tmp/qwen36_ab_full_result.json · 답변체크포인트 /tmp/qwen36_ab_full_answers.json
"""
import json, os, sys, time
import requests

sys.path.insert(0, "/home/deploy/WeRUBLLMManager")
sys.path.insert(0, "/home/deploy/WeRUBLLMManager/scripts")
from core import faithfulness as F
from core import llm_judge as J
import eval_faithfulness as EF

try:
    from routes.medical_law import MEDICAL_LAW_SYSTEM
except Exception as e:
    print("MEDICAL_LAW_SYSTEM import 실패, 폴백:", e, flush=True)
    MEDICAL_LAW_SYSTEM = "당신은 한국 의료법 전문 AI입니다. 제공된 근거 자료에 기반해서만 답하고, 근거 밖 사항은 추론하지 마세요."

OLLAMA = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
BASELINE = "qwen2.5:14b"
CANDIDATE = "qwen3.6:35b-a3b"
ANS_CKPT = "/tmp/qwen36_ab_full_answers.json"
RESULT = "/tmp/qwen36_ab_full_result.json"

_cfg = json.load(open("/home/deploy/WeRUBLLMManager/config/eval_questions.json"))
QUESTIONS = list(dict.fromkeys((_cfg.get("core") or []) + (_cfg.get("pool") or [])))
print("질의 수: %d (core %d + pool %d)" % (len(QUESTIONS), len(_cfg.get("core", [])), len(_cfg.get("pool", []))), flush=True)


def gen(model, q, ev, timeout=1200):
    prompt = ("[근거 자료]\n%s\n\n[질문]\n%s\n\n위 근거 자료에 기반하여 한국어로 정확히 답하세요." % (ev, q))
    t0 = time.time()
    r = requests.post(OLLAMA + "/api/generate", json={
        "model": model, "system": MEDICAL_LAW_SYSTEM, "prompt": prompt,
        "stream": False, "think": False,
        "options": {"num_ctx": 8192, "temperature": 0.3, "num_predict": 2048},
    }, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    return (d.get("response") or ""), round(time.time() - t0, 1), d.get("done_reason")


def ollama_ps():
    try:
        return requests.get(OLLAMA + "/api/ps", timeout=10).json().get("models", [])
    except Exception:
        return []


def main():
    evid = {}
    for q in QUESTIONS:
        evid[q] = EF.get_evidence(q)
    print("근거 수집 완료 (평균 %d자)" % (sum(len(v) for v in evid.values()) // len(evid)), flush=True)

    # --- 체크포인트 재사용(있으면 생성 스킵) ---
    answers = {BASELINE: {}, CANDIDATE: {}}
    gen_sec = {BASELINE: {}, CANDIDATE: {}}
    done_reason = {BASELINE: {}, CANDIDATE: {}}
    resource = {}
    if os.path.exists(ANS_CKPT):
        try:
            ck = json.load(open(ANS_CKPT))
            answers = ck["answers"]; gen_sec = ck["gen_sec"]; done_reason = ck.get("done_reason", {BASELINE: {}, CANDIDATE: {}})
            resource = ck.get("resource", {})
            print("체크포인트 로드: baseline %d · candidate %d 생성완료" % (len(answers[BASELINE]), len(answers[CANDIDATE])), flush=True)
        except Exception as e:
            print("체크포인트 로드 실패, 새로 생성:", e, flush=True)

    def save_ckpt():
        json.dump({"answers": answers, "gen_sec": gen_sec, "done_reason": done_reason, "resource": resource},
                  open(ANS_CKPT, "w"), ensure_ascii=False)

    # Phase 1 — baseline
    print("\n=== Phase 1: %s 생성 ===" % BASELINE, flush=True)
    for q in QUESTIONS:
        if q in answers[BASELINE]:
            continue
        a, s, dr = gen(BASELINE, q, evid[q])
        answers[BASELINE][q] = a; gen_sec[BASELINE][q] = s; done_reason[BASELINE][q] = dr
        save_ckpt()
        print("  %.1fs %d자 dr=%s · %s" % (s, len(a), dr, q[:26]), flush=True)

    # Phase 2 — candidate (하이브리드)
    print("\n=== Phase 2: %s 생성(하이브리드) ===" % CANDIDATE, flush=True)
    for i, q in enumerate(QUESTIONS):
        if q in answers[CANDIDATE]:
            continue
        a, s, dr = gen(CANDIDATE, q, evid[q])
        answers[CANDIDATE][q] = a; gen_sec[CANDIDATE][q] = s; done_reason[CANDIDATE][q] = dr
        if not resource.get("candidate_ps"):
            resource["candidate_ps"] = ollama_ps()
        save_ckpt()
        print("  %.1fs %d자 dr=%s · %s" % (s, len(a), dr, q[:26]), flush=True)

    # Phase 3 — judge 일괄 (크래시 방어 + 답변 길이 캡: 장문→추출 JSON 잘림→JSONDecodeError 봉합)
    JUDGE_ANS_CAP = 3500  # 충실도 판정용 답변 상한(양 모델 동일 적용; qwen2.5는 대개 <800자라 무영향)
    print("\n=== Phase 3: judge(%s) — 방어+캡%d ===" % (F.JUDGE_MODEL, JUDGE_ANS_CAP), flush=True)
    out = {"baseline": BASELINE, "candidate": CANDIDATE, "n": len(QUESTIONS), "per_q": [],
           "resource": resource, "judge_ans_cap": JUDGE_ANS_CAP}
    agg = {BASELINE: {"faith": [], "halluc": 0, "qual": [], "sec": [], "trunc": 0, "parse_fail": 0},
           CANDIDATE: {"faith": [], "halluc": 0, "qual": [], "sec": [], "trunc": 0, "parse_fail": 0}}
    for qi, q in enumerate(QUESTIONS):
        row = {"q": q}
        for m in (BASELINE, CANDIDATE):
            ans_full, ev = answers[m][q], evid[q]
            ans = ans_full[:JUDGE_ANS_CAP]
            try:
                fr = F.score(ans, ev)
            except Exception as e:
                agg[m]["parse_fail"] += 1
                print("    ⚠ F.score 실패(%s) m=%s Q=%s" % (str(e)[:60], m, q[:20]), flush=True)
                fr = {"faithfulness": None, "hallucination": False,
                      "n_claims": 0, "n_supported": 0, "n_unsupported": 0, "n_contradicted": 0}
            try:
                jr = J.judge(q, ans, ev)
                overall = jr.get("overall") if isinstance(jr, dict) else None
            except Exception as e:
                print("    ⚠ J.judge 실패(%s) m=%s Q=%s" % (str(e)[:60], m, q[:20]), flush=True)
                overall = None
            trunc = (done_reason[m].get(q) == "length")
            row[m] = {"faith": fr["faithfulness"], "halluc": bool(fr["hallucination"]),
                      "claims": [fr["n_claims"], fr["n_supported"], fr["n_unsupported"], fr["n_contradicted"]],
                      "quality": overall, "gen_sec": gen_sec[m][q], "truncated": trunc,
                      "answer": ans[:400]}
            if fr["faithfulness"] is not None:
                agg[m]["faith"].append(fr["faithfulness"])
            agg[m]["halluc"] += int(bool(fr["hallucination"]))
            if overall is not None:
                agg[m]["qual"].append(overall)
            agg[m]["sec"].append(gen_sec[m][q])
            agg[m]["trunc"] += int(trunc)
        out["per_q"].append(row)
        # 증분 저장
        json.dump(out, open(RESULT, "w"), ensure_ascii=False, indent=1)
        print("  [%d/%d] %s | b faith=%.3f q=%s tr=%s || c faith=%.3f q=%s tr=%s"
              % (qi + 1, len(QUESTIONS), q[:20],
                 row[BASELINE]["faith"] or 0, row[BASELINE]["quality"], row[BASELINE]["truncated"],
                 row[CANDIDATE]["faith"] or 0, row[CANDIDATE]["quality"], row[CANDIDATE]["truncated"]), flush=True)

    def avg(x):
        return round(sum(x) / len(x), 3) if x else None
    summary = {}
    for m in (BASELINE, CANDIDATE):
        a = agg[m]
        summary[m] = {"faith_avg": avg(a["faith"]), "faith_n": len(a["faith"]),
                      "halluc": "%d/%d" % (a["halluc"], len(QUESTIONS)),
                      "quality_avg": avg(a["qual"]), "gen_sec_avg": avg(a["sec"]),
                      "truncated": "%d/%d" % (a["trunc"], len(QUESTIONS)),
                      "parse_fail": "%d/%d" % (a["parse_fail"], len(QUESTIONS))}
    # 문항별 승패(충실도)
    wins = {BASELINE: 0, CANDIDATE: 0, "tie": 0}
    for r in out["per_q"]:
        fb, fc = r[BASELINE]["faith"], r[CANDIDATE]["faith"]
        if fb is None or fc is None:
            continue
        if abs(fb - fc) < 1e-6:
            wins["tie"] += 1
        elif fb > fc:
            wins[BASELINE] += 1
        else:
            wins[CANDIDATE] += 1
    out["summary"] = summary
    out["faith_wins"] = wins
    json.dump(out, open(RESULT, "w"), ensure_ascii=False, indent=1)
    print("\n===== 요약(N=%d) =====" % len(QUESTIONS), flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("문항별 충실도 승패:", wins, flush=True)
    print("결과:", RESULT, flush=True)


if __name__ == "__main__":
    main()
