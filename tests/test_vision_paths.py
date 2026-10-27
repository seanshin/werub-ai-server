"""비전 입력 경로 계약 회귀 (그룹웨어 제보 — 경로마다 지원이 달랐다).

실측: `/api/chat + images[]`는 동작하나 `/api/generate + images[]`는 **조용히 무시**되고
(모델이 "이미지가 포함되어 있지 않습니다"라고 답해 호출자가 원인을 알 수 없었다),
`/v1/chat/completions + image_url`은 변환 부재로 502였다. 둘 다 지원으로 봉합한 계약을 고정한다.
"""
import pytest

from routes.ollama import _normalize_images
from routes.openai_compat import UnsupportedImageURL, normalize_inbound_messages


# --- /api/generate ---------------------------------------------------------

def test_data_uri_prefix_is_stripped():
    """Ollama는 접두 없는 raw base64만 받는다 — 접두를 넘기면 이미지가 무시된다."""
    assert _normalize_images(["data:image/png;base64,AAAB"]) == ["AAAB"]
    assert _normalize_images(["AAAB"]) == ["AAAB"]


def test_non_list_and_blank_are_dropped():
    for bad in (None, "", "data:image/png;base64,AAAB", 42, {}):
        assert _normalize_images(bad) == []
    assert _normalize_images(["", "   ", None, 7]) == []


def test_multiple_images_preserved_in_order():
    out = _normalize_images(["data:image/jpeg;base64,ONE", "TWO"])
    assert out == ["ONE", "TWO"]


# --- /v1/chat/completions --------------------------------------------------

def test_openai_multimodal_converted_to_ollama_shape():
    msgs = normalize_inbound_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "명함을 읽어줘"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,IMG1"}},
        ],
    }])
    assert msgs[0]["content"] == "명함을 읽어줘"
    assert msgs[0]["images"] == ["IMG1"], "변환 없으면 업스트림이 거절해 502가 난다"


def test_plain_string_content_untouched():
    msgs = normalize_inbound_messages([{"role": "user", "content": "안녕"}])
    assert msgs[0]["content"] == "안녕" and "images" not in msgs[0]


def test_remote_url_image_rejected_not_fetched():
    """요청 경로에서 임의 URL을 받아오는 것은 SSRF 표면이라 금지 — 400으로 안내한다."""
    with pytest.raises(UnsupportedImageURL):
        normalize_inbound_messages([{
            "role": "user",
            "content": [{"type": "image_url",
                         "image_url": {"url": "https://example.com/a.png"}}],
        }])


def test_multiple_text_parts_joined():
    msgs = normalize_inbound_messages([{
        "role": "user",
        "content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}],
    }])
    assert msgs[0]["content"] == "A\nB"
