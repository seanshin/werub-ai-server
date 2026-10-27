"""한국어 AI 텍스트 자연화 메트릭 테스트

원본: epoko77-ai/im-not-ai test_metrics.py, test_metrics_v2.py 이식 + 확장
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.korean_humanize import (
    # v1.6
    comma_inclusion_rate,
    comma_usage_rate,
    ending_comma_rate,
    comma_segment_length,
    conclusion_pivot_count,
    safe_balance_count,
    hanja_nominalizer_density,
    lexical_diversity,
    # v2.0 simplification
    lexical_diversity_ttr,
    lexical_density,
    ending_diversity,
    # v2.0 normalisation
    normalisation_score,
    da_streak_rate,
    # v2.0 interference
    inanimate_subject_rate,
    by_passive_count,
    double_passive_count,
    pronoun_density,
    deul_overuse_rate,
    relative_clause_nesting,
    have_make_literal_count,
    double_particle_count,
    progressive_aspect_rate,
    # 통합
    interference_index,
    compute_korean_quality,
    get_s1_prompt_rules,
    get_quality_summary,
)


# ---------------------------------------------------------------------------
# v1.6 메트릭 테스트
# ---------------------------------------------------------------------------

class TestCommaMetrics:
    def test_comma_inclusion_empty(self):
        assert comma_inclusion_rate("") == 0.0

    def test_comma_inclusion_basic(self):
        text = "첫 문장이다. 둘째, 이것이다. 셋째이다."
        rate = comma_inclusion_rate(text)
        assert 0.3 <= rate <= 0.4  # 1/3

    def test_comma_usage_rate(self):
        text = "하나, 둘, 셋이다. 넷이다."
        rate = comma_usage_rate(text)
        assert rate > 0.5  # 2 commas / 2 sentences = 1.0

    def test_ending_comma_rate_with_connective(self):
        text = "이것은 중요하고, 저것도 중요하다. 그러나 이것은 다르며, 저것은 같다."
        rate = ending_comma_rate(text)
        assert rate > 0  # 연결어미 뒤 쉼표 존재

    def test_ending_comma_rate_clean(self):
        text = "이것은 중요하고 저것도 중요하다. 그러나 이것은 다르며 저것은 같다."
        rate = ending_comma_rate(text)
        assert rate == 0.0  # 연결어미 뒤 쉼표 없음

    def test_comma_segment_length(self):
        text = "나는 오늘, 학교에 갔다."
        length = comma_segment_length(text)
        assert length > 0

    def test_comma_segment_no_comma(self):
        text = "나는 오늘 학교에 갔다."
        length = comma_segment_length(text)
        assert length > 0  # 문장 전체가 1 segment


class TestLexiconMetrics:
    def test_conclusion_pivot_basic(self):
        text = "따라서 이것은 중요하다. 결론적으로 성공이다."
        assert conclusion_pivot_count(text) == 2

    def test_conclusion_pivot_zero(self):
        text = "오늘 날씨가 좋다."
        assert conclusion_pivot_count(text) == 0

    def test_safe_balance(self):
        text = "양쪽 모두 장점이 있다. 신중하게 판단해야 한다."
        assert safe_balance_count(text) == 2

    def test_hanja_density(self):
        text = "혁신적 변화와 전략적 접근이 필요하다."
        density = hanja_nominalizer_density(text)
        assert density > 0  # "혁신적", "전략적" → -적 2건

    def test_hanja_density_single_char(self):
        # 단독 "적", "성" 은 제외해야 함
        text = "적 성 화"
        density = hanja_nominalizer_density(text)
        assert density == 0.0

    def test_lexical_diversity_basic(self):
        text = "나는 나는 나는 학교에 갔다."
        div = lexical_diversity(text)
        assert div < 1.0  # 반복어 있으므로

    def test_lexical_diversity_unique(self):
        text = "사과 배 포도 수박 참외"
        div = lexical_diversity(text)
        assert div == 1.0  # 전부 고유

    def test_lexical_diversity_empty(self):
        assert lexical_diversity("") == 0.0


# ---------------------------------------------------------------------------
# v2.0 메트릭 테스트
# ---------------------------------------------------------------------------

class TestSimplificationMetrics:
    def test_ttr_alias(self):
        text = "나는 학교에 갔다."
        assert lexical_diversity_ttr(text) == lexical_diversity(text)

    def test_lexical_density_basic(self):
        text = "혁신적 변화가 필요하다. 전략적 접근이 중요하다."
        density = lexical_density(text)
        assert density > 0

    def test_ending_diversity_uniform(self):
        text = "이것은 중요하다. 저것도 중요하다. 그것도 중요하다. 무엇이든 중요하다."
        div = ending_diversity(text)
        assert div < 0.5  # 전부 "하다"로 끝남

    def test_ending_diversity_varied(self):
        text = "이것은 중요하다. 저것을 살펴보았다. 그것이 핵심인가? 물론이다!"
        div = ending_diversity(text)
        assert div > 0.5  # 다양한 종결


class TestNormalisationMetrics:
    def test_normalisation_score_high(self):
        text = "기술이 발전한다. 사회가 변화된다. 이것이 현실이다."
        score = normalisation_score(text)
        assert score > 0.5  # 전부 ~한다/~된다/~이다

    def test_normalisation_score_low(self):
        text = "맞아. 그렇지? 아마도 그럴걸."
        score = normalisation_score(text)
        assert score == 0.0

    def test_da_streak_basic(self):
        text = "첫째다. 둘째다. 셋째다. 넷째다. 다섯째는 아니야."
        streaks = da_streak_rate(text)
        assert streaks >= 1

    def test_da_streak_none(self):
        text = "이것이다. 아마도. 그렇지? 좋아."
        streaks = da_streak_rate(text)
        assert streaks == 0


class TestInterferenceMetrics:
    def test_by_passive(self):
        text = "이 보고서는 AI에 의해 작성되었다."
        count = by_passive_count(text)
        assert count >= 1

    def test_double_passive(self):
        text = "이 결과는 판단되어진다. 그것은 보여진다."
        count = double_passive_count(text)
        assert count >= 2

    def test_double_passive_clean(self):
        text = "이 결과는 판단된다. 그것은 보인다."
        count = double_passive_count(text)
        assert count == 0

    def test_pronoun_density_high(self):
        text = "그는 학교에 갔다. 그녀는 집에 있었다. 그들은 함께 놀았다."
        density = pronoun_density(text)
        assert density > 0

    def test_pronoun_density_clean(self):
        text = "학교에 갔다. 집에 있었다. 함께 놀았다."
        density = pronoun_density(text)
        assert density == 0.0

    def test_deul_overuse(self):
        text = "데이터들을 분석하고 결과들을 검토하며 문제들을 해결했다."
        rate = deul_overuse_rate(text)
        assert rate > 0

    def test_deul_overuse_clean(self):
        text = "데이터를 분석하고 결과를 검토하며 문제를 해결했다."
        rate = deul_overuse_rate(text)
        assert rate == 0.0

    def test_have_make_literal(self):
        text = "경쟁력을 가지고 있다. 회의를 가졌다."
        count = have_make_literal_count(text)
        assert count >= 2

    def test_double_particle(self):
        text = "학교에서의 생활과 직장에서의 경험을 비교한다."
        count = double_particle_count(text)
        assert count == 2

    def test_double_particle_simple(self):
        # 단순 ~의 는 비대상
        text = "학교의 생활과 직장의 경험을 비교한다."
        count = double_particle_count(text)
        assert count == 0

    def test_progressive_rate(self):
        text = "그는 공부하고 있다. 그녀는 운동하고 있다. 나는 쉬고 있다."
        rate = progressive_aspect_rate(text)
        assert rate > 0.5

    def test_progressive_clean(self):
        text = "그는 공부한다. 그녀는 운동한다. 나는 쉰다."
        rate = progressive_aspect_rate(text)
        assert rate == 0.0

    def test_interference_index_structure(self):
        text = "AI에 의해 작성되어진다. 그녀는 데이터들을 가지고 있다."
        result = interference_index(text)
        assert "components" in result
        assert "weighted_total" in result
        assert result["weighted_total"] > 0


# ---------------------------------------------------------------------------
# 통합 API 테스트
# ---------------------------------------------------------------------------

class TestComputeKoreanQuality:
    def test_basic_structure(self):
        text = "오늘 날씨가 좋다. 산책하기 좋은 날이다."
        result = compute_korean_quality(text)
        assert "version" in result
        assert "risk_band" in result
        assert "risk_score" in result
        assert "metrics" in result
        assert "z_scores" in result
        assert "v2_metrics" in result
        assert "v2_interference_index" in result
        assert "evidence" in result

    def test_risk_band_values(self):
        text = "오늘 날씨가 좋다."
        result = compute_korean_quality(text)
        assert result["risk_band"] in ("low", "medium", "high")

    def test_genre_fallback(self):
        text = "오늘 날씨가 좋다."
        result = compute_korean_quality(text, genre="nonexistent")
        assert "warning" in result

    def test_ai_like_text_higher_risk(self):
        ai_text = (
            "결론적으로, 이 연구는 매우 혁신적인 결과를 보여주고 있다. "
            "따라서 이를 통해 기술적 발전이 이루어졌다고 할 수 있다. "
            "그러므로 이 시사하는 바가 크다. "
            "양쪽 모두 장점도 있지만, 신중하게 판단해야 한다. "
            "균형 잡힌 시각으로 접근하며, 본질적으로 중요한 문제이다."
        )
        human_text = "오늘 학교에 갔다. 친구를 만났다. 밥을 먹었다. 집에 왔다."
        ai_result = compute_korean_quality(ai_text)
        human_result = compute_korean_quality(human_text)
        assert ai_result["risk_score"] >= human_result["risk_score"]

    def test_empty_text(self):
        result = compute_korean_quality("")
        assert result["risk_band"] == "low"
        assert result["char_count"] == 0


class TestPromptRules:
    def test_not_empty(self):
        rules = get_s1_prompt_rules()
        assert len(rules) > 100

    def test_contains_key_patterns(self):
        rules = get_s1_prompt_rules()
        assert "번역투" in rules
        assert "관용구" in rules
        assert "이모지" in rules
        assert "결론적으로" in rules

    def test_quality_summary(self):
        text = "오늘 날씨가 좋다."
        result = compute_korean_quality(text)
        summary = get_quality_summary(result)
        assert "risk_band" in summary
        assert "risk_score" in summary
        assert "key_signals" in summary
        assert "ending_comma_rate" in summary["key_signals"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
