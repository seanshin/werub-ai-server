#!/usr/bin/env python3
"""G1 — heavy(35b-a3b) vs 상주(14b) 창작·장문 A/B. 사전 등록 그대로 실행한다.

등록 문서: docs/01-plan/features/heavy-creative-longform-eval.plan.md (측정 **전** 작성)
표본     : docs/01-plan/references/heavy-creative-eval-questions.json (실행 전 커밋=고정·N=40)

절차(등록 §5) — 단계마다 별도 실행이라 중단 내성이 있다:
    1) --capability   B팔 사고 누출 검증 3문항(사고 0자·본문 정상). **실패 시 본 측정 무효**
    2) --noise        A팔 동일 표본 2회 → judge 흔들림 밴드(A-A). 이걸 모르면 A-B 차이를 못 읽는다
    3) --run          A/B 본 실행(교차 순서·seed 고정)
    4) --verdict      §3 기준 그대로 판정(코드가 기준을 들고 있다 — 사후 조정 방지)

판정 기준(등록 §3 · 여기서 바꾸지 말 것):
    heavy 권고 = ①품질 부호검정 p<0.05로 B 우위 AND ②결정적 축 비열세 AND ③빈 응답·붕괴 0
    그 외 = 현상 유지. 부분 우위는 관찰 기록으로만.

무효 조건(등록 §4): thinking 누출 · 캐시 히트 의심(0.00N초) · 다른 GPU 작업 겹침 · 노이즈 바닥 미측정.
결과 → docs/01-plan/references/heavy-creative-eval-result.json (원자료)
      체크포인트 logs/heavy_creative_ab_answers.json (중단 내성 — 답변 재사용은 생성만·판정은 항상 새로)
"""
import argparse
import json
import math
import os
import sys
import time

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import llm_judge as J                      # noqa: E402
from core.korean import korean_quality_check                      # noqa: E402
from core.korean_humanize import detect_repetition                # noqa: E402

OLLAMA = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
A_MODEL = os.environ.get("G1_A_MODEL", "qwen2.5:14b")        # 상주(대조)
B_MODEL = os.environ.get("G1_B_MODEL", "qwen3.6:35b-a3b")    # heavy(후보)
SEED = 42
# 양팔 **동일** — D2 핀과 같은 값을 명시해 엔진 기본값 의존을 없앤다(대칭성)
OPTIONS = {"temperature": 0.8, "top_p": 0.9, "seed": SEED,
           "num_predict": 1400, "repeat_penalty": 1.1}
SYSTEM = "당신은 한국어 작가입니다. 지시를 정확히 지키고, 반드시 한국어로만 씁니다."

QUESTIONS = os.path.join(_ROOT, "docs/01-plan/references/heavy-creative-eval-questions.json")
RESULT = os.path.join(_ROOT, "docs/01-plan/references/heavy-creative-eval-result.json")
CKPT = os.path.join(_ROOT, "logs/heavy_creative_ab_answers.json")


def _load_questions():
    with open(QUESTIONS, encoding="utf-8") as f:
        d = json.load(f)
    qs = d["questions"]
    assert len(qs) == d["_n"] == 40, "표본이 40이 아니다 — 사전 등록 위반"
    return qs


def _generate(model, prompt, timeout=600):
    """1회 생성. 반환 (text, thinking_len, sec, raw)."""
    t0 = time.time()
    r = requests.post(f"{OLLAMA}/api/generate", timeout=timeout, json={
        "model": model, "prompt": prompt, "system": SYSTEM, "stream": False,
        "think": False, "options": dict(OPTIONS), "keep_alive": "5m"})
    sec = time.time() - t0
    r.raise_for_status()
    d = r.json()
    return (d.get("response") or ""), len(d.get("thinking") or ""), sec, d


# ── 1) capability 검증 — B팔이 think=false를 실제로 지키는가 ────────────────
def cmd_capability():
    """v2.125.5에서 후보가 `think:false`를 무시해 1차 측정이 통째로 무효됐다.
    본 측정 전에 그 조건을 먼저 깬다 — 여기서 실패하면 §4 무효 조건이므로 진행 금지."""
    probes = [("짧은 인사말을 한 문장으로 써라.", 1),
              ("사과 세 종류를 쉼표로 나열하라.", 1),
              ("가을 저녁을 120자 내외로 묘사하라.", 40)]
    ok = True
    for prompt, min_len in probes:
        text, think_len, sec, _ = _generate(B_MODEL, prompt, timeout=300)
        good = think_len == 0 and len(text.strip()) >= min_len
        ok = ok and good
        print("  %s think=%d자 본문=%d자 %.1fs :: %s" % (
            "✅" if good else "🔴", think_len, len(text.strip()), sec, prompt[:24]), flush=True)
    print("capability:", "PASS" if ok else "FAIL(본 측정 무효 — 진행 금지)", flush=True)
    return 0 if ok else 2


# ── 결정적 축 ──────────────────────────────────────────────────────────────
def _deterministic(q, text):
    """지시 준수(신호 전부 포함·길이 범위) + 한국어/반복. judge와 독립."""
    t = text or ""
    signals = all(s in t for s in q["signals_all"])
    length_ok = q["min_len"] <= len(t) <= q["max_len"]
    # 🔴 2026-08-25 정정: 종전엔 `compute_korean_quality(...).get("korean_ratio")`를 읽었는데
    #    그 함수는 그 키를 **반환하지 않는다**(AI 패턴 메트릭만) → 결과 JSON 양팔 `korean_ratio: null`.
    #    사전 등록 §2-2가 못 박은 결정적 축 3개 중 1개가 **조용히 결측**됐다. 정본은 `core/korean`.
    #    📌 "탐지기부터 검증하라"의 재발 — 내가 null을 보고도 넘겼다.
    _ok, ratio, _detail = korean_quality_check(t) if t else (False, 0.0, "empty")
    rep = detect_repetition(t) if t else {}
    return {"signals_ok": bool(signals), "length_ok": bool(length_ok), "chars": len(t),
            "korean_ratio": ratio,
            "repetition_score": (rep or {}).get("score"),
            "empty": len(t.strip()) == 0}


def _judge_quality(q, text):
    try:
        jr = J.judge(q["prompt"][:1500], text, "")
        return jr.get("overall") if isinstance(jr, dict) else None
    except Exception as e:  # noqa: BLE001
        print("    ⚠ judge 실패: %s" % str(e)[:60], flush=True)
        return None


def _sign_test(pairs):
    """쌍별 부호검정(양측). pairs=[(a,b)] · 반환 (b승, a승, 동률, p)."""
    b_win = sum(1 for a, b in pairs if a is not None and b is not None and b > a)
    a_win = sum(1 for a, b in pairs if a is not None and b is not None and b < a)
    tie = sum(1 for a, b in pairs if a is not None and b is not None and b == a)
    n = b_win + a_win
    if n == 0:
        return b_win, a_win, tie, 1.0
    k = min(b_win, a_win)
    cum = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return b_win, a_win, tie, min(1.0, 2 * cum)


def _load_ckpt():
    if os.path.exists(CKPT):
        with open(CKPT, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_ckpt(d):
    os.makedirs(os.path.dirname(CKPT), exist_ok=True)
    with open(CKPT, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


# ── 2) 노이즈 바닥 — A팔 2회(A-A) ──────────────────────────────────────────
def cmd_noise():
    qs = _load_questions()
    ck = _load_ckpt()
    for run in ("A1", "A2"):
        for i, q in enumerate(qs, 1):
            key = "%s::%s" % (run, q["id"])
            if key in ck:
                continue
            text, think_len, sec, _ = _generate(A_MODEL, q["prompt"])
            ck[key] = {"text": text, "think": think_len, "sec": sec}
            _save_ckpt(ck)
            print("  %s %2d/40 %s %.1fs %d자" % (run, i, q["id"], sec, len(text)), flush=True)
    pairs = []
    for q in qs:
        a1 = _judge_quality(q, ck["A1::%s" % q["id"]]["text"])
        a2 = _judge_quality(q, ck["A2::%s" % q["id"]]["text"])
        pairs.append((a1, a2))
    w2, w1, tie, p = _sign_test(pairs)
    band = {"a2_win": w2, "a1_win": w1, "tie": tie, "p": round(p, 4),
            "flip_rate": round((w1 + w2) / max(1, len(pairs)), 3)}
    print("A-A 노이즈 바닥:", json.dumps(band, ensure_ascii=False), flush=True)
    ck["_noise_band"] = band
    _save_ckpt(ck)
    return 0


# ── 3) 본 실행 ─────────────────────────────────────────────────────────────
def cmd_run():
    qs = _load_questions()
    ck = _load_ckpt()
    if "_noise_band" not in ck:
        print("🔴 노이즈 바닥 미측정 — 등록 §4 무효 조건. `--noise` 먼저.", flush=True)
        return 2
    for i, q in enumerate(qs, 1):
        # 교차 순서: 홀수 문항은 B 먼저(적재 순서 편향 상쇄)
        arms = [("B", B_MODEL), ("A", A_MODEL)] if i % 2 else [("A", A_MODEL), ("B", B_MODEL)]
        for arm, model in arms:
            key = "%s::%s" % (arm, q["id"])
            if key in ck:
                continue
            text, think_len, sec, _ = _generate(model, q["prompt"])
            if sec < 0.05:
                print("  🔴 캐시 히트 의심(%.3fs) — 등록 §4 무효 조건" % sec, flush=True)
            ck[key] = {"text": text, "think": think_len, "sec": sec}
            _save_ckpt(ck)
            print("  %s %2d/40 %s %.1fs %d자 think=%d" % (
                arm, i, q["id"], sec, len(text), think_len), flush=True)
    print("생성 완료 — `--verdict`로 판정", flush=True)
    return 0


# ── 4) 판정 ───────────────────────────────────────────────────────────────
def cmd_verdict():
    qs = _load_questions()
    ck = _load_ckpt()
    missing = [q["id"] for q in qs for arm in ("A", "B") if "%s::%s" % (arm, q["id"]) not in ck]
    if missing:
        print("🔴 미생성 %d건 — `--run` 먼저: %s" % (len(missing), missing[:3]), flush=True)
        return 2
    rows, pairs = [], []
    think_leak = 0
    for q in qs:
        a, b = ck["A::%s" % q["id"]], ck["B::%s" % q["id"]]
        think_leak += 1 if b["think"] else 0
        da, db = _deterministic(q, a["text"]), _deterministic(q, b["text"])
        qa, qb = _judge_quality(q, a["text"]), _judge_quality(q, b["text"])
        pairs.append((qa, qb))
        rows.append({"id": q["id"], "kind": q["kind"],
                     "A": {**da, "quality": qa, "sec": round(a["sec"], 1)},
                     "B": {**db, "quality": qb, "sec": round(b["sec"], 1), "think": b["think"]}})

    def _rate(arm, field):
        vals = [r[arm][field] for r in rows if r[arm][field] is not None]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

    def _mean(arm, field):
        vals = [r[arm][field] for r in rows if isinstance(r[arm].get(field), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None

    bw, aw, tie, p = _sign_test(pairs)
    det = {arm: {"signals_ok": _rate(arm, "signals_ok"), "length_ok": _rate(arm, "length_ok"),
                 "korean_ratio": _mean(arm, "korean_ratio"),
                 "repetition_score": _mean(arm, "repetition_score"),
                 "empty": sum(1 for r in rows if r[arm]["empty"]),
                 "sec_mean": _mean(arm, "sec")} for arm in ("A", "B")}
    band = ck.get("_noise_band", {})

    # 등록 §3 기준 — 코드가 기준을 들고 있다
    cond1 = p < 0.05 and bw > aw
    cond2 = all((det["B"][k] or 0) >= (det["A"][k] or 0) - eps for k, eps in
                (("signals_ok", 0.05), ("length_ok", 0.05),
                 ("korean_ratio", 0.02), ("repetition_score", 3)))
    cond3 = det["B"]["empty"] == 0 and think_leak == 0
    verdict = "heavy 권고(창작·장문 한정)" if (cond1 and cond2 and cond3) else "현상 유지"
    # 사전 등록 §4 마지막 항: A-A 노이즈와 A-B 차이가 **같은 급**이면 그 축은 판정 불가로 선언한다.
    # (결론이 '현상 유지'로 같더라도, 기준을 적어놓고 발동시키지 않으면 다음 측정에서 유리하게 쓰인다.)
    quality_undecidable = bool(
        band and abs((bw - aw)) <= abs(band.get("a1_win", 0) - band.get("a2_win", 0)) + 1
        and p > 0.05)
    invalid = []
    if think_leak:
        invalid.append("B팔 thinking 누출 %d건" % think_leak)
    if not band:
        invalid.append("노이즈 바닥 미측정")

    out = {"_registered": "docs/01-plan/features/heavy-creative-longform-eval.plan.md",
           "_date": time.strftime("%Y-%m-%d"), "A_model": A_MODEL, "B_model": B_MODEL,
           "options": OPTIONS, "n": len(rows),
           "quality_sign_test": {"B_win": bw, "A_win": aw, "tie": tie, "p": round(p, 4)},
           "noise_band_AA": band, "deterministic": det,
           "conditions": {"quality_B_superior": cond1, "deterministic_not_inferior": cond2,
                          "no_empty_no_leak": cond3},
           "quality_axis_undecidable": quality_undecidable,
           "verdict": verdict, "invalidated_by": invalid, "rows": rows}
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(json.dumps({k: out[k] for k in
                      ("quality_sign_test", "noise_band_AA", "deterministic",
                       "conditions", "quality_axis_undecidable", "verdict", "invalidated_by")},
                     ensure_ascii=False, indent=1), flush=True)
    print("원자료 →", RESULT, flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description="G1 heavy 창작·장문 A/B (사전 등록 실행기)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capability", action="store_true", help="1) B팔 사고 누출 검증")
    g.add_argument("--noise", action="store_true", help="2) A-A 노이즈 바닥")
    g.add_argument("--run", action="store_true", help="3) A/B 본 실행")
    g.add_argument("--verdict", action="store_true", help="4) 판정")
    a = ap.parse_args()
    if a.capability:
        return cmd_capability()
    if a.noise:
        return cmd_noise()
    if a.run:
        return cmd_run()
    return cmd_verdict()


if __name__ == "__main__":
    sys.exit(main())
