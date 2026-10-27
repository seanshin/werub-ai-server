#!/usr/bin/env python3
"""의료 파이프라인 A/B — full(med42 포함) vs single_model(qwen2.5:14b).

KorMedMCQA 표준벤치서 qwen>med42로 뜬 재평가 플래그의 실전 검증: **실제 프로덕션 경로**
(/api/chat/enhanced medical)에서 두 구성을 비교. MCQA(파라메트릭)와 달리 파이프라인
전체(정규화→추론→한국어 종합)를 측정.

축 3: ①객관 정확도(KorMedMCQA 문항을 파이프라인에 통과→정답 일치, judge 편향 0)
      ②주관 품질(자유 임상질문→블라인드 judge 루브릭, ⚠️judge=qwen2.5:14b라
        자기선호 편향 가능성 캐비엇 — 객관축과 교차 해석)
      ③지연(arm별 평균 초)
결과 → eval.db(eval_results: model=arm명 / quality_results: source=ab-*).

사용법: scripts/eval_medical_ab.py [--n 30]
"""
import argparse, os, re, sqlite3, sys, time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API = os.environ.get("ADMIN_API", "http://127.0.0.1:8585")
EVAL_DB = os.environ.get("EVAL_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db"))

ARMS = {
    "pipeline-full-med42": {"domain": "medical"},
    "pipeline-single-qwen14b": {"domain": "medical", "single_model": "qwen2.5:14b"},
}

FREEFORM = [
    "당뇨 환자가 메트포르민 복용 중인데 조영제 CT를 찍어야 합니다. 주의사항은?",
    "와파린 복용 환자가 잇몸 출혈이 잦아졌다고 합니다. 어떻게 접근해야 하나요?",
    "65세 남성, 갑자기 한쪽 팔에 힘이 빠지고 말이 어눌해졌다가 30분 만에 회복됐습니다. 무엇을 의심하고 어떻게 해야 하나요?",
    "임신 8주 산모가 이소트레티노인을 복용 중이었습니다. 어떤 조치가 필요한가요?",
    "만성 신부전 환자(eGFR 25)에게 NSAIDs 진통제를 써도 되나요? 대안은?",
]


def call_arm(arm_body, message, timeout=300):
    body = dict(arm_body)
    body["messages"] = [{"role": "user", "content": message}]
    t0 = time.time()
    r = requests.post(API + "/api/chat/enhanced", json=body, timeout=timeout)
    return (r.json().get("answer") or ""), time.time() - t0


def run_mc(n):
    from datasets import load_dataset
    ds = load_dataset("sean0042/KorMedMCQA", "doctor", split="test")
    items = []
    for i in range(min(n, len(ds))):
        q = ds[i]
        opts = "\n".join("%d. %s" % (j, q[k]) for j, k in enumerate("ABCDE", 1) if q.get(k))
        items.append(("문제: %s\n%s\n\n정답 번호(1~5) 하나를 반드시 명시해 답하세요." % (q["question"], opts),
                      int(q["answer"])))
    db = sqlite3.connect(EVAL_DB, timeout=10)
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    print("=== ① 객관 정확도 (KorMedMCQA doctor %d문항 → 실제 파이프라인) ===" % len(items))
    for arm, body in ARMS.items():
        correct = pf = 0
        secs = []
        for msg, gold in items:
            try:
                ans, sec = call_arm(body, msg)
                secs.append(sec)
                m = re.search(r"정답[^\d]{0,10}([1-5])", ans) or re.search(r"([1-5])\s*[번\.]", ans) or re.search(r"[1-5]", ans)
                pred = int(m.group(1) if m and m.groups() else m.group()) if m else None
            except Exception:
                pred = None
            if pred is None:
                pf += 1
            if pred == gold:
                correct += 1
        acc = correct / len(items) * 100
        mean_sec = sum(secs) / len(secs) if secs else 0
        db.execute("INSERT INTO eval_results(ts,model,benchmark,config,n,correct,accuracy,decoding,sec,parse_fail)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (ts, arm, "kormedmcqa-pipeline", "doctor", len(items), correct,
                    round(acc, 2), "freeform", round(sum(secs), 1), pf))
        db.commit()
        print("  %-26s acc=%5.1f%% (%d/%d) pf=%d 평균 %.1fs/문항" % (arm, acc, correct, len(items), pf, mean_sec))
    db.close()


def run_judge():
    from core import llm_judge as J
    db = sqlite3.connect(EVAL_DB, timeout=10)
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    print("\n=== ② 주관 품질 (자유 임상질문 %d → 블라인드 judge, ⚠️자기선호 캐비엇) ===" % len(FREEFORM))
    for arm, body in ARMS.items():
        overalls = []
        for q in FREEFORM:
            try:
                ans, sec = call_arm(body, q)
                r = J.judge(q, ans)  # 근거 없음 — 임상 자유질문(모델명 미노출=블라인드)
            except Exception as e:
                print("  스킵(%s): %s" % (q[:20], e))
                continue
            s = r["scores"]

            def g(d):
                return s.get(d, {}).get("score")
            db.execute("INSERT INTO quality_results(ts,source,question,accuracy,helpfulness,safety,"
                       "fluency,overall,korean_ratio,judge_model,sec) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (ts, "ab-" + arm, q, g("accuracy"), g("helpfulness"), g("safety"), g("fluency"),
                        r["overall"], None, r["judge_model"], round(sec, 1)))
            db.commit()
            if r["overall"]:
                overalls.append(r["overall"])
        if overalls:
            print("  %-26s overall=%.2f/5 (n=%d)" % (arm, sum(overalls) / len(overalls), len(overalls)))
    db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--skip-mc", action="store_true")
    ap.add_argument("--skip-judge", action="store_true")
    a = ap.parse_args()
    if not a.skip_mc:
        run_mc(a.n)
    if not a.skip_judge:
        run_judge()
