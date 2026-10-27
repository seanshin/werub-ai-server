"""notice.db — ERP 공시 + 색인 잡 (erp-notice-rag).

설계: docs/02-design/features/erp-notice-rag.design.md §3
컨벤션: SQLite WAL + busy_timeout 5000ms (기존 DB들과 동일), conn은 호출측에서 close.
"""
from __future__ import annotations

import os
import sqlite3
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTICE_DB_PATH = os.path.join(BASE_DIR, "notice.db")

# ERP 회신 A-4 코드표 8종 (plan §3-2)
NOTICE_TYPES = ("TARIFF", "DRUG_MATERIAL", "REVIEW_CRITERIA", "MEDICAL_LAW",
                "ADMIN", "NARCOTIC", "LABOR", "INTERNAL")
# ERP status → AI 내부 status 매핑 (plan §3-1)
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REVOKED = "revoked"

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False

_DDL = """
CREATE TABLE IF NOT EXISTS notices (
  id            TEXT PRIMARY KEY,
  erp_ref_id    TEXT NOT NULL,
  revision_no   INTEGER DEFAULT 1,
  notice_type   TEXT NOT NULL,
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,
  doc_no        TEXT,
  source        TEXT,
  source_url    TEXT,
  effective_from TEXT NOT NULL,
  effective_to  TEXT,
  status        TEXT DEFAULT 'active',
  supersedes_id TEXT,
  content_hash  TEXT,
  created_at    TEXT,
  updated_at    TEXT,
  UNIQUE(erp_ref_id, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_notices_active ON notices(status, notice_type, effective_from);
CREATE INDEX IF NOT EXISTS idx_notices_erp ON notices(erp_ref_id);

CREATE TABLE IF NOT EXISTS notice_index_jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  notice_id   TEXT NOT NULL,
  client_request_id TEXT,
  action      TEXT NOT NULL,
  mode        TEXT DEFAULT 'nightly',
  status      TEXT DEFAULT 'pending',
  engine      TEXT,
  chunks      INTEGER,
  error       TEXT,
  retry_count INTEGER DEFAULT 0,
  created_at  TEXT,
  started_at  TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_client ON notice_index_jobs(client_request_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON notice_index_jobs(status, mode);
"""


def get_notice_db(db_path: str | None = None) -> sqlite3.Connection:
    """thread-local 연결 (WAL, busy_timeout 5000ms). 최초 호출 시 DDL 적용."""
    global _initialized
    path = db_path or NOTICE_DB_PATH
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    with _init_lock:
        conn.executescript(_DDL)
        conn.commit()
        _initialized = True
    _local.conn = conn
    _local.path = path
    return conn


def checkpoint_truncate(conn: sqlite3.Connection) -> None:
    """대량 clear+replace 동기화 후 WAL 고수위 축소(디스크 위생·비대칭 해소).

    성능 이슈가 아니다 — 자동 체크포인트는 정상 동작하며(데이터는 병합됨) 조회 지연도
    WAL 크기와 무관(실측). 다만 위성 DB는 주기 체크포인트(queue.db만) 대상이 아니라,
    대량 트랜잭션 후 WAL 파일이 고수위(치료재료 91K 적재 시 ~20MB)로 남는다. sync가
    ANALYZE는 하면서 체크포인트만 빠져 있던 비대칭을 맞춘다. 실패는 비치명(로그도 생략)."""
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
