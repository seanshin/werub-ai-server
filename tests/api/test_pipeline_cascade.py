"""파이프라인 단계 실패 시 하위 waiting 단계 cascade-fail 회귀 테스트.

버그: _advance_pipeline은 성공 시에만 다음 단계를 pending으로 올려, 중간 단계가
최종 실패(max retry)하면 downstream waiting 잡이 영구 고아(never run·_auto_purge가
completed/failed만 지워 never purge)가 됐다(라이브서 2026-06-04 10건 stuck 확인).
fix: _handle_llm_error 최종 실패 경로 → _fail_pipeline_downstream 으로 cascade.

app 모듈(flask 필요) 임포트는 fixture 내부 — 컬렉션은 flask 없이도 가능, 실행은 GPU venv.
"""
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
  completed_at TEXT, metadata TEXT, options TEXT, webhook_url TEXT, job_type TEXT
)
"""


@pytest.fixture
def app_tmpdb(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "q.db"))
    app._db_local.conn = None
    conn = app.get_db()
    conn.execute(_JOBS_DDL)
    conn.commit()
    yield app, conn
    app._db_local.conn = None


def _ins(conn, jid, status, pid, step, total, created="2026-06-30T00:00:00Z"):
    meta = json.dumps({"pipeline_id": pid, "step_index": step, "total_steps": total}) if pid else "{}"
    conn.execute(
        "INSERT INTO jobs (id,status,model,prompt,created_at,metadata,job_type) VALUES (?,?,?,?,?,?,?)",
        (jid, status, "m", "p", created, meta, "llm"))
    conn.commit()


def _job(jid, pid, step):
    return {"id": jid, "metadata": json.dumps({"pipeline_id": pid, "step_index": step, "total_steps": 3})}


def _statuses(app):
    # _fail_pipeline_downstream 은 thread-local 커넥션을 close()(정상 패턴)하므로
    # assert 시 get_db()로 재취득(재연결)해 조회.
    conn = app.get_db()
    return {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM jobs")}


def test_downstream_cascade_fail(app_tmpdb):
    app, conn = app_tmpdb
    pid = "pipe-test01"
    _ins(conn, "j0", "running", pid, 0, 3)
    _ins(conn, "j1", "waiting", pid, 1, 3)
    _ins(conn, "j2", "waiting", pid, 2, 3)

    app._fail_pipeline_downstream(_job("j0", pid, 0), "boom")

    rows = _statuses(app)
    assert rows["j1"] == "failed" and rows["j2"] == "failed"


def test_non_pipeline_job_noop(app_tmpdb):
    app, conn = app_tmpdb
    _ins(conn, "w1", "waiting", "other-pipe", 0, 2)
    # pipeline_id 없는 잡 → 무동작(early return, 커넥션 close 안 함)
    app._fail_pipeline_downstream({"id": "x", "metadata": "{}"}, "boom")
    assert conn.execute("SELECT status FROM jobs WHERE id='w1'").fetchone()["status"] == "waiting"


def test_only_same_pipeline_affected(app_tmpdb):
    app, conn = app_tmpdb
    _ins(conn, "a1", "waiting", "pipe-aaaa1111", 1, 2)
    _ins(conn, "b1", "waiting", "pipe-bbbb2222", 1, 2)
    app._fail_pipeline_downstream(_job("a0", "pipe-aaaa1111", 0), "boom")
    rows = _statuses(app)
    assert rows["a1"] == "failed" and rows["b1"] == "waiting"  # 다른 파이프라인 불변


def test_completed_steps_not_touched(app_tmpdb):
    app, conn = app_tmpdb
    pid = "pipe-cccc3333"
    _ins(conn, "c0", "completed", pid, 0, 3)  # 이미 완료된 상위 단계
    _ins(conn, "c2", "waiting", pid, 2, 3)
    app._fail_pipeline_downstream(_job("c1", pid, 1), "boom")
    rows = _statuses(app)
    assert rows["c0"] == "completed" and rows["c2"] == "failed"  # waiting만 전이
