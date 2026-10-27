"""MCP (Model Context Protocol) Blueprint — Streamable HTTP 전송.

외부 AI 에이전트(Claude Desktop/Code, GPT, Cursor)가
config/tools.json 도구를 MCP 표준으로 호출할 수 있게 한다.

엔드포인트: POST /mcp (JSON-RPC 2.0)
"""
import json
import logging
import time
import uuid

from flask import Blueprint, request, jsonify, Response

logger = logging.getLogger("ollama-admin")

mcp_bp = Blueprint("mcp", __name__)

# Injected at init
_tool_registry = None
_execute_action = None
_flask_app = None

MCP_SERVER_NAME = "werub-ai"
MCP_SERVER_VERSION = "2.66.0"
MCP_PROTOCOL_VERSION = "2025-03-26"


def init_mcp(*, tool_registry, execute_action_fn, flask_app=None):
    """app.py에서 호출하여 의존성 주입"""
    global _tool_registry, _execute_action, _flask_app
    _tool_registry = tool_registry
    _execute_action = execute_action_fn
    _flask_app = flask_app


# ---------------------------------------------------------------------------
# MCP JSON-RPC endpoint
# ---------------------------------------------------------------------------

@mcp_bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    """MCP Streamable HTTP — JSON-RPC 2.0
    ---
    tags: [MCP]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [jsonrpc, method]
          properties:
            jsonrpc: {type: string, example: "2.0"}
            id: {type: string}
            method: {type: string}
            params: {type: object}
    responses:
      200:
        description: JSON-RPC response
    """
    body = request.get_json(force=True)

    # Batch request
    if isinstance(body, list):
        results = [_handle_rpc(req) for req in body]
        return Response(
            json.dumps(results, ensure_ascii=False),
            content_type="application/json",
        )

    result = _handle_rpc(body)
    if result is None:
        # Notification (no id) — no response
        return "", 204

    return Response(
        json.dumps(result, ensure_ascii=False),
        content_type="application/json",
    )


def _handle_rpc(req):
    """단일 JSON-RPC 요청 처리"""
    rpc_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    handler = _METHODS.get(method)
    if not handler:
        return _error(rpc_id, -32601, f"Method not found: {method}")

    try:
        result = handler(params)
        if rpc_id is None:
            return None  # notification
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    except Exception as e:
        logger.error("MCP %s error: %s", method, e, exc_info=True)
        return _error(rpc_id, -32000, str(e)[:300])


def _error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# MCP Methods
# ---------------------------------------------------------------------------

def _initialize(params):
    """initialize — 클라이언트 핸드셰이크"""
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": {
            "name": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
        },
    }


def _initialized(params):
    """notifications/initialized — 클라이언트 초기화 완료 알림"""
    return {}


def _tools_list(params):
    """tools/list — 사용 가능한 도구 목록"""
    if not _tool_registry:
        return {"tools": []}

    tools = []
    for tool in _tool_registry.list_tools(include_schema=True):
        if not tool.get("enabled", True):
            continue
        tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool.get("parameters", {"type": "object", "properties": {}}),
        })

    return {"tools": tools}


def _tools_call(params):
    """tools/call — 도구 실행"""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if not _tool_registry or not _execute_action:
        return _tool_error("MCP server not initialized")

    # tools.json에서 action 조회
    action = _tool_registry.get_action(tool_name)
    if not action:
        return _tool_error(f"Unknown tool: {tool_name}")

    start = time.time()
    try:
        result, duration_ms = _execute_action(action, arguments, None)
    except Exception as e:
        return _tool_error(str(e)[:300])

    elapsed = int((time.time() - start) * 1000)

    # MCP 응답 형식
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str),
            },
        ],
        "isError": bool(result.get("error")) if isinstance(result, dict) else False,
        "_meta": {
            "tool": tool_name,
            "duration_ms": elapsed,
        },
    }


def _tool_error(message):
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _resources_list(params):
    """resources/list — 리소스 목록"""
    return {
        "resources": [
            {
                "uri": "status://server",
                "name": "서버 상태",
                "description": "AI 서버 시스템 상태 정보",
                "mimeType": "application/json",
            },
        ],
    }


def _resources_read(params):
    """resources/read — 리소스 읽기"""
    uri = params.get("uri", "")

    if uri == "status://server":
        from core.web_search import check_status as search_status
        status = {
            "server": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
            "tools_count": len(_tool_registry) if _tool_registry else 0,
            "search": search_status(),
        }
        return {
            "contents": [{
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(status, ensure_ascii=False),
            }],
        }

    return {"contents": [], "error": f"Unknown resource: {uri}"}


def _ping(params):
    """ping — 연결 확인"""
    return {}


# ---------------------------------------------------------------------------
# Method routing
# ---------------------------------------------------------------------------

_METHODS = {
    "initialize": _initialize,
    "notifications/initialized": _initialized,
    "ping": _ping,
    "tools/list": _tools_list,
    "tools/call": _tools_call,
    "resources/list": _resources_list,
    "resources/read": _resources_read,
}
