"""플랫폼 감지 및 기능 플래그

단일 소스코드(git)에서 iMac M칩 / GPU 서버 / CPU 서버를 자동 감지하여
각 플랫폼에서 활성화할 Blueprint·기능을 결정한다.

감지 우선순위:
  1. 환경변수 PLATFORM_OVERRIDE (테스트/강제 지정용)
  2. darwin → IMAC
  3. nvidia-smi 존재 + 응답 → GPU
  4. 그 외 → CPU

사용 예시:
  from core.platform import Platform, is_imac, is_gpu, feature

  if is_imac():
      from core.apple_silicon import get_all_metrics
      ...

  if feature("smart_gateway"):
      app.register_blueprint(gateway_bp)
"""
import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger("ollama-admin")


class Platform:
    IMAC = "imac"   # macOS + Apple Silicon (M1/M2/M3/M4)
    GPU  = "gpu"    # Linux + NVIDIA GPU
    CPU  = "cpu"    # Linux, GPU 없음


# ---------------------------------------------------------------------------
# 감지
# ---------------------------------------------------------------------------

_detected: str | None = None


def detect() -> str:
    """현재 플랫폼 감지 (최초 1회 실행 후 캐시)"""
    global _detected
    if _detected:
        return _detected

    override = os.environ.get("PLATFORM_OVERRIDE", "").strip().lower()
    if override in (Platform.IMAC, Platform.GPU, Platform.CPU):
        _detected = override
        logger.info("[platform] override: %s", _detected)
        return _detected

    if sys.platform == "darwin":
        _detected = Platform.IMAC
    elif shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True, text=True, timeout=5
            )
            _detected = Platform.GPU if r.returncode == 0 else Platform.CPU
        except Exception:
            _detected = Platform.CPU
    else:
        _detected = Platform.CPU

    logger.info("[platform] detected: %s (sys.platform=%s)", _detected, sys.platform)
    return _detected


def is_imac() -> bool:
    return detect() == Platform.IMAC


def is_gpu() -> bool:
    return detect() == Platform.GPU


def is_cpu() -> bool:
    return detect() == Platform.CPU


def label() -> str:
    """사람이 읽기 좋은 플랫폼 레이블"""
    return {
        Platform.IMAC: "iMac (Apple Silicon)",
        Platform.GPU:  "GPU Server (NVIDIA)",
        Platform.CPU:  "CPU Server",
    }.get(detect(), "Unknown")


# ---------------------------------------------------------------------------
# 기능 플래그 정의
# ---------------------------------------------------------------------------
#
# 각 기능이 어느 플랫폼에서 활성화되는지 명세한다.
# feature() 함수로 런타임에 조회한다.
#
# ┌─────────────────────────────┬──────┬─────┬─────┐
# │ Feature                     │ iMac │ GPU │ CPU │
# ├─────────────────────────────┼──────┼─────┼─────┤
# │ apple_silicon_monitor       │  ✓   │     │     │
# │ coreml_embed                │  ✓   │     │     │
# │ smart_gateway               │  ✓   │     │     │
# │ ane_stt                     │  ✓   │     │     │
# │ model_cache_manager         │  ✓   │     │     │
# ├─────────────────────────────┼──────┼─────┼─────┤
# │ image_gen (ComfyUI)         │      │  ✓  │     │
# │ gpu_training                │      │  ✓  │     │
# │ drug_pipeline_gpu           │      │  ✓  │     │
# │ medical_training            │      │  ✓  │     │
# ├─────────────────────────────┼──────┼─────┼─────┤
# │ queue                       │  ✓   │  ✓  │  ✓  │ (공통)
# │ rag                         │  ✓   │  ✓  │  ✓  │
# │ chat / generate             │  ✓   │  ✓  │  ✓  │
# │ korean / writing            │  ✓   │  ✓  │  ✓  │
# │ search                      │  ✓   │  ✓  │  ✓  │
# │ orchestrator                │  ✓   │  ✓  │  ✓  │
# │ medical_ai                  │  ✓*  │  ✓  │  ✓  │ (*경량 모델)
# └─────────────────────────────┴──────┴─────┴─────┘

_FEATURE_MAP: dict[str, dict[str, bool]] = {
    # ── iMac 전용 ──────────────────────────────────────────────────────
    "apple_silicon_monitor": {
        Platform.IMAC: True,
        Platform.GPU:  False,
        Platform.CPU:  False,
    },
    "coreml_embed": {
        Platform.IMAC: True,
        Platform.GPU:  False,
        Platform.CPU:  False,
    },
    "smart_gateway": {          # 복잡 요청을 GPU 서버로 포워딩
        Platform.IMAC: True,
        Platform.GPU:  False,
        Platform.CPU:  False,
    },
    "ane_stt": {                # faster-whisper + ANE (Metal)
        Platform.IMAC: True,
        Platform.GPU:  False,
        Platform.CPU:  False,
    },
    "model_cache_manager": {    # Unified Memory 스마트 keep-alive
        Platform.IMAC: True,
        Platform.GPU:  False,
        Platform.CPU:  False,
    },

    # ── GPU 서버 전용 ────────────────────────────────────────────────────
    "image_gen": {              # ComfyUI 이미지 생성
        Platform.IMAC: False,
        Platform.GPU:  True,
        Platform.CPU:  False,
    },
    "gpu_training": {           # CUDA fine-tuning
        Platform.IMAC: False,
        Platform.GPU:  True,
        Platform.CPU:  False,
    },
    "drug_pipeline_gpu": {      # Drug Batch GPU 스케줄러
        Platform.IMAC: False,
        Platform.GPU:  True,
        Platform.CPU:  False,
    },
    "medical_training": {       # Medical Training Blueprint
        Platform.IMAC: False,
        Platform.GPU:  True,
        Platform.CPU:  False,
    },

    # ── 공통 ────────────────────────────────────────────────────────────
    "queue":        {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "rag":          {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "chat":         {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "korean":       {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "writing":      {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "search":       {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "orchestrator": {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "medical_ai":   {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
    "mcp":          {p: True  for p in (Platform.IMAC, Platform.GPU, Platform.CPU)},
}


def feature(name: str) -> bool:
    """기능 플래그 조회

    Args:
        name: 기능명 (위 _FEATURE_MAP 키)

    Returns:
        현재 플랫폼에서 해당 기능 활성화 여부
    """
    return _FEATURE_MAP.get(name, {}).get(detect(), False)


def features_summary() -> dict:
    """현재 플랫폼의 전체 기능 플래그 요약"""
    p = detect()
    return {
        "platform": p,
        "label": label(),
        "features": {k: v.get(p, False) for k, v in _FEATURE_MAP.items()},
    }
