"""LLM 평가 질의 풀 — core 고정 + pool 일자 기반 결정적 회전 (v2.122.0).

N=5 고정 질의의 커버리지 한계(절대 품질 대표성 부족) 보완: core(기존 5, 매 배치
포함=추세 연속성) + pool(의료법 전 영역)에서 rotate_k개를 **일자 기반 결정적**으로
회전 샘플 — 재현 가능(같은 날=같은 세트, 러너 2종이 같은 날 같은 질문을 측정),
`ceil(len(pool)/k)`일이면 풀 전체 커버.

질의 파일: config/eval_questions.json (문항 추가/삭제=파일만 수정, 무재배포).
"""
from __future__ import annotations

import json
import os

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "config", "eval_questions.json")

# 파일 미존재/손상 시 폴백(기존 core 5 — 측정 중단 방지)
_FALLBACK_CORE = [
    "진료기록부 보존기간은?",
    "의료광고 사전심의 의무는?",
    "처방전 대리수령이 가능한가?",
    "의료기관 개설자가 아닌 자의 의료기관 개설 시 벌칙은?",
    "환자 진료기록 사본 발급 요청을 거부할 수 있는가?",
]


def load_pool(path: str = None) -> dict:
    """질의 풀 로드 → {core, pool, rotate_k}. 실패 시 폴백(core만)."""
    try:
        with open(path or _DEFAULT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        core = [q.strip() for q in d.get("core") or [] if isinstance(q, str) and q.strip()]
        pool = [q.strip() for q in d.get("pool") or [] if isinstance(q, str) and q.strip()]
        k = int(d.get("rotate_k") or 5)
        if not core:
            core = list(_FALLBACK_CORE)
        return {"core": core, "pool": pool, "rotate_k": max(0, k)}
    except Exception:  # noqa: BLE001
        return {"core": list(_FALLBACK_CORE), "pool": [], "rotate_k": 0}


def select_questions(core: list, pool: list, k: int, day_ordinal: int) -> list:
    """core 전부 + pool에서 k개 결정적 회전 선택.

    회전: 시작 인덱스 (day_ordinal*k) % len(pool)부터 k개 순환 — 연속 일자가 서로
    다른 구간을 덮어 ceil(n/k)일 주기로 풀 전체 커버. 중복 질문은 제거(순서 보존).
    """
    out = list(core)
    n = len(pool)
    if n and k > 0:
        k = min(k, n)
        start = (day_ordinal * k) % n
        out.extend(pool[(start + i) % n] for i in range(k))
    seen, dedup = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            dedup.append(q)
    return dedup


def questions_for_day(day_ordinal: int, path: str = None) -> list:
    """해당 일자의 측정 세트(core+회전). 러너 공용 진입점."""
    p = load_pool(path)
    return select_questions(p["core"], p["pool"], p["rotate_k"], day_ordinal)
