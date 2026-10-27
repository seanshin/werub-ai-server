"""Vision API Blueprint — 이미지 분석/설명 생성 + 의료 영상 사전 검증"""

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

# ---------------------------------------------------------------------------
# 설계 메모 (비전)
#
# - 한국어 강제(force_korean)는 **필터**다. 영문 우세 출력(영문 명함·수치 OCR)을 통째로
#   지워 200 + 빈 description 이 나갔던 사고가 있다. _keep_content 가 그 방어다 —
#   **한국어화보다 내용 보존이 우선**이며, 결과가 비거나 기호만 남으면 원문을 돌려준다.
# - thinking 계열 비전 모델(qwen3-vl 등)은 사고 토큰을 `thinking` 필드로 내보내므로
#   num_predict 가 모자라면 `response` 가 빈 채 done_reason=length 로 끝난다.
#   **모델 비호환으로 오진하기 쉬운 지점**이다.
#
# 상세: docs/DEVELOPER_GUIDE.md §1-5, §1-6
# ---------------------------------------------------------------------------
import base64
import io
import json as _json
import logging
import re
import requests
import time as _time
from flask import Blueprint, request, jsonify, Response

from core.korean import force_korean
from core.config import OLLAMA_BASE as _OLLAMA_BASE
from core import model_router  # v2.125.13 D1 이관 — Ollama 직접 호출 10곳을 task 라우터로

logger = logging.getLogger("ollama-admin")

# 한국어 보장 헬퍼 — force_korean은 '필터'(한글 없는 라인 삭제)라 영어중심 출력엔 부적합.
# 영어 우세 판독(LLaVA-Med 등)은 translate_core(의료 도메인)로 '번역'해 내용 보존.
_KOREAN_CH = re.compile("[가-힯]")
_ASCII_LETTER = re.compile(r"[A-Za-z]")


# 모델 특수 토큰 — 파트너 응답에 그대로 실려 나가면 UI에 노출된다.
# 실측(2026-08-04, 트윈 영상 판독 경로): MedGemma 1.5가 `recommendations` 끝에
# `<start_of_image>`를 붙여 내보냈다. gemma 계열 이미지 플레이스홀더가 생성 출력으로
# 새어 나온 것으로, 모델이 바뀌어도 같은 클래스가 재발할 수 있어 목록으로 막는다.
_SPECIAL_TOKEN_RE = re.compile(
    r"<\s*/?\s*(?:start_of_image|end_of_image|image|start_of_turn|end_of_turn|"
    r"start_of_audio|end_of_audio|bos|eos|pad|unk|s|/s)\s*>", re.IGNORECASE)


def _strip_special_tokens(text):
    """모델 특수 토큰 제거(순수). 제거 후 남는 공백·빈 줄도 정리한다."""
    if not text:
        return text
    out = _SPECIAL_TOKEN_RE.sub("", text)
    out = re.sub(r"[ \t]+\n", "\n", out)      # 줄 끝 공백
    out = re.sub(r"\n{3,}", "\n\n", out)      # 과다 빈 줄
    return out.strip()


# 진료과 명칭 교정 — **판독 소견이 아니라 안내 문구의 오류**라 결정적으로 고칠 수 있다.
# 실측(2026-08-04, N=6): MedGemma가 면책 문장에서 "전문 **방사선 종양학과** 진료사의 해석을
# 대체하지 않습니다"라고 **2/6(33%)** 반복했다. 방사선종양학과는 암 방사선 **치료** 진료과이고,
# 영상 판독은 **영상의학과(Radiology)** 소관이다 — 환자·의료진을 다른 과로 보내는 안내다.
# ⚠️ 같은 측정에서 "비용탈구"(늑막삼출 오역) 등은 **0/6으로 재현되지 않아 넣지 않았다.**
#    1회 관측으로 사전을 만들면 존재하지 않는 문제를 고치게 된다.
# 판독 소견의 의학 용어는 건드리지 않는다 — 진료과 명칭만 대상이다.
_DEPT_FIXES = (
    (re.compile(r"방사선\s*종양\s*학?과"), "영상의학과"),
    (re.compile(r"방사선\s*과(?!목)"), "영상의학과"),
)


def _fix_department_terms(text):
    """영상 판독 안내 문구의 진료과 명칭 교정(순수)."""
    if not text:
        return text
    for rx, right in _DEPT_FIXES:
        text = rx.sub(right, text)
    return text


def _ensure_korean(text, language):
    """language=ko일 때 의료 영상 출력을 한국어로 보장한다(P1 한글 네이티브 옵션).

    - 한국어 우세: force_korean으로 잔여 영문/CJK 토막 정리(기존 동작 유지)
    - 영어 우세(영어중심 의료 VLM): translate_core(의료 도메인)로 번역
      → 트윈이 별도 /imaging/translate 후처리 없이 한글 판독을 소비
    실패·예외 시 force_korean/원문 폴백 — 응답을 절대 깨지 않는다.
    """
    if language != "ko" or not text or not text.strip():
        return text
    kor = len(_KOREAN_CH.findall(text))
    eng = len(_ASCII_LETTER.findall(text))
    # 한국어가 충분하면(영문은 토막 수준) 필터 정리 — 기존 force_korean 경로
    if kor >= 20 or kor >= eng:
        return _keep_content(force_korean(text), text)
    # 영어 우세 → 의료 번역(필터처럼 삭제하지 않고 내용 보존)
    try:
        from routes.writing import translate_core  # 지연 import (순환참조 회피)
        out = translate_core(text, ["ko"], source="auto", domain="medical", style="literal")
        ko = ((out or {}).get("translation") or {}).get("ko", "").strip()
        if ko:
            return ko
    except Exception as e:
        logger.warning(f"[vision] ko translate fallback: {e}")
    return _keep_content(force_korean(text), text)


_INFO_CH = re.compile(r"[0-9A-Za-z\uac00-\ud7a3\u4e00-\u9fff]")


def _keep_content(filtered, original):
    """필터가 정보를 다 날리면 원문을 돌려준다 — **한국어화보다 내용 보존이 우선**이다.

    `force_korean`은 필터라 영문 우세 출력(영문 명함·수치 OCR 등)을 지운다. 결과가 빈
    문자열이면 200 + 빈 description이 나가 호출자는 "인식 실패"로 오인하고(그룹웨어 명함 OCR
    경로), 기호만 남으면(`"* *"`) 역시 정보가 0이다. 두 경우 모두 원문이 낫다.
    """
    if filtered and _INFO_CH.search(filtered):
        return filtered
    logger.warning("[vision] 한국어 필터가 정보를 모두 제거(%r) → 원문 유지(%d자)",
                   (filtered or "")[:20], len(original or ""))
    return original


# 허용 이미지 MIME 타입 + 최대 크기
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB

# 품질 감점 기준
_QUALITY_PENALTIES = {
    "LOW_RESOLUTION": 30,
    "OVER_COMPRESSED": 20,
    "BLURRY": 25,
    "UNDER_EXPOSED": 15,
    "OVER_EXPOSED": 15,
    # CROPPED·INVALID_COLORSPACE는 기획(P3-1 §품질 체크)만 있고 판정 로직 미구현 —
    # 데드 상수는 제거(X11·2026-08-26). 구현 시 issues.append 경로부터 추가할 것.
}

_CLASSIFICATION_PROMPT = """Analyze this image and respond ONLY with a JSON object (no other text):
{"is_medical": true/false, "modality": "xray"|"ct"|"mri"|"ultrasound"|"pathology"|"fundus"|"dermoscopy"|null, "body_part": "chest"|"abdomen"|"brain"|"spine"|"extremity"|"pelvis"|"thyroid"|"breast"|"retina"|"skin"|null, "confidence": 0.0-1.0}

Rules:
- is_medical=true only for clinical/diagnostic images (x-ray, CT slice, MRI slice, ultrasound, pathology slide, fundus photo, dermoscopy)
- is_medical=false for regular photos, screenshots, documents, diagrams, illustrations
- confidence reflects how certain you are about the classification"""


def _validate_image(file_key="image"):
    """이미지 파일 검증 + base64 변환. 실패 시 (None, error_response) 반환."""
    if file_key not in request.files:
        return None, (jsonify({"error": f"{file_key} file required", "code": "MISSING_IMAGE"}), 400)
    img_file = request.files[file_key]
    if not img_file.filename:
        return None, (jsonify({"error": "empty filename", "code": "EMPTY_FILENAME"}), 400)
    content_type = img_file.content_type or ""
    if content_type and content_type not in _ALLOWED_IMAGE_TYPES:
        return None, (jsonify({
            "error": f"unsupported image type: {content_type}",
            "code": "UNSUPPORTED_FORMAT",
            "supported": list(_ALLOWED_IMAGE_TYPES),
        }), 415)
    data = img_file.read()
    if len(data) > _MAX_IMAGE_SIZE:
        return None, (jsonify({
            "error": f"image too large: {len(data)/(1024*1024):.1f}MB (max {_MAX_IMAGE_SIZE//(1024*1024)}MB)",
            "code": "IMAGE_TOO_LARGE",
        }), 413)
    if len(data) == 0:
        return None, (jsonify({"error": "empty image file", "code": "EMPTY_IMAGE"}), 400)
    return base64.b64encode(data).decode("utf-8"), None


# ---------------------------------------------------------------------------
# Precheck — 이미지 품질 검사 + AI 분류
# ---------------------------------------------------------------------------

def _check_image_quality(img_b64):
    """이미지 품질 평가 — PIL 기반, GPU 불필요"""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return {"score": 100, "issues": [], "resolution": "unknown",
                "resolution_ok": True, "exposure_ok": True, "sharpness_ok": True}

    raw = base64.b64decode(img_b64)
    img = Image.open(io.BytesIO(raw))
    w, h = img.size
    issues = []

    # 해상도
    if w < 256 or h < 256:
        issues.append("LOW_RESOLUTION")

    # 파일 크기 (과도한 압축)
    if len(raw) < 10240:
        issues.append("OVER_COMPRESSED")

    # 선명도 — Laplacian variance (numpy only, no scipy)
    gray = np.array(img.convert("L"), dtype=np.float64)
    # Laplacian 근사: d²/dx² + d²/dy² via second differences
    lap_y = gray[2:, :] - 2 * gray[1:-1, :] + gray[:-2, :]
    lap_x = gray[:, 2:] - 2 * gray[:, 1:-1] + gray[:, :-2]
    lap_var = (lap_y[:, 1:-1].var() + lap_x[1:-1, :].var()) / 2
    sharpness_ok = lap_var >= 100
    if not sharpness_ok:
        issues.append("BLURRY")

    # 노출
    mean_brightness = gray.mean()
    exposure_ok = True
    if mean_brightness < 30:
        issues.append("UNDER_EXPOSED")
        exposure_ok = False
    elif mean_brightness > 225:
        issues.append("OVER_EXPOSED")
        exposure_ok = False

    score = max(0, 100 - sum(_QUALITY_PENALTIES.get(i, 10) for i in issues))

    return {
        "score": int(score),
        "issues": issues,
        "resolution": f"{w}x{h}",
        "resolution_ok": bool(w >= 256 and h >= 256),
        "exposure_ok": bool(exposure_ok),
        "sharpness_ok": bool(sharpness_ok),
    }


def _classify_medical_image(img_b64):
    """MedGemma로 의료 영상 분류"""
    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py)
    res, exc = _router_generate("vision.medical_classify", {
        "model": MEDICAL_VISION_MODEL,
        "prompt": _CLASSIFICATION_PROMPT,
        "images": [img_b64],
        "stream": False,
        "keep_alive": "10m",  # precheck 후 analyze까지 VRAM 유지
        "options": {"num_predict": 100, "temperature": 0.1},
    }, timeout=30)
    if exc is not None:
        logger.warning("precheck classification failed: %s", exc)
        return {"is_medical": None, "modality": None, "body_part": None,
                "modality_confidence": 0, "body_part_confidence": 0, "error": str(exc)}

    if not res.ok or res.status != 200:
        return {"is_medical": None, "modality": None, "body_part": None,
                "modality_confidence": 0, "body_part_confidence": 0,
                "error": f"Ollama HTTP {res.status}"}

    text = res.raw.get("response", "")
    # JSON 추출
    match = re.search(r"\{[^}]+\}", text)
    if not match:
        return {"is_medical": None, "modality": None, "body_part": None,
                "modality_confidence": 0, "body_part_confidence": 0,
                "raw": text[:200]}

    try:
        d = _json.loads(match.group())
    except _json.JSONDecodeError:
        return {"is_medical": None, "modality": None, "body_part": None,
                "modality_confidence": 0, "body_part_confidence": 0,
                "raw": text[:200]}

    conf = float(d.get("confidence", 0.5))
    return {
        "is_medical": bool(d.get("is_medical", False)),
        "modality": d.get("modality"),
        "modality_confidence": conf,
        "body_part": d.get("body_part"),
        "body_part_confidence": conf,
    }


def _run_precheck(img_b64, expected_modality=None):
    """품질 검사 + AI 분류 → 종합 판정"""
    import time
    start = time.time()

    quality = _check_image_quality(img_b64)
    classification = _classify_medical_image(img_b64)

    is_medical = classification.get("is_medical", False)
    suitable = (quality["score"] >= 50) and (is_medical is True)

    warnings = []
    rejection_reason = None

    if is_medical is False:
        rejection_reason = "NOT_MEDICAL_IMAGE"
        warnings.append("이미지가 의료 영상이 아닙니다.")
    elif is_medical is None:
        warnings.append("의료 영상 여부를 판단할 수 없습니다 (분류 모델 오류).")

    if quality["score"] < 50 and is_medical is not False:
        rejection_reason = rejection_reason or "LOW_QUALITY"
        for issue in quality["issues"]:
            msg = {
                "LOW_RESOLUTION": "이미지 해상도가 너무 낮습니다 (최소 256×256).",
                "OVER_COMPRESSED": "이미지가 과도하게 압축되었습니다.",
                "BLURRY": "이미지가 흐릿합니다. 선명한 영상을 사용해주세요.",
                "UNDER_EXPOSED": "이미지가 너무 어둡습니다.",
                "OVER_EXPOSED": "이미지가 너무 밝습니다 (과다 노출).",
            }.get(issue, issue)
            warnings.append(msg)

    if expected_modality and classification.get("modality"):
        if expected_modality != classification["modality"]:
            warnings.append(
                f"모달리티 불일치: 예상 {expected_modality}, 감지 {classification['modality']} — 영상 종류를 확인해주세요.")
            classification["expected_modality"] = expected_modality
            classification["modality_mismatch"] = True

    duration_ms = int((time.time() - start) * 1000)

    result = {
        "suitable": suitable,
        "confidence": classification.get("modality_confidence", 0),
        "classification": classification,
        "quality": quality,
        "warnings": warnings,
        "model": MEDICAL_VISION_MODEL,
        "duration_ms": duration_ms,
    }
    if rejection_reason:
        result["rejection_reason"] = rejection_reason
    return result


def _handle_ollama_error(e, endpoint="vision"):
    """Ollama 호출 에러를 상세 에러 응답으로 변환"""
    error_str = str(e)
    if isinstance(e, requests.exceptions.Timeout):
        logger.warning("%s: Ollama timeout", endpoint)
        return jsonify({
            "error": "model inference timeout — image may be too complex or model overloaded",
            "code": "TIMEOUT",
            "detail": error_str,
        }), 504
    if isinstance(e, requests.exceptions.ConnectionError):
        logger.error("%s: Ollama connection failed", endpoint)
        return jsonify({
            "error": "LLM engine not reachable — Ollama may be down",
            "code": "LLM_UNAVAILABLE",
            "detail": error_str,
        }), 503
    logger.error("%s error: %s", endpoint, error_str)
    return jsonify({
        "error": f"inference failed: {error_str}",
        "code": "INFERENCE_ERROR",
        "detail": error_str,
    }), 500


def _handle_ollama_response_error(res, model, endpoint="vision"):
    """Ollama HTTP 응답 에러 처리 (4xx/5xx) — 응답 코드·본문은 이관 전과 같다(404 MODEL_NOT_FOUND · 그 외 502 LLM_ERROR).

    v2.125.13 D1 이관: 인자가 `requests.Response`에서 `model_router.Result`로 바뀌었다. 어댑터는 4xx/5xx를
    `Result(ok=False, status=<HTTP>, error="HTTP <status>: <error 본문>")`로 돌려주므로 `detail`은 그 접두를 벗겨 복원한다.
    (⚠️ 어댑터가 `error` 본문을 120자에서 자르고 `error` 키 없는 본문은 빈 문자열로 준다 — 종전 200자·`r.text` 폴백과 이 한 점만 다르다.)
    """
    status = res.status
    if status is None or status < 400:
        return None
    if status == 404:
        return jsonify({
            "error": f"model '{model}' not found — run 'ollama pull {model}'",
            "code": "MODEL_NOT_FOUND",
            "model": model,
        }), 404
    detail = res.error or ""
    prefix = f"HTTP {status}"
    if detail.startswith(prefix):
        detail = detail[len(prefix):]
        detail = detail[2:] if detail.startswith(": ") else detail
    logger.error("%s: Ollama returned %d: %s", endpoint, status, detail)
    return jsonify({
        "error": f"LLM engine error (HTTP {status})",
        "code": "LLM_ERROR",
        "status_code": status,
        "detail": detail,
    }), 502


class _ExcSession:
    """`requests` 모듈을 그대로 쓰되, 어댑터가 `Result(ok=False)`로 삼키는 **예외 객체**를 붙잡아 둔다.

    `_handle_ollama_error`는 Timeout→504·ConnectionError→503을 예외 **타입**으로 가른다 — `Result.error`(문자열)로는
    복원할 수 없어 세션 층에서 잡아 둔다. URL·본문·타임아웃은 그대로 `requests.post`로 흘러간다(전송 변경 없음).
    """

    def __init__(self):
        self.exc = None

    def post(self, url, json=None, timeout=None, stream=False):
        try:
            return requests.post(url, json=json, timeout=timeout, stream=stream)
        except Exception as e:
            self.exc = e
            raise


def _router_generate(task, payload, timeout):
    """종전 직접 호출(`requests` → `/api/generate`, `json=payload`, `timeout=…`) → `model_router.generate`.

    `payload`는 종전 본문 그대로(`_vision_payload` 결과 또는 리터럴 dict) — 어댑터가 만드는 와이어 본문은 **키까지 같다**
    (회귀 tests/test_router_migration_vision.py). 반환 `(res, exc)`:
      - `exc`가 있으면 전송 예외(종전 `except Exception as e:` 분기) → `_handle_ollama_error(exc)`
      - 아니면 `res.status`로 4xx/5xx(`_handle_ollama_response_error`) · 성공이면 종전 `r.json()`은 `res.raw`
    """
    sess = _ExcSession()
    res = model_router.generate(
        task, payload["prompt"], model=payload["model"], system=payload.get("system"),
        options=payload.get("options"), images=payload.get("images"), think=payload.get("think"),
        keep_alive=payload.get("keep_alive"), timeout=timeout,
        adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=sess))
    exc = None
    if not res.ok and res.status is None:
        # 어댑터가 삼킨 예외 — 세션이 잡아 둔 원본(타입 보존). 없으면(응답 JSON 파싱 실패 등) 메시지로 복원.
        exc = sess.exc if sess.exc is not None else RuntimeError(res.error)
    return res, exc

vision_bp = Blueprint("vision", __name__)

# 일반 이미지 설명 모델 — v2.125.10에서 qwen3-vl:8b로 복귀(사전 등록 PoC 통과).
# ⚠️ 이력: v2.118.2가 "qwen3-vl:8b는 vision 응답이 빈 문자열(done_reason=length)"이라 minicpm-v로
# 바꿨는데, 빈 응답의 정체는 **비호환이 아니라 사고 예산**이었다 — 이 모델은 `think:false`·`/no_think`를
# 무시하고 항상 사고(300~3,000자)를 먼저 쓰며 그 토큰이 `num_predict`에서 빠진다. 300으로 부르면
# 사고만 쓰다 잘려 본문이 0자, 2048이면 3과제 모두 정상(2026-08-19 실측 `scripts/poc_vision_qwen3vl_think.py`).
# 실측 우위: 한글 명함 OCR 4/5(minicpm-v는 이름·직함·회사 **창작**) · 한글 문단 OCR 유사도 0.99(minicpm-v 0.19=프롬프트 반복)
# · 두 장 이미지 구분 유일. 폴백은 minicpm-v(빈 응답·오류 시). 사고형 모델 예산은 `_vision_payload`가 보정한다.
VISION_MODEL = "qwen3-vl:8b"
VISION_FALLBACK_MODEL = "minicpm-v:latest"
#: 사고형 비전 모델(qwen3-vl 계열)은 사고 토큰이 num_predict를 먹는다 → 최소 예산.
#: 2048→4096(v2.125.55): 기본 temperature 1이라 사고 길이가 확률적 — 2048에선 한글 명함 통제군이
#: 2/20 빈 응답(사고 6,884자에서 잘림)·실경로 페이로드로도 2,424토큰 회차 관측. 4096은 0/40·p95 10.1s.
#: 사전 등록·결과: docs/01-plan/references/vision-thinking-budget-2026-09-29.md
THINKING_VISION_MIN_PREDICT = 4096


def _is_thinking_vision(model: str) -> bool:
    return (model or "").startswith("qwen3-vl")


def _vision_payload(model, prompt, images, num_predict, **extra):
    """일반 비전 `/api/generate` 본문 — 사고형 모델이면 예산을 올리고 think=False를 함께 보낸다.

    think=False는 이 모델에서 **무시되지만** 존중하는 모델(qwen3-vl 30b 등 후속)에선 사고를 끈다 — 두 경우 모두
    본문이 잘리지 않게 예산은 항상 올린다. 실측: 300→사고만 쓰고 0자, 2048→대개 정상이나 꼬리에서 잘림(2/20), 4096→0/40.
    """
    payload = {"model": model, "prompt": prompt, "images": images, "stream": False,
               "options": {"num_predict": num_predict}, **extra}
    if _is_thinking_vision(model):
        payload["options"]["num_predict"] = max(num_predict, THINKING_VISION_MIN_PREDICT)
        payload["think"] = False
    return payload
# MedGemma 1.5 멀티모달(2026-01, gemma3 4.3B·128K·vision+text, mmproj 프로젝터 포함) — 영상 판독 1.5 업그레이드.
# 트윈 P1 게이트 해소(공식 멀티모달 공개). Q8_0=판독 정확도↑(병변 배제 구체화), 적재 6.7GB·14b 공존 ~15GB<16.3GB.
# (Q4_K_M=경량 4.9GB 폴백 옵션. 실 X-ray 비교서 Q8이 더 완결적 differential 산출 → Q8 채택.)
MEDICAL_VISION_MODEL = "dcarrascosa/medgemma-1.5-4b-it:Q8_0"
MEDICAL_VISION_FALLBACK = "dcarrascosa/medgemma-1.5-4b-it:Q4_K_M"  # 경량 폴백(VRAM 압박 시)

MEDICAL_IMAGING_SYSTEM = """You are a medical imaging analysis AI assistant.
Analyze the provided medical image and generate a structured report.
Always respond in the requested language.
Include: findings, impression, and recommendations.
IMPORTANT: This is for educational/reference purposes only. Not a substitute for professional radiologist interpretation."""


@vision_bp.route("/api/vision/medical/precheck", methods=["POST"])
def api_vision_medical_precheck():
    """의료 영상 사전 검증 — 품질 + 의료 영상 분류
    ---
    tags: [Vision, Medical Imaging]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
        description: "검증할 이미지 파일"
      - name: expected_modality
        in: formData
        type: string
        required: false
        description: "예상 모달리티 (불일치 검출용: xray, ct, mri, ultrasound, pathology, fundus, dermoscopy)"
    responses:
      200:
        description: 검증 결과 (suitable=true/false)
        schema:
          type: object
          properties:
            suitable: {type: boolean}
            confidence: {type: number}
            classification: {type: object}
            quality: {type: object}
            warnings: {type: array, items: {type: string}}
    """
    img_data, err = _validate_image("image")
    if err:
        return err

    expected_modality = request.form.get("expected_modality")
    result = _run_precheck(img_data, expected_modality)
    return jsonify(result)


@vision_bp.route("/api/vision/describe", methods=["POST"])
def api_vision_describe():
    """이미지 설명 생성 (alt text, 측션)
    ---
    tags: [Vision]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
        description: "이미지 파일 (jpg/png/webp)"
      - name: prompt
        in: formData
        type: string
        required: false
      - name: language
        in: formData
        type: string
        required: false
    responses:
      200:
        schema:
          type: object
          properties:
            description: {type: string}
            model: {type: string}
    """
    img_data, err = _validate_image("image")
    if err:
        return err

    prompt = request.form.get("prompt", "")
    language = request.form.get("language", "ko")

    if not prompt:
        if language == "ko":
            prompt = "이 이미지를 한국어로 자세하게 설명해주세요. 웹 접근성을 위한 alt text로 사용할 수 있게 작성해주세요."
        else:
            prompt = "Describe this image in detail. Write it as an alt text for web accessibility."

    # 호출자가 모델을 고를 수 있다 — 실측상 한글 인명·상호 OCR은 qwen3-vl:8b가 정확하고
    # 기본 minicpm-v는 오독한다(그룹웨어 실측: 이수경→이성奀). 미지정 시 기존 기본값.
    model = (request.form.get("model") or "").strip() or VISION_MODEL
    # 사고형 모델은 로드 포함 최대 40초대 실측(캐릭터 그림 36~42초) → 타임아웃을 그에 맞춘다.
    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py). 예외→폴백→에러 계약 그대로.
    res, exc = _router_generate("vision.describe", _vision_payload(model, prompt, [img_data], 500), timeout=180)
    if exc is not None:
        # 폴백 시도
        if VISION_FALLBACK_MODEL and model != VISION_FALLBACK_MODEL:
            model = VISION_FALLBACK_MODEL
            res, exc2 = _router_generate("vision.describe", _vision_payload(model, prompt, [img_data], 200), timeout=90)
            if exc2 is not None:
                return _handle_ollama_error(exc2, "vision/describe")
        else:
            return _handle_ollama_error(exc, "vision/describe")

    err_resp = _handle_ollama_response_error(res, model, "vision/describe")
    if err_resp and model == VISION_MODEL and VISION_FALLBACK_MODEL:
        # 기본 모델 실패 → 폴백
        model = VISION_FALLBACK_MODEL
        res, exc = _router_generate("vision.describe", _vision_payload(model, prompt, [img_data], 200), timeout=90)
        if exc is not None:
            return _handle_ollama_error(exc, "vision/describe")
        err_resp = _handle_ollama_response_error(res, model, "vision/describe")
        if err_resp:
            return err_resp
    elif err_resp:
        return err_resp

    result = res.raw
    desc = result.get("response", "")
    if not desc.strip() and model == VISION_MODEL and _is_thinking_vision(model):
        # 사고 폭주 꼬리는 호출마다 **독립**이다 — 같은 모델 1회 재시도가 폴백(minicpm: 한글 OCR 창작)보다 낫다.
        # 사전 등록 (3)(docs/01-plan/references/vision-thinking-budget-2026-09-29.md §9): 대리 조건 예산 1024에서
        # 첫 호출 빈 본문 12/80 → 재시도 빈 본문 2/12(p2 0.17). 예산 상향·temperature로는 꼬리가 남았다(§6·§8).
        logger.warning("vision/describe empty response from %s (done_reason=%s, thinking=%d자) → 같은 모델 1회 재시도",
                       model, result.get("done_reason"), len(result.get("thinking") or ""))
        r2, exc2 = _router_generate("vision.describe", _vision_payload(model, prompt, [img_data], 500), timeout=180)
        if exc2 is None and not _handle_ollama_response_error(r2, model, "vision/describe"):
            desc2 = (r2.raw or {}).get("response", "")
            logger.info("vision/describe 재시도 %s (thinking=%d자)", "성공" if desc2.strip() else "도 빈 본문",
                        len((r2.raw or {}).get("thinking") or ""))
            if desc2.strip():
                result, desc = r2.raw, desc2
        else:
            logger.warning("vision/describe 재시도 실패(%s) → 폴백", exc2 or "응답 오류")
    if not desc.strip() and model == VISION_MODEL and VISION_FALLBACK_MODEL:
        # 200이지만 빈 응답(재시도까지) — 폴백으로 한 번 더.
        logger.warning("vision/describe empty response from %s (done_reason=%s, thinking=%d자) → 폴백 %s",
                       model, result.get("done_reason"), len(result.get("thinking") or ""),
                       VISION_FALLBACK_MODEL)
        model = VISION_FALLBACK_MODEL
        res, exc = _router_generate("vision.describe", _vision_payload(model, prompt, [img_data], 200), timeout=90)
        if exc is not None:
            return _handle_ollama_error(exc, "vision/describe")
        err_resp = _handle_ollama_response_error(res, model, "vision/describe")
        if err_resp:
            return err_resp
        result = res.raw
        desc = result.get("response", "")
    if not desc.strip():
        # 폴백까지 비었다. 무성 실패 방지로 명시 반환(v2.118.2 규칙 유지).
        logger.warning("vision/describe empty response from %s (done_reason=%s)",
                       model, result.get("done_reason"))
        return jsonify({"error": f"vision model '{model}' returned empty response",
                        "model": model}), 502
    desc = _ensure_korean(desc, language)
    return jsonify({"description": desc.strip(), "model": model})


@vision_bp.route("/api/vision/medical/analyze", methods=["POST"])
def api_vision_medical_analyze():
    """의료 영상 분석 (MedGemma)
    ---
    tags: [Vision, Medical Imaging]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
        description: "의료 영상 파일 (DICOM export jpg/png)"
      - name: modality
        in: formData
        type: string
        required: false
        description: "영상 종류 (xray, ct, mri, ultrasound, pathology, fundus, dermoscopy)"
      - name: body_part
        in: formData
        type: string
        required: false
        description: "촬영 부위 (chest, abdomen, brain, spine, extremity 등)"
      - name: clinical_info
        in: formData
        type: string
        required: false
        description: "임상 정보 (증상, 병력 등)"
      - name: language
        in: formData
        type: string
        required: false
        description: "응답 언어 (ko/en, 기본: ko)"
      - name: precheck
        in: formData
        type: string
        required: false
        description: "사전 검증 모드 (off: 기본, warn: 경고 포함, strict: 부적합 시 거절)"
    responses:
      200:
        schema:
          type: object
          properties:
            findings: {type: string}
            impression: {type: string}
            recommendations: {type: string}
            model: {type: string}
            modality: {type: string}
            disclaimer: {type: string}
      422:
        description: "precheck=strict 모드에서 부적합 이미지"
    """
    img_data, err = _validate_image("image")
    if err:
        return err

    modality = request.form.get("modality", "unknown")
    body_part = request.form.get("body_part", "unknown")
    clinical_info = request.form.get("clinical_info", "")
    language = request.form.get("language", "ko")
    precheck_mode = request.form.get("precheck", "off")

    # Precheck 실행 (warn/strict)
    precheck_result = None
    if precheck_mode in ("warn", "strict"):
        precheck_result = _run_precheck(img_data, modality if modality != "unknown" else None)
        if precheck_mode == "strict" and not precheck_result.get("suitable"):
            return jsonify({
                "error": "image failed precheck — not suitable for medical analysis",
                "code": "PRECHECK_FAILED",
                "precheck": precheck_result,
            }), 422

    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."

    prompt = f"""{lang_instruction}

영상 종류: {modality}
촬영 부위: {body_part}
임상 정보: {clinical_info if clinical_info else '제공되지 않음'}

이 의료 영상을 분석하고 다음 형식으로 보고서를 작성하세요:

## 소견 (Findings)
영상에서 관찰되는 모든 소견을 체계적으로 기술하세요.

## 판독 소견 (Impression)
주요 진단 또는 감별 진단을 기술하세요.

## 권고 사항 (Recommendations)
추가 검사나 추적 관찰이 필요한 경우 권고하세요."""

    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py)
    res, exc = _router_generate("vision.medical_analyze", {
        "model": MEDICAL_VISION_MODEL,
        "prompt": prompt,
        "system": MEDICAL_IMAGING_SYSTEM,
        "images": [img_data],
        "stream": False,
        "options": {"num_predict": 1024, "temperature": 0.3},
    }, timeout=120)
    if exc is not None:
        return _handle_ollama_error(exc, "vision/medical/analyze")

    err_resp = _handle_ollama_response_error(res, MEDICAL_VISION_MODEL, "vision/medical/analyze")
    if err_resp:
        return err_resp

    result = res.raw
    response_text = _fix_department_terms(_strip_special_tokens(result.get("response", "")))
    if not response_text:
        return jsonify({
            "error": "model returned empty response",
            "code": "EMPTY_RESPONSE",
            "model": MEDICAL_VISION_MODEL,
        }), 502

    response_text = _ensure_korean(response_text, language)

    # Parse sections
    findings = ""
    impression = ""
    recommendations = ""
    current_section = ""
    for line in response_text.split("\n"):
        lower = line.lower().strip()
        if "소견" in lower and "판독" not in lower or "findings" in lower and "impression" not in lower:
            current_section = "findings"
            continue
        elif "판독" in lower or "impression" in lower:
            current_section = "impression"
            continue
        elif "권고" in lower or "recommendation" in lower:
            current_section = "recommendations"
            continue

        if current_section == "findings":
            findings += line + "\n"
        elif current_section == "impression":
            impression += line + "\n"
        elif current_section == "recommendations":
            recommendations += line + "\n"

    # Fallback if parsing fails
    if not findings and not impression:
        findings = response_text

    resp = {
        "findings": findings.strip(),
        "impression": impression.strip(),
        "recommendations": recommendations.strip(),
        "raw_response": response_text,
        "model": MEDICAL_VISION_MODEL,
        "modality": modality,
        "body_part": body_part,
        "tokens": result.get("eval_count", 0),
        "duration_ms": result.get("eval_duration", 0) // 1000000,
        "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."
    }
    if precheck_result:
        resp["precheck"] = precheck_result
    return jsonify(resp)


@vision_bp.route("/api/vision/medical/analyze/stream", methods=["POST"])
def api_vision_medical_analyze_stream():
    """의료 영상 분석 — 스트리밍 (SSE, 토큰 단위)
    ---
    tags: [Vision, Medical Imaging]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
      - name: modality
        in: formData
        type: string
        required: false
      - name: body_part
        in: formData
        type: string
        required: false
      - name: clinical_info
        in: formData
        type: string
        required: false
      - name: language
        in: formData
        type: string
        required: false
      - name: precheck
        in: formData
        type: string
        required: false
        description: "off/warn/strict"
    responses:
      200:
        description: "SSE stream (text/event-stream)"
    """
    img_data, err = _validate_image("image")
    if err:
        return err

    modality = request.form.get("modality", "unknown")
    body_part = request.form.get("body_part", "unknown")
    clinical_info = request.form.get("clinical_info", "")
    language = request.form.get("language", "ko")
    precheck_mode = request.form.get("precheck", "off")

    # Precheck
    if precheck_mode in ("warn", "strict"):
        precheck_result = _run_precheck(img_data, modality if modality != "unknown" else None)
        if precheck_mode == "strict" and not precheck_result.get("suitable"):
            return jsonify({
                "error": "image failed precheck",
                "code": "PRECHECK_FAILED",
                "precheck": precheck_result,
            }), 422

    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."
    prompt = f"""{lang_instruction}

영상 종류: {modality}
촬영 부위: {body_part}
임상 정보: {clinical_info if clinical_info else '제공되지 않음'}

이 의료 영상을 분석하고 다음 형식으로 보고서를 작성하세요:

## 소견 (Findings)
영상에서 관찰되는 모든 소견을 체계적으로 기술하세요.

## 판독 소견 (Impression)
주요 진단 또는 감별 진단을 기술하세요.

## 권고 사항 (Recommendations)
추가 검사나 추적 관찰이 필요한 경우 권고하세요."""

    def generate():
        start = _time.time()
        total_tokens = 0
        try:
            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py). SSE 이벤트 형식 그대로.
            # (어댑터 stream은 HTTP 4xx/5xx를 예외로 던진다 → 종전엔 조용히 빈 스트림으로 끝나던 것이 error 이벤트로 나온다)
            for chunk in model_router.stream(
                    "vision.medical_stream", prompt, model=MEDICAL_VISION_MODEL,
                    system=MEDICAL_IMAGING_SYSTEM, images=[img_data], keep_alive="10m",
                    options={"num_predict": 1024, "temperature": 0.3}, timeout=120,
                    adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=requests)):
                if chunk.text:
                    yield f"data: {_json.dumps({'type': 'token', 'token': chunk.text}, ensure_ascii=False)}\n\n"
                if chunk.done:
                    total_tokens = chunk.tokens
                    duration_ms = int((_time.time() - start) * 1000)
                    yield f"data: {_json.dumps({'type': 'done', 'model': MEDICAL_VISION_MODEL, 'modality': modality, 'body_part': body_part, 'tokens': total_tokens, 'duration_ms': duration_ms, 'disclaimer': 'AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요.'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@vision_bp.route("/api/vision/medical/models")
def api_vision_medical_models():
    """의료 영상 분석 모델 목록
    ---
    tags: [Vision, Medical Imaging]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    return jsonify({"models": [
        {"id": MEDICAL_VISION_MODEL, "name": "MedGemma 1.5 4B (multimodal)", "size": "4B",
         "developer": "Google DeepMind", "type": "medical-vision", "primary": True,
         "specialties": ["radiology", "dermatology", "pathology", "ophthalmology"],
         "modalities": ["xray", "ct", "mri", "ultrasound", "pathology", "fundus", "dermoscopy"],
         "description": "MedGemma 1.5 멀티모달(2026-01, gemma3·128K·vision+text) — 영상 판독 1.5 업그레이드"},
        {"id": MEDICAL_VISION_FALLBACK, "name": "MedGemma 4B (legacy)", "size": "4B",
         "developer": "Google DeepMind", "type": "medical-vision", "primary": False,
         "specialties": ["radiology", "dermatology", "pathology", "ophthalmology"],
         "modalities": ["xray", "ct", "mri", "ultrasound", "pathology", "fundus", "dermoscopy"],
         "description": "구 멀티모달(1.5 미가용 시 폴백)"},
    ]})


@vision_bp.route("/api/vision/medical/report", methods=["POST"])
def api_vision_medical_report():
    """의료 영상 비교 판독 (이전 영상 대비)
    ---
    tags: [Vision, Medical Imaging]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: current_image
        in: formData
        type: file
        required: true
      - name: previous_image
        in: formData
        type: file
        required: false
      - name: modality
        in: formData
        type: string
        required: false
      - name: body_part
        in: formData
        type: string
        required: false
      - name: clinical_info
        in: formData
        type: string
        required: false
      - name: language
        in: formData
        type: string
        required: false
    responses:
      200:
        schema: {type: object}
    """
    current_data, err = _validate_image("current_image")
    if err:
        return err

    images = [current_data]
    comparison = False

    if "previous_image" in request.files:
        prev_data, prev_err = _validate_image("previous_image")
        if prev_err:
            return prev_err
        images.append(prev_data)
        comparison = True

    modality = request.form.get("modality", "unknown")
    body_part = request.form.get("body_part", "unknown")
    clinical_info = request.form.get("clinical_info", "")
    language = request.form.get("language", "ko")

    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."

    if comparison:
        prompt = f"""{lang_instruction}

영상 종류: {modality} | 부위: {body_part}
임상 정보: {clinical_info if clinical_info else '없음'}

두 장의 의료 영상이 제공됩니다. 첫 번째는 현재 영상, 두 번째는 이전 영상입니다.

1. 현재 영상 소견
2. 이전 영상 대비 변화
3. 종합 판독 소견
4. 권고 사항"""
    else:
        prompt = f"""{lang_instruction}

영상 종류: {modality} | 부위: {body_part}
임상 정보: {clinical_info if clinical_info else '없음'}

의료 영상을 분석하고 구조화된 판독 보고서를 작성하세요.
소견, 판독 소견, 권고 사항을 포함하세요."""

    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py)
    res, exc = _router_generate("vision.medical_report", {
        "model": MEDICAL_VISION_MODEL,
        "prompt": prompt,
        "system": MEDICAL_IMAGING_SYSTEM,
        "images": images,
        "stream": False,
        "options": {"num_predict": 1500, "temperature": 0.3},
    }, timeout=180)
    if exc is not None:
        return _handle_ollama_error(exc, "vision/medical/report")

    err_resp = _handle_ollama_response_error(res, MEDICAL_VISION_MODEL, "vision/medical/report")
    if err_resp:
        return err_resp

    result = res.raw
    response_text = _fix_department_terms(_strip_special_tokens(result.get("response", "")))
    if not response_text:
        return jsonify({
            "error": "model returned empty response",
            "code": "EMPTY_RESPONSE",
            "model": MEDICAL_VISION_MODEL,
        }), 502

    response_text = _ensure_korean(response_text, language)

    return jsonify({
        "report": response_text,
        "comparison": comparison,
        "model": MEDICAL_VISION_MODEL,
        "modality": modality,
        "body_part": body_part,
        "tokens": result.get("eval_count", 0),
        "duration_ms": result.get("eval_duration", 0) // 1000000,
        "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."
    })


# ---------------------------------------------------------------------------
# Dual-Read API (v2.48.0) — MedGemma + LLaVA-Med
# ---------------------------------------------------------------------------

# v2.69.0: LLaVA-Med 삭제 → MedGemma:4b로 통합
LLAVA_MED_MODEL = "medgemma:4b"

@vision_bp.route("/api/vision/medical/dual-read", methods=["POST"])
def api_vision_dual_read():
    """Dual-read medical imaging — MedGemma (screening) + LLaVA-Med (detail)
    ---
    tags: [Vision, Medical Imaging]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
      - name: modality
        in: formData
        type: string
        description: "영상 종류 (xray, ct, mri, pathology 등)"
      - name: clinical_info
        in: formData
        type: string
      - name: language
        in: formData
        type: string
        default: ko
    responses:
      200:
        description: 이중 판독 결과
    """
    img_data, err = _validate_image("image")
    if err:
        return err

    modality = request.form.get("modality", "unknown")
    clinical_info = request.form.get("clinical_info", "")
    language = request.form.get("language", "ko")
    lang_instruction = "반드시 한국어로 답변하세요." if language == "ko" else f"Respond in {language}."

    prompt = f"""{lang_instruction}
영상 종류: {modality}
임상 정보: {clinical_info or '없음'}

이 의료 영상을 분석하세요:
1. 소견 (Findings)
2. 판독 소견 (Impression)
3. 권고 사항 (Recommendations)"""

    results = {}

    # 1차: MedGemma 4B (스크리닝)
    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py).
    # v2.125.15 무성 실패 일괄: HTTP 4xx/5xx도 전송 예외와 같은 error 형태로 — 종전엔
    # `{"error":…}` 본문을 `.get("response","")`로 읽어 findings ""·tokens 0으로 흘렀다.
    res1, exc1 = _router_generate("vision.dual_read_primary", {
        "model": MEDICAL_VISION_MODEL,
        "prompt": prompt, "system": MEDICAL_IMAGING_SYSTEM,
        "images": [img_data], "stream": False,
        "options": {"num_predict": 1024, "temperature": 0.3},
    }, timeout=120)
    if exc1 is None and res1.ok:
        d1 = res1.raw
        results["primary_read"] = {
            "model": MEDICAL_VISION_MODEL,
            "findings": d1.get("response", ""),
            "tokens": d1.get("eval_count", 0),
            "elapsed_ms": int(d1.get("total_duration", 0) / 1e6),
        }
    else:
        results["primary_read"] = {"model": MEDICAL_VISION_MODEL,
                                   "error": str(exc1) if exc1 is not None
                                   else (res1.error or "HTTP %s" % res1.status)}

    # 2차: LLaVA-Med (정밀 판독)
    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_vision.py)
    res2, exc2 = _router_generate("vision.dual_read_secondary", {
        "model": LLAVA_MED_MODEL,
        "prompt": prompt, "system": "You are a medical imaging specialist. Provide detailed findings.",
        "images": [img_data], "stream": False,
        "options": {"num_predict": 1024, "temperature": 0.3},
    }, timeout=120)
    if exc2 is None and res2.ok:
        d2 = res2.raw
        results["secondary_read"] = {
            "model": LLAVA_MED_MODEL,
            "findings": d2.get("response", ""),
            "tokens": d2.get("eval_count", 0),
            "elapsed_ms": int(d2.get("total_duration", 0) / 1e6),
        }
    else:
        results["secondary_read"] = {"model": LLAVA_MED_MODEL,
                                     "error": str(exc2) if exc2 is not None
                                     else (res2.error or "HTTP %s" % res2.status)}

    # 한국어 옵션: 양 판독 모두 한글 보장(영어중심 LLaVA-Med는 의료 번역) — 트윈 /imaging/translate 후처리 생략
    if language == "ko":
        for _k in ("primary_read", "secondary_read"):
            _rd = results.get(_k, {})
            if _rd.get("findings") and "error" not in _rd:
                _rd["findings"] = _ensure_korean(_rd["findings"], language)

    # 비교
    p = results.get("primary_read", {}).get("findings", "")
    s = results.get("secondary_read", {}).get("findings", "")
    has_both = bool(p) and bool(s) and "error" not in results.get("primary_read", {}) and "error" not in results.get("secondary_read", {})
    results["comparison"] = {
        "dual_read_complete": has_both,
        "disclaimer": "AI 보조 판독이며, 최종 판독은 영상의학과 전문의에게 의뢰하세요.",
    }

    return jsonify(results)


@vision_bp.route("/api/vision/models")
def api_vision_models():
    """비전 모델 목록
    ---
    tags: [Vision]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    return jsonify({"models": [
        {"id": "qwen3-vl:8b", "name": "Qwen3-VL 8B", "size": "8B",
         "type": "vision", "description": "기본 비전 모델 — 고품질 이미지 분석, OCR 32언어, 프롬프트 생성 (v2.52.0 기본)"},
        {"id": "minicpm-v:latest", "name": "MiniCPM-V 8B", "size": "8B",
         "type": "vision", "description": "한국어 비전 모델 — 1.8M 픽셀, 멀티이미지 (폴백용)"},
        {"id": "medgemma:4b", "name": "MedGemma 4B", "size": "4B",
         "type": "medical-vision", "description": "의료 영상 분석 — 방사선, 피부과, 병리, 안과 (Google DeepMind)"},
        {"id": LLAVA_MED_MODEL, "name": "LLaVA-Med 7B", "size": "7B",
         "type": "medical-vision", "description": "의료 VLM — 방사선, 병리 정밀 판독 (Q8 quantized)"},
        {"id": "minicpm-v:latest", "name": "MiniCPM-V 8B", "size": "8B",
         "type": "vision", "description": "1.8M 픽셀, 멀티이미지, 한국어 지원, GPT-4V 능가 (v2.48.0)"},
        {"id": "deepseek-ocr:latest", "name": "DeepSeek OCR 3B", "size": "3B",
         "type": "ocr", "description": "문서/이미지 OCR — 레이아웃+텍스트 추출, 마크다운 변환 (v2.48.0)"},
    ]})
