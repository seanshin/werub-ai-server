"""캐시 키 서로게이트 내성 — 2026-08-03 `/api/chat` 500 1,726건 재발 가드.

JS 클라이언트가 이모지 중간에서 문자열을 자르면 짝 잃은 서로게이트(`\ud83c`)가 넘어온다.
`ensure_ascii=False` JSON을 UTF-8로 인코딩하면 `UnicodeEncodeError`로 터진다.
**캐시 키 함수는 어떤 입력에도 죽으면 안 되고, 서로 다른 입력이 같은 키가 되어도 안 된다.**
"""
from core.cache import response_cache_key, chat_cache_key

LONE = "\ud83c"          # 이모지 서로게이트 페어의 반쪽


def test_lone_surrogate_no_exception():
    k = response_cache_key({"model": "m", "text": f"안녕 {LONE} 테스트"})
    assert isinstance(k, str) and len(k) == 64


def test_chat_key_with_surrogate():
    msgs = [{"role": "user", "content": f"명함 {LONE} 분석"}]
    assert len(chat_cache_key("qwen2.5:14b", msgs)) == 64


def test_surrogate_keys_stay_distinct():
    """errors='replace'였다면 서로 다른 입력이 같은 키가 된다 — 그걸 막는다."""
    a = response_cache_key({"t": f"{LONE}A"})
    b = response_cache_key({"t": f"{LONE}B"})
    c = response_cache_key({"t": "\ud83d" + "A"})
    assert a != b and a != c


def test_normal_text_unaffected():
    a = response_cache_key({"t": "평범한 한국어"})
    assert a == response_cache_key({"t": "평범한 한국어"})
    assert a != response_cache_key({"t": "다른 한국어"})


def test_image_digest_surrogate_safe():
    from core.cache import _image_digest
    assert len(_image_digest(f"data:image/png;base64,AAA{LONE}")) == 64
