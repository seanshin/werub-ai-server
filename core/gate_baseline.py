"""게이트 베이스라인 — "알려진 실패"를 등록하고 **신규 실패만** 차단한다.

🔴 왜 필요한가 (2026-08-10 실측):
계약 스모크를 "전건 통과 아니면 배포 중단"으로 잡으면 못 쓴다.
08-06~08-10 **나흘간 스모크는 63/64**였다(무면허 벌칙 오귀속). 그 기준이었다면
그 사이 나간 v2.124.92·93·94가 전부 막혔을 것이다.
게다가 LLM 출력 게이트의 실패는 **확률적**이다 — 같은 질의 12회에 금지 신호가 6~8회 나왔다.
결정적 전건 통과를 요구하는 것은 원리적으로 성립하지 않는다.

⇒ 기준을 "절대"가 아니라 **"기준선 대비 신규"**로 잡는다.
   - 베이스라인에 **없는** 실패 → 차단
   - 베이스라인에 **있는** 실패 → 보고만(통제군을 약화시키는 게 아니라 *알려진 상태로 관리*)
   - 베이스라인에 있는데 **통과** → "회복" 보고(등록 해제 후보)

🔑 이건 R5(통제군 약화 금지)를 지키면서 게이트를 실용화하는 유일한 방법이다.
   "실패를 지우기"가 아니라 "실패를 알려진 것으로 등록하고, 새 실패만 막기"다.

⚠️ 베이스라인이 **커지기만 하면** 게이트는 죽는다. 항목 수를 지표로 추적하고
   증가 추세면 그 자체를 결함으로 본다(기획서 §13 S7). 등록은 **사람이 사유와 함께**
   하며 자동 추가하지 않는다. `stale_days` 경과 항목은 리포트에 ⚠️로 표시한다.

순수 모듈(Flask·네트워크 무관) — 단위 테스트 가능.
기획: docs/01-plan/features/model-decoupling-and-eval-driven-development.plan.md §8-3b
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import json
import os
from datetime import date, datetime

DEFAULT_STALE_DAYS = 30


def load(path: str) -> dict:
    """베이스라인 파일 → {name: entry}. 파일 없으면 빈 베이스라인(=전건이 신규).

    파일이 깨졌으면 **예외를 올린다** — 조용히 빈 베이스라인으로 떨어지면
    모든 기존 실패가 신규로 잡혀 게이트가 배포를 막는다(그러면 사람이 게이트를 끈다).
    """
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    items = raw.get("known_failures", raw) if isinstance(raw, dict) else raw
    out = {}
    for it in items or []:
        if isinstance(it, str):
            out[it] = {"name": it}
        elif isinstance(it, dict) and it.get("name"):
            out[it["name"]] = it
    return out


def _age_days(entry: dict, today: date | None = None) -> int | None:
    since = entry.get("since")
    if not since:
        return None
    try:
        d = datetime.strptime(str(since)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return ((today or date.today()) - d).days


def classify(results, baseline: dict, *, stale_days: int = DEFAULT_STALE_DAYS,
             today: date | None = None) -> dict:
    """스모크 결과를 베이스라인과 대조.

    results: [(name, passed, detail)] — smoke_contract._results 형태 그대로.

    반환:
      new_failures  차단 대상(베이스라인에 없는 실패)
      known_failures 알려진 실패(보고만)
      recovered     베이스라인에 있는데 통과 → 등록 해제 후보
      stale         등록 후 stale_days 경과한 알려진 실패(방치 경고)
      passed/total  집계
    """
    new_failures, known, recovered, stale = [], [], [], []
    passed = 0
    for name, ok, detail in results:
        entry = baseline.get(name)
        if ok:
            passed += 1
            if entry is not None:
                recovered.append({"name": name, "note": entry.get("note", "")})
            continue
        if entry is None:
            new_failures.append({"name": name, "detail": detail})
        else:
            age = _age_days(entry, today)
            known.append({"name": name, "detail": detail,
                          "note": entry.get("note", ""), "age_days": age})
            if age is not None and age >= stale_days:
                stale.append({"name": name, "age_days": age,
                              "note": entry.get("note", "")})
    return {
        "total": len(results), "passed": passed,
        "new_failures": new_failures, "known_failures": known,
        "recovered": recovered, "stale": stale,
        "baseline_size": len(baseline),
    }


def should_block(verdict: dict) -> bool:
    """차단 여부 — **신규 실패가 있을 때만**. 알려진 실패는 배포를 막지 않는다."""
    return bool(verdict.get("new_failures"))


def format_report(verdict: dict) -> str:
    lines = ["=== 게이트 판정 ===",
             "  통과 %d/%d · 베이스라인 %d건"
             % (verdict["passed"], verdict["total"], verdict["baseline_size"])]
    if verdict["new_failures"]:
        lines.append("  🔴 신규 실패 %d건 — **차단**" % len(verdict["new_failures"]))
        for f in verdict["new_failures"]:
            lines.append("      %s  %s" % (f["name"], (f.get("detail") or "")[:70]))
    else:
        lines.append("  ✅ 신규 실패 없음")
    for f in verdict["known_failures"]:
        age = "" if f["age_days"] is None else " (%d일)" % f["age_days"]
        lines.append("  ⬜ 알려진 실패%s %s — %s" % (age, f["name"], f["note"][:50]))
    for f in verdict["recovered"]:
        lines.append("  🎉 회복 %s — 베이스라인에서 제거할 것" % f["name"])
    for f in verdict["stale"]:
        lines.append("  ⚠️ 방치 %d일 %s — 고치거나 사유를 갱신할 것"
                     % (f["age_days"], f["name"]))
    return "\n".join(lines)
