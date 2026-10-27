"""medlaw 색인 파싱 붕괴 가드 (v2.125.36).

🔴 실사고(2026-09-03): 폰트 toUnicode 결손 PDF(530076-1)가 **한글 2.1%** 쓰레기
텍스트로 파싱되고도 그대로 색인돼 검색을 오염시켰다(ambiguous 쌍 판독에서 발견).
가드=본문 500자 이상 & 한글 비율 5% 미만이면 색인하지 않고 `garbage_skipped`로
보고(무성 드롭 금지). 임계 5%는 코퍼스 실측(정상 최저 11.9% vs 붕괴 2.1%)에서 왔다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import core.medlaw_processor as mp
import core.doc_parser as dp


class _PD:
    def __init__(self, text, ok=True):
        self.text = text
        self.ok = ok


class _Col:
    def __init__(self):
        self.upserts = []

    def delete(self, where=None):
        pass

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upserts += documents


@pytest.fixture
def wired(monkeypatch):
    col = _Col()
    import client.rag as rag
    monkeypatch.setattr(rag, "get_collection", lambda *a, **k: col)
    monkeypatch.setattr(rag, "embed_text", lambda t, prefix="": [0.0])
    monkeypatch.setattr(dp, "chunk_notice", lambda t: [t[:500]])
    monkeypatch.setattr(dp, "notice_metadata",
                        lambda p, t: {"source_file": os.path.basename(p), "origin": "khidi_notice"})
    return col


def _with_parse(monkeypatch, text):
    monkeypatch.setattr(dp, "parse_document", lambda p: _PD(text))


def test_garbage_parse_is_skipped_and_reported(monkeypatch, wired):
    """실사고 형상 — 긴 본문인데 한글이 거의 없다(2%대) → 색인 0 + 보고."""
    garbage = ("2026-309 「 」( 2025-65 , 2025.9.18.) 46 . 1. 2. 3. " * 60) + "의료기기"
    assert len(garbage) >= 500
    _with_parse(monkeypatch, garbage)
    r = mp.index_files(["/tmp/broken.pdf"])
    assert r["garbage_skipped"] == 1
    assert r["ok"] == 0
    assert wired.upserts == [], "쓰레기 텍스트가 색인됐다 — 검색 오염"


def test_table_heavy_but_legit_passes(monkeypatch, wired):
    """정상 최저선 형상(표 위주·한글 ~12%)은 색인된다 — 임계가 과잉이면 실문서를 버린다."""
    row = "K05070.01 IVD reagents 80% 2026-05-01 250226 자가검사용시약 "
    text = row * 30
    ratio = sum("가" <= c <= "힣" for c in text) / len(text)
    assert 0.05 < ratio < 0.2, f"통제군 형상이 의도와 다르다: {ratio:.2%}"
    _with_parse(monkeypatch, text)
    r = mp.index_files(["/tmp/table.pdf"])
    assert r["garbage_skipped"] == 0 and r["ok"] == 1


def test_short_text_is_not_judged(monkeypatch, wired):
    """500자 미만은 판정 보류 — 짧은 표지·서지는 비율이 흔들린다."""
    _with_parse(monkeypatch, "2026-309 (short cover page) 1234567890")
    r = mp.index_files(["/tmp/short.pdf"])
    assert r["garbage_skipped"] == 0 and r["ok"] == 1
