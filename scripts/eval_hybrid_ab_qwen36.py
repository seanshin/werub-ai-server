#!/usr/bin/env python3
"""Grounded A/B — qwen2.5:14b(상주) vs qwen3.6:35b-a3b(하이브리드).

Phase 0.5 방식 재현: 동일 의료법 RAG 근거(get_evidence)에 생성 모델만 교체,
qwen2.5:14b judge로 faithfulness(core.faithfulness)+quality(core.llm_judge) 채점.
스왑 최소화: (1) qwen2.5 전질의 생성 → (2) qwen3.6 전질의 생성(1스왑) → (3) judge 일괄(1스왑복원).
결과는 stdout + /tmp/qwen36_ab_result.json 저장(재생성 방지·읽기전용 폴링).
"""
import json, os, sys, time
import requests

sys.path.insert(0, "/home/deploy/WeRUBLLMManager")
from core import faithfulness as F
from core import llm_judge as J

try:
    from routes.medical_law import MEDICAL_LAW_SYSTEM
except Exception as e:
    print("MEDICAL_LAW_SYSTEM import 실패, 폴백:", e)
    MEDICAL_LAW_SYSTEM = "당신은 한국 의료법 전문 AI입니다. 제공된 근거 자료에 기반해서만 답하고, 근거 밖 사항은 추론하지 마세요."

sys.path.insert(0, "/home/deploy/WeRUBLLMManager/scripts")
import eval_faithfulness as EF  # get_evidence, get_evidence_chunks

OLLAMA = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
BASELINE = "qwen2.5:14b"
CANDIDATE = "qwen3.6:35b-a3b"

# core 5 질의 — config/eval_questions.json에서 로드(진행중 배치와 비교 연속성)
try:
    _cfg = json.load(open("/home/deploy/WeRUBLLMManager/config/eval_questions.json"))
    QUESTIONS = _cfg.get("core") or _cfg
    assert isinstance(QUESTIONS, list) and QUESTIONS
except Exception as _e:
    print("eval_questions 로드 실패, 폴백:", _e)
    QUESTIONS = ["진료기록부 보존기간은?", "의료광고 사전심의 의무는?",
                 "처방전 대리수령이 가능한가?",
                 "의료기관 개설자가 아닌 자의 의료기관 개설 시 벌칙은?",
                 "환자 진료기록 사본 발급 요청을 거부할 수 있는가?"]


def gen(model, q, ev, timeout=900):
    prompt = ("[근거 자료]\n%s\n\n[질문]\n%s\n\n위 근거 자료에 기반하여 한국어로 정확히 답하세요."
              % (ev, q))
    t0 = time.time()
    r = requests.post(OLLAMA + "/api/generate", json={
        "model": model, "system": MEDICAL_LAW_SYSTEM, "prompt": prompt,
        "stream": False, "think": False,  # qwen3.6=thinking 모델, off=공정·프로덕션 정합(빈응답 방지)
        "options": {"num_ctx": 8192, "temperature": 0.3, "num_predict": 1024},
    }, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("response") or ""), round(time.time() - t0, 1)


def ollama_ps():
    try:
        return requests.get(OLLAMA + "/api/ps", timeout=10).json().get("models", [])
    except Exception:
        return []


def main():
    # 근거 사전수집(1회, judge/생성 공통)
    evid = {}
    for q in QUESTIONS:
        evid[q] = EF.get_evidence(q)
        print("근거 %d자 · Q=%s" % (len(evid[q]), q[:30]))

    answers = {BASELINE: {}, CANDIDATE: {}}
    gen_sec = {BASELINE: {}, CANDIDATE: {}}
    resource = {}

    # Phase 1 — 상주 baseline 생성
    print("\n=== Phase 1: %s 생성(상주) ===" % BASELINE)
    for q in QUESTIONS:
        a, s = gen(BASELINE, q, evid[q])
        answers[BASELINE][q] = a
        gen_sec[BASELINE][q] = s
        print("  %.1fs · %d자 · %s" % (s, len(a), q[:30]))

    # Phase 2 — candidate 생성(하이브리드, 1스왑)
    print("\n=== Phase 2: %s 생성(하이브리드) ===" % CANDIDATE)
    for i, q in enumerate(QUESTIONS):
        a, s = gen(CANDIDATE, q, evid[q])
        answers[CANDIDATE][q] = a
        gen_sec[CANDIDATE][q] = s
        if i == 0:
            resource["candidate_ps"] = ollama_ps()  # 오프로드% 관측
        print("  %.1fs · %d자 · %s" % (s, len(a), q[:30]))

    # Phase 3 — judge 일괄(qwen2.5 복원)
    print("\n=== Phase 3: judge(%s) faithfulness+quality ===" % F.JUDGE_MODEL)
    out = {"baseline": BASELINE, "candidate": CANDIDATE, "per_q": [], "resource": resource}
    agg = {BASELINE: {"faith": [], "halluc": 0, "qual": [], "sec": []},
           CANDIDATE: {"faith": [], "halluc": 0, "qual": [], "sec": []}}
    for q in QUESTIONS:
        row = {"q": q}
        for m in (BASELINE, CANDIDATE):
            ans, ev = answers[m][q], evid[q]
            fr = F.score(ans, ev)
            jr = J.judge(q, ans, ev)
            ov = J.overall(jr) if not isinstance(jr, dict) or "overall" not in jr else jr.get("overall")
            # llm_judge.judge 반환 형태 방어
            try:
                overall = jr.get("overall") if isinstance(jr, dict) else None
                if overall is None:
                    overall = J.overall(jr)
            except Exception:
                overall = None
            row[m] = {"faith": fr["faithfulness"], "halluc": bool(fr["hallucination"]),
                      "claims": [fr["n_claims"], fr["n_supported"], fr["n_unsupported"], fr["n_contradicted"]],
                      "quality": overall, "gen_sec": gen_sec[m][q], "answer": ans[:600]}
            if fr["faithfulness"] is not None:
                agg[m]["faith"].append(fr["faithfulness"])
            agg[m]["halluc"] += int(bool(fr["hallucination"]))
            if overall is not None:
                agg[m]["qual"].append(overall)
            agg[m]["sec"].append(gen_sec[m][q])
            print("  [%s] faith=%.3f halluc=%s qual=%s %.1fs Q=%s"
                  % (m, fr["faithfulness"] or 0, fr["hallucination"], overall, gen_sec[m][q], q[:24]))
        out["per_q"].append(row)

    def avg(x):
        return round(sum(x) / len(x), 3) if x else None
    summary = {}
    for m in (BASELINE, CANDIDATE):
        a = agg[m]
        summary[m] = {"faith_avg": avg(a["faith"]), "halluc": "%d/%d" % (a["halluc"], len(QUESTIONS)),
                      "quality_avg": avg(a["qual"]), "gen_sec_avg": avg(a["sec"])}
    out["summary"] = summary
    print("\n===== 요약 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if resource.get("candidate_ps"):
        for mm in resource["candidate_ps"]:
            print("candidate 로드: %s size=%.1fGB" % (mm.get("name"), (mm.get("size") or 0) / 1e9))
    with open("/tmp/qwen36_ab_result.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n결과 저장: /tmp/qwen36_ab_result.json")


if __name__ == "__main__":
    main()
