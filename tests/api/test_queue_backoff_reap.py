"""배치 워커 — 재시도 백오프(next_retry_at) + stale running 리퍼 회귀 테스트.

Item 1: 재시도 백오프 미적용 봉합 — _handle_llm_error가 next_retry_at(미래)을 설정,
        디큐 게이트(next_retry_at<=now)가 백오프 미도래 잡을 제외(이전엔 즉시 재큐).
Item 2: 워커 재생성 중첩창 in-flight 이중처리 race — startup/주기 리셋을 staleness
        게이트(>_STALE_RUNNING_S만 복구). _reap_stale_running 안전망.

app 모듈(flask 필요) 임포트는 fixture 내부 — 실행은 GPU venv.
"""
import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_JOBS_DDL = """
CREATE TABLE jobs (
  id TEXT PRIMARY KEY, batch_id TEXT, status TEXT, model TEXT, system_prompt TEXT,
  prompt TEXT, response TEXT, error TEXT, tokens INTEGER, duration_ms INTEGER,
  priority INTEGER, retry_count INTEGER DEFAULT 0, created_at TEXT, started_at TEXT,
  completed_at TEXT, metadata TEXT, options TEXT, webhook_url TEXT, job_type TEXT,
  next_retry_at TEXT
)
"""


def _iso(delta_s=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=delta_s)).isoformat() + "Z"


@pytest.fixture
def app_tmpdb(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "q.db"))
    app._db_local.conn = None
    # 브로드캐스트/웹훅은 SSE/네트워크 의존 → no-op
    monkeypatch.setattr(app, "_broadcast_job_event", lambda *a, **k: None)
    monkeypatch.setattr(app, "_fire_webhook", lambda *a, **k: None)
    conn = app.get_db()
    conn.execute(_JOBS_DDL)
    conn.commit()
    yield app, conn
    app._db_local.conn = None


def _ins(conn, jid, status, **kw):
    cols = {"id": jid, "status": status, "model": "qwen2.5:14b", "prompt": "p",
            "created_at": _iso(-100), "job_type": "llm", "retry_count": 0}
    cols.update(kw)
    keys = ",".join(cols)
    qs = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO jobs ({keys}) VALUES ({qs})", tuple(cols.values()))
    conn.commit()


def _fresh(app):
    return app.get_db()


# ── Item 1: 재시도 백오프 ──

def test_handle_error_sets_future_next_retry(app_tmpdb):
    app, conn = app_tmpdb
    _ins(conn, "j1", "running", started_at=_iso(-5))
    job = {"id": "j1", "model": "qwen2.5:14b", "metadata": "{}"}
    app._handle_llm_error(job, retry=0, error_msg="boom")  # retry 0 < MAX(3)

    row = _fresh(app).execute(
        "SELECT status, retry_count, next_retry_at FROM jobs WHERE id='j1'").fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert row["next_retry_at"] and row["next_retry_at"] > _iso(0)  # 미래(백오프)


def test_dequeue_gate_excludes_future_backoff(app_tmpdb):
    app, conn = app_tmpdb
    _ins(conn, "ready", "pending", next_retry_at=None)
    _ins(conn, "past", "pending", next_retry_at=_iso(-10))
    _ins(conn, "future", "pending", next_retry_at=_iso(600))
    # worker_loop 디큐와 동일 게이트 술어
    now = _iso(0)
    rows = _fresh(app).execute(
        "SELECT id FROM jobs WHERE status='pending' AND job_type='llm' "
        "AND (next_retry_at IS NULL OR next_retry_at <= ?) ORDER BY id", (now,)).fetchall()
    ids = {r["id"] for r in rows}
    assert ids == {"ready", "past"}  # future는 제외(백오프 미도래)


def test_handle_error_terminal_failed_after_max(app_tmpdb):
    app, conn = app_tmpdb
    _ins(conn, "j2", "running", started_at=_iso(-5))
    job = {"id": "j2", "model": "qwen2.5:14b", "metadata": "{}"}
    app._handle_llm_error(job, retry=3, error_msg="boom")  # retry >= MAX → failed
    row = _fresh(app).execute("SELECT status FROM jobs WHERE id='j2'").fetchone()
    assert row["status"] == "failed"


# ── Item 2: stale running 리퍼 ──

def test_reap_resets_only_stale_running(app_tmpdb, monkeypatch):
    app, conn = app_tmpdb
    _ins(conn, "stale", "running", started_at=_iso(-(app._STALE_RUNNING_S + 60)))
    _ins(conn, "fresh", "running", started_at=_iso(-10))  # 막 시작(구 워커 처리 가능)
    monkeypatch.setattr(app, "_last_stale_reap", 0)  # 60s 게이트 우회

    app._reap_stale_running()

    rows = {r["id"]: r["status"] for r in _fresh(app).execute("SELECT id,status FROM jobs")}
    assert rows["stale"] == "pending"   # orphan 복구
    assert rows["fresh"] == "running"   # in-flight 보존(이중처리 race 방지)


def test_reap_60s_gate(app_tmpdb, monkeypatch):
    app, conn = app_tmpdb
    _ins(conn, "stale", "running", started_at=_iso(-(app._STALE_RUNNING_S + 60)))
    import time
    monkeypatch.setattr(app, "_last_stale_reap", time.time())  # 방금 실행 → gate
    app._reap_stale_running()
    assert _fresh(app).execute("SELECT status FROM jobs WHERE id='stale'").fetchone()["status"] == "running"
