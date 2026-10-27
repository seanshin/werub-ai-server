#!/usr/bin/env python3
"""v2.125.47 — 임상 표 drug_codes의 LLM_SEED(T4·모델 자체 지식) 행 격리.

2026-09-28 실측: LLM_SEED 51건이 drug_codes에 있었고(성분명·용량 오류 실물 확인 —
예: Myfortic→"마이포르티크(미도스프론) 11mg", Favipiravir→"아발로비르"), 31건은
is_common=1이라 medical_cli 처방 추천(`routes/medical_cli.py`)에 노출될 수 있었다.

절차(기본 dry-run):
  1) DB 파일 백업(`clinic_codes.db.bak_llmseed_<ts>` — --apply 때만)
  2) `drug_codes_quarantine`에 원행 + quarantined_at·reason 복사
  3) drug_codes에서 source='LLM_SEED' 삭제
  4) staging_drug의 고아 'enriching'(24h 초과) → failed('orphaned enriching')

사용: ./venv/bin/python scripts/quarantine_llm_seed_drugs.py [--apply] [--db clinic_codes.db]
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

REASON = "LLM_SEED(T4) 모델 자체 지식 — 임상 표 자동 반영 금지(v2.125.47)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "clinic_codes.db"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(a.db, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    n_seed = conn.execute("SELECT COUNT(*) FROM drug_codes WHERE source='LLM_SEED'").fetchone()[0]
    n_common = conn.execute(
        "SELECT COUNT(*) FROM drug_codes WHERE source='LLM_SEED' AND is_common=1").fetchone()[0]
    n_total = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
    n_orphan = conn.execute(
        "SELECT COUNT(*) FROM staging_drug WHERE status='enriching' "
        "AND created_at < datetime('now','-1 day')").fetchone()[0]
    print(f"drug_codes 전체 {n_total} · LLM_SEED {n_seed}(is_common {n_common}) · 고아 enriching {n_orphan}")
    if not a.apply:
        print("dry-run — 변경 없음(--apply로 실행)")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    bak = f"{a.db}.bak_llmseed_{ts}"
    shutil.copy2(a.db, bak)
    os.chmod(bak, 0o600)
    print(f"백업 {bak}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("CREATE TABLE IF NOT EXISTS drug_codes_quarantine AS "
                     "SELECT *, '' AS quarantined_at, '' AS quarantine_reason FROM drug_codes WHERE 0")
        conn.execute("INSERT INTO drug_codes_quarantine SELECT *, ?, ? FROM drug_codes "
                     "WHERE source='LLM_SEED'", [now, REASON])
        moved = conn.execute("DELETE FROM drug_codes WHERE source='LLM_SEED'").rowcount
        orphan = conn.execute(
            "UPDATE staging_drug SET status='failed', error='orphaned enriching (v2.125.47 정리)' "
            "WHERE status='enriching' AND created_at < datetime('now','-1 day')").rowcount
    left = conn.execute("SELECT COUNT(*) FROM drug_codes WHERE source='LLM_SEED'").fetchone()[0]
    q = conn.execute("SELECT COUNT(*) FROM drug_codes_quarantine").fetchone()[0]
    print(f"격리 {moved}건(격리표 누적 {q}) · 잔여 LLM_SEED {left} · 고아 정리 {orphan} · "
          f"drug_codes {n_total}→{n_total - moved}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
