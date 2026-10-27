"""v2.125.53 — 신규 설치 DB 스키마: 컬럼 마이그레이션이 테이블 생성 **뒤**에 돌아야 한다.

종전: 두 init_db(app.py·core/db.py) 모두 `ALTER TABLE … ADD COLUMN` 루프가 api_keys·stats_history·
api_activity 생성 **전**에 돌았다. 신규 DB에선 ALTER가 '테이블 없음'으로 조용히 실패(except pass)하고
테이블이 그 컬럼 없이 만들어졌다 → `api_keys.expires_at` 부재로 Bearer 검증·키 발급이 `no such column`,
`stats_history` 성능 컬럼 16개 부재. 라이브 DB는 테이블이 먼저 있었기에 드러나지 않았다(연동 검토 회신와 같은 클래스).
이 결함은 speaker 인증 회귀가 **간헐 실패**(선행 테스트가 어떤 스키마로 DB를 만들었나에 좌우)하며 드러났다.
"""
import sqlite3

import pytest

EXPECTED = {
    "api_keys": {"expires_at"},
    "api_activity": {"key_name"},
    "jobs": {"retry_count", "options", "webhook_url", "job_type"},
}
STATS_PERF = {"vram_used_mb", "gpu_util_pct", "latency_p95_ms", "error_rate_pct",
              "worker_threads_peak", "model_vram_ratio", "inflight_peak"}


def _cols(path, table):
    c = sqlite3.connect(path)
    try:
        return {r[1] for r in c.execute(f"pragma table_info({table})")}
    finally:
        c.close()


def test_app_init_db_on_fresh_db(tmp_path, monkeypatch):
    import app as A
    db = str(tmp_path / "fresh_queue.db")
    monkeypatch.setattr(A, "DB_PATH", db)
    saved = getattr(A._db_local, "conn", None)
    A._db_local.conn = None
    try:
        A.init_db()
    finally:
        c = getattr(A._db_local, "conn", None)
        if c is not None:
            c.close()
        A._db_local.conn = saved
    for table, cols in EXPECTED.items():
        assert cols <= _cols(db, table), (table, cols - _cols(db, table))
    assert STATS_PERF <= _cols(db, "stats_history"), STATS_PERF - _cols(db, "stats_history")
    assert "next_retry_at" in _cols(db, "jobs")


def test_app_init_db_key_lookup_works_on_fresh_db(tmp_path, monkeypatch):
    """증상 재현: 신규 DB에서 키 검증 쿼리(expires_at 조건)가 예외 없이 돈다."""
    import app as A
    db = str(tmp_path / "fresh_queue2.db")
    monkeypatch.setattr(A, "DB_PATH", db)
    saved = getattr(A._db_local, "conn", None)
    A._db_local.conn = None
    try:
        A.init_db()
    finally:
        c = getattr(A._db_local, "conn", None)
        if c is not None:
            c.close()
        A._db_local.conn = saved
    c = sqlite3.connect(db)
    c.execute("SELECT id, name FROM api_keys WHERE key_hash=? AND active=1 "
              "AND (expires_at IS NULL OR expires_at > ?)", ("x", "2026"))
    c.close()


def test_core_db_init_db_on_fresh_db(tmp_path, monkeypatch):
    import core.db as D
    db = str(tmp_path / "fresh_core.db")
    monkeypatch.setattr(D, "DB_PATH", db)
    saved = getattr(D._db_local, "conn", None)
    D._db_local.conn = None
    try:
        D.init_db()
    finally:
        c = getattr(D._db_local, "conn", None)
        if c is not None:
            c.close()
        D._db_local.conn = saved
    for table, cols in EXPECTED.items():
        assert cols <= _cols(db, table), (table, cols - _cols(db, table))


@pytest.mark.parametrize("path,start,after", [
    ("app.py", "    # 컬럼 마이그레이션 (기존 DB 호환)", "CREATE TABLE IF NOT EXISTS orchestration_runs"),
    ("core/db.py", "    # Schema migration (backward compatibility)", "CREATE TABLE IF NOT EXISTS characters"),
])
def test_migration_loop_follows_all_creates(path, start, after):
    """구조 고정: 마이그레이션 루프가 init_db의 마지막 CREATE TABLE보다 뒤에 있다."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
    body = src[src.index("def init_db"):]
    assert body.index(after) < body.index(start)
