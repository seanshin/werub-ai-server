#!/usr/bin/env python3
"""저가약 대체조제 장려금 지급대상 의약품 목록(xlsx) → notice.db drug_substitution.

2시트 구조: sheet0=전체 목록, sheet1='단일품목 제외'(대체 후보 존재=장려금 대상).
eligible = (code ∈ sheet1) AND 급여정지 아님. 순수 stdlib xlsx 파서(openpyxl 불요).
멱등 INSERT OR REPLACE.

  venv/bin/python scripts/ingest_drug_substitution.py ~/substitution.xlsx --dry-run
  venv/bin/python scripts/ingest_drug_substitution.py ~/substitution.xlsx
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hira_drug_parser import iter_xlsx_records  # noqa: E402
from core.drug_substitution import SUB_HEADERS  # noqa: E402 (hira_drug_sync와 공용)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", help="저가약 대체조제 목록 xlsx 경로")
    ap.add_argument("--dry-run", action="store_true", help="파싱·집계만(적재 생략)")
    ap.add_argument("--allow-small", action="store_true",
                    help="건수 sane 게이트(8000) 미달이어도 강행")
    args = ap.parse_args()

    path = os.path.expanduser(args.xlsx)
    if not os.path.isfile(path):
        print(f"[err] 파일 없음: {path}", file=sys.stderr)
        return 2

    t = time.time()
    records, edition, src = iter_xlsx_records(path, SUB_HEADERS, sheet_index=0)
    # sheet1 = '단일품목 제외'(대체 후보 존재 = 장려금 대상). 없으면 전체를 후보로 폴백.
    try:
        elig_rows, _, _ = iter_xlsx_records(path, SUB_HEADERS, sheet_index=1)
        eligible_codes = {r["code"].strip() for r in elig_rows if r.get("code")}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] sheet1(단일품목 제외) 파싱 실패 — 전체를 대상으로: {e}", file=sys.stderr)
        eligible_codes = {r["code"].strip() for r in records if r.get("code")}

    print(f"[parse] {time.time()-t:.1f}s | edition={edition or '?'} | "
          f"records={len(records)} eligible(단일품목제외)={len(eligible_codes)} | source={src}")

    if args.dry_run:
        print("[dry-run] 적재 생략")
        from core.drug_substitution import _price_int
        for r in records[:4]:
            elig = r["code"].strip() in eligible_codes and not (r.get("suspended") or "").strip()
            print(f"   {r['code']} {'✓대상' if elig else ' 비대상'} | {r.get('product','')[:40]} "
                  f"| {r.get('ingr_code','')} | 상한 {_price_int(r.get('price'))}")
        return 0

    # 월별 완전 스냅샷 → clear+교체(목록서 빠진 품목의 stale eligible 잔존 방지).
    # 안전 게이트: 파싱 건수가 비정상(부분 파일)이면 기존 정본 보존.
    MIN_ROWS = 8000
    if len(records) < MIN_ROWS and not args.allow_small:
        print(f"[err] records {len(records)} < {MIN_ROWS} — 부분 파일 의심, 적재 중단"
              f" (강행: --allow-small)", file=sys.stderr)
        return 3

    from core.drug_substitution import import_substitution, stats
    n = import_substitution(records, eligible_codes, clear=True)
    print(f"[sub] 전량 교체 적재 {n}건 | stats={stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
