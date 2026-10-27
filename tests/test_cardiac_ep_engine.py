"""core.cardiac_ep 엔진 플러밍 단위테스트 (P3a) — 레지스트리·검증·DB 자동 마이그레이션.

후속 대리모델 드롭인을 위한 engine 선택 구조. 세그/다운로드 비의존(검증 로직만).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import cardiac_ep as cep  # noqa: E402


def test_engine_registry_defaults():
    assert cep.DEFAULT_ENGINE in cep.SUPPORTED_ENGINES
    assert "eikonal-surrogate-v1.1" in cep.SUPPORTED_ENGINES


def test_engine_column_auto_migration(tmp_path, monkeypatch):
    db = str(tmp_path / "avatar.db")
    # 구 스키마(engine 컬럼 없음) 선생성 → _db() 자동 마이그레이션 검증
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE cardiac_ep_jobs "
                 "(job_id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cep, "AVATAR_DB_PATH", db)
    c = cep._db()
    cols = {r[1] for r in c.execute("PRAGMA table_info(cardiac_ep_jobs)")}
    c.close()
    assert "engine" in cols                              # ALTER TABLE ADD COLUMN 적용


def test_submit_rejects_unknown_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(cep, "AVATAR_DB_PATH", str(tmp_path / "avatar.db"))
    import core.security
    monkeypatch.setattr(core.security, "validate_webhook_url", lambda u: True)
    r = cep.submit("https://example.com/x.nii.gz", engine="bogus-v9")
    assert "error" in r and "unsupported engine" in r["error"]   # 잡 생성 전 거부


def test_submit_default_engine_creates_job(tmp_path, monkeypatch):
    monkeypatch.setattr(cep, "AVATAR_DB_PATH", str(tmp_path / "avatar.db"))
    import core.security
    monkeypatch.setattr(core.security, "validate_webhook_url", lambda u: True)

    # 백그라운드 _process(다운로드) 스레드 no-op으로 부작용 차단
    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(cep.threading, "Thread", _NoThread)
    r = cep.submit("https://example.com/x.nii.gz")
    assert r.get("status") == "pending" and "job_id" in r
    job = cep.get_job(r["job_id"])
    assert job["engine"] == cep.DEFAULT_ENGINE           # 기본 엔진 영속화
