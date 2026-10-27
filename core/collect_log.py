"""외부 정본 데이터 수집 로그 + 주기 수집보고서.

HIRA 게시판 동기화(약제급여목록·저가약대체조제)·data.go.kr API(치료재료·DUR 8유형·
e약은요)의 **모든 수집 시도**를 이력으로 남기고(collect_log — 기존 *_sync_meta는
소스별 최신 상태만 유지), 주기적으로 집계 보고서(collect_reports)를 생성한다.

- 로그 적재: 각 sync의 _record 훅에서 best-effort 호출(수집 실패에 영향 0)
- 보고서: 기간 내 소스별 시도/성공/skip/오류 집계 + 신규판 적재 내역 + 현재 정본
  규모(fee_codes·대체조제·DUR) 스냅샷 + 요약문. 스케줄러 주기 생성(_due 게이트 =
  워커 churn 가로질러 cadence 보장) + 수동 생성 API.
- 로그 관리: retention purge(기본 365일, config collect_log_retention_days)
  — 보고서 생성 시 자동 + 수동 API.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("ollama-admin")

_DDL = """
CREATE TABLE IF NOT EXISTS collect_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT,               -- UTC isoformat
  source     TEXT,               -- 약제급여목록·저가약대체조제·치료재료·병용금기·e약은요 …
  kind       TEXT,               -- hira-board | datago-api
  status     TEXT,               -- ok | skip | error | denied
  count      INTEGER,            -- 적재/수집 건수
  edition    TEXT,               -- 적재 판 식별자(있으면)
  detail     TEXT,               -- 게시글 제목 등 부가정보
  duration_s REAL,
  error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_collect_log_ts ON collect_log(ts);
CREATE INDEX IF NOT EXISTS idx_collect_log_source ON collect_log(source, ts);

CREATE TABLE IF NOT EXISTS collect_reports (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT,             -- 생성 시각(UTC)
  period_days  INTEGER,
  summary      TEXT,             -- 사람용 요약문
  report       TEXT              -- 전체 JSON
);
CREATE INDEX IF NOT EXISTS idx_collect_reports_ts ON collect_reports(ts);
"""

# datago-api로 분류할 소스(그 외는 hira-board)
_API_SOURCES = {"치료재료", "병용금기", "연령금기", "임부금기", "용량주의",
                "투여기간주의", "노인주의", "효능군중복", "서방정분할주의", "e약은요"}


def _db():
    from core.notice_db import get_notice_db
    conn = get_notice_db()
    conn.executescript(_DDL)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def log_event(source, status, count=0, edition=None, detail=None,
              duration_s=0.0, error="", kind=None) -> None:
    """수집 시도 1건 기록. best-effort — 어떤 예외도 호출자(수집 경로)에 전파하지 않음."""
    try:
        conn = _db()
        conn.execute(
            """INSERT INTO collect_log (ts,source,kind,status,count,edition,detail,duration_s,error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (_now(), source,
             kind or ("datago-api" if source in _API_SOURCES else "hira-board"),
             status, int(count or 0), edition, (detail or "")[:200],
             round(float(duration_s or 0), 1), (error or "")[:300]))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[collect-log] log_event skip: {str(e)[:120]}")


def query_logs(source=None, status=None, since_days=None, limit=100) -> list[dict]:
    """수집 로그 조회(최신순). 필터: source·status·since_days."""
    conn = _db()
    q = "SELECT * FROM collect_log WHERE 1=1"
    args: list = []
    if source:
        q += " AND source=?"; args.append(source)
    if status:
        q += " AND status=?"; args.append(status)
    if since_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=float(since_days))).isoformat()
        q += " AND ts>=?"; args.append(cutoff)
    q += " ORDER BY id DESC LIMIT ?"; args.append(max(1, min(int(limit or 100), 1000)))
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def purge_logs(retention_days: int = 365) -> int:
    """retention 초과 로그 삭제. 삭제 건수 반환. <=0 무동작."""
    if retention_days <= 0:
        return 0
    conn = _db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cur = conn.execute("DELETE FROM collect_log WHERE ts < ?", (cutoff,))
    # 보고서는 로그의 4배 기간 보존(집계 산출물 — 원로그보다 오래 가치 유지)
    rcut = (datetime.now(timezone.utc) - timedelta(days=retention_days * 4)).isoformat()
    conn.execute("DELETE FROM collect_reports WHERE ts < ?", (rcut,))
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# 수집보고서
# ---------------------------------------------------------------------------

def _data_stats() -> dict:
    """현재 정본 데이터 규모 스냅샷(실패 항목은 error 문자열)."""
    out = {}
    try:
        from core.fee_codes import fee_stats
        out["fee_codes"] = fee_stats()
    except Exception as e:  # noqa: BLE001
        out["fee_codes"] = {"error": str(e)[:80]}
    try:
        from core.drug_substitution import stats as sub_stats
        out["drug_substitution"] = sub_stats()
    except Exception as e:  # noqa: BLE001
        out["drug_substitution"] = {"error": str(e)[:80]}
    try:
        from core.dur import stats as dur_stats
        out["dur"] = dur_stats()
    except Exception as e:  # noqa: BLE001
        out["dur"] = {"error": str(e)[:80]}
    return out


def build_report(period_days: int = 7) -> dict:
    """기간 내 수집 활동 집계 보고서(dict). 저장은 generate_report가 수행."""
    now = datetime.now(timezone.utc)
    frm = now - timedelta(days=max(1, period_days))
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM collect_log WHERE ts>=? ORDER BY id", (frm.isoformat(),)).fetchall()]

    per: dict[str, dict] = {}
    for r in rows:
        s = per.setdefault(r["source"], {
            "source": r["source"], "kind": r["kind"], "attempts": 0,
            "ok": 0, "skip": 0, "error": 0, "denied": 0,
            "loaded": [], "errors": [], "last_status": None, "last_ts": None})
        s["attempts"] += 1
        s[r["status"]] = s.get(r["status"], 0) + 1
        s["last_status"], s["last_ts"] = r["status"], r["ts"]
        if r["status"] == "ok":
            s["loaded"].append({"ts": r["ts"], "edition": r["edition"],
                                "count": r["count"], "detail": r["detail"]})
        if r["status"] in ("error", "denied") and r["error"]:
            s["errors"].append({"ts": r["ts"], "error": r["error"]})
    for s in per.values():
        s["loaded"] = s["loaded"][-5:]
        s["errors"] = s["errors"][-5:]

    sources = sorted(per.values(), key=lambda x: x["source"])
    n_ok = sum(s["ok"] for s in sources)
    n_err = sum(s["error"] for s in sources)
    n_denied = sum(s["denied"] for s in sources)
    new_loads = [f"{s['source']} {ld['edition'] or ld['detail'] or ''}({ld['count']}건)"
                 for s in sources for ld in s["loaded"]]
    problems = [s["source"] for s in sources if s["last_status"] in ("error", "denied")]
    # 품질 종합 지수 합류 — 정본 데이터 수집(입력)과 답변 품질(출력)을 한 보고서에서
    # 함께 본다. 미측정/실패는 None(비차단) — 수집 보고 자체를 막지 않는다.
    quality = None
    try:
        from core.eval_score import latest_index
        quality = latest_index()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[collect-report] quality index skip: {str(e)[:120]}")

    q_txt = ""
    if isinstance(quality, dict) and quality.get("score") is not None:
        q_txt = f" 품질 지수 {quality['score']}({quality.get('grade', '?')})."
    summary = (f"최근 {period_days}일 수집 시도 {len(rows)}건 — 적재 {n_ok}·skip "
               f"{sum(s['skip'] for s in sources)}·오류 {n_err}·미신청 {n_denied}. "
               + (f"신규 적재: {', '.join(new_loads)}. " if new_loads else "신규 적재 없음. ")
               + (f"⚠️ 점검 필요: {', '.join(problems)}." if problems else "전 소스 정상.")
               + q_txt)

    return {"generated_at": now.isoformat() + "Z", "period_days": period_days,
            "period_from": frm.isoformat() + "Z", "total_attempts": len(rows),
            "sources": sources, "data_stats": _data_stats(), "quality_index": quality,
            "summary": summary}


def generate_report(period_days: int = 7, retention_days: int | None = None) -> dict:
    """보고서 생성·저장(+로그 retention 정리). 저장된 보고서 dict 반환."""
    rep = build_report(period_days)
    conn = _db()
    conn.execute("INSERT INTO collect_reports (ts,period_days,summary,report) VALUES (?,?,?,?)",
                 (rep["generated_at"], period_days, rep["summary"],
                  json.dumps(rep, ensure_ascii=False)))
    conn.commit()
    try:
        if retention_days is None:
            from core.config import load_config
            retention_days = int((load_config() or {}).get("collect_log_retention_days", 365))
        purged = purge_logs(retention_days)
        if purged:
            logger.info(f"[collect-log] retention purge {purged}건 (>{retention_days}d)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[collect-log] retention skip: {str(e)[:120]}")
    logger.info(f"[collect-report] 생성: {rep['summary'][:160]}")
    return rep


def latest_report() -> dict | None:
    row = _db().execute("SELECT * FROM collect_reports ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["report"] = json.loads(d["report"])
    except Exception:  # noqa: BLE001
        pass
    return d


def list_reports(limit: int = 20) -> list[dict]:
    rows = _db().execute(
        "SELECT id,ts,period_days,summary FROM collect_reports ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit or 20), 200)),)).fetchall()
    return [dict(r) for r in rows]


def report_due(interval_s: int) -> bool:
    """마지막 보고서가 interval_s 이전이거나 없으면 True(워커 churn 안전 — DB 기준)."""
    try:
        row = _db().execute("SELECT MAX(ts) FROM collect_reports").fetchone()
        raw = row[0] if row else None
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(str(raw).rstrip("Z"))
        return (datetime.now(timezone.utc) - last).total_seconds() >= interval_s
    except Exception:  # noqa: BLE001
        return False


def generate_if_due(interval_days: int = 7) -> dict | None:
    """스케줄러용 — due면 생성, 아니면 None."""
    if report_due(max(1, interval_days) * 86400):
        return generate_report(period_days=interval_days)
    return None
