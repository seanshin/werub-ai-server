#!/usr/bin/env python3
"""식약처 의약품개요정보(e약은요) 오픈 API → drug_info.db.

DrbEasyDrugInfoService/getDrbEasyDrugList 전량 수집(~4.8K품목) → drug_easy_info.
멱등(item_seq PK, clear 후 재적재). 서비스키는 시크릿 — env DUR_API_KEY/인자 주입(미커밋).

  DUR_API_KEY=xxxx venv/bin/python scripts/ingest_drug_info.py
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dur_api import fetch_all  # noqa: E402
from core.drug_info import import_api_items, stats  # noqa: E402

EASY_BASE = "https://apis.data.go.kr/1471000/DrbEasyDrugInfoService"
OPERATION = "getDrbEasyDrugList"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-key", default=os.environ.get("DUR_API_KEY"))
    ap.add_argument("--num-rows", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.service_key:
        print("[err] 서비스키 필요 — --service-key 또는 env DUR_API_KEY", file=sys.stderr)
        return 2

    t = time.time()
    recs = fetch_all(OPERATION, args.service_key, num_rows=args.num_rows,
                     sleep=0.05, base=EASY_BASE)
    print(f"[e약은요] fetch {len(recs)}건 ({time.time()-t:.1f}s)")
    if args.dry_run:
        for r in recs[:2]:
            print("  ", {k: (str(r.get(k))[:40] if r.get(k) else None)
                         for k in ("itemName", "itemSeq", "efcyQesitm", "seQesitm")})
        return 0
    n = import_api_items(recs, clear=True)
    print(f"[e약은요] 적재 {n}건 | stats={stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
