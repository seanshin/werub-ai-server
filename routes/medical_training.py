"""Medical AI Fine-tuning Management Blueprint — 학습 관리 API + 웹 UI"""
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, request, jsonify, render_template_string
from core.errors import internal_error

logger = logging.getLogger("ollama-admin")

medical_training_bp = Blueprint("medical_training", __name__)

_BASE_DIR = None
_TRAINING_DIR = None
_OLLAMA_BASE = None

# 학습 상태 (in-memory)
_training_state = {
    "status": "idle",         # idle | preparing | training | exporting | deploying | completed | failed
    "progress": 0,            # 0-100
    "current_step": "",
    "started_at": None,
    "completed_at": None,
    "error": None,
    "result": None,
    "pid": None,
}
_training_lock = threading.Lock()


def init_medical_training(*, base_dir, ollama_base=None):
    global _BASE_DIR, _TRAINING_DIR, _OLLAMA_BASE
    _BASE_DIR = base_dir
    _TRAINING_DIR = os.path.join(base_dir, "training")
    _OLLAMA_BASE = ollama_base or "http://localhost:11434"


def _get_data_stats():
    """학습 데이터 통계"""
    data_dir = os.path.join(_TRAINING_DIR, "data", "medical")
    stats = {"train_samples": 0, "eval_samples": 0, "gold_samples": 0, "db_available": False}

    train_path = os.path.join(data_dir, "train.jsonl")
    eval_path = os.path.join(data_dir, "eval.jsonl")
    gold_path = os.path.join(data_dir, "medical_gold.jsonl")

    if os.path.exists(train_path):
        with open(train_path) as f:
            stats["train_samples"] = sum(1 for _ in f)
    if os.path.exists(eval_path):
        with open(eval_path) as f:
            stats["eval_samples"] = sum(1 for _ in f)
    if os.path.exists(gold_path):
        with open(gold_path) as f:
            stats["gold_samples"] = sum(1 for _ in f)

    db_path = os.path.join(_BASE_DIR, "medical.db")
    stats["db_available"] = os.path.exists(db_path)

    return stats


def _get_model_versions():
    """배포된 모델 버전 목록"""
    output_dir = os.path.join(_TRAINING_DIR, "outputs", "medical")
    versions = []
    if not os.path.exists(output_dir):
        return versions

    for name in sorted(os.listdir(output_dir)):
        if name.startswith("result-v") and name.endswith(".json"):
            path = os.path.join(output_dir, name)
            try:
                with open(path) as f:
                    d = json.load(f)
                versions.append({
                    "version": d.get("version"),
                    "model_name": d.get("model_name"),
                    "loss": d.get("train", {}).get("loss"),
                    "samples": d.get("train", {}).get("samples"),
                    "runtime_sec": d.get("train", {}).get("runtime_sec"),
                    "timestamp": d.get("timestamp"),
                })
            except (OSError, json.JSONDecodeError, KeyError):
                continue
    return versions


def _get_config():
    """현재 학습 설정"""
    import yaml
    config_path = os.path.join(_TRAINING_DIR, "medical_config.yaml")
    if not os.path.exists(config_path):
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f)


def _run_training_pipeline():
    """백그라운드 학습 파이프라인 실행"""
    global _training_state
    venv_python = os.path.join(_BASE_DIR, "venv", "bin", "python")
    if not os.path.exists(venv_python):
        venv_python = "python3"

    try:
        # Phase 0: VRAM 확보 — Ollama 모델 언로드
        with _training_lock:
            _training_state["status"] = "preparing"
            _training_state["current_step"] = "VRAM 확보: Ollama 모델 언로드"
            _training_state["progress"] = 5

        try:
            subprocess.run(["sudo", "systemctl", "stop", "comfyui"],
                          capture_output=True, timeout=10)
            logger.info("Training VRAM: ComfyUI stopped")
        except Exception as e:
            logger.debug("ComfyUI stop failed: %s", e)
        try:
            subprocess.run(["sudo", "systemctl", "stop", "ollama"],
                          capture_output=True, timeout=10)
            logger.info("Training VRAM: Ollama stopped")
        except Exception as e:
            logger.debug("Ollama stop failed: %s", e)
        time.sleep(3)

        # Phase 1: 데이터 준비
        with _training_lock:
            _training_state["current_step"] = "데이터 수집 및 전처리"
            _training_state["progress"] = 10

        prep_script = os.path.join(_TRAINING_DIR, "prepare_medical_data.py")
        r = subprocess.run([venv_python, prep_script],
                          capture_output=True, text=True, timeout=300, cwd=_BASE_DIR)
        if r.returncode != 0:
            raise RuntimeError(f"데이터 준비 실패: {r.stderr[-500:]}")

        with _training_lock:
            _training_state["progress"] = 20
            _training_state["current_step"] = "데이터 준비 완료"

        # Phase 2: 학습
        with _training_lock:
            _training_state["status"] = "training"
            _training_state["current_step"] = "QLoRA 학습 진행 중"
            _training_state["progress"] = 30

        train_script = os.path.join(_TRAINING_DIR, "train_medical.py")
        proc = subprocess.Popen([venv_python, train_script],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, cwd=_BASE_DIR)
        with _training_lock:
            _training_state["pid"] = proc.pid

        # 로그 모니터링 (진행률 추출)
        for line in proc.stdout:
            line = line.strip()
            if "[train] 학습 시작" in line:
                with _training_lock:
                    _training_state["progress"] = 40
            elif "[train] 완료" in line:
                with _training_lock:
                    _training_state["progress"] = 70
                    _training_state["current_step"] = "학습 완료, GGUF 변환 중"
                    _training_state["status"] = "exporting"
            elif "[export] GGUF" in line:
                with _training_lock:
                    _training_state["progress"] = 85
                    _training_state["status"] = "deploying"
                    _training_state["current_step"] = "Ollama 배포 중"
            elif "[deploy] ✅" in line:
                with _training_lock:
                    _training_state["progress"] = 95

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("학습 프로세스 비정상 종료")

        # 결과 파일 로드
        output_dir = os.path.join(_TRAINING_DIR, "outputs", "medical")
        result_files = sorted([f for f in os.listdir(output_dir) if f.startswith("result-v")])
        result = {}
        if result_files:
            with open(os.path.join(output_dir, result_files[-1])) as f:
                result = json.load(f)

        # 자동 활성화: 학습 완료된 모델을 HEAVY_MODEL로 등록
        model_name = result.get("model_name")
        if model_name:
            try:
                from routes.medical import (_load_active_model_config,
                                            _save_active_model_config, get_heavy_model)
                old_heavy = get_heavy_model()
                new_cfg = _load_active_model_config()
                new_cfg.update({
                    "heavy_model": model_name,
                    "previous_heavy": old_heavy,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                    "activated_version": result.get("version", model_name),
                    "auto_activated": True,
                })
                _save_active_model_config(new_cfg)
                logger.info("Auto-activated fine-tuned model: %s (prev: %s)", model_name, old_heavy)
                result["auto_activated"] = True
            except Exception as ae:
                logger.warning("Auto-activation failed: %s (manual activate needed)", ae)
                result["auto_activated"] = False

        with _training_lock:
            _training_state["status"] = "completed"
            _training_state["progress"] = 100
            _training_state["current_step"] = "완료"
            _training_state["completed_at"] = datetime.now(timezone.utc).isoformat()
            _training_state["result"] = result
            _training_state["pid"] = None

    except Exception as e:
        logger.error("Medical training failed: %s", e)
        with _training_lock:
            _training_state["status"] = "failed"
            _training_state["error"] = str(e)
            _training_state["current_step"] = f"실패: {str(e)[:200]}"
            _training_state["pid"] = None


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@medical_training_bp.route("/api/medical/training/status")
def training_status():
    """학습 상태 조회
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
    """
    with _training_lock:
        state = dict(_training_state)

    state["data"] = _get_data_stats()
    state["versions"] = _get_model_versions()
    cfg = _get_config()
    state["config"] = {
        "base_model": cfg.get("base_model", ""),
        "lora_r": cfg.get("lora_r"),
        "num_epochs": cfg.get("num_epochs"),
        "batch_size": cfg.get("batch_size"),
    }
    # 활성 모델 정보 포함
    try:
        from routes.medical import get_heavy_model, get_light_model
        state["active_model"] = {
            "heavy": get_heavy_model(),
            "light": get_light_model(),
        }
    except ImportError:
        logger.debug("Could not import medical model info")
    return jsonify(state)


@medical_training_bp.route("/api/medical/training/prepare", methods=["POST"])
def training_prepare():
    """데이터 준비 실행
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      409:
        description: "충돌 — 이미 실행 중이거나 중복 요청"
        schema:
          type: object
          properties: {error: {type: string, example: "training in progress"}}
    """
    with _training_lock:
        if _training_state["status"] not in ("idle", "completed", "failed"):
            return jsonify({"error": "training in progress"}), 409

    venv_python = os.path.join(_BASE_DIR, "venv", "bin", "python")
    if not os.path.exists(venv_python):
        venv_python = "python3"

    prep_script = os.path.join(_TRAINING_DIR, "prepare_medical_data.py")
    try:
        r = subprocess.run([venv_python, prep_script],
                          capture_output=True, text=True, timeout=300, cwd=_BASE_DIR)
        stats = _get_data_stats()
        return jsonify({
            "ok": True,
            "output": r.stdout[-1000:],
            "data": stats,
        })
    except Exception as e:
        return internal_error(e)


@medical_training_bp.route("/api/medical/training/start", methods=["POST"])
def training_start():
    """학습 시작 (백그라운드)
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      409:
        description: "충돌 — 이미 실행 중이거나 중복 요청"
        schema:
          type: object
          properties: {error: {type: string, example: "training already in progress"}}
    """
    with _training_lock:
        if _training_state["status"] in ("preparing", "training", "exporting", "deploying"):
            return jsonify({"error": "training already in progress", "status": _training_state["status"]}), 409

        _training_state["status"] = "preparing"
        _training_state["progress"] = 0
        _training_state["current_step"] = "시작 중..."
        _training_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _training_state["completed_at"] = None
        _training_state["error"] = None
        _training_state["result"] = None

    thread = threading.Thread(target=_run_training_pipeline, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "training started in background"})


@medical_training_bp.route("/api/medical/training/stop", methods=["POST"])
def training_stop():
    """학습 중지
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "no training process running"}}
    """
    import signal as _signal
    with _training_lock:
        pid = _training_state.get("pid")
        if not pid:
            return jsonify({"error": "no training process running"}), 400
        try:
            os.kill(pid, _signal.SIGTERM)
            _training_state["status"] = "idle"
            _training_state["current_step"] = "사용자에 의해 중지됨"
            _training_state["pid"] = None
        except ProcessLookupError:
            _training_state["status"] = "idle"
            _training_state["pid"] = None
    return jsonify({"ok": True})


@medical_training_bp.route("/api/medical/training/versions")
def training_versions():
    """배포된 모델 버전 목록
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            versions: {}
    """
    return jsonify({"versions": _get_model_versions()})


@medical_training_bp.route("/api/medical/training/active")
def training_active():
    """현재 활성 모델 조회
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: 현재 활성 모델 정보
        schema:
          type: object
          properties:
            heavy_model: {}
            light_model: {}
            activated_at: {}
            activated_version: {}
            previous_heavy: {}
    """
    from routes.medical import get_heavy_model, get_light_model, _load_active_model_config
    cfg = _load_active_model_config()
    return jsonify({
        "heavy_model": get_heavy_model(),
        "light_model": get_light_model(),
        "activated_at": cfg.get("activated_at"),
        "activated_version": cfg.get("activated_version"),
        "previous_heavy": cfg.get("previous_heavy"),
    })


@medical_training_bp.route("/api/medical/training/activate", methods=["POST"])
def training_activate():
    """파인튜닝 모델 활성화 — Ollama 검증 후 적용
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            model_name: {type: string, description: "Ollama 모델명 (예: medical-qwen:v1)"}
            version: {type: string, description: "버전 (model_name 대신 사용 가능)"}
            target: {type: string, enum: [heavy, light, both], default: heavy}
    responses:
      200:
        description: 활성화 성공
        schema:
          type: object
          properties:
            ok: {type: boolean}
            model_name: {}
            target: {}
            heavy_model: {}
            light_model: {}
            test_response: {}
      400:
        description: 모델 검증 실패
    """
    data = request.json or {}
    model_name = data.get("model_name", "").strip()
    version = data.get("version", "").strip()
    target = data.get("target", "heavy")

    # version으로 model_name 자동 결정
    if not model_name and version:
        cfg = _get_config()
        prefix = cfg.get("model_name_prefix", "medical-qwen")
        model_name = f"{prefix}:{version}"

    if not model_name:
        return jsonify({"error": "model_name or version required"}), 400

    # Step 1: Ollama에서 모델 존재 확인
    try:
        r = http_requests.post(f"{_OLLAMA_BASE}/api/show",
                               json={"name": model_name}, timeout=10)
        if r.status_code != 200:
            return jsonify({"error": f"model '{model_name}' not found in Ollama",
                           "hint": "Run training pipeline first or register manually with 'ollama create'"}), 400
    except Exception as e:
        return jsonify({"error": f"Ollama connection failed: {e}"}), 503

    # Step 2: 모델 테스트 — 간단한 프롬프트로 응답 확인
    try:
        r = http_requests.post(f"{_OLLAMA_BASE}/api/generate",
                               json={"model": model_name,
                                     "prompt": "안녕하세요. 테스트입니다.",
                                     "stream": False,
                                     "options": {"num_predict": 20}},
                               timeout=60)
        if r.status_code != 200:
            return jsonify({"error": f"model test failed (HTTP {r.status_code})"}), 400
        test_response = r.json().get("response", "")
        if len(test_response) < 2:
            return jsonify({"error": "model test returned empty response"}), 400
    except Exception as e:
        return jsonify({"error": f"model test failed: {e}"}), 400

    # Step 3: 현재 설정 백업 & 새 모델 활성화
    from routes.medical import _load_active_model_config, _save_active_model_config, get_heavy_model, get_light_model

    old_cfg = _load_active_model_config()
    new_cfg = dict(old_cfg)
    new_cfg["previous_heavy"] = get_heavy_model()
    new_cfg["previous_light"] = get_light_model()
    new_cfg["activated_at"] = datetime.now(timezone.utc).isoformat()
    new_cfg["activated_version"] = version or model_name

    if target in ("heavy", "both"):
        new_cfg["heavy_model"] = model_name
    if target in ("light", "both"):
        new_cfg["light_model"] = model_name

    _save_active_model_config(new_cfg)

    logger.info("Medical model activated: %s (target=%s, prev_heavy=%s)",
                model_name, target, old_cfg.get("heavy_model", "default"))

    return jsonify({
        "ok": True,
        "model_name": model_name,
        "target": target,
        "heavy_model": new_cfg.get("heavy_model", get_heavy_model()),
        "light_model": new_cfg.get("light_model", get_light_model()),
        "test_response": test_response[:100],
    })


@medical_training_bp.route("/api/medical/training/rollback", methods=["POST"])
def training_rollback():
    """이전 모델로 롤백
    ---
    tags: [Medical Training]
    security: [{Bearer: []}]
    responses:
      200:
        description: 롤백 성공
        schema:
          type: object
          properties:
            ok: {type: boolean}
            heavy_model: {}
            light_model: {}
            rolled_back_from: {}
            message: {type: string}
      400:
        description: 롤백할 이전 모델 없음
    """
    from routes.medical import _load_active_model_config, _save_active_model_config, _DEFAULT_HEAVY, _DEFAULT_LIGHT

    cfg = _load_active_model_config()
    prev_heavy = cfg.get("previous_heavy")
    prev_light = cfg.get("previous_light")

    if not prev_heavy and not prev_light:
        # 기본값으로 초기화
        if os.path.exists(os.path.join(_BASE_DIR, "medical_model_active.json")):
            os.remove(os.path.join(_BASE_DIR, "medical_model_active.json"))
            return jsonify({
                "ok": True,
                "message": "reset to defaults",
                "heavy_model": _DEFAULT_HEAVY,
                "light_model": _DEFAULT_LIGHT,
            })
        return jsonify({"error": "no previous model to rollback to"}), 400

    new_cfg = {
        "heavy_model": prev_heavy or _DEFAULT_HEAVY,
        "light_model": prev_light or _DEFAULT_LIGHT,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "activated_version": "rollback",
        "previous_heavy": cfg.get("heavy_model"),
        "previous_light": cfg.get("light_model"),
    }
    _save_active_model_config(new_cfg)

    logger.info("Medical model rollback: heavy=%s, light=%s", new_cfg["heavy_model"], new_cfg["light_model"])

    return jsonify({
        "ok": True,
        "heavy_model": new_cfg["heavy_model"],
        "light_model": new_cfg["light_model"],
        "rolled_back_from": cfg.get("heavy_model"),
    })


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

TRAINING_UI_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Medical AI Fine-tuning</title>
<style>
:root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --muted: #8b949e; --accent: #58a6ff; --green: #3fb950; --red: #f85149; --yellow: #d29922; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 20px; max-width: 900px; margin: 0 auto; }
h1 { font-size: 20px; margin-bottom: 20px; }
h2 { font-size: 15px; margin-bottom: 10px; color: var(--accent); }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.stat { text-align: center; }
.stat .value { font-size: 24px; font-weight: bold; }
.stat .label { font-size: 11px; color: var(--muted); margin-top: 2px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.badge.idle { background: var(--border); color: var(--muted); }
.badge.training { background: #1f6feb33; color: var(--accent); }
.badge.completed { background: #23883033; color: var(--green); }
.badge.failed { background: #da363333; color: var(--red); }
.badge.preparing, .badge.exporting, .badge.deploying { background: #9e6a0333; color: var(--yellow); }
.progress-bar { width: 100%; height: 8px; background: var(--border); border-radius: 4px; margin: 8px 0; overflow: hidden; }
.progress-fill { height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.5s; }
.btn { padding: 8px 16px; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 13px; margin-right: 8px; transition: all 0.2s; }
.btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-danger { background: var(--red); color: #fff; border-color: var(--red); }
.btn-success { background: var(--green); color: #fff; border-color: var(--green); }
.btn-warn { background: var(--yellow); color: #000; border-color: var(--yellow); }
.btn-sm { padding: 3px 10px; font-size: 11px; margin: 0; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn:hover:not(:disabled) { opacity: 0.8; }
.model-tag { display: inline-block; padding: 3px 10px; border-radius: 4px; font-family: monospace; font-size: 13px; font-weight: 600; background: #1f6feb22; color: var(--accent); border: 1px solid var(--accent); }
.model-row { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.model-label { font-size: 11px; color: var(--muted); min-width: 55px; }
.active-marker { color: var(--green); font-weight: bold; font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: left; }
th { color: var(--muted); font-weight: 600; }
.log { background: #0d1117; border: 1px solid var(--border); border-radius: 4px; padding: 12px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; color: var(--muted); }
.meta { font-size: 11px; color: var(--muted); }
a { color: var(--accent); text-decoration: none; }
</style>
</head>
<body>
<h1>🧬 Medical AI Fine-tuning</h1>

<div class="card">
  <h2>학습 상태</h2>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
    <span class="badge" id="statusBadge">-</span>
    <span id="stepText" class="meta">-</span>
  </div>
  <div class="progress-bar"><div class="progress-fill" id="progressBar" style="width:0%"></div></div>
  <div class="meta" id="timeInfo"></div>
  <div style="margin-top:12px;">
    <button class="btn btn-primary" id="btnPrepare" onclick="prepare()">📦 데이터 준비</button>
    <button class="btn btn-primary" id="btnStart" onclick="startTraining()">🚀 학습 시작</button>
    <button class="btn btn-danger" id="btnStop" onclick="stopTraining()" disabled>⏹ 중지</button>
  </div>
</div>

<div class="card">
  <h2>🎯 활성 모델</h2>
  <div class="model-row">
    <span class="model-label">HEAVY</span>
    <span class="model-tag" id="activeHeavy">-</span>
    <span class="meta" id="activeHeavyInfo"></span>
  </div>
  <div class="model-row">
    <span class="model-label">LIGHT</span>
    <span class="model-tag" id="activeLight">-</span>
  </div>
  <div style="margin-top:10px;display:flex;align-items:center;gap:8px;">
    <button class="btn btn-warn btn-sm" onclick="rollbackModel()">⏪ 이전 모델로 롤백</button>
    <button class="btn btn-sm" onclick="resetModel()">🔄 기본값 초기화</button>
    <span class="meta" id="activeMeta"></span>
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2>학습 데이터</h2>
    <div class="grid">
      <div class="stat"><div class="value" id="trainCount">-</div><div class="label">Train 샘플</div></div>
      <div class="stat"><div class="value" id="evalCount">-</div><div class="label">Eval 샘플</div></div>
      <div class="stat"><div class="value" id="goldCount">-</div><div class="label">Gold 데이터</div></div>
      <div class="stat"><div class="value" id="dbStatus">-</div><div class="label">Medical DB</div></div>
    </div>
  </div>
  <div class="card">
    <h2>학습 설정</h2>
    <div class="meta">
      <div id="configInfo">-</div>
    </div>
  </div>
</div>

<div class="card">
  <h2>배포된 모델 버전</h2>
  <table>
    <thead><tr><th>버전</th><th>모델명</th><th>Loss</th><th>샘플</th><th>소요</th><th>날짜</th><th></th></tr></thead>
    <tbody id="versionsTable"><tr><td colspan="6" class="meta">로딩 중...</td></tr></tbody>
  </table>
</div>

<div class="card" id="resultCard" style="display:none">
  <h2>최근 결과</h2>
  <div class="log" id="resultLog"></div>
</div>

<div class="meta" style="margin-top:20px;text-align:center;">
  <a href="./">← Dashboard</a> &nbsp;|&nbsp; <a href="docs">Swagger</a> &nbsp;|&nbsp; Auto-refresh: 5s
</div>

<script>
const BASE = (() => { const p = window.location.pathname.replace(/\\/medical-training\\/?$/, ''); return p || ''; })();

let _activeModel = {};

async function fetchStatus() {
  try {
    const [statusR, activeR] = await Promise.all([
      fetch(BASE + '/api/medical/training/status'),
      fetch(BASE + '/api/medical/training/active'),
    ]);
    const d = await statusR.json();
    _activeModel = await activeR.json();
    updateUI(d);
    updateActiveUI(_activeModel);
  } catch(e) { console.error(e); }
}

function updateActiveUI(a) {
  document.getElementById('activeHeavy').textContent = a.heavy_model || '-';
  document.getElementById('activeLight').textContent = a.light_model || '-';
  const info = [];
  if (a.activated_version && a.activated_version !== 'rollback')
    info.push('v' + a.activated_version);
  if (a.activated_at)
    info.push(new Date(a.activated_at).toLocaleString('ko'));
  document.getElementById('activeHeavyInfo').textContent = info.join(' | ');
  const meta = a.previous_heavy ? '이전: ' + a.previous_heavy : '';
  document.getElementById('activeMeta').textContent = meta;
}

function updateUI(d) {
  // Status badge
  const badge = document.getElementById('statusBadge');
  badge.textContent = d.status;
  badge.className = 'badge ' + d.status;

  document.getElementById('stepText').textContent = d.current_step || '';
  document.getElementById('progressBar').style.width = d.progress + '%';

  // Time
  let timeText = '';
  if (d.started_at) timeText += '시작: ' + new Date(d.started_at).toLocaleString('ko');
  if (d.completed_at) timeText += ' | 완료: ' + new Date(d.completed_at).toLocaleString('ko');
  document.getElementById('timeInfo').textContent = timeText;

  // Buttons
  const running = ['preparing','training','exporting','deploying'].includes(d.status);
  document.getElementById('btnPrepare').disabled = running;
  document.getElementById('btnStart').disabled = running;
  document.getElementById('btnStop').disabled = !running;

  // Data stats
  if (d.data) {
    document.getElementById('trainCount').textContent = d.data.train_samples;
    document.getElementById('evalCount').textContent = d.data.eval_samples;
    document.getElementById('goldCount').textContent = d.data.gold_samples;
    document.getElementById('dbStatus').textContent = d.data.db_available ? '✅' : '❌';
  }

  // Config
  if (d.config && d.config.base_model) {
    document.getElementById('configInfo').innerHTML =
      'Base: <b>' + d.config.base_model.split('/').pop() + '</b><br>' +
      'LoRA r=' + d.config.lora_r + ', epochs=' + d.config.num_epochs + ', batch=' + d.config.batch_size;
  }

  // Versions
  const tbody = document.getElementById('versionsTable');
  if (d.versions && d.versions.length) {
    const activeHeavy = _activeModel.heavy_model || '';
    tbody.innerHTML = d.versions.reverse().map(v => {
      const isActive = v.model_name && v.model_name === activeHeavy;
      const marker = isActive ? ' <span class="active-marker">✓ 활성</span>' : '';
      const btn = v.model_name && !isActive
        ? '<button class="btn btn-success btn-sm" onclick="activateModel(&quot;' + v.model_name + '&quot;,&quot;' + v.version + '&quot;)">활성화</button>'
        : (isActive ? '<span class="active-marker">사용 중</span>' : '');
      return '<tr><td>v' + v.version + '</td><td>' + (v.model_name||'-') + marker + '</td>' +
        '<td>' + (v.loss ? v.loss.toFixed(4) : '-') + '</td>' +
        '<td>' + (v.samples||'-') + '</td>' +
        '<td>' + (v.runtime_sec ? Math.round(v.runtime_sec) + 's' : '-') + '</td>' +
        '<td class="meta">' + (v.timestamp||'') + '</td>' +
        '<td>' + btn + '</td></tr>';
    }).join('');
  } else {
    tbody.innerHTML = '<tr><td colspan="7" class="meta">배포된 모델 없음</td></tr>';
  }

  // Result
  if (d.result) {
    document.getElementById('resultCard').style.display = 'block';
    document.getElementById('resultLog').textContent = JSON.stringify(d.result, null, 2);
  }

  if (d.error) {
    document.getElementById('resultCard').style.display = 'block';
    document.getElementById('resultLog').textContent = '❌ ERROR: ' + d.error;
  }
}

async function prepare() {
  if (!confirm('데이터 준비를 시작합니까?')) return;
  document.getElementById('btnPrepare').disabled = true;
  const r = await fetch(BASE + '/api/medical/training/prepare', {method:'POST'});
  const d = await r.json();
  alert(d.ok ? '데이터 준비 완료 (train: ' + d.data.train_samples + '건)' : '실패: ' + d.error);
  fetchStatus();
}

async function startTraining() {
  if (!confirm('학습을 시작합니까? (GPU 사용, 1-2시간 소요)')) return;
  const r = await fetch(BASE + '/api/medical/training/start', {method:'POST'});
  const d = await r.json();
  if (!d.ok) alert('실패: ' + (d.error||''));
  fetchStatus();
}

async function stopTraining() {
  if (!confirm('학습을 중지합니까?')) return;
  await fetch(BASE + '/api/medical/training/stop', {method:'POST'});
  fetchStatus();
}

async function activateModel(modelName, version) {
  if (!confirm(modelName + ' 모델을 활성화합니까?' + '\\n' + '(Ollama 검증 + 테스트 프롬프트 실행)')) return;
  const btn = event.target; btn.disabled = true; btn.textContent = '검증 중...';
  try {
    const r = await fetch(BASE + '/api/medical/training/activate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({model_name: modelName, version: version, target: 'heavy'})
    });
    const d = await r.json();
    if (d.ok) {
      alert('모델 활성화 완료' + '\\n' + 'HEAVY: ' + d.heavy_model + '\\n' + '테스트 응답: ' + d.test_response);
    } else {
      alert('❌ 활성화 실패: ' + (d.error||''));
    }
  } catch(e) { alert('❌ 오류: ' + e.message); }
  fetchStatus();
}

async function rollbackModel() {
  if (!_activeModel.previous_heavy) {
    alert('롤백할 이전 모델이 없습니다.');
    return;
  }
  if (!confirm('이전 모델(' + _activeModel.previous_heavy + ')로 롤백합니까?')) return;
  try {
    const r = await fetch(BASE + '/api/medical/training/rollback', {
      method: 'POST', headers: {'Content-Type':'application/json'}
    });
    const d = await r.json();
    if (d.ok) {
      alert('✅ 롤백 완료: ' + d.heavy_model);
    } else {
      alert('❌ 실패: ' + (d.error||''));
    }
  } catch(e) { alert('❌ 오류: ' + e.message); }
  fetchStatus();
}

async function resetModel() {
  if (!confirm('기본 모델(qwen2.5:14b / qwen2.5:7b)로 초기화합니까?')) return;
  try {
    const r = await fetch(BASE + '/api/medical/training/rollback', {
      method: 'POST', headers: {'Content-Type':'application/json'}
    });
    const d = await r.json();
    alert(d.ok ? '✅ 기본값으로 초기화: ' + d.heavy_model : '❌ ' + (d.error||''));
  } catch(e) { alert('❌ 오류: ' + e.message); }
  fetchStatus();
}

fetchStatus();
setInterval(fetchStatus, 5000);
</script>
</body>
</html>
"""


@medical_training_bp.route("/medical-training")
def training_ui():
    """Medical AI Fine-tuning 관리 화면"""
    return render_template_string(TRAINING_UI_HTML)
