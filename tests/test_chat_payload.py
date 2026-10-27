"""`/api/chat` 업스트림 페이로드 — 호출자가 준 필드를 버리지 않는지 (그룹웨어 V5 계열).

종전 페이로드는 `{model, messages, stream}`뿐이라 think·options가 조용히 버려졌고
(`think:false`가 무효), messages의 `data:` 접두 이미지도 정규화되지 않아 Ollama가
이미지를 무시했다(/api/generate는 v2.124.32·.50에서 이미 해소된 것과 비대칭).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.ollama import chat_payload, model_supports_thinking, _MODEL_CAPS_CACHE  # noqa: E402

B64 = "iVBORw0KGgoAAAANSUhEUg=="
MSGS = [{"role": "user", "content": "안녕"}]


def test_minimal_payload_unchanged():
    """옵션 없는 기존 호출은 종전과 같은 모양 — 기존 호출자 영향 0."""
    assert chat_payload("m", MSGS) == {"model": "m", "messages": MSGS, "stream": True}


def test_think_is_forwarded():
    assert chat_payload("m", MSGS, {"think": False}, supports_thinking=True)["think"] is False
    assert chat_payload("m", MSGS, {"think": True}, supports_thinking=True)["think"] is True
    assert "think" not in chat_payload("m", MSGS, {})        # 미지정은 미주입(모델 기본값)


def test_think_dropped_for_non_thinking_model():
    """비-thinking 모델에 think를 넘기면 Ollama가 **요청 전체를 거절**한다
    (`"qwen2.5:14b" does not support thinking`) → 전달 전에 걸러 기존 호출자를 지킨다."""
    assert "think" not in chat_payload("qwen2.5:14b", MSGS, {"think": True},
                                       supports_thinking=False)


def test_capability_lookup_caches_and_fails_open():
    _MODEL_CAPS_CACHE.clear()
    calls = []

    def _show(m):
        calls.append(m)
        return {"capabilities": ["completion", "thinking"]}
    assert model_supports_thinking("mm", show_fn=_show) is True
    assert model_supports_thinking("mm", show_fn=_show) is True
    assert len(calls) == 1                                   # 2회차는 캐시

    def _boom(m):
        raise RuntimeError("unreachable")
    _MODEL_CAPS_CACHE.clear()
    assert model_supports_thinking("zz", show_fn=_boom) is True   # 조회 실패=판단 보류(전달)

    _MODEL_CAPS_CACHE.clear()
    assert model_supports_thinking("nn", show_fn=lambda m: {"capabilities": ["completion"]}) is False


def test_options_forwarded_only_when_meaningful():
    assert chat_payload("m", MSGS, {"options": {"temperature": 0}})["options"] == {"temperature": 0}
    assert "options" not in chat_payload("m", MSGS, {"options": {}})
    assert "options" not in chat_payload("m", MSGS, {"options": "bad"})


def test_data_uri_prefix_stripped_in_messages():
    """`data:image/png;base64,` 접두를 떼야 Ollama가 이미지를 읽는다."""
    msgs = [{"role": "user", "content": "읽어줘", "images": ["data:image/png;base64," + B64]}]
    assert chat_payload("m", msgs)["messages"][0]["images"] == [B64]


def test_raw_base64_untouched():
    msgs = [{"role": "user", "content": "읽어줘", "images": [B64]}]
    assert chat_payload("m", msgs)["messages"][0]["images"] == [B64]


def test_empty_images_key_removed():
    """빈 이미지 배열은 키째 제거 — 업스트림 혼선 방지."""
    msgs = [{"role": "user", "content": "안녕", "images": ["", "  "]}]
    assert "images" not in chat_payload("m", msgs)["messages"][0]


def test_original_messages_not_mutated():
    """호출자 객체를 건드리면 세션 저장·캐시 키가 오염된다."""
    msgs = [{"role": "user", "content": "x", "images": ["data:image/png;base64," + B64]}]
    chat_payload("m", msgs)
    assert msgs[0]["images"] == ["data:image/png;base64," + B64]


def test_format_and_tools_forwarded():
    p = chat_payload("m", MSGS, {"format": "json", "tools": [{"name": "t"}]})
    assert p["format"] == "json" and p["tools"] == [{"name": "t"}]


def test_stream_flag():
    assert chat_payload("m", MSGS, {}, stream=False)["stream"] is False
