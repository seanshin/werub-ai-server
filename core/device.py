"""Device(가속기) 선택 — 단일 진실 공급원 (cross-arch 관리 중심점).

이 레포 하나가 두 종류의 머신에서 실행된다:
  - Ubuntu 서버 : x86_64 + NVIDIA CUDA (RTX 5080)        → device "cuda"
  - iMac M1     : arm64  + Apple Metal (MPS)             → device "mps"
  - 그 외/폴백  : CPU                                     → device "cpu"

규칙(지속 관리):
  - device 문자열("cuda"/"mps"/"cpu")을 코드에 **하드코딩하지 말 것**.
    반드시 resolve()를 경유한다. cuda 미가용 머신에서 "cuda"를 그대로 쓰면 즉시 크래시한다.
  - ML 모듈/엔드포인트는 config 키로 머신별 override를 받는다(예: ai_detector_device).
    config 미지정 시 "auto"가 안전 기본값(가용 가속기 자동 선택).
  - 새 ML 모듈을 추가하면 여기 resolve()를 쓰고, docs/CROSS_ARCH.md 체크리스트를 갱신한다.

torch는 무거우므로 함수 내부에서 지연 import한다(이 모듈 import 비용 0).
"""
from __future__ import annotations

import logging
import platform

logger = logging.getLogger("ollama-admin")

# --- 정적 플랫폼 식별 (import 시 1회, torch 불필요) ---
IS_DARWIN = platform.system() == "Darwin"   # iMac (Apple Silicon)
IS_LINUX = platform.system() == "Linux"     # Ubuntu 서버
ARCH = platform.machine()                    # x86_64 / arm64 / aarch64
IS_ARM = ARCH in ("arm64", "aarch64")

_VALID_PREFS = ("auto", "cuda", "mps", "cpu")


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _mps_available() -> bool:
    try:
        import torch
        return bool(torch.backends.mps.is_available())
    except Exception:
        return False


def resolve(preference: str = "auto") -> str:
    """선호 device → 실제 사용 가능한 canonical torch device 문자열.

    반환값은 항상 "cuda" | "mps" | "cpu" 중 하나이며,
    torch 텐서 .to(...) / speechbrain run_opts={"device": ...} 등에 그대로 넣을 수 있다.

    - None/"" : 미설정 = "auto"로 간주
    - "auto" : cuda > mps > cpu 우선순위로 가용 가속기 자동 선택
    - "cuda" : 가용 시 cuda, 아니면 cpu 폴백(크래시 방지)
    - "mps"  : 가용 시 mps, 아니면 cpu 폴백
    - "cpu"  : 항상 cpu
    - 그 외(오타 등) : **방어적으로 cpu** — 잘못된 값이 GPU를 잡지 않도록.
    """
    if preference is None or str(preference).strip() == "":
        pref = "auto"  # 미설정 = auto
    else:
        pref = str(preference).strip().lower()
        if pref not in _VALID_PREFS:
            logger.debug("unknown device preference %r → cpu(방어)", preference)
            return "cpu"

    if pref == "cpu":
        return "cpu"
    if pref == "cuda":
        if _cuda_available():
            return "cuda"
        logger.info("device=cuda 요청했으나 CUDA 미가용 → cpu 폴백 (arch=%s)", ARCH)
        return "cpu"
    if pref == "mps":
        if _mps_available():
            return "mps"
        logger.info("device=mps 요청했으나 MPS 미가용 → cpu 폴백 (arch=%s)", ARCH)
        return "cpu"

    # auto
    if _cuda_available():
        return "cuda"
    if _mps_available():
        return "mps"
    return "cpu"


def pipeline_arg(device: str):
    """canonical device 문자열 → HuggingFace transformers pipeline(device=) 인자.

    transformers는 정수(0=cuda:0, -1=cpu) 또는 문자열("mps")을 받는다.
    매핑: cuda→0, mps→"mps", cpu→-1.
    """
    d = (device or "cpu").strip().lower()
    if d == "cuda":
        return 0
    if d == "mps":
        return "mps"
    return -1


def info() -> dict:
    """현재 머신의 가속기 가용성 스냅샷 (진단/엔드포인트용)."""
    return {
        "os": platform.system(),
        "arch": ARCH,
        "is_arm": IS_ARM,
        "cuda_available": _cuda_available(),
        "mps_available": _mps_available(),
        "auto_device": resolve("auto"),
    }
