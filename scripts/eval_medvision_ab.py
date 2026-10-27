#!/usr/bin/env python3
"""의료 영상판독 모델 재평가 — KorMedMCQA-V(doctor) A/B.

현 Ollama 0.30.11서 vision 작동 확인된 fittable 후보만(qwen3-vl·gemma4는 빈응답=제외).
format 제약 디코딩(원 하네스 동일). 종료 후 상주 fast-lane(qwen2.5:14b) 복원.
결과 → stdout + /tmp/medvision_ab_result.json
"""
import json, re, sys, time, requests

O = "http://127.0.0.1:11434"
APP = "http://127.0.0.1:8585"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
MODELS = [
    "dcarrascosa/medgemma-1.5-4b-it:Q8_0",   # 현 프로덕션 medical vision(baseline)
    "minicpm-v:latest",                        # 범용 VLM(작동 확인) — "범용>의료특화" 가설 검증
    "MedAIBase/MedGemma1.5:4b",                # 풀프리시전 MedGemma1.5 빌드(Q8 대비)
]
SYS = "당신은 한국 의료 영상 판독 전문가입니다. 문제와 영상을 보고 정답 하나의 번호(1~5)만 답하세요."
LET = ["A", "B", "C", "D", "E"]


def imgs(row):
    try:
        out = []
        for d in json.loads(row["images"]):
            b = d.get("image_base64") or ""
            if "," in b[:64]:
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


def main():
    from datasets import load_dataset
    ds = load_dataset("seongsubae/KorMedMCQA-V", "doctor", split="test")
    items = [ds[i] for i in range(min(N, len(ds)))]
    print("=== KorMedMCQA-V (doctor, %d문항, 영상 멀티모달) — chance≈20%% ===" % len(items), flush=True)
    results = []
    for model in MODELS:
        cor = pf = noimg = empty = 0
        t0 = time.time()
        for row in items:
            im = imgs(row)
            if not im:
                noimg += 1
            try:
                raw = ask(model, row, im)
                if not (raw or "").strip():
                    empty += 1
                p = parse(raw)
            except Exception:
                p = None
            if p is None:
                pf += 1
            if p == row["answer"]:
                cor += 1
        acc = cor / len(items) * 100
        row_r = {"model": model, "acc": round(acc, 1), "correct": cor, "n": len(items),
                 "parse_fail": pf, "empty": empty, "noimg": noimg, "sec": round(time.time() - t0)}
        results.append(row_r)
        print("  %-40s acc=%5.1f%% (%d/%d) pf=%d empty=%d noimg=%d %ds"
              % (model, acc, cor, len(items), pf, empty, noimg, row_r["sec"]), flush=True)
    json.dump({"n": len(items), "results": results}, open("/tmp/medvision_ab_result.json", "w"),
              ensure_ascii=False, indent=1)
    # 상주 fast-lane 복원(앱 경유=keep_alive -1 재설정)
    try:
        requests.post(APP + "/api/chat", json={"model": "qwen2.5:14b",
                      "messages": [{"role": "user", "content": "ok"}], "stream": False}, timeout=120)
        print("상주 qwen2.5:14b 복원 요청 완료", flush=True)
    except Exception as e:
        print("상주 복원 요청 실패(수동 필요):", str(e)[:80], flush=True)
    print("결과: /tmp/medvision_ab_result.json", flush=True)


if __name__ == "__main__":
    main()
