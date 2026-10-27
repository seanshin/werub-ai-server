"""비밀번호 해싱 회귀 테스트 — per-user salt 전환 + 레거시 무중단 호환.

2026-06-09: static salt(b"werub-ollama-salt") → per-user 랜덤 salt 마이그레이션.
핵심 불변식: 기존(레거시) 해시 검증 유지 + 신규 해시 per-user salt + lazy 재해싱 신호.

순수 단위 — 외부 의존 0. 실행: venv/bin/python -m pytest tests/test_password.py -q
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import password as pw  # noqa: E402


def _legacy_hash(password):
    """전환 이전 포맷(static salt, bare 64-hex) 재현."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), b"werub-ollama-salt", 200000).hex()


# ── 1) 신규 포맷: 검증 통과 + per-user salt(동일 비번 ≠ 동일 해시) ──
def test_new_hash_roundtrip():
    h = pw.hash_password("correct horse battery")
    assert h.startswith("pbkdf2_sha256$200000$")
    assert h.count("$") == 3
    assert pw.verify_password("correct horse battery", h) is True
    assert pw.verify_password("wrong", h) is False


def test_new_hash_uses_random_salt():
    h1 = pw.hash_password("samepw")
    h2 = pw.hash_password("samepw")
    assert h1 != h2  # per-user 랜덤 salt → 동일 비번도 다른 해시
    assert pw.verify_password("samepw", h1) and pw.verify_password("samepw", h2)


# ── 2) 레거시 호환: 기존 static-salt 해시 그대로 검증 ──
def test_legacy_hash_still_verifies():
    legacy = _legacy_hash("oldsecret")
    assert len(legacy) == 64 and "$" not in legacy
    assert pw.verify_password("oldsecret", legacy) is True
    assert pw.verify_password("nope", legacy) is False


# ── 3) needs_rehash: 레거시=True, 신규=False ──
def test_needs_rehash():
    assert pw.needs_rehash(_legacy_hash("x")) is True   # 레거시 → 재해싱 필요
    assert pw.needs_rehash(pw.hash_password("x")) is False
    assert pw.needs_rehash("") is True                  # 빈 값 방어
    assert pw.needs_rehash(None) is True


# ── 4) 손상/이상 입력 방어 (예외 없이 False) ──
def test_malformed_inputs():
    assert pw.verify_password("x", "") is False
    assert pw.verify_password("x", None) is False
    assert pw.verify_password("x", "pbkdf2_sha256$bad$nothex$zz") is False
    assert pw.verify_password("x", "unknown_algo$200000$aa$bb") is False


# ── L2) dummy_verify: 무예외·None 반환(타이밍 균등화용) ──
def test_dummy_verify_no_raise():
    assert pw.dummy_verify("anything") is None
    assert pw.dummy_verify("") is None
    assert pw.dummy_verify("\ud800surrogate") is None  # 손상 입력에도 예외 없음
    assert pw.verify_password("x", pw._DUMMY_HASH) is False  # 더미는 어떤 비번도 통과 못함


# ── M1) surrogate 등 인코딩 불가 입력: verify는 예외 없이 False ──
def test_verify_handles_unencodable_input():
    h = pw.hash_password("normal")
    assert pw.verify_password("\ud800bad", h) is False  # lone surrogate → UnicodeEncodeError → False
    # 정상 비번(BMP 밖 정상 문자 포함)은 영향 없음
    assert pw.verify_password("normal", h) is True
    h2 = pw.hash_password("𐀀ok")  # U+10000 정상 문자 — 인코딩·검증 정상
    assert pw.verify_password("𐀀ok", h2) is True


# ── 5) lazy 마이그레이션 시나리오: 레거시 검증→재해싱→신규로 재검증 ──
def test_lazy_migration_flow():
    legacy = _legacy_hash("p@ss")
    assert pw.verify_password("p@ss", legacy) and pw.needs_rehash(legacy)
    upgraded = pw.hash_password("p@ss")           # 로그인 성공 시 호출측이 수행
    assert not pw.needs_rehash(upgraded)
    assert pw.verify_password("p@ss", upgraded)
    assert pw.verify_password("p@ss", legacy)     # 옛 해시도 여전히 유효(이행 중 안전)
