"""의료 테넌트 키 해싱 + 지연 마이그레이션 회귀 (보안 P2 M3).

`medical_tenants.api_key`가 평문 저장·평문 비교였다(6/23 감사 M3). HIS가 라이브로
X-Medical-Key를 쓰고 있어 묶여 있었으나, **첫 인증 시 자동 전환**하면 호출자 작업이 0이라
협의 없이 처리 가능하다는 판단으로 구현. 여기서는 DB 계약과 전환 규칙만 검증한다.
"""
import sqlite3

import pytest

from routes.medical import _migrate_tenant_key, hash_medical_key


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE medical_tenants (
        id TEXT PRIMARY KEY, name TEXT, api_key TEXT NOT NULL UNIQUE,
        api_key_hash TEXT, api_key_prefix TEXT, is_active INTEGER DEFAULT 1)""")
    return conn


def test_hash_is_stable_and_not_plaintext():
    h = hash_medical_key("med-abc123")
    assert h == hash_medical_key("med-abc123")
    assert len(h) == 64 and "med-" not in h
    assert hash_medical_key("med-abc124") != h


def test_hash_of_empty_is_defined():
    """키 미지정 경로가 예외로 죽지 않는다(호출자는 그 전에 걸러내지만 방어)."""
    assert len(hash_medical_key("")) == 64
    assert len(hash_medical_key(None)) == 64


def test_lazy_migration_replaces_plaintext():
    conn = _db()
    conn.execute("INSERT INTO medical_tenants (id, name, api_key) VALUES ('t1','병원','med-secret')")
    conn.commit()

    _migrate_tenant_key(conn, "t1", "med-secret")

    row = conn.execute("SELECT * FROM medical_tenants WHERE id='t1'").fetchone()
    assert row["api_key_hash"] == hash_medical_key("med-secret")
    assert row["api_key_prefix"] == "med-secret"[:12]
    assert "secret" not in row["api_key"], "평문이 남으면 마이그레이션 의미가 없다"
    assert row["api_key"] == "hashed:t1"


def test_migration_placeholder_is_unique_per_tenant():
    """레거시 NOT NULL UNIQUE 제약 — 여러 테넌트를 전환해도 충돌하면 안 된다.

    빈 문자열로 비우면 두 번째 테넌트에서 UNIQUE 충돌로 500이 난다(HR Law에서 겪은 클래스).
    """
    conn = _db()
    for tid in ("t1", "t2", "t3"):
        conn.execute("INSERT INTO medical_tenants (id, name, api_key) VALUES (?,?,?)",
                     (tid, tid, f"med-{tid}-key"))
    conn.commit()

    for tid in ("t1", "t2", "t3"):
        _migrate_tenant_key(conn, tid, f"med-{tid}-key")

    keys = [r[0] for r in conn.execute("SELECT api_key FROM medical_tenants")]
    assert len(set(keys)) == 3, "플레이스홀더가 겹치면 UNIQUE 제약에 걸린다"
    assert all(k.startswith("hashed:") for k in keys)


def test_placeholder_never_matches_a_real_key():
    """플레이스홀더가 우연히 유효 키로 인증되면 안 된다(실제 키는 med- 접두)."""
    assert not "hashed:t1".startswith("med-")


@pytest.mark.parametrize("key", ["med-a", "med-" + "f" * 32])
def test_hash_lookup_roundtrip(key):
    conn = _db()
    conn.execute("INSERT INTO medical_tenants (id, name, api_key, api_key_hash) VALUES (?,?,?,?)",
                 ("t9", "h", "hashed:t9", hash_medical_key(key)))
    conn.commit()
    found = conn.execute("SELECT id FROM medical_tenants WHERE api_key_hash=? AND is_active=1",
                         (hash_medical_key(key),)).fetchone()
    assert found and found["id"] == "t9"
