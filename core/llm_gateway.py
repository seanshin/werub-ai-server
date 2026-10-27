"""LLM Gateway — 로컬(Ollama) + 외부(OpenAI/Anthropic/Google) 통합 (v2.51.0)"""
import json
import logging
import os
import time

import requests

from core.korean import force_korean, korean_quality_check

logger = logging.getLogger("ollama-admin")

# 프로바이더별 한국어 검사 필요 여부
KOREAN_CHECK_BY_PROVIDER = {
    "ollama": True,
    "openai": False,      # GPT는 한국어 안정적
    "anthropic": False,   # Claude 한국어 우수
    "google": True,       # Gemini 가끔 일본어 혼입
    "groq": True,         # Llama 한국어 약함
    "openrouter": True,
}


class LLMGateway:
    """로컬 + 외부 모델 통합 게이트웨이"""

    def __init__(self, config_path=None, ollama_base="http://127.0.0.1:11434"):
        self.ollama_base = ollama_base
        self.config_path = config_path
        self.providers = self._load_config()
        self._session = requests.Session()

    def _load_config(self):
        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    return json.load(f).get("providers", {})
            except Exception as e:
                logger.warning(f"Failed to load providers config: {e}")
        return {"ollama": {"type": "ollama", "base_url": self.ollama_base, "enabled": True}}

    def _save_config(self):
        if self.config_path:
            with open(self.config_path, "w") as f:
                json.dump({"providers": self.providers}, f, indent=2, ensure_ascii=False)

    def reload_config(self):
        self.providers = self._load_config()

    def get_providers(self):
        """활성/비활성 프로바이더 목록"""
        result = {}
        for name, cfg in self.providers.items():
            result[name] = {
                "type": cfg.get("type"),
                "enabled": cfg.get("enabled", False),
                "models": cfg.get("models", []),
                "has_api_key": bool(self._get_api_key(name)),
            }
        return result

    def get_all_models(self):
        """전체 사용 가능 모델 (로컬+외부)"""
        models = []
        for name, cfg in self.providers.items():
            if not cfg.get("enabled"):
                continue
            provider_models = cfg.get("models", [])
            if provider_models == "auto" and cfg["type"] == "ollama":
                # Ollama는 동적 조회
                try:
                    r = self._session.get(f"{self.ollama_base}/api/tags", timeout=3)
                    provider_models = [m["name"] for m in r.json().get("models", [])]
                except Exception:
                    provider_models = []
            for m in provider_models:
                models.append({"model": m, "provider": name, "type": cfg["type"]})
        return models

    def enable_provider(self, name, api_key=None):
        """프로바이더 활성화"""
        if name not in self.providers:
            return False, f"Unknown provider: {name}"
        self.providers[name]["enabled"] = True
        if api_key:
            env_key = self.providers[name].get("api_key_env", f"{name.upper()}_API_KEY")
            os.environ[env_key] = api_key
        self._save_config()
        return True, "enabled"

    def disable_provider(self, name):
        if name not in self.providers:
            return False, f"Unknown provider: {name}"
        if name == "ollama":
            return False, "Cannot disable ollama (local)"
        self.providers[name]["enabled"] = False
        self._save_config()
        return True, "disabled"

    def test_provider(self, name):
        """프로바이더 연결 테스트"""
        if name not in self.providers:
            return {"ok": False, "error": f"Unknown provider: {name}"}
        cfg = self.providers[name]
        if not cfg.get("enabled"):
            return {"ok": False, "error": "Provider disabled"}

        try:
            if cfg["type"] == "ollama":
                r = self._session.get(f"{cfg['base_url']}/api/tags", timeout=5)
                return {"ok": r.status_code == 200, "models": len(r.json().get("models", []))}
            elif cfg["type"] == "openai":
                api_key = self._get_api_key(name)
                if not api_key:
                    return {"ok": False, "error": "API key not set"}
                r = self._session.get(f"{cfg['base_url']}/models",
                                       headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
                return {"ok": r.status_code == 200}
            elif cfg["type"] == "anthropic":
                api_key = self._get_api_key(name)
                if not api_key:
                    return {"ok": False, "error": "API key not set"}
                # Anthropic은 모델 목록 API가 없으므로 간단한 메시지 테스트
                return {"ok": True, "note": "API key configured (test requires actual request)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # -----------------------------------------------------------------------
    # 통합 Generate
    # -----------------------------------------------------------------------

    def generate(self, model, prompt, system="", options=None, is_korean=False):
        """통합 생성 — 모델명으로 프로바이더 자동 선택"""
        provider_name = self._resolve_provider(model)
        cfg = self.providers.get(provider_name, self.providers.get("ollama"))

        start = time.time()

        if cfg["type"] == "ollama":
            result = self._generate_ollama(cfg, model, prompt, system, options)
        elif cfg["type"] == "openai":
            result = self._generate_openai(cfg, provider_name, model, prompt, system, options)
        elif cfg["type"] == "anthropic":
            result = self._generate_anthropic(cfg, model, prompt, system, options)
        else:
            result = {"response": f"Unsupported provider type: {cfg['type']}",
                      "tokens": 0, "provider": provider_name, "model": model}

        result["duration_ms"] = int((time.time() - start) * 1000)
        result["provider"] = provider_name

        # 한국어 후처리
        if is_korean and result.get("response"):
            need_check = KOREAN_CHECK_BY_PROVIDER.get(provider_name, True)
            if need_check:
                result["response"] = force_korean(result["response"])
            is_valid, ratio, _ = korean_quality_check(result["response"])
            result["korean_quality"] = {"ratio": round(ratio, 4), "valid": is_valid}

        return result

    def _resolve_provider(self, model):
        """모델명 → 프로바이더 결정 (로컬 우선)"""
        # 1. 네임스페이스 명시 (openai:gpt-4o)
        if ":" in model and model.split(":")[0] in self.providers:
            parts = model.split(":", 1)
            if parts[0] in self.providers and parts[0] != "ollama":
                return parts[0]

        # 2. 각 프로바이더의 모델 목록에서 정확한 매칭
        for name, cfg in self.providers.items():
            if cfg.get("enabled") and model in cfg.get("models", []):
                return name

        # 3. prefix 매칭
        PREFIX_MAP = {
            "gpt-": "openai", "o3-": "openai", "o4-": "openai",
            "claude-": "anthropic",
            "gemini-": "google",
        }
        for prefix, provider in PREFIX_MAP.items():
            if model.startswith(prefix) and self.providers.get(provider, {}).get("enabled"):
                return provider

        # 4. 기본: Ollama
        return "ollama"

    def _get_api_key(self, provider_name):
        cfg = self.providers.get(provider_name, {})
        env_key = cfg.get("api_key_env", f"{provider_name.upper()}_API_KEY")
        return os.environ.get(env_key)

    # -----------------------------------------------------------------------
    # Provider Adapters
    # -----------------------------------------------------------------------

    def _generate_ollama(self, cfg, model, prompt, system, options):
        opts = options or {}
        r = self._session.post(f"{cfg['base_url']}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False, "options": opts,
        }, timeout=120)
        if r.status_code != 200:
            return {"response": f"Ollama error: {r.status_code}", "tokens": 0, "model": model}
        d = r.json()
        return {
            "response": d.get("response", ""),
            "tokens": d.get("eval_count", 0),
            "model": model,
        }

    def _generate_openai(self, cfg, provider_name, model, prompt, system, options):
        api_key = self._get_api_key(provider_name)
        if not api_key:
            return {"response": f"API key not set for {provider_name}", "tokens": 0, "model": model}

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        opts = options or {}
        try:
            r = self._session.post(f"{cfg['base_url']}/chat/completions", json={
                "model": model,
                "messages": messages,
                "max_tokens": opts.get("num_predict", 2048),
                "temperature": opts.get("temperature", 0.7),
            }, headers={"Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"}, timeout=60)

            if r.status_code == 429:
                return {"response": "Rate limit exceeded. Retry later.", "tokens": 0,
                        "model": model, "error": "rate_limit"}
            if r.status_code != 200:
                return {"response": f"OpenAI error: {r.status_code} {r.text[:200]}",
                        "tokens": 0, "model": model}

            d = r.json()
            return {
                "response": d["choices"][0]["message"]["content"],
                "tokens": d.get("usage", {}).get("completion_tokens", 0),
                "input_tokens": d.get("usage", {}).get("prompt_tokens", 0),
                "model": model,
            }
        except Exception as e:
            return {"response": f"OpenAI error: {e}", "tokens": 0, "model": model}

    def _generate_anthropic(self, cfg, model, prompt, system, options):
        api_key = self._get_api_key("anthropic")
        if not api_key:
            return {"response": "Anthropic API key not set", "tokens": 0, "model": model}

        opts = options or {}
        try:
            body = {
                "model": model,
                "max_tokens": opts.get("num_predict", 2048),
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                body["system"] = system

            r = self._session.post(f"{cfg['base_url']}/messages", json=body,
                                    headers={"x-api-key": api_key,
                                             "anthropic-version": "2023-06-01",
                                             "Content-Type": "application/json"}, timeout=60)

            if r.status_code == 429:
                return {"response": "Rate limit exceeded.", "tokens": 0, "model": model, "error": "rate_limit"}
            if r.status_code != 200:
                return {"response": f"Anthropic error: {r.status_code} {r.text[:200]}",
                        "tokens": 0, "model": model}

            d = r.json()
            return {
                "response": d["content"][0]["text"],
                "tokens": d.get("usage", {}).get("output_tokens", 0),
                "input_tokens": d.get("usage", {}).get("input_tokens", 0),
                "model": model,
            }
        except Exception as e:
            return {"response": f"Anthropic error: {e}", "tokens": 0, "model": model}
