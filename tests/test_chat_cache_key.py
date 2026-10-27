"""응답 캐시 키 — 출력에 영향을 주는 입력이 전부 키에 들어가는지 (V0 회귀).

2026-07-30 그룹웨어 제보: `/api/chat` 캐시 키가 `model + 사용자 메시지 텍스트`뿐이라
**이미지가 달라도 직전 응답이 200으로 반환**됐다(A 명함 → B 명함 정보). think·options·
system도 같은 이유로 무시됐다. 여기 테스트는 그 조합들이 서로 다른 키를 만드는지 고정한다.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import chat_cache_key, generate_cache_key, _image_digest   # noqa: E402

RED = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
BLUE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
Q = "이 단색 그림의 색을 한국어 한 단어로만 답하세요."


def _msgs(img=None):
    m = {"role": "user", "content": Q}
    if img:
        m["images"] = [img]
    return [m]


# ── V0 본체: 같은 프롬프트 + 다른 이미지 ────────────────────────────────────

def test_different_image_yields_different_key():
    """그룹웨어 재현 프로브(빨강→파랑)가 같은 키를 만들면 안 된다."""
    assert chat_cache_key("qwen3-vl:8b", _msgs(RED)) != chat_cache_key("qwen3-vl:8b", _msgs(BLUE))


def test_image_presence_changes_key():
    """이미지 없는 호출과 있는 호출은 다른 응답 = 다른 키."""
    assert chat_cache_key("m", _msgs()) != chat_cache_key("m", _msgs(RED))


def test_same_request_is_stable():
    """캐시 이득은 유지 — 완전히 같은 요청은 같은 키."""
    assert chat_cache_key("m", _msgs(RED), {"think": False}) == \
           chat_cache_key("m", _msgs(RED), {"think": False})


def test_data_uri_prefix_is_equivalent():
    """`data:image/png;base64,` 접두는 서버가 떼고 보내므로 같은 이미지로 본다."""
    assert chat_cache_key("m", _msgs(RED)) == \
           chat_cache_key("m", _msgs("data:image/png;base64," + RED))


# ── 생성 파라미터 ───────────────────────────────────────────────────────────

def test_think_changes_key():
    a = chat_cache_key("m", _msgs(), {"think": False})
    b = chat_cache_key("m", _msgs(), {"think": True})
    c = chat_cache_key("m", _msgs(), {})
    assert len({a, b, c}) == 3                       # 미지정도 별개


def test_options_change_key():
    assert chat_cache_key("m", _msgs(), {"options": {"temperature": 0}}) != \
           chat_cache_key("m", _msgs(), {"options": {"temperature": 1.5}})


def test_options_key_order_is_stable():
    """dict 순서가 달라도 같은 옵션이면 같은 키(직렬화 정렬)."""
    assert chat_cache_key("m", _msgs(), {"options": {"temperature": 0, "num_ctx": 8192}}) == \
           chat_cache_key("m", _msgs(), {"options": {"num_ctx": 8192, "temperature": 0}})


def test_system_message_changes_key():
    """system이 다르면 답이 달라진다 — 구 키는 user만 봐서 충돌했다."""
    base = _msgs()
    with_sys = [{"role": "system", "content": "너는 의사다."}] + base
    assert chat_cache_key("m", base) != chat_cache_key("m", with_sys)


def test_model_and_tools_change_key():
    assert chat_cache_key("a", _msgs()) != chat_cache_key("b", _msgs())
    assert chat_cache_key("a", _msgs(), {"tools": [{"name": "x"}]}) != \
           chat_cache_key("a", _msgs(), {"tools": [{"name": "y"}]})


# ── 견고성 ──────────────────────────────────────────────────────────────────

def test_malformed_messages_do_not_raise():
    assert chat_cache_key("m", [None, "junk", {"role": "user"}])          # 예외 없이 키 생성
    assert chat_cache_key("m", None)
    assert chat_cache_key("m", [{"role": "user", "images": RED}])         # 문자열 단일 이미지


def test_image_digest_is_content_addressed():
    assert _image_digest(RED) == _image_digest(RED)
    assert _image_digest(RED) != _image_digest(BLUE)


# ── /api/generate·큐 경로(같은 규율) ────────────────────────────────────────

def test_generate_key_includes_options_and_system():
    base = generate_cache_key("m", "프롬프트")
    assert base != generate_cache_key("m", "프롬프트", "시스템")
    assert base != generate_cache_key("m", "프롬프트", None, {"temperature": 1.5})


def test_generate_key_accepts_json_string_options():
    """큐는 options를 JSON 문자열로 보관 — 제출/워커 양쪽 키가 일치해야 히트한다."""
    assert generate_cache_key("m", "p", "", {"temperature": 0.7}) == \
           generate_cache_key("m", "p", "", '{"temperature": 0.7}')
