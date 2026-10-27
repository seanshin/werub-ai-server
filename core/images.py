"""이미지 입력 정규화 — 호출자가 보낸 이미지를 업스트림이 읽을 수 있는 형태로.

호출자가 보낸 이미지가 **조용히 무시**되는 사고가 반복됐다(2026-07 그룹웨어 제보 계열):
`data:` 접두를 붙여 보내면 Ollama가 무시했고(v2.124.50), WEBP는 디코드가 안 돼 모델이
"이미지가 제공되지 않았습니다"라고 답했다(2026-07-30 실측). 둘 다 200 + 그럴듯한 오답이라
호출자 로그에 흔적이 남지 않는다 → 서버가 받아서 맞춰준다.
"""
import base64
import binascii
import io
import logging

logger = logging.getLogger("ollama-admin")

# base64 문자열 앞부분만 보고 포맷을 판별한다(디코드 없이). 변환이 필요한 포맷만
# 실제로 디코드하므로 PNG·JPEG 같은 정상 경로에는 비용이 0이다.
_B64_MAGIC = (
    ("UklGR", "webp"),
    ("iVBOR", "png"),
    ("/9j/", "jpeg"),
    ("R0lGO", "gif"),
    ("Qk", "bmp"),
)

# Ollama(llama.cpp 계열)가 디코드하지 못해 조용히 버려지는 포맷 → PNG로 변환한다.
_NEEDS_TRANSCODE = {"webp"}

# 디컴프레션 폭탄 방어 — 본문 상한(10MB)을 통과한 WEBP도 픽셀로는 거대할 수 있다.
_MAX_PIXELS = 40_000_000


def sniff_format(b64: str):
    """raw base64 앞부분 → 포맷 문자열(모르면 None). 디코드하지 않는다."""
    if not isinstance(b64, str):
        return None
    head = b64.lstrip()[:8]
    for prefix, fmt in _B64_MAGIC:
        if head.startswith(prefix):
            return fmt
    return None


def strip_data_uri(value: str) -> str:
    """`data:image/png;base64,<payload>` → `<payload>`. 접두가 없으면 그대로."""
    v = (value or "").strip()
    if v.startswith("data:"):
        _, _, b64 = v.partition(",")
        return b64.strip()
    return v


def _transcode_to_png(b64: str) -> str:
    """WEBP 등 → PNG base64. 실패하면 원본을 그대로 돌려준다(동작 후퇴 없음)."""
    try:
        raw = base64.b64decode(b64, validate=False)
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
            if w * h > _MAX_PIXELS:
                logger.warning("이미지 변환 생략: %dx%d 픽셀이 상한 초과", w, h)
                return b64
            im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except (binascii.Error, ValueError, OSError, ImportError) as e:
        logger.warning("이미지 변환 실패(%s) — 원본 전달", type(e).__name__)
        return b64


def normalize_image(value):
    """호출자 이미지 1건 → 업스트림이 읽는 raw base64. 빈 값이면 None.

    ①`data:` 접두 제거 ②업스트림이 못 읽는 포맷(WEBP)은 PNG로 변환.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    b64 = strip_data_uri(value)
    if not b64:
        return None
    if sniff_format(b64) in _NEEDS_TRANSCODE:
        b64 = _transcode_to_png(b64)
    return b64


def normalize_images(images):
    """이미지 목록 정규화 — 비목록·빈 항목은 버린다."""
    if not isinstance(images, (list, tuple)):
        return []
    out = []
    for item in images:
        norm = normalize_image(item)
        if norm:
            out.append(norm)
    return out
