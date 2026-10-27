"""core.priority_queue 단위 테스트.

도메인 우선순위 매핑, path 자동감지, user override(min), 응급/긴급 키워드 분류.
순수 모듈(logging/re만)이라 어디서든 실행.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.priority_queue import (  # noqa: E402
    get_domain_priority,
    classify_urgency,
    get_priority_stats,
    DOMAIN_PRIORITY,
    PATH_DOMAIN_MAP,
)


class TestDomainPriority:
    def test_action_mapped(self):
        assert get_domain_priority(action="triage_emergency") == 0
        assert get_domain_priority(action="summarize") == 1
        assert get_domain_priority(action="explain") == 2
        assert get_domain_priority(action="chat") == 3
        assert get_domain_priority(action="game_npc") == 4

    def test_unknown_action_default(self):
        assert get_domain_priority(action="nonexistent") == 5

    def test_no_args_default(self):
        assert get_domain_priority() == 5

    def test_path_fallback(self):
        # /api/triage → "triage" → 2
        assert get_domain_priority(path="/api/triage/start") == 2
        # /api/chat → "chat" → 3
        assert get_domain_priority(path="/api/chat") == 3
        # /api/game → "game_npc" → 4
        assert get_domain_priority(path="/api/game/npc") == 4

    def test_unknown_path_default(self):
        assert get_domain_priority(path="/api/unknown/thing") == 5

    def test_action_takes_precedence_over_path(self):
        # action(summarize=1)이 path(game=4)보다 우선
        assert get_domain_priority(action="summarize", path="/api/game") == 1

    def test_user_priority_min(self):
        # domain(chat=3)과 user(1) 중 더 높은 우선순위(min)
        assert get_domain_priority(action="chat", user_priority=1) == 1
        # domain(summarize=1)이 user(8)보다 높음 → 1
        assert get_domain_priority(action="summarize", user_priority=8) == 1

    def test_user_priority_with_unknown_action(self):
        # domain 기본 5, user 2 → 2
        assert get_domain_priority(action="nope", user_priority=2) == 2

    def test_user_priority_zero(self):
        assert get_domain_priority(action="chat", user_priority=0) == 0

    def test_user_priority_string_coerced(self):
        # int(user_priority) 변환
        assert get_domain_priority(action="chat", user_priority="2") == 2


class TestClassifyUrgency:
    def test_emergency_keyword(self):
        r = classify_urgency(action="summarize", content="환자 심정지 발생")
        assert r["urgency"] == "emergency"
        assert r["priority"] == 0
        assert "심정지" in r["reason"]

    def test_emergency_english_case_insensitive(self):
        r = classify_urgency(content="patient in Cardiac Arrest")
        assert r["urgency"] == "emergency"
        assert r["priority"] == 0

    def test_urgent_keyword(self):
        r = classify_urgency(content="급성 흉통 호소")
        assert r["urgency"] == "urgent"
        assert r["priority"] == 1

    def test_emergency_precedence_over_urgent(self):
        # 응급+긴급 키워드 동시 → 응급 우선
        r = classify_urgency(content="흉통과 심정지")
        assert r["urgency"] == "emergency"
        assert r["priority"] == 0

    def test_low_priority_action_no_content(self):
        r = classify_urgency(action="game_npc")
        assert r["urgency"] == "low"
        assert r["priority"] == 4

    def test_high_priority_action_becomes_urgent(self):
        # priority<=1 & 키워드 없음 → urgent
        r = classify_urgency(action="summarize")
        assert r["priority"] == 1
        assert r["urgency"] == "urgent"

    def test_mid_priority_normal(self):
        r = classify_urgency(action="chat")  # priority 3
        assert r["urgency"] == "normal"

    def test_no_keyword_no_match_stays_normal(self):
        r = classify_urgency(action="explain", content="일반적인 설명 요청")  # priority 2
        assert r["urgency"] == "normal"
        assert r["priority"] == 2

    def test_reason_fallback(self):
        r = classify_urgency(action="explain")
        assert "domain priority" in r["reason"]


class TestPriorityStats:
    def test_stats_shape(self):
        s = get_priority_stats()
        assert s["domain_count"] == len(DOMAIN_PRIORITY)
        assert s["path_rules"] == len(PATH_DOMAIN_MAP)
        assert set(s["levels"].keys()) == {0, 1, 2, 3, 4}
