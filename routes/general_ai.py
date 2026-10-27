"""General AI Blueprint — 일반 AI 표면, 의료 AI와 구조적 분리 (v2.125.34)

의료 표면(/api/medical/* 등)과 **별도의 일반 AI 영역**이다. 분리는 문서가 아니라
코드가 보증한다:
  1) 모델 allowlist — 이 표면은 아래 ALLOWED_MODELS만 호출할 수 있다. 의료 특화
     모델(med42·medgemma·medllama 계열)은 목록에 없고, 목록 밖 model 지정은 400이다.
  2) 시스템 프롬프트 계약 — 의료·법률 등 전문 자문 요청에는 답하지 않고 전용
     표면을 안내하도록 고정한다.
  3) 라이선스 준수 장치 — community 프로필의 HyperCLOVA X SEED(1.5B)는 라이선스가
     의료 등 전문 서비스 제공 용도 사용을 금지한다(HyperCLOVA X SEED Model License,
     NAVER). 이 모델이 일반 표면에만 배선되는 것 자체가 준수 조건이며, 응답에
     `powered_by` 표기를 싣는다(고지 의무).

4 엔드포인트:
  POST /api/general/chat         — 일반/커뮤니티 채팅 (비스트림)
  POST /api/general/chat/stream  — NDJSON 스트리밍
  GET  /api/general/models       — 이 표면에서 허용된 모델 + 가용성
  GET  /api/general/status       — 상태 + 분리 계약 명시
"""
import json
import logging
import time

import requests
from flask import Blueprint, Response, jsonify, request

from core.config import OLLAMA_BASE

logger = logging.getLogger("ollama-admin")

general_ai_bp = Blueprint("general_ai", __name__)

# ── 표면 계약 ────────────────────────────────────────────────────────────────
# 이 표면에서 부를 수 있는 모델 전부. 의료 특화 모델은 여기 없어야 하며,
# 회귀(tests/test_general_ai.py)가 이름 패턴으로 재유입을 차단한다.
ALLOWED_MODELS = {
    "qwen2.5:14b":        {"tier": "quality", "note": "일반 기본 — 상주 모델"},
    "qwen2.5:7b":         {"tier": "light",   "note": "경량 범용"},
    "exaone3.5:7.8b":     {"tier": "light",   "note": "한국어 범용(LG EXAONE)"},
    "hyperclova-seed:1.5b": {
        "tier": "community", "note": "커뮤니티 프로필 기본(HyperCLOVA X SEED 1.5B)",
        "powered_by": "HyperCLOVA X",
    },
}

# profile → (라우팅 task, 시스템 프롬프트). 모델은 config/model_routing.json이 정한다.
_SYSTEM_COMMON = (
    "당신은 WeRU.B의 일반 AI 어시스턴트다. 한국어로 간결하고 정확하게 답한다. "
    "의료·건강·법률 등 전문 자문이 필요한 질문에는 직접 판단을 제공하지 말고, "
    "전문 서비스 영역(의료 AI)이 별도로 있음을 안내한다. 모르는 것은 모른다고 말한다."
)
PROFILES = {
    "assistant": {
        "task": "general.chat",
        "system": _SYSTEM_COMMON,
    },
    "community": {
        "task": "general.community_chat",
        "system": _SYSTEM_COMMON + " 커뮤니티 게시판 도우미로서 친근하고 짧게 답한다.",
    },
}

MAX_MESSAGE_CHARS = 8000
MAX_HISTORY_ITEMS = 20
MAX_HISTORY_CHARS = 4000


def _powered_by(model: str):
    return (ALLOWED_MODELS.get(model) or {}).get("powered_by")


def _build_messages(profile: dict, history, message: str):
    msgs = [{"role": "system", "content": profile["system"]}]
    for h in history[-MAX_HISTORY_ITEMS:]:
        role = h.get("role")
        content = (h.get("content") or "")[:MAX_HISTORY_CHARS]
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": message})
    return msgs


def _validate(data):
    """공통 검증. (error_response, None) 또는 (None, ctx)를 돌려준다."""
    message = (data.get("message") or "").strip()
    if not message:
        return (jsonify({"error": "message is required"}), 400), None
    if len(message) > MAX_MESSAGE_CHARS:
        return (jsonify({"error": f"message too long (max {MAX_MESSAGE_CHARS} chars)"}), 400), None

    profile_name = data.get("profile", "assistant")
    profile = PROFILES.get(profile_name)
    if profile is None:
        return (jsonify({"error": f"unknown profile — one of {sorted(PROFILES)}"}), 400), None

    model = data.get("model")
    if model is not None and model not in ALLOWED_MODELS:
        # 🔴 분리 계약의 집행 지점 — 의료 모델을 포함한 목록 밖 모델은 여기서 끊는다.
        return (jsonify({"error": "model not allowed on the general surface",
                         "allowed": sorted(ALLOWED_MODELS)}), 400), None

    history = data.get("history") or []
    if not isinstance(history, list):
        return (jsonify({"error": "history must be a list"}), 400), None

    return None, {"message": message, "profile_name": profile_name,
                  "profile": profile, "model": model, "history": history}


@general_ai_bp.route("/api/general/chat", methods=["POST"])
def general_chat():
    """일반 AI 채팅 (의료 AI와 분리된 표면)

    일반 목적 대화. `profile=community`는 HyperCLOVA X SEED(1.5B)로 답하며
    응답에 `powered_by`가 실린다. 의료 특화 모델은 이 표면에서 호출할 수 없다
    (목록 밖 `model`은 400 — 허용 목록은 `GET /api/general/models`).
    ---
    tags: [General AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string, description: 사용자 메시지(최대 8000자)}
            profile: {type: string, enum: [assistant, community], default: assistant}
            model: {type: string, description: 허용 목록 내 모델 오버라이드(선택)}
            history:
              type: array
              description: 이전 대화(최대 20건 유지)
              items:
                type: object
                properties:
                  role: {type: string, enum: [user, assistant]}
                  content: {type: string}
    responses:
      200:
        description: 응답
        schema:
          type: object
          properties:
            message: {type: string, description: 모델 응답}
            model: {type: string}
            profile: {type: string}
            powered_by: {type: string, description: "커뮤니티 모델일 때 'HyperCLOVA X'(라이선스 고지)"}
            tokens: {type: integer}
            duration_ms: {type: integer}
      400:
        description: "message 누락·초과, 미허용 model/profile — 예: error=message is required"
      502:
        description: 생성 실패(LLM 오류를 빈 결과로 뭉개지 않는다)
    """
    err, ctx = _validate(request.get_json(force=True, silent=True) or {})
    if err:
        return err

    from core import model_router
    start = time.time()
    try:
        res = model_router.chat(
            ctx["profile"]["task"],
            _build_messages(ctx["profile"], ctx["history"], ctx["message"]),
            model=ctx["model"], think=False, timeout=120)
    except Exception as e:                        # 전송·배선 오류 — 무성 실패 금지
        logger.warning("general chat failed: %s", e)
        return jsonify({"error": f"generation failed: {e}"}), 502
    if not res.ok or not (res.text or "").strip():
        logger.warning("general chat not ok: status=%s error=%s", res.status, res.error)
        reason = res.error or (f"HTTP {res.status}" if not res.ok else "empty response")
        return jsonify({"error": reason}), 502

    out = {
        "message": res.text,
        "model": res.model,
        "profile": ctx["profile_name"],
        "tokens": res.tokens,
        "duration_ms": res.duration_ms or int((time.time() - start) * 1000),
    }
    pb = _powered_by(res.model)
    if pb:
        out["powered_by"] = pb
    return jsonify(out)


@general_ai_bp.route("/api/general/chat/stream", methods=["POST"])
def general_chat_stream():
    """일반 AI 채팅 스트리밍 (NDJSON)

    조각은 `{"delta": "..."}`, 마지막 줄은 `{"done": true, "model": ..., "tokens": ...}`.
    스트림 중 오류는 무언 종료 대신 `{"error": "..."}` 줄로 표면화된다.
    ---
    tags: [General AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string}
            profile: {type: string, enum: [assistant, community], default: assistant}
            model: {type: string}
            history: {type: array, items: {type: object}}
    responses:
      200: {description: NDJSON 스트림}
      400: {description: 검증 실패}
      502: {description: 스트림 시작 전 생성 실패}
    """
    err, ctx = _validate(request.get_json(force=True, silent=True) or {})
    if err:
        return err

    from core import model_router
    try:
        gen = model_router.chat_stream(
            ctx["profile"]["task"],
            _build_messages(ctx["profile"], ctx["history"], ctx["message"]),
            model=ctx["model"], think=False, timeout=120)
        first = next(gen, None)                   # 배선 오류를 200 스트림 이전에 드러낸다
    except Exception as e:
        logger.warning("general chat stream failed to start: %s", e)
        return jsonify({"error": f"generation failed: {e}"}), 502

    pb_meta = {"profile": ctx["profile_name"]}

    def _emit():
        try:
            for chunk in ([first] if first is not None else []):
                yield _line(chunk)
            for chunk in gen:
                yield _line(chunk)
        except Exception as e:                    # 무언 종료 금지(v2.125.13 규율)
            logger.warning("general chat stream aborted: %s", e)
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"

    def _line(chunk):
        if chunk.done:
            d = {"done": True, "model": chunk.model, "tokens": chunk.tokens, **pb_meta}
            pb = _powered_by(chunk.model)
            if pb:
                d["powered_by"] = pb
            return json.dumps(d, ensure_ascii=False) + "\n"
        return json.dumps({"delta": chunk.text}, ensure_ascii=False) + "\n"

    return Response(_emit(), mimetype="application/x-ndjson")


@general_ai_bp.route("/api/general/models")
def general_models():
    """일반 표면 허용 모델 목록 + 가용성

    이 표면에서 호출 가능한 모델 전부다(의료 특화 모델 없음 — 분리 계약).
    가용성 조회 실패 시 `available`은 null이다(결측을 false로 세지 않는다).
    ---
    tags: [General AI]
    responses:
      200:
        description: 허용 모델 목록
        schema:
          type: object
          properties:
            models:
              type: array
              items:
                type: object
                properties:
                  name: {type: string}
                  tier: {type: string}
                  note: {type: string}
                  powered_by: {type: string}
                  available: {type: boolean, description: 조회 실패 시 null}
            separation: {type: string, description: 분리 계약 설명}
    """
    installed = None
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        if r.ok:
            installed = {m.get("name") for m in r.json().get("models", [])}
    except Exception as e:
        logger.warning("general models availability check failed: %s", e)

    models = []
    for name, meta in sorted(ALLOWED_MODELS.items()):
        row = {"name": name, "tier": meta["tier"], "note": meta["note"],
               "available": (name in installed) if installed is not None else None}
        if meta.get("powered_by"):
            row["powered_by"] = meta["powered_by"]
        models.append(row)
    return jsonify({
        "models": models,
        "separation": "일반 AI 전용 표면 — 의료 특화 모델은 이 목록에 없고 호출도 거부된다"
                      "(의료는 /api/medical/* 표면).",
    })


@general_ai_bp.route("/api/general/status")
def general_status():
    """일반 AI 표면 상태
    ---
    tags: [General AI]
    responses:
      200:
        description: 상태
        schema:
          type: object
          properties:
            service: {type: string}
            profiles: {type: array, items: {type: string}}
            default_models: {type: object, description: "profile → 라우팅 표가 정한 모델"}
            separation: {type: object}
    """
    from core import model_router
    defaults = {}
    for name, p in PROFILES.items():
        try:
            defaults[name] = model_router.resolve(p["task"]).get("model")
        except Exception as e:                    # 라우팅 표 미등록 등 — 결측은 null
            logger.warning("general status resolve failed for %s: %s", name, e)
            defaults[name] = None
    return jsonify({
        "service": "general-ai",
        "profiles": sorted(PROFILES),
        "default_models": defaults,
        "separation": {
            "contract": "일반 AI와 의료 AI는 별도 표면이다",
            "enforcement": ["모델 allowlist(목록 밖 400)", "전문 자문 안내 시스템 프롬프트",
                            "HyperCLOVA 라이선스상 전문 서비스 금지 — 일반 표면 전용 배선"],
            "medical_surface": "/api/medical/*",
        },
    })
