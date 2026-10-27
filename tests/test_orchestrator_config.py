"""routes.orchestrator config 로더 회귀 테스트 (v2.72.0 모델 config화).

대상:
- _load_general_chat_cfg: general_chat 블록 타입·범위 가드 (timeout=null/bool/범위초과,
  model 빈값, temperature 범위, num_predict 타입) — 잘못된 값은 기본값 유지(무한대기 방지).
- workflow_default_model 로드.

GPU 서버(flask/requests 설치 환경)에서 실행. iMac 등 의존성 없으면 자동 skip.
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 무거운 의존(flask/requests) 없는 환경(iMac 코드전용)에서는 스킵
pytest.importorskip("flask")
pytest.importorskip("requests")

from routes import orchestrator as orch  # noqa: E402


def _write_tools(tmpdir, payload):
    """tmpdir/config/tools.json 에 payload를 기록하고 tmpdir 반환."""
    cfgdir = os.path.join(str(tmpdir), "config")
    os.makedirs(cfgdir, exist_ok=True)
    with open(os.path.join(cfgdir, "tools.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return str(tmpdir)


class TestGeneralChatConfig:
    """_load_general_chat_cfg — general_chat 블록 검증·병합."""

    def test_valid_override(self, tmp_path):
        d = _write_tools(tmp_path, {"general_chat": {"model": "qwen3:14b", "timeout": 120}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["model"] == "qwen3:14b"
        assert orch._general_chat_cfg["timeout"] == 120

    def test_missing_block_uses_defaults(self, tmp_path):
        d = _write_tools(tmp_path, {})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["model"] == "qwen2.5:14b"
        assert orch._general_chat_cfg["timeout"] == 60

    def test_timeout_null_guarded(self, tmp_path):
        # timeout=null → requests 무한대기(스레드 고갈) 방지: 기본값 유지
        d = _write_tools(tmp_path, {"general_chat": {"timeout": None}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["timeout"] == 60

    def test_timeout_bool_guarded(self, tmp_path):
        # bool은 int 하위클래스 → 배제 (timeout=True가 1초로 새면 안 됨)
        d = _write_tools(tmp_path, {"general_chat": {"timeout": True}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["timeout"] == 60

    @pytest.mark.parametrize("bad", [0, -5, 9999])
    def test_timeout_out_of_range_guarded(self, tmp_path, bad):
        d = _write_tools(tmp_path, {"general_chat": {"timeout": bad}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["timeout"] == 60

    def test_timeout_valid_float(self, tmp_path):
        d = _write_tools(tmp_path, {"general_chat": {"timeout": 90.5}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["timeout"] == 90.5

    def test_model_empty_guarded(self, tmp_path):
        d = _write_tools(tmp_path, {"general_chat": {"model": "   "}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["model"] == "qwen2.5:14b"

    def test_temperature_out_of_range_guarded(self, tmp_path):
        d = _write_tools(tmp_path, {"general_chat": {"temperature": 5}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["temperature"] == 0.5

    def test_temperature_valid(self, tmp_path):
        d = _write_tools(tmp_path, {"general_chat": {"temperature": 0.9}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["temperature"] == 0.9

    def test_num_predict_type_guarded(self, tmp_path):
        # 문자열 "500" → int 아님 → 거부, 기본값 유지
        d = _write_tools(tmp_path, {"general_chat": {"num_predict": "500"}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["num_predict"] == 500

    def test_broken_json_falls_back_to_defaults(self, tmp_path):
        cfgdir = os.path.join(str(tmp_path), "config")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "tools.json"), "w") as f:
            f.write("{ this is not valid json")
        orch._load_general_chat_cfg(str(tmp_path))
        assert orch._general_chat_cfg["model"] == "qwen2.5:14b"
        assert orch._general_chat_cfg["timeout"] == 60

    def test_system_override(self, tmp_path):
        d = _write_tools(tmp_path, {"general_chat": {"system": "너는 테스트 봇이다."}})
        orch._load_general_chat_cfg(d)
        assert orch._general_chat_cfg["system"] == "너는 테스트 봇이다."


class TestWorkflowDefaultModel:
    """workflow_default_model — 워크플로 액션(ai:/llm:) 기본 모델."""

    def test_override(self, tmp_path):
        d = _write_tools(tmp_path, {"workflow_default_model": "exaone-deep:7.8b"})
        orch._load_general_chat_cfg(d)
        assert orch._workflow_default_model == "exaone-deep:7.8b"

    def test_missing_uses_default(self, tmp_path):
        d = _write_tools(tmp_path, {})
        orch._load_general_chat_cfg(d)
        assert orch._workflow_default_model == "qwen2.5:14b"

    def test_empty_uses_default(self, tmp_path):
        d = _write_tools(tmp_path, {"workflow_default_model": "   "})
        orch._load_general_chat_cfg(d)
        assert orch._workflow_default_model == "qwen2.5:14b"
