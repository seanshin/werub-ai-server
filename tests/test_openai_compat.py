"""routes.openai_compat 순수 변환 단위테스트.

OpenAI ChatCompletion ↔ Ollama /api/chat 양방향 변환이 규격에 맞는지
(num_ctx 주입·tool_calls arguments 문자열화·SSE 델타·finish_reason·usage).
네트워크/Flask 라우팅 비의존 — 순수 함수만 검증.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.openai_compat import (  # noqa: E402
    build_ollama_payload,
    normalize_inbound_messages,
    _openai_tool_calls,
    ollama_chunk_to_openai,
    build_finish_chunk,
    ollama_final_to_openai,
    models_to_openai,
)


# --- build_ollama_payload ---------------------------------------------------

def test_payload_injects_num_ctx():
    body = {"model": "qwen2.5:7b", "messages": [{"role": "user", "content": "hi"}]}
    p = build_ollama_payload(body, 16384)
    assert p["model"] == "qwen2.5:7b"
    assert p["options"]["num_ctx"] == 16384
    assert p["stream"] is False


def test_payload_max_completion_tokens_to_num_predict():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "max_completion_tokens": 4096}
    p = build_ollama_payload(body, 8192)
    assert p["options"]["num_predict"] == 4096


def test_payload_num_predict_capped():
    """과도한 max_tokens는 _MAX_NUM_PREDICT(16384)로 캡 — 자원 남용 방지."""
    from routes.openai_compat import _MAX_NUM_PREDICT
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 9_999_999}
    p = build_ollama_payload(body, 32768)
    assert p["options"]["num_predict"] == _MAX_NUM_PREDICT


def test_payload_max_tokens_fallback():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "max_tokens": 512}
    p = build_ollama_payload(body, 8192)
    assert p["options"]["num_predict"] == 512


def test_payload_passes_tools_and_stream():
    tools = [{"type": "function", "function": {"name": "createFrame", "parameters": {}}}]
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": tools, "stream": True}
    p = build_ollama_payload(body, 8192)
    assert p["tools"] == tools
    assert p["stream"] is True


def test_payload_temperature_top_p_stop():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "temperature": 0.3, "top_p": 0.9, "stop": "END"}
    p = build_ollama_payload(body, 8192)
    assert p["options"]["temperature"] == 0.3
    assert p["options"]["top_p"] == 0.9
    assert p["options"]["stop"] == ["END"]


def test_payload_stop_list():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "stop": ["A", "B", 3]}
    p = build_ollama_payload(body, 8192)
    assert p["options"]["stop"] == ["A", "B"]  # 비문자열 제거


def test_payload_no_tools_key_when_absent():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    p = build_ollama_payload(body, 8192)
    assert "tools" not in p


# --- 멀티턴 입력 정규화 (arguments 문자열 → 객체, A.1 블로커) ----------------

def test_inbound_arguments_string_to_object():
    msgs = [
        {"role": "user", "content": "render hello"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "render", "arguments": "{\"jsx\":\"hello\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    out = normalize_inbound_messages(msgs)
    args = out[1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, dict) and args == {"jsx": "hello"}  # Ollama가 기대하는 객체
    # 잉여 OpenAI 필드 보존(Ollama 관용), tool 메시지 그대로
    assert out[1]["tool_calls"][0]["id"] == "call_1"
    assert out[2]["tool_call_id"] == "call_1"


def test_inbound_empty_arguments_to_empty_object():
    msgs = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "f", "arguments": ""}}]}]
    out = normalize_inbound_messages(msgs)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {}


def test_inbound_invalid_arguments_to_empty_object():
    msgs = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "f", "arguments": "{not json"}}]}]
    out = normalize_inbound_messages(msgs)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {}


def test_inbound_object_arguments_unchanged():
    msgs = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "f", "arguments": {"a": 1}}}]}]
    out = normalize_inbound_messages(msgs)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {"a": 1}


def test_inbound_plain_messages_untouched():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    out = normalize_inbound_messages(msgs)
    assert out == msgs


def test_payload_normalizes_tool_history():
    body = {"model": "m", "messages": [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "f", "arguments": "{\"x\":1}"}}]}]}
    p = build_ollama_payload(body, 32768)
    assert p["messages"][0]["tool_calls"][0]["function"]["arguments"] == {"x": 1}


# --- tool_choice 처리 (B.4) -------------------------------------------------

def test_tool_choice_none_drops_tools():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "f"}}], "tool_choice": "none"}
    p = build_ollama_payload(body, 32768)
    assert "tools" not in p


def test_tool_choice_required_adds_nudge():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "f"}}], "tool_choice": "required"}
    p = build_ollama_payload(body, 32768)
    assert "tools" in p
    assert p["messages"][-1]["role"] == "system" and "tool" in p["messages"][-1]["content"].lower()


def test_tool_choice_named_nudges_specific_tool():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "createFrame"}}],
            "tool_choice": {"type": "function", "function": {"name": "createFrame"}}}
    p = build_ollama_payload(body, 32768)
    assert "createFrame" in p["messages"][-1]["content"]


def test_tool_choice_auto_no_nudge():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "f"}}], "tool_choice": "auto"}
    p = build_ollama_payload(body, 32768)
    assert "tools" in p
    assert all(msg.get("content") != "" or msg["role"] != "system" for msg in p["messages"][1:])
    assert p["messages"][-1]["role"] == "user"  # nudge 없음


# --- tool_calls 변환 (객체 arguments → JSON 문자열) --------------------------

def test_tool_calls_arguments_stringified():
    ollama_tc = [{"id": "call_1", "function": {"index": 0, "name": "setFill",
                                               "arguments": {"color": "#fff"}}}]
    out = _openai_tool_calls(ollama_tc)
    assert out[0]["type"] == "function"
    assert out[0]["id"] == "call_1"
    assert out[0]["function"]["name"] == "setFill"
    # arguments는 반드시 문자열(JSON)
    assert isinstance(out[0]["function"]["arguments"], str)
    assert json.loads(out[0]["function"]["arguments"]) == {"color": "#fff"}


def test_tool_calls_generates_id_when_missing():
    out = _openai_tool_calls([{"function": {"name": "f", "arguments": {}}}])
    assert out[0]["id"].startswith("call_")
    assert out[0]["index"] == 0


def test_tool_calls_preserves_string_arguments():
    out = _openai_tool_calls([{"id": "c", "function": {"name": "f", "arguments": "{\"a\":1}"}}])
    assert out[0]["function"]["arguments"] == "{\"a\":1}"


# --- 스트림 청크 변환 -------------------------------------------------------

def test_chunk_content_delta_role_first_only():
    c1, role_sent, stc = ollama_chunk_to_openai(
        {"message": {"role": "assistant", "content": "Hel"}},
        cmpl_id="id1", created=1, model="m", role_sent=False)
    assert c1["object"] == "chat.completion.chunk"
    assert c1["choices"][0]["delta"]["role"] == "assistant"
    assert c1["choices"][0]["delta"]["content"] == "Hel"
    assert role_sent is True and stc is False

    c2, role_sent, _ = ollama_chunk_to_openai(
        {"message": {"content": "lo"}},
        cmpl_id="id1", created=1, model="m", role_sent=role_sent)
    assert "role" not in c2["choices"][0]["delta"]  # 두 번째부터 role 없음
    assert c2["choices"][0]["delta"]["content"] == "lo"


def test_chunk_empty_returns_none():
    out, role_sent, stc = ollama_chunk_to_openai(
        {"message": {"content": ""}}, cmpl_id="i", created=1, model="m", role_sent=False)
    assert out is None
    assert role_sent is False and stc is False


def test_chunk_tool_calls_sets_flag():
    out, _, stc = ollama_chunk_to_openai(
        {"message": {"tool_calls": [{"id": "c", "function": {"name": "f", "arguments": {"x": 1}}}]}},
        cmpl_id="i", created=1, model="m", role_sent=False)
    assert stc is True
    tc = out["choices"][0]["delta"]["tool_calls"][0]
    assert tc["function"]["name"] == "f"
    assert json.loads(tc["function"]["arguments"]) == {"x": 1}


def test_finish_chunk_with_usage():
    ch = build_finish_chunk(cmpl_id="i", created=1, model="m", finish_reason="tool_calls",
                            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert ch["choices"][0]["finish_reason"] == "tool_calls"
    assert ch["choices"][0]["delta"] == {}
    assert ch["usage"]["total_tokens"] == 15


def test_finish_chunk_no_usage():
    ch = build_finish_chunk(cmpl_id="i", created=1, model="m", finish_reason="stop")
    assert "usage" not in ch


# --- 비스트리밍 변환 --------------------------------------------------------

def test_final_content_message():
    resp = {"message": {"role": "assistant", "content": "Hello"},
            "prompt_eval_count": 12, "eval_count": 3}
    out = ollama_final_to_openai(resp, cmpl_id="i", created=1, model="m")
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"]["content"] == "Hello"
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}


def test_final_tool_calls_finish_reason():
    resp = {"message": {"content": "", "tool_calls": [
        {"id": "c", "function": {"name": "createFrame", "arguments": {"w": 100}}}]}}
    out = ollama_final_to_openai(resp, cmpl_id="i", created=1, model="m")
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    tc = out["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(tc["function"]["arguments"]) == {"w": 100}


def test_final_no_usage_when_counts_absent():
    out = ollama_final_to_openai({"message": {"content": "x"}}, cmpl_id="i", created=1, model="m")
    assert "usage" not in out


# --- /v1/models -------------------------------------------------------------

def test_models_to_openai():
    tags = {"models": [{"name": "qwen2.5:7b"}, {"name": "qwen2.5:14b"}]}
    out = models_to_openai(tags)
    assert out["object"] == "list"
    ids = [m["id"] for m in out["data"]]
    assert ids == ["qwen2.5:7b", "qwen2.5:14b"]
    assert all(m["object"] == "model" for m in out["data"])


def test_models_empty():
    out = models_to_openai({})
    assert out["data"] == []
