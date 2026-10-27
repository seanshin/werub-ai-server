"""Activity API Blueprint — 캐시, iMac 상태, 활동 로그, 메트릭"""
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, request, jsonify, Response

from core import metrics_reservoir

logger = logging.getLogger("ollama-admin")

activity_bp = Blueprint("activity", __name__)


def _escape_like(val: str) -> str:
    """LIKE 패턴 특수문자 이스케이프"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

_get_db = None
_load_config = None
_is_admin_request = None
_get_worker_alive = None
_get_queue_paused = None
_OLLAMA_BASE = None
_BASE_DIR = None
_llm_response_cache = None
_image_models_cache = None


def init_activity(*, get_ollama_status=None, is_imac_available=None, imac_ollama_base=None, lightweight_models=None, get_db, load_config, is_admin_request,
                  get_worker_alive, get_queue_paused, ollama_base,
                  base_dir, llm_response_cache, image_models_cache):
    global _get_db, _load_config, _is_admin_request
    global _get_worker_alive, _get_queue_paused, _OLLAMA_BASE, _BASE_DIR
    global _llm_response_cache, _image_models_cache
    global _get_ollama_status
    global _is_imac_available, IMAC_OLLAMA_BASE
    global LIGHTWEIGHT_MODELS
    _get_db = get_db
    _load_config = load_config
    _is_admin_request = is_admin_request
    _get_worker_alive = get_worker_alive
    _get_queue_paused = get_queue_paused
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir
    _llm_response_cache = llm_response_cache
    _image_models_cache = image_models_cache
    _get_ollama_status = get_ollama_status
    _is_imac_available = is_imac_available
    IMAC_OLLAMA_BASE = imac_ollama_base
    LIGHTWEIGHT_MODELS = lightweight_models


@activity_bp.route("/api/eval/pairs", methods=["POST"])
def api_eval_pairs_submit():
    """외부 호출자 답변 충실도 채점 제출 (비동기 — 야간 배치가 채점)
    ---
    tags: [System]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [source, pairs]
          properties:
            source: {type: string, example: cerno, description: "호출자 라벨 — 적재 시 ext: 접두가 붙어 자체 배치와 분리된다"}
            pairs:
              type: array
              description: "각 항목 {id, question, answer, evidence[], meta} — evidence는 **답변 생성에 실제 쓴 근거**. 요청당 최대 100건"
              items: {type: object}
    responses:
      200: {description: "접수 — accepted/rejected 건수와 사유"}
      400: {description: "source/pairs 누락 또는 유효 레코드 0"}
    """
    data = request.json or {}
    source = (data.get("source") or "").strip()
    pairs = data.get("pairs")
    if not source or not isinstance(pairs, list):
        return jsonify({"error": "source(string)와 pairs(array)가 필요합니다"}), 400

    from core.eval_pairs import parse_pairs
    # parse_pairs는 NDJSON 줄 단위 검증기 — dict 배열도 같은 규율로 통과시킨다
    # (한 건이 깨져도 나머지는 접수·사유는 돌려준다).
    records, errors = parse_pairs(json.dumps(p, ensure_ascii=False) for p in pairs)
    if not records:
        return jsonify({"error": "유효한 레코드가 없습니다", "rejected": errors}), 400

    # 야간 배치가 집어가도록 스풀에 적재한다(즉시 채점하지 않는다 — judge 호출이
    # 요청 지연·동시 슬롯을 잠식하므로 비동기 사후 샘플링이 합의된 형태).
    spool_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "eval_spool")
    os.makedirs(spool_dir, exist_ok=True)
    safe_source = re.sub(r"[^A-Za-z0-9_.-]", "_", source)[:32]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(spool_dir, f"{safe_source}-{day}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    try:
        os.chmod(path, 0o600)          # 답변·근거 텍스트 — 전 DB/파일 600 규율
    except OSError:
        pass
    logger.info("[eval] %s 쌍 %d건 접수(거절 %d) → %s", source, len(records), len(errors), path)
    return jsonify({"ok": True, "source": f"ext:{source}", "accepted": len(records),
                    "rejected": len(errors), "reasons": errors,
                    "scored_at": "다음 평가 배치(`eval_faithfulness.py --spool` — 야간 cron은 2026-08-04부터 꺼져 있어 온디맨드, EVAL_GUIDE §6) 후 /api/eval/status?source=%s 로 조회" % source})


#: 고정 추세 단절점 — 측정 **조건**이 바뀐 배치(그 ts부터 새 조건). 앞뒤 값을 이어 읽으면 안 된다.
#: 새 항목은 릴리즈 노트에 근거(같은 답변 재채점 이동 폭)를 적고 여기 추가한다.
TREND_BREAKS = [
    {"ts": "2026-07-16T00:00:00+00:00Z", "kind": "retrieval_space",
     "note": "거리 공간 L2→cosine(v2.124.8) — ctx_precision/sufficiency 이전 값 비교 불가"},
    {"ts": "2026-07-20T00:00:00+00:00Z", "kind": "verifier",
     "note": "교차검증 백엔드 bespoke-minicheck→qwen-entail — mc_agree/strict 기준 변경"},
    {"ts": "2026-08-18T23:32:46+00:00Z", "kind": "evidence_format",
     "note": "평가 근거에 법령명 라벨(v2.125.8 §7) — 같은 답변 재채점 충실도 0.9374→0.9511 · law_misattributed 이전 배치=null"},
]

#: 배치별 법령명 오귀속 집계 컬럼 — lm_n=축을 가진 행 수(0이면 미측정 배치), lm=플래그 행 수.
#: detail.law_misattributed 가 null(무효 처리)이거나 키가 없으면(구 배치) 둘 다 안 센다.
_LM_TREND_COLS = ("SUM(CASE WHEN json_type(detail,'$.law_misattributed')='array'"
                  " THEN 1 ELSE 0 END) lm_n,"
                  " SUM(CASE WHEN json_type(detail,'$.law_misattributed')='array'"
                  " AND json_array_length(detail,'$.law_misattributed')>0"
                  " THEN 1 ELSE 0 END) lm")


def _law_misattr(detail_json):
    """faithfulness_results.detail → law_misattributed 리스트(없으면/무효면 None).

    v2.125.8 축은 detail JSON에만 영속된다. null(무효 배치)·키 부재(구 배치)는 None으로
    돌려 "0건"과 "미측정"을 가른다 — 결측을 0으로 세면 축이 깨끗해 보인다.
    """
    if not detail_json:
        return None
    try:
        v = json.loads(detail_json).get("law_misattributed")
    except Exception:  # noqa: BLE001
        return None
    return list(v) if isinstance(v, list) else None


def _entail_prob(detail_json):
    """faithfulness_results.detail → 판정 확률 집계 dict(없으면 None). (P6-1 Phase 0)

    `law_misattributed`와 같은 규율: 구 배치(키 부재)·확률 미지원 백엔드(값 null)는
    **None**으로 돌려 "0건"과 "미측정"을 가른다.
    """
    if not detail_json:
        return None
    try:
        v = json.loads(detail_json).get("entail_prob")
    except Exception:  # noqa: BLE001
        return None
    return v if isinstance(v, dict) else None


def _confidence_summary(questions):
    """문항별 `entail_prob` → 배치 집계(없으면 None). 순수 함수. (P6-1 Phase 0)

    🔑 **평균은 주장 수 가중이다.** 문항별 평균을 단순 평균하면 주장이 1개인 문항이
    20개짜리와 같은 무게를 갖는다.
    🔑 `low`(0.35~0.65 = 판정자가 망설인 주장)는 **원문을 열어볼 대상 지목**이라
    합계가 맞다. `missing`은 확률을 못 뽑은 주장 수 — 분모에서 빠진 것이 몇 건인지
    보여야 평균을 믿을 수 있다.
    축을 가진 문항이 하나도 없으면 **None**(미측정 — "0건"이 아니다).
    """
    eps = [q.get("entail_prob") for q in (questions or [])]
    have = [e for e in eps if isinstance(e, dict) and (e.get("n") or 0) > 0]
    if not have:
        return None
    tot_n = sum(e["n"] for e in have)
    return {"n": tot_n,
            "mean": round(sum(e["mean"] * e["n"] for e in have) / tot_n, 4),
            "low": sum(e.get("low") or 0 for e in have),
            "missing": sum(e.get("missing") or 0 for e in eps if isinstance(e, dict)),
            "questions": len(have)}


@activity_bp.route("/api/eval/status")
def api_eval_status():
    """LLM 품질 평가 현황 — 최신 배치 집계·세밀 지표·시계열·종합 지수 (정본: docs/EVAL_GUIDE.md)
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        description: "평가 현황 (eval.db 없으면 available:false)"
        schema:
          type: object
          properties:
            available: {type: boolean}
            faithfulness: {type: object, description: "최신 배치 — ts·n·mean·hallucinations·mc_agree(교차검증 일치율)·mc_conflicts(리뷰 트리거)·strict·ctx_precision(검색 정밀도)·ctx_sufficiency(근거 충분성)·law_misattributed(법령명 오귀속 문항 수·judge가 못 보는 축·미측정=null)·confidence(판정 확률: n·mean(주장 가중)·low=망설인 주장 수 0.35~0.65·missing·questions. 미측정=null)·source·judge_model·questions[](id·faith·claims·교차검증·검색·law_misattributed[])"}
            quality: {type: object, description: "최신 배치 — overall·차원별(accuracy/helpfulness/safety/fluency)·korean_ratio·checks_rate·checks_failed_top(실패 항목 빈도=개선 지점)·questions[](차원별·korean_ratio 포함)"}
            judge_consistency: {type: object, description: "judge 자기일관성(주간) — flip_rate·overall_std·score_range_max·questions[](flipped 항목)"}
            quality_index: {type: object, description: "품질 종합 지수 qi-1.0 — score(0~100)·grade(A~D)·components·weights_used·version (내부 관리지표)"}
            trend_faithfulness: {type: array, description: "배치별 시계열(mean·strict·mc_agree·mc_conflicts·hallucinations)"}
            trend_quality: {type: array, description: "배치별 시계열(overall·차원별·checks_rate)"}
            trend_index: {type: array, description: "배치별 종합 지수(score·grade)"}
            benchmarks: {type: array, description: "모델별 최신 벤치 정답률(KorMedMCQA 등)"}
    """
    import sqlite3
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db")
    if not os.path.exists(db_path):
        return jsonify({"available": False})
    out = {"available": True}
    # source 필터 — 외부 호출자(예: Cerno) 답변을 자체 배치와 섞지 않고 따로 본다(v2.124.64).
    # 미지정이면 종전 동작(전 소스 통합) 그대로.
    src = (request.args.get("source") or "").strip()
    if src and not src.startswith("ext:"):
        src = "ext:" + src                          # 호출자는 접두 없이 불러도 된다
    if src:
        _sf, _sp = " AND source = ?", (src,)
        out["source_filter"] = src
    else:
        # 기본 화면은 **자체 배치만** 본다 — 외부 제출(ext:*)이 최신 배치를 차지하면
        # 대시보드와 품질 지수가 남의 답변 기준으로 바뀐다(라이브 실측으로 포착).
        _sf, _sp = " AND source NOT LIKE 'ext:%'", ()
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        # 충실도 — 최신 배치(ts) 집계
        r = conn.execute("""SELECT ts, COUNT(*) n, AVG(faithfulness) f, SUM(hallucination) h,
                            MAX(source) src, MAX(judge_model) jm
                            FROM faithfulness_results
                            WHERE ts=(SELECT MAX(ts) FROM faithfulness_results WHERE 1=1%s)%s"""
                         % (_sf, _sf), _sp * 2).fetchone()
        if r and r["n"]:
            out["faithfulness"] = {"ts": r["ts"], "n": r["n"],
                                   "mean": round(r["f"] or 0, 3), "hallucinations": r["h"],
                                   "source": r["src"], "judge_model": r["jm"]}
            # v2.120.0 세밀 지표 — MiniCheck 교차검증(컬럼은 러너 마이그레이션으로 존재,
            # 구 DB/미측정 배치는 NULL → None 유지)
            try:
                r2 = conn.execute(
                    """SELECT AVG(mc_agree) ma, SUM(mc_conflicts) mc, AVG(strict_faithfulness) sf
                       FROM faithfulness_results
                       WHERE ts=(SELECT MAX(ts) FROM faithfulness_results WHERE 1=1%s)%s
                       AND mc_agree IS NOT NULL""" % (_sf, _sf), _sp * 2).fetchone()
                if r2 and r2["ma"] is not None:
                    out["faithfulness"]["mc_agree"] = round(r2["ma"], 3)
                    out["faithfulness"]["mc_conflicts"] = int(r2["mc"] or 0)
                    out["faithfulness"]["strict"] = (round(r2["sf"], 3)
                                                     if r2["sf"] is not None else None)
                # v2.124.0 검색 품질(retrieval) — 최신 배치 평균
                r3 = conn.execute(
                    """SELECT AVG(ctx_precision) cp, AVG(ctx_sufficiency) cs
                       FROM faithfulness_results
                       WHERE ts=(SELECT MAX(ts) FROM faithfulness_results WHERE 1=1%s)%s
                       AND ctx_precision IS NOT NULL""" % (_sf, _sf), _sp * 2).fetchone()
                if r3 and r3["cp"] is not None:
                    out["faithfulness"]["ctx_precision"] = round(r3["cp"], 3)
                    out["faithfulness"]["ctx_sufficiency"] = (round(r3["cs"], 3)
                                                              if r3["cs"] is not None else None)
                # 질문별 세부(최신 배치) — 카드 드릴다운 테이블용
                rows2 = conn.execute(
                    """SELECT id, question, source, faithfulness, n_claims, n_contradicted,
                              mc_agree, mc_conflicts, strict_faithfulness,
                              ctx_precision, ctx_sufficiency, detail
                       FROM faithfulness_results
                       WHERE ts=(SELECT MAX(ts) FROM faithfulness_results WHERE 1=1%s)%s
                       ORDER BY id""" % (_sf, _sf), _sp * 2).fetchall()
                out["faithfulness"]["questions"] = [
                    {"id": x["id"], "q": x["question"], "source": x["source"],
                     "faith": x["faithfulness"], "claims": x["n_claims"],
                     "contradicted": x["n_contradicted"], "mc_agree": x["mc_agree"],
                     "mc_conflicts": x["mc_conflicts"], "strict": x["strict_faithfulness"],
                     "ctx_precision": x["ctx_precision"], "ctx_sufficiency": x["ctx_sufficiency"],
                     "law_misattributed": _law_misattr(x["detail"]),
                     "entail_prob": _entail_prob(x["detail"])}
                    for x in rows2]
                # v2.125.9 법령명 오귀속(v2.125.8 축) — 충실도 judge가 원리적으로 못 보는 축이라
                # 집계에 안 실으면 사람이 눈치채는 경로가 없다(DB detail JSON에만 있었다).
                # 값=플래그된 문항 수. 축이 없는(구) 배치·무효 처리된 배치는 None.
                lm = [qq["law_misattributed"] for qq in out["faithfulness"]["questions"]]
                out["faithfulness"]["law_misattributed"] = (
                    sum(1 for v in lm if v) if any(v is not None for v in lm) else None)
                # P6-1 Phase 0 판정 확률 — 주장 수로 가중한 평균 + 망설인 주장 수.
                # 🔑 **평균은 주장 가중이다**: 문항별 평균을 단순 평균하면 주장이 1개인
                # 문항이 20개짜리와 같은 무게를 갖는다. low(0.35~0.65)는 원문을 열어볼
                # 대상 지목이라 합계가 맞다. 축을 가진 문항이 하나도 없으면 None.
                out["faithfulness"]["confidence"] = _confidence_summary(
                    out["faithfulness"]["questions"])
            except Exception:  # noqa: BLE001 — 구 스키마 graceful
                pass
        # 품질 — 최신 배치 집계
        r = conn.execute("""SELECT ts, COUNT(*) n, AVG(overall) o, AVG(accuracy) a, AVG(helpfulness) hp,
                            AVG(safety) s, AVG(fluency) fl, AVG(korean_ratio) k, MAX(judge_model) jm
                            FROM quality_results WHERE ts=(SELECT MAX(ts) FROM quality_results)"""
                         ).fetchone()
        if r and r["n"]:
            out["quality"] = {"ts": r["ts"], "n": r["n"], "overall": round(r["o"] or 0, 2),
                              "accuracy": round(r["a"] or 0, 1), "helpfulness": round(r["hp"] or 0, 1),
                              "safety": round(r["s"] or 0, 1), "fluency": round(r["fl"] or 0, 1),
                              "korean_ratio": round(r["k"] or 0, 2), "judge_model": r["jm"]}
            # v2.120.0 세밀 지표 — 체크리스트(12 이진 항목) 통과율 + 실패 항목 빈도
            try:
                rows2 = conn.execute(
                    """SELECT id, question, overall, accuracy, helpfulness, safety, fluency,
                              korean_ratio, checks_rate, checks_failed FROM quality_results
                       WHERE ts=(SELECT MAX(ts) FROM quality_results) ORDER BY id""").fetchall()
                rates = [x["checks_rate"] for x in rows2 if x["checks_rate"] is not None]
                if rates:
                    out["quality"]["checks_rate"] = round(sum(rates) / len(rates), 3)
                    failed_freq = {}
                    for x in rows2:
                        for item in (x["checks_failed"] or "").split(","):
                            if item.strip():
                                failed_freq[item.strip()] = failed_freq.get(item.strip(), 0) + 1
                    out["quality"]["checks_failed_top"] = sorted(
                        failed_freq.items(), key=lambda kv: -kv[1])[:6]
                    out["quality"]["questions"] = [
                        {"id": x["id"], "q": x["question"], "overall": x["overall"],
                         "accuracy": x["accuracy"], "helpfulness": x["helpfulness"],
                         "safety": x["safety"], "fluency": x["fluency"],
                         "korean_ratio": x["korean_ratio"],
                         "checks_rate": x["checks_rate"],
                         "checks_failed": [i for i in (x["checks_failed"] or "").split(",") if i]}
                        for x in rows2]
            except Exception:  # noqa: BLE001 — 구 스키마 graceful
                pass
        # 벤치 — 모델별 최신 정답률
        rows = conn.execute("""SELECT model, benchmark, accuracy, n, ts FROM eval_results e
                               WHERE id=(SELECT MAX(id) FROM eval_results WHERE model=e.model AND benchmark=e.benchmark)
                               ORDER BY accuracy DESC LIMIT 8""").fetchall()
        out["benchmarks"] = [dict(r) for r in rows]
        # 추세 — 배치(ts)별 집계, 최근 30배치. A/B 실험(source LIKE 'ab-%')은 프로덕션 추세서 제외
        # v2.120.2: 세밀 지표(strict·mc_agree·mc_conflicts·차원별·checks_rate) 시계열 합류
        # (미측정 배치는 NULL → 프론트가 결측 구간으로 표시)
        try:
            rows = conn.execute("""SELECT ts, AVG(faithfulness) f, SUM(hallucination) h, COUNT(*) n,
                                   AVG(strict_faithfulness) sf, AVG(mc_agree) ma, SUM(mc_conflicts) mc,
                                   AVG(ctx_precision) cp, AVG(ctx_sufficiency) cs,
                                   %s
                                   FROM faithfulness_results WHERE source NOT LIKE 'ab-%%'
                                   GROUP BY ts ORDER BY ts DESC LIMIT 30""" % _LM_TREND_COLS).fetchall()
            out["trend_faithfulness"] = [
                {"ts": r["ts"], "mean": round(r["f"] or 0, 3), "hallucinations": r["h"], "n": r["n"],
                 "strict": round(r["sf"], 3) if r["sf"] is not None else None,
                 "mc_agree": round(r["ma"], 3) if r["ma"] is not None else None,
                 "mc_conflicts": int(r["mc"]) if r["mc"] is not None else None,
                 "ctx_precision": round(r["cp"], 3) if r["cp"] is not None else None,
                 "ctx_sufficiency": round(r["cs"], 3) if r["cs"] is not None else None,
                 # 법령명 오귀속 문항 수 — 축이 없는 배치(구 detail·무효 처리 null)는 None
                 "law_misattributed": int(r["lm"]) if r["lm_n"] else None}
                for r in reversed(rows)]
        except Exception:  # noqa: BLE001 — 구 스키마(세밀 컬럼 부재) 폴백
            rows = conn.execute("""SELECT ts, AVG(faithfulness) f, SUM(hallucination) h, COUNT(*) n
                                   FROM faithfulness_results WHERE source NOT LIKE 'ab-%'
                                   GROUP BY ts ORDER BY ts DESC LIMIT 30""").fetchall()
            out["trend_faithfulness"] = [{"ts": r["ts"], "mean": round(r["f"] or 0, 3),
                                          "hallucinations": r["h"], "n": r["n"]} for r in reversed(rows)]
        try:
            rows = conn.execute("""SELECT ts, AVG(overall) o, AVG(accuracy) a, AVG(helpfulness) hp,
                                   AVG(safety) s, AVG(fluency) fl, AVG(checks_rate) cr
                                   FROM quality_results
                                   WHERE source NOT LIKE 'ab-%' GROUP BY ts ORDER BY ts DESC LIMIT 30""").fetchall()
            out["trend_quality"] = [
                {"ts": r["ts"], "overall": round(r["o"] or 0, 2),
                 "accuracy": round(r["a"], 2) if r["a"] is not None else None,
                 "helpfulness": round(r["hp"], 2) if r["hp"] is not None else None,
                 "safety": round(r["s"], 2) if r["s"] is not None else None,
                 "fluency": round(r["fl"], 2) if r["fl"] is not None else None,
                 "checks_rate": round(r["cr"], 3) if r["cr"] is not None else None}
                for r in reversed(rows)]
        except Exception:  # noqa: BLE001 — 구 스키마 폴백
            rows = conn.execute("""SELECT ts, AVG(overall) o FROM quality_results
                                   WHERE source NOT LIKE 'ab-%' GROUP BY ts ORDER BY ts DESC LIMIT 30""").fetchall()
            out["trend_quality"] = [{"ts": r["ts"], "overall": round(r["o"] or 0, 2)} for r in reversed(rows)]
        # judge 단절점 (v2.124.95) — judge가 바뀐 배치 앞뒤는 **추세를 이어 읽으면 안 된다**.
        # 지금은 judge가 피평가 모델과 같은 계열이라(qwen2.5:14b) 모델을 바꾸면 기준도
        # 같이 바뀐다. 화면이 그 지점을 표시할 수 있도록 ts를 돌려준다.
        try:
            from core import judge_config
            jm_rows = conn.execute(
                """SELECT ts, judge_model FROM quality_results
                   WHERE source NOT LIKE 'ab-%' GROUP BY ts ORDER BY ts LIMIT 200""").fetchall()
            out["judge"] = {
                "current": judge_config.judge_model("quality"),
                "pinned": judge_config.is_pinned(),
                "breaks": judge_config.trend_breaks([dict(r) for r in jm_rows]),
            }
        except Exception as e:  # noqa: BLE001 — 부가 정보, 상태 조회를 막지 않는다
            logger.debug("judge breaks skipped: %s", e)
        # 추세 단절점 통합(v2.125.11) — judge 교체(동적)와 **평가 근거 표현 변경 같은 고정 사건**을 한 목록으로.
        # 화면은 이 목록을 추세 아래에 적는다. 앞뒤를 그냥 이어 읽으면 안 되는 지점이다.
        try:
            breaks = [{"ts": b["ts"], "kind": "judge", "note": "judge %s→%s" % (b["from"], b["to"])}
                      for b in (out.get("judge") or {}).get("breaks") or []]
            breaks += [dict(b) for b in TREND_BREAKS]
            out["trend_breaks"] = sorted(breaks, key=lambda b: b["ts"])
        except Exception as e:  # noqa: BLE001
            logger.debug("trend breaks skipped: %s", e)
        # v2.124.0 judge 자기일관성(meta-eval) — 최신 배치 요약(주간 측정)
        try:
            r = conn.execute(
                """SELECT ts, COUNT(*) n, AVG(checks_flip_rate) fr, AVG(overall_std) os,
                   MAX(score_range_max) rng
                   FROM judge_consistency WHERE ts=(SELECT MAX(ts) FROM judge_consistency)"""
            ).fetchone()
            if r and r["n"]:
                out["judge_consistency"] = {
                    "ts": r["ts"], "n": r["n"],
                    "flip_rate": round(r["fr"], 3) if r["fr"] is not None else None,
                    "overall_std": round(r["os"], 3) if r["os"] is not None else None,
                    "score_range_max": r["rng"]}
                rows_jc = conn.execute(
                    """SELECT question, runs, overall_mean, overall_std, checks_flip_rate,
                              flipped_items FROM judge_consistency
                       WHERE ts=(SELECT MAX(ts) FROM judge_consistency) ORDER BY id""").fetchall()
                out["judge_consistency"]["questions"] = [
                    {"q": x["question"], "runs": x["runs"], "overall_mean": x["overall_mean"],
                     "overall_std": x["overall_std"], "flip_rate": x["checks_flip_rate"],
                     "flipped": [i for i in (x["flipped_items"] or "").split(",") if i]}
                    for x in rows_jc]
        except Exception:  # noqa: BLE001 — 테이블 미존재(미측정) graceful
            pass
        # v2.123.0 품질 종합 지수(qi-1.0) — 최신 배치 + 배치별 시계열(내부 관리지표)
        try:
            from core.eval_score import index_from_batches
            out["quality_index"] = index_from_batches(out.get("faithfulness"), out.get("quality"))
            # 시계열: faith 배치 기준, quality는 같은 시간대(ts 시간 버킷) 배치를 조인
            # (야간 cron이 연달아 실행=대개 같은 시각대). 미조인=faith 구성만(재정규화).
            q_by_hour = {}
            for r in out.get("trend_quality") or []:
                q_by_hour[(r.get("ts") or "")[:13]] = r
            trend_idx = []
            for r in out.get("trend_faithfulness") or []:
                qrow = q_by_hour.get((r.get("ts") or "")[:13])
                qi = index_from_batches(
                    {"mean": r.get("mean"), "hallucinations": r.get("hallucinations"),
                     "n": r.get("n"), "strict": r.get("strict")},
                    {"overall": qrow.get("overall"), "checks_rate": qrow.get("checks_rate"),
                     "korean_ratio": None} if qrow else None)
                if qi:
                    trend_idx.append({"ts": r.get("ts"), "score": qi["score"], "grade": qi["grade"]})
            out["trend_index"] = trend_idx
        except Exception as e:  # noqa: BLE001
            logger.debug("quality index compute failed: %s", e)
        conn.close()
    except Exception as e:
        logger.debug("eval status read failed: %s", e)
        out["error"] = "read_failed"
    return jsonify(out)


@activity_bp.route("/api/eval/detail")
def api_eval_detail():
    """평가 결과 단건 상세 — 답변 전문·주장별 판정(교차검증)·차원 사유·체크 항목.

    쿼리: kind=faithfulness|quality, id=행 ID. v2.121.0 이후 배치만 detail 보유
    (이전 행은 detail=null — 집계만).
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
      404:
        description: "행 없음"
    """
    import sqlite3
    import json as _json
    kind = request.args.get("kind", "faithfulness")
    table = {"faithfulness": "faithfulness_results", "quality": "quality_results"}.get(kind)
    rid = request.args.get("id", type=int)
    if not table or not rid:
        return jsonify({"error": "kind(faithfulness|quality)+id required"}), 400
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db")
    if not os.path.exists(db_path):
        return jsonify({"error": "no eval.db"}), 404
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    d = dict(row)
    try:
        d["detail"] = _json.loads(d["detail"]) if d.get("detail") else None
    except Exception:  # noqa: BLE001
        d["detail"] = None
    return jsonify({"kind": kind, "row": d})


@activity_bp.route("/api/eval/history")
def api_eval_history():
    """질문별 배치 이력 — 같은 질문의 최근 N배치 충실도/품질 추이(확대 검토용).

    쿼리: question(정확 일치), limit(기본 20).
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    import sqlite3
    question = (request.args.get("question") or "").strip()
    limit = max(1, min(request.args.get("limit", type=int) or 20, 100))
    if not question:
        return jsonify({"error": "question required"}), 400
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db")
    if not os.path.exists(db_path):
        return jsonify({"error": "no eval.db"}), 404
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    out = {"question": question}
    try:
        rows = conn.execute(
            """SELECT id, ts, faithfulness, hallucination, n_claims, n_contradicted,
                      mc_agree, mc_conflicts, strict_faithfulness
               FROM faithfulness_results WHERE question=? ORDER BY id DESC LIMIT ?""",
            (question, limit)).fetchall()
        out["faithfulness"] = [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        out["faithfulness"] = []
    try:
        rows = conn.execute(
            """SELECT id, ts, overall, accuracy, helpfulness, safety, fluency,
                      checks_rate, checks_failed
               FROM quality_results WHERE question=? ORDER BY id DESC LIMIT ?""",
            (question, limit)).fetchall()
        out["quality"] = [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        out["quality"] = []
    conn.close()
    return jsonify(out)


@activity_bp.route("/api/cache/stats")
def api_cache_stats():
    """Cache statistics
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    return jsonify({
        "llm_response_cache": {"size": len(_llm_response_cache), "max": 500, "ttl": 3600},
        "image_models_cache": {"size": len(_image_models_cache), "max": 1, "ttl": 300},
    })

@activity_bp.route("/api/imac/status")
def api_imac_status():
    """iMac Ollama connection status
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            available: {type: boolean}
            base_url: {type: string}
            lightweight_models: {type: array, items: {type: string}}
            models: {type: array, items: {type: object}}
    """
    available = _is_imac_available()
    models = []
    if available:
        try:
            r = http_requests.get(f"{IMAC_OLLAMA_BASE}/api/tags", timeout=5)
            models = [{"name": m["name"], "size_gb": round(m["size"]/1e9, 1)}
                      for m in r.json().get("models", [])]
        except Exception as e:
            logger.debug("iMac model list fetch failed: %s", e)
    return jsonify({
        "available": available,
        "base_url": IMAC_OLLAMA_BASE,
        "lightweight_models": sorted(LIGHTWEIGHT_MODELS),
        "models": models,
    })




@activity_bp.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Clear all caches
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    _llm_response_cache.clear()
    _image_models_cache.clear()
    return jsonify({"status": "cleared"})


# ---------------------------------------------------------------------------
# HR Law API — 근태/인사 법률 질의응답
# ---------------------------------------------------------------------------



# API Activity Log
# ---------------------------------------------------------------------------

@activity_bp.route("/api/activity")
def api_activity():
    """Get API activity log
    ---
    tags: [Config]
    security: [{Bearer: []}]
    parameters:
      - {name: limit,  in: query, type: integer, default: 100}
      - {name: offset, in: query, type: integer, default: 0}
      - {name: method, in: query, type: string,  description: "GET/POST/DELETE"}
      - {name: path,   in: query, type: string,  description: "Path contains filter"}
      - {name: status, in: query, type: integer, description: "HTTP status code"}
      - {name: model,  in: query, type: string}
    responses:
      200:
        schema:
          type: object
          properties:
            logs: {type: array, items: {type: object}}
            total: {type: integer}
            stats: {type: object}
    """
    try:
        limit  = min(int(request.args.get("limit",  200)), 500)
        offset = max(int(request.args.get("offset", 0)),   0)
    except ValueError:
        return jsonify({"error": "limit/offset must be integers"}), 400

    method  = request.args.get("method", "").upper()
    path_q  = request.args.get("path", "")
    status  = request.args.get("status", "")
    model_q = request.args.get("model", "")

    where, params = [], []
    if method:
        where.append("method=?");      params.append(method)
    if path_q:
        where.append("path LIKE ? ESCAPE '\\'");   params.append(f"%{_escape_like(path_q)}%")
    if status:
        try:
            where.append("status_code=?"); params.append(int(status))
        except ValueError:
            logger.debug("Invalid status filter: %s", status)
    if model_q:
        where.append("model LIKE ? ESCAPE '\\'");  params.append(f"%{_escape_like(model_q)}%")

    clause = " AND ".join(where) if where else "1=1"
    conn = _get_db()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM api_activity WHERE {clause}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM api_activity WHERE {clause} ORDER BY ts DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        # 요약 통계 (최근 1시간)
        import datetime as _dt
        hour_ago = (datetime.now(timezone.utc) - _dt.timedelta(hours=1)).isoformat() + "Z"
        stats_row = conn.execute(
            "SELECT COUNT(*) as calls, AVG(duration_ms) as avg_ms, "
            "SUM(CASE WHEN status_code>=500 THEN 1 ELSE 0 END) as errors "
            "FROM api_activity WHERE ts > ?", (hour_ago,)
        ).fetchone()
        top_paths = conn.execute(
            "SELECT path, COUNT(*) as cnt FROM api_activity WHERE ts > ? "
            "GROUP BY path ORDER BY cnt DESC LIMIT 5", (hour_ago,)
        ).fetchall()
    finally:
        conn.close()

    return jsonify({
        "logs": [dict(r) for r in rows],
        "total": total,
        "stats": {
            "calls_1h":     stats_row["calls"] if stats_row else 0,
            "avg_ms_1h":    round(stats_row["avg_ms"] or 0, 1),
            "errors_1h":    stats_row["errors"] if stats_row else 0,
            "top_paths_1h": [dict(r) for r in top_paths],
        },
    })


@activity_bp.route("/api/activity/servers")
def api_activity_servers():
    """Get activity statistics grouped by API key (calling server)
    ---
    tags: [Config]
    security: [{Bearer: []}]
    parameters:
      - {name: hours, in: query, type: integer, default: 24, description: "Period in hours"}
    responses:
      200:
        schema:
          type: object
          properties:
            servers: {type: array, items: {type: object}}
            period_hours: {type: integer}
    """
    import datetime as _dt
    try:
        hours = min(int(request.args.get("hours", 24)), 720)
    except ValueError:
        return jsonify({"error": "hours must be integer"}), 400

    since = (datetime.now(timezone.utc) - _dt.timedelta(hours=hours)).isoformat() + "Z"
    conn = _get_db()
    try:
        # API 키(key_name)별 집계 — key_name이 NULL이면 IP로 fallback
        servers = conn.execute("""
        SELECT
            COALESCE(key_name, ip, 'unknown')                             AS key_name,
            GROUP_CONCAT(DISTINCT ip)                                     AS ips,
            COUNT(*)                                                      AS total_calls,
            SUM(CASE WHEN status_code < 400 THEN 1 ELSE 0 END)           AS success,
            SUM(CASE WHEN status_code >= 400 AND status_code < 500 THEN 1 ELSE 0 END) AS client_errors,
            SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END)          AS server_errors,
            ROUND(AVG(duration_ms), 1)                                    AS avg_ms,
            MAX(duration_ms)                                              AS max_ms,
            MIN(duration_ms)                                              AS min_ms,
            MAX(ts)                                                       AS last_seen,
            MIN(ts)                                                       AS first_seen,
            SUM(request_size)                                             AS total_req_bytes,
            SUM(response_size)                                            AS total_res_bytes
        FROM api_activity
        WHERE ts > ?
        GROUP BY COALESCE(key_name, ip, 'unknown')
        ORDER BY total_calls DESC
        """, (since,)).fetchall()

        result = []
        bucket_hours = max(1, hours // 6)
        now_iso = datetime.now(timezone.utc).isoformat() + "Z"
        for s in servers:
            key = s["key_name"]
            # 해당 클라이언트의 Top 5 경로
            top_paths = conn.execute("""
                SELECT path, method,
                    COUNT(*) AS cnt,
                    ROUND(AVG(duration_ms),1) AS avg_ms,
                    SUM(CASE WHEN status_code>=400 THEN 1 ELSE 0 END) AS errors
                FROM api_activity
                WHERE COALESCE(key_name, ip, 'unknown')=? AND ts>?
                GROUP BY path, method
                ORDER BY cnt DESC LIMIT 5
            """, (key, since)).fetchall()

            # 시간대별 호출 분포 (6개 구간)
            timeline = conn.execute("""
                SELECT
                    CAST((julianday(?) - julianday(ts)) * 24 / ? AS INTEGER) AS bucket,
                    COUNT(*) AS cnt
                FROM api_activity
                WHERE COALESCE(key_name, ip, 'unknown')=? AND ts>?
                GROUP BY bucket ORDER BY bucket
            """, (now_iso, bucket_hours, key, since)).fetchall()

            row = dict(s)
            row["success_rate"] = round(s["success"] / s["total_calls"] * 100, 1) if s["total_calls"] else 0
            row["top_paths"] = [dict(p) for p in top_paths]
            row["timeline"] = [dict(t) for t in timeline]
            result.append(row)
    finally:
        conn.close()

    return jsonify({"servers": result, "period_hours": hours})

# ---------------------------------------------------------------------------
# Prometheus Metrics


# ═══════════════════════════════════════════════════════════════════════════════
# 시스템 환경 감지 + 자동 설정
# ═══════════════════════════════════════════════════════════════════════════════

import platform as _platform

def _detect_system():
    """시스템 환경 자동 감지"""
    info = {
        "os": _platform.system(),           # Linux / Darwin
        "os_version": _platform.release(),
        "arch": _platform.machine(),        # x86_64 / arm64
        "hostname": _platform.node(),
        "python": _platform.python_version(),
        "cpu_count": os.cpu_count() or 1,
        "is_linux": _platform.system() == "Linux",
        "is_macos": _platform.system() == "Darwin",
        "is_arm": _platform.machine() in ("arm64", "aarch64"),
    }

    # RAM
    try:
        if info["is_linux"]:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        info["ram_gb"] = round(int(line.split()[1]) / 1024 / 1024, 1)
                        break
        else:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                info["ram_gb"] = round(int(r.stdout.strip()) / 1024**3, 1)
    except Exception:
        info["ram_gb"] = 0

    # GPU
    info["gpu_type"] = "none"
    info["gpu_name"] = None
    info["gpu_vram_gb"] = 0

    # NVIDIA (CUDA)
    import shutil as _shutil
    try:
        if _shutil.which("nvidia-smi"):
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                info["gpu_type"] = "cuda"
                info["gpu_name"] = parts[0]
                info["gpu_vram_gb"] = round(int(parts[1]) / 1024, 1)
    except Exception as e:
        logger.debug("NVIDIA GPU detection failed: %s", e)

    # Apple Metal (sysctl 기반 — system_profiler보다 빠르고 안정적)
    if info["is_macos"] and info["gpu_type"] == "none":
        try:
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3
            )
            chip = r.stdout.strip() if r.returncode == 0 else ""
            if not chip:
                # 폴백: hw.model
                r2 = subprocess.run(
                    ["sysctl", "-n", "hw.model"],
                    capture_output=True, text=True, timeout=3
                )
                chip = r2.stdout.strip() if r2.returncode == 0 else ""
            if "Apple" in chip or chip.startswith("Mac"):
                # GPU 코어 수 (ioreg — sudo 불필요)
                gpu_cores = 0
                try:
                    rg = subprocess.run(
                        ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                        capture_output=True, text=True, timeout=2
                    )
                    # GPU 코어는 별도 감지 — chip명에서 추정
                    import re as _re
                    m = _re.search(r'M\d+', chip)
                    if m:
                        gpu_cores = 8  # M1 기본값 (정확한 값은 apple_silicon.py에서)
                except Exception:
                    pass
                info["gpu_type"] = "metal"
                info["gpu_name"] = chip if chip else "Apple Silicon"
                info["gpu_vram_gb"] = info["ram_gb"]   # Unified Memory
                info["gpu_cores"] = gpu_cores
        except Exception as e:
            logger.debug("Apple Metal detection failed: %s", e)

    # 권장 설정 계산
    info["recommended"] = _calc_recommended(info)

    return info

def _calc_recommended(info):
    """환경 기반 권장 설정"""
    rec = {}
    vram = info.get("gpu_vram_gb", 0)
    ram = info.get("ram_gb", 0)
    gpu = info.get("gpu_type", "none")

    # 권장 모델
    if gpu == "cuda":
        if vram >= 12: rec["model"] = "qwen2.5:14b"
        elif vram >= 8: rec["model"] = "qwen2.5:7b"
        elif vram >= 4: rec["model"] = "qwen2.5:3b"
        else: rec["model"] = "qwen2.5:1.5b"
    elif gpu == "metal":
        if ram >= 32: rec["model"] = "qwen2.5:14b"
        elif ram >= 16: rec["model"] = "qwen2.5:7b"
        elif ram >= 8: rec["model"] = "qwen2.5:3b"
        else: rec["model"] = "qwen2.5:1.5b"
    else:
        rec["model"] = "qwen2.5:1.5b"

    # 병렬 수
    if gpu == "cuda": rec["num_parallel"] = 4
    elif gpu == "metal": rec["num_parallel"] = 2
    else: rec["num_parallel"] = 1

    # Flash Attention
    rec["flash_attention"] = gpu == "cuda"

    # KV Cache
    rec["kv_cache"] = "q8_0" if gpu == "cuda" else "f16"

    # Keep alive
    rec["keep_alive"] = "-1"  # 영구

    # 학습 가능 여부
    if gpu == "cuda":
        rec["training_capable"] = vram >= 4
        rec["training_model"] = "qwen2.5:1.5b" if vram < 8 else ("qwen2.5:3b" if vram < 12 else "qwen2.5:7b")
    elif gpu == "metal":
        rec["training_capable"] = ram >= 16
        rec["training_model"] = "qwen2.5:1.5b" if ram < 32 else "qwen2.5:3b"
    else:
        rec["training_capable"] = False
        rec["training_model"] = None

    return rec

# 앱 시작 시 감지
_SYSTEM_INFO = _detect_system()
logger.info(f"System: {_SYSTEM_INFO['os']} {_SYSTEM_INFO['arch']} | "
            f"GPU: {_SYSTEM_INFO['gpu_type']} ({_SYSTEM_INFO.get('gpu_name','none')}) | "
            f"RAM: {_SYSTEM_INFO['ram_gb']}GB | "
            f"Recommended model: {_SYSTEM_INFO['recommended']['model']}")


# ═══════════════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------

@activity_bp.route("/metrics")
def prometheus_metrics():
    """Prometheus metrics endpoint
    ---
    tags: [Config]
    responses:
      200:
        description: Prometheus text format
    """
    conn = _get_db()
    try:
        counts = {}
        for s in ("pending", "running", "completed", "failed"):
            counts[s] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (s,)).fetchone()[0]

        row = conn.execute("""
            SELECT COALESCE(SUM(tokens),0) as total_tokens,
                   COALESCE(SUM(duration_ms),0) as total_duration_ms,
                   COUNT(*) as total_completed
            FROM jobs WHERE status='completed' AND tokens > 0
        """).fetchone()
        total_tokens = row["total_tokens"]
        total_duration_ms = row["total_duration_ms"]
        total_completed = row["total_completed"]

        model_rows = conn.execute("""
            SELECT model, COUNT(*) as cnt, COALESCE(SUM(tokens),0) as tokens
            FROM jobs WHERE status='completed' GROUP BY model
        """).fetchall()
        # 성능 모니터링 (P1/P2): 수집기가 박제한 최신 스냅샷 (≤5min, spawn 없음)
        perf_row = conn.execute(
            "SELECT vram_used_mb,vram_total_mb,gpu_util_pct,gpu_temp_c,worker_threads,"
            "worker_threads_peak,sse_subscribers,worker_recycles,worker_uptime_s "
            "FROM stats_history ORDER BY ts DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    ollama_up = 1 if _get_ollama_status() else 0

    lines = [
        "# HELP ollama_queue_jobs_total Total jobs by status",
        "# TYPE ollama_queue_jobs_total gauge",
    ]
    for s, v in counts.items():
        lines.append(f'ollama_queue_jobs_total{{status="{s}"}} {v}')

    lines += [
        "# HELP ollama_queue_tokens_total Total tokens generated",
        "# TYPE ollama_queue_tokens_total counter",
        f"ollama_queue_tokens_total {total_tokens}",
        "# HELP ollama_queue_duration_ms_total Total processing duration in ms",
        "# TYPE ollama_queue_duration_ms_total counter",
        f"ollama_queue_duration_ms_total {total_duration_ms}",
        "# HELP ollama_queue_completed_total Total completed jobs",
        "# TYPE ollama_queue_completed_total counter",
        f"ollama_queue_completed_total {total_completed}",
        "# HELP ollama_worker_alive Worker thread status",
        "# TYPE ollama_worker_alive gauge",
        f"ollama_worker_alive {1 if _get_worker_alive() else 0}",
        "# HELP ollama_queue_paused Queue paused status",
        "# TYPE ollama_queue_paused gauge",
        f"ollama_queue_paused {1 if _get_queue_paused() else 0}",
        "# HELP ollama_engine_up Ollama engine running",
        "# TYPE ollama_engine_up gauge",
        f"ollama_engine_up {ollama_up}",
    ]

    if model_rows:
        lines += [
            "# HELP ollama_model_jobs_total Completed jobs per model",
            "# TYPE ollama_model_jobs_total counter",
        ]
        for mr in model_rows:
            lines.append(f'ollama_model_jobs_total{{model="{mr["model"]}"}} {mr["cnt"]}')
        lines += [
            "# HELP ollama_model_tokens_total Tokens per model",
            "# TYPE ollama_model_tokens_total counter",
        ]
        for mr in model_rows:
            lines.append(f'ollama_model_tokens_total{{model="{mr["model"]}"}} {mr["tokens"]}')

    # ── 성능 모니터링 메트릭 (P1/P2) ──
    # 지연/에러율: 인메모리 리저버(live). GPU/스레드/SSE: 수집기 최신 스냅샷(≤5min).
    try:
        lat = metrics_reservoir.quantiles()
        err = metrics_reservoir.error_rate()
        up_err = metrics_reservoir.upstream_error_rate()
        lines += [
            "# HELP ollama_http_latency_ms HTTP request latency percentiles (rolling reservoir)",
            "# TYPE ollama_http_latency_ms gauge",
            f'ollama_http_latency_ms{{quantile="0.5"}} {int(lat["p50"])}',
            f'ollama_http_latency_ms{{quantile="0.95"}} {int(lat["p95"])}',
            f'ollama_http_latency_ms{{quantile="0.99"}} {int(lat["p99"])}',
            # 워커 기동 이후 누적(max_requests=5000 재활용마다 리셋) — 창 기반 아님.
            "# HELP ollama_http_error_rate_pct Own 5xx rate pct since worker start (excludes upstream proxy failures)",
            "# TYPE ollama_http_error_rate_pct gauge",
            f"ollama_http_error_rate_pct {err}",
            # 원격 노드(gpu/prod/his/mac) 프록시 미도달 — 이 서버의 건전성과 구분해 본다.
            "# HELP ollama_http_upstream_error_rate_pct Remote-node proxy failure rate pct since worker start",
            "# TYPE ollama_http_upstream_error_rate_pct gauge",
            f"ollama_http_upstream_error_rate_pct {up_err}",
        ]
        for cls, n in metrics_reservoir.status_counts().items():
            lines.append(f'ollama_http_requests_total{{status="{cls}"}} {n}')
        # 스레드 풀 포화 (v2.124.94) — 고갈의 실제 신호. worker_threads는 관측용.
        flight = metrics_reservoir.inflight()
        lines += [
            "# HELP ollama_inflight_requests Requests currently being handled (thread pool occupancy)",
            "# TYPE ollama_inflight_requests gauge",
            f"ollama_inflight_requests {flight['current']}",
            "# HELP ollama_inflight_requests_peak In-flight peak since last collector snapshot",
            "# TYPE ollama_inflight_requests_peak gauge",
            f"ollama_inflight_requests_peak {flight['peak']}",
        ]
        _pool = os.environ.get("GUNICORN_THREADS")
        if _pool and _pool.isdigit():
            lines += [
                "# HELP ollama_worker_thread_pool Configured gthread pool size (saturation denominator)",
                "# TYPE ollama_worker_thread_pool gauge",
                f"ollama_worker_thread_pool {int(_pool)}",
            ]
    except Exception as e:
        logger.debug("reservoir metrics skipped: %s", e)

    if perf_row:
        def _g(name, val, help_txt):
            if val is not None:
                lines.extend([f"# HELP {name} {help_txt}", f"# TYPE {name} gauge", f"{name} {val}"])
        _g("ollama_gpu_vram_used_mb", perf_row["vram_used_mb"], "GPU VRAM used MB")
        _g("ollama_gpu_vram_total_mb", perf_row["vram_total_mb"], "GPU VRAM total MB")
        _g("ollama_gpu_util_pct", perf_row["gpu_util_pct"], "GPU utilization pct")
        _g("ollama_gpu_temp_c", perf_row["gpu_temp_c"], "GPU temperature C")
        _g("ollama_worker_threads", perf_row["worker_threads"], "Worker thread count (nlwp)")
        _g("ollama_worker_threads_peak", perf_row["worker_threads_peak"], "Worker thread peak since last snapshot")
        _g("ollama_sse_subscribers", perf_row["sse_subscribers"], "Active SSE monitor subscribers")
        _g("ollama_worker_recycles_total", perf_row["worker_recycles"], "Cumulative worker boots (churn)")
        _g("ollama_worker_uptime_seconds", perf_row["worker_uptime_s"], "Current worker uptime seconds")

    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4; charset=utf-8")

