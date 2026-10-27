#!/usr/bin/env python3
"""Drug Data Provenance 마이그레이션

1. drug_codes 테이블에 출처 컬럼 8개 추가
2. data_provenance 테이블 생성
3. batch_run 테이블 생성
4. staging_drug 테이블 생성
5. 기존 400건 소급 매핑 (source='SCRIPT', tier='T3')
"""
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "clinic_codes.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate():
    conn = get_db()
    cur = conn.cursor()

    # ── 1. drug_codes 출처 컬럼 추가 ──────────────────────
    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(drug_codes)").fetchall()}
    alter_cols = [
        ("source", "TEXT DEFAULT 'SCRIPT'"),
        ("source_tier", "TEXT DEFAULT 'T3'"),
        ("source_url", "TEXT"),
        ("source_version", "TEXT"),
        ("collected_at", "TEXT"),
        ("enriched_by", "TEXT"),
        ("confidence", "REAL DEFAULT 0.75"),
        ("updated_at", "TEXT"),
    ]
    for col_name, col_def in alter_cols:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE drug_codes ADD COLUMN {col_name} {col_def}")
            print(f"  + drug_codes.{col_name}")

    # 인덱스
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dc_source ON drug_codes(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dc_tier ON drug_codes(source_tier)")

    # ── 2. data_provenance 테이블 ──────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS data_provenance (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name     TEXT NOT NULL,
        record_key     TEXT NOT NULL,
        action         TEXT NOT NULL,
        field          TEXT,
        old_value      TEXT,
        new_value      TEXT,
        source         TEXT NOT NULL,
        source_tier    TEXT NOT NULL,
        source_url     TEXT,
        source_version TEXT,
        batch_run_id   INTEGER,
        actor          TEXT DEFAULT 'system',
        confidence     REAL,
        created_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (batch_run_id) REFERENCES batch_run(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_table ON data_provenance(table_name, record_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_source ON data_provenance(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_action ON data_provenance(action)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_batch ON data_provenance(batch_run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_created ON data_provenance(created_at)")
    print("  + data_provenance table")

    # ── 3. batch_run 테이블 ──────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS batch_run (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type         TEXT NOT NULL,
        trigger          TEXT NOT NULL,
        source           TEXT,
        status           TEXT DEFAULT 'running',
        total_fetched    INTEGER DEFAULT 0,
        total_enriched   INTEGER DEFAULT 0,
        total_validated  INTEGER DEFAULT 0,
        total_committed  INTEGER DEFAULT 0,
        total_skipped    INTEGER DEFAULT 0,
        total_failed     INTEGER DEFAULT 0,
        llm_model        TEXT,
        llm_tokens_used  INTEGER DEFAULT 0,
        llm_batch_id     TEXT,
        started_at       TEXT DEFAULT (datetime('now')),
        completed_at     TEXT,
        duration_ms      INTEGER,
        error            TEXT,
        summary          TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_br_status ON batch_run(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_br_type ON batch_run(run_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_br_started ON batch_run(started_at)")
    print("  + batch_run table")

    # ── 4. staging_drug 테이블 ──────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS staging_drug (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        source         TEXT NOT NULL,
        source_tier    TEXT NOT NULL,
        source_url     TEXT,
        raw_data       TEXT NOT NULL,
        parsed_data    TEXT,
        target_table   TEXT NOT NULL,
        status         TEXT DEFAULT 'fetched',
        batch_run_id   INTEGER,
        queue_job_id   TEXT,
        confidence     REAL,
        error          TEXT,
        created_at     TEXT DEFAULT (datetime('now')),
        updated_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (batch_run_id) REFERENCES batch_run(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_status ON staging_drug(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_batch ON staging_drug(batch_run_id)")
    print("  + staging_drug table")

    # ── 5. 기존 drug_codes 소급 매핑 ──────────────────────
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = cur.execute("""
        UPDATE drug_codes SET
            source = 'SCRIPT',
            source_tier = 'T3',
            source_version = 'expand_scripts_v2-v6+400',
            collected_at = '2026-05-21T00:00:00Z',
            confidence = 0.75,
            updated_at = ?
        WHERE source IS NULL OR source = 'SCRIPT'
    """, [now]).rowcount

    # 기존 drug_interactions 소급 — source가 이미 있는 것은 T2, 없는 것은 T3
    cur.execute("""
        UPDATE drug_interactions SET source = 'MANUAL'
        WHERE source IS NULL OR source = ''
    """)

    # Provenance 초기 레코드 — 기존 drug_codes CREATE 이력 일괄 삽입
    existing_prov = cur.execute("SELECT COUNT(*) FROM data_provenance WHERE table_name='drug_codes'").fetchone()[0]
    if existing_prov == 0:
        cur.execute("""
            INSERT INTO data_provenance (table_name, record_key, action, source, source_tier, source_version, actor, confidence, created_at)
            SELECT 'drug_codes', code, 'CREATE', 'SCRIPT', 'T3', 'expand_scripts_v2-v6+400', 'migration', 0.75, '2026-05-21T00:00:00Z'
            FROM drug_codes
        """)
        prov_count = cur.execute("SELECT COUNT(*) FROM data_provenance WHERE table_name='drug_codes'").fetchone()[0]
        print(f"  + data_provenance: {prov_count} CREATE records")

    conn.commit()

    # ── 결과 출력 ──────────────────────
    total_drugs = cur.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
    total_with_source = cur.execute("SELECT COUNT(*) FROM drug_codes WHERE source IS NOT NULL").fetchone()[0]
    total_prov = cur.execute("SELECT COUNT(*) FROM data_provenance").fetchone()[0]

    print(f"\n=== Migration Complete ===")
    print(f"  drug_codes:      {total_drugs} ({total_with_source} with source)")
    print(f"  data_provenance: {total_prov} records")
    print(f"  batch_run:       table ready")
    print(f"  staging_drug:    table ready")

    conn.close()


if __name__ == "__main__":
    print("=== Drug Provenance Migration ===")
    migrate()
