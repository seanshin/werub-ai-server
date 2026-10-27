"""v2.87.0 — 청구코드→notice_type 매핑(core/fee_codes) 단위테스트.

순수 sqlite(외부 의존 0). 실행: venv/bin/python -m pytest tests/test_fee_codes.py -q
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fee_codes  # noqa: E402


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_category_to_types_known():
    assert fee_codes.category_to_types("TARIFF") == ["REVIEW_CRITERIA", "TARIFF"]
    assert fee_codes.category_to_types("수가") == ["REVIEW_CRITERIA", "TARIFF"]
    assert fee_codes.category_to_types("DRUG") == ["DRUG_MATERIAL", "REVIEW_CRITERIA"]
    assert fee_codes.category_to_types("약가") == ["DRUG_MATERIAL", "REVIEW_CRITERIA"]
    assert fee_codes.category_to_types("MATERIAL") == ["DRUG_MATERIAL", "REVIEW_CRITERIA"]
    assert fee_codes.category_to_types("치료재료") == ["DRUG_MATERIAL", "REVIEW_CRITERIA"]


def test_category_to_types_unknown_empty():
    assert fee_codes.category_to_types("UNKNOWN") == []
    assert fee_codes.category_to_types("") == []
    assert fee_codes.category_to_types(None) == []
    assert fee_codes.category_to_types("  TARIFF  ".strip()) == ["REVIEW_CRITERIA", "TARIFF"]


def test_import_and_resolve(conn):
    n = fee_codes.import_fee_codes(
        [{"code": "AA157", "category": "TARIFF", "name": "재진"},
         {"code": "J5301", "category": "치료재료", "name": "재료"},
         {"code": "  ", "category": "X"},   # 빈 code 무시
         {}],                                # 빈 dict 무시
        conn=conn)
    assert n == 2
    assert fee_codes.resolve_notice_types("AA157", conn=conn) == ["REVIEW_CRITERIA", "TARIFF"]
    assert fee_codes.resolve_notice_types("J5301", conn=conn) == ["DRUG_MATERIAL", "REVIEW_CRITERIA"]


def test_resolve_unknown_returns_empty(conn):
    fee_codes.import_fee_codes([{"code": "AA157", "category": "TARIFF"}], conn=conn)
    assert fee_codes.resolve_notice_types("NOPE", conn=conn) == []
    assert fee_codes.resolve_notice_types("", conn=conn) == []
    assert fee_codes.resolve_notice_types(None, conn=conn) == []


def test_resolve_strips_whitespace(conn):
    fee_codes.import_fee_codes([{"code": "AA157", "category": "TARIFF"}], conn=conn)
    assert fee_codes.resolve_notice_types("  AA157 ", conn=conn) == ["REVIEW_CRITERIA", "TARIFF"]


def test_import_upsert_replaces(conn):
    fee_codes.import_fee_codes([{"code": "AA157", "category": "TARIFF"}], conn=conn)
    fee_codes.import_fee_codes([{"code": "AA157", "category": "DRUG"}], conn=conn)  # 갱신
    assert fee_codes.resolve_notice_types("AA157", conn=conn) == ["DRUG_MATERIAL", "REVIEW_CRITERIA"]
    assert fee_codes.fee_stats(conn=conn)["total"] == 1


def test_unmapped_category_resolves_empty(conn):
    # 적재됐지만 매핑규칙 없는 category → resolve []
    fee_codes.import_fee_codes([{"code": "ZZ", "category": "LABOR"}], conn=conn)
    assert fee_codes.resolve_notice_types("ZZ", conn=conn) == []


def test_fee_stats(conn):
    fee_codes.import_fee_codes(
        [{"code": "A", "category": "TARIFF"}, {"code": "B", "category": "DRUG"},
         {"code": "C", "category": "TARIFF"}], conn=conn)
    s = fee_codes.fee_stats(conn=conn)
    assert s["total"] == 3
    assert s["by_category"]["TARIFF"] == 2
    assert s["by_category"]["DRUG"] == 1
