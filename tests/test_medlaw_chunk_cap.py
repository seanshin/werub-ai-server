"""RAG 청크 프롬프트 캡 회귀 (v2.124.6 — 600→1500 상향, config `medlaw_chunk_chars`).
열거 조문이 캡에 잘려 못 본 호를 기억으로 채우던 환각 클래스 봉합의 튜닝 레버."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routes.medical_law as ML


def test_default_1500(monkeypatch):
    monkeypatch.setattr(ML, "load_config", lambda: {})
    assert ML._chunk_cap() == 1500


def test_config_override(monkeypatch):
    monkeypatch.setattr(ML, "load_config", lambda: {"medlaw_chunk_chars": 2500})
    assert ML._chunk_cap() == 2500


def test_clamp_low_high(monkeypatch):
    monkeypatch.setattr(ML, "load_config", lambda: {"medlaw_chunk_chars": 50})
    assert ML._chunk_cap() == 200
    monkeypatch.setattr(ML, "load_config", lambda: {"medlaw_chunk_chars": 99999})
    assert ML._chunk_cap() == 4000


def test_bad_value_falls_back(monkeypatch):
    monkeypatch.setattr(ML, "load_config", lambda: {"medlaw_chunk_chars": "abc"})
    assert ML._chunk_cap() == 1500
    monkeypatch.setattr(ML, "load_config", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert ML._chunk_cap() == 1500
