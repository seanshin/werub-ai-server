"""core.hr_law 순수 함수 단위 테스트 (2026-06-06 복구된 모듈).

classify_question(난이도 분류), select_model_by_difficulty(모델 선택),
format_law_reference(법령 인용 포맷). DB/RAG 의존 함수는 제외(순수 함수만).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hr_law import (  # noqa: E402
    classify_question,
    select_model_by_difficulty,
    format_law_reference,
)


class TestClassifyQuestion:
    def test_simple_keywords(self):
        # "정의", "뭔가요" = SIMPLE
        assert classify_question("근로기준법의 정의가 뭔가요?") == "simple"

    def test_complex_keywords(self):
        # "부당해고", "판례", "손해배상", "소송" = COMPLEX
        assert classify_question("부당해고 판례와 손해배상 소송 절차") == "complex"

    def test_complex_outweighs_simple(self):
        # COMPLEX("위반","처벌")=2 > SIMPLE("무엇")=1 → complex
        assert classify_question("위반 시 처벌은 무엇인가요?") == "complex"

    def test_tie_short_is_simple(self):
        # 키워드 없음 + 길이<30 → simple
        assert classify_question("연차는?") == "simple"

    def test_tie_long_is_complex(self):
        # 키워드 없음 + 길이>=30 → complex
        q = "근로계약 갱신 거절이 반복될 때 갱신기대권 인정 여부를 자세히 알려주세요"
        assert len(q) >= 30
        assert classify_question(q) == "complex"

    def test_how_keyword_simple(self):
        assert classify_question("연차휴가는 어떻게 계산하나요?") == "simple"


class TestSelectModel:
    def test_complex_uses_14b(self):
        assert select_model_by_difficulty("complex") == "qwen2.5:14b"

    def test_simple_uses_7b(self):
        assert select_model_by_difficulty("simple") == "qwen2.5:7b"

    def test_unknown_defaults_7b(self):
        assert select_model_by_difficulty("") == "qwen2.5:7b"
        assert select_model_by_difficulty("weird") == "qwen2.5:7b"


class TestFormatLawReference:
    def test_full(self):
        assert format_law_reference("근로기준법", "제23조", "계약 기간") == "[근로기준법 제23조] 계약 기간"

    def test_article_only(self):
        assert format_law_reference("근로기준법", "제23조", "") == "[근로기준법 제23조]"

    def test_law_only(self):
        assert format_law_reference("근로기준법", "", "") == "[근로기준법]"

    def test_title_without_article_falls_back_to_law(self):
        # article 없으면 title 있어도 법률명만
        assert format_law_reference("근로기준법", "", "계약 기간") == "[근로기준법]"
