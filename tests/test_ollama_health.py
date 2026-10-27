"""core.ollama_health.evaluate_status 회귀 테스트 (v2.72.0 P1-B).

Ollama Engine Down 오탐 수정의 핵심 로직: 단발 블립 흡수 + N회 연속 실패만 다운 확정.
순수 함수라 의존성 없이 어디서든 실행 가능.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ollama_health import evaluate_status  # noqa: E402


class TestEvaluateStatus:
    def test_success_returns_true_and_resets(self):
        state = {"value": False, "fails": 3}
        assert evaluate_status(True, state) is True
        assert state["fails"] == 0
        assert state["value"] is True

    def test_single_blip_absorbed(self):
        # 직전 정상(True) → 1회 실패는 다운으로 확정하지 않음(낙관 유지)
        state = {"value": True, "fails": 0}
        assert evaluate_status(False, state) is True
        assert state["fails"] == 1

    def test_two_consecutive_fails_confirm_down(self):
        state = {"value": True, "fails": 0}
        assert evaluate_status(False, state) is True   # 1회: 흡수
        assert evaluate_status(False, state) is False  # 2회: 다운 확정
        assert state["fails"] == 2

    def test_recovery_after_down(self):
        state = {"value": False, "fails": 5}
        assert evaluate_status(True, state) is True
        assert state["fails"] == 0

    def test_full_sequence(self):
        # 정상 → 블립 → 회복 → 2연속다운 → 회복
        state = {"value": False, "fails": 0}
        seq_in = [True, True, False, True, False, False, False, True]
        expected = [True, True, True, True, True, False, False, True]
        out = [evaluate_status(s, state) for s in seq_in]
        assert out == expected

    def test_threshold_configurable(self):
        # fail_threshold=3 → 3회 연속이어야 다운
        state = {"value": True, "fails": 0}
        assert evaluate_status(False, state, fail_threshold=3) is True   # 1
        assert evaluate_status(False, state, fail_threshold=3) is True   # 2
        assert evaluate_status(False, state, fail_threshold=3) is False  # 3

    def test_missing_fails_key_defaults_zero(self):
        # fails 키 없는 상태에서도 안전 (get 기본 0)
        state = {"value": True}
        assert evaluate_status(False, state) is True
        assert state["fails"] == 1

    def test_cold_start_first_fail_stays_down(self):
        # 초기 상태(value=False)에서 첫 실패 → 직전 False 유지 = 다운
        state = {"value": False, "fails": 0}
        assert evaluate_status(False, state) is False
