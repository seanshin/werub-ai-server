"""보조 추론 사이드카 클라이언트 — 신형 VLM(Qwen3-VL 의료영상) 온디맨드.

Ollama가 못 돌리는 신형 모델을 Ollama 밖 별도 사이드카(llama-server 등)로 제공.
이 모듈은 **클라이언트 레이어만** — 사이드카 미기동/미도달/비활성 시 전부 graceful
(호출자가 기존 Ollama-MedGemma 경로로 폴백). 라이브 의료 경로 무접촉.

계약: OpenAI 호환 POST {url}/v1/chat/completions (llama-server 멀티모달).
기획: docs/01-plan/features/auxiliary-inference-sidecar.plan.md
"""
from __future__ import annotations

import time

from core.config import load_config

# config.json 키 (전부 opt-in, 기본값=기존 동작 불변)
DEFAULT_URL = "http://127.0.0.1:8191"
DEFAULT_MODEL = "qwen3-vl-8b"
DEFAULT_ENGINE = "ollama"       # ollama | sidecar | dual
DEFAULT_TIMEOUT = 120
VALID_ENGINES = ("ollama", "sidecar", "dual")


def _conf() -> dict | None:
    """사이드카 설정 로드. 비활성/미설정이면 None."""
    cfg = load_config()
    if not cfg.get("aux_infer_enabled", False):
        return None
    url = (cfg.get("aux_infer_url") or DEFAULT_URL).rstrip("/")
    if not url:
        return None
    return {
        "url": url,
        "model": cfg.get("aux_infer_model") or DEFAULT_MODEL,
        "timeout": cfg.get("aux_infer_timeout") or DEFAULT_TIMEOUT,
    }


def engine() -> str:
    """의료비전 엔진 선택. 기본 'ollama'(=기존 동작). 사이드카 비활성이면 강제 'ollama'."""
    cfg = load_config()
    e = cfg.get("medical_vision_engine") or DEFAULT_ENGINE
    if e not in VALID_ENGINES:
        e = DEFAULT_ENGINE
    # 사이드카 비활성인데 sidecar/dual 요청 시 안전하게 ollama로 강등
    if e in ("sidecar", "dual") and _conf() is None:
        return DEFAULT_ENGINE
    return e


def enabled() -> bool:
    return _conf() is not None


def status() -> dict:
    """사이드카 상태(통합 패널용) — 미도달도 graceful 200 shape. 절대 raise 안 함."""
    conf = _conf()
    if conf is None:
        return {"enabled": False, "reachable": False}
    import requests
    t0 = time.time()
    try:
        r = requests.get(f"{conf['url']}/v1/models", timeout=3)
        ms = round((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json().get("data") or []
            models = [m.get("id") for m in data if isinstance(m, dict)]
            return {"enabled": True, "reachable": True, "url": conf["url"],
                    "model": conf["model"], "models": models, "latency_ms": ms}
        return {"enabled": True, "reachable": False, "url": conf["url"],
                "error": f"status={r.status_code}", "latency_ms": ms}
    except Exception as e:  # noqa: BLE001
        return {"enabled": True, "reachable": False, "url": conf["url"],
                "error": str(e)[:80]}


def medical_vision_read(image_b64: str, prompt: str, system: str = "",
                        timeout: float | None = None) -> str | None:
    """사이드카로 의료 영상 판독. 실패(비활성·미도달·빈응답·예외)=None → 호출자 폴백.

    image_b64: raw base64(접두 없이). OpenAI content로 data: URI 래핑해 전송.
    반환: 판독 텍스트 또는 None(절대 raise 안 함).
    """
    conf = _conf()
    if conf is None or not image_b64 or not (prompt or "").strip():
        return None
    import requests
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
    ]})
    payload = {"model": conf["model"], "messages": messages, "stream": False,
               "temperature": 0.3, "max_tokens": 1024}
    try:
        r = requests.post(f"{conf['url']}/v1/chat/completions", json=payload,
                          timeout=timeout or conf["timeout"])
        if r.status_code != 200:
            return None
        choices = r.json().get("choices") or []
        if not choices:
            return None
        content = (choices[0].get("message") or {}).get("content") or ""
        return content.strip() or None
    except Exception:  # noqa: BLE001
        return None
