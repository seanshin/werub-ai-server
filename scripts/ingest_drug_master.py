#!/usr/bin/env python3
"""HIRA 약가마스터 의약품표준코드(CSV, CP949) → notice.db drug_master.

표준코드 ↔ 제품코드 ↔ 품목기준코드 ↔ 대표코드 ↔ 성분명코드 ↔ ATC 크로스워크 적재.
멱등(표준코드 PK, INSERT OR REPLACE). 대용량(~30만행) 스트리밍.

  venv/bin/python scripts/ingest_drug_master.py ~/drug_master.csv --dry-run
  venv/bin/python scripts/ingest_drug_master.py ~/drug_master.csv
"""
import argparse
import csv
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.drug_master import import_rows, resolve, stats  # noqa: E402

# CSV 한글 헤더 → drug_master 필드
COLMAP = {
    "표준코드": "std_code",
    "제품코드(개정후)": "product_code",
    "품목기준코드": "item_code",
    "대표코드": "rep_code",
    "일반명코드(성분명코드)": "ingr_code",
    "국제표준코드(ATC코드)": "atc",
    "한글상품명": "name",
    "업체명": "maker",
    "전문일반구분": "rx",
    "약품규격": "spec",
    "취소일자": "cancelled",
}


def _read(path, encoding):
    with io.open(path, encoding=encoding, errors="replace", newline="") as fh:
        r = csv.DictReader(fh)
        for row in r:
            yield {fld: (row.get(src) or "").strip() for src, fld in COLMAP.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="약가마스터 표준코드 CSV 경로")
    ap.add_argument("--encoding", default="cp949")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = os.path.expanduser(args.csv)
    if not os.path.isfile(path):
        print(f"[err] 파일 없음: {path}", file=sys.stderr)
        return 2

    t = time.time()
    if args.dry_run:
        n = 0
        for row in _read(path, args.encoding):
            if n < 3:
                print("   ", {k: row[k] for k in ("std_code", "product_code", "ingr_code", "atc", "name")})
            n += 1
        print(f"[dry-run] {time.time()-t:.1f}s | rows={n} (적재 생략)")
        return 0

    n = import_rows(_read(path, args.encoding))
    print(f"[drug_master] 적재 {n}건 ({time.time()-t:.1f}s) | stats={stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
