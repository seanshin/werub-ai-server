"""/api/rag/ask·index collection 전달 회귀 가드 (edu 연동서 발견한 비대칭 봉합).

기존: search만 collection을 받고 ask는 기본 컬렉션(werub_docs) 고정, index는
청크 경로만 반영·단건/배치는 무시 → 호출자가 collection을 지정해도 조용히
기본 컬렉션으로 색인/검색되는 무성 결함. edu 계약 {question, collection} 지원.

routes.rag는 client.rag(chromadb)를 import하므로 chromadb/flask 없으면 skip.
실행(GPU): venv/bin/python -m pytest tests/test_rag_collection.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("chromadb")
pytest.importorskip("flask")

from flask import Flask  # noqa: E402

import routes.rag as R  # noqa: E402

app = Flask(__name__)


def _capture_search(calls):
    def fake_search(question, n_results=3, collection_name="werub_docs", where=None):
        calls["collection"] = collection_name
        return []                                   # 소스 0 → LLM 미호출 조기 반환
    return fake_search


def test_ask_passes_collection(monkeypatch):
    calls = {}
    monkeypatch.setattr(R, "rag_search", _capture_search(calls))
    with app.test_request_context(json={"question": "q", "collection": "edu_docs"}):
        resp = R.rag_ask()
    assert calls["collection"] == "edu_docs"
    assert resp.get_json()["sources"] == []


def test_ask_defaults_to_werub_docs(monkeypatch):
    calls = {}
    monkeypatch.setattr(R, "rag_search", _capture_search(calls))
    with app.test_request_context(json={"question": "q"}):
        R.rag_ask()
    assert calls["collection"] == "werub_docs"


def test_index_single_passes_collection(monkeypatch):
    calls = {}

    def fake_index(doc_id, text, metadata=None, collection_name="werub_docs"):
        calls["collection"] = collection_name
        return {"ok": True, "doc_id": doc_id}
    monkeypatch.setattr(R, "index_document", fake_index)
    monkeypatch.setattr(R, "fts_index_batch", lambda docs, collection=None: None)
    with app.test_request_context(json={"id": "d1", "text": "본문", "collection": "edu_docs"}):
        resp = R.rag_index.__wrapped__()            # require_auth 우회(원함수)
    assert calls["collection"] == "edu_docs"
    assert resp.get_json()["ok"] is True


def test_index_single_writes_fts(monkeypatch):
    """단건 색인도 FTS 동시 기록 — 배치만 쓰던 비대칭 봉합(키워드 검색 사각 제거)."""
    fts = {}
    monkeypatch.setattr(R, "index_document",
                        lambda doc_id, text, metadata=None, collection_name="werub_docs":
                        {"ok": True, "doc_id": doc_id})

    def fake_fts(docs, collection="werub_docs"):
        fts["docs"] = docs
        fts["collection"] = collection
    monkeypatch.setattr(R, "fts_index_batch", fake_fts)
    with app.test_request_context(json={"id": "d1", "text": "본문",
                                        "metadata": {"courseId": "C1"},
                                        "collection": "edu_docs"}):
        R.rag_index.__wrapped__()
    assert fts["collection"] == "edu_docs"
    assert fts["docs"] == [{"id": "d1", "text": "본문", "metadata": {"courseId": "C1"}}]


def test_index_chunked_writes_fts(monkeypatch):
    """청크 색인도 부모 doc_id로 FTS 기록 — delete 동반 정리와 대칭."""
    fts = {}
    monkeypatch.setattr(R, "index_document_chunked",
                        lambda *a, **k: {"ok": True, "doc_id": a[0], "chunks": 2})
    monkeypatch.setattr(R, "fts_index_batch",
                        lambda docs, collection="werub_docs": fts.update(ids=[d["id"] for d in docs]))
    with app.test_request_context(json={"id": "d1", "text": "본문", "collection": "edu_docs",
                                        "chunk_strategy": "paragraph"}):
        resp = R.rag_index.__wrapped__()
    assert fts["ids"] == ["d1"]
    assert resp.get_json()["chunks"] == 2


class _FakeCol:
    """삭제 검증용 — id 목록을 들고 있다가 delete로 지운다."""

    def __init__(self, ids):
        self.ids = list(ids)
        self.deletes = []

    def delete(self, ids=None, where=None):
        self.deletes.append({"ids": ids, "where": where})
        if ids:
            self.ids = [i for i in self.ids if i not in set(ids)]
        if where and where.get("source_doc_id") == "meta-hit":
            self.ids = []

    def get(self, ids=None, include=None):
        return {"ids": list(self.ids)}

    def count(self):
        return len(self.ids)


def test_client_delete_cleans_chunks(monkeypatch):
    """client.delete_document — 부모 id + source_doc_id 청크 동반 삭제 + **건수 반환**."""
    import client.rag as C

    col = _FakeCol(["d1"])
    monkeypatch.setattr(C, "get_collection", lambda name: col)
    out = C.delete_document("d1", collection_name="edu_docs")
    assert out == {"ok": True, "deleted": 1}
    assert {"ids": ["d1"], "where": None} in col.deletes
    assert {"ids": None, "where": {"source_doc_id": "d1"}} in col.deletes


def test_delete_falls_back_to_id_prefix(monkeypatch):
    """`source_doc_id` 메타가 없는 청크(배치 색인 등)도 id 접두로 지운다.

    2026-07-30 Cerno 실측: 부모 id 삭제가 무효인데 `ok:true`가 나가 문서가 조용히 잔존했다.
    """
    import client.rag as C

    col = _FakeCol(["guide-hf__chunk_0", "guide-hf__chunk_1", "other__chunk_0"])
    monkeypatch.setattr(C, "get_collection", lambda name: col)
    out = C.delete_document("guide-hf", collection_name="cerno_org")
    assert out["deleted"] == 2
    assert col.ids == ["other__chunk_0"], "다른 문서 청크는 남아야 한다"


def test_delete_reports_zero_when_nothing_matched(monkeypatch):
    """멱등이라 ok:true지만 **deleted=0**으로 '지울 게 없었다'를 구분할 수 있어야 한다."""
    import client.rag as C

    col = _FakeCol(["keep-1"])
    monkeypatch.setattr(C, "get_collection", lambda name: col)
    assert C.delete_document("nope", collection_name="cerno_org") == {"ok": True, "deleted": 0}


def test_delete_prefix_does_not_overmatch_similar_ids(monkeypatch):
    """`note`로 `note-A`를 지우면 안 된다 — 구분자(__chunk_)까지 포함해 비교한다."""
    import client.rag as C

    col = _FakeCol(["note-A__chunk_0", "note__chunk_0"])
    monkeypatch.setattr(C, "get_collection", lambda name: col)
    out = C.delete_document("note", collection_name="cerno_priv")
    assert out["deleted"] == 1 and col.ids == ["note-A__chunk_0"]


def test_delete_passes_collection(monkeypatch):
    calls = {}

    def fake_delete(doc_id, collection_name="werub_docs"):
        calls["collection"] = collection_name
        return {"ok": True, "doc_id": doc_id}
    monkeypatch.setattr(R, "delete_document", fake_delete)
    monkeypatch.setattr(R, "fts_delete", lambda doc_id, collection=None: None)
    with app.test_request_context("/api/rag/document/d1?collection=edu_docs"):
        R.rag_delete.__wrapped__("d1")
    assert calls["collection"] == "edu_docs"


def test_delete_cleans_fts(monkeypatch):
    """벡터 삭제 시 FTS 동반 정리 — 미정리면 hybrid 검색 FTS 경로로 재부상."""
    fts_calls = {}
    monkeypatch.setattr(R, "delete_document",
                        lambda doc_id, collection_name="werub_docs": {"ok": True})

    def fake_fts_delete(doc_id, collection="werub_docs"):
        fts_calls["doc_id"] = doc_id
        fts_calls["collection"] = collection
    monkeypatch.setattr(R, "fts_delete", fake_fts_delete)
    with app.test_request_context("/api/rag/document/d1?collection=edu_docs"):
        resp = R.rag_delete.__wrapped__("d1")
    assert fts_calls == {"doc_id": "d1", "collection": "edu_docs"}
    assert resp.get_json()["ok"] is True


def test_delete_fts_failure_non_critical(monkeypatch):
    """FTS 정리 실패는 벡터 삭제 결과를 막지 않음 (best-effort)."""
    monkeypatch.setattr(R, "delete_document",
                        lambda doc_id, collection_name="werub_docs": {"ok": True})

    def boom(doc_id, collection="werub_docs"):
        raise RuntimeError("fts down")
    monkeypatch.setattr(R, "fts_delete", boom)
    with app.test_request_context("/api/rag/document/d1"):
        resp = R.rag_delete.__wrapped__("d1")
    assert resp.get_json()["ok"] is True


def test_stats_passes_collection(monkeypatch):
    calls = {}

    def fake_stats(collection_name="werub_docs"):
        calls["collection"] = collection_name
        return {"count": 0}
    monkeypatch.setattr(R, "collection_stats", fake_stats)
    with app.test_request_context("/api/rag/stats?collection=edu_docs"):
        R.rag_stats()
    assert calls["collection"] == "edu_docs"


def test_index_batch_passes_collection(monkeypatch):
    calls = {}

    def fake_batch(docs, collection_name="werub_docs"):
        calls["collection"] = collection_name
        return {"ok": True, "count": len(docs)}
    monkeypatch.setattr(R, "index_batch", fake_batch)
    monkeypatch.setattr(R, "fts_index_batch", lambda docs, collection=None: None)
    docs = [{"id": "d1", "text": "본문"}]
    with app.test_request_context(json={"documents": docs, "collection": "edu_docs"}):
        resp = R.rag_index.__wrapped__()
    assert calls["collection"] == "edu_docs"
    assert resp.get_json()["count"] == 1
