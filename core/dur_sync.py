"""식약처 data.go.kr 외부 서비스 연동 — DUR 8유형 + e약은요 안전 자동 갱신.

운영급 외부 API 연동: ①**안전 갱신**(전량 fetch 성공 + 건수 sane 검증 후에만 교체 —
API 실패 시 기존 정본 데이터 보존, 빈 테이블 방지) ②동기화 메타 기록(마지막 시각·건수·
에러) ③주기 스케줄러(daemon) ④관측(sync_status).

서비스키는 시크릿: `config.json datago_api_key`(gitignored) → env `DUR_API_KEY` 폴백.
공개 데이터(환자정보 없음). 정본=식약처, 본 시스템은 미러.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from core import dur as dur_mod
from core import sync_guard
from core.dur_api import fetch_all

logger = logging.getLogger("ollama-admin")

IRDNT_BASE = "https://apis.data.go.kr/1471000/DURIrdntInfoService03"     # DUR 성분정보
PRDLST_BASE = "https://apis.data.go.kr/1471000/DURPrdlstInfoService03"   # DUR 품목정보
EASY_BASE = "https://apis.data.go.kr/1471000/DrbEasyDrugInfoService"     # e약은요

# (source, base, operation, kind, min_count) — min_count: 이보다 적게 오면 이상 → 교체 skip(기존 보존)
_SOURCES = [
    ("병용금기", IRDNT_BASE, "getUsjntTabooInfoList02", "interactions", 800),
    ("연령금기", IRDNT_BASE, "getSpcifyAgrdeTabooInfoList02", "single", 100),
    ("임부금기", IRDNT_BASE, "getPwnmTabooInfoList02", "single", 800),
    ("용량주의", IRDNT_BASE, "getCpctyAtentInfoList02", "single", 300),
    ("투여기간주의", IRDNT_BASE, "getMdctnPdAtentInfoList02", "single", 50),
    ("노인주의", IRDNT_BASE, "getOdsnAtentInfoList02", "single", 50),
    ("효능군중복", IRDNT_BASE, "getEfcyDplctInfoList02", "single", 200),
    ("서방정분할주의", PRDLST_BASE, "getSeobangjeongPartitnAtentInfoList03", "split", 1000),
    ("e약은요", EASY_BASE, "getDrbEasyDrugList", "easy", 3000),
]

_META_DDL = """
CREATE TABLE IF NOT EXISTS dur_sync_meta (
  source       TEXT PRIMARY KEY,
  status       TEXT,            -- ok | error | skip
  count        INTEGER,
  last_sync_at TEXT,
  duration_s   REAL,
  last_error   TEXT
);
"""

_lock = threading.Lock()
_scheduler_started = False
_running = False


def get_service_key(base_dir: str | None = None) -> str | None:
    """config.json datago_api_key → env DUR_API_KEY 폴백."""
    try:
        from core.config import load_config
        k = (load_config() or {}).get("datago_api_key")
        if k and str(k).strip():
            return str(k).strip()
    except Exception:  # noqa: BLE001
        pass
    import os
    return os.environ.get("DUR_API_KEY")


def _meta_db():
    conn = dur_mod.get_dur_db()
    conn.executescript(_META_DDL)
    conn.commit()
    return conn


def _record(source, status, count, dur_s, err=""):
    conn = _meta_db()
    conn.execute(
        """INSERT OR REPLACE INTO dur_sync_meta
           (source,status,count,last_sync_at,duration_s,last_error) VALUES (?,?,?,?,?,?)""",
        (source, status, count, datetime.now(timezone.utc).isoformat() + "Z",
         round(dur_s, 1), (err or "")[:300]))
    conn.commit()
    # 수집 이력 로그(best-effort, notice.db collect_log) — 보고서/이력 관측용
    try:
        from core.collect_log import log_event
        log_event(source, status, count=count, duration_s=dur_s, error=err, kind="datago-api")
    except Exception:  # noqa: BLE001
        pass


def _import(kind, source, recs):
    if kind == "interactions":
        return dur_mod.import_interactions_api(recs, clear=True)
    if kind == "single":
        return dur_mod.import_single(recs, source, clear=True)
    if kind == "split":
        return dur_mod.import_split(recs, clear=True)
    if kind == "easy":
        from core.drug_info import import_api_items
        return import_api_items(recs, clear=True)
    raise ValueError(f"unknown kind {kind}")


def _current_count(kind, source):
    """현재 적재된 행 수 — 교체 대상 테이블 기준(메타가 아니라 실제 데이터).

    메타(`dur_sync_meta`)는 source가 PK라 skip이 한 번 기록되면 직전 ok 건수를 잃는다.
    """
    if kind == "easy":
        from core.drug_info import stats as easy_stats
        return (easy_stats() or {}).get("total")
    st = dur_mod.stats() or {}
    if kind == "interactions":
        return st.get("interactions")
    # single·split은 dur_type별 집계에 함께 담긴다(서방정분할주의 포함) — 소스명 = dur_type
    return (st.get("single_warnings") or {}).get(source)


def refresh_one(source, base, op, kind, min_count, key) -> dict:
    """단일 소스 안전 갱신: fetch 성공+건수 sane → 교체. 실패/이상 시 기존 보존."""
    t = time.time()
    try:
        recs = fetch_all(op, key, num_rows=100, sleep=0.05, base=base)
    except Exception as e:  # noqa: BLE001
        _record(source, "error", 0, time.time() - t, f"fetch: {e}")
        logger.warning(f"[dur-sync] {source} fetch error: {str(e)[:160]}")
        return {"source": source, "status": "error", "error": str(e)[:160]}
    n_fetch = len(recs)
    # 상대 감소 가드 — 절대 하한(min_count)은 "0에 가까운 응답"만 잡고 **"35% 빠진 응답"은
    # 못 잡는다**(2026-08-06 치료재료 실사고: 91,211→59,000이 하한 10,000을 통과).
    cur = sync_guard.safe_count(lambda: _current_count(kind, source))
    ratio = sync_guard.max_drop_ratio()
    if sync_guard.drop_too_large(n_fetch, cur, ratio):
        msg = sync_guard.drop_reason(n_fetch, cur, ratio)
        _record(source, "skip", n_fetch, time.time() - t, msg)
        logger.warning(f"[dur-sync] {source} skip: {msg}")
        return {"source": source, "status": "skip", "fetched": n_fetch, "current": cur}
    if n_fetch < min_count:
        # 건수 이상(API 부분응답/오류) — 기존 정본 보존, 교체 skip
        _record(source, "skip", n_fetch, time.time() - t,
                f"fetched {n_fetch} < min {min_count} — kept existing")
        logger.warning(f"[dur-sync] {source} skip: fetched {n_fetch} < {min_count}, 기존 보존")
        return {"source": source, "status": "skip", "fetched": n_fetch}
    try:
        n = _import(kind, source, recs)        # 여기서만 clear+insert(전량 fetch 성공 후)
        _record(source, "ok", n, time.time() - t)
        return {"source": source, "status": "ok", "count": n}
    except Exception as e:  # noqa: BLE001
        _record(source, "error", 0, time.time() - t, f"import: {e}")
        logger.warning(f"[dur-sync] {source} import error: {str(e)[:160]}")
        return {"source": source, "status": "error", "error": str(e)[:160]}


def _analyze_after_sync() -> None:
    """동기화(전량 clear+insert) 후 dur.db·drug_info.db의 옵티마이저 통계 갱신.

    bulk 교체로 sqlite_stat1이 노후화되면 플래너 인덱스 선택이 열화될 수 있어 ANALYZE 1회.
    실패는 비치명(통계는 읽기전용 메타) → 로그만.
    """
    for label, getter in (("dur", dur_mod.get_dur_db),
                          ("drug_info", _get_drug_info_db)):
        try:
            conn = getter()
            conn.execute("ANALYZE")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # 대량 교체 후 WAL 축소
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dur-sync] ANALYZE {label} skip: {str(e)[:120]}")


def _get_drug_info_db():
    from core.drug_info import get_drug_info_db
    return get_drug_info_db()


def refresh_all(key=None) -> dict:
    """전체 소스 순차 안전 갱신. 동시 실행 방지."""
    global _running
    key = key or get_service_key()
    if not key:
        return {"ok": False, "error": "no service key (config datago_api_key / env DUR_API_KEY)"}
    with _lock:
        if _running:
            return {"ok": False, "error": "already running"}
        _running = True
    t = time.time()
    results = []
    try:
        for source, base, op, kind, mc in _SOURCES:
            results.append(refresh_one(source, base, op, kind, mc, key))
    finally:
        _running = False
    ok = sum(1 for r in results if r["status"] == "ok")
    if ok:
        try:
            # 전 소스 적재 후 a측 한글명 백필(a-side-only 성분도 dur_single/product 크로스레프로 커버)
            filled = dur_mod.backfill_a_names()
            logger.info(f"[dur-sync] a_name backfill: {filled} codes")
        except Exception as e:
            logger.warning(f"[dur-sync] a_name backfill failed: {e}")
        _analyze_after_sync()  # bulk clear+replace로 노후화된 옵티마이저 통계(sqlite_stat1) 갱신
    logger.info(f"[dur-sync] refresh done: {ok}/{len(results)} ok ({time.time()-t:.0f}s)")
    return {"ok": True, "duration_s": round(time.time() - t, 1),
            "results": results, "summary": f"{ok}/{len(results)} ok"}


def sync_status() -> dict:
    conn = _meta_db()
    rows = conn.execute(
        "SELECT * FROM dur_sync_meta ORDER BY source").fetchall()
    sources = [{"source": r["source"], "status": r["status"], "count": r["count"],
                "last_sync_at": r["last_sync_at"], "duration_s": r["duration_s"],
                "last_error": r["last_error"] or ""} for r in rows]
    last = max((r["last_sync_at"] or "" for r in rows), default="")
    return {"sources": sources, "last_sync_at": last,
            "running": _running, "configured": bool(get_service_key())}


def _last_ok_sync_dt():
    """가장 최근 성공(status='ok') 동기화 시각(aware datetime) 또는 None."""
    try:
        conn = _meta_db()
        row = conn.execute(
            "SELECT MAX(last_sync_at) FROM dur_sync_meta WHERE status='ok'").fetchone()
        raw = row[0] if row else None
        if not raw:
            return None
        # 저장 포맷 '...+00:00Z' — 끝의 Z 제거 후 파싱(aware)
        return datetime.fromisoformat(str(raw).rstrip("Z"))
    except Exception:  # noqa: BLE001
        return None


def _sync_due(interval_s: int) -> bool:
    """마지막 성공 동기화가 interval_s 이전이거나 기록이 없으면 True.

    🔑 워커 재생성(gunicorn max_requests) 시 preload_app=False라 모듈이 fresh import →
    스케줄러가 재기동되며 startup마다 무조건 refresh_all을 발화해 식약처 data.go.kr API를
    과다 호출(의도=7일 1회)하던 문제 방지 — 데이터가 이미 신선하면 skip한다. 파싱 불가 등
    불확실 시에는 과다호출을 피하기 위해 보수적으로 False(기존 정본 보존, 수동 sync 가능)."""
    last = _last_ok_sync_dt()
    if last is None:
        return True  # 한 번도 성공 동기화 안 됨 → 필요
    try:
        age = (datetime.now(timezone.utc) - last).total_seconds()
    except Exception:  # noqa: BLE001
        return False
    return age >= interval_s


def start_dur_sync_scheduler(interval_days: int = 7, initial_delay_s: int = 600) -> None:
    """주기 자동 갱신 스케줄러(daemon). 키 없으면 미기동. 기동 후 initial_delay 뒤 1회, 이후 주기.

    initial 동기화는 staleness 조건부(_sync_due) — 워커 재생성마다 외부 API를 다시 풀스캔하지
    않도록(데이터가 interval 이내면 skip). due 판정이 워커 churn을 가로질러 7일 cadence를 보장."""
    global _scheduler_started
    if _scheduler_started:
        return
    if not get_service_key():
        logger.info("[dur-sync] scheduler skipped — no service key")
        return
    _scheduler_started = True

    def _loop():
        time.sleep(initial_delay_s)
        interval = max(1, interval_days) * 86400
        while True:
            try:
                if _sync_due(interval):
                    refresh_all()
                else:
                    logger.info(
                        "[dur-sync] skip — 최근 성공 동기화 후 %dd 이내 "
                        "(워커 재기동 시 외부 API 과다호출 방지)", interval_days)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[dur-sync] scheduler error: {str(e)[:160]}")
            time.sleep(interval)

    threading.Thread(target=_loop, name="dur-sync-scheduler", daemon=True).start()
    logger.info(f"[dur-sync] scheduler started (every {interval_days}d, initial +{initial_delay_s}s)")
