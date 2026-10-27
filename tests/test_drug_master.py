"""core.drug_master 순수 단위테스트 (인메모리 SQLite, CSV/GPU 불요).

표준코드 PK 적재·다코드 해석(표준/제품/품목기준/대표/성분코드)·포장 그룹화·stats 검증.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import drug_master as dm  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dm._DDL)
    c.commit()
    return c


def _seed(c):
    # 바라크루드 0.5mg = 제품코드 658700100, 성분코드 487202ATB, 표준코드 2종(포장)
    rows = [
        {"std_code": "8806587001001", "product_code": "658700100", "item_code": "200605262",
         "rep_code": "8806587001001", "ingr_code": "487202ATB", "atc": "J05AF10",
         "name": "바라크루드정0.5밀리그램", "maker": "한국BMS", "rx": "전문의약품",
         "spec": "0.5밀리그램", "cancelled": ""},
        {"std_code": "8806587001018", "product_code": "658700100", "item_code": "200605262",
         "rep_code": "8806587001001", "ingr_code": "487202ATB", "atc": "J05AF10",
         "name": "바라크루드정0.5밀리그램", "maker": "한국BMS", "rx": "전문의약품",
         "spec": "0.5밀리그램", "cancelled": ""},
        # 동일 성분(487202ATB) 다른 제품
        {"std_code": "8800000000001", "product_code": "641605460", "item_code": "201001111",
         "rep_code": "8800000000001", "ingr_code": "487202ATB", "atc": "J05AF10",
         "name": "엔테원정", "maker": "타사", "rx": "전문의약품", "spec": "0.5mg", "cancelled": ""},
        # 빈 std_code → 제외
        {"std_code": "", "product_code": "999", "ingr_code": "X"},
    ]
    return dm.import_rows(rows, conn=c)


def test_import_and_stats():
    c = _conn()
    n = _seed(c)
    assert n == 3                       # 빈 std_code 제외
    s = dm.stats(conn=c)
    assert s["total_codes"] == 3
    assert s["distinct_product_codes"] == 2


def test_resolve_by_product_code_groups_packages():
    c = _conn()
    _seed(c)
    r = dm.resolve("658700100", conn=c)
    assert r["found"] and r["match_count"] == 1
    m = r["matches"][0]
    assert m["ingr_code"] == "487202ATB" and m["atc"] == "J05AF10"
    assert m["std_code_count"] == 2     # 포장 2종 그룹화
    assert set(m["std_codes"]) == {"8806587001001", "8806587001018"}


def test_resolve_by_standard_barcode():
    c = _conn()
    _seed(c)
    r = dm.resolve("8806587001018", conn=c)
    assert r["matches"][0]["product_code"] == "658700100"


def test_resolve_by_ingredient_code_multiple_products():
    c = _conn()
    _seed(c)
    r = dm.resolve("487202ATB", conn=c)
    assert r["match_count"] == 2        # 동일 성분 2제품
    assert {m["product_code"] for m in r["matches"]} == {"658700100", "641605460"}


def test_resolve_by_item_and_rep_code():
    c = _conn()
    _seed(c)
    assert dm.resolve("200605262", conn=c)["found"]      # 품목기준코드
    assert dm.resolve("8806587001001", conn=c)["found"]  # 대표코드(=표준코드)


def test_resolve_not_found():
    c = _conn()
    _seed(c)
    r = dm.resolve("NOPE", conn=c)
    assert r["found"] is False and r["matches"] == []
    assert dm.resolve("", conn=c)["found"] is False
