"""HIRA 치료재료정보조회서비스(data.go.kr) → fee_codes(MATERIAL) 동기화.

심평원 치료재료 급여·비급여 목록(재료대코드·품목명·규격·상한단가·급여구분)을
data.go.kr Open API로 전량 수집해 fee_codes(category=MATERIAL)에 적재 — 청구 코드
해석 3축(수가 TARIFF·약가 DRUG·치료재료 MATERIAL) 완성. ERP 사전심사의 치료재료
코드→notice_type 자동해석(DRUG_MATERIAL/REVIEW_CRITERIA) 활성화.

API: https://apis.data.go.kr/B551182/mcatInfoService1.2/getPaymentNonPaymentList1.2
(REST/XML·페이지네이션). 서비스키=DUR과 동일 data.go.kr 키(config datago_api_key).
⚠️ B551182(심평원) 서비스는 **별도 활용신청**(data.go.kr, 자동승인) 필요 — 미신청
키는 403 → status='denied'로 기록하고 기존 데이터 불변(휴면). 승인 후 수동
`POST /api/notice/hira-drug/sync {"source":"치료재료"}` 1회면 가동.

안전 갱신: 전량 fetch 성공+건수 sane(min) 검증 후에만 MATERIAL 전량 교체.
메타는 hira_drug_sync_meta(source='치료재료') 공용 — 게시판 동기화와 함께 관측.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

from core import sync_guard

logger = logging.getLogger("ollama-admin")

SOURCE = "치료재료"
API_URL = "https://apis.data.go.kr/B551182/mcatInfoService1.2/getPaymentNonPaymentList1.2"
MIN_COUNT = 10000       # 치료재료 목록 실측 9.1만행(2026-07) — 미달이면 부분응답 의심, 교체 skip
PAGE_ROWS = 1000
MAX_PAGES = 200         # 폭주 방지 상한(20만행)


def _fetch_page(key: str, page: int, num_rows: int = PAGE_ROWS) -> tuple[list[dict], int]:
    """1페이지 fetch → (items, totalCount). 인증/게이트 오류는 예외."""
    import requests
    # 이 서비스는 검색 조건 필수(무조건 전체조회=totalCount 0). itmNm이 SQL LIKE로
    # 전달되므로 "%" 와일드카드=전 품목 매칭(2026-07 실측 91,211건, 공백 " "은 75,078 부분집합).
    r = requests.get(API_URL, params={
        "ServiceKey": key, "numOfRows": num_rows, "pageNo": page,
        "itmNm": "%"}, timeout=60)
    if r.status_code == 403:
        raise PermissionError("data.go.kr 403 — 치료재료정보조회서비스 활용신청 필요")
    r.raise_for_status()
    root = ET.fromstring(r.content)
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("00", "0000", ""):
        msg = (root.findtext(".//resultMsg") or "").strip()
        if "DENIED" in msg.upper() or code in ("30", "31", "32", "33"):
            raise PermissionError(f"data.go.kr {code} {msg}")
        raise RuntimeError(f"API 오류 {code} {msg}")
    total = int(root.findtext(".//totalCount") or 0)
    items = []
    for it in root.iter("item"):
        items.append({c.tag: (c.text or "").strip() for c in it})
    return items, total


_RETRYABLE = (410, 429, 500, 502, 503)
SUB_DIV = 10            # 410 페이지 세분화 배수(1000행 → 100행 × 10)


def _http_status(exc: Exception) -> int:
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", 0) or 0


def _fetch_page_retry(key: str, page: int, rows: int = PAGE_ROWS,
                      attempts: int = 3) -> list[dict]:
    """페이지 수집(재시도+세분화 폴백).

    특정 페이지가 1000행에서만 410 Gone을 반환하는 사례 실측(2026-07, page 60):
    같은 오프셋 구간을 100행 단위로 나누면 10개 전부 200 → 레코드 결함이 아니라
    응답 크기·처리 시간 한계. 재시도 소진 시 SUB_DIV 배 세분화로 **누락 없이** 수집.
    """
    import requests
    for i in range(attempts):
        try:
            return _fetch_page(key, page, rows)[0]
        except requests.HTTPError as e:
            if _http_status(e) not in _RETRYABLE or i == attempts - 1:
                if _http_status(e) in _RETRYABLE and rows > 1:
                    break                       # 재시도 소진 → 세분화 폴백
                raise
            time.sleep(2 + i * 3)
        except (requests.ConnectionError, requests.Timeout) as e:
            # 2026-08-06: 전량 수집(90여 페이지) 중 연결 리셋이 실측됐는데 재시도가
            # HTTP 오류에만 걸려 있어 **한 번의 리셋이 전체 수집을 무효화**했다.
            # 네트워크 오류는 전형적인 일과성이므로 같은 백오프로 재시도한다.
            if i == attempts - 1:
                raise
            logger.info(f"[hira-mcat] page {page} 연결 오류 재시도 {i+1}/{attempts}: "
                        f"{str(e)[:80]}")
            time.sleep(2 + i * 3)

    return _subdivide(key, page, rows)


def _subdivide(key: str, page: int, rows: int = PAGE_ROWS) -> list[dict]:
    """같은 오프셋 구간을 SUB_DIV배로 나눠 수집(410·빈 응답 공통 폴백).

    2026-08-06 실측: page 60은 1000행에서 **200 OK + 빈 배열**인데 100행 × 10으로
    나누면 **1000행 전부** 돌아온다(`total`도 0 → 91,377로 정상화). 즉 응답 크기·처리
    시간 한계이지 레코드 결함이 아니며, 410일 때와 같은 수가 그대로 듣는다.
    """
    sub_rows = max(1, rows // SUB_DIV)
    logger.info(f"[hira-mcat] page {page}({rows}행) — {sub_rows}행 × {SUB_DIV} 세분화 폴백")
    out: list[dict] = []
    first_sub = (page - 1) * (rows // sub_rows) + 1
    for s in range(first_sub, first_sub + (rows // sub_rows)):
        time.sleep(0.1)
        out.extend(_fetch_page_retry(key, s, sub_rows))   # 재귀=중첩 세분화 허용
    return out


def fetch_all(key: str) -> list[dict]:
    """전 페이지 수집. 페이지 간 0.2s 예의 대기(0.05s는 60페이지쯤 410 실측).

    🔴 2026-08-06 실사고: 중간 페이지가 **200 OK + 빈 배열 + totalCount=0**을 돌려주는데
    `if not batch: break`가 그것을 "끝"으로 읽어 **59,000행에서 조기 종료**했다
    (totalCount는 91,377이고 page 61은 정상 1000행이었다). 안전 갱신 가드도
    절대 하한(10,000)만 봐서 통과시켰고 **정본 32,377행이 조용히 사라졌다.**

    `v2.124.15`가 같은 page 60에서 잡았던 것은 **410 Gone**이라 세분화 폴백이 걸렸다.
    지금은 200이라 예외가 없다 — **"200인데 이상한 페이로드"**는 오케스트레이터 폴백을
    우회한 `v2.124.40` 워커 계약 불일치와 같은 클래스다.

    ⇒ 빈 페이지를 **종료 신호로 쓰지 않는다.** totalCount로 계산한 페이지를 끝까지 돌고,
    빈 페이지는 모아 두었다가 재시도한다. 그래도 비면 경고를 남긴다(무성 손실 금지).
    """
    items, total = _fetch_page(key, 1)
    out = list(items)
    pages = min(MAX_PAGES, (total + PAGE_ROWS - 1) // PAGE_ROWS)
    empty: list[int] = []
    for p in range(2, pages + 1):
        time.sleep(0.2)
        batch = _fetch_page_retry(key, p)
        if not batch:
            empty.append(p)          # 끝이 아니라 **구멍**일 수 있다 — 계속 진행
            continue
        out.extend(batch)
    for p in empty:                  # 구멍 재시도 → 그래도 비면 **세분화 폴백**
        time.sleep(0.5)
        batch = _fetch_page_retry(key, p)
        if not batch:
            # page 60은 1000행에서 지속적으로 빈 응답인데 100행 × 10이면 전부 온다.
            # 410일 때와 같은 수라, 빈 응답에도 같은 폴백을 건다(실측 1,000행 회수).
            batch = _subdivide(key, p)
        if batch:
            out.extend(batch)
        else:
            logger.warning(f"[hira-mcat] page {p} 세분화 후에도 빈 응답 — 누락 가능")
    if total and len(out) < total * 0.95:
        logger.warning(f"[hira-mcat] 수집 {len(out)} < totalCount {total} "
                       f"({len(out)*100//max(total,1)}%) — 부분 수집 의심")
    return out


def to_fee_rows(items: list[dict]) -> list[dict]:
    """API item → fee_codes 행(code=재료대코드, category=MATERIAL, name=요약 패킹)."""
    rows = []
    for it in items:
        code = it.get("mcatCd") or ""
        if not code:
            continue
        maker = it.get("mnfEntpNm") or it.get("impEntpNm") or ""
        spec = (it.get("nomNm") or "") + (it.get("unit") or "")
        parts = [it.get("itmNm") or "", spec, maker,
                 ("상한 " + it["mxUnprc"] + "원") if it.get("mxUnprc") else "",
                 it.get("payTpNm") or ""]
        rows.append({"code": code, "category": "MATERIAL",
                     "name": " | ".join(p for p in parts if p)})
    return rows


def _current_material_count():
    """현재 적재된 MATERIAL 행 수 — **메타가 아니라 실제 데이터**를 기준으로 삼는다.
    메타는 source가 PK라 skip이 한 번 기록되면 직전 ok 건수를 잃는다."""
    from core.notice_db import get_notice_db
    row = get_notice_db().execute(
        "SELECT COUNT(*) FROM fee_codes WHERE category='MATERIAL'").fetchone()
    return row[0] if row else 0


def sync(force: bool = False) -> dict:  # noqa: ARG001 (force=시그니처 통일)
    """전량 수집→검증→fee_codes(MATERIAL) 교체. 실패/미신청 시 기존 보존."""
    from core.hira_drug_sync import _record
    from core.dur_sync import get_service_key
    t = time.time()
    key = get_service_key()
    if not key:
        _record(SOURCE, "denied", 0, None, None, None, time.time() - t,
                "no service key (config datago_api_key)")
        return {"ok": False, "source": SOURCE, "error": "no service key"}
    try:
        items = fetch_all(key)
    except PermissionError as e:
        # 활용신청 전 — denied로 기록(last_check 갱신=주기 재시도만, 과다 403 방지)
        _record(SOURCE, "denied", 0, None, None, None, time.time() - t, str(e)[:200])
        logger.info(f"[hira-mcat] denied: {str(e)[:120]}")
        return {"ok": False, "source": SOURCE, "status": "denied", "error": str(e)[:160]}
    except Exception as e:  # noqa: BLE001
        _record(SOURCE, "error", 0, None, None, None, time.time() - t, f"fetch: {e}")
        logger.warning(f"[hira-mcat] fetch error: {str(e)[:160]}")
        return {"ok": False, "source": SOURCE, "error": f"fetch: {str(e)[:160]}"}

    rows = to_fee_rows(items)
    if len(rows) < MIN_COUNT:
        _record(SOURCE, "skip", len(rows), None, None, None, time.time() - t,
                f"rows {len(rows)} < min {MIN_COUNT} — 기존 보존")
        logger.warning(f"[hira-mcat] skip: rows {len(rows)} < {MIN_COUNT}")
        return {"ok": False, "source": SOURCE, "status": "skip", "rows": len(rows),
                "error": f"rows {len(rows)} < min {MIN_COUNT}"}

    # 절대 하한만으로는 **부분 응답을 못 잡는다**(08-03 실사고: 91,211→59,000이
    # MIN_COUNT=10,000을 넉넉히 통과). 현재 적재량 대비 상대 감소를 함께 본다.
    cur = sync_guard.safe_count(_current_material_count)
    ratio = sync_guard.max_drop_ratio()
    if sync_guard.drop_too_large(len(rows), cur, ratio):
        msg = sync_guard.drop_reason(len(rows), cur, ratio)
        _record(SOURCE, "skip", len(rows), None, None, None, time.time() - t, msg)
        logger.warning(f"[hira-mcat] skip: {msg}")
        return {"ok": False, "source": SOURCE, "status": "skip", "rows": len(rows),
                "current": cur, "error": msg}

    try:
        from core.fee_codes import import_fee_codes, _ensure as _fee_ensure
        from core.notice_db import get_notice_db
        conn = get_notice_db()
        _fee_ensure(conn)
        conn.execute("DELETE FROM fee_codes WHERE category='MATERIAL'")
        n = import_fee_codes(rows, conn=conn)
        conn.execute("ANALYZE")
        conn.commit()
        from core.notice_db import checkpoint_truncate
        checkpoint_truncate(conn)      # 대량 교체 후 WAL 고수위 축소(디스크 위생)
    except Exception as e:  # noqa: BLE001
        _record(SOURCE, "error", 0, None, None, None, time.time() - t, f"import: {e}")
        logger.warning(f"[hira-mcat] import error: {str(e)[:160]}")
        return {"ok": False, "source": SOURCE, "error": f"import: {str(e)[:160]}"}

    _record(SOURCE, "ok", n, None, None, "data.go.kr 치료재료정보조회서비스",
            time.time() - t)
    logger.info(f"[hira-mcat] {n}건 적재 ({time.time()-t:.0f}s)")
    return {"ok": True, "source": SOURCE, "status": "ok", "count": n,
            "duration_s": round(time.time() - t, 1)}
