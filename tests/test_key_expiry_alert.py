"""키 만료 D-7 경보 판정(_key_expiry_findings) — v2.124.31.

07-22 실측(appServer 90일 만료 도래→3일 401 폭주 무경보)의 재발 방지 축.
순수 함수만 검증(경보 발송/쿨다운은 기존 _should_alert 인프라).
"""
from datetime import datetime, timedelta, timezone

from routes.system import _key_expiry_findings

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat() + "Z"


def _row(name, expires_dt, last_used_dt=None):
    return {"name": name,
            "expires_at": _iso(expires_dt) if expires_dt else None,
            "last_used_at": _iso(last_used_dt) if last_used_dt else None}


def test_expiring_within_warn_window():
    rows = [_row("d3", NOW + timedelta(days=3)),
            _row("d10", NOW + timedelta(days=10))]
    expiring, broken = _key_expiry_findings(rows, NOW, warn_days=7)
    assert expiring == ["d3"] and broken == []


def test_expired_recently_used_is_broken():
    # appServer 클래스: 만료됐고 만료 직전까지 사용
    rows = [_row("appServer", NOW - timedelta(days=3), NOW - timedelta(days=3, minutes=1))]
    expiring, broken = _key_expiry_findings(rows, NOW)
    assert broken == ["appServer"] and expiring == []


def test_expired_dormant_ignored():
    # hwpX 클래스: 만료됐으나 사용 흔적이 오래됨(휴면) = 경보 대상 아님
    rows = [_row("hwpX", NOW - timedelta(days=3), NOW - timedelta(days=46))]
    expiring, broken = _key_expiry_findings(rows, NOW)
    assert expiring == [] and broken == []


def test_expired_never_used_ignored():
    rows = [_row("unused", NOW - timedelta(days=1), None)]
    assert _key_expiry_findings(rows, NOW) == ([], [])


def test_healthy_and_boundary():
    # 유효 장기 키는 무경보 · warn_days 튜닝 반영
    rows = [_row("longterm", NOW + timedelta(days=365)),
            _row("d10", NOW + timedelta(days=10))]
    assert _key_expiry_findings(rows, NOW, warn_days=7) == ([], [])
    expiring, _ = _key_expiry_findings(rows, NOW, warn_days=14)
    assert set(expiring) == {"d10"}
