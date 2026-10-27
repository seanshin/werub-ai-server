"""v2.87.0 — rag/search·ask `filter`→ChromaDB where 변환(_build_where) 단위테스트.

routes.rag는 client.rag(chromadb)를 import하므로 chromadb/flask 없으면 skip.
실행(GPU): venv/bin/python -m pytest tests/test_rag_filter.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("chromadb")
pytest.importorskip("flask")

from routes.rag import _build_where  # noqa: E402


def test_none_and_empty():
    assert _build_where(None) is None
    assert _build_where({}) is None


def test_single_key_equality():
    assert _build_where({"owner": "u1"}) == {"owner": "u1"}


def test_multi_key_and():
    assert _build_where({"owner": "u1", "ns": "cardio"}) == {
        "$and": [{"owner": "u1"}, {"ns": "cardio"}]
    }


def test_operator_value_passthrough():
    assert _build_where({"owner": {"$in": ["u1", "u2"]}}) == {"owner": {"$in": ["u1", "u2"]}}


def test_top_level_operator_passthrough():
    raw = {"$or": [{"owner": "u1"}, {"ns": "shared"}]}
    assert _build_where(raw) == raw


def test_invalid_type_raises():
    with pytest.raises(ValueError):
        _build_where(["not", "a", "dict"])


def test_too_many_keys_raises():
    with pytest.raises(ValueError):
        _build_where({f"k{i}": i for i in range(11)})
