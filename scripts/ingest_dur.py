#!/usr/bin/env python3
"""식약처 DUR 병용금기 공개 데이터(xls=실제 xlsx) → dur.db.

  OpenData_PotOpenDurIngr_*.xls  → dur_interaction (성분 pair 룰, 권위 룰셋)
  OpenData_PotOpenDurItem_*.xls  → dur_product     (품목→DUR성분코드 슬림 색인)

순수 stdlib 파서(iter_xlsx_records). 멱등 INSERT OR REPLACE. Item은 대용량(~40만행)이라
품목일련번호로 dedup. 둘 중 하나만 줘도 됨(개별 인자).

  venv/bin/python scripts/ingest_dur.py --ingr ~/DurIngr.xls --item ~/DurItem.xls --dry-run
  venv/bin/python scripts/ingest_dur.py --ingr ~/DurIngr.xls --item ~/DurItem.xls
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dur import (  # noqa: E402
    INGR_HEADERS, ITEM_HEADERS, import_interactions, import_products, stats,
)
from core.hira_drug_parser import iter_xlsx_records  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingr", help="DUR 성분(Ingr) xls 경로")
    ap.add_argument("--item", help="DUR 품목(Item) xls 경로")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.ingr and not args.item:
        print("[err] --ingr 또는 --item 중 최소 하나 필요", file=sys.stderr)
        return 2

    if args.ingr:
        p = os.path.expanduser(args.ingr)
        t = time.time()
        recs, _, src = iter_xlsx_records(p, INGR_HEADERS, sheet_index=0, required_field="a_code")
        print(f"[ingr] {time.time()-t:.1f}s | rules={len(recs)} | {src}")
        if args.dry_run:
            for r in recs[:3]:
                print(f"   {r.get('a_name')}({r.get('a_code')}) ↔ {r.get('b_name')}({r.get('b_code')}) : {(r.get('reason') or '')[:40]}")
        else:
            n = import_interactions(recs)
            print(f"[ingr] 적재 {n}건")

    if args.item:
        p = os.path.expanduser(args.item)
        t = time.time()
        recs, _, src = iter_xlsx_records(p, ITEM_HEADERS, sheet_index=0, required_field="ingr_code")
        print(f"[item] {time.time()-t:.1f}s | rows={len(recs)} | {src}")
        if args.dry_run:
            for r in recs[:3]:
                print(f"   {r.get('product_name')}[{r.get('product_serial')}] → {r.get('ingr_code')} ({r.get('ingredient')})")
        else:
            n = import_products(recs)
            print(f"[item] 적재 {n}건(품목 dedup)")

    if not args.dry_run:
        print(f"[stats] {stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
