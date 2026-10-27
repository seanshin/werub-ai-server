"""품질 종합 지수(Quality Index) — 세밀 지표들의 결정적 합성 (v2.123.0).

흩어진 품질 지표(충실도·환각·교차검증·G-Eval·체크리스트·한국어)를 단일 관리 수치
(0~100 + 등급 A~D)로 합성. **내부 관리지표**(추세·회귀 감시·상대 비교용)이며
대외 인증 수치가 아님 — N=고정 질의 풀·LLM-judge 기반 한계는 EVAL_GUIDE 참조.

산식(qi-1.0, 의료 안전 가중 — 공개·결정적·버전 표기):
  score = Σ(정규화 구성값 × 가중치) / Σ(측정된 구성 가중치) × 100
  - faithfulness 0.35 (근거 정합 = 의료 안전 핵심)
  - halluc_free  0.15 (1 - 환각 플래그율 = 안전 KPI)
  - strict       0.10 (judge+MiniCheck 양측 supported 비율 — 이중 검증 보수)
  - overall      0.25 (G-Eval 1~5 → (x-1)/4 정규화)
  - checks       0.10 (체크리스트 12항목 통과율)
  - korean       0.05 (한국어 비율)
  미측정 구성요소는 분모에서 제외(재정규화) — 세밀 지표 도입 전 배치와 호환.
  가중치 변경 시 INDEX_VERSION 올려 추세 해석 단절을 명시할 것.
"""
from __future__ import annotations

INDEX_VERSION = "qi-1.0"

WEIGHTS = {
    "faithfulness": 0.35,
    "halluc_free": 0.15,
    "strict": 0.10,
    "overall": 0.25,
    "checks": 0.10,
    "korean": 0.05,
}

GRADES = ((90, "A"), (80, "B"), (70, "C"), (0, "D"))


def _clamp01(x) -> float:
    return max(0.0, min(1.0, float(x)))


def grade_of(score: float) -> str:
    for th, g in GRADES:
        if score >= th:
            return g
    return "D"


def quality_index(faithfulness=None, halluc_free=None, strict=None,
                  overall=None, checks_rate=None, korean_ratio=None):
    """구성 지표 → {score(0~100), grade, components, weights_used, version}.

    None 인자=미측정(분모 제외). 측정 구성요소 0개면 None 반환.
    overall은 1~5 척도 → (x-1)/4 정규화, 나머지는 0~1 그대로(클램프).
    """
    comps = {}
    if faithfulness is not None:
        comps["faithfulness"] = _clamp01(faithfulness)
    if halluc_free is not None:
        comps["halluc_free"] = _clamp01(halluc_free)
    if strict is not None:
        comps["strict"] = _clamp01(strict)
    if overall is not None:
        comps["overall"] = _clamp01((float(overall) - 1.0) / 4.0)
    if checks_rate is not None:
        comps["checks"] = _clamp01(checks_rate)
    if korean_ratio is not None:
        comps["korean"] = _clamp01(korean_ratio)
    if not comps:
        return None
    total_w = sum(WEIGHTS[k] for k in comps)
    score = round(sum(v * WEIGHTS[k] for k, v in comps.items()) / total_w * 100, 1)
    return {"score": score, "grade": grade_of(score),
            "components": {k: round(v, 3) for k, v in comps.items()},
            "weights_used": {k: WEIGHTS[k] for k in comps},
            "version": INDEX_VERSION}


_LATEST_CACHE = {"ts": 0.0, "val": None}


def latest_index(db_path: str = None, ttl_s: int = 300):
    """eval.db 최신 배치에서 지수 계산(+TTL 캐시 — health 폴링마다 재계산 방지).

    /api/health 알림용: 야간 배치 데이터라 5분 캐시로 충분. 실패/미측정=None(비차단)."""
    import os
    import sqlite3
    import time
    now = time.time()
    if now - _LATEST_CACHE["ts"] < ttl_s:
        return _LATEST_CACHE["val"]
    path = db_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db")
    val = None
    try:
        conn = sqlite3.connect(path, timeout=3)
        conn.row_factory = sqlite3.Row
        f = conn.execute(
            """SELECT COUNT(*) n, AVG(faithfulness) mean, SUM(hallucination) h,
               AVG(strict_faithfulness) s FROM faithfulness_results
               WHERE ts=(SELECT MAX(ts) FROM faithfulness_results)""").fetchone()
        q = conn.execute(
            """SELECT AVG(overall) o, AVG(checks_rate) cr, AVG(korean_ratio) k
               FROM quality_results WHERE ts=(SELECT MAX(ts) FROM quality_results)""").fetchone()
        conn.close()
        faith = ({"mean": f["mean"], "hallucinations": f["h"], "n": f["n"], "strict": f["s"]}
                 if f and f["n"] else None)
        qual = ({"overall": q["o"], "checks_rate": q["cr"], "korean_ratio": q["k"]}
                if q and q["o"] is not None else None)
        val = index_from_batches(faith, qual)
    except Exception:  # noqa: BLE001 — eval.db 부재/구스키마 = 미측정
        val = None
    _LATEST_CACHE["ts"] = now
    _LATEST_CACHE["val"] = val
    return val


def index_from_batches(faith: dict | None, quality: dict | None):
    """배치 집계 dict(각각 /api/eval/status의 faithfulness/quality 형태) → 지수.

    faith: {mean, hallucinations, n, strict} · quality: {overall, checks_rate, korean_ratio}
    한쪽만 있어도 산출(재정규화). 둘 다 없으면 None.
    """
    f = faith or {}
    q = quality or {}
    halluc_free = None
    if f.get("n"):
        halluc_free = 1.0 - (f.get("hallucinations") or 0) / f["n"]
    return quality_index(
        faithfulness=f.get("mean"),
        halluc_free=halluc_free,
        strict=f.get("strict"),
        overall=q.get("overall"),
        checks_rate=q.get("checks_rate"),
        korean_ratio=q.get("korean_ratio"))
