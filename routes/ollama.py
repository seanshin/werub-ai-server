"""Ollama API Blueprint — API 키 관리 + Ollama 프록시 + 설정"""

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

# ---------------------------------------------------------------------------
# 이 모듈이 Ollama를 그대로 프록시하지 않는 이유 (지우기 전에 읽을 것)
#
# 1) num_ctx 자동 상향 — Ollama의 컨텍스트 길이는 **프로세스 전역**(OLLAMA_CONTEXT_LENGTH)
#    이라, 큰 프롬프트가 오면 서버가 **에러 없이 중간을 잘라버린다**. 호출자는 품질 저하만
#    보고 원인을 모른다(실제로 외부 클라이언트가 8K~17K 토큰을 상시 보내던 것을 뒤늦게 발견).
#    → _estimate_tokens/_auto_num_ctx 로 요청별 상향. **고정 버킷**을 쓰는 이유는 가변값이면
#      요청마다 모델이 리로드돼 오실레이션이 생기기 때문이다.
#
# 2) images 정규화 — Ollama는 **접두 없는 raw base64**만 받는다. 호출자가 흔히 보내는
#    `data:image/png;base64,...`를 그대로 넘기면 이미지가 조용히 무시된다(모델이 "이미지가
#    없다"고 답해 원인 추적이 어렵다). → _normalize_images
#
# 3) 프롬프트 캡·인증·레이트리밋 — Ollama에는 없는 계층. 단일 워커 자원 점유와 무인증 노출을 막는다.
#
# 상세: docs/DEVELOPER_GUIDE.md §1
# ---------------------------------------------------------------------------
import sys
import json
import logging
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import requests as http_requests
from flask import Blueprint, request, jsonify, Response
from core.rate_limit import heavy_limit
from core.errors import internal_error
from core.cache import TTLCache

logger = logging.getLogger("ollama-admin")

ollama_bp = Blueprint("ollama", __name__)

_status_cache = {"value": False, "ts": 0}

_get_db = None
_load_config = None
_save_config = None
_ollama_session = None
_is_admin_request = None
_generate_api_key = None
_chat_queue = None
_gpu_manager = None
_OLLAMA_BASE = None
_clear_key_cache = None  # L2: 키 검증 캐시 즉시 폐기(toggle/delete 반영 지연 제거)
_is_local_request = None  # 보안 완화(인증 끄기 등)는 서버 localhost에서만 — admin-menu-settings-cleanup D3


def init_ollama(*, get_db, load_config, save_config, ollama_session, ollama_get=None, ollama_post=None, ollama_delete=None, format_size=None, get_ollama_status=None, version=None, changelog=None, build_system_prompt=None, llm_response_cache=None, rate_limit_key=None, clear_key_cache=None,
                is_admin_request, generate_api_key, chat_queue, gpu_manager,
                ollama_base, is_local_request=None):
    global _get_db, _load_config, _save_config, _ollama_session
    global _is_admin_request, _generate_api_key, _chat_queue, _gpu_manager, _OLLAMA_BASE
    global _ollama_get, _ollama_post, _ollama_delete, _format_size, _get_ollama_status
    global _VERSION, _CHANGELOG
    global _build_system_prompt
    global _llm_response_cache
    global _rate_limit_key
    global _clear_key_cache, _is_local_request
    _clear_key_cache = clear_key_cache
    _is_local_request = is_local_request
    _get_db = get_db
    _load_config = load_config
    _save_config = save_config
    _ollama_session = ollama_session
    _is_admin_request = is_admin_request
    _generate_api_key = generate_api_key
    _chat_queue = chat_queue
    _gpu_manager = gpu_manager
    _OLLAMA_BASE = ollama_base
    _ollama_get = ollama_get
    _ollama_post = ollama_post
    _ollama_delete = ollama_delete
    _format_size = format_size
    _get_ollama_status = get_ollama_status
    _VERSION = version
    _CHANGELOG = changelog
    _build_system_prompt = build_system_prompt
    _llm_response_cache = llm_response_cache
    _rate_limit_key = rate_limit_key


# Server Environment Detection
# ---------------------------------------------------------------------------
def _detect_server_env():
    """서버 환경 자동 감지 (GPU/CPU, OS, 메모리)"""
    env = {"platform": sys.platform, "has_gpu": False, "gpu_name": "", "gpu_vram_mb": 0, "ram_gb": 0}
    try:
        import shutil
        if shutil.which("nvidia-smi"):
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split(",")
                env["has_gpu"] = True
                env["gpu_name"] = parts[0].strip()
                env["gpu_vram_mb"] = int(parts[1].strip().replace(" MiB", ""))
    except Exception as e:
        logger.debug("GPU detection failed: %s", e)
    try:
        import psutil
        env["ram_gb"] = round(psutil.virtual_memory().total / (1024**3))
    except Exception as e:
        logger.debug("RAM detection via psutil failed: %s", e)
    if env["ram_gb"] == 0 and sys.platform == "darwin":
        try:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                env["ram_gb"] = round(int(r.stdout.strip()) / (1024**3))
        except Exception as e:
            logger.debug("RAM detection via sysctl failed: %s", e)
    return env

_SERVER_ENV = _detect_server_env()

# macOS(iMac)에서는 이미지 생성 비활성화
IMAGE_DISABLED = _SERVER_ENV["platform"] == "darwin"

def _image_disabled_response():
    return jsonify({
        "error": "image_disabled",
        "message": "이미지 생성은 이 서버(macOS/iMac)에서 비활성화되어 있습니다. GPU 서버에서만 사용 가능합니다."
    }), 403

def require_image_enabled(f):
    """이미지 관련 엔드포인트 — macOS에서 403 반환"""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if IMAGE_DISABLED:
            return _image_disabled_response()
        return f(*args, **kwargs)
    return decorated

# GPU 서버 vs CPU 서버에 따른 Ollama 성능 옵션
def _get_perf_options():
    """서버 환경에 맞는 성능 옵션 반환"""
    is_gpu = _SERVER_ENV.get("has_gpu", False)
    vram = _SERVER_ENV.get("gpu_vram_mb", 0)
    ram = _SERVER_ENV.get("ram_gb", 0)

    if is_gpu and vram >= 12000:
        # GPU 서버 (RTX 5080 등 VRAM 12GB+)
        return [
            {"key": "OLLAMA_FLASH_ATTENTION", "label": "Flash Attention",
             "desc": "GPU 어텐션 연산 최적화. CUDA GPU에서 필수 활성화",
             "values": ["0", "1"], "default": "1",
             "effect": "GPU 속도 +15~25%, VRAM 사용 -20%"},
            {"key": "OLLAMA_KV_CACHE_TYPE", "label": "KV Cache 양자화",
             "desc": "KV 캐시 양자화. GPU VRAM 절약. f16: 최고 품질, q8_0: 균형, q4_0: VRAM 최소",
             "values": ["f16", "q8_0", "q4_0"], "default": "q8_0",
             "effect": "f16: 품질 최고, VRAM 多 / q8_0: 균형 (권장) / q4_0: VRAM -50%, 품질 소폭 하락"},
            {"key": "OLLAMA_NUM_PARALLEL", "label": "동시 추론 수",
             "desc": f"동시 요청 처리 수. VRAM {vram}MB 기준 2~4 권장. 높으면 처리량 증가하지만 개별 속도 감소",
             "values": ["1", "2", "4", "8"], "default": "2",
             "effect": "1: 최고 속도 / 2: 균형 (GPU 권장) / 4+: 높은 처리량, 개별 속도 감소"},
            {"key": "OLLAMA_MAX_LOADED_MODELS", "label": "최대 로드 모델 수",
             "desc": f"GPU VRAM에 동시 유지할 모델 수. VRAM {vram}MB에서 7B 모델 2개 또는 14B 모델 1개 가능",
             "values": ["1", "2", "3"], "default": "1",
             "effect": "1: VRAM 절약 (권장) / 2: 빠른 전환, 소형 모델만 / 3: 대형 모델 불가"},
            {"key": "OLLAMA_KEEP_ALIVE", "label": "모델 유지 시간",
             "desc": "GPU VRAM에 모델 유지 시간. GPU는 로딩 빠르므로 짧게 설정해도 무방",
             "values": ["0", "5m", "10m", "30m", "1h", "24h"], "default": "5m",
             "effect": "0: 즉시 해제 (ComfyUI VRAM 확보) / 5m: GPU 균형 / 24h: 항상 로드"},
            {"key": "OLLAMA_GPU_OVERHEAD", "label": "GPU 오버헤드 예약",
             "desc": "다른 GPU 앱(ComfyUI 등)을 위해 예약할 VRAM. 0이면 Ollama가 전체 VRAM 사용",
             "values": ["0", "256m", "512m", "1g", "2g"], "default": "512m",
             "effect": "0: Ollama 전용 / 512m: ComfyUI 공존 (권장) / 2g: 대형 이미지 생성 여유"},
        ]
    else:
        # CPU 서버 (iMac M1 등)
        return [
            {"key": "OLLAMA_FLASH_ATTENTION", "label": "Flash Attention",
             "desc": "어텐션 연산 최적화. Apple Silicon에서도 속도 10~20% 향상",
             "values": ["0", "1"], "default": "1",
             "effect": "속도 +15%, 메모리 -20%"},
            {"key": "OLLAMA_KV_CACHE_TYPE", "label": "KV Cache 양자화",
             "desc": "KV 캐시를 양자화하여 메모리 절약. q8_0은 품질 유지, q4_0은 메모리 40% 추가 절약",
             "values": ["f16", "q8_0", "q4_0"], "default": "q8_0",
             "effect": "q8_0: 메모리 -30% (품질 유지) / q4_0: 메모리 -50% (품질 미세 하락)"},
            {"key": "OLLAMA_NUM_PARALLEL", "label": "동시 추론 수",
             "desc": f"동시 요청 처리 수. RAM {ram}GB 기준 1이 최적. 2 이상은 메모리 부족으로 느려질 수 있음",
             "values": ["1", "2", "4"], "default": "1",
             "effect": "1: 최고 속도, 순차 처리 / 2+: 처리량 증가, 개별 속도 감소"},
            {"key": "OLLAMA_MAX_LOADED_MODELS", "label": "최대 로드 모델 수",
             "desc": "메모리에 동시 유지할 모델 수. 1이면 모델 전환 시 언로드 후 새 모델 로드",
             "values": ["1", "2", "3"], "default": "1",
             "effect": "1: 메모리 절약, 모델 전환 시 3~5초 로딩 / 2+: 빠른 전환, 메모리 부담"},
            {"key": "OLLAMA_KEEP_ALIVE", "label": "모델 유지 시간",
             "desc": "사용하지 않는 모델을 메모리에 유지하는 시간. 짧으면 메모리 절약",
             "values": ["0", "5m", "10m", "30m", "1h", "24h"], "default": "10m",
             "effect": "0: 즉시 해제 / 10m: 균형 / 24h: 항상 로드 (메모리 점유)"},
        ]

PERF_OPTIONS = _get_perf_options()

# ---------------------------------------------------------------------------
# Ollama Environment Read/Write (cross-platform: macOS plist / Linux systemd)
# ---------------------------------------------------------------------------
_IS_LINUX = sys.platform == "linux"
_OLLAMA_SYSTEMD_ENV = "/etc/systemd/system/ollama.service.d/environment.conf"
# 유닛의 `EnvironmentFile=-/etc/default/ollama`. systemd에서 EnvironmentFile 값은 drop-in의
# `Environment=`를 **덮어쓴다** — drop-in만 읽으면 화면이 실제와 다른 값을 보여 준다
# (2026-09-29 실측: 화면 MAX_LOADED=1·실제 3). 정본은 이 파일이다(운영 결정 2026-09-29).
_OLLAMA_ENV_FILES = ("/etc/default/ollama",)


def _parse_dropin(path):
    """systemd drop-in의 `Environment="K=V"` 줄 → dict. 없거나 못 읽으면 {}."""
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("Environment="):
                    parts = line[len("Environment="):].strip().strip('"').split("=", 1)
                    if len(parts) == 2:
                        result[parts[0]] = parts[1]
    except OSError:
        pass
    return result


def _parse_env_file(path):
    """EnvironmentFile 형식(`K=V`, `#` 주석, 따옴표 허용) → dict. 없거나 못 읽으면 {}."""
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return result


def _read_plist_env():
    """쓰기 기준 파일(Linux=drop-in / macOS=plist)의 Ollama 환경변수.

    ⚠️ 화면 표시에 쓰지 말 것 — Linux에서 이 값은 `/etc/default/ollama`에 가려질 수 있다.
    표시는 `_effective_ollama_env()`.
    """
    if _IS_LINUX:
        return _parse_dropin(_OLLAMA_SYSTEMD_ENV)
    if not os.path.exists(OLLAMA_PLIST):
        return {}
    try:
        import plistlib
        with open(OLLAMA_PLIST, "rb") as f:
            plist = plistlib.load(f)
        return dict(plist.get("EnvironmentVariables", {}))
    except Exception as e:
        logger.debug("Reading plist env failed: %s", e)
        return {}


def _effective_ollama_env():
    """Ollama가 실제로 받는 값 — {키: {value, source, shadowed}}.

    Linux: drop-in 위에 EnvironmentFile을 덮는다(systemd 우선순위). `shadowed`는 가려진
    drop-in 값(같은 값이어도 기록 — 어느 쪽을 고쳐야 하는지가 중요하다).
    """
    eff = {}
    if not _IS_LINUX:
        for k, v in _read_plist_env().items():
            eff[k] = {"value": v, "source": OLLAMA_PLIST, "shadowed": None}
        return eff
    for k, v in _parse_dropin(_OLLAMA_SYSTEMD_ENV).items():
        eff[k] = {"value": v, "source": _OLLAMA_SYSTEMD_ENV, "shadowed": None}
    for path in _OLLAMA_ENV_FILES:
        for k, v in _parse_env_file(path).items():
            prev = eff.get(k)
            eff[k] = {"value": v, "source": path,
                      "shadowed": ({"value": prev["value"], "source": prev["source"]} if prev else None)}
    return eff


def _perf_write_block_reason(keys=()):
    """쓰기가 불가능하거나 무효인 이유(가능하면 None).

    GPU 서버는 앱 계정(weruby)이 두 파일 모두 쓸 수 없다(root 0644 실측) — 예전엔
    open()이 PermissionError로 500이 났다. 쓸 수 있어도 EnvironmentFile에 있는 키는
    drop-in에 써 봐야 가려지므로 적용되지 않는다(무성 무효).
    """
    target = _OLLAMA_SYSTEMD_ENV if _IS_LINUX else OLLAMA_PLIST
    check = target if os.path.exists(target) else os.path.dirname(target)
    if not os.access(check, os.W_OK):
        return f"{target} 쓰기 권한 없음 — 서버에서 직접 수정(정본: /etc/default/ollama)"
    if _IS_LINUX:
        eff = _effective_ollama_env()
        hidden = [k for k in keys if eff.get(k, {}).get("source") in _OLLAMA_ENV_FILES]
        if hidden:
            return f"{', '.join(hidden)}은(는) /etc/default/ollama가 덮어써서 여기서 바꿔도 적용되지 않음"
    return None


def _write_plist_env(env):
    """Ollama 환경변수 쓰기 (macOS plist / Linux systemd drop-in).

    Linux는 `OLLAMA_*`가 아닌 줄(다른 Environment·지시어)을 보존한다 — 예전엔 파일 전체를
    OLLAMA 줄만으로 다시 써서 다른 설정을 조용히 지웠다.
    """
    if _IS_LINUX:
        os.makedirs(os.path.dirname(_OLLAMA_SYSTEMD_ENV), exist_ok=True)
        kept = []
        try:
            with open(_OLLAMA_SYSTEMD_ENV) as f:
                for line in f:
                    body = line.strip()
                    if body.startswith("Environment=") and body[len("Environment="):].strip().strip('"').startswith("OLLAMA_"):
                        continue
                    kept.append(line if line.endswith("\n") else line + "\n")
        except OSError:
            pass
        if not any(l.strip() == "[Service]" for l in kept):
            kept.insert(0, "[Service]\n")
        lines = kept + [f'Environment="{k}={v}"\n' for k, v in env.items() if k.startswith("OLLAMA_")]
        with open(_OLLAMA_SYSTEMD_ENV, "w") as f:
            f.writelines(lines)
        subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"], capture_output=True, timeout=10)
    else:
        # macOS: plist에 쓰기
        import plistlib
        with open(OLLAMA_PLIST, "rb") as f:
            plist = plistlib.load(f)
        plist["EnvironmentVariables"] = env
        with open(OLLAMA_PLIST, "wb") as f:
            plistlib.dump(plist, f)


@ollama_bp.route("/api/keys", methods=["GET"])
def list_keys():
    """List all API keys (local only)
    ---
    tags: [Keys]
    responses:
      200:
        schema:
          type: object
          properties:
            keys: {type: array, items: {type: object}}
      403:
        description: Local access only
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    conn = _get_db()
    rows = conn.execute("SELECT id, key_prefix, name, created_at, last_used_at, active, expires_at FROM api_keys ORDER BY created_at DESC").fetchall()
    # conn is thread-local persistent — do not close
    return jsonify({"keys": [dict(r) for r in rows]})

@ollama_bp.route("/api/keys", methods=["POST"])
def create_key():
    """Create a new API key (local only)
    ---
    tags: [Keys]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            name: {type: string, default: "unnamed"}
            expires_days: {type: integer, default: 90, description: "0 = never expires"}
    responses:
      200:
        schema:
          type: object
          properties:
            key: {type: string, description: "Full API key (shown only once)"}
            name: {type: string}
            expires_at: {type: string}
      403:
        description: Local access only
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.json or {}
    name = data.get("name", "unnamed")
    expires_days = data.get("expires_days", 90)
    raw_key = _generate_api_key(name, expires_days=expires_days)
    expires = None
    if expires_days > 0:
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat().replace("+00:00", "Z")
    return jsonify({"key": raw_key, "name": name, "expires_at": expires, "message": "Save this key - it won't be shown again"})

@ollama_bp.route("/api/keys/<int:key_id>", methods=["DELETE"])
def delete_key(key_id):
    """Delete an API key (local only)
    ---
    tags: [Keys]
    parameters:
      - {name: key_id, in: path, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    conn = _get_db()
    conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    # conn is thread-local persistent — do not close
    if _clear_key_cache:  # L2: 폐기 즉시 반영(60s TTL 지연 제거)
        _clear_key_cache()
    return jsonify({"ok": True})

@ollama_bp.route("/api/keys/<int:key_id>/toggle", methods=["POST"])
def toggle_key(key_id):
    """Toggle API key active/inactive (local only)
    ---
    tags: [Keys]
    parameters:
      - {name: key_id, in: path, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    conn = _get_db()
    conn.execute("UPDATE api_keys SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?", (key_id,))
    conn.commit()
    # conn is thread-local persistent — do not close
    if _clear_key_cache:  # L2: 활성/비활성 즉시 반영(60s TTL 지연 제거)
        _clear_key_cache()
    return jsonify({"ok": True})

@ollama_bp.route("/api/keys/<int:key_id>/reveal")
def reveal_key(key_id):
    """(폐지) API 키 재확인 — 평문 저장을 중단해 더 이상 제공하지 않는다
    ---
    tags: [Keys]
    parameters:
      - {name: key_id, in: path, type: integer, required: true}
    responses:
      410:
        description: "폐지됨 — 키는 발급 시 1회만 표시된다. 분실 시 재발급."
    """
    # v2.124.46: 평문(key_value) 저장 폐지. DB·백업 유출 시 키 재사용을 막기 위함이며,
    # 발급 응답이 원본을 보는 유일한 기회라는 문서화된 계약과도 일치한다.
    # 404가 아닌 410으로 답해 구 클라이언트가 '없는 키'로 오인하지 않게 한다.
    return jsonify({"error": "키 재확인 기능은 폐지되었습니다(평문 미저장). "
                             "분실 시 새 키를 발급하고 기존 키를 폐기하세요."}), 410


@ollama_bp.route("/api/keys/<int:key_id>/expiry", methods=["POST"])
def update_key_expiry(key_id):
    """API 키 만료 기간 연장 또는 무제한 전환 (local only)
    ---
    tags: [Keys]
    parameters:
      - {name: key_id, in: path, type: integer, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            never: {type: boolean, description: "true면 만료 없음(무제한)"}
            days: {type: integer, description: "지금부터 N일 후 만료. 0 또는 never=true는 무제한"}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            expires_at: {type: string, description: "ISO8601 (무제한이면 null)"}
      400: {description: "days가 정수가 아님"}
      403: {description: "Local access only"}
      404: {description: "키 없음"}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.json or {}
    conn = _get_db()
    row = conn.execute("SELECT id FROM api_keys WHERE id=?", (key_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    never = bool(data.get("never"))
    days = data.get("days")
    if never or days in (0, "0", None):
        # never=true 또는 days 미지정/0 → 무제한
        if not never and days is None:
            return jsonify({"error": "days(>0) 또는 never=true 가 필요합니다"}), 400
        expires = None
    else:
        try:
            days = int(days)
        except (TypeError, ValueError):
            return jsonify({"error": "days must be an integer"}), 400
        if days <= 0:
            return jsonify({"error": "days must be > 0 (무제한은 never=true)"}), 400
        # 지금부터 N일 후 만료 (올바른 ISO8601 — 이중 접미사 +00:00Z 버그 회피)
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    conn.execute("UPDATE api_keys SET expires_at=? WHERE id=?", (expires, key_id))
    conn.commit()
    # conn is thread-local persistent — do not close
    # 검증 캐시(_key_cache)는 TTL 60s로 자동 반영 (toggle/delete와 동일 정책)
    return jsonify({"ok": True, "expires_at": expires})

OLLAMA_PLIST = os.path.expanduser("~/Library/LaunchAgents/homebrew.mxcl.ollama.plist")

# ---------------------------------------------------------------------------

@ollama_bp.route("/api/ollama/perf", methods=["GET"])
def get_ollama_perf():
    """Get Ollama performance settings
    ---
    tags: [Config]
    responses:
      200:
        schema:
          type: object
          properties:
            options: {type: array, items: {type: object}}
            current: {type: object}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    # current=Ollama가 **실제로 받는 값**(예전엔 drop-in만 읽어 가려진 값을 보여 줬다).
    # 응답 필드: current(키→값·예전 호출자 호환) · effective(키→{value, source, shadowed}) ·
    #            writable(이 서버에서 화면 저장 가능?) · read_only_reason(불가 사유 — 화면이 그대로 띄운다).
    effective = _effective_ollama_env()
    return jsonify({
        "options": PERF_OPTIONS,
        "current": {k: e["value"] for k, e in effective.items()},
        "effective": effective,
        "writable": _perf_write_block_reason() is None,
        "read_only_reason": _perf_write_block_reason(),
        "server": {
            "platform": _SERVER_ENV.get("platform", "unknown"),
            "has_gpu": _SERVER_ENV.get("has_gpu", False),
            "gpu_name": _SERVER_ENV.get("gpu_name", ""),
            "gpu_vram_mb": _SERVER_ENV.get("gpu_vram_mb", 0),
            "ram_gb": _SERVER_ENV.get("ram_gb", 0),
        }
    })

@ollama_bp.route("/api/ollama/perf", methods=["POST"])
def update_ollama_perf():
    """Update Ollama performance settings (requires restart)
    ---
    tags: [Config]
    parameters:
      - in: body
        name: body
        schema:
          type: object
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            message: {type: string}
            restart_required: {type: boolean}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.json or {}
    env = _read_plist_env()
    effective = {k: e["value"] for k, e in _effective_ollama_env().items()}
    valid_keys = {o["key"] for o in PERF_OPTIONS}
    # 변경 판정은 **실제 값** 기준 — 쓰기 파일 값과 비교하면 가려진 키가 매번 '변경'으로 보인다.
    changes = {k: str(v) for k, v in data.items()
               if k in valid_keys and str(v) != effective.get(k)}
    if changes:
        reason = _perf_write_block_reason(changes.keys())
        if reason:
            # 적용된 것처럼 200을 주지 않는다(예전: 권한 오류 500 / 가려진 키는 무성 무효).
            return jsonify({"ok": False, "error": "read_only", "reason": reason,
                            "keys": sorted(changes)}), 409
        env.update(changes)
        try:
            _write_plist_env(env)
        except OSError as e:
            return jsonify({"ok": False, "error": "write_failed", "reason": str(e)}), 409
    changed = bool(changes)
    return jsonify({"ok": True, "restart_required": changed,
                    "message": "Ollama 재시작 필요 — Settings에서 Restart 클릭" if changed else "변경 없음"})

@ollama_bp.route("/api/ollama/restart", methods=["POST"])
def restart_ollama():
    """Restart Ollama service to apply new settings
    ---
    tags: [Config]
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            output: {type: string}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    if _IS_LINUX:
        result = subprocess.run(["sudo", "systemctl", "restart", "ollama"], capture_output=True, text=True, timeout=30)
    else:
        subprocess.run(["launchctl", "unload", OLLAMA_PLIST], capture_output=True)
        time.sleep(1)
        result = subprocess.run(["launchctl", "load", OLLAMA_PLIST], capture_output=True, text=True)
    _status_cache["ts"] = 0  # 캐시 무효화
    return jsonify({"ok": result.returncode == 0, "output": result.stdout + result.stderr})

_API_ENDPOINT_COUNT = None


def _api_endpoint_count():
    """등록된 `/api` 경로 수(캐시). url_map은 기동 후 불변이므로 한 번만 센다."""
    global _API_ENDPOINT_COUNT
    if _API_ENDPOINT_COUNT is None:
        try:
            from flask import current_app
            _API_ENDPOINT_COUNT = len({r.rule for r in current_app.url_map.iter_rules()
                                       if r.rule.startswith("/api") or r.rule.startswith("/v1")})
        except Exception:  # noqa: BLE001
            _API_ENDPOINT_COUNT = 0
    return _API_ENDPOINT_COUNT


@ollama_bp.route("/api/version")
def api_version():
    """서비스·API 계약 버전 — 연동 시스템의 버전 확인 진입점.

    ⚠️ **응답 크기 정정(2026-08-31)**: 종전에는 changelog 91건을 항상 실어 **98KB**를
    돌려줬다. 버전만 확인하려는 연동 호출에 그 페이로드는 낭비이고(헬스체크가 이 경로를
    때리면 더욱), 실제로 이 레포의 감사에서 드러난 결함이다. 이제 **기본은 요약**이고
    changelog는 `?changelog=true`로 **명시 요청**할 때만 싣는다(관리 콘솔이 그렇게 부른다).

    `version`(서비스 빌드)과 `api_contract`(호환성 경계)는 **다른 축**이다 — 배포마다
    version은 오르지만 계약은 그대로다. 연동 코드가 분기할 축은 `api_contract`다.
    같은 값이 **모든 응답의 `X-API-Version`·`X-API-Contract` 헤더**에도 실린다.
    ---
    tags: [Config]
    parameters:
      - in: query
        name: changelog
        type: boolean
        required: false
        default: false
        description: "true면 릴리즈 changelog 전문을 포함(약 98KB). 기본은 요약만."
    responses:
      200:
        description: "버전 요약(+선택적 changelog)"
        schema:
          type: object
          properties:
            version: {type: string, description: "서비스 빌드 버전", example: "2.125.28"}
            api_contract: {type: string, description: "API 계약 버전 — 하위 호환이 깨질 때만 오른다", example: "1"}
            commit: {type: string, description: "HEAD 커밋 7자리(.git 없으면 생략)", example: "12ae56d"}
            service: {type: string, example: "weru.b-ai-server"}
            endpoints: {type: integer, description: "등록된 /api 경로 수", example: 561}
            docs:
              type: object
              description: "명세·디스커버리 진입점"
              properties:
                swagger_ui: {type: string, example: "/docs"}
                openapi: {type: string, example: "/openapi.json"}
                medical_catalog: {type: string, example: "/api/medical/catalog"}
            changelog:
              type: array
              description: "`?changelog=true`일 때만"
              items: {type: object}
    """
    from core.api_version import API_CONTRACT, git_commit
    body = {
        "version": _VERSION,
        "api_contract": API_CONTRACT,
        "service": "weru.b-ai-server",
        "endpoints": _api_endpoint_count(),
        "docs": {"swagger_ui": "/docs", "openapi": "/openapi.json",
                 "medical_catalog": "/api/medical/catalog"},
    }
    commit = git_commit()
    if commit:
        body["commit"] = commit
    if request.args.get("changelog", "").lower() in ("1", "true", "yes"):
        body["changelog"] = _CHANGELOG
    return jsonify(body)


# ---------------------------------------------------------------------------
# /api/config 시크릿 마스킹 (admin-menu-settings-cleanup D2)
#
# 예전엔 관리자 세션이면 config 전 키를 원문으로 돌려줬다 — s3_secret_key·datago_api_key·
# apple_worker_token·admin_password_hash가 Settings·Models 탭을 열 때마다 브라우저로 갔다.
# 판정은 **명시 목록 ∪ 이름 패턴**: 패턴만 쓰면 webhook URL(URL 자체가 쓰기 권한)을
# 빠뜨리고, 목록만 쓰면 새로 생기는 키를 빠뜨린다.
# ---------------------------------------------------------------------------
import re as _re

_SECRET_CONFIG_KEYS = frozenset({
    "alert_slack_url", "alert_discord_url", "alert_webhook_url",
    "c2pa_cert_path", "c2pa_key_path",
})
_SECRET_KEY_PATTERN = _re.compile(r"(key|secret|token|password|passwd|hash|credential)", _re.I)


def _is_secret_config_key(k):
    """키 이름만으로 판정한다(값을 보지 않는다) — 값 모양으로 판정하면 짧은 토큰·경로를 놓친다.

    오탐(비밀 아닌 키를 가림)은 화면에서 값이 안 보일 뿐이라 허용하고, 미탐(비밀 노출)을 줄이는 쪽으로 기운다.
    이 판정은 GET/POST 응답 마스킹과 POST의 '마스크 되돌림 무시' 양쪽에 쓰인다 — 한쪽만 바꾸지 말 것.
    """
    return k in _SECRET_CONFIG_KEYS or bool(_SECRET_KEY_PATTERN.search(k))


def _mask_secret(v):
    """시크릿 값 → `{"set": bool, "hint": "…끝4자"|None}`.

    hint는 운영자가 "어느 키가 들어 있나"를 알아보는 용도다. 12자 미만이면 끝 4자가 값의 큰 비율이라 주지 않는다.
    """
    if v in (None, ""):
        return {"set": False}
    s = str(v)
    return {"set": True, "hint": ("…" + s[-4:]) if len(s) >= 12 else None}


def _masked_config(cfg):
    """응답용 사본 — 원본 cfg는 건드리지 않는다(저장은 항상 원문으로)."""
    return {k: (_mask_secret(v) if _is_secret_config_key(k) else v) for k, v in cfg.items()}


# 보안을 **완화**하는 변경 — 서버 localhost에서만 허용(운영 결정 2026-09-29, D3).
# 외부 관리자 세션(Caddy 경유 로그인)은 조이는 방향만 가능하다.
def _loosens_security(k, v, cfg):
    """이 값으로 바꾸면 인증이 **약해지는가**. 같은 값 재저장은 완화가 아니다.

    화면은 auth_enabled·local_bypass를 **함께** 보낸다 — 이미 켜진 bypass를 그대로 보낸 것까지 막으면 외부 관리자가
    인증을 켜는 저장조차 못 한다(회귀 `test_external_session_resave_unchanged_is_allowed`).
    `is False`/`is True`로 엄격 비교 — JSON 불리언만 인정한다("false" 문자열 등은 완화로 보지 않지만 저장도
    그대로 되므로, 이 키에 문자열을 보내는 호출자가 생기면 여기부터 다시 볼 것).
    """
    if k == "auth_enabled":
        return v is False and cfg.get("auth_enabled", True) is not False
    if k == "local_bypass":
        return v is True and cfg.get("local_bypass", False) is not True
    return False


@ollama_bp.route("/api/config", methods=["GET"])
def get_config():
    """Get server configuration (local only)
    ---
    tags: [Config]
    responses:
      200:
        schema:
          type: object
          properties:
            auth_enabled: {type: boolean}
            local_bypass: {type: boolean}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    return jsonify(_masked_config(_load_config()))

@ollama_bp.route("/api/config", methods=["POST"])
def update_config():
    """Update server configuration (local only)
    ---
    tags: [Config]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            auth_enabled: {type: boolean}
            local_bypass: {type: boolean}
    responses:
      200:
        schema:
          type: object
          properties:
            auth_enabled: {type: boolean}
            local_bypass: {type: boolean}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.json or {}
    cfg = _load_config()
    local = bool(_is_local_request and _is_local_request())
    blocked = [k for k in ("auth_enabled", "local_bypass")
               if k in data and not local and _loosens_security(k, data[k], cfg)]
    if blocked:
        return jsonify({"error": "local_only",
                        "reason": "인증을 약하게 하는 변경은 서버 localhost에서만 가능합니다",
                        "keys": blocked}), 403
    valid_keys = ("auth_enabled", "local_bypass", "allowed_origins", "bind_host", "bind_port",
                   "alert_slack_url", "alert_discord_url", "alert_webhook_url", "auto_purge_days",
                   "image_retention_days", "image_max_count", "image_max_size_mb",
                   "default_system_prompt_enabled", "default_model",
                   "apple_worker_enabled", "apple_worker_url", "apple_worker_token",
                   "apple_worker_delegate", "gpu_base", "gpu_api_key")
    for k in valid_keys:
        if k not in data:
            continue
        v = data[k]
        if _is_secret_config_key(k):
            # 마스킹된 값을 그대로 돌려보내거나 빈 칸이면 '변경 없음'. 삭제는 {"clear": true}만.
            if isinstance(v, dict):
                if v.get("clear") is True:
                    cfg[k] = None
                continue
            if v in (None, ""):
                continue
        cfg[k] = v
    _save_config(cfg)
    return jsonify(_masked_config(cfg))

# ---------------------------------------------------------------------------
# Original API endpoints
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 설정 개요 — 고급 설정 표(읽기 전용) + 접속 정보 (admin-menu-settings-cleanup Phase 3)
#
# 기본값은 **코드에서 뽑는다**(지어내지 않는다): `DEFAULT_CONFIG` 리터럴 + 소스 전체의
# `<cfg|config…>.get("키", 리터럴)` AST 스캔. 같은 키에 기본값이 여러 개면 전부 보인다 — 곳마다
# 기본값이 다른 것 자체가 결함 후보다(2026-09-29 첫 스캔: default_model = '' / 'qwen2.5:14b').
# 스캔은 첫 호출 1회(파일 ~150개·≈0.7s) 후 캐시. 편집 UI는 만들지 않는다(기획 §1 비목표).
# ---------------------------------------------------------------------------
import ast as _ast
import threading as _threading

_CONFIG_DEFAULTS = None
_CONFIG_DEFAULTS_LOCK = _threading.Lock()
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 화면에서 바꾸는 키 → 위치. 여기 없는 키는 config.json 직접 편집.
# ⚠️ 유지보수: 설정 화면에 편집 칸을 새로 만들면 여기에도 추가해야 고급 설정 표의 '바꾸는 곳'이 맞는다
#    (빠지면 "config.json 직접"으로 보여 운영자가 파일을 열게 된다). 회귀는 대표 키 몇 개만 검사한다.
_CONFIG_EDIT_LOCATION = {
    "auth_enabled": "설정 › 계정·보안", "local_bypass": "설정 › 계정·보안",
    "alert_slack_url": "설정 › 알림", "alert_discord_url": "설정 › 알림", "alert_webhook_url": "설정 › 알림",
    "image_retention_days": "설정 › 저장소", "image_max_count": "설정 › 저장소", "image_max_size_mb": "설정 › 저장소",
    "default_model": "설정 › 모델·엔진", "llm_resident": "설정 › 모델·엔진", "llm_resident_model": "설정 › 모델·엔진",
    "heavy_enabled": "API /api/heavy/settings", "heavy_model": "API /api/heavy/settings",
    "heavy_keep_alive": "API /api/heavy/settings", "heavy_num_gpu": "API /api/heavy/settings",
}


def _scan_config_defaults():
    """{키: [기본값 repr, …]} — 소스에 **적힌** 기본값만. 실행해서 알아내지 않는다(부작용·느림).

    잡는 모양: `DEFAULT_CONFIG = {…}` 리터럴, 받는 쪽 이름에 cfg/config가 들어간 `.get("키", 리터럴)`.
    못 잡는 모양: 헬퍼 함수 경유(`resident_model(cfg)` 등)·변수 기본값·`cfg["키"]` 직접 접근 → 표에서 빈칸.
    리터럴이 아닌 기본값은 "<식>"으로 적는다(있다는 사실은 알리되 값은 지어내지 않는다).
    """
    found = {}

    def add(k, v):
        found.setdefault(k, [])
        if v not in found[k]:
            found[k].append(v)

    paths = [os.path.join(_APP_ROOT, "app.py")]
    for d in ("routes", "core"):
        for dp, _, fs in os.walk(os.path.join(_APP_ROOT, d)):
            paths += [os.path.join(dp, f) for f in fs if f.endswith(".py")]
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                tree = _ast.parse(f.read())
        except Exception:  # noqa: BLE001 — 읽지 못한 파일은 건너뛴다(표는 '모름'을 빈칸으로 보인다)
            continue
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Assign) and any(isinstance(t, _ast.Name) and t.id == "DEFAULT_CONFIG"
                                                     for t in node.targets) and isinstance(node.value, _ast.Dict)):
                for kn, vn in zip(node.value.keys, node.value.values):
                    if isinstance(kn, _ast.Constant):
                        try:
                            add(kn.value, repr(_ast.literal_eval(vn)))
                        except Exception:  # noqa: BLE001
                            add(kn.value, "<식>")
            elif (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute) and node.func.attr == "get"
                  and len(node.args) >= 2 and isinstance(node.args[0], _ast.Constant)
                  and isinstance(node.args[0].value, str)):
                recv = _ast.unparse(node.func.value).lower()
                if "cfg" in recv or "config" in recv:
                    try:
                        add(node.args[0].value, repr(_ast.literal_eval(node.args[1])))
                    except Exception:  # noqa: BLE001
                        add(node.args[0].value, "<식>")
    return found


def _config_defaults():
    global _CONFIG_DEFAULTS
    with _CONFIG_DEFAULTS_LOCK:
        if _CONFIG_DEFAULTS is None:
            _CONFIG_DEFAULTS = _scan_config_defaults()
        return _CONFIG_DEFAULTS


@ollama_bp.route("/api/config/overview", methods=["GET"])
def config_overview():
    """설정 개요 — 전 키(시크릿 마스킹)·코드 기본값·편집 위치 + 접속 정보 (admin, 읽기 전용)
    ---
    tags: [Config]
    responses:
      200:
        schema:
          type: object
          properties:
            keys: {type: array, items: {type: object}}
            connection: {type: object}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    cfg = _load_config()
    defaults = _config_defaults()
    keys = []
    for k in sorted(cfg):
        v = cfg[k]
        keys.append({
            "key": k,
            "value": _mask_secret(v) if _is_secret_config_key(k) else v,
            "secret": _is_secret_config_key(k),
            "defaults": defaults.get(k, []),
            "edit": _CONFIG_EDIT_LOCATION.get(k, "config.json 직접"),
        })
    env = request.environ
    return jsonify({
        "keys": keys,
        "connection": {
            "ingress_mode": cfg.get("ingress_mode"),
            "bind": f"{env.get('SERVER_NAME', '')}:{env.get('SERVER_PORT', '')}",
            "ollama_base": _OLLAMA_BASE,
            "apple_worker_enabled": cfg.get("apple_worker_enabled"),
            "apple_worker_delegate": cfg.get("apple_worker_delegate"),
        },
    })


@ollama_bp.route("/api/status")
def api_status():
    """Get Ollama engine status and loaded models
    ---
    tags: [Ollama]
    responses:
      200:
        schema:
          type: object
          properties:
            running: {type: boolean}
            models: {type: array, items: {type: object}}
            running_models: {type: array, items: {type: object}}
    """
    running = _get_ollama_status()
    models = _ollama_get("/api/tags") if running else {}
    ps = _ollama_get("/api/ps") if running else {}
    # v2.70.0: batch_mode 표시
    import sys as _sys
    _app_mod = _sys.modules.get("app") or _sys.modules.get("__main__")
    result = {
        "running": running,
        "models": models.get("models", []),
        "running_models": ps.get("models", []),
        "image_disabled": IMAGE_DISABLED,
        "version": getattr(_app_mod, "VERSION", ""),
        "batch_mode": getattr(_app_mod, "BATCH_MODE", False),
    }
    return jsonify(result)

@ollama_bp.route("/api/models")
def api_models():
    """List available Ollama models
    ---
    tags: [Ollama]
    responses:
      200:
        schema:
          type: object
          properties:
            models: {type: array, items: {type: object}}
    """
    return jsonify(_ollama_get("/api/tags"))

# ── 경로별 서빙 모델·digest 조회 (규제 대응 R2 확장 — 트윈 자동 기록·감지용) ──
# 하드코딩 표가 아니라 실제 라우팅 함수(_get_medical_model 등)를 호출해 라이브 진실을
# 반영한다 — 활성 설정(heavy 슬롯)·규제 가드(medical_pin_public_models) 변경이 즉시
# 응답에 나타나므로 호출측이 폴링만으로 모델 교체를 감지할 수 있다.
_MODEL_ROUTING_CACHE = {"ts": 0.0, "data": None}
_MODEL_ROUTING_TTL = 300


def _ollama_digests():
    """설치 모델 → digest(12자리) 맵. 실패 시 빈 dict(응답은 digest null로 강등)."""
    try:
        r = http_requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=5)
        return {m.get("name"): (m.get("digest") or "")[:12]
                for m in r.json().get("models", []) if m.get("name")}
    except Exception:  # noqa: BLE001
        return {}


def build_model_routing():
    """경로→모델·digest·정책 스냅샷 조립(순수 조립 — 테스트 가능)."""
    from routes.medical import _get_medical_model, _pin_public, is_custom_model
    from routes.vision import VISION_MODEL, MEDICAL_VISION_MODEL
    from client.rag import EMBED_MODEL
    digests = _ollama_digests()

    def entry(model, policy):
        d = digests.get(model) or digests.get(f"{model}:latest")
        return {"model": model, "digest": d or None,
                "custom": is_custom_model(model), "policy": policy}

    _OVERRIDABLE = "server-routing(자동); 요청 model 파라미터로 고정 가능"
    action_paths = {
        "/api/medical/predict/readmission": "predict_readmission",
        "/api/medical/predict/complication": "predict_complication",
        "/api/medical/predict/adverse-drug": "predict_adverse_drug",
        "/api/medical/reference": "reference",
        "/api/medical/explain": "explain",
        "/api/medical/translate": "translate",
        "/api/medical/summarize": "summarize",
        "/api/medical/analyze": "analyze",
        "/api/medical/reasoning/*": "reasoning_chain",
    }
    routes = {p: entry(_get_medical_model(a), _OVERRIDABLE)
              for p, a in action_paths.items()}
    routes["/api/medical/dur/*"] = {
        "model": None, "digest": None, "custom": False,
        "policy": "deterministic-db(식약처 DUR 정본 조회) — LLM 미사용"}
    routes["/api/medical-law/ask"] = entry(
        "qwen2.5:14b", "server-fixed 기본; model 오버라이드 가능")
    routes["/api/chat"] = entry(
        "qwen2.5:14b", "domain-routing(기본 상주); model 지정 시 고정")
    routes["/api/chat/enhanced(medical)"] = {
        "pipeline": [entry(_pin_public(s["model"]), s["role"])
                     for s in ENHANCED_CHAT_PIPELINES.get("medical", [])],
        "policy": "단계별 모델; single_model 지정 시 전 단계 단일 고정"}
    routes["/api/vision/describe"] = entry(VISION_MODEL, "server-fixed")
    routes["/api/vision/medical/*"] = entry(MEDICAL_VISION_MODEL, "server-fixed")
    routes["/api/rag/*"] = entry(EMBED_MODEL, "embedding(server-fixed)")
    routes["/v1/chat/completions"] = {
        "model": None, "digest": None, "custom": False,
        "policy": "caller-specified — 모델·temperature·top_p·max_tokens·seed 호출자 통제(규제 대응 권장 경로)"}

    try:
        from core.config import load_config
        pin = bool(load_config().get("medical_pin_public_models", False))
    except Exception:  # noqa: BLE001
        pin = False
    try:
        engine_ver = http_requests.get(f"{_OLLAMA_BASE}/api/version",
                                       timeout=3).json().get("version")
    except Exception:  # noqa: BLE001
        engine_ver = None
    try:
        from app import VERSION as _server_ver
    except Exception:  # noqa: BLE001
        _server_ver = None
    return {
        "server_version": _server_ver,
        "engine": {"name": "ollama", "version": engine_ver},
        "pin_public_models": pin,
        "routes": routes,
        # 모델명→digest 직접 조회 맵. /v1은 caller-specified라 경로에 digest가 없으므로,
        # 호출자가 "같은 모델을 쓰는 다른 경로"에서 digest를 추론할 필요 없이 자신이
        # 명시한 모델명으로 바로 대조할 수 있게 한다(그 경로의 모델이 바뀌면 추론이
        # 깨지는 취약점 제거 — 트윈 request4 확인 요청 대응).
        "models": {name: dg for name, dg in sorted(digests.items()) if dg},
        "note": "digest+태그+엔진 버전=판본 3요소. 자동 라우팅 경로는 활성 설정에 따라 "
                "변할 수 있으므로 허가 트래픽은 model 명시 고정 권장. /v1 등 "
                "caller-specified 경로는 models 맵에서 해당 모델명의 digest를 대조할 것.",
    }


# 프롬프트 문자 캡(방어심층 L6) — 전역 20MB 바디 제한만으로는 인증 호출자가 수 MB
# 프롬프트로 단일 워커를 장시간 점유할 수 있다. 전역 num_ctx(8192)로 실제 활용 가능한
# 입력은 ~30KB 수준이라 20만자는 정상 사용에 여유가 크다.
# ⚠️ /v1(OpenAI 호환)은 Ollama에 직접 붙어 이 라우트를 거치지 않으므로 영향 없다.
_MAX_PROMPT_CHARS_DEFAULT = 200_000


def _prompt_cap():
    try:
        return int(_load_config().get("max_prompt_chars", _MAX_PROMPT_CHARS_DEFAULT))
    except Exception:  # noqa: BLE001
        return _MAX_PROMPT_CHARS_DEFAULT


def _prompt_overflow(*parts):
    """합산 길이가 캡 초과면 (총길이, 캡) 아니면 None — 순수 함수(앱 컨텍스트 불요)."""
    cap = _prompt_cap()
    if cap <= 0:
        return None
    total = sum(len(p) for p in parts if isinstance(p, str))
    return (total, cap) if total > cap else None


def _normalize_images(images):
    """요청의 images[] → Ollama가 받는 raw base64 목록(`core.images` 단일 소스).

    `data:` 접두 제거(Ollama는 접두 없는 base64만 받는다) + Ollama가 디코드하지 못하는
    포맷(WEBP) PNG 변환. 둘 다 안 하면 이미지가 **조용히 무시**된다.
    """
    from core.images import normalize_images as _ni
    return _ni(images)


_MODEL_CAPS_CACHE = TTLCache(maxsize=64, ttl=600)


def model_supports_thinking(model, show_fn=None):
    """모델이 thinking을 지원하는가 — `/api/show` capabilities 기준(10분 캐시).

    지원하지 않는 모델에 `think`를 넘기면 Ollama가 **요청 전체를 거절**한다
    (`"qwen2.5:14b" does not support thinking`). think를 전달하기 시작한 이상
    비-thinking 모델을 쓰는 기존 호출자가 그 에러를 맞으면 안 되므로 먼저 확인한다.
    조회 실패 시 True(=전달)로 두어 지원 모델의 기능을 막지 않는다.
    """
    cached = _MODEL_CAPS_CACHE.get(model)
    if cached is not None:
        return cached
    try:
        fn = show_fn or (lambda m: http_requests.post(
            f"{_OLLAMA_BASE}/api/show", json={"model": m}, timeout=5).json())
        caps = fn(model).get("capabilities") or []
        ok = "thinking" in caps
    except Exception:                                    # noqa: BLE001 — 조회 실패는 판단 보류
        return True
    _MODEL_CAPS_CACHE.set(model, ok)
    return ok


def chat_payload(model, messages, data=None, stream=True, supports_thinking=None):
    """`/api/chat` → Ollama 업스트림 페이로드.

    종전에는 `{model, messages, stream}`만 보내 **호출자가 준 필드가 조용히 버려졌다**
    (2026-07-30 그룹웨어 제보 계열):
      · `think` 미전달 → `think:false`를 줘도 사고 과정이 그대로 나옴(V5의 정체는
        "억제가 불완전"이 아니라 **애초에 전달 안 됨**)
      · `options` 미전달 → temperature·num_ctx 지정이 무시됨(/api/generate는 v2.124.32에서
        해소했으나 chat은 그대로였다)
      · `images`의 `data:` 접두 미제거 → Ollama가 이미지를 무시(/api/generate는 v2.124.50에서
        해소·chat만 비대칭)
    조용한 무시는 오류보다 나쁘다 — 호출자는 성공으로 받고 잘못된 답을 쓴다.
    """
    data = data or {}
    norm_msgs = []
    for m in messages or []:
        if isinstance(m, dict) and m.get("images"):
            m = dict(m)
            m["images"] = _normalize_images(m["images"])
            if not m["images"]:
                m.pop("images")
        norm_msgs.append(m)
    payload = {"model": model, "messages": norm_msgs, "stream": bool(stream)}
    if data.get("think") is not None:
        if supports_thinking is None:
            supports_thinking = model_supports_thinking(model)
        if supports_thinking:
            payload["think"] = data["think"]
    if isinstance(data.get("options"), dict) and data["options"]:
        payload["options"] = data["options"]
    if data.get("format"):
        payload["format"] = data["format"]
    if data.get("tools"):
        payload["tools"] = data["tools"]
    return payload


def _too_long(*parts):
    """캡 초과 시 413 응답 튜플, 아니면 None."""
    over = _prompt_overflow(*parts)
    if not over:
        return None
    return jsonify({"error": f"prompt too long: {over[0]} chars > {over[1]}"}), 413


# 대형 프롬프트 컨텍스트 자동 확장 — 절단(truncation) 방지.
# /api/generate는 options를 Ollama로 전달하지 않아 요청이 전역 슬롯 컨텍스트
# (OLLAMA_CONTEXT_LENGTH ÷ OLLAMA_NUM_PARALLEL ≈ 8192 tok)에 묶인다. 이를 넘는 프롬프트는
# n_keep=4로 중간이 조용히 잘려 답변 품질이 떨어진다(2026-07-25 실측: node 호출자 8K~17K tok
# 프롬프트가 limit≈4098로 절단). 프롬프트 크기를 추정해 num_ctx를 상한까지 자동 상향한다.
# ⚠️ num_ctx↑ = KV VRAM↑(num_ctx×NUM_PARALLEL) + 컨텍스트 변경 시 모델 리로드. 목표를 고정
#    상한값으로 두어(가변값은 요청마다 리로드 오실레이션 유발) 리로드를 최소화한다. 16384는
#    /v1(openai_compat) 경로에서 검증된 VRAM-안전 값. 상한 넘는 초대형은 상한까지만(자원 보호).
_GEN_SLOT_CTX = 8192            # 전역 슬롯 컨텍스트 근사(이 이하로 추정되면 미주입=기존 동작)
_GEN_MAX_NUM_CTX_DEFAULT = 16384


def _estimate_tokens(text):
    """토크나이저 없이 보수적(과소추정 회피) 토큰 수 추정 — 순수 함수.

    한글/CJK는 qwen BPE에서 ~1 tok/char 이상, 그 외(영문·공백·기호)는 ~0.3 tok/char.
    임계 판정(상향 여부)에만 쓰므로 과대추정(불필요한 상향)보다 과소추정(잔존 절단)이 더 나쁘다."""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        if ('가' <= ch <= '힣'      # 한글 음절
                or '぀' <= ch <= 'ヿ'   # 가나
                or '一' <= ch <= '鿿'   # 한자
                or '㄰' <= ch <= '㆏'):  # 한글 자모
            cjk += 1
    other = len(text) - cjk
    return int(cjk * 1.2 + other * 0.32)


def _auto_num_ctx(prompt, system=""):
    """대형 프롬프트면 상향할 num_ctx 값, 아니면 None(미주입). 순수 함수(앱 컨텍스트 불요)."""
    try:
        cfg = _load_config() or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    if not cfg.get("generate_auto_num_ctx", True):
        return None
    try:
        cap = int(cfg.get("generate_max_num_ctx", _GEN_MAX_NUM_CTX_DEFAULT))
    except (TypeError, ValueError):
        cap = _GEN_MAX_NUM_CTX_DEFAULT
    if cap <= _GEN_SLOT_CTX:
        return None
    # 출력 여유(×1.1) 포함해 슬롯 컨텍스트에 근접하면 상향.
    est = _estimate_tokens(prompt) + _estimate_tokens(system)
    if est * 1.1 <= _GEN_SLOT_CTX:
        return None
    return cap


@ollama_bp.route("/api/models/residency", methods=["GET", "POST"])
def api_models_residency():
    """기본 LLM 상주 정책 조회·전환 (무재배포 · admin/localhost)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            resident:
              type: boolean
              description: "true=사전 적재(영구 상주) · false=요청 시 탑재(파인튜닝용 VRAM 확보)"
            model:
              type: string
              description: "사전 적재할 모델(미지정=default_model). 채팅 기본 모델과 다르게 둘 수 있다"
    responses:
      200:
        description: 현재 정책 + 적재 상태(전환 시 즉시 반납/예열 결과 포함)
        schema:
          type: object
          properties:
            resident: {}
            model: {}
            default_model: {}
            chat_default_model: {}
            explicit_model: {}
            applied: {}
            loaded_models: {type: array}
            available_models: {}
            note: {}
      400: {description: 잘못된 요청 · 존재하지 않는 모델}
      403: {description: unauthorized}
    """
    from core.llm_residency import is_resident, resident_model

    cfg = _load_config()
    model = resident_model(cfg)

    if request.method == "POST":
        if not _is_admin_request():
            return jsonify({"error": "unauthorized"}), 403
        data = request.json or {}
        if "resident" not in data and "model" not in data:
            return jsonify({"error": "resident (boolean) 또는 model (string) 필요"}), 400

        if "model" in data:
            want_model = (data.get("model") or "").strip()
            if want_model:
                # 없는 모델을 상주로 지정하면 예열이 매번 실패하며 조용히 무효가 된다 → 선검증.
                try:
                    tags = http_requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=10).json()
                    known = {m.get("name") for m in (tags.get("models") or [])}
                except Exception:                        # noqa: BLE001 — 조회 실패 시 검증 생략
                    known = set()
                if known and want_model not in known:
                    return jsonify({"error": f"unknown model: {want_model}",
                                    "available": sorted(known)}), 400
                cfg["llm_resident_model"] = want_model
            else:
                cfg.pop("llm_resident_model", None)       # 빈 값 = default_model로 복귀
        old_model = model
        want = bool(data.get("resident", is_resident(cfg)))
        cfg["llm_resident"] = want
        _save_config(cfg)
        model = resident_model(cfg)
        # 상주 모델을 바꾼 경우 이전 모델은 반납해야 VRAM이 실제로 넘어간다.
        if old_model and old_model != model:
            try:
                http_requests.post(f"{_OLLAMA_BASE}/api/generate",
                                   json={"model": old_model, "prompt": "", "keep_alive": 0},
                                   timeout=60)
            except Exception as e:                       # noqa: BLE001
                logger.warning("이전 상주 모델(%s) 반납 실패: %s", old_model, e)
        # 설정만 바꾸면 이미 상주 중인 모델은 그대로 남는다(keep_alive=-1은 만료가 없다).
        # 전환의 목적이 VRAM 확보이므로 **즉시 반영**한다.
        acted = "none"
        if model:
            try:
                if want:
                    http_requests.post(f"{_OLLAMA_BASE}/api/generate",
                                       json={"model": model, "prompt": "", "keep_alive": -1},
                                       timeout=120)
                    acted = "preloaded"
                else:
                    http_requests.post(f"{_OLLAMA_BASE}/api/generate",
                                       json={"model": model, "prompt": "", "keep_alive": 0},
                                       timeout=60)
                    acted = "unloaded"
            except Exception as e:                       # noqa: BLE001 — 설정 저장은 유지
                logger.warning("residency 전환 적용 실패(%s): %s", acted, e)
                acted = "config_only"
        cfg = _load_config()
    else:
        acted = None

    loaded, available = [], []
    try:
        loaded = (http_requests.get(f"{_OLLAMA_BASE}/api/ps", timeout=5)
                  .json().get("models") or [])
    except Exception:                                    # noqa: BLE001
        pass
    try:                                                 # UI 드롭다운용 목록(크기 오름차순)
        tags = (http_requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=10)
                .json().get("models") or [])
        available = [{"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 1)}
                     for m in sorted(tags, key=lambda m: m.get("size") or 0)]
    except Exception:                                    # noqa: BLE001
        pass
    return jsonify({
        "resident": is_resident(cfg),
        "model": model,                                   # 사전 적재 대상
        "default_model": model,                           # 하위호환(구 필드명)
        "chat_default_model": cfg.get("default_model", ""),
        "explicit_model": cfg.get("llm_resident_model") or None,
        "applied": acted,
        "loaded_models": [{"name": m.get("name"),
                           "vram_gb": round((m.get("size_vram") or 0) / 1e9, 2),
                           "expires_at": m.get("expires_at")} for m in loaded],
        "available_models": available,
        "note": ("요청 시 탑재 — 유휴 시 OLLAMA_KEEP_ALIVE 후 자동 반납(첫 요청은 적재 지연)"
                 if not is_resident(cfg) else "사전 적재 — 첫 토큰 지연 없음(VRAM 상시 점유)"),
    })


@ollama_bp.route("/api/models/routing")
def api_models_routing():
    """경로별 서빙 모델·digest 조회 (규제 대응 — 모델 교체 자동 감지용)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: query
        name: fresh
        type: string
        description: "truthy면 캐시(5분) 우회"
    responses:
      200:
        description: server_version·engine·pin_public_models·routes(경로별 model/digest/custom/policy)
    """
    now = time.time()
    if (_MODEL_ROUTING_CACHE["data"] is not None
            and now - _MODEL_ROUTING_CACHE["ts"] < _MODEL_ROUTING_TTL
            and not request.args.get("fresh")):
        return jsonify(_MODEL_ROUTING_CACHE["data"])
    data = build_model_routing()
    _MODEL_ROUTING_CACHE["ts"] = now
    _MODEL_ROUTING_CACHE["data"] = data
    return jsonify(data)


@ollama_bp.route("/api/pull", methods=["POST"])
def api_pull():
    """Pull/download a model (streaming NDJSON)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string, example: "qwen2.5:7b"}
    responses:
      200:
        description: NDJSON progress stream
      401:
        description: Unauthorized
    """
    # 🔴 v2.125.18: 파괴적 fleet 관리는 admin 전용이어야 한다 — 임의 유효 키가 코딩·의료·비전·
    #    agent 경로를 깰 수 있었다("fleet은 전부 라이브 배선" 불변식 위반). 형제 /api/keys·/api/config·
    #    service/restart는 이미 게이트돼 있고 여기 둘만 빠져 있었다(같은 클래스 전 경로 쓸기).
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    model = request.json.get("model", "")
    if not model:
        return jsonify({"error": "model name required"}), 400

    def stream():
        try:
            r = http_requests.post(
                f"{_OLLAMA_BASE}/api/pull",
                json={"model": model, "stream": True},
                stream=True, timeout=1800,
            )
            for line in r.iter_lines():
                if line:
                    yield line.decode() + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return Response(stream(), mimetype="application/x-ndjson")

@ollama_bp.route("/api/delete", methods=["POST"])
def api_delete():
    """Delete a model
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string, example: "qwen2.5:7b"}
    responses:
      200:
        description: Model deleted
      401:
        description: Unauthorized
    """
    if not _is_admin_request():      # v2.125.18: 파괴적 — admin 전용(위 /api/pull 주석 참조)
        return jsonify({"error": "unauthorized"}), 403
    model = request.json.get("model", "")
    if not model:
        return jsonify({"error": "model name required"}), 400
    return jsonify(_ollama_delete("/api/delete", {"model": model}))




# ---------------------------------------------------------------------------
# Fact Check API — 글에 포함된 수치/날짜/사실관계 검증
# ---------------------------------------------------------------------------

FACT_CHECK_SYSTEM = """당신은 팩트 체크 전문가입니다. 반드시 한국어로만 답변하세요.

주어진 글을 분석하여 다음을 검증하세요:
1. 수치/통계 (금액, 비율, 인원 등)
2. 날짜/시기 (연도, 기간, 일정 등)
3. 사실관계 (인물, 장소, 사건, 법률 등)
4. 인용/출처 (정확한 인용인지)

각 항목에 대해 다음 형식으로 JSON 배열을 반환하세요:
[
  {
    "claim": "검증 대상 문구 (원문 그대로)",
    "verdict": "correct" | "incorrect" | "unverifiable" | "misleading",
    "confidence": 0.0~1.0,
    "explanation": "판단 근거 (한국어)",
    "suggestion": "수정 제안 (incorrect/misleading일 때만)"
  }
]

검증할 수 없는 주관적 의견은 제외하세요.
반드시 유효한 JSON 배열만 출력하세요. 다른 텍스트를 포함하지 마세요."""

@ollama_bp.route("/api/ai/fact-check", methods=["POST"])
def api_fact_check():
    """Fact-check text content — verify claims, numbers, dates
    ---
    tags: [AI Writing]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "검증할 글 본문"}
            model: {type: string, default: "qwen2.5:14b"}
            context: {type: string, description: "추가 컨텍스트 (카페명, 카테고리 등)"}
    responses:
      200:
        description: 팩트 체크 결과
        schema:
          type: object
          properties:
            claims:
              type: array
              items:
                type: object
                properties:
                  claim: {type: string}
                  verdict: {type: string, enum: [correct, incorrect, unverifiable, misleading]}
                  confidence: {type: number}
                  explanation: {type: string}
                  suggestion: {type: string}
            summary:
              type: object
              properties:
                total: {type: integer}
                correct: {type: integer}
                incorrect: {type: integer}
                unverifiable: {type: integer}
                misleading: {type: integer}
                score: {type: number, description: "신뢰도 점수 0~100"}
      400:
        description: text 누락
    """
    import json as _json

    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    if len(text) > 50000:
        return jsonify({"error": "text exceeds 50KB limit"}), 413

    model = data.get("model", "qwen2.5:14b")
    context = data.get("context", "")

    prompt = f"다음 글의 사실관계를 검증하세요:\n\n{text}"
    if context:
        prompt = f"[컨텍스트: {context}]\n\n{prompt}"

    try:
        r = http_requests.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model,
            "prompt": prompt,
            "system": FACT_CHECK_SYSTEM,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4000},
        }, timeout=120)
        raw = r.json().get("response", "")

        # Parse JSON from response
        # Try to extract JSON array from response
        claims = []
        try:
            # Direct parse
            claims = _json.loads(raw)
        except _json.JSONDecodeError:
            # Try to find JSON array in response
            import re
            match = re.search(r'\[\s*\{.*\}\s*\]', raw, re.DOTALL)
            if match:
                try:
                    claims = _json.loads(match.group())
                except _json.JSONDecodeError:
                    pass

        if not isinstance(claims, list):
            claims = []

        # Validate and clean claims
        valid_claims = []
        for c in claims:
            if isinstance(c, dict) and "claim" in c:
                verdict = c.get("verdict", "unverifiable")
                if verdict not in ("correct", "incorrect", "unverifiable", "misleading"):
                    verdict = "unverifiable"
                valid_claims.append({
                    "claim": str(c.get("claim", "")),
                    "verdict": verdict,
                    "confidence": min(1.0, max(0.0, float(c.get("confidence", 0.5)))),
                    "explanation": str(c.get("explanation", "")),
                    "suggestion": str(c.get("suggestion", "")) if verdict in ("incorrect", "misleading") else "",
                })

        # Summary
        summary = {
            "total": len(valid_claims),
            "correct": sum(1 for c in valid_claims if c["verdict"] == "correct"),
            "incorrect": sum(1 for c in valid_claims if c["verdict"] == "incorrect"),
            "unverifiable": sum(1 for c in valid_claims if c["verdict"] == "unverifiable"),
            "misleading": sum(1 for c in valid_claims if c["verdict"] == "misleading"),
        }
        if summary["total"] > 0:
            summary["score"] = round(
                (summary["correct"] / summary["total"]) * 100
                - (summary["incorrect"] / summary["total"]) * 50
                - (summary["misleading"] / summary["total"]) * 25,
                1
            )
        else:
            summary["score"] = 0

        return jsonify({
            "claims": valid_claims,
            "summary": summary,
            "model": model,
            "text_length": len(text),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 503


@ollama_bp.route("/api/embed", methods=["POST"])
def api_embed():
    """Generate embeddings
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model, input]
          properties:
            model: {type: string, example: "nomic-embed-text"}
            input: {type: string, description: "Text to embed (string or array)"}
    responses:
      200:
        description: Embedding vectors
    """
    data = request.json or {}
    if not data.get("model") or not data.get("input"):
        return jsonify({"error": "model and input required"}), 400
    try:
        r = http_requests.post(f"{_OLLAMA_BASE}/api/embed", json=data, timeout=60)
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@ollama_bp.route("/api/embeddings", methods=["POST"])
def api_embeddings():
    """Generate embeddings (legacy endpoint)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model, prompt]
          properties:
            model: {type: string, example: "nomic-embed-text"}
            prompt: {type: string}
    responses:
      200:
        description: Embedding vector
    """
    data = request.json or {}
    if not data.get("model") or not data.get("prompt"):
        return jsonify({"error": "model and prompt required"}), 400
    try:
        r = http_requests.post(f"{_OLLAMA_BASE}/api/embeddings", json=data, timeout=60)
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@ollama_bp.route("/api/generate", methods=["POST"])
@heavy_limit("30/minute")
def api_generate():
    """Generate text (streaming NDJSON)

    Ollama 네이티브 생성의 **범용 프록시**다. 응답은 NDJSON 스트림으로 토큰 단위 전달된다.
    🔑 **본문은 raw로 통과한다** — 한국어 필터를 걸지 않는다. 번역·코드·영문 출력 같은
    비한국어 사용례를 보존하기 위한 의도된 계약이며 회귀로 고정돼 있다
    (`tests/test_generate_raw_passthrough.py`). 한국어 강제가 필요하면 상위 도메인 API를 쓴다.
    대형 시스템 프롬프트로 무성 절단이 우려되면 `options.num_ctx`를 명시한다.
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            model: {type: string, example: "qwen2.5:7b", description: "모델명 (생략 시 기본 모델 사용)"}
            prompt: {type: string, example: "Why is the sky blue?"}
            system: {type: string, description: "시스템 프롬프트 (생략 시 기본 한국어 프롬프트 자동 적용)"}
            images:
              type: array
              description: "비전 모델 입력. raw base64 또는 data URI(접두는 서버가 제거). PNG·JPEG·GIF·WEBP(WEBP는 서버가 PNG로 변환). 원격 URL 미지원(SSRF 정책)"
              items: {type: string}
    responses:
      200:
        description: NDJSON stream of generated tokens
      413:
        description: "본문/프롬프트 상한 초과(JSON 바디). ⚠️외부 경유는 게이트웨이(nginx 10MB)가 먼저 HTML 413을 반환 — 상태코드로 판별할 것"
      401:
        description: Unauthorized
    """
    data = request.json
    model = data.get("model", "") or _load_config().get("default_model", "")
    prompt = data.get("prompt", "")
    system = data.get("system", "")
    if not model or not prompt:
        return jsonify({"error": "model and prompt required (set default_model in config)"}), 400
    over = _too_long(prompt, system)
    if over:
        return over

    payload = {"model": model, "prompt": prompt, "stream": True}
    # 비전: images[]를 그대로 흘려보낸다. 종전에는 페이로드에 넣지 않아 **조용히 무시**됐고,
    # 모델이 "이미지가 포함되어 있지 않습니다"라고 답해 호출자가 원인을 알 수 없었다
    # (그룹웨어 제보). data: URI 접두는 Ollama가 못 받으므로 제거한다.
    imgs = _normalize_images(data.get("images"))
    if imgs:
        payload["images"] = imgs
    if system:
        payload["system"] = system
    else:
        cfg = _load_config()
        if cfg.get("default_system_prompt_enabled", True):
            payload["system"] = _build_system_prompt()

    # 대형 프롬프트는 전역 슬롯 컨텍스트에서 절단되므로 num_ctx를 자동 상향(절단 방지).
    nc = _auto_num_ctx(prompt, payload.get("system", ""))
    if nc:
        payload["options"] = {"num_ctx": nc}

    def stream():
        try:
            r = http_requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                json=payload, stream=True, timeout=600,
            )
            for line in r.iter_lines():
                if line:
                    yield line.decode() + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return Response(stream(), mimetype="application/x-ndjson")

# ---------------------------------------------------------------------------
# Chat Domain Routing (v2.49.0)
# ---------------------------------------------------------------------------

CHAT_DOMAIN_KEYWORDS = {
    "medical": ["증상", "진단", "약물", "처방", "환자", "통증", "혈압", "수술", "질환", "복용",
                "symptom", "diagnosis", "drug", "patient", "medical", "clinical"],
    "legal": ["근로기준법", "해고", "퇴직금", "연차", "초과근무", "임금", "노동", "법률", "계약",
              "규정", "취업규칙", "법 위반"],
    "coding": ["코드", "함수", "에러", "버그", "API", "프로그래밍", "python", "javascript",
               "sql", "code", "function", "error", "debug", "리팩토링", "테스트"],
    "writing": ["글쓰기", "블로그", "SEO", "작성해", "기사", "칼럼", "에세이", "써줘", "작문",
                "copywriting", "tone", "draft", "리뷰 작성", "후기 작성"],
    # v2.69.0 신규 도메인
    "reasoning": ["추론", "논리", "분석해", "비교해", "왜", "근거", "이유", "증명", "논증",
                  "reasoning", "logic", "analyze", "compare", "prove", "evidence", "why"],
    "agent": ["도구", "실행해", "자동화", "워크플로우", "파이프라인", "에이전트",
              "tool", "execute", "automate", "workflow", "pipeline", "agent"],
}

CHAT_DOMAIN_MODELS = {
    "medical": {"ko": "werub-medical:7b", "en": "thewindmom/llama3-med42-8b", "fallback": "qwen2.5:14b"},
    "legal":   {"ko": "qwen2.5:14b", "en": "qwen2.5:14b", "fallback": "exaone3.5:7.8b"},
    "coding":  {"ko": "qwen3-coder:30b", "en": "qwen3-coder:30b", "fallback": "qwen2.5-coder:14b"},
    "writing": {"ko": "exaone-deep:7.8b", "en": "gemma4:latest", "fallback": "dnotitia/dna:latest"},
    "vision":  {"default": "qwen3-vl:8b", "medical": "medgemma:4b", "ocr": "deepseek-ocr:latest", "fallback": "minicpm-v:latest"},
    "general": {"ko": "qwen2.5:14b", "en": "qwen2.5:14b", "fallback": "qwen2.5:7b"},
    # v2.69.0 신규 모델 도메인
    "reasoning": {"ko": "qwen3:14b", "en": "gpt-oss:20b", "fallback": "qwen3:8b"},
    "agent":    {"default": "gemma4:26b", "fast": "gemma4:latest", "fallback": "qwen3:14b"},
}

DOMAIN_SYSTEM_PROMPTS = {
    "medical": "당신은 의료 AI 어시스턴트입니다. 정확한 의학 정보를 제공하되, 근거 없는 진단이나 처방을 하지 마세요. 응답 끝에 면책 문구를 포함하세요: '이 정보는 AI 참고용이며, 실제 진료를 대체하지 않습니다.'",
    "legal": "당신은 한국 노동법 전문 AI입니다. 근로기준법 등 한국 법률 기반으로 답변하세요. '법적 조언이 아닌 법률 정보 제공'임을 명시하세요.",
    "coding": "당신은 프로그래밍 전문 AI입니다. 코드 블록은 적절한 언어 태그와 함께 제공하세요. 보안 취약점이 있으면 경고하세요.",
    "writing": "당신은 한국어 글쓰기 전문 AI입니다. 구조적이고 논리적인 글을 작성합니다. 목적과 독자에 맞는 톤앤매너를 사용하세요.",
    "vision": "당신은 이미지 분석 AI입니다. 이미지를 상세히 설명하고 사용자의 질문에 답변하세요.",
    "reasoning": "당신은 논리 추론 전문 AI입니다. 단계별로 사고하며, 근거를 명확히 제시하세요. 결론에 도달하기까지의 추론 과정을 투명하게 보여주세요.",
    "agent": "당신은 도구 활용 전문 AI입니다. 사용자의 요청을 분석하여 적절한 도구를 선택하고 실행 계획을 수립하세요.",
}

MODEL_CONTEXT_LIMITS = {
    "qwen2.5:14b": 32000, "qwen2.5:7b": 32000,
    "werub-medical:7b": 32000, "werubmedv2:latest": 32000,
    "exaone-deep:7.8b": 32000, "exaone3.5:7.8b": 32000,
    "dnotitia/dna:latest": 32000,
    "thewindmom/llama3-med42-8b": 8000,
    "deepseek-coder-v2:16b": 16000,
    "qwen2.5-coder:14b": 32000,
    "gemma4:latest": 32000,
    "qwen3-vl:8b": 32000,
    "minicpm-v:latest": 8000,
    "deepseek-ocr:latest": 8000,
    "medgemma:4b": 8000,
    "MedAIBase/MedGemma1.5:4b": 8000,
    "meditron:latest": 4000,
    "medllama2:latest": 4000,
    # v2.69.0 신규 모델
    "gpt-oss:20b": 128000,
    "gemma4:26b": 128000,
    "qwen3:14b": 32000,
    "qwen3-coder:30b": 32000,
}

# 영어 전용 모델 (한국어 품질 검증 스킵)
_CHAT_EN_ONLY_MODELS = {
    "thewindmom/llama3-med42-8b", "medllama2:latest", "meditron:latest",
    "koesn/llama3-openbiollm-8b",
    "monotykamary/medichat-llama3",
}


def _classify_chat_domain(user_msg, images=None):
    """사용자 메시지 → 도메인 분류 (키워드 기반)"""
    if images:
        return "vision", 1.0

    msg = user_msg.lower()
    scores = {}
    for domain, keywords in CHAT_DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in msg)
        if score > 0:
            scores[domain] = score

    if not scores:
        return "general", 1.0

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best = sorted_scores[0][0]
    confidence = sorted_scores[0][1] / (sum(scores.values()) + 1)

    # 신뢰도 < 0.4 또는 동점 → general
    if confidence < 0.4:
        return "general", confidence
    if len(sorted_scores) >= 2 and sorted_scores[0][1] == sorted_scores[1][1]:
        return "general", 0.5

    return best, confidence


def _select_chat_model(domain, lang="ko", vision_subtype=None):
    """도메인+언어 기반 채팅 모델 선택 + 폴백"""
    config = CHAT_DOMAIN_MODELS.get(domain, CHAT_DOMAIN_MODELS["general"])

    if domain == "vision":
        model = config.get(vision_subtype, config["default"])
    else:
        model = config.get(lang, config.get("ko"))

    # 설치 확인 + 폴백
    from routes.medical import _check_model_installed
    if _check_model_installed(model):
        return model, "domain_routing"
    fallback = config.get("fallback", "qwen2.5:14b")
    return fallback, "fallback"


def _detect_vision_subtype(user_msg):
    """비전 서브타입 감지"""
    msg = user_msg.lower()
    if any(k in msg for k in ["x-ray", "ct ", "mri", "영상", "판독", "의료", "medical"]):
        return "medical"
    if any(k in msg for k in ["ocr", "텍스트", "문서", "글자", "읽어", "추출"]):
        return "ocr"
    return "default"


def _is_ko_input(text):
    """한국어 비율 30% 이상이면 True"""
    if not text:
        return True
    ko_count = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    return ko_count / max(len(text), 1) > 0.3


def _should_skip_korean_check(domain, model):
    """도메인/모델에 따라 한국어 품질 검증 스킵"""
    if domain == "coding":
        return True
    if model in _CHAT_EN_ONLY_MODELS:
        return True
    return False


def _adjust_history_for_model(messages, model):
    """모델 컨텍스트에 맞춰 히스토리 자동 축소"""
    limit = MODEL_CONTEXT_LIMITS.get(model, 4000)
    estimated_tokens = sum(len(m.get("content", "")) // 3 for m in messages)

    if estimated_tokens <= limit * 0.8:
        return messages

    # system + 최근 3턴만 유지
    system_msgs = [m for m in messages if m.get("role") == "system"]
    recent = [m for m in messages if m.get("role") != "system"][-6:]
    return system_msgs + recent


# ---------------------------------------------------------------------------
# LLM Gateway — 외부 모델 연동 (v2.51.0)
# ---------------------------------------------------------------------------

_llm_gateway = None

def _get_gateway():
    global _llm_gateway
    if _llm_gateway is None:
        providers_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "providers.json")
        from core.llm_gateway import LLMGateway
        _llm_gateway = LLMGateway(config_path=providers_path, ollama_base=_OLLAMA_BASE)
    return _llm_gateway


@ollama_bp.route("/api/providers")
def api_providers():
    """외부 LLM 프로바이더 목록
    ---
    tags: [Providers]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            providers: {}
    """
    gw = _get_gateway()
    return jsonify({"providers": gw.get_providers()})


@ollama_bp.route("/api/providers/models")
def api_provider_models():
    """전체 사용 가능 모델 (로컬+외부)
    ---
    tags: [Providers]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            models: {}
    """
    gw = _get_gateway()
    return jsonify({"models": gw.get_all_models()})


@ollama_bp.route("/api/providers/<name>/enable", methods=["POST"])
def api_enable_provider(name):
    """프로바이더 활성화
    ---
    tags: [Providers]
    security: [{Bearer: []}]
    parameters:
      - {name: name, in: path, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            api_key: {type: string, description: "API 키 (환경변수에 저장)"}
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            ok: {type: boolean}
            provider: {}
            status: {type: string}
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
    """
    data = request.json or {}
    gw = _get_gateway()
    ok, msg = gw.enable_provider(name, data.get("api_key"))
    if ok:
        return jsonify({"ok": True, "provider": name, "status": "enabled"})
    return jsonify({"error": msg}), 400


@ollama_bp.route("/api/providers/<name>/disable", methods=["POST"])
def api_disable_provider(name):
    """프로바이더 비활성화
    ---
    tags: [Providers]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            ok: {type: boolean}
            provider: {}
            status: {type: string}
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
    """
    gw = _get_gateway()
    ok, msg = gw.disable_provider(name)
    if ok:
        return jsonify({"ok": True, "provider": name, "status": "disabled"})
    return jsonify({"error": msg}), 400


@ollama_bp.route("/api/providers/<name>/test", methods=["POST"])
def api_test_provider(name):
    """프로바이더 연결 테스트
    ---
    tags: [Providers]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
    """
    gw = _get_gateway()
    result = gw.test_provider(name)
    return jsonify(result)


@ollama_bp.route("/api/providers/generate", methods=["POST"])
def api_provider_generate():
    """통합 Generate — 모델명으로 프로바이더 자동 선택
    ---
    tags: [Providers]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model, prompt]
          properties:
            model: {type: string, description: "모델명 (로컬 또는 외부)"}
            prompt: {type: string}
            system: {type: string}
            options: {type: object}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "model and prompt required"}}
    """
    data = request.json or {}
    model = data.get("model", "").strip()
    prompt = data.get("prompt", "").strip()
    if not model or not prompt:
        return jsonify({"error": "model and prompt required"}), 400

    gw = _get_gateway()
    is_ko = _is_ko_input(prompt)
    result = gw.generate(model, prompt, data.get("system", ""), data.get("options"), is_korean=is_ko)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Custom Orchestration — 사용자 정의 파이프라인 (v2.49.0)
# ---------------------------------------------------------------------------

import os as _os

_PIPELINES_PATH = None  # init_ollama에서 설정

# 동시 실행 제어 (v2.50.0) — VRAM OOM 방지
import threading as _orch_threading
_orchestration_semaphore = _orch_threading.Semaphore(1)
_async_runs = {}  # {run_id: {"status": ..., "result": ..., "stages": [...]}}
_async_runs_lock = _orch_threading.Lock()

def _cleanup_old_runs(days=30):
    """30일 초과 실행 이력 자동 정리"""
    try:
        conn = _get_db()
        deleted = conn.execute(
            "DELETE FROM orchestration_runs WHERE created_at < datetime('now', ?)",
            (f'-{days} days',)).rowcount
        conn.commit()
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} orchestration runs older than {days} days")
    except Exception as e:
        logger.debug(f"orchestration cleanup failed: {e}")

def _get_pipelines_path():
    global _PIPELINES_PATH
    if _PIPELINES_PATH is None:
        _PIPELINES_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "custom_pipelines.json")
    return _PIPELINES_PATH

def _load_custom_pipelines():
    path = _get_pipelines_path()
    if _os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_custom_pipelines(pipelines):
    path = _get_pipelines_path()
    with open(path, "w") as f:
        json.dump(pipelines, f, indent=2, ensure_ascii=False)


@ollama_bp.route("/api/orchestration/pipelines", methods=["GET"])
def api_list_pipelines():
    """커스텀 파이프라인 목록 조회
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    responses:
      200:
        description: 저장된 파이프라인 목록 + 기본 제공 파이프라인
        schema:
          type: object
          properties:
            builtin: {type: object}
            custom: {type: object}
    """
    custom = _load_custom_pipelines()
    builtin_names = list(ENHANCED_CHAT_PIPELINES.keys())
    return jsonify({
        "builtin": {name: [{"model": s["model"], "role": s["role"]} for s in steps]
                     for name, steps in ENHANCED_CHAT_PIPELINES.items()},
        "custom": {name: {"description": p.get("description", ""), "stages": len(p.get("stages", [])),
                           "created_at": p.get("created_at", "")}
                    for name, p in custom.items()},
    })


@ollama_bp.route("/api/orchestration/pipeline/<name>", methods=["GET"])
def api_get_pipeline(name):
    """파이프라인 상세 조회
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: name, in: path, type: string, required: true}
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
    """
    # builtin 먼저
    if name in ENHANCED_CHAT_PIPELINES:
        return jsonify({"name": name, "type": "builtin",
                         "stages": ENHANCED_CHAT_PIPELINES[name]})
    custom = _load_custom_pipelines()
    if name in custom:
        return jsonify({"name": name, "type": "custom", **custom[name]})
    return jsonify({"error": f"pipeline '{name}' not found"}), 404


@ollama_bp.route("/api/orchestration/pipeline", methods=["POST"])
def api_create_pipeline():
    """커스텀 파이프라인 생성
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name, stages]
          properties:
            name: {type: string, description: "파이프라인 이름 (영문, 하이픈 허용)"}
            description: {type: string, description: "설명"}
            stages:
              type: array
              description: "단계별 모델+역할+시스템 프롬프트"
              items:
                type: object
                required: [model, role]
                properties:
                  model: {type: string, description: "Ollama 모델명"}
                  role: {type: string, description: "이 단계의 역할 설명"}
                  system: {type: string, description: "시스템 프롬프트 (생략 시 기본)"}
                  temperature: {type: number, default: 0.4}
                  num_predict: {type: integer, default: 2048}
    responses:
      200:
        description: 파이프라인 생성 완료
        schema:
          type: object
          properties:
            ok: {type: boolean}
            name: {}
            stages: {type: integer}
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if name in ENHANCED_CHAT_PIPELINES:
        return jsonify({"error": f"'{name}' is a builtin pipeline, choose another name"}), 400

    stages = data.get("stages", [])
    if not stages or len(stages) < 1:
        return jsonify({"error": "at least 1 stage required"}), 400
    if len(stages) > 5:
        return jsonify({"error": "maximum 5 stages allowed"}), 400

    for i, s in enumerate(stages):
        if not s.get("model") or not s.get("role"):
            return jsonify({"error": f"stage {i+1}: model and role are required"}), 400

    from datetime import datetime, timezone
    pipeline = {
        "description": data.get("description", ""),
        "stages": [{
            "model": s["model"],
            "role": s["role"],
            "system": s.get("system", ""),
            "temperature": s.get("temperature", 0.4),
            "num_predict": s.get("num_predict", 2048),
            "on_fail": s.get("on_fail", "continue"),
            "retry": s.get("retry", 0),
            "fallback_model": s.get("fallback_model"),
        } for s in stages],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    custom = _load_custom_pipelines()
    custom[name] = pipeline
    _save_custom_pipelines(custom)

    return jsonify({"ok": True, "name": name, "stages": len(stages)})


@ollama_bp.route("/api/orchestration/pipeline/<name>", methods=["DELETE"])
def api_delete_pipeline(name):
    """커스텀 파이프라인 삭제
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: name, in: path, type: string, required: true}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "builtin pipeline cannot be deleted"}}
      404:
        description: "대상을 찾을 수 없음"
    """
    if name in ENHANCED_CHAT_PIPELINES:
        return jsonify({"error": "builtin pipeline cannot be deleted"}), 400
    custom = _load_custom_pipelines()
    if name not in custom:
        return jsonify({"error": f"pipeline '{name}' not found"}), 404
    del custom[name]
    _save_custom_pipelines(custom)
    return jsonify({"ok": True, "deleted": name})


def _load_pipeline_stages(pipeline_name):
    """파이프라인 이름 → (stages, type) 반환"""
    if pipeline_name in ENHANCED_CHAT_PIPELINES:
        return ENHANCED_CHAT_PIPELINES[pipeline_name], "builtin"
    custom = _load_custom_pipelines()
    if pipeline_name in custom:
        return custom[pipeline_name]["stages"], "custom"
    return None, None


def _execute_pipeline(stages, user_input, variables=None, debug=False):
    """파이프라인 실행 엔진 — 단계별 에러 처리 + debug 출력 + 한국어 후처리
    중국어/일본어 혼입은 입력 언어와 무관하게 항상 제거."""
    import time as _time
    result_stages = []
    total_tokens = 0
    total_ms = 0
    current_text = user_input
    errors = []
    status = "completed"
    is_ko = True  # 오케스트레이션은 항상 force_korean 적용 (중국어/일본어 혼입 방지)

    for i, step in enumerate(stages):
        model = step["model"]
        role = step["role"]
        system = step.get("system", "")
        temp = step.get("temperature", 0.4)
        num_predict = step.get("num_predict", 2048)
        on_fail = step.get("on_fail", "continue")
        retry_count = step.get("retry", 0)
        fallback_model = step.get("fallback_model")

        if variables and system:
            for k, v in variables.items():
                system = system.replace(f"{{{k}}}", str(v))

        if i == 0:
            prompt = current_text
        else:
            prompt = f"[이전 단계 결과]\n{current_text}\n\n[원래 입력]\n{user_input}\n\n위 결과를 바탕으로 {role}을 수행하세요."

        stage_result = _run_single_stage(model, prompt, system, temp, num_predict, apply_korean=is_ko)

        # 실패 시 재시도 + 폴백
        if stage_result["status"] == "failed" and retry_count > 0:
            for _ in range(retry_count):
                stage_result = _run_single_stage(model, prompt, system, temp, num_predict, apply_korean=is_ko)
                if stage_result["status"] == "success":
                    break

        if stage_result["status"] == "failed" and fallback_model:
            fb = _run_single_stage(fallback_model, prompt, system, temp, num_predict, apply_korean=is_ko)
            if fb["status"] == "success":
                stage_result = fb
                stage_result["fallback_used"] = fallback_model

        stage_entry = {
            "stage": i + 1, "role": role, "model": stage_result.get("model_used", model),
            "tokens": stage_result["tokens"], "duration_ms": stage_result["duration_ms"],
            "status": stage_result["status"],
        }
        if debug:
            stage_entry["output"] = stage_result["response"]
        if stage_result.get("fallback_used"):
            stage_entry["fallback_used"] = stage_result["fallback_used"]
        if stage_result.get("error"):
            stage_entry["error"] = stage_result["error"]
            errors.append({"stage": i + 1, "error": stage_result["error"],
                           "resolved": stage_result["status"] == "success"})

        if stage_result["status"] == "success":
            current_text = stage_result["response"]
        elif on_fail == "abort":
            status = "partial"
            result_stages.append(stage_entry)
            total_tokens += stage_result["tokens"]; total_ms += stage_result["duration_ms"]
            break
        elif on_fail == "skip":
            stage_entry["status"] = "skipped"
        # continue: 에러 텍스트가 다음 단계로 전달됨

        total_tokens += stage_result["tokens"]
        total_ms += stage_result["duration_ms"]
        result_stages.append(stage_entry)

    if errors and status == "completed":
        status = "completed_with_errors"

    # 최종 결과 한국어 후처리
    if is_ko and current_text:
        from core.korean import force_korean
        current_text = force_korean(current_text)

    return {
        "result": current_text, "status": status,
        "stages": result_stages, "errors": errors,
        "total_tokens": total_tokens, "total_duration_ms": total_ms,
    }


def _run_single_stage(model, prompt, system, temp, num_predict, apply_korean=False):
    """단일 단계 실행 — apply_korean=True 시 force_korean 적용"""
    import time as _time
    start = _time.time()
    try:
        r = http_requests.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False,
            "options": {"temperature": temp, "num_predict": num_predict},
        }, timeout=120)
        elapsed = int((_time.time() - start) * 1000)
        if r.status_code != 200:
            return {"status": "failed", "error": f"HTTP {r.status_code}: {r.text[:200]}",
                    "response": "", "tokens": 0, "duration_ms": elapsed, "model_used": model}
        d = r.json()
        if d.get("error"):
            return {"status": "failed", "error": d["error"],
                    "response": "", "tokens": 0, "duration_ms": elapsed, "model_used": model}
        response = d.get("response", "")
        tokens = d.get("eval_count", 0)
        if not response:
            return {"status": "failed", "error": "empty response", "response": "",
                    "tokens": 0, "duration_ms": elapsed, "model_used": model}
        # 한국어 후처리 — 중국어/일본어 혼입은 항상 제거
        if response:
            from core.korean import force_korean
            if apply_korean:
                response = force_korean(response)
            else:
                # 영어 모델이라도 중국어/일본어 혼입만 제거
                import re
                response = re.sub(r'[\u4E00-\u9FFF\u3400-\u4DBF\uF900-\uFAFF]+', '', response)
                response = re.sub(r'[\u3040-\u309F\u30A0-\u30FF]+', '', response)
        return {"status": "success", "response": response, "tokens": tokens,
                "duration_ms": elapsed, "model_used": model}
    except Exception as e:
        elapsed = int((_time.time() - start) * 1000)
        return {"status": "failed", "error": str(e), "response": "",
                "tokens": 0, "duration_ms": elapsed, "model_used": model}


def _save_run(run_id, pipeline_name, pipeline_type, input_text, result, variables=None):
    """실행 이력 DB 저장"""
    from datetime import datetime, timezone
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO orchestration_runs (id, pipeline_name, pipeline_type, input_text, result_text, status, stages_json, variables_json, total_tokens, total_duration_ms, error, created_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, pipeline_name, pipeline_type, input_text,
             result["result"], result["status"],
             json.dumps(result["stages"], ensure_ascii=False),
             json.dumps(variables or {}, ensure_ascii=False),
             result["total_tokens"], result["total_duration_ms"],
             json.dumps(result.get("errors", []), ensure_ascii=False) if result.get("errors") else None,
             datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    except Exception as e:
        logger.debug("Failed to save orchestration run: %s", e)


@ollama_bp.route("/api/orchestration/run", methods=["POST"])
def api_run_pipeline():
    """파이프라인 실행 — sync/stream/async 3모드 지원
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [pipeline, input]
          properties:
            pipeline: {type: string, description: "파이프라인 이름"}
            input: {type: string, description: "입력 텍스트"}
            variables: {type: object, description: "시스템 프롬프트 내 {변수} 치환용"}
            debug: {type: boolean, default: false, description: "중간 결과 포함"}
            mode: {type: string, enum: [sync, stream, async], default: sync, description: "실행 모드"}
    responses:
      200:
        description: 파이프라인 실행 결과
    """
    data = request.json or {}
    pipeline_name = data.get("pipeline", "").strip()
    user_input = data.get("input", "").strip()
    variables = data.get("variables", {})
    debug = data.get("debug", False)
    mode = data.get("mode", "sync")

    if not pipeline_name or not user_input:
        return jsonify({"error": "pipeline and input are required"}), 400

    stages, pipeline_type = _load_pipeline_stages(pipeline_name)
    if stages is None:
        return jsonify({"error": f"pipeline '{pipeline_name}' not found"}), 404

    # SSE 스트리밍 모드
    if mode == "stream":
        return _stream_pipeline_response(pipeline_name, pipeline_type, stages,
                                          user_input, variables, debug)

    # 비동기 모드
    if mode == "async":
        return _async_pipeline_launch(pipeline_name, pipeline_type, stages,
                                       user_input, variables, debug)

    # 동기 모드 (기존)
    acquired = _orchestration_semaphore.acquire(timeout=5)
    if not acquired:
        return jsonify({"error": "다른 파이프라인 실행 중입니다", "retry_after": 10}), 429

    try:
        run_id = str(uuid.uuid4())
        result = _execute_pipeline(stages, user_input, variables, debug)
        _save_run(run_id, pipeline_name, pipeline_type, user_input, result, variables)
    finally:
        _orchestration_semaphore.release()

    # 10% 확률로 오래된 이력 정리
    import random
    if random.random() < 0.1:
        _cleanup_old_runs()

    return jsonify({
        "run_id": run_id,
        "pipeline": pipeline_name,
        "pipeline_type": pipeline_type,
        **result,
    })


def _stream_pipeline_response(pipeline_name, pipeline_type, stages,
                                user_input, variables, debug):
    """SSE 스트리밍 파이프라인 실행"""
    def sse_gen():
        acquired = _orchestration_semaphore.acquire(timeout=10)
        if not acquired:
            yield f"data: {json.dumps({'type': 'error', 'message': '다른 파이프라인 실행 중'})}\n\n"
            return

        run_id = str(uuid.uuid4())
        try:
            yield f"data: {json.dumps({'type': 'pipeline_start', 'run_id': run_id, 'pipeline': pipeline_name, 'stages_total': len(stages)}, ensure_ascii=False)}\n\n"

            current_text = user_input
            total_tokens = 0
            total_ms = 0
            result_stages = []
            is_ko = True

            for i, step in enumerate(stages):
                model = step["model"]
                role = step["role"]
                system = step.get("system", "")
                temp = step.get("temperature", 0.4)
                num_predict = step.get("num_predict", 2048)
                on_fail = step.get("on_fail", "continue")
                fallback_model = step.get("fallback_model")

                if variables and system:
                    for k, v in variables.items():
                        system = system.replace(f"{{{k}}}", str(v))

                yield f"data: {json.dumps({'type': 'stage_start', 'stage': i+1, 'model': model, 'role': role}, ensure_ascii=False)}\n\n"

                prompt = current_text if i == 0 else f"[이전 단계 결과]\n{current_text}\n\n[원래 입력]\n{user_input}\n\n위 결과를 바탕으로 {role}을 수행하세요."
                stage_result = _run_single_stage(model, prompt, system, temp, num_predict, apply_korean=is_ko)

                if stage_result["status"] == "failed" and fallback_model:
                    fb = _run_single_stage(fallback_model, prompt, system, temp, num_predict, apply_korean=is_ko)
                    if fb["status"] == "success":
                        stage_result = fb

                stage_data = {
                    'type': 'stage_complete', 'stage': i+1, 'status': stage_result['status'],
                    'tokens': stage_result['tokens'], 'duration_ms': stage_result['duration_ms'],
                    'model': stage_result.get('model_used', model),
                }
                if debug:
                    stage_data['output'] = stage_result['response'][:500]
                else:
                    stage_data['preview'] = stage_result['response'][:200]
                yield f"data: {json.dumps(stage_data, ensure_ascii=False)}\n\n"

                if stage_result["status"] == "success":
                    current_text = stage_result["response"]
                elif on_fail == "abort":
                    break

                total_tokens += stage_result["tokens"]
                total_ms += stage_result["duration_ms"]
                result_stages.append({"stage": i+1, "role": role, "model": model,
                                       "tokens": stage_result["tokens"],
                                       "duration_ms": stage_result["duration_ms"],
                                       "status": stage_result["status"]})

            # 최종 한국어 후처리
            if is_ko and current_text:
                from core.korean import force_korean
                current_text = force_korean(current_text)

            result = {"result": current_text, "status": "completed",
                      "stages": result_stages, "errors": [],
                      "total_tokens": total_tokens, "total_duration_ms": total_ms}
            _save_run(run_id, pipeline_name, pipeline_type, user_input, result, variables)

            yield f"data: {json.dumps({'type': 'pipeline_complete', 'run_id': run_id, 'status': 'completed', 'total_tokens': total_tokens, 'total_duration_ms': total_ms}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'result', 'content': current_text}, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            _orchestration_semaphore.release()

    return Response(sse_gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _async_pipeline_launch(pipeline_name, pipeline_type, stages,
                            user_input, variables, debug):
    """비동기 파이프라인 실행 — 즉시 run_id 반환, 백그라운드 실행"""
    run_id = str(uuid.uuid4())

    with _async_runs_lock:
        _async_runs[run_id] = {
            "status": "running", "pipeline": pipeline_name,
            "stages_total": len(stages), "current_stage": 0,
            "stages": [], "result": None, "error": None,
        }

    def worker():
        acquired = _orchestration_semaphore.acquire(timeout=60)
        if not acquired:
            with _async_runs_lock:
                _async_runs[run_id]["status"] = "failed"
                _async_runs[run_id]["error"] = "세마포어 타임아웃"
            return

        try:
            current_text = user_input
            total_tokens = 0
            total_ms = 0
            result_stages = []
            is_ko = True

            for i, step in enumerate(stages):
                with _async_runs_lock:
                    _async_runs[run_id]["current_stage"] = i + 1

                model = step["model"]
                role = step["role"]
                system = step.get("system", "")
                temp = step.get("temperature", 0.4)
                num_predict = step.get("num_predict", 2048)
                on_fail = step.get("on_fail", "continue")
                fallback_model = step.get("fallback_model")

                if variables and system:
                    for k, v in variables.items():
                        system = system.replace(f"{{{k}}}", str(v))

                prompt = current_text if i == 0 else f"[이전 단계 결과]\n{current_text}\n\n[원래 입력]\n{user_input}\n\n위 결과를 바탕으로 {role}을 수행하세요."
                stage_result = _run_single_stage(model, prompt, system, temp, num_predict, apply_korean=is_ko)

                if stage_result["status"] == "failed" and fallback_model:
                    fb = _run_single_stage(fallback_model, prompt, system, temp, num_predict, apply_korean=is_ko)
                    if fb["status"] == "success":
                        stage_result = fb

                stage_entry = {"stage": i+1, "role": role, "model": model,
                               "tokens": stage_result["tokens"],
                               "duration_ms": stage_result["duration_ms"],
                               "status": stage_result["status"]}
                result_stages.append(stage_entry)

                with _async_runs_lock:
                    _async_runs[run_id]["stages"] = result_stages[:]

                if stage_result["status"] == "success":
                    current_text = stage_result["response"]
                elif on_fail == "abort":
                    break

                total_tokens += stage_result["tokens"]
                total_ms += stage_result["duration_ms"]

            if is_ko and current_text:
                from core.korean import force_korean
                current_text = force_korean(current_text)

            result = {"result": current_text, "status": "completed",
                      "stages": result_stages, "errors": [],
                      "total_tokens": total_tokens, "total_duration_ms": total_ms}
            _save_run(run_id, pipeline_name, pipeline_type, user_input, result, variables)

            with _async_runs_lock:
                _async_runs[run_id].update({
                    "status": "completed", "result": current_text,
                    "stages": result_stages,
                    "total_tokens": total_tokens, "total_duration_ms": total_ms,
                })

        except Exception as e:
            with _async_runs_lock:
                _async_runs[run_id]["status"] = "failed"
                _async_runs[run_id]["error"] = str(e)
        finally:
            _orchestration_semaphore.release()

    t = _orch_threading.Thread(target=worker, daemon=True)
    t.start()

    return jsonify({
        "run_id": run_id,
        "status": "running",
        "pipeline": pipeline_name,
        "stages_total": len(stages),
    })


@ollama_bp.route("/api/orchestration/run/<run_id>/status", methods=["GET"])
def api_async_run_status(run_id):
    """비동기 실행 상태 조회 (폴링)
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: run_id, type: string, required: true}
    responses:
      200:
        description: 실행 상태
    """
    with _async_runs_lock:
        run = _async_runs.get(run_id)

    if run:
        return jsonify({"run_id": run_id, **run})

    # DB에서 조회 (완료된 과거 실행)
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM orchestration_runs WHERE id=?", (run_id,)
        ).fetchone()
        conn.close()
        if row:
            d = dict(row)
            if d.get("stages_json"):
                try:
                    d["stages"] = json.loads(d["stages_json"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return jsonify({"run_id": run_id, "status": d["status"],
                            "result": d.get("result_text"), "stages": d.get("stages", []),
                            "total_tokens": d.get("total_tokens"),
                            "total_duration_ms": d.get("total_duration_ms")})
    except Exception:
        pass

    return jsonify({"error": "run not found"}), 404


@ollama_bp.route("/api/orchestration/runs")
def api_list_runs():
    """실행 이력 목록
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: pipeline, in: query, type: string}
      - {name: limit, in: query, type: integer, default: 20}
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            runs: {type: array}
            count: {type: integer}
    """
    pipeline = request.args.get("pipeline")
    limit = min(int(request.args.get("limit", 20)), 100)

    conn = _get_db()
    if pipeline:
        rows = conn.execute(
            "SELECT id, pipeline_name, pipeline_type, status, total_tokens, total_duration_ms, created_at, substr(input_text,1,80) as input_preview FROM orchestration_runs WHERE pipeline_name=? ORDER BY created_at DESC LIMIT ?",
            (pipeline, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, pipeline_name, pipeline_type, status, total_tokens, total_duration_ms, created_at, substr(input_text,1,80) as input_preview FROM orchestration_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()

    return jsonify({"runs": [dict(r) for r in rows], "count": len(rows)})


@ollama_bp.route("/api/orchestration/run/<run_id>")
def api_get_run(run_id):
    """실행 결과 상세
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: string, required: true}
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "run not found"}}
    """
    conn = _get_db()
    row = conn.execute("SELECT * FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return jsonify({"error": "run not found"}), 404
    d = dict(row)
    if d.get("stages_json"):
        d["stages"] = json.loads(d["stages_json"])
        del d["stages_json"]
    if d.get("variables_json"):
        d["variables"] = json.loads(d["variables_json"])
        del d["variables_json"]
    if d.get("error"):
        try: d["errors"] = json.loads(d["error"])
        except Exception: pass
    return jsonify(d)


@ollama_bp.route("/api/orchestration/test", methods=["POST"])
def api_test_pipeline():
    """파이프라인 테스트 — validate/dry_run/full_test
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [pipeline]
          properties:
            pipeline: {type: string}
            input: {type: string, description: "dry_run/full_test 시 필요"}
            mode: {type: string, enum: [validate, dry_run, full_test], default: validate}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "input is required for dry_run/full_test"}}
      404:
        description: "대상을 찾을 수 없음"
      429:
        description: "요청 한도 초과(레이트리밋)"
        schema:
          type: object
          properties: {error: {type: string, example: "다른 파이프라인 실행 중입니다"}}
    """
    data = request.json or {}
    pipeline_name = data.get("pipeline", "").strip()
    mode = data.get("mode", "validate")
    user_input = data.get("input", "").strip()

    stages, pipeline_type = _load_pipeline_stages(pipeline_name)
    if stages is None:
        return jsonify({"error": f"pipeline '{pipeline_name}' not found"}), 404

    if mode in ("dry_run", "full_test") and not user_input:
        return jsonify({"error": "input is required for dry_run/full_test"}), 400

    # validate: 모델 존재 + VRAM 확인
    from routes.medical import _check_model_installed
    from core.gpu_scheduler import MODEL_VRAM_ESTIMATE, TOTAL_VRAM_GB
    checks = []
    for i, s in enumerate(stages):
        installed = _check_model_installed(s["model"])
        checks.append({"check": "model_installed", "stage": i+1, "model": s["model"], "ok": installed})
    max_vram = max(MODEL_VRAM_ESTIMATE.get(s["model"], 5.0) for s in stages)
    checks.append({"check": "vram_peak", "max_model_gb": max_vram, "total_vram_gb": TOTAL_VRAM_GB,
                    "ok": max_vram < TOTAL_VRAM_GB})

    validation = {"valid": all(c["ok"] for c in checks), "checks": checks}

    if mode == "validate":
        return jsonify({"pipeline": pipeline_name, "validation": validation})

    # dry_run/full_test: 모델 실행 필요 → Semaphore
    if mode in ("dry_run", "full_test"):
        if not _orchestration_semaphore.acquire(timeout=5):
            return jsonify({"error": "다른 파이프라인 실행 중입니다", "retry_after": 10}), 429

    if mode == "dry_run":
        try:
            s1 = _run_single_stage(stages[0]["model"], user_input,
                                stages[0].get("system", ""),
                                stages[0].get("temperature", 0.4),
                                stages[0].get("num_predict", 2048))
        finally:
            _orchestration_semaphore.release()
        return jsonify({
            "pipeline": pipeline_name, "mode": "dry_run", "validation": validation,
            "stage_1": {
                "model": stages[0]["model"], "role": stages[0]["role"],
                "status": s1["status"], "tokens": s1["tokens"],
                "duration_ms": s1["duration_ms"],
                "output_preview": s1["response"][:300] if s1["response"] else "",
            },
        })

    # full_test: 전체 실행 + 품질 메트릭
    try:
        result = _execute_pipeline(stages, user_input, debug=True)
    finally:
        _orchestration_semaphore.release()
    for s in result["stages"]:
        output = s.get("output", "")
        s["quality"] = {
            "output_length": len(output),
            "korean_ratio": round(sum(1 for c in output if '\uac00' <= c <= '\ud7a3') / max(len(output), 1), 2),
            "empty_output": len(output.strip()) == 0,
        }
    return jsonify({
        "pipeline": pipeline_name, "mode": "full_test",
        "validation": validation, "test_result": result["status"],
        **result,
    })


@ollama_bp.route("/api/orchestration/compare", methods=["POST"])
def api_compare_pipelines():
    """파이프라인 A/B 비교
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [pipelines, input]
          properties:
            pipelines: {type: array, items: {type: string}, description: "비교할 파이프라인 2~3개"}
            input: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "2+ pipelines and input required"}}
      429:
        description: "요청 한도 초과(레이트리밋)"
        schema:
          type: object
          properties: {error: {type: string, example: "다른 파이프라인 실행 중입니다"}}
    """
    data = request.json or {}
    names = data.get("pipelines", [])[:3]
    user_input = data.get("input", "").strip()
    if len(names) < 2 or not user_input:
        return jsonify({"error": "2+ pipelines and input required"}), 400

    if not _orchestration_semaphore.acquire(timeout=5):
        return jsonify({"error": "다른 파이프라인 실행 중입니다", "retry_after": 10}), 429

    results = {}
    try:
        for name in names:
            stages, ptype = _load_pipeline_stages(name)
            if stages is None:
                results[name] = {"error": f"not found"}
                continue
            r = _execute_pipeline(stages, user_input)
        results[name] = {
            "status": r["status"],
            "total_tokens": r["total_tokens"],
            "total_duration_ms": r["total_duration_ms"],
            "output_length": len(r["result"]),
            "stages_count": len(r["stages"]),
            "result_preview": r["result"][:200],
        }
    finally:
        _orchestration_semaphore.release()

    # 비교 분석
    valid = {k: v for k, v in results.items() if "error" not in v}
    comparison = {}
    if valid:
        comparison["fastest"] = min(valid, key=lambda k: valid[k]["total_duration_ms"])
        comparison["most_tokens"] = max(valid, key=lambda k: valid[k]["total_tokens"])
        comparison["longest_output"] = max(valid, key=lambda k: valid[k]["output_length"])

    return jsonify({"input_preview": user_input[:80], "results": results, "comparison": comparison})


@ollama_bp.route("/api/orchestration/pipeline/<name>", methods=["PUT"])
def api_update_pipeline(name):
    """파이프라인 수정 — 이전 버전 자동 백업
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: name, in: path, type: string, required: true}
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            stages: {type: array}
            description: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "1-5 stages required"}}
      404:
        description: "대상을 찾을 수 없음"
    """
    if name in ENHANCED_CHAT_PIPELINES:
        return jsonify({"error": "builtin pipeline cannot be modified"}), 400
    custom = _load_custom_pipelines()
    if name not in custom:
        return jsonify({"error": f"pipeline '{name}' not found"}), 404

    data = request.json or {}
    stages = data.get("stages")
    if stages and (len(stages) < 1 or len(stages) > 5):
        return jsonify({"error": "1-5 stages required"}), 400

    # 이전 버전 백업
    old = custom[name]
    version = old.get("version", 1)
    backup_name = f"{name}@v{version}"
    custom[backup_name] = {**old, "archived": True}

    # 새 버전
    from datetime import datetime, timezone
    if stages:
        old["stages"] = [{
            "model": s["model"], "role": s["role"],
            "system": s.get("system", ""), "temperature": s.get("temperature", 0.4),
            "num_predict": s.get("num_predict", 2048),
            "on_fail": s.get("on_fail", "continue"), "retry": s.get("retry", 0),
            "fallback_model": s.get("fallback_model"),
        } for s in stages]
    if data.get("description"):
        old["description"] = data["description"]
    old["version"] = version + 1
    old["updated_at"] = datetime.now(timezone.utc).isoformat()

    custom[name] = old
    _save_custom_pipelines(custom)
    return jsonify({"ok": True, "name": name, "version": version + 1, "backup": backup_name})


@ollama_bp.route("/api/orchestration/pipeline/<name>/versions")
def api_pipeline_versions(name):
    """파이프라인 버전 목록
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            pipeline: {}
            versions: {}
    """
    custom = _load_custom_pipelines()
    versions = []
    if name in custom:
        versions.append({"version": custom[name].get("version", 1), "current": True,
                          "created_at": custom[name].get("updated_at", custom[name].get("created_at", ""))})
    for k, v in custom.items():
        if k.startswith(f"{name}@v"):
            ver = k.split("@v")[-1]
            versions.append({"version": int(ver), "current": False, "created_at": v.get("created_at", "")})
    versions.sort(key=lambda x: x["version"], reverse=True)
    return jsonify({"pipeline": name, "versions": versions})


@ollama_bp.route("/api/orchestration/pipeline/<name>/rollback", methods=["POST"])
def api_pipeline_rollback(name):
    """이전 버전 복원
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: name, in: path, type: string, required: true}
      - {name: version, in: query, type: integer, required: true}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "version parameter required"}}
      404:
        description: "대상을 찾을 수 없음"
    """
    version = request.args.get("version", type=int)
    if not version:
        return jsonify({"error": "version parameter required"}), 400
    custom = _load_custom_pipelines()
    backup_name = f"{name}@v{version}"
    if backup_name not in custom:
        return jsonify({"error": f"version {version} not found"}), 404

    # 현재 → 백업, 복원 → 현재
    if name in custom:
        cur_ver = custom[name].get("version", 1)
        custom[f"{name}@v{cur_ver}"] = {**custom[name], "archived": True}
    custom[name] = {**custom[backup_name], "archived": False}
    _save_custom_pipelines(custom)
    return jsonify({"ok": True, "restored_version": version})


@ollama_bp.route("/api/orchestration/pipeline/<name>/clone", methods=["POST"])
def api_clone_pipeline(name):
    """파이프라인 복제
    ---
    tags: [Orchestration]
    security: [{Bearer: []}]
    parameters:
      - {name: name, in: path, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            new_name: {type: string, description: "새 파이프라인 이름"}
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            ok: {type: boolean}
            name: {}
            cloned_from: {}
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
      404:
        description: "대상을 찾을 수 없음"
    """
    data = request.json or {}
    new_name = data.get("new_name", f"{name}-copy").strip()

    # 원본 로드
    if name in ENHANCED_CHAT_PIPELINES:
        source = {"stages": ENHANCED_CHAT_PIPELINES[name], "description": f"Clone of builtin:{name}"}
    else:
        custom = _load_custom_pipelines()
        if name not in custom:
            return jsonify({"error": f"pipeline '{name}' not found"}), 404
        source = custom[name]

    custom = _load_custom_pipelines()
    if new_name in custom or new_name in ENHANCED_CHAT_PIPELINES:
        return jsonify({"error": f"'{new_name}' already exists"}), 400

    from datetime import datetime, timezone
    custom[new_name] = {
        "description": source.get("description", f"Clone of {name}"),
        "stages": source["stages"],
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cloned_from": name,
    }
    _save_custom_pipelines(custom)
    return jsonify({"ok": True, "name": new_name, "cloned_from": name})


# ---------------------------------------------------------------------------
# Enhanced Chat — Multi-model orchestration per domain (v2.49.0)
# ---------------------------------------------------------------------------

ENHANCED_CHAT_PIPELINES = {
    "medical": [
        {"model": "werub-medical:7b", "role": "증상 정규화 + 한국어 분석",
         "system": "당신은 의료 AI입니다. 환자 증상을 분석하고 한국어로 정리하세요."},
        {"model": "thewindmom/llama3-med42-8b", "role": "감별진단 + 임상 추론",
         "system": "You are a clinical expert. Provide differential diagnosis with ICD-10 codes and evidence-based reasoning."},
        {"model": "werub-medical:7b", "role": "한국어 종합 + 면책",
         "system": "당신은 의료 AI입니다. 이전 분석을 한국어로 종합 정리하세요. 끝에 면책 문구를 포함하세요: '이 정보는 AI 참고용이며 실제 진료를 대체하지 않습니다.'"},
    ],
    "coding": [
        {"model": "deepseek-coder-v2:16b", "role": "코드 생성",
         "system": "You are an expert programmer. Generate clean, well-documented code with proper error handling."},
        {"model": "qwen2.5:14b", "role": "코드 리뷰 + 한국어 설명",
         "system": "당신은 코드 리뷰어입니다. 이전 코드를 검토하고 개선점을 한국어로 설명하세요. 보안 취약점이 있으면 경고하세요."},
    ],
    "writing": [
        {"model": "qwen2.5:14b", "role": "초안 생성",
         "system": "당신은 한국어 글쓰기 AI입니다. 풍부한 내용의 초안을 작성하세요."},
        {"model": "exaone-deep:7.8b", "role": "구조/논증 강화",
         "system": "당신은 글 구조 편집자입니다. 논리 흐름을 강화하고 근거를 보강하세요. 글 본문만 출력하세요. 반드시 한국어로만 답변하세요. <thought> 태그를 사용하지 마세요."},
        {"model": "dnotitia/dna:latest", "role": "한국어 다듬기",
         "system": "당신은 한국어 문체 전문가입니다. 번역투를 제거하고 자연스러운 한국어로 다듬으세요. 글 본문만 출력하세요."},
    ],
    "legal": [
        {"model": "qwen2.5:14b", "role": "법률 답변",
         "system": "당신은 한국 노동법 전문 AI입니다. 근로기준법 등 한국 법률 기반으로 정확하게 답변하세요."},
        {"model": "exaone-deep:7.8b", "role": "논증 보강 + 근거 정리",
         "system": "당신은 법률 논증 전문가입니다. 이전 답변의 법적 근거를 강화하고 조문 번호를 정리하세요. 법적 조언이 아닌 법률 정보 제공임을 명시하세요. 반드시 한국어로만 답변하세요. <thought> 태그를 사용하지 마세요."},
    ],
}


# depth="fast" 경량 파이프라인 — 단계 축소(컨시어지 속도). medical은 영어 med42 단계를
# 제거해 werub 2단계(증상+감별진단 통합 → 한국어종합+면책)=단계↓+스왑0. 응답 shape 동일.
# 도메인별 fast 미정의 시 full로 폴백.
ENHANCED_CHAT_PIPELINES_FAST = {
    "medical": [
        {"model": "werub-medical:7b", "role": "증상 분석 + 감별진단",
         "system": "당신은 의료 AI입니다. 환자 증상을 분석하고 가능성 높은 감별진단(가능하면 ICD-10)과 권장 진료과를 한국어로 간결히 제시하세요."},
        {"model": "werub-medical:7b", "role": "한국어 종합 + 면책",
         "system": "당신은 의료 AI입니다. 이전 분석을 환자가 이해하기 쉬운 한국어로 종합하세요. 끝에 면책 문구를 포함하세요: '이 정보는 AI 참고용이며 실제 진료를 대체하지 않습니다.'"},
    ],
    "coding": [
        {"model": "deepseek-coder-v2:16b", "role": "코드 생성 + 설명",
         "system": "You are an expert programmer. Generate clean, documented code with error handling, then briefly explain it in Korean."},
    ],
    "writing": [
        {"model": "qwen2.5:14b", "role": "초안 + 한국어 다듬기",
         "system": "당신은 한국어 글쓰기 AI입니다. 자연스럽고 번역투 없는 한국어로 완성도 높은 글을 작성하세요. 글 본문만 출력하세요."},
    ],
    "legal": [
        {"model": "qwen2.5:14b", "role": "법률 답변 + 근거",
         "system": "당신은 한국 노동법 전문 AI입니다. 근로기준법 등 한국 법률 기반으로 정확히 답변하고 조문 번호를 정리하세요. 법적 조언이 아닌 법률 정보 제공임을 명시하세요."},
    ],
}


@ollama_bp.route("/api/chat/enhanced", methods=["POST"])
def api_chat_enhanced():
    """Enhanced chat — multi-model orchestration per domain
    ---
    tags: [Chat]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [messages]
          properties:
            messages:
              type: array
              items:
                type: object
                properties:
                  role: {type: string, enum: [system, user, assistant]}
                  content: {type: string}
            domain: {type: string, enum: [auto, medical, coding, writing, legal], default: auto}
            single_model:
              description: "모델 스왑 제거. 모델명 문자열(예 qwen2.5:14b=상주모델 재사용 로드0)·true(파이프라인 1단계 모델)·미지정(단계별 전문모델, 기본). 응답 shape 동일"
            depth: {type: string, enum: [full, fast], default: full, description: "fast=단계 축소(medical 3→2단계, 컨시어지 속도). single_model과 조합 가능"}
    responses:
      200:
        description: 강화된 채팅 응답 (다중 모델 오케스트레이션)
    """
    data = request.json or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "messages required"}), 400
    over = _too_long(*[m.get("content") for m in messages if isinstance(m, dict)])
    if over:
        return over

    user_domain = data.get("domain", "auto")

    # 마지막 사용자 메시지
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    # 도메인 감지
    if user_domain and user_domain != "auto":
        domain = user_domain
        confidence = 1.0
    else:
        domain, confidence = _classify_chat_domain(last_user_msg)

    # depth="fast" → 단계 축소 파이프라인(미정의 도메인은 full 폴백)
    depth = data.get("depth", "full")
    if depth == "fast":
        pipeline = ENHANCED_CHAT_PIPELINES_FAST.get(domain) or ENHANCED_CHAT_PIPELINES.get(domain)
    else:
        pipeline = ENHANCED_CHAT_PIPELINES.get(domain)
    if not pipeline:
        # general은 강화 파이프라인 없음 → 단일 모델
        return jsonify({
            "answer": "enhanced 모드는 medical/coding/writing/legal 도메인에서 사용 가능합니다. domain 파라미터를 지정하거나, 해당 도메인 질문을 해주세요.",
            "domain_detected": domain,
            "enhanced": False,
            "suggestion": "일반 질문은 /api/chat를 사용하세요.",
        })

    from core.korean import force_korean, korean_quality_check

    # 모델 스왑 제거(옵션): single_model 지정 시 전 단계를 단일 모델로 실행.
    #  - 문자열(모델명): 해당 모델로 전 단계. 예: "qwen2.5:14b"(상주모델 재사용=로드0·축출0)
    #  - true: 파이프라인 1단계 모델로 전 단계 (예: medical=werub-medical:7b, 로드1회)
    #  - 미지정/false: 기존 단계별 전문모델(스왑 발생) — 기본·무변경
    _sm = data.get("single_model")
    if isinstance(_sm, str) and _sm.strip():
        force_model = _sm.strip()
    elif _sm is True:
        force_model = pipeline[0]["model"]
    else:
        force_model = None

    stages = []
    total_tokens = 0
    total_ms = 0
    current_text = last_user_msg
    is_ko = _is_ko_input(last_user_msg)

    for i, step in enumerate(pipeline):
        model = force_model or step["model"]
        # 규제 라우팅 가드(opt-in, routes.medical._pin_public): 파이프라인 하드코딩
        # 모델도 자동 라우팅으로 간주 — single_model 명시는 존중(force_model 무가드)
        if not force_model:
            try:
                from routes.medical import _pin_public
                model = _pin_public(model)
            except Exception:  # noqa: BLE001 — 가드 실패=기존 모델 유지
                pass
        role = step["role"]
        system = step["system"]

        if i == 0:
            prompt = current_text
        else:
            prompt = f"[이전 단계 결과]\n{current_text}\n\n[원래 질문]\n{last_user_msg}\n\n위 결과를 바탕으로 {role}을 수행하세요."

        import time as _time
        start = _time.time()
        try:
            r = http_requests.post(f"{_OLLAMA_BASE}/api/generate", json={
                "model": model, "prompt": prompt, "system": system,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 2048},
            }, timeout=120)
            d = r.json()
            response = d.get("response", "")
            tokens = d.get("eval_count", 0)
            elapsed = int((_time.time() - start) * 1000)
        except Exception as e:
            response = f"Stage {i+1} failed: {e}"
            tokens = 0
            elapsed = int((_time.time() - start) * 1000)

        # 한국어 후처리 — 최종 단계 또는 한국어 모델 출력에 적용
        if is_ko and response and model not in _CHAT_EN_ONLY_MODELS:
            response = force_korean(response)

        current_text = response
        total_tokens += tokens
        total_ms += elapsed
        stages.append({
            "stage": i + 1,
            "role": role,
            "model": model,
            "tokens": tokens,
            "duration_ms": elapsed,
        })

    # 최종 결과 한국어 품질 검증
    quality_info = {}
    if is_ko and current_text:
        current_text = force_korean(current_text)
        is_valid, korean_ratio, _ = korean_quality_check(current_text)
        quality_info = {"korean_ratio": round(korean_ratio, 4), "validated": is_valid}

    return jsonify({
        "answer": current_text,
        "domain_detected": domain,
        "domain_confidence": round(confidence, 2),
        "enhanced": True,
        "depth": depth,
        "stages": stages,
        "single_model": force_model,
        "total_tokens": total_tokens,
        "total_duration_ms": total_ms,
        "quality_check": quality_info,
    })


@ollama_bp.route("/api/chat", methods=["POST"])
@heavy_limit("30/minute")
def api_chat():
    """Chat with model (streaming or quality-checked with session memory) — Fair Queue 적용
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [messages]
          properties:
            model: {type: string, example: "qwen2.5:7b", description: "모델명 (생략 시 자동 선택)"}
            messages:
              type: array
              description: "system role 메시지 없으면 기본 한국어 프롬프트 자동 주입"
              items:
                type: object
                properties:
                  role: {type: string, enum: [system, user, assistant]}
                  content: {type: string}
                  images:
                    type: array
                    description: "비전 모델 입력. raw base64 또는 data URI(접두는 서버가 제거). PNG·JPEG·GIF·WEBP(WEBP는 서버가 PNG로 변환). 요청 본문 상한 10MB(외부 경유·초과 시 413)"
                    items: {type: string}
            stream: {type: boolean, default: true, description: "스트리밍 응답 (기본값 true)"}
            quality_check: {type: boolean, default: false, description: "품질 검증 + 재시도 (비스트리밍)"}
            session_id: {type: string, example: "550e8400-e29b-41d4-a716-446655440000", description: "세션 UUID (생략 시 새 세션, 비스트리밍 전용)"}
            user_id: {type: string, example: "user123", description: "사용자 ID (선택, 세션 추적용)"}
            think: {type: boolean, description: "thinking 지원 모델에만 전달(미지원 모델엔 자동 생략). ⚠️qwen3-vl 등은 false여도 완전 억제되지 않음(엔진 특성)"}
            options:
              type: object
              description: "Ollama 생성 옵션(temperature·num_ctx·num_predict 등) 그대로 전달"
            no_cache: {type: boolean, default: false, description: "캐시 무시. 캐시 키는 모델·전 메시지·이미지 지문·think·options를 포함하므로 하나라도 다르면 별개 응답(v2.124.59)"}
    responses:
      200:
        description: >
          stream=true → NDJSON 스트림.
          stream=false/quality_check=true → JSON {answer, model_used, complexity, session_id, message_count, quality_check}.
      401:
        description: Unauthorized
      429:
        description: Server busy — too many concurrent requests
    """
    data = request.json
    user_specified_model = data.get("model", "")  # 사용자 명시 모델
    model = user_specified_model  # 도메인 라우팅에서 덮어쓸 수 있음
    messages = data.get("messages", [])
    stream_mode = data.get("stream", True)
    quality_check = data.get("quality_check", False)
    session_id = data.get("session_id", None)
    user_id = data.get("user_id", None)
    user_domain = data.get("domain", "auto")  # v2.49.0: 도메인 지정

    if not messages:
        return jsonify({"error": "messages required"}), 400
    over = _too_long(*[m.get("content") for m in messages if isinstance(m, dict)])
    if over:
        return over

    # 마지막 사용자 메시지 추출
    last_user_msg = ""
    last_user_images = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            last_user_images = msg.get("images")
            break

    # ═══════════════════════════════════════════════════════════
    # Domain Detection + Model Auto-Selection (v2.49.0)
    # ═══════════════════════════════════════════════════════════
    domain_detected = "general"
    domain_confidence = 1.0
    routing_reason = "user_specified" if model else "auto"

    if not model:
        # 도메인 감지
        if user_domain and user_domain != "auto":
            domain_detected = user_domain
            domain_confidence = 1.0
        else:
            domain_detected, domain_confidence = _classify_chat_domain(last_user_msg, last_user_images)

        # 언어 감지
        lang = "ko" if _is_ko_input(last_user_msg) else "en"

        # 비전 서브타입
        vision_sub = _detect_vision_subtype(last_user_msg) if domain_detected == "vision" else None

        # 모델 선택
        model, routing_reason = _select_chat_model(domain_detected, lang, vision_sub)

    # 도메인 라우팅 후에도 model이 비어있으면 default_model 폴백
    if not model:
        model = _load_config().get("default_model", "qwen2.5:14b")

    # system 프롬프트: 도메인별 프롬프트 우선, 없으면 기본
    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        domain_prompt = DOMAIN_SYSTEM_PROMPTS.get(domain_detected)
        if domain_prompt:
            messages = [{"role": "system", "content": domain_prompt}] + messages
        else:
            cfg = _load_config()
            if cfg.get("default_system_prompt_enabled", True):
                messages = [{"role": "system", "content": _build_system_prompt()}] + messages

    # 컨텍스트 윈도우 안전 관리
    messages = _adjust_history_for_model(messages, model)

    # ═══════════════════════════════════════════════════════════
    # NON-STREAMING MODE (quality check + session memory)
    # ═══════════════════════════════════════════════════════════
    if not stream_mode or quality_check:
        from core.korean import korean_quality_check as validate_korean_quality, KOREAN_RETRY_SUFFIX as RETRY_SUFFIX
        skip_korean = _should_skip_korean_check(domain_detected, model)
        complexity = domain_detected  # 도메인 라우팅이 complexity를 대체

        # --- Session history injection (v2.38.0) ---
        if session_id:
            try:
                from core.chat_memory import get_session_messages, compress_chat_history
                history = get_session_messages(session_id, limit=10)
                if history:
                    # Auto-compress if session has too many messages
                    from core.chat_memory import get_or_create_session as _peek_session
                    sess_info = _peek_session(session_id)
                    if sess_info.get("message_count", 0) > 20:
                        try:
                            compress_chat_history(session_id, recent_turns=10)
                            history = get_session_messages(session_id, limit=10)
                        except Exception:
                            pass  # compression failure is non-fatal

                    # Inject history before current messages (after system prompt)
                    history_msgs = [
                        {"role": h["role"], "content": h["content"]}
                        for h in reversed(history)  # oldest first
                        if h["role"] in ("user", "assistant")
                    ]
                    if history_msgs:
                        # Find insertion point (after system messages)
                        insert_at = 0
                        for idx, m in enumerate(messages):
                            if m.get("role") == "system":
                                insert_at = idx + 1
                            else:
                                break
                        messages = messages[:insert_at] + history_msgs + messages[insert_at:]
            except Exception as e:
                logger.warning(f"History injection failed: {e}")

        # --- Fair Queue ---
        try:
            _gpu_manager.ensure_llm_vram()
        except Exception as e:
            logger.debug("ensure_llm_vram failed before chat: %s", e)
        queue_key = _rate_limit_key()
        # 대기 시간 측정 — 큐가 흡수해 429 없이 조용히 느려지는 것을 호출자가 알 수 있게
        # 응답 헤더로 돌려준다(2026-07-30 Cerno 실측: 단일 키 동시 요청이 완전 직렬화되는데
        # 대기열이 삼켜 "그냥 느린 서버"로만 보였다). → core/queue_signal.py
        _q_t0 = time.time()
        acquired, waited, position, est = _chat_queue.acquire(queue_key)
        _q_waited_ms = int((time.time() - _q_t0) * 1000)
        if not acquired:
            qs = _chat_queue.stats()
            return jsonify({
                "error": "server busy",
                "message": f"동시 처리 한도 초과",
                "retry_after": 5, "queue_stats": qs,
            }), 429

        try:
            # LLM call
            resp = http_requests.post(
                f"{_OLLAMA_BASE}/api/chat",
                json=chat_payload(model, messages, data, stream=False),
                timeout=600,
            )
            resp.raise_for_status()
            response_text = resp.json().get("message", {}).get("content", "")

            # Korean quality validation + retry (도메인/모델별 스킵)
            if skip_korean:
                is_valid, korean_ratio = True, 1.0
            else:
                from core.korean import force_korean as _fk
                response_text = _fk(response_text)
                is_valid, korean_ratio, _ = validate_korean_quality(response_text)
            attempts = 1

            if not is_valid and not skip_korean:
                for attempt in range(2, 4):  # max 2 retries
                    retry_msgs = list(messages)
                    for i in range(len(retry_msgs) - 1, -1, -1):
                        if retry_msgs[i].get("role") == "user":
                            retry_msgs.insert(i, {"role": "system", "content": RETRY_SUFFIX})
                            break
                    try:
                        r2 = http_requests.post(
                            f"{_OLLAMA_BASE}/api/chat",
                            json=chat_payload(model, retry_msgs, data, stream=False),
                            timeout=600,
                        )
                        r2.raise_for_status()
                        new_text = _fk(r2.json().get("message", {}).get("content", ""))
                        is_valid, korean_ratio, _ = validate_korean_quality(new_text)
                        attempts = attempt
                        if is_valid:
                            response_text = new_text
                            break
                    except Exception:
                        continue

            result = {
                "answer": response_text,
                "model_used": model,
                "domain_detected": domain_detected,
                "domain_confidence": round(domain_confidence, 2),
                "routing_reason": routing_reason,
                "complexity": complexity,
                "quality_check": {
                    "korean_ratio": round(korean_ratio, 4),
                    "validated": is_valid,
                    "threshold": 0.30,
                    "attempts": attempts,
                    "skipped": skip_korean,
                },
                "history_injected": session_id is not None,
            }

            # 한국어 자연화 경량 체크
            try:
                if response_text and len(response_text) >= 50 and not skip_korean:
                    from core.korean_humanize import compute_korean_quality, get_quality_summary
                    hq = compute_korean_quality(response_text, genre="chat")
                    result["quality_check"]["humanize"] = get_quality_summary(hq)
            except Exception:
                pass

            # Session memory (best-effort)
            try:
                from core.chat_memory import get_or_create_session, append_chat_message
                session = get_or_create_session(session_id, user_id, model)
                if last_user_msg:
                    append_chat_message(session["id"], "user", last_user_msg, None)
                if response_text:
                    append_chat_message(session["id"], "assistant", response_text, korean_ratio)
                result["session_id"] = session["id"]
                result["message_count"] = session["message_count"] + (
                    (1 if last_user_msg else 0) + (1 if response_text else 0)
                )
            except Exception as e:
                logger.warning(f"Session memory failed: {e}")

            # Cache
            if response_text:
                from core.cache import chat_cache_key
                _llm_response_cache.set(chat_cache_key(model, messages, data), response_text)

            from core.queue_signal import wait_headers
            resp_out = jsonify(result)
            resp_out.headers.extend(wait_headers(_q_waited_ms, position,
                                                 _chat_queue.stats().get("active_total")))
            return resp_out

        except Exception as e:
            logger.error(f"Non-streaming chat error: {e}", exc_info=True)
            return internal_error(e)
        finally:
            _chat_queue.release(queue_key)

    # ═══════════════════════════════════════════════════════════
    # STREAMING MODE (default, backward compatible)
    # ═══════════════════════════════════════════════════════════

    # --- Session history injection for streaming (v2.38.0) ---
    if session_id:
        try:
            from core.chat_memory import get_session_messages, get_or_create_session as _peek_s, compress_chat_history
            _hist = get_session_messages(session_id, limit=10)
            if _hist:
                _si = _peek_s(session_id)
                if _si.get("message_count", 0) > 20:
                    try:
                        compress_chat_history(session_id, recent_turns=10)
                        _hist = get_session_messages(session_id, limit=10)
                    except Exception as e:
                        logger.debug("Chat history compression failed: %s", e)
                _hmsgs = [{"role": h["role"], "content": h["content"]}
                          for h in reversed(_hist) if h["role"] in ("user", "assistant")]
                if _hmsgs:
                    _ins = 0
                    for _idx, _m in enumerate(messages):
                        if _m.get("role") == "system":
                            _ins = _idx + 1
                        else:
                            break
                    messages = messages[:_ins] + _hmsgs + messages[_ins:]
        except Exception as e:
            logger.debug("Session history injection failed: %s", e)

    # --- LLM 캐시 확인 (슬롯 미사용) ---
    if not data.get("no_cache"):
        from core.cache import chat_cache_key
        _ck = chat_cache_key(model, messages, data)
        _cached = _llm_response_cache.get(_ck)
        if _cached:
            def cached_stream():
                yield json.dumps({"model": model, "message": {"role": "assistant", "content": _cached}, "done": True, "cached": True}) + "\n"
            return Response(cached_stream(), mimetype="application/x-ndjson")
    else:
        _ck = None

    # --- ComfyUI VRAM 해제 + 공정 큐 슬롯 획득 ---
    try:
        _gpu_manager.ensure_llm_vram()
    except Exception as e:
        logger.debug("ensure_llm_vram failed before generate: %s", e)
    queue_key = _rate_limit_key()
    _q_t0 = time.time()                     # 큐 대기 노출(비스트리밍과 동일 규율)
    acquired, waited, position, est = _chat_queue.acquire(queue_key)
    _q_waited_ms = int((time.time() - _q_t0) * 1000)
    if not acquired:
        qs = _chat_queue.stats()
        return jsonify({
            "error": "server busy",
            "message": f"동시 처리 한도 초과 (활성 {qs['active_total']}/{qs['max_global']}, 대기 {qs['wait_queue']}/{qs['max_wait_queue']})",
            "position": position,
            "retry_after": 5,
            "queue_stats": qs,
        }), 429

    # messages 내 images·think·options 포워딩 (비전 모델 + 생성 파라미터)
    payload = chat_payload(model, messages, data, stream=True)

    def stream():
        _full_response = []
        _stream_done = False
        try:
            r = http_requests.post(
                f"{_OLLAMA_BASE}/api/chat",
                json=payload, stream=True, timeout=600,
            )
            for line in r.iter_lines():
                if line:
                    decoded = line.decode()
                    # 응답 수집 (캐시용)
                    try:
                        chunk = json.loads(decoded)
                        if chunk.get("message", {}).get("content"):
                            _full_response.append(chunk["message"]["content"])
                        if chunk.get("done"):
                            _stream_done = True
                    except (json.JSONDecodeError, KeyError):
                        pass
                    yield decoded + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"
        finally:
            _chat_queue.release(queue_key)
            raw_text = "".join(_full_response) if _stream_done else ""
            # 한국어 후처리 후 캐시/세션 저장 (v2.62.1)
            # 저장분 정화는 문자 단위만(v2.125.21 — 종전 force_korean은 코드·영어 줄을
            # 통째로 지워 캐시 재생이 라이브와 갈렸고, 전량 삭제 시 저장 스킵까지 났다)
            from core.korean import sanitize_stream_store as _fk_stream, korean_quality_check as _kqc_stream
            full_text = _fk_stream(raw_text) if raw_text else ""
            _kr = 0.0
            if full_text:
                _, _kr, _ = _kqc_stream(full_text)
            # 캐시 저장 (필터링된 텍스트)
            if _ck and full_text:
                _llm_response_cache.set(_ck, full_text)
            # 세션 저장 (필터링된 텍스트 + korean_ratio)
            if session_id and full_text:
                try:
                    from core.chat_memory import get_or_create_session, append_chat_message
                    _last_user = ""
                    for _m in reversed(messages):
                        if _m.get("role") == "user":
                            _last_user = _m.get("content", "")
                            break
                    sess = get_or_create_session(session_id, user_id, model)
                    if _last_user:
                        append_chat_message(sess["id"], "user", _last_user, None)
                    append_chat_message(sess["id"], "assistant", full_text, _kr)
                except Exception:
                    pass  # best-effort

    from core.queue_signal import wait_headers
    return Response(stream(), mimetype="application/x-ndjson",
                    headers=wait_headers(_q_waited_ms, position,
                                         _chat_queue.stats().get("active_total")))



@ollama_bp.route("/api/chat/queue/stats", methods=["GET"])
def api_chat_queue_stats():
    """Chat queue statistics
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    responses:
      200:
        description: Current chat queue status
    """
    return jsonify(_chat_queue.stats())


@ollama_bp.route("/api/chat/sessions", methods=["GET"])
def api_chat_sessions():
    """List chat sessions
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: query
        name: user_id
        type: string
        description: 특정 사용자 필터
      - in: query
        name: limit
        type: integer
        default: 20
      - in: query
        name: offset
        type: integer
        default: 0
    responses:
      200:
        description: 세션 목록
    """
    try:
        from core.chat_memory import list_sessions
        user_id = request.args.get("user_id")
        limit = min(int(request.args.get("limit", 20)), 100)
        offset = int(request.args.get("offset", 0))
        sessions = list_sessions(user_id=user_id, limit=limit, offset=offset)
        return jsonify({"sessions": sessions, "count": len(sessions)})
    except Exception as e:
        return internal_error(e)


@ollama_bp.route("/api/chat/sessions/<session_id>/messages", methods=["GET"])
def api_chat_session_messages(session_id):
    """Get messages for a session
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: session_id
        type: string
        required: true
      - in: query
        name: limit
        type: integer
        default: 50
      - in: query
        name: offset
        type: integer
        default: 0
    responses:
      200:
        description: 세션 메시지 목록
    """
    try:
        from core.chat_memory import get_session_messages
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
        messages = get_session_messages(session_id, limit=limit, offset=offset)
        return jsonify({"session_id": session_id, "messages": messages, "count": len(messages)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return internal_error(e)


@ollama_bp.route("/api/chat/sessions/<session_id>", methods=["DELETE"])
def api_chat_session_delete(session_id):
    """Delete a chat session
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: session_id
        type: string
        required: true
    responses:
      200:
        description: 세션 삭제 완료
    """
    try:
        from core.chat_memory import delete_session
        delete_session(session_id)
        return jsonify({"ok": True, "deleted": session_id})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return internal_error(e)


@ollama_bp.route("/api/chat/purge", methods=["POST"])
def api_chat_purge():
    """비활성 챗 세션+메시지 retention 정리 (PHI 최소화/거버넌스)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            retention_days:
              type: integer
              description: 비활성 보관 일수(기본 config chat_retention_days 또는 90). 0 이하=무동작
            vacuum:
              type: boolean
              description: 삭제 후 VACUUM으로 디스크 공간 OS 회수(기본 false, 라이브 DB 락 가능 — 한산할 때만)
    responses:
      200:
        description: 정리 결과 (sessions_deleted/messages_deleted/vacuum)
    """
    try:
        from core.chat_memory import purge_old_sessions
        body = request.get_json(silent=True) or {}
        days = body.get("retention_days")
        if days is None:
            from core.config import load_config
            days = load_config().get("chat_retention_days", 90)
        days = int(days)

        res = purge_old_sessions(days)

        if body.get("vacuum"):
            # 삭제로 생긴 freelist 페이지를 OS로 회수. 라이브 DB라 락 경합 시 실패할 수
            # 있으므로 가드(VACUUM은 sqlite3가 트랜잭션으로 감싸지 않음).
            from core.db import get_db
            try:
                get_db().execute("VACUUM")
                res["vacuum"] = "ok"
            except Exception as ve:
                res["vacuum"] = f"failed: {ve}"
        return jsonify({"ok": True, **res})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return internal_error(e)


@ollama_bp.route("/api/gpu/status", methods=["GET"])
def api_gpu_status():
    """GPU Resource Manager status
    ---
    tags: [System]
    security: [{Bearer: []}]
    responses:
      200:
        description: GPU resource allocation status
        schema:
          type: object
          properties:
            gpu: {}
            resource_manager: {}
            chat_queue: {}
    """
    gpu_info = {}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], timeout=5).decode().strip().split(", ")
        gpu_info = {"vram_used_mb": int(out[0]), "vram_total_mb": int(out[1]),
                    "gpu_util_pct": int(out[2]), "temp_c": int(out[3])}
    except Exception as e:
        logger.debug("nvidia-smi query failed: %s", e)
    return jsonify({
        "gpu": gpu_info,
        "resource_manager": _gpu_manager.status(),
        "chat_queue": _chat_queue.stats(),
    })




@ollama_bp.route("/api/service/<action>", methods=["POST"])
def api_service(action):
    """Control Ollama service (start/stop/restart)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - {name: action, in: path, type: string, required: true, enum: [start, stop, restart]}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            output: {type: string}
      401:
        description: Unauthorized
    """
    if action == "start":
        result = subprocess.run(["brew", "services", "start", "ollama"],
                                capture_output=True, text=True)
    elif action == "stop":
        result = subprocess.run(["brew", "services", "stop", "ollama"],
                                capture_output=True, text=True)
    elif action == "restart":
        subprocess.run(["brew", "services", "stop", "ollama"],
                       capture_output=True, text=True)
        time.sleep(1)
        result = subprocess.run(["brew", "services", "start", "ollama"],
                                capture_output=True, text=True)
    else:
        return jsonify({"error": "invalid action"}), 400

    return jsonify({
        "ok": result.returncode == 0,
        "output": result.stdout + result.stderr,
    })

