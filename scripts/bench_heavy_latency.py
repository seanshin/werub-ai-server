#!/usr/bin/env python3
"""heavy tier 지연 통제 측정 — `qwen3.6:35b-a3b`(현행) vs `qwen3-vl:30b-a3b`(후보).

사전 등록 문서: `docs/01-plan/features/heavy-ab-qwen3vl-30b.plan.md` §4
품질 A/B(`eval_hybrid_ab_qwen3vl30.py`)의 `gen_sec`으로는 **지연을 판정하지 않는다** —
실트래픽이 상주 14b를 되불러 30초 주기로 서로 축출하는 것을 2026-08-18에 실측했다.
그래서 지연만 따로, 통제 조건에서 잰다.

통제하는 것:
  1. **상주 해제** — 큐 워커가 2초마다 `_ensure_default_model()`을 부른다. 안 끄면 측정 중
     14b가 끼어들어 축출 왕복이 난다(v2.124.82). `finally`로 **반드시 복원**한다.
  2. **arm 교대 A→B→A→B** — 한 번씩 재고 비교하면 그 사이의 드리프트를 변경 몫으로 읽는다.
     같은 arm의 반복 간 차이가 **노이즈 바닥**이고, 그보다 작은 차이는 판정하지 않는다
     (v2.125.2 — 노이즈 바닥 22.7초 위에서 변경 몫 2.6초를 '개선'으로 읽을 뻔했다).
  3. **워밍업 1회** 후 측정 — 콜드 KV 캐시가 첫 호출만 다르게 만든다(v2.124.20).
  4. 모델 **교대 시마다 반납**(keep_alive=0) — 둘이 공존할 수 없으므로 적재 비용도 측정에 포함.

판정은 **중앙값**으로 한다(평균은 적재 1회에 끌려간다).

사용:  ./venv/bin/python -u scripts/bench_heavy_latency.py [--reps 3] [--n 6]
결과 → /tmp/heavy_latency_result.json
"""
import argparse
import json
import statistics
import time

import requests

OLLAMA = "http://127.0.0.1:11434"
APP = "http://localhost:8585"
BASELINE = "qwen3.6:35b-a3b"
CANDIDATE = "qwen3-vl:30b-a3b"
RESULT = "/tmp/heavy_latency_result.json"

# 길이를 고정해야 tok/s가 비교 가능하다. 프롬프트는 heavy tier가 실제로 받는 종류로.
PROMPTS = [
    "의료법상 진료기록부의 보존 기간과 그 예외를 조문 근거와 함께 설명해 주세요.",
    "의료기관 개설 요건과 개설 신고·허가의 구분을 정리해 주세요.",
    "무면허 의료행위의 벌칙 조항과 형량을 설명해 주세요.",
    "환자가 진료기록 사본 발급을 요청할 때 의료기관이 거부할 수 있는 경우는?",
    "의료광고 사전심의 의무의 대상과 예외를 설명해 주세요.",
    "전자의무기록의 보관·관리 의무와 침해 시 벌칙을 설명해 주세요.",
]


def post(url, payload, timeout=1800):
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def ps():
    try:
        return requests.get(OLLAMA + "/api/ps", timeout=10).json().get("models", [])
    except Exception:
        return []


def unload(model):
    try:
        post(OLLAMA + "/api/generate",
             {"model": model, "prompt": "x", "keep_alive": 0, "stream": False,
              "options": {"num_predict": 1}}, timeout=120)
    except Exception:
        pass


def measure(model, n, num_predict):
    """콜드 적재 1회 + 웜 n회. 반환: (적재초, GPU비율, [웜 tok/s], [웜 wall])"""
    t0 = time.time()
    post(OLLAMA + "/api/generate",
         {"model": model, "prompt": "안녕하세요", "stream": False, "think": False,
          "options": {"num_predict": 8, "num_ctx": 8192}})
    load_s = round(time.time() - t0, 1)

    gpu_pct = None
    for m in ps():
        if m["name"] == model and m.get("size"):
            gpu_pct = round(100 * m["size_vram"] / m["size"], 1)

    toks, walls = [], []
    for i in range(n):
        p = PROMPTS[i % len(PROMPTS)]
        w0 = time.time()
        d = post(OLLAMA + "/api/generate",
                 {"model": model, "prompt": p, "stream": False, "think": False,
                  "options": {"num_predict": num_predict, "num_ctx": 8192,
                              "temperature": 0.3, "seed": 7}})
        walls.append(round(time.time() - w0, 2))
        ed = d.get("eval_duration", 1) / 1e9
        toks.append(round(d.get("eval_count", 0) / ed, 2) if ed else 0.0)
    return load_s, gpu_pct, toks, walls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="arm 반복 수(노이즈 바닥 산출용, 최소 2)")
    ap.add_argument("--n", type=int, default=6, help="arm당 웜 호출 수")
    ap.add_argument("--num-predict", type=int, default=256)
    args = ap.parse_args()
    assert args.reps >= 2, "arm 반복이 2 미만이면 노이즈 바닥을 못 낸다 — 판정 불가"

    out = {"baseline": BASELINE, "candidate": CANDIDATE,
           "reps": args.reps, "n_per_arm": args.n, "num_predict": args.num_predict, "arms": []}
    restored = False
    try:
        print("== 상주 해제(측정 중 14b 끼어듦 차단) ==", flush=True)
        post(APP + "/api/models/residency", {"resident": False})
        time.sleep(5)
        print("  잔여 적재:", [m["name"] for m in ps()], flush=True)

        for rep in range(args.reps):
            for model in (BASELINE, CANDIDATE):      # 교대 A→B→A→B…
                unload(BASELINE if model == CANDIDATE else CANDIDATE)
                load_s, gpu, toks, walls = measure(model, args.n, args.num_predict)
                arm = {"rep": rep, "model": model, "load_s": load_s, "gpu_pct": gpu,
                       "tok_s": toks, "wall_s": walls,
                       "tok_s_median": round(statistics.median(toks), 2),
                       "wall_median": round(statistics.median(walls), 2)}
                out["arms"].append(arm)
                print("  rep%d %-18s 적재 %5.1fs · GPU %s%% · 중앙 %6.2f tok/s · 중앙 %6.2fs"
                      % (rep, model, load_s, gpu, arm["tok_s_median"], arm["wall_median"]), flush=True)
                json.dump(out, open(RESULT, "w"), ensure_ascii=False, indent=1)
                unload(model)
    finally:
        print("\n== 상주 복원 ==", flush=True)
        try:
            post(APP + "/api/models/residency", {"resident": True, "model": "qwen2.5:14b"})
            time.sleep(8)
            for m in ps():
                print("  복원: %s vram %.2fGB GPU %.0f%%"
                      % (m["name"], m["size_vram"] / 1e9, 100 * m["size_vram"] / m["size"]), flush=True)
            restored = True
        except Exception as e:
            print("  [!] 복원 실패 — 수동 확인 필요:", e, flush=True)
    out["residency_restored"] = restored

    # ── 노이즈 바닥부터 낸다. 그보다 작은 차이는 판정하지 않는다. ──
    def med(model):
        return [a["wall_median"] for a in out["arms"] if a["model"] == model]

    b, c = med(BASELINE), med(CANDIDATE)
    floor = max(max(b) - min(b), max(c) - min(c))          # 같은 arm 반복 간 최대 변동
    delta = statistics.median(b) - statistics.median(c)     # 양수 = 후보가 빠름
    out["noise_floor_s"] = round(floor, 2)
    out["delta_s"] = round(delta, 2)
    out["baseline_median_s"] = round(statistics.median(b), 2)
    out["candidate_median_s"] = round(statistics.median(c), 2)
    out["speedup_pct"] = round(100 * delta / statistics.median(b), 1) if b else None

    if abs(delta) <= floor:
        out["verdict"] = "판정 불가 — 차이가 노이즈 바닥 이내"
    elif delta > 0 and out["speedup_pct"] >= 20:
        out["verdict"] = "후보 우세 — 사전 등록 지연 조건(≥20%) 충족"
    elif delta > 0:
        out["verdict"] = "후보가 빠르나 사전 등록 기준(≥20%) 미달"
    else:
        out["verdict"] = "현행 우세"
    json.dump(out, open(RESULT, "w"), ensure_ascii=False, indent=1)

    print("\n===== 지연 요약 =====", flush=True)
    print("  노이즈 바닥(같은 arm 반복 간)  %6.2fs" % out["noise_floor_s"], flush=True)
    print("  현행 중앙 %6.2fs · 후보 중앙 %6.2fs · 차이 %+.2fs (%s%%)"
          % (out["baseline_median_s"], out["candidate_median_s"], out["delta_s"], out["speedup_pct"]), flush=True)
    print("  판정: %s" % out["verdict"], flush=True)
    print("  결과:", RESULT, flush=True)


if __name__ == "__main__":
    main()
