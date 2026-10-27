"""AI 생성 이미지 ML 판별 (B2/B3) — 오픈소스 화소 분류기.

메타데이터/마킹이 없는 임의 이미지를 화소 패턴으로 AI 추정.
모델: Organika/sdxl-detector (umm-maybe 개선판, transformers image-classification).

⚠️ 정확도 한계: 신규 생성기·압축·스크린샷·편집본에 취약하고 오탐/미탐 존재.
   결과는 '추정치(ai_probability)'이며 법적 판단의 단독 근거가 될 수 없음.
   확정 신호는 우리 마킹(content_marking.read_marking)이 우선.

외부 API 호출 0 — HF 오픈소스 모델을 로컬(자체 호스팅)에서 실행. [[feedback_no_external_api]]
지연: 최초 호출 시 모델 로드(~8s, 1회), 이후 추론 CPU ~0.1s.
"""
from __future__ import annotations

import io
import logging
import threading

logger = logging.getLogger("ollama-admin")

DEFAULT_MODEL = "Organika/sdxl-detector"
# AI(생성) 클래스로 간주할 라벨 토큰
_AI_LABEL_TOKENS = ("artificial", "ai", "fake", "generated", "synthetic", "machine")

_pipeline = None
_pipeline_model = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()
_load_failed = False


def _cap_torch_threads(n: int):
    """torch CPU 스레드 제한 — 모델 로드 시 코어수만큼 스레드 폭증 방지.
    추론이 ~0.1s로 짧아 다수 스레드 불필요. nlwp 베이스라인 교란(인시던트 진단)도 방지."""
    try:
        import torch
        torch.set_num_threads(max(1, int(n)))
    except Exception as e:
        logger.debug("set_num_threads failed: %s", e)


def _resolve_device(device: str):
    """device 문자열 → transformers pipeline device 인자.

    공용 core.device 모듈에 위임(cross-arch 단일 진실 공급원). [[device]]
    선호 device를 가용성 기준으로 정규화(cuda/mps 미가용 시 cpu 폴백)한 뒤
    transformers pipeline 인자(cuda→0, mps→"mps", cpu→-1)로 변환한다.
    """
    from core import device as device_util
    return device_util.pipeline_arg(device_util.resolve(device))


def _load(model: str, device: str, threads: int = 4):
    """transformers image-classification 파이프라인 로드 (lazy, 1회)."""
    global _pipeline, _pipeline_model, _load_failed
    if _pipeline is not None and _pipeline_model == model:
        return _pipeline
    with _load_lock:
        if _pipeline is not None and _pipeline_model == model:
            return _pipeline
        try:
            _cap_torch_threads(threads)
            from transformers import pipeline
            dev = _resolve_device(device)
            logger.info("Loading AI image detector: %s (device=%s, threads=%s)", model, device, threads)
            _pipeline = pipeline("image-classification", model=model, device=dev)
            _pipeline_model = model
            _load_failed = False
        except Exception as e:
            _load_failed = True
            logger.warning("AI detector load failed: %s", e)
            raise
    return _pipeline


def is_available() -> bool:
    """transformers/torch import 가능 여부 (모델 로드 전 가벼운 체크)."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:
        return False


def _ai_prob_from_scores(scores: list) -> tuple[float, str]:
    """[{label, score}] → (ai_probability, top_label). AI 라벨 score 합산."""
    ai = 0.0
    top_label = scores[0]["label"] if scores else ""
    for s in scores:
        lab = str(s.get("label", "")).lower()
        if any(tok in lab for tok in _AI_LABEL_TOKENS):
            ai += float(s.get("score", 0.0))
    return max(0.0, min(1.0, ai)), top_label


def classify(data: bytes, *, model: str = DEFAULT_MODEL, device: str = "cpu",
             threads: int = 4) -> dict:
    """이미지 바이트 → AI 생성 확률 추정.

    반환: {"available": bool, "ai_probability": float|None, "label": str,
           "model": str, "scores": [...], "error": str?}
    """
    if not data:
        return {"available": False, "ai_probability": None, "error": "empty"}
    if not is_available():
        return {"available": False, "ai_probability": None,
                "error": "transformers/torch not installed"}
    try:
        clf = _load(model, device, threads)
    except Exception as e:
        return {"available": False, "ai_probability": None, "error": f"load failed: {e}"}

    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            with _infer_lock:  # 파이프라인 동시호출 보호
                scores = clf(im)
        ai_prob, top = _ai_prob_from_scores(scores)
        return {
            "available": True,
            "ai_probability": round(ai_prob, 4),
            "label": top,
            "model": model,
            "scores": [{"label": s["label"], "score": round(float(s["score"]), 4)} for s in scores],
        }
    except Exception as e:
        logger.warning("AI detector inference failed: %s", e)
        return {"available": False, "ai_probability": None, "error": str(e)}


# PIL은 모듈 상단에서 import하면 무거우므로 함수 내에서 지연 import하되,
# classify에서 자주 쓰므로 모듈 로드 시 한 번만.
try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None
