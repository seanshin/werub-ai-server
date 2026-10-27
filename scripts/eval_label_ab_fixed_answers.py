#!/usr/bin/env python3
"""(v2.125.8 §7-4 하네스 — GPU /tmp/label_ab.py에서 커밋. 결과 원자료 docs/01-plan/references/label-ab-fixed-answers-n40.json)

근거 라벨이 **충실도 판정을 흔드는가** — 답변을 고정하고 두 표현으로 재채점한다.

옵션 A(평가 근거에 `/ask`와 같은 법령명 라벨을 붙임)의 유일한 위험은
**judge가 보는 텍스트가 달라져 충실도·환각이 함께 움직이는 것**이다.
그러면 추세 단절점이 생기고, 이 프로젝트는 그걸 여러 번 겪었다.

🔑 **답변을 고정하고 재채점해야만 처치 효과를 읽을 수 있다**(v2.124.85).
   08-18 배치에 저장된 40개 답변을 그대로 쓰고, **근거 표현만** 바꾼다.
   생성 변동이 0이므로 차이가 나오면 그건 라벨 때문이다.

사전 등록 판정:
  🔴 되돌린다  — 충실도 평균이 **±0.02 넘게** 움직이거나 환각 플래그가 **±3건 넘게** 바뀜
  ✅ 채택      — 그 안이면 라벨이 판정을 안 흔든 것으로 보고 유지
  ⚠️ 문항별 뒤집힘도 함께 본다 — 평균이 같아도 개별이 흔들리면 안정적이지 않다
"""
import json
import re
import sqlite3
import sys

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import eval_faithfulness as EF          # noqa: E402
from core import faithfulness as F      # noqa: E402

_LABEL_LINE = re.compile(r"^\[[^\]]*\]$", re.M)


def strip_labels(ev):
    """라벨 줄만 제거해 **종전 표현**을 복원한다(본문은 그대로)."""
    return _LABEL_LINE.sub("", ev).replace("\n\n", "\n").strip()


def main():
    db = sqlite3.connect(os.path.join(_ROOT, "eval.db"))
    db.row_factory = sqlite3.Row
    ts = db.execute("SELECT MAX(ts) t FROM faithfulness_results").fetchone()["t"]
    rows = db.execute("SELECT question, answer FROM faithfulness_results "
                      "WHERE ts=? AND answer IS NOT NULL AND answer<>''", (ts,)).fetchall()
    print("고정 답변 %d건 (배치 %s)\n" % (len(rows), ts[:19]), flush=True)

    out = []
    for i, r in enumerate(rows):
        q, a = r["question"], r["answer"]
        ev_new = EF.get_evidence(q)          # 라벨 있음(현재 코드)
        ev_old = strip_labels(ev_new)        # 라벨만 제거 = 종전 표현
        try:
            f_old = F.score(a, ev_old)
            f_new = F.score(a, ev_new)
        except Exception as e:               # noqa: BLE001
            print("  [%2d] 채점 실패: %s" % (i, str(e)[:60]), flush=True)
            continue
        rec = {"q": q,
               "old": {"faith": f_old.get("faithfulness"), "halluc": bool(f_old.get("hallucination"))},
               "new": {"faith": f_new.get("faithfulness"), "halluc": bool(f_new.get("hallucination")),
                       "law": f_new.get("law_misattributed") or []}}
        out.append(rec)
        fo, fn = rec["old"]["faith"], rec["new"]["faith"]
        mark = "" if (fo is None or fn is None or abs(fo - fn) < 1e-9) else "  ← 뒤집힘"
        print("  [%2d] old=%s new=%s%s  %s" % (i, fo, fn, mark, q[:26]), flush=True)

    def avg(key):
        v = [r[key]["faith"] for r in out if r[key]["faith"] is not None]
        return round(sum(v) / len(v), 4) if v else None

    ho = sum(1 for r in out if r["old"]["halluc"])
    hn = sum(1 for r in out if r["new"]["halluc"])
    flips = [r for r in out if r["old"]["faith"] is not None and r["new"]["faith"] is not None
             and abs(r["old"]["faith"] - r["new"]["faith"]) > 1e-9]
    law = [(r["q"][:30], r["new"]["law"]) for r in out if r["new"]["law"]]

    print("\n" + "=" * 62)
    print("  충실도 평균   라벨없음 %s  →  라벨있음 %s  (Δ %+.4f)"
          % (avg("old"), avg("new"), (avg("new") or 0) - (avg("old") or 0)))
    print("  환각 플래그   %d  →  %d  (Δ %+d)" % (ho, hn, hn - ho))
    print("  문항별 뒤집힘 %d/%d" % (len(flips), len(out)))
    print("\n  법령명 오귀속(라벨 있을 때) %d/%d" % (len(law), len(out)))
    for q, l in law:
        print("    ·", q, l)

    d = abs((avg("new") or 0) - (avg("old") or 0))
    verdict = ("🔴 되돌린다 — 충실도 %.4f 이동(임계 0.02)" % d if d > 0.02
               else "🔴 되돌린다 — 환각 %+d(임계 ±3)" % (hn - ho) if abs(hn - ho) > 3
               else "✅ 채택 — 라벨이 판정을 흔들지 않음")
    print("\n  사전 등록 판정: %s" % verdict)
    json.dump(out, open("/tmp/label_ab.json", "w"), ensure_ascii=False)
    print("  원자료: /tmp/label_ab.json")


if __name__ == "__main__":
    main()
