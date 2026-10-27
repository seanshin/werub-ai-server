"""core.drug_info 순수 단위테스트 (인메모리 SQLite, API/GPU 불요).

e약은요 API item 적재(필드 매핑·item_seq PK) + 제품명/품목코드 조회(정확·부분일치).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import drug_info as di  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(di._DDL)
    c.commit()
    return c


def _seed(c):
    return di.import_api_items([
        {"itemSeq": "195700020", "itemName": "활명수", "entpName": "동화약품(주)",
         "efcyQesitm": "식욕감퇴, 소화불량...", "useMethodQesitm": "1회 1병...",
         "atpnQesitm": "3개월 미만 영아 금지", "seQesitm": None,
         "depositMethodQesitm": "실온 보관"},
        {"itemSeq": "199000001", "itemName": "타이레놀정500밀리그램",
         "entpName": "한국얀센", "efcyQesitm": "두통, 발열",
         "seQesitm": "드물게 간손상"},
        {"itemName": "코드없음", "efcyQesitm": "x"},   # item_seq 없으면 제외
    ], conn=c)


def test_import_and_stats():
    c = _conn()
    n = _seed(c)
    assert n == 2                       # item_seq 없는 행 제외
    s = di.stats(conn=c)
    assert s["total"] == 2
    assert s["with_side_effect"] == 1   # 타이레놀만 부작용 채움


def test_lookup_by_item_seq():
    c = _conn(); _seed(c)
    r = di.lookup("195700020", conn=c)
    assert r["found"] and r["match_count"] == 1
    m = r["matches"][0]
    assert m["name"] == "활명수" and m["efficacy"].startswith("식욕감퇴")
    assert "side_effect" not in m       # 빈 필드 생략


def test_lookup_by_exact_name():
    c = _conn(); _seed(c)
    r = di.lookup("활명수", conn=c)
    assert r["found"] and r["matches"][0]["item_seq"] == "195700020"


def test_lookup_by_partial_name():
    c = _conn(); _seed(c)
    r = di.lookup("타이레놀", conn=c)   # 부분일치
    assert r["found"] and r["matches"][0]["name"].startswith("타이레놀")


def test_lookup_not_found():
    c = _conn(); _seed(c)
    assert di.lookup("존재안함", conn=c)["found"] is False
    assert di.lookup("", conn=c)["found"] is False


def test_disclaimer_present():
    c = _conn(); _seed(c)
    assert "약사" in di.lookup("활명수", conn=c)["disclaimer"]
