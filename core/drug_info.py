"""식약처 의약품개요정보(e약은요) — 환자용 의약품 정보 정본 + 조회.

식약처 e약은요 오픈 API(DrbEasyDrugInfoService/getDrbEasyDrugList)를 drug_info.db에
적재. 효능·사용법·주의사항·상호작용·부작용·보관법 등 **환자 친화 정본 정보**를 제공해
"이 약 어떻게 먹어요? 부작용은?" 류 질의를 LLM 환각 없이 근거 답변(자문 보조).

품목일련번호(itemSeq)·제품명 기준 조회. 비식별(공개 의약품 정보). 최종 판단은 의사·약사.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRUG_INFO_DB_PATH = os.path.join(BASE_DIR, "drug_info.db")

DRUG_INFO_DISCLAIMER = (
    "식약처 e약은요(의약품개요정보) 정본 기반 참고 정보입니다. "
    "복용·중단·병용 등 최종 판단은 의사·약사와 상의하세요."
)

_local = threading.local()
_init_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS drug_easy_info (
  item_seq     TEXT PRIMARY KEY,   -- 품목일련번호
  name         TEXT,               -- 제품명(itemName)
  maker        TEXT,               -- 업체명(entpName)
  efficacy     TEXT,               -- 효능(efcyQesitm)
  usage        TEXT,               -- 사용법(useMethodQesitm)
  warn         TEXT,               -- 주의경고(atpnWarnQesitm)
  precaution   TEXT,               -- 주의사항(atpnQesitm)
  interaction  TEXT,               -- 상호작용(intrcQesitm)
  side_effect  TEXT,               -- 부작용(seQesitm)
  storage      TEXT,               -- 보관법(depositMethodQesitm)
  image        TEXT,               -- 낱알이미지(itemImage)
  bizrno       TEXT,
  open_de      TEXT,
  update_de    TEXT,
  updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_easy_name ON drug_easy_info(name);
"""

# API 필드 → 컬럼
_FIELD_MAP = {
    "itemSeq": "item_seq", "itemName": "name", "entpName": "maker",
    "efcyQesitm": "efficacy", "useMethodQesitm": "usage",
    "atpnWarnQesitm": "warn", "atpnQesitm": "precaution",
    "intrcQesitm": "interaction", "seQesitm": "side_effect",
    "depositMethodQesitm": "storage", "itemImage": "image",
    "bizrno": "bizrno", "openDe": "open_de", "updateDe": "update_de",
}
_COLS = ("item_seq", "name", "maker", "efficacy", "usage", "warn", "precaution",
         "interaction", "side_effect", "storage", "image", "bizrno", "open_de", "update_de")


def get_drug_info_db(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DRUG_INFO_DB_PATH
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    with _init_lock:
        conn.executescript(_DDL)
        conn.commit()
    _local.conn = conn
    _local.path = path
    return conn


def _ensure(conn):
    conn.executescript(_DDL)
    conn.commit()


def import_api_items(records, conn=None, clear=False, commit_every: int = 2000) -> int:
    """e약은요 API item(직접 dict) → drug_easy_info 적재(item_seq PK 멱등)."""
    conn = conn or get_drug_info_db()
    _ensure(conn)
    if clear:
        conn.execute("DELETE FROM drug_easy_info")
    now = datetime.now(timezone.utc).isoformat() + "Z"
    n = 0
    for r in records:
        rec = {col: (str(r.get(api) or "").strip() if r.get(api) is not None else "")
               for api, col in _FIELD_MAP.items()}
        if not rec.get("item_seq"):
            continue
        conn.execute(
            f"INSERT OR REPLACE INTO drug_easy_info ({','.join(_COLS)},updated_at) "
            f"VALUES ({','.join('?' * len(_COLS))},?)",
            tuple(rec.get(c, "") for c in _COLS) + (now,))
        n += 1
        if n % commit_every == 0:
            conn.commit()
    conn.commit()
    return n


def _row_dict(row) -> dict:
    d = {c: row[c] for c in _COLS}
    return {k: v for k, v in d.items() if v}  # 빈 필드 생략


def lookup(query, conn=None, limit: int = 5) -> dict:
    """제품명 또는 품목일련번호 → 환자용 의약품 정보. 정확 일치 우선, 없으면 부분일치."""
    conn = conn or get_drug_info_db()
    _ensure(conn)
    q = str(query or "").strip()
    if not q:
        return {"found": False, "query": q, "matches": []}
    rows = conn.execute("SELECT * FROM drug_easy_info WHERE item_seq=?", (q,)).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM drug_easy_info WHERE name=? LIMIT ?",
                            (q, limit)).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM drug_easy_info WHERE name LIKE ? LIMIT ?",
                            (f"%{q}%", limit)).fetchall()
    matches = [_row_dict(r) for r in rows]
    return {"found": bool(matches), "query": q, "match_count": len(matches),
            "matches": matches, "disclaimer": DRUG_INFO_DISCLAIMER}


def stats(conn=None) -> dict:
    conn = conn or get_drug_info_db()
    _ensure(conn)
    total = conn.execute("SELECT count(*) FROM drug_easy_info").fetchone()[0]
    with_se = conn.execute(
        "SELECT count(*) FROM drug_easy_info WHERE side_effect<>''").fetchone()[0]
    return {"total": total, "with_side_effect": with_se}
