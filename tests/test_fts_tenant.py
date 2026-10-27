"""v2.88.3 — FTS5 키워드 검색 테넌트 격리 단위테스트 (H1 fix).

순수 SQLite FTS5 — Ollama/ChromaDB 호출 없음(모듈 임포트에만 chromadb 필요 → GPU에서 실행).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("chromadb")
pytest.importorskip("requests")

import client.rag as rag  # noqa: E402


@pytest.fixture()
def fts_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "_FTS_DB_PATH", str(tmp_path / "fts.db"))
    rag.fts_index("d-shared", "공유 고혈압 가이드라인", title="공유문서", collection="medical_knowledge", tenant="shared")
    rag.fts_index("d-t1", "테넌트1 비공개 고혈압 프로토콜", title="t1문서", collection="medical_knowledge", tenant="t1")
    rag.fts_index("d-t2", "테넌트2 비공개 고혈압 프로토콜", title="t2문서", collection="medical_knowledge", tenant="t2")
    return rag


def test_fts_tenant_isolation(fts_tmp):
    ids = {r["doc_id"] for r in rag.fts_search("고혈압", collection="medical_knowledge", tenant="t1")}
    assert ids == {"d-shared", "d-t1"}  # 타 테넌트(t2) 비공개 제외


def test_fts_no_tenant_backcompat(fts_tmp):
    # tenant 미지정 = 필터 없음 (werub_docs 등 테넌트 무관 컬렉션 호환)
    ids = {r["doc_id"] for r in rag.fts_search("고혈압", collection="medical_knowledge")}
    assert ids == {"d-shared", "d-t1", "d-t2"}


def test_fts_legacy_migration(tmp_path, monkeypatch):
    # 구 스키마(tenant 없음) → 자동 마이그레이션, 기존 행은 'shared'로 보존·검색됨
    import sqlite3
    p = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(p)
    conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id, text, title, category, collection, tokenize='unicode61')")
    conn.execute("INSERT INTO docs_fts VALUES ('old-1', '레거시 고혈압 문서', '', '', 'medical_knowledge')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(rag, "_FTS_DB_PATH", p)
    rows = rag.fts_search("고혈압", collection="medical_knowledge", tenant="t1")
    assert [r["doc_id"] for r in rows] == ["old-1"]
    assert rows[0]["tenant"] == "shared"


def test_fts_index_batch_tenant(fts_tmp, tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "_FTS_DB_PATH", str(tmp_path / "batch.db"))
    rag.fts_index_batch([
        {"id": "b1", "text": "당뇨 배치 공유", "metadata": {"title": "b1"}},
        {"id": "b2", "text": "당뇨 배치 비공개", "metadata": {"title": "b2", "tenant_id": "t9"}},
    ], collection="medical_knowledge")
    ids = {r["doc_id"] for r in rag.fts_search("당뇨", collection="medical_knowledge", tenant="t1")}
    assert ids == {"b1"}  # tenant_id 없는 배치행='shared' 통과, t9 비공개 제외


def test_hybrid_fts_only_metadata_has_tenant(fts_tmp, monkeypatch):
    # hybrid의 FTS-only 항목 metadata에 tenant_id 동봉 → _tenant_kb_filter 일관 판정 가능
    monkeypatch.setattr(rag, "search", lambda *a, **k: [])  # 벡터 결과 0 강제
    res = rag.hybrid_search("고혈압", collection_name="medical_knowledge", tenant="t1", min_score=0)
    assert res, "FTS-only 결과 있어야 함"
    assert all(r["metadata"].get("tenant_id") in ("shared", "t1") for r in res)
