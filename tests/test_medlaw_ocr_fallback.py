"""medlaw OCR 폴백 (v2.125.37) — 폭주 정리기 + index_files 구제 경로.

사전 등록 측정(docs/01-plan/references/medlaw-ocr-fallback-preregistration-2026-09-03.md)
으로 채택: 실사고 PDF(530076-1) vs 정답 hwpx — 정리 후 한글 60.1%·재현율 0.983·
식별자 5/5·1.2분.

불변식:
  1) 정리기는 구분선 폭주를 지우고 내용 줄(한글·영숫자·표 행)은 보존한다.
  2) 가드에 걸린 PDF는 OCR로 구제되고 parse_method=ocr로 표기된다.
  3) OCR 산출물도 같은 게이트를 통과해야 색인된다(쓰레기를 쓰레기로 대체 금지).
  4) PDF가 아니면 OCR을 시도하지 않고, OCR 예외는 색인을 죽이지 않는다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import core.medlaw_processor as mp
import core.medlaw_ocr as mo
import core.doc_parser as dp

GARBAGE = ("2026-309 「 」( 2025-65 , 2025.9.18.) 46 . 1. 2. 3. " * 60) + "의료기기"
CLEAN_KO = ("의료기기 기술문서심사기관 지정 및 운영 등에 관한 규정 일부개정고시안 "
            "제구조제이항 개정 규정에 따라 심사기관은 절차서를 마련하여야 한다 " * 12)


# ── 1) clean_ocr_text ───────────────────────────────────────────────────────

def test_cleaner_drops_separator_runaway():
    """실측 폭주 형상 — `---` 줄 수백 개가 통째로 사라진다."""
    text = "제1조(목적) 이 규정은\n" + "---  \n" * 300 + "부칙 이 고시는 시행한다"
    out = mo.clean_ocr_text(text)
    assert "---" not in out
    assert "제1조(목적)" in out and "부칙" in out


def test_cleaner_collapses_identical_runs_but_keeps_pairs():
    text = "가나다\n가나다\n가나다\n가나다\n라마바"
    out = mo.clean_ocr_text(text).splitlines()
    assert out.count("가나다") == 2          # 3연속부터 붕괴
    assert "라마바" in out


def test_cleaner_keeps_table_rows_with_digits():
    """숫자 표 행은 내용이다 — 지우면 수가표류 문서가 깨진다."""
    text = "| 250226 | 80% | 2026-05-01 |\n| 250226 | 80% | 2026-05-01 |"
    assert "250226" in mo.clean_ocr_text(text)


# ── 2~4) index_files 배선 ──────────────────────────────────────────────────

class _PD:
    def __init__(self, text, ok=True):
        self.text = text
        self.ok = ok


class _Col:
    def __init__(self):
        self.upserts = []
        self.metas = []

    def delete(self, where=None):
        pass

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upserts += documents
        self.metas += metadatas


@pytest.fixture
def wired(monkeypatch):
    col = _Col()
    import client.rag as rag
    monkeypatch.setattr(rag, "get_collection", lambda *a, **k: col)
    monkeypatch.setattr(rag, "embed_text", lambda t, prefix="": [0.0])
    monkeypatch.setattr(dp, "chunk_notice", lambda t: [t[:500]])
    monkeypatch.setattr(dp, "notice_metadata",
                        lambda p, t: {"source_file": os.path.basename(p), "origin": "khidi_notice"})
    monkeypatch.setattr(dp, "parse_document", lambda p: _PD(GARBAGE))
    return col


def test_garbage_pdf_recovered_via_ocr(monkeypatch, wired):
    monkeypatch.setattr(mo, "ocr_pdf_text", lambda p, **k: CLEAN_KO)
    r = mp.index_files(["/tmp/broken.pdf"])
    assert r["ocr_recovered"] == 1 and r["garbage_skipped"] == 0
    assert r["ok"] == 1
    assert wired.metas[0]["parse_method"] == "ocr"
    assert "기술문서심사기관" in wired.upserts[0]


def test_ocr_output_must_pass_same_gate(monkeypatch, wired):
    """OCR도 쓰레기를 낼 수 있다 — 같은 게이트 미통과면 색인 금지."""
    monkeypatch.setattr(mo, "ocr_pdf_text", lambda p, **k: GARBAGE)
    r = mp.index_files(["/tmp/broken.pdf"])
    assert r["ocr_recovered"] == 0 and r["garbage_skipped"] == 1
    assert wired.upserts == []


def test_non_pdf_garbage_skips_without_ocr(monkeypatch, wired):
    def boom(p, **k):
        raise AssertionError("PDF가 아닌데 OCR을 시도했다")
    monkeypatch.setattr(mo, "ocr_pdf_text", boom)
    r = mp.index_files(["/tmp/broken.hwpx"])
    assert r["garbage_skipped"] == 1 and r["ocr_recovered"] == 0


def test_ocr_exception_degrades_to_skip(monkeypatch, wired):
    def boom(p, **k):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(mo, "ocr_pdf_text", boom)
    r = mp.index_files(["/tmp/broken.pdf"])
    assert r["garbage_skipped"] == 1
    assert wired.upserts == []


def test_normal_document_untouched_by_fallback(monkeypatch, wired):
    """정상 문서 경로 불변 — OCR이 불리지 않는다."""
    monkeypatch.setattr(dp, "parse_document", lambda p: _PD(CLEAN_KO))
    def boom(p, **k):
        raise AssertionError("정상 문서에 OCR을 시도했다")
    monkeypatch.setattr(mo, "ocr_pdf_text", boom)
    r = mp.index_files(["/tmp/fine.pdf"])
    assert r["ok"] == 1 and r["ocr_recovered"] == 0
    assert "parse_method" not in wired.metas[0]