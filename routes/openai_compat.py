"""OpenAI 호환 엔드포인트 — POST /v1/chat/completions + GET /v1/models

OpenPencil 등 OpenAI Chat Completions 클라이언트(ai-sdk)가 우리 AI 서버를
표준 OpenAI 포맷으로 호출할 수 있게 한다.

설계: 순수 패스스루(Ollama `/v1`)가 아니라 Ollama `/api/chat`로 **변환 프록시**한다.
이유 = Ollama `/v1`은 전역 OLLAMA_CONTEXT_LENGTH(현재 8192)에 묶여 OpenPencil의
대형 시스템 프롬프트(~27KB)+다수 도구 스키마를 조용히 절단한다. `/api/chat`는
요청별 `options.num_ctx` 주입이 가능하므로 전역 env(전 모델 VRAM)를 건드리지 않고
필요한 컨텍스트만 키운다.

번역:
  요청  OpenAI ChatCompletion  → Ollama /api/chat (options.num_ctx 등 주입)
  응답  Ollama NDJSON 스트림    → OpenAI SSE(data: {...}\\n\\n + [DONE])
        Ollama 단건 JSON        → OpenAI ChatCompletion JSON
  도구  Ollama tool_calls.function.arguments(객체) → OpenAI arguments(JSON 문자열)
"""

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

# ---------------------------------------------------------------------------
# 왜 Ollama의 네이티브 /v1 을 쓰지 않고 직접 변환하는가 (핵심 설계 결정)
#
# Ollama도 /v1 을 제공하지만 **전역 컨텍스트에 묶여** 대형 시스템 프롬프트+도구 스키마가
# 무성 절단된다. 그래서 /api/chat 으로 변환하면서 요청별 options.num_ctx 를 주입한다.
#
# 추가로 OpenAI ↔ Ollama 스키마 차이를 여기서 흡수한다:
#   - tool_calls.arguments: OpenAI=JSON **문자열** / Ollama=**객체**
#     (변환 없으면 멀티턴 에이전트 루프가 1스텝 만에 죽는다)
#   - 멀티모달 content 배열 → content 문자열 + images[](raw base64)
#   - 스트리밍: Ollama NDJSON → OpenAI SSE(`data: {...}` + [DONE])
#
# ⚠️ 원격 http(s) 이미지 URL은 **서버가 대신 받아오지 않는다** — 요청 경로에서 임의 URL을
#    fetch 하는 것은 SSRF 표면이라 우리 정책상 금지(400으로 안내).
#
# 상세: docs/DEVELOPER_GUIDE.md §1-1, §1-3, §1-4
# ---------------------------------------------------------------------------
import json
import logging
import time
import uuid

import requests as http_requests
from flask import Blueprint, request, jsonify, Response

logger = logging.getLogger("ollama-admin")

openai_bp = Blueprint("openai_compat", __name__)

_OLLAMA_BASE = None
_load_config = None

# OpenPencil 등 대형 페이로드(시스템 프롬프트+도구 스키마)가 전역 ctx(8192)를 넘으면 절단되므로
# 기본 컨텍스트를 키운다. config.json `openpencil_num_ctx`로 무재배포 튜닝.
# ⚠️num_ctx는 input+output 총량 — OpenPencil 입력(27KB 시스템 ~7K토큰 + 90+도구 ~11K토큰 ≈ 17K)이
# 16384를 초과하므로 32768 필요(아니면 입력 절단=도구호출 깨짐).
# ⚠️🔴 VRAM 주의: Ollama는 KV를 num_ctx × OLLAMA_NUM_PARALLEL로 잡는다(현재 parallel=4). GPU 실측:
#   7b@16K=10.16GB GPU완전 / 7b@32K=17.81GB **CPU 부분 스필**(parallel=4라 32K×4 KV). 스필=느림.
#   → 32K를 GPU 완전 적재로 빠르게 쓰려면 OLLAMA_NUM_PARALLEL=2 권장(7b@32K≈11GB). parallel=4 유지 시
#   32K는 동작하나 느리다(에이전트 50스텝엔 부적합). 모델은 클라이언트(OpenPencil) model 값이 결정.
_DEFAULT_NUM_CTX = 32768
# 비정상적으로 큰 값으로 인한 OOM 방지용 상한.
_MAX_NUM_CTX = 131072
# max_tokens(→num_predict) 상한 — 인증된 클라이언트가 초장문 생성을 요청해 단일 워커를
# 장시간 점유(타임아웃까지)하는 자원 남용 방지. 정상 출력엔 충분, 초과분은 캡.
_MAX_NUM_PREDICT = 16384
_REQUEST_TIMEOUT = 600


def init_openai_compat(*, ollama_base, load_config):
    global _OLLAMA_BASE, _load_config
    _OLLAMA_BASE = ollama_base.rstrip("/")
    _load_config = load_config


def _num_ctx():
    cfg = {}
    try:
        cfg = _load_config() if _load_config else {}
    except Exception:
        cfg = {}
    try:
        n = int(cfg.get("openpencil_num_ctx", _DEFAULT_NUM_CTX))
    except (TypeError, ValueError):
        n = _DEFAULT_NUM_CTX
    if n <= 0:
        n = _DEFAULT_NUM_CTX
    return min(n, _MAX_NUM_CTX)


def _new_id():
    return "chatcmpl-" + uuid.uuid4().hex[:24]


# ---------------------------------------------------------------------------
# 순수 변환 함수 (Flask/네트워크 비의존 — 단위테스트 대상)
# ---------------------------------------------------------------------------

def normalize_inbound_messages(messages):
    """OpenAI 메시지의 assistant.tool_calls.arguments(JSON 문자열) → Ollama 객체로 역변환.

    🔑 멀티턴 에이전트 루프 핵심: OpenAI는 assistant 턴의 tool_calls arguments를 JSON **문자열**로
    보내지만 Ollama `/api/chat`는 **객체**를 기대한다(문자열이면 400 "Value looks like object,
    can't find closing '}'"). 응답 방향(객체→문자열)의 역이며, 이게 없으면 도구 호출 직후 다음
    스텝 요청이 전부 실패해 에이전트 루프가 1스텝 만에 멈춘다. id/type/tool_call_id 등 OpenAI
    잉여 필드는 Ollama가 관용하므로 arguments만 손댄다.
    """
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get("tool_calls"):
            m = dict(m)
            fixed = []
            for tc in m["tool_calls"]:
                tc = dict(tc)
                fn = dict(tc.get("function") or {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        fn["arguments"] = json.loads(args) if args.strip() else {}
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("openai_compat: tool_call arguments not valid JSON, using {}")
                        fn["arguments"] = {}
                tc["function"] = fn
                fixed.append(tc)
            m["tool_calls"] = fixed
        m = _convert_multimodal_content(m)
        out.append(m)
    return out


class UnsupportedImageURL(ValueError):
    """원격 URL 이미지 — 서버가 대신 내려받지 않는다(SSRF 표면 차단)."""


def _convert_multimodal_content(m):
    """OpenAI 멀티모달 content 배열 → Ollama 형식(content 문자열 + images[]).

    OpenAI는 `content: [{"type":"text",...},{"type":"image_url","image_url":{"url":...}}]`
    를 쓰지만 Ollama `/api/chat`는 **content 문자열 + images(raw base64 배열)**를 받는다.
    변환 없이 넘기면 업스트림이 거절해 502가 났다(그룹웨어 제보).

    원격 http(s) URL은 **서버가 대신 받아오지 않는다** — 요청 경로에서 임의 URL을 가져오는
    것은 SSRF 표면이라 우리 정책상 금지다(meeting/detect 계열과 동일 규율). base64 data URI로
    보내도록 400으로 안내한다.
    """
    if not isinstance(m, dict) or not isinstance(m.get("content"), list):
        return m
    texts, images = [], []
    for part in m["content"]:
        if not isinstance(part, dict):
            if isinstance(part, str):
                texts.append(part)
            continue
        ptype = part.get("type")
        if ptype == "text":
            texts.append(part.get("text") or "")
        elif ptype in ("image_url", "input_image"):
            url = part.get("image_url", {})
            url = url.get("url") if isinstance(url, dict) else (url or part.get("image_url"))
            url = (url or "").strip()
            if url.startswith(("http://", "https://")):
                raise UnsupportedImageURL(
                    "원격 URL 이미지는 지원하지 않습니다. base64 data URI로 보내세요"
                    "(예: data:image/png;base64,...)")
            # data: 접두 제거 + Ollama가 못 읽는 포맷 변환(/api/chat·generate와 같은 규율)
            from core.images import normalize_image
            norm = normalize_image(url)
            if norm:
                images.append(norm)
    m = dict(m)
    m["content"] = "\n".join(t for t in texts if t)
    if images:
        m["images"] = images
    return m


def _tool_choice_nudge(tool_choice):
    """Ollama /api/chat는 tool_choice를 강제하지 못한다(0.21 실측: required/named 무시).
    required/named는 시스템 지시로 best-effort 유도. 반환=추가 system content(없으면 None)."""
    if tool_choice == "required":
        return "You must use one of the available tools to answer. Do not reply with plain text."
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name")
        if name:
            return f"You must call the `{name}` tool to answer. Do not reply with plain text."
    return None


def build_ollama_payload(body, num_ctx):
    """OpenAI ChatCompletion 요청 dict → Ollama /api/chat 페이로드 dict."""
    model = body.get("model")
    messages = normalize_inbound_messages(body.get("messages") or [])
    tool_choice = body.get("tool_choice")

    # 도구: OpenAI function 스키마 = Ollama 스키마(동일). tool_choice="none"이면 미전달(=비활성).
    tools = body.get("tools")
    if tools and tool_choice != "none":
        nudge = _tool_choice_nudge(tool_choice)
        if nudge:  # Ollama 미강제 → 시스템 지시로 유도(best-effort)
            messages = messages + [{"role": "system", "content": nudge}]

    payload = {
        "model": model,
        "messages": messages,
        "stream": bool(body.get("stream", False)),
    }
    if tools and tool_choice != "none":
        payload["tools"] = tools

    options = {"num_ctx": num_ctx}

    # 토큰 상한: OpenAI는 max_completion_tokens(신)/max_tokens(구) → Ollama num_predict
    # 상한 _MAX_NUM_PREDICT로 캡(자원 남용/단일 워커 장시간 점유 방지).
    max_tok = body.get("max_completion_tokens", body.get("max_tokens"))
    if isinstance(max_tok, int) and max_tok > 0:
        options["num_predict"] = min(max_tok, _MAX_NUM_PREDICT)

    if isinstance(body.get("temperature"), (int, float)):
        options["temperature"] = body["temperature"]
    if isinstance(body.get("top_p"), (int, float)):
        options["top_p"] = body["top_p"]
    if isinstance(body.get("frequency_penalty"), (int, float)):
        options["repeat_penalty"] = 1.0 + body["frequency_penalty"]
    if isinstance(body.get("seed"), int):
        options["seed"] = body["seed"]

    stop = body.get("stop")
    if isinstance(stop, str):
        options["stop"] = [stop]
    elif isinstance(stop, list) and stop:
        options["stop"] = [s for s in stop if isinstance(s, str)]

    payload["options"] = options
    # 참고: tool_choice/n/presence_penalty 등은 Ollama /api/chat가 지원하지 않아 무시.
    return payload


def _openai_tool_calls(ollama_tool_calls):
    """Ollama tool_calls(arguments=객체) → OpenAI tool_calls(arguments=JSON 문자열)."""
    out = []
    for i, tc in enumerate(ollama_tool_calls or []):
        fn = tc.get("function", {}) or {}
        args = fn.get("arguments", {})
        # OpenAI 규격: arguments는 반드시 문자열(JSON). Ollama는 객체로 준다.
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        idx = fn.get("index", i)
        out.append({
            "index": idx,
            "id": tc.get("id") or ("call_" + uuid.uuid4().hex[:8]),
            "type": "function",
            "function": {"name": fn.get("name", ""), "arguments": args},
        })
    return out


def ollama_chunk_to_openai(chunk, *, cmpl_id, created, model, role_sent):
    """Ollama 스트림 청크 → (OpenAI chunk dict | None, role_sent, saw_tool_calls).

    내보낼 델타가 없으면 첫 요소가 None.
    """
    msg = chunk.get("message") or {}
    delta = {}
    saw_tool_calls = False

    content = msg.get("content")
    tool_calls = msg.get("tool_calls")

    if tool_calls:
        delta["tool_calls"] = _openai_tool_calls(tool_calls)
        saw_tool_calls = True
    if content:
        delta["content"] = content

    if not delta:
        return None, role_sent, saw_tool_calls

    if not role_sent:
        delta = {"role": "assistant", **delta}
        role_sent = True

    out = {
        "id": cmpl_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return out, role_sent, saw_tool_calls


def build_finish_chunk(*, cmpl_id, created, model, finish_reason, usage=None):
    chunk = {
        "id": cmpl_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    if usage:
        chunk["usage"] = usage
    return chunk


def _usage(resp):
    p = resp.get("prompt_eval_count")
    c = resp.get("eval_count")
    if p is None and c is None:
        return None
    p = p or 0
    c = c or 0
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def ollama_final_to_openai(resp, *, cmpl_id, created, model):
    """비스트리밍: Ollama 단건 응답 → OpenAI ChatCompletion JSON."""
    msg = resp.get("message") or {}
    message = {"role": "assistant", "content": msg.get("content") or ""}
    finish_reason = "stop"
    if msg.get("tool_calls"):
        message["tool_calls"] = _openai_tool_calls(msg["tool_calls"])
        finish_reason = "tool_calls"
    out = {
        "id": cmpl_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    usage = _usage(resp)
    if usage:
        out["usage"] = usage
    return out


def models_to_openai(tags):
    """Ollama /api/tags → OpenAI /v1/models 리스트."""
    data = []
    for m in (tags.get("models") or []):
        name = m.get("name") or m.get("model")
        if not name:
            continue
        data.append({
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "ollama",
        })
    return {"object": "list", "data": data}


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

def _err(message, status, etype="invalid_request_error", code=None):
    return jsonify({"error": {"message": message, "type": etype, "code": code}}), status


@openai_bp.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("invalid JSON body", 400)
    if not body.get("model"):
        return _err("'model' is required", 400)
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        return _err("'messages' must be a non-empty array", 400)

    num_ctx = _num_ctx()
    try:
        payload = build_ollama_payload(body, num_ctx)
    except UnsupportedImageURL as e:
        # 502(업스트림 거절) 대신 원인이 분명한 400으로 답한다.
        return _err(str(e), 400)
    stream = payload["stream"]
    model = body["model"]
    cmpl_id = _new_id()
    created = int(time.time())

    if stream:
        def gen():
            role_sent = False
            saw_tool_calls = False
            try:
                r = http_requests.post(
                    f"{_OLLAMA_BASE}/api/chat",
                    json=payload, stream=True, timeout=_REQUEST_TIMEOUT,
                )
                if r.status_code != 200:
                    logger.warning("openai_compat upstream error %s: %s", r.status_code, r.text[:300])
                    yield "data: " + json.dumps({
                        "error": {"message": f"upstream model error ({r.status_code})",
                                  "type": "upstream_error", "code": r.status_code}
                    }) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                usage = None
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    out, role_sent, stc = ollama_chunk_to_openai(
                        chunk, cmpl_id=cmpl_id, created=created, model=model,
                        role_sent=role_sent)
                    if stc:
                        saw_tool_calls = True
                    if out is not None:
                        yield "data: " + json.dumps(out, ensure_ascii=False) + "\n\n"
                    if chunk.get("done"):
                        usage = _usage(chunk)
                        break
                finish = "tool_calls" if saw_tool_calls else "stop"
                yield "data: " + json.dumps(build_finish_chunk(
                    cmpl_id=cmpl_id, created=created, model=model,
                    finish_reason=finish, usage=usage), ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"
            except http_requests.RequestException as e:
                logger.warning("openai_compat upstream connection failed: %s", e)
                yield "data: " + json.dumps({
                    "error": {"message": "upstream connection failed",
                              "type": "upstream_error", "code": None}
                }) + "\n\n"
                yield "data: [DONE]\n\n"

        resp = Response(gen(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"  # nginx 버퍼링 비활성(SSE)
        return resp

    # 비스트리밍
    try:
        r = http_requests.post(
            f"{_OLLAMA_BASE}/api/chat",
            json=payload, timeout=_REQUEST_TIMEOUT,
        )
    except http_requests.RequestException as e:
        logger.warning("openai_compat upstream connection failed: %s", e)
        return _err("upstream connection failed", 502, "upstream_error")
    if r.status_code != 200:
        logger.warning("openai_compat upstream error %s: %s", r.status_code, r.text[:300])
        return _err(f"upstream model error ({r.status_code})", 502, "upstream_error",
                    code=r.status_code)
    try:
        resp_json = r.json()
    except ValueError:
        return _err("invalid upstream response", 502, "upstream_error")
    return jsonify(ollama_final_to_openai(resp_json, cmpl_id=cmpl_id, created=created, model=model))


@openai_bp.route("/v1/models", methods=["GET"])
def list_models():
    try:
        r = http_requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=15)
        r.raise_for_status()
        tags = r.json()
    except (http_requests.RequestException, ValueError) as e:
        return _err(f"failed to list models: {e}", 502, "upstream_error")
    return jsonify(models_to_openai(tags))
