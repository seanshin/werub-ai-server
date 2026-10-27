"""scripts/eval_faithfulness.get_evidence_chunks 일과성 실패 재시도 (07-27 id360).

배치 첫 문항에서 본 코퍼스(medical_law_knowledge) 검색이 임베딩 콜드 로드
timeout으로 1회 실패 → 구 코드는 `except: pass`로 무성 스킵 → 근거가 발간물만
남아 judge가 정확한 답변을 contradicted 오판(배치 위양성·지수 하락 실측).
fix = 컬렉션별 1회 재시도 + 실패 로그. 본 테스트는 ①1회 실패 후 재시도 복구
②재시도 소진 시 해당 컬렉션만 제외하고 진행(graceful)을 가드.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "eval_faithfulness_script", os.path.join(ROOT, "scripts", "eval_faithfulness.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_sleep(monkeypatch, mod):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)  # 재시도 대기 스킵


def test_evidence_retry_recovers_after_transient_failure(monkeypatch):
    mod = _load_runner()
    _patch_sleep(monkeypatch, mod)
    calls = {"legal": 0}

    def fake_search(query, n_results=5, collection_name="", where=None, min_score=0.3):
        if collection_name == "medical_law_knowledge":
            calls["legal"] += 1
            if calls["legal"] == 1:
                raise RuntimeError("embed cold timeout")     # 1차 일과성 실패
            return [{"id": "l1", "text": "제15조(진료거부 금지 등) 정당한 사유 없이 거부하지 못한다.",
                     "metadata": {}, "score": 0.7}]
        return []

    import client.rag
    monkeypatch.setattr(client.rag, "search", fake_search)
    chunks = mod.get_evidence_chunks("존재하지않는질의xx", top_k=5)
    assert any("제15조" in c for c in chunks)                 # 재시도로 복구
    assert calls["legal"] >= 2


def test_evidence_retry_exhausted_stays_graceful(monkeypatch, capsys):
    mod = _load_runner()
    _patch_sleep(monkeypatch, mod)

    def fake_search(query, n_results=5, collection_name="", where=None, min_score=0.3):
        if collection_name == "medical_law_knowledge":
            raise RuntimeError("still down")                  # 재시도까지 전부 실패
        return [{"id": "p1", "text": "발간물 근거 조각", "metadata": {}, "score": 0.5}]

    import client.rag
    monkeypatch.setattr(client.rag, "search", fake_search)
    chunks = mod.get_evidence_chunks("존재하지않는질의xx", top_k=5)
    assert any("발간물 근거" in c for c in chunks)            # 발간물은 유지(전파 없음)
    err = capsys.readouterr().err
    assert "재시도 소진" in err                                # 무성 실패 금지 — 로그 필수
