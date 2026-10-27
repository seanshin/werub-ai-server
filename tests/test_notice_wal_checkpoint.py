"""notice_db.checkpoint_truncate — 대량 sync 후 WAL 고수위 축소(디스크 위생).

성능이 아니라 위생 항목: 자동 체크포인트는 정상이나 WAL 파일이 고수위로 남는 걸 정리.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notice_db import checkpoint_truncate  # noqa: E402


def test_checkpoint_truncate_shrinks_wal(tmp_path):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER, blob TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)",
                     [(i, "x" * 2000) for i in range(3000)])
    conn.commit()
    wal_before = os.path.getsize(db + "-wal") if os.path.exists(db + "-wal") else 0
    checkpoint_truncate(conn)
    wal_after = os.path.getsize(db + "-wal") if os.path.exists(db + "-wal") else 0
    # 데이터는 보존, WAL은 축소(또는 이미 0)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3000
    assert wal_after <= wal_before


def test_checkpoint_truncate_swallows_errors():
    """비치명 — 닫힌/이상 커넥션에도 예외를 던지지 않는다."""
    conn = sqlite3.connect(":memory:")
    conn.close()
    checkpoint_truncate(conn)          # 예외 없이 무동작
