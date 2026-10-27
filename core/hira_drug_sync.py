"""HIRA 게시판 의료 데이터 자동 동기화 — 약제급여목록 + 저가약 대체조제.

hira.or.kr 게시판에 월별로 게시되는 목록 xlsx를 주기 다운로드해 정본 테이블 갱신:
  ①「약제급여목록및급여상한금액표」(매월 1일판) → fee_codes(DRUG)
  ②「저가약 대체조제 장려금 지급대상 의약품 현황」(매월) → drug_substitution

안전 갱신(dur_sync 패턴): 다운로드+파싱 성공 + 건수 sane(min_count) 검증 후에만
기존 데이터 전량 교체(월별 완전 스냅샷 — 목록 제외 품목 잔존 방지) — 사이트 장애/
파일 이상 시 기존 정본 보존. edition 게이트로 동일판 재적재 방지. 스케줄러는
`_sync_due`(메타 기반)로 워커 재생성을 가로질러 cadence 보장(DUR 과다동기화
v2.110.4 교훈).

robots.txt 확인: /co/ 경로만 Disallow — 게시판(bbsDummyKR.do)·다운로드
(bbs/bbsCDownLoad.do)는 허용(2026-07-06 확인). 공개용 자료. 주기당 소스별 요청 3건.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone

from core import sync_guard

logger = logging.getLogger("ollama-admin")

BASE = "https://www.hira.or.kr"
LIST_URL = BASE + "/bbsDummyKR.do?pgmid={pgmid}"
VIEW_URL = (BASE + "/bbsDummyKR.do?pgmid={pgmid}"
            "&brdScnBltNo=4&brdBltNo={post_no}&pageIndex=1&pageIndex2=1")
DOWNLOAD_URL = (BASE + "/bbs/bbsCDownLoad.do?apndNo={apnd_no}&apndBrdBltNo={brd_blt_no}"
                "&apndBrdTyNo={brd_ty_no}&apndBltNo={blt_no}")

MAX_DOWNLOAD_MB = 80
_UA = "Mozilla/5.0 (compatible; werub-ai-admin; +https://example.org)"

_META_DDL = """
CREATE TABLE IF NOT EXISTS hira_drug_sync_meta (
  source        TEXT PRIMARY KEY,
  status        TEXT,            -- ok | skip | error
  count         INTEGER,
  edition       TEXT,            -- 적재된 판 식별자(YYYY-MM-DD 또는 게시글 제목)
  post_no       TEXT,
  post_title    TEXT,
  last_check_at TEXT,            -- 마지막 확인 시각(ok/skip)
  last_sync_at  TEXT,            -- 마지막 적재(ok) 시각
  duration_s    REAL,
  last_error    TEXT
);
"""

_lock = threading.Lock()
_scheduler_started = False
_running = False


# ---------------------------------------------------------------------------
# 소스별 ingest — 각 함수는 (적재건수, edition|None) 반환. 파싱 실패는 예외.
# ---------------------------------------------------------------------------

def _ingest_drug_list(path: str, min_count: int):
    """약제급여목록 xlsx → fee_codes(DRUG) 전량 교체. TARIFF 등 타 카테고리 불변."""
    from core.hira_drug_parser import parse_drug_xlsx, fee_rows
    doc = parse_drug_xlsx(path)
    rows = fee_rows(doc)
    if len(rows) < min_count:
        return None, doc.edition, len(rows)          # 게이트 미달 — 호출자가 skip 처리
    from core.fee_codes import import_fee_codes, _ensure as _fee_ensure
    from core.notice_db import get_notice_db
    conn = get_notice_db()
    _fee_ensure(conn)
    conn.execute("DELETE FROM fee_codes WHERE category='DRUG'")
    n = import_fee_codes(rows, conn=conn)             # commit 포함(단일 커넥션=원자적)
    conn.execute("ANALYZE")
    conn.commit()
    from core.notice_db import checkpoint_truncate
    checkpoint_truncate(conn)          # 대량 교체 후 WAL 고수위 축소(디스크 위생)
    return n, doc.edition, len(rows)


def _ingest_substitution(path: str, min_count: int):
    """저가약 대체조제 목록 xlsx → drug_substitution 전량 교체(clear 스냅샷)."""
    from core.hira_drug_parser import iter_xlsx_records
    from core.drug_substitution import SUB_HEADERS, import_substitution
    records, edition, _src = iter_xlsx_records(path, SUB_HEADERS, sheet_index=0)
    if len(records) < min_count:
        return None, edition, len(records)
    try:
        elig_rows, _, _ = iter_xlsx_records(path, SUB_HEADERS, sheet_index=1)
        eligible = {r["code"].strip() for r in elig_rows if r.get("code")}
    except Exception:  # noqa: BLE001 — sheet1(단일품목 제외) 없으면 전체를 후보로
        eligible = {r["code"].strip() for r in records if r.get("code")}
    from core.notice_db import get_notice_db
    n = import_substitution(records, eligible, conn=get_notice_db(), clear=True)
    return n, edition, len(records)


def _current_count(source):
    """현재 적재된 행 수 — 교체 대상 테이블 기준(메타가 아니라 실제 데이터).

    메타는 source가 PK라 skip이 한 번 기록되면 직전 ok 건수를 잃는다.
    """
    from core.notice_db import get_notice_db
    conn = get_notice_db()
    if source == "약제급여목록":
        return conn.execute(
            "SELECT COUNT(*) FROM fee_codes WHERE category='DRUG'").fetchone()[0]
    if source == "저가약대체조제":
        return conn.execute("SELECT COUNT(*) FROM drug_substitution").fetchone()[0]
    return None


# (source, pgmid, 게시글 제목 키워드, 첨부 선택 키워드, min_count, ingest)
_SOURCES = [
    ("약제급여목록", "HIRAA030014050000", "약제급여목록및급여상한금액표",
     "약제급여목록및급여상한금액표", 15000, _ingest_drug_list),
    ("저가약대체조제", "HIRAA030015000000", "저가약 대체조제 장려금 지급대상 의약품",
     "지급대상 의약품 목록", 8000, _ingest_substitution),
]


# ---------------------------------------------------------------------------
# 순수 파서 (테스트 가능)
# ---------------------------------------------------------------------------

def parse_list_latest(html: str, title_keyword: str):
    """목록 페이지 HTML → 키워드 매칭 최신 게시글 {post_no, title} 또는 None.

    행 예: <a href="?pgmid=...&amp;brdBltNo=1707&amp;...">2026.07.01. 현재 약제급여목록및급여상한금액표</a>
    목록은 최신순이나 방어적으로 post_no 최대값 채택.
    """
    best = None
    for m in re.finditer(
            r'brdBltNo=(\d+)[^>]*>\s*([^<]*' + re.escape(title_keyword) + r'[^<]*)<',
            html):
        no, title = int(m.group(1)), m.group(2).strip()
        if best is None or no > best[0]:
            best = (no, title)
    if best is None:
        return None
    return {"post_no": str(best[0]), "title": best[1]}


def parse_download_params(html: str, att_keyword: str):
    """게시글 뷰 HTML → 첨부 다운로드 파라미터 dict 또는 None.

    downLoadBbs('<apndNo>','<brdBltNo>','<brdTyNo>','<bltNo>') 호출을 파싱.
    첨부가 여럿이면(고시문 hwp+목록 xlsx 등) 파일명에 att_keyword가 있는 항목 우선,
    없으면 첫 항목.
    """
    items = []
    for m in re.finditer(r"downLoadBbs\('(\d+)','(\d+)','(\d+)','(\d+)'\)", html):
        # 파일명은 같은 <li> 안에서 링크 앞에 표기됨 — 앞쪽 400자에서 키워드 탐색
        ctx = html[max(0, m.start() - 400):m.start()]
        items.append((att_keyword in ctx, {
            "apnd_no": m.group(1), "brd_blt_no": m.group(2),
            "brd_ty_no": m.group(3), "blt_no": m.group(4)}))
    if not items:
        return None
    for keyed, params in items:
        if keyed:
            return params
    return items[0][1]


# ---------------------------------------------------------------------------
# 메타
# ---------------------------------------------------------------------------

def _meta_db():
    from core.notice_db import get_notice_db
    conn = get_notice_db()
    conn.executescript(_META_DDL)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _get_meta(source: str):
    row = _meta_db().execute(
        "SELECT * FROM hira_drug_sync_meta WHERE source=?", (source,)).fetchone()
    return dict(row) if row else None


def _record(source, status, count, edition, post_no, post_title, dur_s, err=""):
    conn = _meta_db()
    prev = _get_meta(source) or {}
    now = _now()
    conn.execute(
        """INSERT OR REPLACE INTO hira_drug_sync_meta
           (source,status,count,edition,post_no,post_title,last_check_at,last_sync_at,duration_s,last_error)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (source, status, count,
         edition or prev.get("edition"), post_no, (post_title or "")[:200],
         # denied(활용신청 전)도 확인시각 갱신 — 주기 재시도만 하고 워커 churn마다 403 안 때림
         now if status in ("ok", "skip", "denied") else prev.get("last_check_at"),
         now if status == "ok" else prev.get("last_sync_at"),
         round(dur_s, 1), (err or "")[:300]))
    conn.commit()
    # 수집 이력 로그(best-effort) — meta는 최신 상태만, collect_log는 전 시도 이력
    try:
        from core.collect_log import log_event
        log_event(source, status, count=count, edition=edition,
                  detail=post_title, duration_s=dur_s, error=err)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 동기화
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 60, max_bytes: int = MAX_DOWNLOAD_MB * 1024 * 1024) -> bytes:
    """고정 호스트(hira.or.kr) GET — URL은 상수 템플릿에서만 조립(사용자 입력 없음)."""
    import requests
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout, stream=True)
    r.raise_for_status()
    buf, total = [], 0
    for chunk in r.iter_content(65536):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"download exceeds {MAX_DOWNLOAD_MB}MB")
        buf.append(chunk)
    return b"".join(buf)


def sync_one(source: str, force: bool = False) -> dict:
    """단일 소스: 최신 게시글 확인 → 신규 edition이면 다운로드·검증 후 교체 적재."""
    src = next((s for s in _SOURCES if s[0] == source), None)
    if src is None:
        return {"ok": False, "source": source, "error": f"unknown source {source}"}
    name, pgmid, title_kw, att_kw, min_count, ingest = src
    t = time.time()

    # 1) 목록 → 최신 게시글
    try:
        post = parse_list_latest(
            _http_get(LIST_URL.format(pgmid=pgmid), timeout=30).decode("utf-8", "replace"),
            title_kw)
    except Exception as e:  # noqa: BLE001
        _record(name, "error", 0, None, None, None, time.time() - t, f"list: {e}")
        logger.warning(f"[hira-sync] {name} list fetch error: {str(e)[:160]}")
        return {"ok": False, "source": name, "error": f"list: {str(e)[:160]}"}
    if not post:
        _record(name, "error", 0, None, None, None, time.time() - t, "목록에서 게시글 미발견")
        return {"ok": False, "source": name, "error": "목록에서 게시글을 찾지 못함"}

    # 2) edition 게이트(다운로드 전) — 같은 게시글이면 skip
    prev = _get_meta(name) or {}
    post_key = f"{post['post_no']}:{post['title']}"
    if not force and prev.get("post_no") == post["post_no"] \
            and prev.get("post_title") == post["title"] and prev.get("status") in ("ok", "skip"):
        _record(name, "skip", prev.get("count") or 0, prev.get("edition"),
                post["post_no"], post["title"], time.time() - t)
        return {"ok": True, "source": name, "status": "skip", "detail": "이미 최신 게시글 적재됨",
                "post": post_key}

    # 3) 뷰 → 첨부 파라미터 → 다운로드 → 임시파일
    tmp_path = None
    try:
        params = parse_download_params(
            _http_get(VIEW_URL.format(pgmid=pgmid, post_no=post["post_no"]), timeout=30)
            .decode("utf-8", "replace"), att_kw)
        if not params:
            raise ValueError("첨부 다운로드 링크 미발견")
        data = _http_get(DOWNLOAD_URL.format(**params), timeout=180)
        if data[:2] != b"PK":
            raise ValueError("첨부가 xlsx(zip) 형식이 아님")
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        n, edition, n_rows = ingest(tmp_path, min_count)
    except Exception as e:  # noqa: BLE001
        _record(name, "error", 0, None, post["post_no"], post["title"], time.time() - t,
                f"download/parse: {e}")
        logger.warning(f"[hira-sync] {name} download/parse error: {str(e)[:160]}")
        return {"ok": False, "source": name, "error": f"download/parse: {str(e)[:160]}"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # 4-a) 상대 감소 가드 — 절대 하한(min_count)은 "0에 가까운 응답"만 잡고
    #      **"35% 빠진 응답"은 못 잡는다**(2026-08-06 치료재료 실사고).
    cur = sync_guard.safe_count(lambda: _current_count(name))
    ratio = sync_guard.max_drop_ratio()
    if n is not None and sync_guard.drop_too_large(n, cur, ratio):
        msg = sync_guard.drop_reason(n, cur, ratio)
        _record(name, "skip", n, None, post["post_no"], post["title"], time.time() - t, msg)
        logger.warning(f"[hira-sync] {name} skip: {msg}")
        return {"ok": False, "source": name, "status": "skip", "rows": n,
                "current": cur, "error": msg}

    # 4) 건수 게이트 미달 — 기존 정본 보존
    if n is None:
        _record(name, "skip", n_rows, None, post["post_no"], post["title"], time.time() - t,
                f"rows {n_rows} < min {min_count} — 기존 보존")
        logger.warning(f"[hira-sync] {name} skip: rows {n_rows} < {min_count}, 기존 보존")
        return {"ok": False, "source": name, "status": "skip", "rows": n_rows,
                "error": f"rows {n_rows} < min {min_count}"}

    _record(name, "ok", n, edition or post["title"], post["post_no"], post["title"],
            time.time() - t)
    logger.info(f"[hira-sync] {name} {edition or post['title']} {n}건 적재 "
                f"(post {post['post_no']}, {time.time()-t:.0f}s)")
    return {"ok": True, "source": name, "status": "ok", "edition": edition or post["title"],
            "count": n, "post_no": post["post_no"], "title": post["title"],
            "duration_s": round(time.time() - t, 1)}


def sync(force: bool = False, source: str | None = None) -> dict:
    """전체(또는 단일) 소스 순차 동기화. 동시 실행 방지."""
    global _running
    with _lock:
        if _running:
            return {"ok": False, "error": "already running"}
        _running = True
    t = time.time()
    try:
        names = [source] if source else [s[0] for s in _SOURCES]
        results = [sync_one(n, force=force) for n in names]
    finally:
        _running = False
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": ok == len(results), "duration_s": round(time.time() - t, 1),
            "results": results, "summary": f"{ok}/{len(results)} ok"}


def sync_status() -> dict:
    metas = []
    for name, pgmid, *_rest in _SOURCES:
        m = _get_meta(name)
        metas.append({"source": name, "pgmid": pgmid, "meta": m})
    # 치료재료(data.go.kr API) — 게시판 소스와 메타 테이블 공용
    try:
        from core.hira_mcat_sync import SOURCE as MCAT_SOURCE
        metas.append({"source": MCAT_SOURCE, "pgmid": None, "meta": _get_meta(MCAT_SOURCE)})
    except Exception:  # noqa: BLE001
        pass
    return {"sources": metas, "running": _running}


# ---------------------------------------------------------------------------
# 스케줄러 (dur_sync 패턴 — _sync_due로 워커 churn 가로질러 cadence 보장)
# ---------------------------------------------------------------------------

def _sync_due(interval_s: int, source: str) -> bool:
    """소스의 마지막 확인이 interval_s 이전이거나 기록 없으면 True.

    ok/skip(=사이트 확인 성공)의 last_check_at 기준 — 신규판 없어 skip이어도 확인
    주기를 지켜 워커 재생성마다 외부 사이트를 재스크랩하지 않는다(DUR 과다동기화 교훈).
    error는 last_check 미갱신이라 다음 기회에 재시도. 기록이 있는데 파싱 불가면
    과다호출을 피하기 위해 보수적으로 False(수동 sync 가능)."""
    try:
        meta = _get_meta(source)
    except Exception:  # noqa: BLE001
        return False
    raw = (meta or {}).get("last_check_at")
    if not raw:
        return True  # 확인 성공 기록 없음 → 필요
    try:
        last = datetime.fromisoformat(str(raw).rstrip("Z"))
        age = (datetime.now(timezone.utc) - last).total_seconds()
    except Exception:  # noqa: BLE001
        return False
    return age >= interval_s


def start_hira_drug_sync_scheduler(interval_days: int = 7, initial_delay_s: int = 900) -> None:
    """주기 자동 갱신 스케줄러(daemon). 게시는 월 1회이나 확인은 interval마다(경량 스크랩)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        time.sleep(initial_delay_s)
        interval = max(1, interval_days) * 86400
        while True:
            try:
                due = [s[0] for s in _SOURCES if _sync_due(interval, s[0])]
                if due:
                    for name in due:
                        sync_one(name)
                else:
                    logger.info("[hira-sync] skip — 전 소스 최근 확인 후 %dd 이내", interval_days)
                # 치료재료(data.go.kr API) — 같은 주기, denied(활용신청 전)면 휴면 유지
                from core.hira_mcat_sync import SOURCE as MCAT_SOURCE, sync as mcat_sync
                if _sync_due(interval, MCAT_SOURCE):
                    mcat_sync()
                # 주기 수집보고서(_due=DB 기준이라 워커 churn 안전)
                from core.collect_log import generate_if_due
                generate_if_due(interval_days)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[hira-sync] scheduler error: {str(e)[:160]}")
            time.sleep(interval)

    threading.Thread(target=_loop, name="hira-drug-sync-scheduler", daemon=True).start()
    logger.info(f"[hira-sync] scheduler started (every {interval_days}d, initial +{initial_delay_s}s, "
                f"sources={[s[0] for s in _SOURCES]})")
