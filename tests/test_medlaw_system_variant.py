"""모델별 생성 규칙 변형 회귀 (2026-08-04).

`MEDICAL_LAW_SYSTEM`은 폐루프 7~18이 **qwen2.5:14b의 실패**를 보고 누적한 것이다.
35b급은 실패 방향이 달라(무관 근거에 끌림·분량 과다) 짧은 변형을 둔다.

여기 고정하는 것: **미지정이면 기존 규칙 그대로**(기본 동작 불변)이고,
config로만 변형이 선택되며, 변형은 관측된 실패 모드를 실제로 겨냥한다.
"""
from unittest.mock import patch

import routes.medical_law as ML


class TestVariantSelection:
    def test_default_when_unconfigured(self):
        with patch.object(ML, "load_config", return_value={}):
            assert ML._system_for("qwen3.6:35b-a3b") is ML.MEDICAL_LAW_SYSTEM
            assert ML._system_for("qwen2.5:14b") is ML.MEDICAL_LAW_SYSTEM

    def test_concise_selected_by_model(self):
        cfg = {"medlaw_system_by_model": {"qwen3.6:35b-a3b": "concise"}}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._system_for("qwen3.6:35b-a3b") is ML.MEDICAL_LAW_SYSTEM_CONCISE
            # 매핑에 없는 모델은 영향 없음
            assert ML._system_for("qwen2.5:14b") is ML.MEDICAL_LAW_SYSTEM

    def test_unknown_variant_falls_back(self):
        cfg = {"medlaw_system_by_model": {"m": "존재하지않는변형"}}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._system_for("m") is ML.MEDICAL_LAW_SYSTEM

    def test_config_failure_falls_back(self):
        with patch.object(ML, "load_config", side_effect=RuntimeError("boom")):
            assert ML._system_for("m") is ML.MEDICAL_LAW_SYSTEM

    def test_none_model_safe(self):
        with patch.object(ML, "load_config", return_value={}):
            assert ML._system_for(None) is ML.MEDICAL_LAW_SYSTEM


class TestConciseContent:
    """관측된 35b 실패 모드를 실제로 겨냥하는지 — 규칙이 빠지면 실험이 무의미해진다."""

    def test_targets_irrelevant_evidence(self):
        """①무관 근거에 끌림(408자 고시가 답변 절반)"""
        t = ML.MEDICAL_LAW_SYSTEM_CONCISE
        assert "무관한 근거는 인용하지" in t
        assert "모든 근거를 성실히 반영하려 하지" in t

    def test_targets_verbosity(self):
        """②분량 과다(1,322자 · 지연 25~30초의 주원인)"""
        assert "간결하게" in ML.MEDICAL_LAW_SYSTEM_CONCISE
        assert "부연" in ML.MEDICAL_LAW_SYSTEM_CONCISE

    def test_verbosity_rule_has_a_floor(self):
        """간결 지시에 **하한**이 있어야 한다 — 없으면 조문 번호만 던지고 끝난다.
        실측: "의료법 제34조제1항" 11자 답변(12건 중 1건)."""
        t = ML.MEDICAL_LAW_SYSTEM_CONCISE
        assert "완결된 문장" in t
        assert "조문 번호나 제목만 나열하고 끝내지" in t

    def test_targets_missed_penalty(self):
        """③근거 87% 지점의 벌칙 조를 못 찾음"""
        assert "목록 뒤쪽에 있더라도 끝까지 찾아" in ML.MEDICAL_LAW_SYSTEM_CONCISE

    def test_keeps_universal_guards(self):
        """35b라도 근거 없는 창작·방향 반전은 막아야 한다(오늘 /api/chat 실측)."""
        t = ML.MEDICAL_LAW_SYSTEM_CONCISE
        assert "창작하지 마세요" in t
        assert "규범 방향은 뒤집지" in t
        assert "단서" in t
        assert "면책 문구" in t
        assert "한국어" in t

    def test_actually_shorter(self):
        """길이 자체가 처치의 일부 — 현행보다 뚜렷하게 짧아야 한다."""
        assert len(ML.MEDICAL_LAW_SYSTEM_CONCISE) < len(ML.MEDICAL_LAW_SYSTEM) * 0.6


class TestDefaultModel:
    """`/ask` 기본 모델을 상주 모델에 연동 — 안 하면 quality 모드에서 요청마다
    상주가 축출되고 12초 콜드 복귀를 문다(2026-08-04 실측)."""

    def test_follows_resident_when_on(self):
        cfg = {"llm_resident": True, "llm_resident_model": "qwen3.6:35b-a3b"}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._default_model() == "qwen3.6:35b-a3b"

    def test_falls_back_to_default_model_key(self):
        cfg = {"llm_resident": True, "default_model": "qwen2.5:7b"}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._default_model() == "qwen2.5:7b"

    def test_legacy_when_residency_off(self):
        """상주가 꺼져 있으면 종전 기본값 — 기존 동작 보존."""
        with patch.object(ML, "load_config",
                          return_value={"llm_resident": False,
                                        "llm_resident_model": "qwen3.6:35b-a3b"}):
            assert ML._default_model() == "qwen2.5:14b"

    def test_explicit_override_wins(self):
        """연동을 원치 않으면 config로 고정할 수 있어야 한다."""
        cfg = {"llm_resident": True, "llm_resident_model": "qwen3.6:35b-a3b",
               "medlaw_default_model": "qwen2.5:14b"}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._default_model() == "qwen2.5:14b"

    def test_config_failure_falls_back(self):
        with patch.object(ML, "load_config", side_effect=RuntimeError("boom")):
            assert ML._default_model() == "qwen2.5:14b"


class TestKeepAlive:
    """상주 고정 유지 — keep_alive 없이 호출하면 Ollama가 만료를 5분으로 재설정해
    상주가 풀린다(2026-08-04 실측: quality 모드에서 ps가 비고 매 요청 콜드 43s)."""

    def test_sends_policy_for_resident_model(self):
        cfg = {"llm_resident": True, "llm_resident_model": "qwen3.6:35b-a3b"}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._keep_alive_for("qwen3.6:35b-a3b") == {"keep_alive": -1}

    def test_silent_for_other_models(self):
        """상주 모델이 아니면 아무것도 보내지 않는다 — 기존 동작 보존."""
        cfg = {"llm_resident": True, "llm_resident_model": "qwen3.6:35b-a3b"}
        with patch.object(ML, "load_config", return_value=cfg):
            assert ML._keep_alive_for("qwen2.5:14b") == {}

    def test_silent_when_residency_off(self):
        with patch.object(ML, "load_config",
                          return_value={"llm_resident": False,
                                        "llm_resident_model": "qwen3.6:35b-a3b"}):
            assert ML._keep_alive_for("qwen3.6:35b-a3b") == {}

    def test_config_failure_safe(self):
        with patch.object(ML, "load_config", side_effect=RuntimeError("boom")):
            assert ML._keep_alive_for("m") == {}
