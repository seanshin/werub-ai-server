"""v2.87.0 — 의료 지식베이스 테넌트 격리(_kb_tenant_ok/_tenant_kb_filter) 단위테스트.

routes.medical는 의존이 무거워 flask/chromadb 없으면 skip(GPU 실행).
실행(GPU): venv/bin/python -m pytest tests/test_medical_isolation.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("flask")
pytest.importorskip("chromadb")

import routes.medical as m  # noqa: E402


def test_kb_tenant_ok_allows_shared_legacy_self():
    assert m._kb_tenant_ok({"tenant_id": "shared"}, "t1") is True   # 공유
    assert m._kb_tenant_ok({"tenant_id": "t1"}, "t1") is True       # 자기 테넌트
    assert m._kb_tenant_ok({}, "t1") is True                        # 레거시(메타 없음)=공유 보존
    assert m._kb_tenant_ok({"tenant_id": None}, "t1") is True
    assert m._kb_tenant_ok({"tenant_id": ""}, "t1") is True


def test_kb_tenant_ok_blocks_other_tenant():
    assert m._kb_tenant_ok({"tenant_id": "t2"}, "t1") is False


def test_tenant_kb_filter_drops_other_tenant_private():
    docs = [
        {"id": "1", "metadata": {"tenant_id": "shared"}},
        {"id": "2", "metadata": {"tenant_id": "t1"}},
        {"id": "3", "metadata": {"tenant_id": "t2"}},   # 타 테넌트 비공개 → 제외
        {"id": "4", "metadata": {}},                     # 레거시 → 보존
    ]
    assert [d["id"] for d in m._tenant_kb_filter(docs, "t1")] == ["1", "2", "4"]


def test_tenant_kb_filter_empty_inputs():
    assert m._tenant_kb_filter([], "t1") == []
    assert m._tenant_kb_filter(None, "t1") == []
