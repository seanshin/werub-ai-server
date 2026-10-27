"""청구코드 → notice_type 매핑 (erp-notice-rag P0 — 코드 기반 검색/평가).

ERP 코드마스터(현행 441,511건, ERP `GET /regulatory/fees/export.csv`)를 fee_codes에
적재 → 청구 `code` → `category` → `notice_type` 자동 해석(방식 ②).
ERP 합의 매핑 규칙(category → notice_type):
  TARIFF(수가)        → REVIEW_CRITERIA, TARIFF
  DRUG(약가)          → DRUG_MATERIAL, REVIEW_CRITERIA
  MATERIAL(치료재료)  → DRUG_MATERIAL, REVIEW_CRITERIA

방식 ①(ERP가 매핑 후 notice_type 직접 전달)도 호환 — 그 경우 본 모듈 미사용.
저장소: notice.db (공시 도메인과 동일 DB).
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.notice_db import get_notice_db

# category(코드마스터 분류) → notice_type 리스트. 영문/한글 라벨 모두 수용.
CATEGORY_NOTICE_TYPE = {
    "TARIFF": ["REVIEW_CRITERIA", "TARIFF"],
    "수가": ["REVIEW_CRITERIA", "TARIFF"],
    "FEE": ["REVIEW_CRITERIA", "TARIFF"],
    "DRUG": ["DRUG_MATERIAL", "REVIEW_CRITERIA"],
    "약가": ["DRUG_MATERIAL", "REVIEW_CRITERIA"],
    "MATERIAL": ["DRUG_MATERIAL", "REVIEW_CRITERIA"],
    "치료재료": ["DRUG_MATERIAL", "REVIEW_CRITERIA"],
}

_DDL = """
CREATE TABLE IF NOT EXISTS fee_codes (
  code       TEXT PRIMARY KEY,
  category   TEXT,
  name       TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fee_category ON fee_codes(category);
"""


def _ensure(conn):
    conn.executescript(_DDL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def category_to_types(category) -> list:
    """category 라벨 → notice_type 리스트(미지정/미매칭은 [])."""
    return CATEGORY_NOTICE_TYPE.get((category or "").strip(), [])


def import_fee_codes(rows, conn=None) -> int:
    """정규화 rows([{code, category, name}]) 적재(upsert). 적재 건수 반환."""
    conn = conn or get_notice_db()
    _ensure(conn)
    n = 0
    now = _now()
    for r in rows:
        code = str((r or {}).get("code") or "").strip()
        if not code:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO fee_codes (code, category, name, updated_at) VALUES (?,?,?,?)",
            (code, str(r.get("category") or "").strip(), str(r.get("name") or "").strip(), now))
        n += 1
    conn.commit()
    return n


def resolve_notice_types(code, conn=None) -> list:
    """청구 code → notice_type 리스트. 미적재/미매칭은 [](필터 미적용)."""
    if not code:
        return []
    conn = conn or get_notice_db()
    _ensure(conn)
    code = str(code).strip()
    row = conn.execute("SELECT category FROM fee_codes WHERE code=?", (code,)).fetchone()
    if not row:
        # 크로스워크 폴백: 표준코드(바코드)·품목기준코드 등 → 제품코드 해석 후 재조회
        try:
            from core.drug_master import to_product_codes
            for pc in to_product_codes(code, conn):
                row = conn.execute("SELECT category FROM fee_codes WHERE code=?", (pc,)).fetchone()
                if row:
                    break
        except Exception:  # noqa: BLE001
            pass
    if not row:
        return []
    return category_to_types(row["category"])


def fee_stats(conn=None) -> dict:
    conn = conn or get_notice_db()
    _ensure(conn)
    total = conn.execute("SELECT count(*) FROM fee_codes").fetchone()[0]
    by_cat = conn.execute("SELECT category, count(*) c FROM fee_codes GROUP BY category").fetchall()
    return {"total": total, "by_category": {r[0]: r["c"] for r in by_cat}}
