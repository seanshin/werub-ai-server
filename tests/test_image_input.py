"""이미지 입력 정규화 — 호출자 이미지가 조용히 무시되지 않는지.

`data:` 접두(v2.124.50)와 WEBP(2026-07-30 실측: 모델이 "이미지가 제공되지 않았습니다")는
둘 다 **200 + 그럴듯한 오답**을 만든다. 서버가 받아서 맞춰준다.
"""
import base64
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.images import (normalize_image, normalize_images, sniff_format,   # noqa: E402
                         strip_data_uri)

PIL = pytest.importorskip("PIL")
from PIL import Image                                                        # noqa: E402


def _b64(fmt, size=(8, 8), color=(200, 10, 10)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def test_sniff_format_without_decoding():
    assert sniff_format(_b64("PNG")) == "png"
    assert sniff_format(_b64("JPEG")) == "jpeg"
    assert sniff_format(_b64("WEBP")) == "webp"
    assert sniff_format(_b64("GIF")) == "gif"
    assert sniff_format("") is None and sniff_format(None) is None


def test_strip_data_uri():
    assert strip_data_uri("data:image/png;base64,AAAA") == "AAAA"
    assert strip_data_uri("AAAA") == "AAAA"
    assert strip_data_uri("  data:image/webp;base64,BBBB  ") == "BBBB"


def test_webp_is_transcoded_to_png():
    """Ollama가 WEBP를 못 읽어 이미지를 버리던 것 → 서버가 PNG로 바꿔 보낸다."""
    out = normalize_image(_b64("WEBP"))
    assert sniff_format(out) == "png"
    with Image.open(io.BytesIO(base64.b64decode(out))) as im:
        assert im.size == (8, 8)                      # 내용 보존


def test_webp_with_data_uri_prefix():
    out = normalize_image("data:image/webp;base64," + _b64("WEBP"))
    assert sniff_format(out) == "png"


def test_supported_formats_untouched():
    """PNG·JPEG·GIF는 그대로 — 정상 경로에 변환 비용 0."""
    for fmt in ("PNG", "JPEG", "GIF"):
        src = _b64(fmt)
        assert normalize_image(src) == src


def test_empty_and_invalid_inputs():
    assert normalize_image("") is None
    assert normalize_image("   ") is None
    assert normalize_image(None) is None
    assert normalize_image(123) is None
    assert normalize_images("not a list") == []
    assert normalize_images([None, "", _b64("PNG")]) == [_b64("PNG")]


def test_corrupt_webp_falls_back_to_original():
    """변환 실패는 원본 전달(동작 후퇴 없음) — 예외로 요청을 죽이지 않는다."""
    broken = base64.b64encode(b"RIFF" + b"\x00" * 20).decode()
    assert sniff_format(broken) == "webp"
    assert normalize_image(broken) == broken


def test_oversized_pixels_skipped(monkeypatch):
    """디컴프레션 폭탄 방어 — 픽셀 상한 초과는 변환하지 않는다."""
    import core.images as ci
    monkeypatch.setattr(ci, "_MAX_PIXELS", 10)
    src = _b64("WEBP", size=(16, 16))
    assert ci.normalize_image(src) == src              # 변환 생략·원본 유지
