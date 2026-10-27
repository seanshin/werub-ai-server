"""core.tool_registry.ToolRegistry 단위 테스트.

Tool Calling 핵심 — config/tools.json 로드, defaults 병합, enabled 필터,
Ollama 변환, reload, 에러 내성. 순수 모듈(flask/requests 무관)이라 어디서든 실행.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tool_registry import ToolRegistry  # noqa: E402


def _make_config(tmp_path, payload):
    """tools.json을 tmp에 쓰고 경로 반환."""
    p = os.path.join(str(tmp_path), "tools.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return p


SAMPLE = {
    "version": 3,
    "defaults": {"model": "qwen3:14b", "max_tool_calls": 8},
    "tools": [
        {"name": "tool_a", "description": "도구 A", "action": "ai:/api/a", "enabled": True,
         "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}},
        {"name": "tool_b", "description": "도구 B", "action": "llm:", "enabled": False},
        {"name": "tool_c", "description": "도구 C", "action": "search://"},  # enabled 키 없음 → 기본 True
    ],
}


class TestLoad:
    def test_loads_tools(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert reg.get("tool_a") is not None
        assert reg.get("tool_a")["description"] == "도구 A"

    def test_version(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert reg.version == 3

    def test_get_missing_returns_none(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert reg.get("nonexistent") is None


class TestDefaults:
    def test_override_merges_with_builtin(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        d = reg.defaults
        assert d["model"] == "qwen3:14b"        # config override
        assert d["max_tool_calls"] == 8          # config override
        assert d["tool_timeout"] == 30           # 내장 기본 유지
        assert d["temperature"] == 0.3           # 내장 기본 유지

    def test_no_defaults_keeps_builtin(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, {"version": 1, "tools": []}))
        assert reg.defaults["model"] == "qwen3:8b"


class TestEnabledFilter:
    def test_get_enabled_excludes_disabled(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        enabled = reg.get_enabled_tools()
        assert set(enabled.keys()) == {"tool_a", "tool_c"}  # b는 enabled=False

    def test_len_counts_enabled_only(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert len(reg) == 2

    def test_missing_enabled_key_defaults_true(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert "tool_c" in reg.get_enabled_tools()


class TestActions:
    def test_get_action_enabled(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert reg.get_action("tool_a") == "ai:/api/a"

    def test_get_action_disabled_returns_none(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert reg.get_action("tool_b") is None

    def test_get_action_missing_returns_none(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        assert reg.get_action("nope") is None


class TestOllamaConversion:
    def test_to_ollama_tools_format(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        tools = reg.to_ollama_tools()
        assert len(tools) == 2  # enabled만
        a = next(t for t in tools if t["function"]["name"] == "tool_a")
        assert a["type"] == "function"
        assert a["function"]["description"] == "도구 A"
        assert a["function"]["parameters"]["properties"]["x"]["type"] == "string"

    def test_to_ollama_tools_default_params(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        tools = reg.to_ollama_tools()
        c = next(t for t in tools if t["function"]["name"] == "tool_c")
        # parameters 미지정 → 기본 빈 object 스키마
        assert c["function"]["parameters"] == {"type": "object", "properties": {}}


class TestListTools:
    def test_list_includes_disabled(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        names = {t["name"] for t in reg.list_tools()}
        assert names == {"tool_a", "tool_b", "tool_c"}  # 비활성 포함

    def test_list_enabled_flags(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        flags = {t["name"]: t["enabled"] for t in reg.list_tools()}
        assert flags == {"tool_a": True, "tool_b": False, "tool_c": True}

    def test_list_schema_opt_in(self, tmp_path):
        reg = ToolRegistry(config_path=_make_config(tmp_path, SAMPLE))
        without = reg.list_tools()
        with_schema = reg.list_tools(include_schema=True)
        assert "parameters" not in without[0]
        assert "parameters" in with_schema[0]


class TestResilience:
    def test_missing_file_empty_no_crash(self, tmp_path):
        reg = ToolRegistry(config_path=os.path.join(str(tmp_path), "nope.json"))
        assert len(reg) == 0
        assert reg.version == 0
        assert reg.defaults["model"] == "qwen3:8b"  # 내장 기본 유지

    def test_broken_json_no_crash(self, tmp_path):
        p = os.path.join(str(tmp_path), "broken.json")
        with open(p, "w") as f:
            f.write("{ not valid json ")
        reg = ToolRegistry(config_path=p)
        assert len(reg) == 0

    def test_reload_picks_up_changes(self, tmp_path):
        p = _make_config(tmp_path, {"version": 1, "tools": [{"name": "t1"}]})
        reg = ToolRegistry(config_path=p)
        assert len(reg) == 1 and reg.version == 1
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "tools": [{"name": "t1"}, {"name": "t2"}]}, f)
        reg.reload()
        assert len(reg) == 2 and reg.version == 2
