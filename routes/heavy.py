"""Heavy tier — 하이브리드(GPU VRAM + 시스템 RAM) 대형 모델 on-demand 구동.

상주 fast-lane(config.default_model, 예: qwen2.5:14b, keep_alive=-1)과 **분리**된 별도 경로.
16GB VRAM에 안 들어가는 대형 모델을 Ollama 자동 레이어 오프로드(부분 CPU)로 구동한다.
MAX_LOADED=1이라 heavy는 상주 모델을 일시 축출 → 응답 후 상주 복원(fast-lane 보호).
품질 우선·속도 비이슈 tier(하이브리드는 느림). 기본 opt-in(heavy_enabled=false=기존동작 불변).

설계 근거: docs/01-plan/features/hybrid-large-model-inference.plan.md (PoC 실측).
- 하이브리드 메커니즘 검증됨(MoE >16GB 자동 오프로드 56 tok/s). 대형 dense는 느림(50~88s/문항).
- single-flight(동시 heavy 2개=OOM/스래싱 방지) + 응답 후 상주 복원.
"""
import json
import logging
import threading

import requests as http_requests
from flask import Blueprint, request, jsonify, Response
from core.rate_limit import heavy_limit

logger = logging.getLogger("werub.heavy")

heavy_bp = Blueprint("heavy", __name__)

_load_config = None
_save_config = None
_OLLAMA_BASE = None
_gpu_manager = None
_is_admin_request = None

# 동시 heavy 실행 방지(대형 모델 2개=16GB 초과 OOM/RAM 스래싱). non-blocking.
_heavy_lock = threading.Lock()

_DEFAULTS = {
    "heavy_enabled": False,
    # 2026-08-04 **근거 정정** — 종전 근거였던 "충실도 우위"는 재현되지 않는다.
    #   구 기록: v2.124.4(N=40) 충실도 0.931 vs 0.908 · 08-03(N=20) 0.946 vs 0.878
    #   ⚠️ 두 측정 모두 v2.124.75 이전 **일괄 주장 판정 오염**(정답 주장을 contradicted로
    #      떨어뜨림) 위에서 잰 값이다. 봉합 후 같은 조건 재측정(N=16·문항별 대응):
    #        충실도 14b 0.979 vs qwen3.6 0.941 — 4승 1패 11동, **부호검정 p=0.375 유의하지 않음**
    #        품질   14b 4.30  vs qwen3.6 4.56  — 2승 10패 4동, **p=0.039 유의**
    # ⇒ 실제 우위는 충실도가 아니라 **품질(루브릭)** 축이다. heavy tier가 "품질 우선·
    #    속도 비이슈" tier라 기본값 유지 결론은 같지만, 근거는 이 문장으로 읽을 것.
    # 대가는 지연 4.5배(6.7s→30.1s) — 비실시간 심층 자문 용도엔 수용 가능.
    # ⚠️ 같은 날 AirLLM(0.05 tok/s·서비스 중단 필요)·llama.cpp MoE(Ollama가 이미 58 tok/s로
    #    더 빠르고 qwen3.6 로드 불가)·qwen3:14b 상주 교체(grounded 충실도 −0.034) 전부 탈락 →
    #    **이미 갖고 있던 이 조합이 답**이었다. 경위: docs/01-plan/features/airllm-integration.plan.md
    # ⚠️ thinking 모델이라 아래 핸들러가 think=false를 기본 주입한다(미주입 시 **빈 응답** 실증).
    #    기본값을 비-thinking 모델로 바꾸면 Ollama가 요청 전체를 거절하므로 반드시 확인할 것.
    # 2026-08-04 근거 정정 — 종전 근거였던 "충실도 우위"는 재현되지 않는다.
    #   구 기록: v2.124.4(N=40) 0.931 vs 0.908 · 08-03(N=20) 0.946 vs 0.878
    #   ⚠️ 두 측정 모두 v2.124.75 이전 **일괄 주장 판정 오염** 위에서 잰 값이다.
    #      봉합 후 재측정(N=16): 충실도 14b 0.979 vs qwen3.6 0.941 — p=0.375 **유의 아님**
    #                            품질   14b 4.30  vs qwen3.6 4.56  — p=0.039 **유의**
    #   ⇒ 실제 우위는 충실도가 아니라 **품질(루브릭)** 축이다. 기본값 유지 결론은 같다.
    #
    # ⚠️ **EXAONE 4.0 32B는 시험 후 기각**(2026-08-04, 모델 파일은 보존).
    #   N=8에서 품질 0승 5패(p=0.062)로 우세해 보여 기본값을 잠시 전환했으나, **N=16 재측정에서
    #   5승 4패 7동(p=1.000)으로 재현 실패** — 전형적인 소표본 착시였다. 오히려 충실도는
    #   qwen3.6이 4승 0패(0.984 vs 0.940). 지연도 2.4배(27s→64s)인데, **dense 32B(19GB)가
    #   16GB VRAM을 넘어 CPU 오프로드를 타기 때문**이다. heavy가 빠른 건 qwen3.6이
    #   **MoE(활성 3B)**여서다 — 같은 크기라도 dense면 이 tier의 전제가 깨진다.
    #   라이선스도 EXAONE 1.2 - NC(qwen3.6은 Apache-2.0). 재시도하려면 N≥16으로 시작할 것.
    "heavy_model": "qwen3.6:35b-a3b",
    "heavy_keep_alive": "0",   # 응답 후 언로드 → 상주 fast-lane 복원(기본). "5m"=warm 유지
    "heavy_num_gpu": None,       # None=Ollama 자동 레이어 분할. 정수=GPU 레이어수(수동 오프로드)
}


def init_heavy(*, load_config, save_config, ollama_base, gpu_manager, is_admin_request):
    global _load_config, _save_config, _OLLAMA_BASE, _gpu_manager, _is_admin_request
    _load_config = load_config
    _save_config = save_config
    _OLLAMA_BASE = ollama_base
    _gpu_manager = gpu_manager
    _is_admin_request = is_admin_request


def _cfg(key):
    return (_load_config() or {}).get(key, _DEFAULTS[key])


def _restore_resident():
    """heavy 실행 후 상주 fast-lane(default_model) 복원 — 백그라운드(응답 지연 0)."""
    try:
        if _gpu_manager is not None:
            _gpu_manager._reload_llm()  # → _ensure_default_model(keep_alive=-1)
    except Exception as e:
        logger.warning("resident restore failed: %s", e)


def _build_options(data):
    opts = dict(data.get("options") or {})
    ng = _cfg("heavy_num_gpu")
    if isinstance(ng, int):          # 수동 하이브리드 분할(GPU 레이어수 고정)
        opts.setdefault("num_gpu", ng)
    return opts


@heavy_bp.route("/api/chat/heavy", methods=["POST"])
@heavy_limit("10/minute")
def api_chat_heavy():
    """하이브리드 대형 모델 채팅 (상주 fast-lane과 분리·on-demand).
    ---
    tags: [Heavy]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            messages: {type: array, description: "OpenAI형 messages[]. 없으면 prompt 사용"}
            prompt:   {type: string, description: "단일 프롬프트(messages 대체)"}
            model:    {type: string, description: "heavy 모델 오버라이드(기본 config.heavy_model)"}
            system:   {type: string}
            stream:   {type: boolean, default: true}
            think:    {type: boolean, default: false, description: "thinking 모델 추론 노출(기본 false=답변만). qwen3.6 등은 false 필수(빈응답 방지)"}
    responses:
      200: {description: "Ollama /api/chat NDJSON 스트림(또는 stream=false 단건 JSON)"}
      400: {description: "heavy 비활성 / 입력오류"}
      409: {description: "다른 heavy 요청 처리중(single-flight)"}
    """
    if not _cfg("heavy_enabled"):
        return jsonify({"error": "heavy tier is disabled (set heavy_enabled=true)"}), 400
    data = request.get_json(force=True, silent=True) or {}

    messages = data.get("messages")
    if not messages:
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "messages[] or prompt is required"}), 400
        messages = []
        if data.get("system"):
            messages.append({"role": "system", "content": data["system"]})
        messages.append({"role": "user", "content": prompt})

    model = data.get("model") or _cfg("heavy_model")
    stream = data.get("stream", True)
    keep_alive = _cfg("heavy_keep_alive")
    # think=false 기본: qwen3.6 등 thinking 모델이 추론 토큰만 내고 응답이 비는 것 방지
    # (프로덕션 의도=답변 즉시 생성). data.think=true로 추론 노출 오버라이드 가능.
    payload = {"model": model, "messages": messages, "stream": bool(stream),
               "keep_alive": keep_alive, "think": bool(data.get("think", False)),
               "options": _build_options(data)}

    # single-flight: 동시 heavy 실행 차단(대형 모델 동시 로드=OOM)
    if not _heavy_lock.acquire(blocking=False):
        return jsonify({"error": "another heavy request is in progress", "retry_after": 10}), 409

    logger.info("heavy chat start — model=%s stream=%s (상주 축출)", model, stream)

    if not stream:
        try:
            r = http_requests.post(f"{_OLLAMA_BASE}/api/chat", json=payload, timeout=1800)
        except Exception as e:
            _heavy_lock.release()
            threading.Thread(target=_restore_resident, daemon=True).start()
            return jsonify({"error": f"heavy upstream error: {str(e)[:200]}"}), 502
        _heavy_lock.release()
        threading.Thread(target=_restore_resident, daemon=True).start()
        if r.status_code != 200:
            return jsonify({"error": f"heavy model error({r.status_code})"}), 502
        return jsonify(r.json())

    def generate():
        try:
            with http_requests.post(f"{_OLLAMA_BASE}/api/chat", json=payload,
                                    stream=True, timeout=1800) as r:
                for line in r.iter_lines():
                    if line:
                        yield line + b"\n"
        except Exception as e:
            logger.warning("heavy stream error: %s", e)
            yield (json.dumps({"error": str(e)[:200]}) + "\n").encode()
        finally:
            _heavy_lock.release()
            # 응답 종료 후 상주 fast-lane 복원(백그라운드)
            threading.Thread(target=_restore_resident, daemon=True).start()

    return Response(generate(), mimetype="application/x-ndjson")


@heavy_bp.route("/api/heavy/status")
def api_heavy_status():
    """Heavy tier 상태 — config + 현재 로드 모델 + heavy 실행중 여부.
    ---
    tags: [Heavy]
    responses:
      200: {description: 상태}
    """
    out = {
        "enabled": bool(_cfg("heavy_enabled")),
        "heavy_model": _cfg("heavy_model"),
        "keep_alive": _cfg("heavy_keep_alive"),
        "num_gpu": _cfg("heavy_num_gpu"),
        "running": _heavy_lock.locked(),
        "resident_model": (_load_config() or {}).get("default_model", ""),
        "loaded": [],
    }
    try:
        r = http_requests.get(f"{_OLLAMA_BASE}/api/ps", timeout=3)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                sv = m.get("size_vram", 0) or 0
                sz = m.get("size", 0) or 0
                out["loaded"].append({
                    "name": m.get("name", ""),
                    "vram_gb": round(sv / 1e9, 1),
                    "total_gb": round(sz / 1e9, 1),
                    "cpu_offload_pct": round((1 - sv / sz) * 100) if sz else 0,
                })
    except Exception:
        pass
    return jsonify(out)


@heavy_bp.route("/api/heavy/settings", methods=["POST"])
def api_heavy_settings():
    """Heavy tier 설정 토글 (localhost/admin only).
    ---
    tags: [Heavy]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            heavy_enabled:    {type: boolean}
            heavy_model:      {type: string}
            heavy_keep_alive: {type: string}
            heavy_num_gpu:    {type: integer, description: "GPU 레이어수(수동 하이브리드). null=자동"}
    responses:
      200: {description: 갱신된 설정}
      403: {description: unauthorized}
    """
    if _is_admin_request and not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.get_json(force=True, silent=True) or {}
    cfg = _load_config() or {}
    if "heavy_enabled" in data:
        cfg["heavy_enabled"] = bool(data["heavy_enabled"])
    if "heavy_model" in data:
        cfg["heavy_model"] = str(data["heavy_model"])
    if "heavy_keep_alive" in data:
        cfg["heavy_keep_alive"] = str(data["heavy_keep_alive"])
    if "heavy_num_gpu" in data:
        v = data["heavy_num_gpu"]
        cfg["heavy_num_gpu"] = int(v) if isinstance(v, (int, float)) and v is not None else None
    _save_config(cfg)
    return jsonify({"ok": True, "settings": {k: cfg.get(k, _DEFAULTS[k]) for k in _DEFAULTS}})
