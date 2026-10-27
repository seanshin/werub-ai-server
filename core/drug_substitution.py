"""저가약 대체조제 장려금 지급대상 의약품 — 조회 테이블 + lookup.

심평원 「저가약 대체조제 장려금 지급대상 의약품 목록」(xlsx)을 notice.db
`drug_substitution`에 적재. "이 약이 장려금 대상인가 + 어떤 동일성분 의약품으로
대체조제 가능한가"를 조회한다(ERP 사전심사 보조).

eligibility:
  목록 xlsx는 2시트 — sheet0=전체, sheet1='단일품목 제외'(=대체 가능한 후보가 있는,
  실제 장려금 대상). eligible = (code ∈ sheet1) AND 급여정지 아님.

대체 후보(보수적·안전):
  **정확한 주성분코드 동일**(성분+함량+제형 동일) AND **대표코드 상이**(다른 제품)
  AND eligible 인 의약품, 상한금액 오름차순(저가 우선). 앞4+뒤3만 일치하는 광의
  그룹(함량 혼입 위험)은 쓰지 않는다 — 임상 안전.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.notice_db import get_notice_db

# 목록 xlsx 헤더 매핑(헤더 행은 스캔 자동 검출) — ingest 스크립트·hira_drug_sync 공용
SUB_HEADERS = {
    "제품코드": "code", "②대표코드": "rep_code", "주성분코드": "ingr_code",
    "주성분명": "ingredient", "제품명": "product", "업체명": "maker",
    "상한금액": "price", "전일": "rx", "생동인정여부(공고번호)": "bioeq",
    "비고(급여정지)": "suspended",
}

_DDL = """
CREATE TABLE IF NOT EXISTS drug_substitution (
  code        TEXT PRIMARY KEY,   -- 제품코드
  ingr_code   TEXT,               -- 주성분코드(성분+함량+제형)
  rep_code    TEXT,               -- 대표코드
  ingredient  TEXT,               -- 주성분명
  product     TEXT,               -- 제품명
  maker       TEXT,               -- 업체명
  price       INTEGER,            -- 상한금액(원, 산정불가=NULL)
  rx          TEXT,               -- 전문/일반
  bioeq       TEXT,               -- 생동인정여부(공고번호)
  suspended   TEXT,               -- 비고(급여정지)
  eligible    INTEGER,            -- 장려금 지급대상(1/0)
  updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_ingr ON drug_substitution(ingr_code);
"""


def _ensure(conn):
    conn.executescript(_DDL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _price_int(s):
    """상한금액 문자열 → int. '산정불가' 등 비숫자/빈값은 None."""
    s = (s or "").strip().replace(",", "")
    return int(s) if s.isdigit() else None


def import_substitution(records, eligible_codes, conn=None, clear=False) -> int:
    """sheet0 records + sheet1 eligible_codes(set) → 적재. 적재 건수 반환.

    records: [{code, ingr_code, rep_code, ingredient, product, maker, price, rx, bioeq, suspended}]
    eligible_codes: '단일품목 제외' 시트의 code 집합(대체 가능 후보가 존재하는 품목).
    clear=True: 기존 전량 삭제 후 적재(월별 완전 스냅샷 — 목록서 빠진 품목의 stale
    eligible 잔존 방지). 호출자가 파싱 성공+건수 sane 검증 후에만 사용(단일 커밋=원자적).
    """
    conn = conn or get_notice_db()
    _ensure(conn)
    if clear:
        conn.execute("DELETE FROM drug_substitution")
    elig = set(eligible_codes or [])
    now = _now()
    n = 0
    for r in records:
        code = str((r or {}).get("code") or "").strip()
        if not code:
            continue
        suspended = str(r.get("suspended") or "").strip()
        eligible = 1 if (code in elig and not suspended) else 0
        conn.execute(
            """INSERT OR REPLACE INTO drug_substitution
               (code, ingr_code, rep_code, ingredient, product, maker, price, rx,
                bioeq, suspended, eligible, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, str(r.get("ingr_code") or "").strip(), str(r.get("rep_code") or "").strip(),
             str(r.get("ingredient") or "").strip(), str(r.get("product") or "").strip(),
             str(r.get("maker") or "").strip(), _price_int(r.get("price")),
             str(r.get("rx") or "").strip(), str(r.get("bioeq") or "").strip(),
             suspended, eligible, now))
        n += 1
    conn.commit()
    return n


def _row_dict(row) -> dict:
    return {
        "code": row["code"], "ingr_code": row["ingr_code"], "rep_code": row["rep_code"],
        "ingredient": row["ingredient"], "product": row["product"], "maker": row["maker"],
        "price": row["price"], "rx": row["rx"], "bioeq": row["bioeq"],
        "suspended": row["suspended"] or "", "eligible": bool(row["eligible"]),
    }


def lookup(code, conn=None, max_alts: int = 20) -> dict:
    """제품코드 → 대체조제/장려금 정보 + 대체 후보(동일 주성분코드·다른 대표코드·저가순).

    미적재 코드는 {found: False}. 대체 후보는 eligible 품목만, 상한금액 오름차순.
    """
    conn = conn or get_notice_db()
    _ensure(conn)
    code = str(code or "").strip()
    if not code:
        return {"found": False, "code": code}
    row = conn.execute("SELECT * FROM drug_substitution WHERE code=?", (code,)).fetchone()
    resolved_via = None
    if not row:
        # 크로스워크 폴백: 바코드(표준코드)·품목기준코드 등 → 제품코드 해석 후 재조회
        try:
            from core.drug_master import to_product_codes
            for pc in to_product_codes(code, conn):
                if pc != code:
                    row = conn.execute("SELECT * FROM drug_substitution WHERE code=?",
                                       (pc,)).fetchone()
                    if row:
                        resolved_via = {"input": code, "via": "drug_master", "product_code": pc}
                        break
        except Exception:  # noqa: BLE001
            pass
    if not row:
        return {"found": False, "code": code}
    out = _row_dict(row)
    out["found"] = True
    if resolved_via:
        out["resolved_via"] = resolved_via
    alts = []
    if row["ingr_code"]:
        # 동일 주성분코드(성분+함량+제형 동일) + 대표코드 상이 + 장려금 대상 = 안전한 대체 후보
        cur = conn.execute(
            """SELECT * FROM drug_substitution
               WHERE ingr_code=? AND code<>? AND eligible=1
                 AND (rep_code='' OR rep_code<>?)
               ORDER BY (price IS NULL), price ASC LIMIT ?""",
            (row["ingr_code"], code, row["rep_code"] or "", max_alts))
        my_price = row["price"]
        for r in cur.fetchall():
            a = _row_dict(r)
            # cheaper = 장려금 대상(약가차액 발생 = 더 저렴한 약으로 대체). 양가 모두 있을 때만 판정.
            a["cheaper"] = (my_price is not None and a["price"] is not None and a["price"] < my_price)
            alts.append(a)
    out["alternatives"] = alts
    out["alternatives_count"] = len(alts)
    out["cheaper_alternatives_count"] = sum(1 for a in alts if a["cheaper"])
    return out


def stats(conn=None) -> dict:
    conn = conn or get_notice_db()
    _ensure(conn)
    total = conn.execute("SELECT count(*) FROM drug_substitution").fetchone()[0]
    eligible = conn.execute("SELECT count(*) FROM drug_substitution WHERE eligible=1").fetchone()[0]
    suspended = conn.execute(
        "SELECT count(*) FROM drug_substitution WHERE suspended<>''").fetchone()[0]
    ingredients = conn.execute(
        "SELECT count(DISTINCT ingr_code) FROM drug_substitution").fetchone()[0]
    return {"total": total, "eligible": eligible, "suspended": suspended,
            "distinct_ingredient_codes": ingredients}
