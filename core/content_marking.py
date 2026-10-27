"""AI 생성물 표시(마킹) — 가시 워터마크 + 기계판독 메타데이터.

근거: 「인공지능 기본법」 §31 투명성 확보 의무 (생성형 AI 산출물 표시) +
정보통신망법 개정(게시·플랫폼 단계 AI 생성물 표시).

설계 원칙:
- 외부 의존 0 (Pillow만). C2PA 서명은 별도(선택) 모듈.
- 순수 함수 — Flask/네트워크 무관, 단위테스트 용이.
- idempotent: 이미 우리 마킹이 있으면 가시 워터마크 재합성 안 함(이중 표시 방지).
- 실패해도 원본 바이트 보존(마킹은 best-effort, 파이프라인 중단 금지).
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import io
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("ollama-admin")

# 메타데이터 키 (PNG iTXt / EXIF UserComment 공통)
META_FLAG = "AI-Generated"          # "true"
META_GENERATOR = "AI-Generator"     # 예: "WeRU.B AI (ComfyUI)"
META_MODEL = "AI-Model"             # 체크포인트/모델명
META_CREATED = "AI-Created"         # ISO8601 UTC
META_SPEC = "AI-Marking-Spec"       # 법적 근거 문구

_SPEC_TEXT = "KR AI Basic Act §31 / Net Act — AI-generated content"
_DEFAULT_LABEL = "AI 생성 · AI-generated"
_DEFAULT_LABEL_ASCII = "AI-generated"

# 한글 글리프 지원 폰트 후보 (GPU=리눅스, iMac=mac)
_KR_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/AppleGothic.ttf",
    "/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf",
]
# ASCII 폴백 폰트 후보
_ASCII_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

_VALID_POS = {"br", "bl", "tr", "tl", "bottom"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_font(size: int, want_korean: bool):
    """(font, supports_korean) 반환. 폰트 없으면 (PIL 기본폰트, False)."""
    from PIL import ImageFont
    if want_korean:
        for p in _KR_FONT_CANDIDATES:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size), True
                except Exception:
                    continue
    for p in _ASCII_FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size), False
            except Exception:
                continue
    try:
        return ImageFont.load_default(), False
    except Exception:
        return None, False


def read_marking(data: bytes) -> dict:
    """이미지 바이트에서 우리 AI 마킹(메타데이터) 판독.

    반환: {"marked": bool, "flag": .., "generator": .., "model": ..,
           "created": .., "spec": .., "source": "png-text|exif|none"}
    """
    out = {"marked": False, "source": "none"}
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            # PNG text chunks
            text = {}
            if hasattr(im, "text") and im.text:
                text.update(im.text)
            # info 에도 들어올 수 있음
            for k in (META_FLAG, META_GENERATOR, META_MODEL, META_CREATED, META_SPEC):
                if k in im.info and k not in text:
                    text[k] = im.info[k]
            if text.get(META_FLAG, "").lower() == "true":
                return {
                    "marked": True, "source": "png-text",
                    "flag": "true",
                    "generator": text.get(META_GENERATOR),
                    "model": text.get(META_MODEL),
                    "created": text.get(META_CREATED),
                    "spec": text.get(META_SPEC),
                }
            # EXIF UserComment (JPEG)
            try:
                exif = im.getexif()
                # 0x9286 = UserComment, 0x010e = ImageDescription
                desc = exif.get(0x010E) or ""
                if isinstance(desc, bytes):
                    desc = desc.decode("utf-8", "ignore")
                if META_FLAG in str(desc):
                    return {"marked": True, "source": "exif", "flag": "true",
                            "generator": None, "model": None,
                            "created": None, "spec": str(desc)}
            except Exception:
                pass
    except Exception as e:
        logger.debug("read_marking failed: %s", e)
    return out


# 타 생성기/툴의 AI 메타데이터 신호 (PNG text chunk 키 또는 소프트웨어명)
_AI_METADATA_KEYS = ("parameters", "prompt", "workflow", "sd-metadata",
                     "Comment", "generation_data", "dream")
_AI_SOFTWARE_HINTS = ("stable diffusion", "automatic1111", "comfyui", "midjourney",
                      "dall-e", "dalle", "imagen", "firefly", "novelai",
                      "invokeai", "fooocus", "flux", "sdxl", "leonardo")
# C2PA / 출처표준 바이트 시그니처 (라이브러리 없이 휴리스틱)
_C2PA_SIGS = (b"c2pa", b"jumbf", b"urn:c2pa", b"contentauth", b"caBX")


def detect_provenance(data: bytes) -> dict:
    """외부(타 생성기) AI 출처 신호를 휴리스틱으로 판독.

    우리 마킹(read_marking)과 별개로, C2PA/SynthD류 표준·타 툴 메타데이터를 탐지.
    반환: {"c2pa": bool, "ai_metadata": [키...], "software": str|None, "hints": [...]}
    주의: 휴리스틱(확정 아님). 메타 제거 시 미탐지. SynthID 워터마크는 비공개라 미탐지.
    """
    out = {"c2pa": False, "ai_metadata": [], "software": None, "hints": []}
    if not data:
        return out
    # 1) C2PA 바이트 시그니처 (앞/뒤 일부만 스캔 — 비용 절감)
    head = data[:200_000]
    tail = data[-100_000:] if len(data) > 100_000 else b""
    blob = head + tail
    low = blob.lower()
    for sig in _C2PA_SIGS:
        if sig.lower() in low:
            out["c2pa"] = True
            out["hints"].append(f"c2pa-sig:{sig.decode('ascii','ignore')}")
            break
    # 2) 이미지 메타데이터 (PNG text / EXIF Software)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            text = {}
            if hasattr(im, "text") and im.text:
                text.update(im.text)
            for k, v in list(im.info.items()):
                if isinstance(v, str):
                    text.setdefault(k, v)
            for k in text:
                if k in _AI_METADATA_KEYS:
                    out["ai_metadata"].append(k)
            # 소프트웨어 힌트 (값 전체에서 탐색)
            joined = " ".join(str(v) for v in text.values()).lower()
            try:
                exif = im.getexif()
                sw = exif.get(0x0131)  # Software
                if sw:
                    joined += " " + str(sw).lower()
                    out["software"] = str(sw)
            except Exception:
                pass
            for hint in _AI_SOFTWARE_HINTS:
                if hint in joined:
                    out["hints"].append(f"software:{hint}")
                    if not out["software"]:
                        out["software"] = hint
    except Exception as e:
        logger.debug("detect_provenance image parse failed: %s", e)
    return out


def _draw_watermark(im, label: str, position: str, opacity: float,
                    font_scale: float = 0.022):
    """RGBA 합성으로 반투명 라벨 박스를 그린다. 새 RGB 이미지 반환.

    font_scale: 폰트 크기 = min(W,H) * font_scale (기본 0.022).
    세로로 긴 이미지(웹툰 등)에서 높이 기준이면 과대해지므로 짧은 변 기준으로 산정하고,
    라벨이 이미지 폭을 지배하지 않도록 폭 기준 상한도 적용한다.
    """
    from PIL import Image, ImageDraw

    if im.mode != "RGBA":
        base = im.convert("RGBA")
    else:
        base = im.copy()
    W, H = base.size

    # 폰트 크기: 짧은 변 비례 (최소 12px) + 폭 기준 상한 (라벨이 폭을 지배하지 않도록)
    try:
        scale = float(font_scale)
    except (TypeError, ValueError):
        scale = 0.022
    scale = max(0.005, min(0.08, scale))
    font_size = max(12, int(min(W, H) * scale))
    font_size = min(font_size, max(12, W // 16))
    want_kr = any(ord(c) > 0x2000 for c in label)  # 한글/기호 포함 여부
    font, supports_kr = _resolve_font(font_size, want_kr)
    if want_kr and not supports_kr:
        label = _DEFAULT_LABEL_ASCII  # 한글 폰트 없으면 ASCII 폴백

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 텍스트 박스 크기 측정
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = len(label) * font_size // 2, font_size
    pad = max(6, font_size // 3)
    bw, bh = tw + pad * 2, th + pad * 2

    margin = max(8, int(min(W, H) * 0.02))
    if position == "bl":
        x, y = margin, H - bh - margin
    elif position == "tr":
        x, y = W - bw - margin, margin
    elif position == "tl":
        x, y = margin, margin
    elif position == "bottom":
        x, y = (W - bw) // 2, H - bh - margin
    else:  # br (default)
        x, y = W - bw - margin, H - bh - margin

    a = max(0, min(255, int(255 * opacity)))
    # 반투명 검은 배경 + 흰 글자 (대비 확보)
    draw.rectangle([x, y, x + bw, y + bh], fill=(0, 0, 0, int(a * 0.55)))
    tx, ty = x + pad, y + pad
    try:
        # 가독성 위해 얇은 외곽선
        draw.text((tx, ty), label, font=font, fill=(255, 255, 255, a),
                  stroke_width=max(1, font_size // 14), stroke_fill=(0, 0, 0, a))
    except TypeError:
        draw.text((tx, ty), label, font=font, fill=(255, 255, 255, a))

    composited = Image.alpha_composite(base, overlay)
    return composited.convert("RGB")


def mark_image_bytes(
    data: bytes,
    *,
    generator: str = "WeRU.B AI",
    model: str | None = None,
    label: str | None = None,
    position: str = "br",
    opacity: float = 0.85,
    font_scale: float = 0.022,
    visible: bool = True,
    metadata: bool = True,
    force: bool = False,
) -> bytes:
    """이미지 바이트에 가시 워터마크 + 메타데이터 마킹을 적용해 PNG 바이트 반환.

    - 이미 마킹돼 있으면(force=False) 원본 그대로 반환(idempotent).
    - 실패 시 원본 바이트 반환(파이프라인 보호).
    """
    if not data:
        return data
    try:
        from PIL import Image, PngImagePlugin
    except Exception as e:
        logger.warning("Pillow unavailable, skip marking: %s", e)
        return data

    if not force:
        existing = read_marking(data)
        if existing.get("marked"):
            return data

    if position not in _VALID_POS:
        position = "br"
    label = label or _DEFAULT_LABEL

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            work = im
            if visible:
                work = _draw_watermark(im, label, position, opacity, font_scale)
            elif work.mode not in ("RGB", "RGBA", "L"):
                work = work.convert("RGB")

            pnginfo = None
            if metadata:
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_itxt(META_FLAG, "true")
                pnginfo.add_itxt(META_GENERATOR, str(generator))
                if model:
                    pnginfo.add_itxt(META_MODEL, str(model))
                pnginfo.add_itxt(META_CREATED, _now_iso())
                pnginfo.add_itxt(META_SPEC, _SPEC_TEXT)

            buf = io.BytesIO()
            work.save(buf, format="PNG", pnginfo=pnginfo)
            return buf.getvalue()
    except Exception as e:
        logger.warning("mark_image_bytes failed, returning original: %s", e)
        return data


def marking_metadata(generator: str = "WeRU.B AI", model: str | None = None) -> dict:
    """S3 객체 메타데이터로 함께 저장할 dict (기계판독 보조)."""
    md = {META_FLAG: "true", META_GENERATOR: str(generator), META_CREATED: _now_iso()}
    if model:
        md[META_MODEL] = str(model)
    return md
