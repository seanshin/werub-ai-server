#!/usr/bin/env python3
"""KorMedMCQA-V — 영상 멀티모달 벤치 (MedGemma1.5 vision). Ollama images 파라미터."""
import json, re, sys, time, requests
from datasets import load_dataset

O = "http://127.0.0.1:11434"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
MODELS = ["dcarrascosa/medgemma-1.5-4b-it:Q8_0", "qwen3-vl:8b"]
SYS = "당신은 한국 의료 영상 판독 전문가입니다. 문제와 영상을 보고 정답 하나의 번호(1~5)만 답하세요."
LET = ["A", "B", "C", "D", "E"]


def imgs(row):
    try:
        out = []
        for d in json.loads(row["images"]):
            b = d.get("image_base64") or ""
            if "," in b[:64]:      # data:image/png;base64,... 접두 제거(Ollama는 raw base64만)
                b = b.split(",", 1)[1]
            if b:
                out.append(b)
        return out
    except Exception:
        return []


def ask(model, row, images):
    opts = "\n".join("%d. %s" % (i, row[k]) for i, k in enumerate(LET, 1) if row.get(k))
    prompt = "문제: %s\n%s\n\n정답 번호(1~5)만:" % (row["question"], opts)
    k = sum(1 for c in LET if row.get(c))
    body = {"model": model, "system": SYS, "prompt": prompt, "images": images,
            "stream": False, "options": {"temperature": 0, "num_predict": 12},
            "format": {"type": "object", "properties": {"answer": {"type": "integer", "minimum": 1, "maximum": k}}, "required": ["answer"]}}
    return requests.post(O + "/api/generate", json=body, timeout=300).json().get("response", "")


def parse(txt):
    try:
        v = json.loads(txt).get("answer")
        if isinstance(v, int) and 1 <= v <= 5:
            return LET[v - 1]
    except Exception:
        pass
    m = re.search(r"[1-5]", txt or "")
    return LET[int(m.group()) - 1] if m else None


ds = load_dataset("seongsubae/KorMedMCQA-V", "doctor", split="test")
items = [ds[i] for i in range(min(N, len(ds)))]
print("=== KorMedMCQA-V (doctor, %d문항, 영상 멀티모달) ===" % len(items))
for model in MODELS:
    cor = pf = noimg = 0
    t0 = time.time()
    for row in items:
        im = imgs(row)
        if not im:
            noimg += 1
        try:
            p = parse(ask(model, row, im))
        except Exception:
            p = None
        if p is None:
            pf += 1
        if p == row["answer"]:
            cor += 1
    acc = cor / len(items) * 100
    print("  %-40s acc=%5.1f%% (%d/%d) pf=%d noimg=%d %.0fs" % (
        model, acc, cor, len(items), pf, noimg, time.time() - t0))
