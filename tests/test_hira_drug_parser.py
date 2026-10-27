"""core.hira_drug_parser 순수 단위테스트 (xlsx 불요 — 합성 데이터).

헤더 매핑·레코드 변환·name 패킹·fee_rows 변환을 파일 없이 검증한다.
GPU/openpyxl 불요 → iMac에서도 실행 가능.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hira_drug_parser import (  # noqa: E402
    DrugDoc, _colnum, _edition_from, _norm_header, compose_name, fee_rows,
    row_to_record,
)


# ── 헬퍼 ──
def test_colnum():
    assert _colnum("A1") == 1 and _colnum("B2") == 2 and _colnum("Z9") == 26
    assert _colnum("AA1") == 27 and _colnum("AB12") == 28
    assert _colnum("") == 0 and _colnum("12") == 0


def test_norm_header():
    assert _norm_header("주성분코드\n_동일제형") == "주성분코드_동일제형"
    assert _norm_header("  상한금액표 금액 ") == "상한금액표금액"
    assert _norm_header(None) == ""


def test_edition_from():
    assert _edition_from("x.xlsx", "2026년6월1일_(21,898)_공개용") == "2026-06-01"
    assert _edition_from("약제_2026_6_1_공개용.xlsx", "Sheet1") == "2026-06-01"
    assert _edition_from("nope.xlsx", "no-date") == ""


# ── 레코드 변환 ──
def test_row_to_record():
    colmap = {9: "code", 10: "product", 14: "price"}
    cells = {9: "645302132", 10: "포크랄시럽", 14: "129", 99: "ignored"}
    rec = row_to_record(cells, colmap)
    assert rec == {"code": "645302132", "product": "포크랄시럽", "price": "129"}


def test_row_to_record_missing_cells():
    colmap = {9: "code", 14: "price"}
    rec = row_to_record({9: "X1"}, colmap)
    assert rec["code"] == "X1" and rec["price"] == ""


# ── name 패킹 ──
def test_compose_name_full():
    rec = {"product": "포크랄시럽", "ingredient": "chloral hydrate",
           "spec": "95(1)", "unit": "mL/병", "maker": "한림제약(주)",
           "price": "129", "rx": "전문"}
    n = compose_name(rec)
    assert n == "포크랄시럽 | chloral hydrate | 95(1)mL/병 | 한림제약(주) | 상한 129원 | 전문"


def test_compose_name_skips_blank():
    rec = {"product": "약A", "ingredient": "", "spec": "", "unit": "정",
           "maker": "", "price": "", "rx": ""}
    assert compose_name(rec) == "약A | 정"


def test_compose_name_nonnumeric_price():
    # '산정불가' 등 비숫자 상한은 '원' 없이 라벨 그대로.
    assert compose_name({"product": "약B", "price": "산정불가"}) == "약B | 상한 산정불가"
    # 천단위 콤마는 숫자로 인정 → '원' 부착.
    assert compose_name({"product": "약C", "price": "12,345"}) == "약C | 상한 12,345원"


def test_compose_name_empty():
    assert compose_name({}) == ""


# ── fee_rows ──
def test_fee_rows():
    doc = DrugDoc(records=[
        {"code": "645302132", "product": "약A", "price": "129", "rx": "전문"},
        {"code": "", "product": "헤더잔재"},          # code 없으면 제외
        {"code": " 651904420 ", "product": "약B"},     # 공백 트림
    ])
    rows = fee_rows(doc)
    assert len(rows) == 2
    assert rows[0] == {"code": "645302132", "category": "DRUG",
                       "name": "약A | 상한 129원 | 전문"}
    assert rows[1]["code"] == "651904420" and rows[1]["category"] == "DRUG"


def test_fee_rows_custom_category():
    doc = DrugDoc(records=[{"code": "X", "product": "p"}])
    assert fee_rows(doc, category="MATERIAL")[0]["category"] == "MATERIAL"


def test_drugdoc_count():
    assert DrugDoc(records=[{"code": "a"}, {"code": "b"}]).count == 2
