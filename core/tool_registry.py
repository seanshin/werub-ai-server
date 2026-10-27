"""Tool Calling 도구 레지스트리 — config/tools.json 로드 + Ollama 변환"""

import json
import logging
import os

logger = logging.getLogger("ollama-admin")


class ToolRegistry:
    """설정 파일 기반 도구 레지스트리

    - config/tools.json에서 도구 정의 로드
    - enabled 플래그로 개별 도구 on/off
    - Ollama /api/chat tools 파라미터 형식으로 변환
    - reload()로 서버 재시작 없이 갱신
    """

    def __init__(self, config_path=None, base_dir=None):
        self._base_dir = base_dir or "."
        self._config_path = config_path or os.path.join(self._base_dir, "config", "tools.json")
        self._tools = {}
        self._defaults = {
            "model": "qwen3:8b",
            "max_tool_calls": 5,
            "tool_timeout": 30,
            "total_timeout": 120,
            "temperature": 0.3,
        }
        self._version = 0
        self.reload()

    def reload(self):
        """설정 파일 다시 로드"""
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.warning("tools.json not found: %s", self._config_path)
            return
        except Exception as e:
            logger.error("ToolRegistry load error: %s", e)
            return

        self._defaults.update(data.get("defaults", {}))
        self._version = data.get("version", 0)
        self._tools = {}
        for tool in data.get("tools", []):
            name = tool.get("name")
            if name:
                self._tools[name] = tool
        logger.info("ToolRegistry loaded: %d tools (v%d)", len(self._tools), self._version)

    @property
    def defaults(self):
        return self._defaults

    @property
    def version(self):
        return self._version

    def get(self, name):
        """이름으로 도구 스펙 조회"""
        return self._tools.get(name)

    def get_enabled_tools(self):
        """enabled=true인 도구만 반환"""
        return {k: v for k, v in self._tools.items() if v.get("enabled", True)}

    def to_ollama_tools(self):
        """Ollama /api/chat tools 파라미터 형식으로 변환

        Returns:
            list[dict]: [{"type": "function", "function": {...}}, ...]
        """
        result = []
        for name, spec in self.get_enabled_tools().items():
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec.get("description", ""),
                    "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return result

    def get_action(self, tool_name):
        """도구 이름 -> 내부 action 문자열 (ai:/api/... 형식)"""
        spec = self._tools.get(tool_name)
        if not spec:
            return None
        if not spec.get("enabled", True):
            return None
        return spec.get("action")

    def list_tools(self, include_schema=False):
        """등록된 도구 목록 (API 응답용)"""
        result = []
        for name, spec in self._tools.items():
            entry = {
                "name": name,
                "description": spec.get("description", ""),
                "enabled": spec.get("enabled", True),
                "action": spec.get("action", ""),
            }
            if include_schema:
                entry["parameters"] = spec.get("parameters", {"type": "object", "properties": {}})
            result.append(entry)
        return result

    def __len__(self):
        return len(self.get_enabled_tools())
