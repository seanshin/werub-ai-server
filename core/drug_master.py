"""HIRA 약가마스터 의약품표준코드 — 의약품 코드 크로스워크 + 해석.

심평원 「약가마스터 의약품표준코드」(CSV)를 notice.db `drug_master`에 적재.
**표준코드(바코드) ↔ 제품코드(보험) ↔ 품목기준코드 ↔ 대표코드 ↔ 성분명코드 ↔ ATC**를
단일 해석으로 잇는다. HIS가 어떤 코드(스캔 바코드/보험코드 등)를 줘도 의약품 정체성을
해석 → fee_codes(약가/notice_type)·drug_substitution(대체조제)·DUR(성분명) 연계의 다리.

관계:
  제품코드(개정후)  = fee_codes.code (HIRA 보험 9자리)
  성분명코드(일반명) = drug_substitution.ingr_code (HIRA 주성분코드, 예 487202ATB)
저장: notice.db (fee_codes·drug_substitution과 동일 HIRA 약가 도메인).
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.notice_db import get_notice_db

_DDL = """
CREATE TABLE IF NOT EXISTS drug_master (
  std_code     TEXT PRIMARY KEY,  -- 표준코드(바코드, 포장단위)
  product_code TEXT,              -- 제품코드(개정후, 보험 9자리) = fee_codes.code
  item_code    TEXT,              -- 품목기준코드
  rep_code     TEXT,              -- 대표코드
  ingr_code    TEXT,              -- 성분명코드(일반명) = drug_substitution.ingr_code
  atc          TEXT,              -- 국제표준코드(ATC)
  name         TEXT,              -- 한글상품명
  maker        TEXT,              -- 업체명
  rx           TEXT,              -- 전문일반구분
  spec         TEXT,              -- 약품규격
  cancelled    TEXT,              -- 취소일자(있으면 등재취소)
  updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_dm_product ON drug_master(product_code);
CREATE INDEX IF NOT EXISTS idx_dm_item    ON drug_master(item_code);
CREATE INDEX IF NOT EXISTS idx_dm_rep     ON drug_master(rep_code);
CREATE INDEX IF NOT EXISTS idx_dm_ingr    ON drug_master(ingr_code);
CREATE INDEX IF NOT EXISTS idx_dm_atc     ON drug_master(atc);
"""

_FIELDS = ("std_code", "product_code", "item_code", "rep_code", "ingr_code",
           "atc", "name", "maker", "rx", "spec", "cancelled")


def _ensure(conn):
    conn.executescript(_DDL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def import_rows(rows, conn=None, commit_every: int = 20000) -> int:
    """정규화 rows([{std_code, product_code, ...}]) 적재(upsert). 적재 건수 반환.

    std_code(표준코드)가 PK. 빈 std_code 행은 제외. 대용량(~30만) 배치 commit.
    """
    conn = conn or get_notice_db()
    _ensure(conn)
    now = _now()
    n = 0
    for r in rows:
        std = str((r or {}).get("std_code") or "").strip()
        if not std:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO drug_master
               (std_code,product_code,item_code,rep_code,ingr_code,atc,name,maker,rx,spec,cancelled,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (std, *(str(r.get(f) or "").strip() for f in _FIELDS[1:]), now))
        n += 1
        if n % commit_every == 0:
            conn.commit()
    conn.commit()
    return n


def resolve(code, conn=None, max_std: int = 20) -> dict:
    """임의의 의약품 코드 → 정체성 해석. 표준코드/제품코드/품목기준코드/대표코드/성분명코드 매칭.

    같은 제품의 포장단위(표준코드)가 여럿이라 제품(품목기준코드+제품코드+성분코드)으로 그룹화.
    """
    conn = conn or get_notice_db()
    _ensure(conn)
    code = str(code or "").strip()
    if not code:
        return {"found": False, "code": code, "matches": []}
    cur = conn.execute(
        """SELECT * FROM drug_master
           WHERE std_code=? OR product_code=? OR item_code=? OR rep_code=? OR ingr_code=?""",
        (code, code, code, code, code))
    groups: dict = {}
    for row in cur.fetchall():
        key = (row["item_code"], row["product_code"], row["ingr_code"])
        g = groups.get(key)
        if g is None:
            g = {
                "name": row["name"], "product_code": row["product_code"],
                "item_code": row["item_code"], "rep_code": row["rep_code"],
                "ingr_code": row["ingr_code"], "atc": row["atc"],
                "maker": row["maker"], "rx": row["rx"], "spec": row["spec"],
                "cancelled": row["cancelled"] or "", "std_codes": [],
            }
            groups[key] = g
        if row["std_code"] and len(g["std_codes"]) < max_std:
            g["std_codes"].append(row["std_code"])
    matches = list(groups.values())
    for g in matches:
        g["std_code_count"] = len(g["std_codes"])
    return {"found": bool(matches), "code": code,
            "match_count": len(matches), "matches": matches}


def to_product_codes(code, conn=None) -> list:
    """임의 의약품 코드(표준코드/바코드·품목기준코드·대표코드·성분코드) → 보험 제품코드 목록.

    크로스워크 폴백용 — fee_codes/drug_substitution이 제품코드 외 입력을 받을 때 정규화.
    빈 제품코드(보험 미등재)는 제외.
    """
    r = resolve(code, conn=conn)
    out, seen = [], set()
    for m in r.get("matches", []):
        pc = (m.get("product_code") or "").strip()
        if pc and pc not in seen:
            seen.add(pc)
            out.append(pc)
    return out


def stats(conn=None) -> dict:
    conn = conn or get_notice_db()
    _ensure(conn)
    total = conn.execute("SELECT count(*) FROM drug_master").fetchone()[0]
    with_product = conn.execute(
        "SELECT count(*) FROM drug_master WHERE product_code<>''").fetchone()[0]
    products = conn.execute(
        "SELECT count(DISTINCT product_code) FROM drug_master WHERE product_code<>''").fetchone()[0]
    cancelled = conn.execute(
        "SELECT count(*) FROM drug_master WHERE cancelled<>''").fetchone()[0]
    return {"total_codes": total, "rows_with_product_code": with_product,
            "distinct_product_codes": products, "cancelled": cancelled}
