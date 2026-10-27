"""챗 세션 retention purge 회귀 테스트 (PHI 최소화/거버넌스).

chat_sessions만 나이기반 retention이 없어 무한보존이던 갭을 봉합한
core.chat_memory.purge_old_sessions 의 동작 검증. 순수 단위 — 외부 의존 0(임시 DB만).
실행: venv/bin/python -m pytest tests/test_chat_retention.py -q
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db as dbmod  # noqa: E402
from core import chat_memory  # noqa: E402


def _iso(days_ago: float) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days_ago)
    ).isoformat()


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod._db_local.conn = None
    from core.db import init_chat_memory_tables
    init_chat_memory_tables()
    yield
    try:
        c = getattr(dbmod._db_local, "conn", None)
        if c:
            c.close()
    except Exception:
        pass
    dbmod._db_local.conn = None


def _seed_session(sid: str, last_active_days_ago: float, n_msgs: int = 2):
    conn = dbmod.get_db()
    now = _iso(last_active_days_ago)
    conn.execute(
        "INSERT INTO chat_sessions (id, user_id, model, created_at, last_active_at, message_count) "
        "VALUES (?,?,?,?,?,?)",
        (sid, None, "qwen2.5:7b", now, now, n_msgs),
    )
    for i in range(n_msgs):
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, korean_ratio, created_at) "
            "VALUES (?,?,?,?,?)",
            (sid, "user", f"msg {i}", None, now),
        )
    conn.commit()


def _uuid(tag: str) -> str:
    # 36자 UUID 포맷(스키마 제약과 무관하나 실데이터 형태 유지)
    base = f"{tag:0<8}-0000-0000-0000-000000000000"
    return base[:36]


def test_purge_deletes_only_old_sessions(tmp_db):
    _seed_session(_uuid("old1"), last_active_days_ago=120, n_msgs=3)
    _seed_session(_uuid("old2"), last_active_days_ago=95, n_msgs=2)
    _seed_session(_uuid("fresh"), last_active_days_ago=10, n_msgs=4)

    res = chat_memory.purge_old_sessions(retention_days=90)

    assert res["sessions_deleted"] == 2
    assert res["messages_deleted"] == 5  # 3 + 2

    conn = dbmod.get_db()
    remaining = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    remaining_msgs = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    assert remaining == 1  # fresh만 남음
    assert remaining_msgs == 4


def test_purge_disabled_when_nonpositive(tmp_db):
    _seed_session(_uuid("old1"), last_active_days_ago=999, n_msgs=2)

    for d in (0, -1, None):
        res = chat_memory.purge_old_sessions(retention_days=d)
        assert res["sessions_deleted"] == 0
        assert res.get("skipped") is True

    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0] == 1


def test_purge_noop_when_all_fresh(tmp_db):
    _seed_session(_uuid("f1"), last_active_days_ago=5)
    _seed_session(_uuid("f2"), last_active_days_ago=89)

    res = chat_memory.purge_old_sessions(retention_days=90)
    assert res["sessions_deleted"] == 0
    assert res["messages_deleted"] == 0
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0] == 2


def test_purge_uses_created_at_when_last_active_null(tmp_db):
    # last_active_at NULL → created_at 폴백으로 판정
    conn = dbmod.get_db()
    old = _iso(200)
    conn.execute(
        "INSERT INTO chat_sessions (id, user_id, model, created_at, last_active_at, message_count) "
        "VALUES (?,?,?,?,?,?)",
        (_uuid("nullact"), None, "m", old, None, 0),
    )
    conn.commit()

    res = chat_memory.purge_old_sessions(retention_days=90)
    assert res["sessions_deleted"] == 1
