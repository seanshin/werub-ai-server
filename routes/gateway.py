"""Smart Gateway Blueprint — iMac M칩 전용

iMac이 GPU 서버의 앞단(게이트웨이) 역할을 수행한다.

동작 원리:
  1. 요청이 들어오면 경량 모델로 복잡도(intent) 분류
  2. 단순 → iMac 로컬 Ollama가 직접 처리
  3. 복잡 → GPU 서버로 투명하게 포워딩

엔드포인트:
  POST /api/gateway/chat          — 자동 라우팅 채팅
  POST /api/gateway/route         — 라우팅 결과만 반환 (디버그)
  GET  /api/gateway/status        — 게이트웨이 상태
  POST /api/gateway/config        — 라우팅 설정 변경
"""
import logging
import os
import time

import requests
from flask import Blueprint, jsonify, request
from core.errors import internal_error

from core.platform import feature, is_imac

logger = logging.getLogger("ollama-admin")

gateway_bp = Blueprint("gateway", __name__)

# ---------------------------------------------------------------------------
# 모듈 상태 (init_gateway로 주입)
# ---------------------------------------------------------------------------
_ollama_session: requests.Session | None = None
_ollama_base: str = "http://localhost:11434"
_gpu_base: str = ""          # GPU 서버 URL (설정에서 주입)

# 라우팅 설정 (런타임 변경 가능)
_config = {
    "enabled": True,
    "local_model": "qwen2.5:1.5b",   # 분류용 경량 모델
    "gpu_base": "",                    # GPU 서버 base URL
    "gpu_api_key": "",                 # GPU 서버 API 키
    "threshold": 0.6,                  # 복잡도 임계값 (이상이면 GPU로)
    "always_local": [],                # 항상 로컬 처리할 모델 목록
    "always_gpu": [],                  # 항상 GPU로 보낼 모델 목록
    "timeout_local": 120,
    "timeout_gpu": 300,
}

# 통계
_stats = {
    "total": 0,
    "routed_local": 0,
    "routed_gpu": 0,
    "errors": 0,
}

# 복잡도 분류 키워드 (빠른 규칙 기반 1차 판단)
_COMPLEX_KEYWORDS = [
    "추론", "분석", "비교", "설명", "논문", "코드", "프로그램", "알고리즘",
    "의학", "진단", "치료", "법률", "계약", "소송", "판례",
    "번역", "요약", "정리", "보고서", "계획서", "제안서",
    "reason", "analyze", "compare", "explain", "diagnose",
    "code", "program", "algorithm", "legal", "medical",
]
_SIMPLE_KEYWORDS = [
    "안녕", "감사", "고마워", "어때", "뭐야", "뭔지",
    "hello", "hi", "thanks", "what is", "who is",
]


def init_gateway(*, ollama_session, ollama_base, gpu_base="", gpu_api_key=""):
    global _ollama_session, _ollama_base, _gpu_base
    _ollama_session = ollama_session
    _ollama_base = ollama_base
    _config["gpu_base"] = gpu_base
    _config["gpu_api_key"] = gpu_api_key
    _gpu_base = gpu_base
    logger.info("[gateway] initialized | local=%s | gpu=%s", ollama_base, gpu_base or "미설정")


# ---------------------------------------------------------------------------
# 내부 함수
# ---------------------------------------------------------------------------

def _classify_complexity(message: str) -> tuple[str, float]:
    """메시지 복잡도 분류 (규칙 기반)

    Returns:
        ("local"|"gpu", confidence)
    """
    msg_lower = message.lower()
    length = len(message)

    # 길이 기반 1차 판단
    if length > 500:
        return "gpu", 0.8
    if length < 50:
        return "local", 0.8

    # 키워드 기반
    complex_hits = sum(1 for kw in _COMPLEX_KEYWORDS if kw in msg_lower)
    simple_hits  = sum(1 for kw in _SIMPLE_KEYWORDS  if kw in msg_lower)

    if complex_hits >= 2:
        return "gpu", min(0.9, 0.6 + complex_hits * 0.1)
    if simple_hits >= 1 and complex_hits == 0:
        return "local", 0.8

    # 기본값: 로컬 처리
    return "local", 0.6


def _call_local(messages: list, model: str | None = None, stream: bool = False) -> dict:
    """로컬 Ollama로 채팅"""
    model = model or _config["local_model"]
    payload = {"model": model, "messages": messages, "stream": stream}
    resp = _ollama_session.post(
        f"{_ollama_base}/api/chat",
        json=payload,
        timeout=_config["timeout_local"],
        stream=stream,
    )
    resp.raise_for_status()
    return resp.json() if not stream else resp


def _call_gpu(path: str, payload: dict) -> dict:
    """GPU 서버로 요청 포워딩"""
    gpu_base = _config.get("gpu_base", "")
    if not gpu_base:
        raise ValueError("GPU 서버 URL 미설정 (/api/gateway/config 에서 gpu_base 설정)")

    headers = {"Content-Type": "application/json"}
    if _config.get("gpu_api_key"):
        headers["Authorization"] = f"Bearer {_config['gpu_api_key']}"

    resp = requests.post(
        f"{gpu_base}{path}",
        json=payload,
        headers=headers,
        timeout=_config["timeout_gpu"],
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@gateway_bp.route("/api/gateway/chat", methods=["POST"])
def gateway_chat():
    """스마트 라우팅 채팅 — 자동으로 로컬/GPU 분기
    ---
    tags: [Gateway]
    summary: Smart routing chat (iMac 전용)
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string}
            model: {type: string, description: "지정 시 해당 모델 강제 사용"}
            force_local: {type: boolean, default: false}
            force_gpu: {type: boolean, default: false}
    responses:
      200:
        description: LLM 응답 + 라우팅 정보
    """
    _stats["total"] += 1
    data = request.get_json(force=True, silent=True) or {}
    message    = data.get("message", "")
    model      = data.get("model")
    force_local = data.get("force_local", False)
    force_gpu   = data.get("force_gpu", False)

    if not message:
        return jsonify({"error": "message required"}), 400

    messages = [{"role": "user", "content": message}]
    if data.get("system"):
        messages.insert(0, {"role": "system", "content": data["system"]})

    # 라우팅 결정
    if force_local or model in _config["always_local"]:
        route, confidence = "local", 1.0
    elif force_gpu or model in _config["always_gpu"]:
        route, confidence = "gpu", 1.0
    elif not _config.get("gpu_base"):
        route, confidence = "local", 1.0   # GPU URL 미설정 시 항상 로컬
    else:
        route, confidence = _classify_complexity(message)
        if confidence < _config["threshold"]:
            route = "local"

    t0 = time.time()
    try:
        if route == "local":
            _stats["routed_local"] += 1
            result = _call_local(messages, model=model)
            content = result.get("message", {}).get("content", "")
            used_model = result.get("model", model or _config["local_model"])
        else:
            _stats["routed_gpu"] += 1
            payload = {"messages": messages, "stream": False}
            if model:
                payload["model"] = model
            result = _call_gpu("/api/chat", payload)
            content = result.get("message", {}).get("content", "")
            used_model = result.get("model", "gpu")

        duration_ms = round((time.time() - t0) * 1000)
        return jsonify({
            "message": content,
            "model": used_model,
            "routed_to": route,
            "confidence": round(confidence, 2),
            "duration_ms": duration_ms,
        })

    except Exception as e:
        _stats["errors"] += 1
        logger.error("[gateway] %s 오류: %s", route, e)
        # GPU 실패 시 로컬 폴백
        if route == "gpu":
            try:
                _stats["routed_local"] += 1
                result = _call_local(messages, model=None)
                content = result.get("message", {}).get("content", "")
                duration_ms = round((time.time() - t0) * 1000)
                return jsonify({
                    "message": content,
                    "model": _config["local_model"],
                    "routed_to": "local",
                    "fallback": True,
                    "fallback_reason": str(e),
                    "duration_ms": duration_ms,
                })
            except Exception as fe:
                return jsonify({"error": str(fe)}), 500
        return internal_error(e)


@gateway_bp.route("/api/gateway/route", methods=["POST"])
def gateway_route():
    """라우팅 분류 결과만 반환 (실제 LLM 호출 없음, 디버그용)
    ---
    tags: [Gateway]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string}
    """
    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "")
    if not message:
        return jsonify({"error": "message required"}), 400

    route, confidence = _classify_complexity(message)
    gpu_available = bool(_config.get("gpu_base"))

    return jsonify({
        "message_length": len(message),
        "route": route if gpu_available else "local",
        "confidence": round(confidence, 2),
        "gpu_available": gpu_available,
        "reason": "gpu URL 미설정" if not gpu_available and route == "gpu" else None,
    })


@gateway_bp.route("/api/gateway/status")
def gateway_status():
    """게이트웨이 상태 및 통계
    ---
    tags: [Gateway]
    """
    gpu_base = _config.get("gpu_base", "")
    gpu_healthy = False
    if gpu_base:
        try:
            r = requests.get(f"{gpu_base}/api/queue/status", timeout=5)
            gpu_healthy = r.ok
        except Exception:
            pass

    return jsonify({
        "enabled": _config["enabled"],
        "local_model": _config["local_model"],
        "gpu_base": gpu_base or None,
        "gpu_connected": gpu_healthy,
        "stats": _stats.copy(),
        "config": {k: v for k, v in _config.items() if k != "gpu_api_key"},
    })


@gateway_bp.route("/api/gateway/config", methods=["GET", "POST"])
def gateway_config():
    """게이트웨이 설정 조회/변경
    ---
    tags: [Gateway]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            gpu_base: {type: string, description: "GPU 서버 base URL (예: http://192.0.2.108:8585)"}
            gpu_api_key: {type: string}
            local_model: {type: string}
            enabled: {type: boolean}
            threshold: {type: number}
    """
    if request.method == "GET":
        return jsonify({k: v for k, v in _config.items() if k != "gpu_api_key"})

    data = request.get_json(force=True, silent=True) or {}
    allowed = ("gpu_base", "gpu_api_key", "local_model", "enabled",
               "threshold", "always_local", "always_gpu")
    for k in allowed:
        if k in data:
            _config[k] = data[k]
    logger.info("[gateway] config updated: %s", {k: v for k, v in data.items() if k != "gpu_api_key"})
    return jsonify({"ok": True, "config": {k: v for k, v in _config.items() if k != "gpu_api_key"}})
