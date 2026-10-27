"""core.drug_substitution 순수 단위테스트 (인메모리 SQLite, xlsx/GPU 불요).

적재·eligible 판정·lookup 대체후보(동일 주성분코드·대표코드 상이·저가순)·stats 검증.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.drug_substitution import (  # noqa: E402
    _price_int, import_substitution, lookup, stats,
)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_price_int():
    assert _price_int("129") == 129
    assert _price_int("1,420") == 1420
    assert _price_int("산정불가") is None
    assert _price_int("") is None and _price_int(None) is None


def _rows():
    # 동일 주성분코드 1492XATB(doxepin 6mg) 3종 + 다른 성분 1종
    return [
        {"code": "A1", "ingr_code": "1492XATB", "rep_code": "R1", "product": "약A",
         "maker": "갑제약", "price": "300", "rx": "전문", "suspended": ""},
        {"code": "A2", "ingr_code": "1492XATB", "rep_code": "R2", "product": "약B",
         "maker": "을제약", "price": "110", "rx": "전문", "suspended": ""},
        {"code": "A3", "ingr_code": "1492XATB", "rep_code": "R3", "product": "약C",
         "maker": "병제약", "price": "200", "rx": "전문", "suspended": "급여정지(2026.4.4)"},
        {"code": "B1", "ingr_code": "9999XATB", "rep_code": "R9", "product": "딴약",
         "maker": "정제약", "price": "50", "rx": "일반", "suspended": ""},
    ]


def test_import_and_stats():
    c = _conn()
    # eligible(단일품목 제외) = A1,A2,A3,B1 모두. A3은 급여정지라 eligible=0 처리.
    n = import_substitution(_rows(), {"A1", "A2", "A3", "B1"}, conn=c)
    assert n == 4
    s = stats(conn=c)
    assert s["total"] == 4
    assert s["eligible"] == 3            # A3는 급여정지로 제외
    assert s["suspended"] == 1
    assert s["distinct_ingredient_codes"] == 2


def test_eligible_requires_membership_and_not_suspended():
    c = _conn()
    # B1을 eligible set에서 제외 → eligible=0
    import_substitution(_rows(), {"A1", "A2", "A3"}, conn=c)
    assert lookup("B1", conn=c)["eligible"] is False
    assert lookup("A1", conn=c)["eligible"] is True
    assert lookup("A3", conn=c)["eligible"] is False  # 급여정지


def test_lookup_alternatives_safe_and_sorted():
    c = _conn()
    import_substitution(_rows(), {"A1", "A2", "A3", "B1"}, conn=c)
    r = lookup("A1", conn=c)
    assert r["found"] is True and r["price"] == 300
    alts = r["alternatives"]
    # 동일 주성분코드(1492XATB)·대표코드 상이·eligible만 → A2만(A3는 급여정지 비대상)
    assert [a["code"] for a in alts] == ["A2"]
    assert r["alternatives_count"] == 1
    # 저가순: A2(110) 우선
    assert alts[0]["price"] == 110
    # A2(110) < A1(300) → cheaper=True(장려금 대상), cheaper_count 반영
    assert alts[0]["cheaper"] is True
    assert r["cheaper_alternatives_count"] == 1


def test_lookup_pricier_alternative_not_cheaper():
    # 질의약이 이미 최저가면 대체후보는 cheaper=False(장려금 비대상)
    c = _conn()
    rows = [
        {"code": "L1", "ingr_code": "K", "rep_code": "R1", "product": "최저가", "price": "59", "suspended": ""},
        {"code": "L2", "ingr_code": "K", "rep_code": "R2", "product": "더비쌈", "price": "60", "suspended": ""},
    ]
    import_substitution(rows, {"L1", "L2"}, conn=c)
    r = lookup("L1", conn=c)
    assert r["alternatives"][0]["code"] == "L2"
    assert r["alternatives"][0]["cheaper"] is False
    assert r["cheaper_alternatives_count"] == 0


def test_lookup_excludes_different_ingredient():
    c = _conn()
    import_substitution(_rows(), {"A1", "A2", "B1"}, conn=c)
    # B1은 다른 주성분코드 → 후보 없음
    assert lookup("B1", conn=c)["alternatives"] == []


def test_lookup_not_found():
    c = _conn()
    import_substitution(_rows(), set(), conn=c)
    assert lookup("ZZZ", conn=c) == {"found": False, "code": "ZZZ"}
    assert lookup("", conn=c)["found"] is False


def test_lookup_same_repcode_not_substitute():
    # 같은 대표코드(동일제품 생산규격 차이)는 대체조제 아님 → 후보 제외
    c = _conn()
    rows = [
        {"code": "C1", "ingr_code": "K", "rep_code": "SAME", "product": "p1", "price": "100", "suspended": ""},
        {"code": "C2", "ingr_code": "K", "rep_code": "SAME", "product": "p2", "price": "90", "suspended": ""},
    ]
    import_substitution(rows, {"C1", "C2"}, conn=c)
    assert lookup("C1", conn=c)["alternatives"] == []


def test_import_clear_replaces_snapshot():
    """clear=True — 월별 완전 스냅샷 교체: 목록서 빠진 품목의 stale eligible 잔존 방지."""
    c = _conn()
    import_substitution(_rows(), {"A1", "A2", "A3", "B1"}, conn=c)
    # 신판에서 B1이 목록서 제외 → clear 교체 후 조회되면 안 됨
    new_rows = [r for r in _rows() if r["code"] != "B1"]
    n = import_substitution(new_rows, {"A1", "A2", "A3"}, conn=c, clear=True)
    assert n == 3
    s = stats(conn=c)
    assert s["total"] == 3               # B1 잔존 없음
    assert lookup("B1", conn=c)["found"] is False
    # clear=False(기본)는 기존 upsert 유지
    import_substitution(_rows(), {"A1", "A2", "A3", "B1"}, conn=c)
    assert stats(conn=c)["total"] == 4
