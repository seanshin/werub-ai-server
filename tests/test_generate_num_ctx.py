"""/api/generate 대형 프롬프트 num_ctx 자동 상향 (절단 방지) — 순수 헬퍼 회귀.

2026-07-25 실측: node 호출자가 8K~17K tok 프롬프트를 /api/generate로 보냈으나 핸들러가
options를 전달하지 않아 전역 슬롯 컨텍스트(≈8192)에서 조용히 절단(limit≈4098)되던 결함 봉합.
"""
import routes.ollama as O


def test_estimate_tokens_korean_denser_than_english():
    # 같은 문자 수라도 한글이 영문보다 토큰이 많게 추정(qwen BPE 특성 반영).
    ko = O._estimate_tokens("가" * 1000)
    en = O._estimate_tokens("a" * 1000)
    assert ko > en
    assert O._estimate_tokens("") == 0
    assert O._estimate_tokens(None) == 0


def test_small_prompt_no_injection(monkeypatch):
    monkeypatch.setattr(O, "_load_config", lambda: {})
    assert O._auto_num_ctx("짧은 질문입니다") is None
    assert O._auto_num_ctx("a" * 5000) is None  # 영문 5000자 ≈ 1600 tok, 슬롯 이내


def test_large_korean_prompt_bumps_to_cap(monkeypatch):
    monkeypatch.setattr(O, "_load_config", lambda: {})
    # 한글 9000자 ≈ 10800 tok > 8192 → 상한(16384)으로 상향.
    assert O._auto_num_ctx("가" * 9000) == O._GEN_MAX_NUM_CTX_DEFAULT


def test_system_counts_toward_estimate(monkeypatch):
    monkeypatch.setattr(O, "_load_config", lambda: {})
    # 프롬프트 단독은 슬롯 이내지만 system 합산 시 초과 → 상향.
    assert O._auto_num_ctx("가" * 4000, "가" * 4000) == O._GEN_MAX_NUM_CTX_DEFAULT


def test_disabled_by_config(monkeypatch):
    monkeypatch.setattr(O, "_load_config", lambda: {"generate_auto_num_ctx": False})
    assert O._auto_num_ctx("가" * 20000) is None


def test_custom_cap(monkeypatch):
    monkeypatch.setattr(O, "_load_config", lambda: {"generate_max_num_ctx": 12000})
    assert O._auto_num_ctx("가" * 9000) == 12000


def test_cap_at_or_below_slot_disables(monkeypatch):
    # 상한이 슬롯 이하면 상향 의미 없음 → 미주입(기존 동작).
    monkeypatch.setattr(O, "_load_config", lambda: {"generate_max_num_ctx": 8192})
    assert O._auto_num_ctx("가" * 20000) is None


def test_bad_config_falls_back(monkeypatch):
    monkeypatch.setattr(O, "_load_config", lambda: {"generate_max_num_ctx": "oops"})
    assert O._auto_num_ctx("가" * 9000) == O._GEN_MAX_NUM_CTX_DEFAULT
    # _load_config 예외도 graceful.
    def boom():
        raise RuntimeError("no config")
    monkeypatch.setattr(O, "_load_config", boom)
    assert O._auto_num_ctx("가" * 9000) == O._GEN_MAX_NUM_CTX_DEFAULT
