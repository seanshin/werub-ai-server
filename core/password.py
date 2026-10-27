"""비밀번호 해싱 — 단일 진실 공급원 (per-user salt + 레거시 무중단 마이그레이션).

배경:
  이전에는 app.py/routes/training.py 5곳이 각각
      hashlib.pbkdf2_hmac("sha256", pw, b"werub-ollama-salt", 200000)
  로 **고정(static) salt**를 하드코딩했다. KDF(pbkdf2 200k)는 강하지만 static salt는
    1) 동일 비밀번호 → 동일 해시(정보 노출)
    2) 단일 known-salt 사전계산(rainbow) 가능
  이라는 약점이 있어 per-user 랜덤 salt로 전환한다.

저장 포맷(자기기술, Django 유사):
    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
  레거시 해시는 구분자 '$' 없는 bare 64-hex(static salt)다. verify가 포맷을 보고
  자동 분기하므로 **기존 비밀번호는 그대로 동작**한다. 로그인 성공 시 needs_rehash()가
  True면 호출측이 새 포맷으로 재해싱·저장(lazy 마이그레이션)하여 강제 리셋 없이 이행한다.

규칙(지속 관리):
  - 비밀번호 해싱/검증은 반드시 이 모듈을 경유한다. pbkdf2 직접 호출 금지.
  - LEGACY_SALT/ITERATIONS는 레거시 검증 호환을 위해 유지(삭제 시 기존 해시 검증 불가).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

ALGO = "pbkdf2_sha256"
ITERATIONS = 200_000          # 신규·레거시 공통(레거시도 200k였음)
SALT_BYTES = 16
LEGACY_SALT = b"werub-ollama-salt"   # 레거시 bare-hex 해시 검증 전용(호환 보존)


def _pbkdf2(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    # 인코딩 명시(utf-8). surrogate 등 인코딩 불가 입력은 verify의 except에서 False 처리된다.
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def hash_password(password: str) -> str:
    """신규 비밀번호를 per-user 랜덤 salt로 해싱해 자기기술 포맷으로 반환."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _pbkdf2(password, salt, ITERATIONS)
    return f"{ALGO}${ITERATIONS}${salt.hex()}${digest}"


def _is_legacy(stored: str) -> bool:
    """레거시 = 구분자 없는 bare hex(static salt 포맷)."""
    return "$" not in (stored or "")


def verify_password(password: str, stored: str) -> bool:
    """평문 비밀번호가 저장 해시와 일치하면 True. 신규/레거시 포맷 모두 처리(상수시간)."""
    if not stored:
        return False
    try:
        if _is_legacy(stored):
            candidate = _pbkdf2(password, LEGACY_SALT, ITERATIONS)
            return hmac.compare_digest(candidate, stored)
        algo, iter_s, salt_hex, digest = stored.split("$", 3)
        if algo != ALGO:
            return False
        candidate = _pbkdf2(password, bytes.fromhex(salt_hex), int(iter_s))
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


# username 열거 방지용 더미 해시(신규 포맷, 의미 없는 고정값) — 존재하지 않는 사용자에게도
# verify와 동일 비용의 pbkdf2를 1회 태워 응답 시간을 균등화한다.
_DUMMY_HASH = f"{ALGO}${ITERATIONS}${'00' * SALT_BYTES}${'0' * 64}"


def dummy_verify(password: str) -> None:
    """존재하지 않거나 비활성인 사용자 경로에서 호출 — 결과는 폐기, 타이밍만 균등화."""
    try:
        verify_password(password, _DUMMY_HASH)
    except Exception:
        pass


def needs_rehash(stored: str) -> bool:
    """저장 해시가 구식(레거시 static salt 또는 반복횟수 미달)이면 True → 호출측 재해싱 권장."""
    if not stored or _is_legacy(stored):
        return True
    try:
        algo, iter_s, _salt, _digest = stored.split("$", 3)
        return algo != ALGO or int(iter_s) < ITERATIONS
    except (ValueError, TypeError):
        return True
