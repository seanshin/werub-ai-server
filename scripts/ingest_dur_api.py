#!/usr/bin/env python3
"""식약처 DUR 성분정보 오픈 API → dur.db (병용금기 + 단일성분 금기/주의 6종).

7개 오퍼레이션을 전량 수집해 적재:
  병용금기 → dur_interaction (clear 후 재적재)
  연령/임부/용량/투여기간/노인/효능군중복 → dur_single (유형별 clear 후 재적재)

⚠️ 서비스키는 시크릿 — 코드에 두지 않는다. `--service-key` 또는 env `DUR_API_KEY`로 주입.

  DUR_API_KEY=xxxx venv/bin/python scripts/ingest_dur_api.py
  venv/bin/python scripts/ingest_dur_api.py --service-key xxxx --only 연령금기,임부금기
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dur as dur_mod  # noqa: E402
from core.dur_api import OPERATIONS, fetch_all  # noqa: E402

# DUR 품목정보 서비스(서방정분할주의는 성분정보엔 없고 품목정보에만 있음)
PRODUCT_BASE = "https://apis.data.go.kr/1471000/DURPrdlstInfoService03"
SPLIT_OP = "getSeobangjeongPartitnAtentInfoList03"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-key", default=os.environ.get("DUR_API_KEY"))
    ap.add_argument("--only", help="쉼표구분 유형명만(예: 연령금기,임부금기)")
    ap.add_argument("--num-rows", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.service_key:
        print("[err] 서비스키 필요 — --service-key 또는 env DUR_API_KEY", file=sys.stderr)
        return 2

    only = set(args.only.split(",")) if args.only else None
    for dur_type, op in OPERATIONS.items():
        if only and dur_type not in only:
            continue
        t = time.time()
        try:
            recs = fetch_all(op, args.service_key, num_rows=args.num_rows, sleep=0.05)
        except Exception as e:  # noqa: BLE001
            print(f"[{dur_type}] FETCH ERR: {str(e)[:160]}", file=sys.stderr)
            continue
        print(f"[{dur_type}] fetch {len(recs)}건 ({time.time()-t:.1f}s)")
        if args.dry_run:
            if recs:
                print("   sample:", {k: recs[0].get(k) for k in ("INGR_CODE", "INGR_NAME", "PROHBT_CONTENT")})
            continue
        if dur_type == "병용금기":
            n = dur_mod.import_interactions_api(recs, clear=True)
        else:
            n = dur_mod.import_single(recs, dur_type, clear=True)
        print(f"[{dur_type}] 적재 {n}건")

    # 서방정분할주의(품목 서비스 전용)
    if only is None or "서방정분할주의" in only:
        t = time.time()
        try:
            recs = fetch_all(SPLIT_OP, args.service_key, num_rows=args.num_rows,
                             sleep=0.05, base=PRODUCT_BASE)
            print(f"[서방정분할주의] fetch {len(recs)}건 ({time.time()-t:.1f}s)")
            if not args.dry_run:
                n = dur_mod.import_split(recs, clear=True)
                print(f"[서방정분할주의] 적재 {n}건")
        except Exception as e:  # noqa: BLE001
            print(f"[서방정분할주의] FETCH ERR: {str(e)[:160]}", file=sys.stderr)

    if not args.dry_run:
        print(f"[stats] {dur_mod.stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
