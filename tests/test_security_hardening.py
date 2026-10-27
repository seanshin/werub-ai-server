"""방어심층 하드닝(보안 P3 L3·L5·L6) 회귀.

L3=LIKE 이스케이프(검색 거동 왜곡 방지) · L5=보안 헤더 · L6=프롬프트 문자 캡.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── L3: LIKE 패턴 이스케이프 ──────────────────────────────────────────────
def test_like_escapes_wildcards():
    from routes.medical_law import _like
    assert _like("의료법") == "%의료법%"
    assert _like("100%") == r"%100\%%"          # % 리터럴화
    assert _like("a_b") == r"%a\_b%"            # _ 리터럴화
    assert _like("a\\b") == r"%a\\b%"           # 역슬래시 우선 처리
    assert _like("") == "%%" and _like(None) == "%%"


# ── L6: 프롬프트 문자 캡 ──────────────────────────────────────────────────
def test_prompt_cap_allows_normal_and_blocks_huge(monkeypatch):
    import routes.ollama as O
    monkeypatch.setattr(O, "_load_config", lambda: {"max_prompt_chars": 100})
    assert O._prompt_overflow("짧은 프롬프트") is None
    assert O._prompt_overflow("x" * 101) == (101, 100)


def test_prompt_cap_sums_parts(monkeypatch):
    import routes.ollama as O
    monkeypatch.setattr(O, "_load_config", lambda: {"max_prompt_chars": 100})
    assert O._prompt_overflow("x" * 60, "y" * 60) == (120, 100)   # 합산 초과
    assert O._prompt_overflow("x" * 40, None, 123) is None         # 문자열 아닌 값 무시


def test_prompt_cap_disabled_when_zero(monkeypatch):
    import routes.ollama as O
    monkeypatch.setattr(O, "_load_config", lambda: {"max_prompt_chars": 0})
    assert O._prompt_overflow("x" * 10_000_000) is None


def test_prompt_cap_default_is_generous(monkeypatch):
    """전역 num_ctx(8192)로 쓸 수 있는 입력(~30KB)보다 충분히 크다."""
    import routes.ollama as O
    monkeypatch.setattr(O, "_load_config", lambda: {})
    assert O._prompt_cap() >= 100_000
