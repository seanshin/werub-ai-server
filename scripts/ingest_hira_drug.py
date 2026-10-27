#!/usr/bin/env python3
"""HIRA 「약제급여목록 및 급여상한금액표」(xlsx) → fee_codes(DRUG) 적재.

제품코드 → category=DRUG → notice_type[DRUG_MATERIAL, REVIEW_CRITERIA] 자동해석을
활성화한다(ERP 사전심사 약가코드 지원). **RAG 색인 없음**(구조적 lookup=fee_codes로
충분 + 대형 ChromaDB 컬렉션 폭주 회피, 2026-06-11 인시던트 교훈).

순수 표준 라이브러리 파서라 openpyxl 불요. INSERT OR REPLACE 멱등(재적재 안전).

  venv/bin/python scripts/ingest_hira_drug.py ~/drug_list.xlsx --dry-run
  venv/bin/python scripts/ingest_hira_drug.py ~/drug_list.xlsx
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hira_drug_parser import parse_drug_xlsx, fee_rows  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", help="약제급여목록 xlsx 경로")
    ap.add_argument("--dry-run", action="store_true", help="파싱·집계만(적재 생략)")
    ap.add_argument("--category", default="DRUG", help="fee_codes category(기본 DRUG)")
    args = ap.parse_args()

    path = os.path.expanduser(args.xlsx)
    if not os.path.isfile(path):
        print(f"[err] 파일 없음: {path}", file=sys.stderr)
        return 2

    t = time.time()
    doc = parse_drug_xlsx(path)
    rows = fee_rows(doc, category=args.category)
    print(f"[parse] {time.time()-t:.1f}s | edition={doc.edition or '?'} | "
          f"records={doc.count} fee_rows={len(rows)} | source={doc.source_file}")

    if args.dry_run:
        print("[dry-run] 적재 생략")
        for r in rows[:5]:
            print(f"   fee: {r['code']}  {r['name'][:80]}")
        return 0

    from core.fee_codes import import_fee_codes, fee_stats
    n = import_fee_codes(rows)
    print(f"[fees] 적재 {n}건 | stats={fee_stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
