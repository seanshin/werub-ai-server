"""WebSocket 실시간 스트리밍 — /ws/chat, /ws/status (P3-3)

Flask 기반 WebSocket — 별도 라이브러리 없이 SSE 대안 + polling 방식.
실제 WebSocket은 Flask-SocketIO 또는 별도 서버 필요하므로,
여기서는 경량 SSE 강화 + Long Polling 하이브리드를 제공.

기능:
  - /api/ws/chat — Long-polling 채팅 (SSE 대안)
  - /api/ws/status — 실시간 큐/GPU 상태 스트리밍
  - /api/ws/workflow — 워크플로 진행 실시간 스트리밍
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response
from core.security import require_auth
from core import model_router
from core.model_router import OllamaAdapter

logger = logging.getLogger("ollama-admin")

ws_bp = Blueprint("websocket", __name__)

_ollama_session = None
_OLLAMA_BASE = None


def init_websocket(*, ollama_session, ollama_base):
    global _ollama_session, _OLLAMA_BASE
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base


@ws_bp.route("/api/ws/chat", methods=["POST"])
@require_auth
def ws_chat_stream():
    """실시간 채팅 스트리밍 — SSE 토큰 단위 (first-token latency 최소화)
    ---
    tags: [WebSocket / Streaming]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model, messages]
          properties:
            model: {type: string}
            messages:
              type: array
              items:
                type: object
                properties:
                  role: {type: string, enum: [system, user, assistant]}
                  content: {type: string}
            options: {type: object}
    responses:
      200:
        description: SSE 스트리밍 (token-by-token)
    """
    data = request.json or {}
    model = data.get("model", "qwen2.5:7b")
    messages = data.get("messages", [])
    options = data.get("options", {})

    if not messages:
        return jsonify({"error": "messages required"}), 400

    session_id = str(uuid.uuid4())

    def generate():
        yield f"data: {json.dumps({'event': 'start', 'session_id': session_id, 'model': model}, ensure_ascii=False)}\n\n"

        start_time = time.time()
        first_token_sent = False
        total_tokens = 0

        try:
            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
            # 모델은 요청(model=)이 정한다. 스트림 실패는 어댑터가 던지고 아래 except가 종전처럼 error 이벤트로 낸다.
            for ch in model_router.chat_stream(
                    "ws.chat", messages, model=model, options=options, timeout=120,
                    adapter=OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)):
                content = ch.text
                if content:
                    if not first_token_sent:
                        first_token_ms = int((time.time() - start_time) * 1000)
                        yield f"data: {json.dumps({'event': 'first_token', 'latency_ms': first_token_ms}, ensure_ascii=False)}\n\n"
                        first_token_sent = True

                    total_tokens += 1
                    yield f"data: {json.dumps({'event': 'token', 'content': content}, ensure_ascii=False)}\n\n"

                if ch.done:
                    break

        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

        total_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'event': 'done', 'session_id': session_id, 'total_tokens': total_tokens, 'total_ms': total_ms}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })


@ws_bp.route("/api/ws/generate", methods=["POST"])
@require_auth
def ws_generate_stream():
    """실시간 텍스트 생성 스트리밍 — SSE 토큰 단위
    ---
    tags: [WebSocket / Streaming]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model, prompt]
          properties:
            model: {type: string}
            prompt: {type: string}
            system: {type: string}
            options: {type: object}
    responses:
      200:
        description: SSE 스트리밍 (token-by-token)
    """
    data = request.json or {}
    model = data.get("model", "qwen2.5:7b")
    prompt = data.get("prompt", "")
    system = data.get("system", "")
    options = data.get("options", {})

    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    def generate():
        start_time = time.time()
        first_token_sent = False
        total_tokens = 0
        full_response = []

        try:
            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
            for ch in model_router.stream(
                    "ws.generate", prompt, model=model, system=system, options=options, timeout=120,
                    adapter=OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)):
                text = ch.text
                if text:
                    if not first_token_sent:
                        first_token_ms = int((time.time() - start_time) * 1000)
                        yield f"data: {json.dumps({'event': 'first_token', 'latency_ms': first_token_ms}, ensure_ascii=False)}\n\n"
                        first_token_sent = True

                    total_tokens += 1
                    full_response.append(text)
                    yield f"data: {json.dumps({'event': 'token', 'content': text}, ensure_ascii=False)}\n\n"

                if ch.done:
                    break

        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

        total_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'event': 'done', 'total_tokens': total_tokens, 'total_ms': total_ms, 'response': ''.join(full_response)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })


@ws_bp.route("/api/ws/status")
@require_auth
def ws_status_stream():
    """실시간 상태 모니터링 스트리밍 (큐 + GPU + 모델)
    ---
    tags: [WebSocket / Streaming]
    security: [{Bearer: []}]
    parameters:
      - name: interval
        in: query
        type: integer
        default: 3
        description: "업데이트 간격 (초)"
      - name: duration
        in: query
        type: integer
        default: 60
        description: "스트리밍 지속 시간 (초)"
    responses:
      200:
        description: SSE 상태 스트리밍
    """
    interval = min(int(request.args.get("interval", 3)), 30)
    duration = min(int(request.args.get("duration", 60)), 300)

    def generate():
        start = time.time()
        while time.time() - start < duration:
            status = _collect_live_status()
            yield f"data: {json.dumps(status, ensure_ascii=False)}\n\n"
            time.sleep(interval)

        yield f"data: {json.dumps({'event': 'end'}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@ws_bp.route("/api/gpu/scheduler")
@require_auth
def api_gpu_scheduler_status():
    """GPU 스케줄러 상태 — 프로파일, VRAM, 로드 모델
    ---
    tags: [GPU / Scheduler]
    security: [{Bearer: []}]
    responses:
      200:
        description: GPU 스케줄러 상태
    """
    from core.gpu_scheduler import get_scheduler_status
    return jsonify(get_scheduler_status())


@ws_bp.route("/api/gpu/scheduler/apply", methods=["POST"])
@require_auth
def api_gpu_scheduler_apply():
    """GPU 스케줄 프로파일 즉시 적용
    ---
    tags: [GPU / Scheduler]
    security: [{Bearer: []}]
    responses:
      200:
        description: 적용 결과
    """
    from core.gpu_scheduler import apply_schedule_profile
    return jsonify(apply_schedule_profile())


@ws_bp.route("/api/gpu/model/load", methods=["POST"])
@require_auth
def api_gpu_model_load():
    """특정 모델 VRAM 적재
    ---
    tags: [GPU / Scheduler]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string}
            urgent: {type: boolean, default: false}
    responses:
      200:
        description: 적재 결과
    """
    data = request.json or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "model required"}), 400
    from core.gpu_scheduler import ensure_model_loaded
    ok = ensure_model_loaded(model, urgent=data.get("urgent", False))
    return jsonify({"ok": ok, "model": model})


@ws_bp.route("/api/gpu/model/unload", methods=["POST"])
@require_auth
def api_gpu_model_unload():
    """특정 모델 VRAM 해제
    ---
    tags: [GPU / Scheduler]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string}
    responses:
      200:
        description: 해제 결과
    """
    data = request.json or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "model required"}), 400
    from core.gpu_scheduler import unload_model
    ok = unload_model(model)
    return jsonify({"ok": ok, "model": model})


@ws_bp.route("/api/priority/info")
@require_auth
def api_priority_info():
    """우선순위 시스템 정보
    ---
    tags: [Queue / Priority]
    security: [{Bearer: []}]
    responses:
      200:
        description: 우선순위 레벨 + 도메인 매핑
    """
    from core.priority_queue import get_priority_stats
    return jsonify(get_priority_stats())


@ws_bp.route("/api/priority/classify", methods=["POST"])
@require_auth
def api_priority_classify():
    """콘텐츠 긴급도 분류 (테스트용)
    ---
    tags: [Queue / Priority]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            action: {type: string}
            content: {type: string}
    responses:
      200:
        description: 긴급도 분류 결과
    """
    data = request.json or {}
    from core.priority_queue import classify_urgency
    return jsonify(classify_urgency(action=data.get("action"), content=data.get("content")))


def _collect_live_status():
    """실시간 상태 수집"""
    status = {
        "event": "status",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Ollama 모델 상태
    try:
        r = _ollama_session.get(f"{_OLLAMA_BASE}/api/ps", timeout=3)
        models = r.json().get("models", [])
        status["loaded_models"] = [m.get("name", "") for m in models]
    except Exception:
        status["loaded_models"] = []

    # GPU VRAM
    try:
        from core.gpu_scheduler import get_vram_usage
        status["vram"] = get_vram_usage()
    except Exception:
        status["vram"] = {}

    # 큐 상태
    try:
        from core.config import get_self_base
        import requests as http_req
        r = http_req.get(f"{get_self_base()}/api/queue/status", timeout=2)
        status["queue"] = r.json()
    except Exception:
        status["queue"] = {}

    return status
