#!/usr/bin/env python3
"""표준 벤치마크 정량 평가 하네스 (LLM 평가 프레임워크 Phase 1).

- 외부 API 0: Ollama 직접 호출(로컬 모델). 벤치 데이터만 HF에서 1회 다운로드.
- **공정 디코딩**: Ollama `format`(JSON 스키마)로 답을 {"answer": int}로 강제 →
  생성형 MC의 포맷추종 편향 제거(P0서 MedGemma 장황출력 문제 해소). loglikelihood
  대안(Ollama가 echo+logprobs 미지원)으로 채택, 측정 선행 결론.
- 객관식(MC) 벤치: KorMedMCQA(의료면허)·KMMLU(한국어 일반). answer=1-based int.
- 결과 → eval.db (모델×벤치×config×정답률, 일시·디코딩·파라미터).

사용법:
  scripts/eval_bench.py --bench kormedmcqa --n 100
  scripts/eval_bench.py --bench kormedmcqa --models qwen2.5:14b,thewindmom/llama3-med42-8b:latest --full
  scripts/eval_bench.py --bench kmmlu --configs Biology,Chemistry --n 50
"""
import argparse, json, os, re, sqlite3, time
from datetime import datetime, timezone

import requests

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
EVAL_DB = os.environ.get("EVAL_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db"))

DEFAULT_MODELS = ["qwen2.5:14b", "thewindmom/llama3-med42-8b:latest",
                  "dcarrascosa/medgemma-1.5-4b-it:Q8_0", "qwen2.5:7b"]

BENCHMARKS = {
    "kormedmcqa": {"hf": "sean0042/KorMedMCQA",
                   "configs": ["doctor", "nurse", "pharm", "dentist"],
                   "choices": ["A", "B", "C", "D", "E"], "split": "test"},
    "kmmlu": {"hf": "HAERAE-HUB/KMMLU",
              "configs": ["Biology", "Chemistry", "Accounting"],
              "choices": ["A", "B", "C", "D"], "split": "test"},
}
SYS = "당신은 한국어 객관식 시험을 보는 전문가입니다. 보기 중 정답 하나의 번호만 고르세요."


def init_db():
    c = sqlite3.connect(EVAL_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS eval_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, model TEXT, benchmark TEXT,
        config TEXT, n INTEGER, correct INTEGER, accuracy REAL, decoding TEXT,
        sec REAL, parse_fail INTEGER)""")
    c.commit()
    return c


def load_items(bench, config, n):
    from datasets import load_dataset
    spec = BENCHMARKS[bench]
    ds = load_dataset(spec["hf"], config, split=spec["split"])
    cols = [c for c in spec["choices"] if c in ds.column_names]
    items = []
    limit = len(ds) if n is None else min(n, len(ds))
    for i in range(limit):
        row = ds[i]
        choices = [(c, row[c]) for c in cols if row.get(c) not in (None, "")]
        items.append({"q": row["question"], "choices": choices, "answer": int(row["answer"])})
    return items


def ask(model, item, decoding):
    opts = "\n".join("%d. %s" % (j, txt) for j, (_, txt) in enumerate(item["choices"], 1))
    prompt = "문제: %s\n%s\n\n정답 번호 하나를 고르세요." % (item["q"], opts)
    k = len(item["choices"])
    body = {"model": model, "system": SYS, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_predict": 30}}
    if decoding == "format":
        body["format"] = {"type": "object",
                          "properties": {"answer": {"type": "integer", "minimum": 1, "maximum": k}},
                          "required": ["answer"]}
    r = requests.post(OLLAMA + "/api/generate", json=body, timeout=180)
    return r.json().get("response", "")


def parse(txt, k):
    if not txt:
        return None
    try:
        v = json.loads(txt).get("answer")
        if isinstance(v, int) and 1 <= v <= k:
            return v
    except Exception:
        pass
    m = re.search(r"[1-%d]" % k, txt)
    return int(m.group()) if m else None


def run(models, bench, configs, n, decoding):
    db = init_db()
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    data = {cfg: load_items(bench, cfg, n) for cfg in configs}
    print("=== %s | configs=%s | n=%s | decoding=%s ===" % (bench, ",".join(configs), n or "full", decoding))
    summary = {}
    for model in models:
        correct = tot = pf = 0
        t0 = time.time()
        per = {}
        for cfg in configs:
            c = t = 0
            for item in data[cfg]:
                k = len(item["choices"])
                try:
                    p = parse(ask(model, item, decoding), k)
                except Exception:
                    p = None
                if p is None:
                    pf += 1
                t += 1
                if p == item["answer"]:
                    c += 1
            per[cfg] = "%d/%d" % (c, t)
            correct += c
            tot += t
        sec = time.time() - t0
        acc = correct / tot * 100 if tot else 0
        db.execute("INSERT INTO eval_results(ts,model,benchmark,config,n,correct,accuracy,decoding,sec,parse_fail)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (ts, model, bench, ",".join(configs), tot, correct, round(acc, 2), decoding, round(sec, 1), pf))
        db.commit()
        summary[model] = (acc, correct, tot, round(sec), pf, per)
        print("%-46s acc=%5.1f%%  (%d/%d)  %ds  parse_fail=%d  %s"
              % (model, acc, correct, tot, sec, pf, per))
    print("\n=== %s 순위 ===" % bench)
    for m, (acc, c, t, s, pf, _) in sorted(summary.items(), key=lambda x: -x[1][0]):
        print("  %5.1f%%  %s  (pf=%d)" % (acc, m, pf))
    print("→ eval.db 저장 완료:", EVAL_DB)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default="kormedmcqa", choices=list(BENCHMARKS))
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--configs", default="")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--decoding", default="format", choices=["format", "plain"])
    a = ap.parse_args()
    cfgs = a.configs.split(",") if a.configs else BENCHMARKS[a.bench]["configs"]
    run(a.models.split(","), a.bench, cfgs, None if a.full else a.n, a.decoding)
