"""스마트 GPU 스케줄링 — 시간대별 모델 전략 + VRAM 모니터링 (P3-1)

기능:
  - 시간대별 모델 상주 전략 (의료 시간 vs 비의료 시간)
  - VRAM 사용량 모니터링 + 자동 모델 해제/적재
  - 모델 적재 예열 (cold start 방지)
  - 긴급 의료 요청 시 모델 즉시 로드
"""
import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("ollama-admin")

_ollama_session = None
_OLLAMA_BASE = None
_schedule_lock = threading.Lock()

# 모델 VRAM 사용량 추정 (GB)
MODEL_VRAM_ESTIMATE = {
    "qwen2.5:14b": 9.5,
    "qwen2.5:7b": 5.0,
    "meditron": 5.5,
    "meditron:latest": 5.5,
    "medgemma:4b": 3.5,
    "MedAIBase/MedGemma1.5:4b": 8.5,
    "medgemma:27b": 17.0,
    "minicpm-v:latest": 5.5,
    "llama3.2:3b": 2.5,
    "qwen2.5:3b": 2.5,
    "bge-m3": 1.0,
    # 의료 모델 (v2.48.0)
    "werub-medical:7b": 5.0,
    "werubmedv2:latest": 5.0,
    "thewindmom/llama3-med42-8b": 8.5,
    "koesn/llama3-openbiollm-8b": 5.0,
    # biomistral, llava-med 삭제 (v2.69.0)
    "gpt-oss:20b": 13.0,
    "monotykamary/medichat-llama3": 5.0,
    "medllama2:latest": 4.0,
    # 글쓰기/비전 모델 (v2.48.0)
    "exaone-deep:7.8b": 5.5,
    "dnotitia/dna:latest": 5.0,
    "gemma4:latest": 14.0,
    "qwen3-vl:8b": 6.5,
    "deepseek-ocr:latest": 4.5,
    "minicpm-v:latest": 5.5,
    # v2.69.0 신규 모델
    "gemma4:26b": 17.0,
    "qwen3:14b": 9.5,
    "qwen3-coder:30b": 4.0,   # MoE 활성 3.3B → VRAM 소형
}

# 시간대별 모델 전략
SCHEDULE_PROFILES = {
    "medical_hours": {
        "hours": (8, 19),  # 08:00~19:00
        "resident": ["qwen2.5:14b"],  # 상주 모델
        "standby": ["werub-medical:7b", "thewindmom/llama3-med42-8b", "medgemma:4b"],
        "description": "의료 시간대: 14b 상주, werub+Med42+medgemma 대기",
    },
    "off_hours": {
        "hours": (19, 8),  # 19:00~08:00
        "resident": ["qwen2.5:7b"],
        "standby": [],
        "description": "비의료 시간: 7b 경량 운영",
    },
}

# GPU 총 VRAM (RTX 5080)
TOTAL_VRAM_GB = 16.0
VRAM_SAFETY_MARGIN = 1.5  # 여유 1.5GB


def init_gpu_scheduler(*, ollama_session, ollama_base):
    global _ollama_session, _OLLAMA_BASE
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base


def get_current_profile():
    """현재 시간대에 맞는 스케줄 프로파일 반환"""
    hour = datetime.now().hour
    if 8 <= hour < 19:
        return "medical_hours", SCHEDULE_PROFILES["medical_hours"]
    return "off_hours", SCHEDULE_PROFILES["off_hours"]


def get_loaded_models():
    """현재 Ollama에 로드된 모델 목록 조회"""
    try:
        r = _ollama_session.get(f"{_OLLAMA_BASE}/api/ps", timeout=5)
        models = r.json().get("models", [])
        return [{
            "name": m.get("name", ""),
            "size": m.get("size", 0),
            "size_vram": m.get("size_vram", 0),
            "expires_at": m.get("expires_at", ""),
        } for m in models]
    except Exception as e:
        logger.debug("Failed to get loaded models: %s", e)
        return []


def get_vram_usage():
    """현재 VRAM 사용량 추정 (로드된 모델 기반)"""
    loaded = get_loaded_models()
    used_gb = 0.0
    model_details = []
    for m in loaded:
        name = m["name"]
        # size_vram이 있으면 그걸 사용, 없으면 추정
        if m.get("size_vram", 0) > 0:
            vram_gb = m["size_vram"] / (1024 ** 3)
        else:
            vram_gb = MODEL_VRAM_ESTIMATE.get(name, 4.0)
        used_gb += vram_gb
        model_details.append({"name": name, "vram_gb": round(vram_gb, 1)})

    return {
        "total_gb": TOTAL_VRAM_GB,
        "used_gb": round(used_gb, 1),
        "free_gb": round(TOTAL_VRAM_GB - used_gb, 1),
        "utilization_pct": round(used_gb / TOTAL_VRAM_GB * 100, 1),
        "models": model_details,
    }


def unload_model(model_name):
    """특정 모델 VRAM에서 해제"""
    try:
        _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model_name,
            "prompt": "",
            "keep_alive": 0,
        }, timeout=10)
        logger.info("GPU Scheduler: unloaded %s", model_name)
        return True
    except Exception as e:
        logger.warning("GPU Scheduler: failed to unload %s: %s", model_name, e)
        return False


def preload_model(model_name, keep_alive=-1):
    """모델을 VRAM에 사전 적재"""
    try:
        _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model_name,
            "prompt": "",
            "keep_alive": keep_alive,
        }, timeout=120)
        logger.info("GPU Scheduler: preloaded %s (keep_alive=%s)", model_name, keep_alive)
        return True
    except Exception as e:
        logger.warning("GPU Scheduler: failed to preload %s: %s", model_name, e)
        return False


def ensure_model_loaded(model_name, urgent=False):
    """모델이 로드되어 있는지 확인, 없으면 로드 (VRAM 여유 확보 포함)

    Args:
        model_name: 필요한 모델
        urgent: True면 다른 모델 강제 해제
    """
    with _schedule_lock:
        loaded = get_loaded_models()
        loaded_names = [m["name"] for m in loaded]

        if model_name in loaded_names:
            return True  # 이미 로드됨

        # VRAM 여유 확인
        vram = get_vram_usage()
        needed_gb = MODEL_VRAM_ESTIMATE.get(model_name, 5.0)
        free_gb = vram["free_gb"]

        if free_gb < needed_gb + VRAM_SAFETY_MARGIN:
            # VRAM 부족 — 우선순위 낮은 모델부터 해제
            profile_name, profile = get_current_profile()
            resident = profile["resident"]

            # 해제 후보: 현재 프로파일의 resident가 아닌 모델
            for m in loaded:
                if m["name"] not in resident or urgent:
                    unload_model(m["name"])
                    freed = MODEL_VRAM_ESTIMATE.get(m["name"], 4.0)
                    free_gb += freed
                    if free_gb >= needed_gb + VRAM_SAFETY_MARGIN:
                        break

        return preload_model(model_name)


def apply_schedule_profile():
    """현재 시간대 프로파일에 맞게 모델 적재 조정

    Returns:
        {"profile": str, "actions": [str], "vram": dict}
    """
    with _schedule_lock:
        profile_name, profile = get_current_profile()
        loaded = get_loaded_models()
        loaded_names = [m["name"] for m in loaded]
        actions = []

        # 1. 프로파일에 없는 모델 해제 (resident + standby가 아닌 모델)
        allowed = set(profile["resident"] + profile.get("standby", []))
        for m in loaded:
            if m["name"] not in allowed:
                unload_model(m["name"])
                actions.append(f"unloaded {m['name']}")

        # 2. resident 모델 로드
        for model in profile["resident"]:
            if model not in loaded_names:
                preload_model(model, keep_alive=-1)
                actions.append(f"loaded {model}")

        vram = get_vram_usage()
        return {
            "profile": profile_name,
            "description": profile["description"],
            "actions": actions,
            "vram": vram,
        }


def get_scheduler_status():
    """스케줄러 전체 상태 조회"""
    profile_name, profile = get_current_profile()
    return {
        "current_profile": profile_name,
        "profile_description": profile["description"],
        "schedule_hours": profile["hours"],
        "resident_models": profile["resident"],
        "standby_models": profile.get("standby", []),
        "loaded_models": get_loaded_models(),
        "vram": get_vram_usage(),
    }


# ---------------------------------------------------------------------------
# 백그라운드 스케줄러 (매 5분 프로파일 체크)
# ---------------------------------------------------------------------------

_scheduler_running = False


def start_scheduler_loop(enabled: bool = False):
    """백그라운드 스케줄링 루프 시작 — 기본 **꺼짐**(v2.125.49).

    ⚠️ 시간대 프로파일(off_hours=14b 해제→7b 적재)은 상주 정책(`core/llm_residency` —
    `llm_resident`·14b)과 **정면 충돌**한다. 라이브(2026-09-27~28): 워커 재기동마다 이 루프가
    14b를 내리고 7b를 올리면 6초 뒤 시작 예열이 14b를 되올려 7b를 내쫓았다 — off_hours는
    한 번도 실제로 적용된 적이 없고 **적재 3회만 헛돌았다**. 켜려면 config
    `gpu_schedule_profiles_enabled=true` + 상주 정책을 프로파일과 일치시킬 것.
    수동 적용(`/api/gpu/schedule/apply`류)은 그대로 둔다.
    """
    global _scheduler_running
    if not enabled:
        logger.info("GPU Scheduler: 시간대 프로파일 자동 적용 off(상주 정책 우선)")
        return
    if _scheduler_running:
        return
    _scheduler_running = True

    def _loop():
        last_profile = None
        while _scheduler_running:
            try:
                profile_name, _ = get_current_profile()
                if profile_name != last_profile:
                    result = apply_schedule_profile()
                    if result["actions"]:
                        logger.info("GPU Scheduler: profile changed to %s — %s",
                                    profile_name, result["actions"])
                    last_profile = profile_name
            except Exception as e:
                logger.debug("GPU Scheduler loop error: %s", e)
            time.sleep(300)  # 5분마다 체크

    t = threading.Thread(target=_loop, daemon=True, name="gpu-scheduler")
    t.start()
    logger.info("GPU Scheduler background loop started")


def stop_scheduler_loop():
    global _scheduler_running
    _scheduler_running = False
