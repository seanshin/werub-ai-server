"""한국어 맞춤법 검사 모듈 테스트"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.korean_spellcheck import (
    spellcheck,
    spellcheck_long,
    get_spellcheck_summary,
    _split_text,
)


class TestSplitText:
    def test_short_text(self):
        chunks = _split_text("짧은 텍스트")
        assert len(chunks) == 1

    def test_exact_limit(self):
        text = "가" * 500
        chunks = _split_text(text, max_chars=500)
        assert len(chunks) == 1

    def test_over_limit_split(self):
        text = "첫번째 문장이다. 두번째 문장이다. " * 30  # ~500자 초과
        chunks = _split_text(text, max_chars=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 200 or "." not in chunk  # 강제 분할 제외

    def test_empty(self):
        assert _split_text("") == [""]

    def test_single_long_sentence(self):
        text = "가나다라" * 200  # 800자, 문장 구분 없음
        chunks = _split_text(text, max_chars=500)
        assert len(chunks) >= 2


class TestSpellcheck:
    """실제 네이버 API 호출 테스트 — 네트워크 필요"""

    @pytest.mark.skipif(
        os.environ.get("SKIP_NETWORK_TESTS") == "1",
        reason="Network tests skipped",
    )
    def test_basic_correction(self):
        result = spellcheck("안녕 하세요. 저는 한국인 입니다.")
        assert result["success"] is True
        assert result["errors"] >= 0
        assert result["checked"]  # 비어있지 않음
        assert result["duration_ms"] >= 0

    @pytest.mark.skipif(
        os.environ.get("SKIP_NETWORK_TESTS") == "1",
        reason="Network tests skipped",
    )
    def test_correct_text(self):
        result = spellcheck("안녕하세요. 저는 한국인입니다.")
        assert result["success"] is True

    def test_empty_text(self):
        result = spellcheck("")
        assert result["success"] is True
        assert result["errors"] == 0
        assert result["checked"] == ""

    def test_none_text(self):
        result = spellcheck(None)
        assert result["success"] is True

    def test_whitespace_only(self):
        result = spellcheck("   ")
        assert result["success"] is True


class TestSpellcheckLong:
    def test_empty(self):
        result = spellcheck_long("")
        assert result["success"] is True
        assert result["total_errors"] == 0
        assert result["chunks_count"] == 0

    @pytest.mark.skipif(
        os.environ.get("SKIP_NETWORK_TESTS") == "1",
        reason="Network tests skipped",
    )
    def test_short_text(self):
        result = spellcheck_long("안녕 하세요.")
        assert result["success"] is True
        assert result["chunks_count"] == 1

    @pytest.mark.skipif(
        os.environ.get("SKIP_NETWORK_TESTS") == "1",
        reason="Network tests skipped",
    )
    def test_long_text(self):
        # 500자 초과 텍스트
        text = "오늘은 날씨가 좋다. " * 50  # ~550자
        result = spellcheck_long(text)
        assert result["success"] is True
        assert result["chunks_count"] >= 1
        assert isinstance(result["checked"], str)


class TestSummary:
    def test_short_summary(self):
        result = {
            "success": True,
            "errors": 2,
            "duration_ms": 150,
            "words": {"안녕하세요": "passed", "입니 다": "wrong_spacing"},
        }
        summary = get_spellcheck_summary(result)
        assert summary["errors"] == 2
        assert summary["success"] is True

    def test_long_summary(self):
        result = {
            "success": True,
            "total_errors": 5,
            "chunks_count": 3,
            "duration_ms": 450,
        }
        summary = get_spellcheck_summary(result)
        assert summary["errors"] == 5
        assert summary["chunks"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
