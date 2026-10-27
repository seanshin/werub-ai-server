#!/usr/bin/env python3
"""writing/draft CJK 표류 측정 — 사전 등록 `docs/01-plan/references/writing-draft-drift-2026-09-30.md`.

최소 Flask 앱에 `writing_bp`만 등록해 test client로 **실제 핸들러**를 돈다(스케줄러·DB 초기화 없음 —
앱 2중 기동이 아니다). Ollama는 라이브와 공유한다.

🔑 매 요청 전 `_writing_cache.clear()` — 09-28 측정 1차는 캐시 히트로 절반 이상 무효였다.
🔑 `_generate`를 감싸 **raw**(필터 전)를 기록한다 — 최종 draft만 보면 소실된 초안도 멀쩡해 보인다.

사용(GPU): ./venv/bin/python scripts/writing_drift_measure.py --arm A --n 20 --out logs/writing_drift_A_base.json
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from flask import Flask  # noqa: E402

import routes.writing as W  # noqa: E402
from core.korean import cjk_share, force_korean  # noqa: E402

GATE_INPUT = {"prompt": "사내 건강검진 안내문을 작성해줘", "tone": "formal", "length": "short"}
# 09-28 측정(scripts/korean_drift_survey.py DRAFTS)과 같은 10문장 — 팔 V는 이것을 2회 순환
DRAFTS = ["봄날 산책에 대한 짧은 수필을 써 줘.", "새해 다짐을 담은 짧은 글을 써 줘.",
          "동네 도서관을 소개하는 짧은 글을 써 줘.", "비 오는 날의 출근길을 짧게 묘사해 줘.",
          "처음 요리를 해 본 경험을 짧게 써 줘.", "가을 운동회의 추억을 짧게 써 줘.",
          "반려식물을 키우는 즐거움에 대해 짧게 써 줘.", "지하철에서 본 풍경을 짧게 묘사해 줘.",
          "할머니의 손맛에 대한 짧은 글을 써 줘.", "첫 월급을 받은 날을 짧게 써 줘."]
THRESH = 0.02   # core.korean.CJK_DRIFT_THRESHOLD와 같은 값(등록 문서 §3)


def payload(arm, i):
    if arm == "A":
        return dict(GATE_INPUT)
    return {"prompt": DRAFTS[i % len(DRAFTS)], "length": "short"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["A", "V"], required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    raws = []
    orig = W._generate

    def spy(model, prompt, system, options=None, timeout=None):
        r = orig(model, prompt, system, options, timeout)
        raws.append(r[0])
        return r

    W._generate = spy   # 반환 불변 — 기록만
    app = Flask(__name__)
    app.register_blueprint(W.writing_bp)
    client = app.test_client()

    rows = []
    for i in range(a.n):
        W._writing_cache.clear()
        raws.clear()
        t0 = time.time()
        r = client.post("/api/writing/draft", json=payload(a.arm, i))
        dt = time.time() - t0
        d = r.get_json(silent=True) or {}
        draft = (d.get("draft") or "").strip()
        shares = [round(cjk_share(x), 3) for x in raws]
        # observe=False — 출처 식별용 재필터가 표류 경고를 한 번 더 남기지 않게(09-30 1차 실행은 caller=__main__ 중복)
        src = next((k for k, x in enumerate(raws) if force_korean(x, observe=False).strip() == draft), None) if draft else None
        shipped_drift = r.status_code == 200 and src is not None and shares[src] > THRESH
        rows.append({"i": i, "status": r.status_code, "sec": round(dt, 1), "attempts": len(raws),
                     "shares": shares, "raw_lens": [len(x) for x in raws], "src": src,
                     "draft_len": len(draft), "first_drift": bool(shares) and shares[0] > THRESH,
                     "shipped_drift": shipped_drift, "draft": draft, "raws": list(raws)})
        print(f"{a.arm}{i:02d} {r.status_code} {dt:5.1f}s shares={shares} src={src} "
              f"draft={len(draft)}자{' 🔴표류출하' if shipped_drift else ''}", flush=True)
        time.sleep(1)

    secs = [x["sec"] for x in rows]
    summary = {"arm": a.arm, "n": a.n,
               "d1_first_drift": sum(x["first_drift"] for x in rows),
               "d2_shipped_drift": sum(x["shipped_drift"] for x in rows),
               "d3_502": sum(x["status"] == 502 for x in rows),
               "other_status": sorted({x["status"] for x in rows} - {200, 502}),
               "median_s": round(statistics.median(secs), 1), "max_s": max(secs),
               "src_unidentified_200": sum(x["status"] == 200 and x["src"] is None for x in rows)}
    print(json.dumps(summary, ensure_ascii=False))
    json.dump({"summary": summary, "rows": rows}, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("→", a.out)


if __name__ == "__main__":
    main()
