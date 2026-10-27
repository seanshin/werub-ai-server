"""Training API Blueprint — Fine-tuning 관리"""
import os
import json
import time
import sqlite3 as _sqlite3
import subprocess
import shutil
import logging
from flask import Blueprint, request, jsonify, session
from core.errors import internal_error
from werkzeug.utils import secure_filename

from core.config import OLLAMA_BASE
from core.password import hash_password as _core_hash_password

logger = logging.getLogger("ollama-admin")

training_bp = Blueprint("training", __name__)

# app.py에서 주입
_ollama_session = None
_get_main_db = None

def init_training(ollama_session, get_db_fn, ollama_base=None, system_info=None):
    global _ollama_session, _get_main_db
    global _OLLAMA_BASE
    global _SYSTEM_INFO
    _ollama_session = ollama_session
    _get_main_db = get_db_fn
    _OLLAMA_BASE = ollama_base
    _SYSTEM_INFO = system_info

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "queue.db")
TRAINING_DB = os.path.join(BASE_DIR, "training.db")

# Content Safety — Training API (Fine-tuning 관리)
# ═══════════════════════════════════════════════════════════════════════════════

import sqlite3 as _sqlite3
TRAINING_DB = os.path.join(os.path.dirname(DB_PATH), "training.db")

def _get_training_db():
    conn = _sqlite3.connect(TRAINING_DB, timeout=10)
    conn.row_factory = _sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS training_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        base_model TEXT NOT NULL,
        sample_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'P2',
        precision_score REAL, recall_score REAL, f1_score REAL,
        prev_f1_score REAL,
        model_path TEXT, error_message TEXT,
        trigger_type TEXT DEFAULT 'manual',
        started_at TEXT, completed_at TEXT, deployed_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS training_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    return conn


# ═══ 사용자 관리 DB ═══════════════════════════════════════════════════════════
USERS_DB = os.path.join(os.path.dirname(DB_PATH), "users.db")

def _get_users_db():
    conn = __import__("sqlite3").connect(USERS_DB, timeout=10)
    conn.row_factory = __import__("sqlite3").Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        role TEXT NOT NULL DEFAULT 'viewer',
        permissions TEXT DEFAULT '{}',
        is_active INTEGER DEFAULT 1,
        last_login TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    return conn

# 역할별 기본 권한
ROLE_DEFAULTS = {
    "admin": {"dashboard":True,"queue":True,"models":True,"chat":True,"generate":True,"knowledge":True,"image":True,"activity":True,"settings":True,"logs":True,"training":True},
    "operator": {"dashboard":True,"queue":False,"models":False,"chat":False,"generate":False,"knowledge":False,"image":False,"activity":False,"settings":False,"logs":False,"training":True},
    "viewer": {"dashboard":True,"queue":False,"models":False,"chat":True,"generate":False,"knowledge":False,"image":False,"activity":True,"settings":False,"logs":False,"training":False},
}

def _check_user_permission(tab_name):
    """현재 세션 사용자의 탭 접근 권한 확인"""
    username = session.get("web_user", "admin")
    conn = _get_users_db()
    try:
        user = conn.execute("SELECT role, permissions FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
    finally:
        conn.close()
    if not user:
        return True  # 기존 호환 (user 없으면 허용)
    try:
        perms = json.loads(user["permissions"] or "{}")
    except Exception:
        perms = ROLE_DEFAULTS.get(user["role"], {})
    return perms.get(tab_name, False)


def _training_config(key, default=None):
    conn = _get_training_db()
    try:
        row = conn.execute("SELECT value FROM training_config WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    return row["value"] if row else default

def _set_training_config(key, value):
    conn = _get_training_db()
    try:
        conn.execute("INSERT OR REPLACE INTO training_config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()

# GPU 유휴 검사
def _is_gpu_idle():
    """P0/P1 작업이 없고 GPU < 10%일 때 유휴"""
    try:
        conn = _get_main_db()
        try:
            pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]
        finally:
            conn.close()
        if pending > 0:
            return False
        # GPU 사용률 체크
        if shutil.which("nvidia-smi"):
            r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                gpu_pct = int(r.stdout.strip())
                return gpu_pct < 10
        return True
    except Exception:
        return True

def _is_allowed_hours():
    """학습 허용 시간대 체크"""
    allowed = _training_config("allowed_hours", "00-06")
    if allowed == "24h":
        return True
    try:
        start, end = allowed.split("-")
        now_h = datetime.now().hour
        return int(start) <= now_h < int(end)
    except Exception:
        return True

# 학습 상태 추적
_training_status = {"running": False, "run_id": None, "paused": False}

@training_bp.route("/api/training/status")
def training_status():
    """Training pipeline status
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            running: {type: boolean}
            paused: {type: boolean}
            run_id: {type: integer}
            gpu_idle: {type: boolean}
            allowed_hours: {type: boolean}
            config: {type: object}
    """
    conn = _get_training_db()
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM training_runs ORDER BY id DESC LIMIT 10"
        ).fetchall()]
    finally:
        conn.close()

    return jsonify({
        "running": _training_status["running"],
        "paused": _training_status["paused"],
        "run_id": _training_status["run_id"],
        "gpu_idle": _is_gpu_idle(),
        "allowed_hours": _is_allowed_hours(),
        "config": {
            "allowed_hours": _training_config("allowed_hours", "00-06"),
            "gpu_max_pct": int(_training_config("gpu_max_pct", "80")),
            "auto_pause": _training_config("auto_pause", "true") == "true",
            "max_duration_min": int(_training_config("max_duration_min", "120")),
            "auto_trigger_threshold": int(_training_config("auto_trigger_threshold", "50")),
        },
        "recent_runs": runs,
    })

@training_bp.route("/api/training/start", methods=["POST"])
def training_start():
    """Start fine-tuning (queued if GPU busy)
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: body, in: body, schema: {type: object, properties: {
          base_model: {type: string, default: "qwen2.5:1.5b"},
          force: {type: boolean, default: false},
          trigger_type: {type: string, default: "manual"}
        }}}
    responses:
      200:
        schema:
          type: object
          properties:
            status: {type: string}
            run_id: {type: integer}
    """
    if _training_status["running"]:
        return jsonify({"status": "already_running", "run_id": _training_status["run_id"]}), 409

    body = request.json or {}
    base_model = body.get("base_model", "qwen2.5:1.5b")
    force = body.get("force", False)
    trigger_type = body.get("trigger_type", "manual")

    # 안전장치: 디스크 + VRAM 사전 체크
    try:
        st = os.statvfs("/")
        free_gb = st.f_frsize * st.f_bavail / (1024**3)
        if free_gb < 20:
            return jsonify({"status": "error", "reason": f"디스크 부족 ({free_gb:.0f}GB < 20GB)"}), 400
    except Exception: pass

    # 유휴 검사 (force가 아니면)
    if not force:
        if not _is_gpu_idle():
            conn = _get_training_db()
            try:
                conn.execute(
                    "INSERT INTO training_runs (base_model, status, priority, trigger_type) VALUES (?, 'queued', 'P2', ?)",
                    (base_model, trigger_type))
                conn.commit()
                run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            finally:
                conn.close()
            return jsonify({"status": "queued", "run_id": run_id, "reason": "GPU busy"})

        if not _is_allowed_hours():
            conn = _get_training_db()
            try:
                conn.execute(
                    "INSERT INTO training_runs (base_model, status, priority, trigger_type) VALUES (?, 'waiting', 'P2', ?)",
                    (base_model, trigger_type))
                conn.commit()
                run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            finally:
                conn.close()
            return jsonify({"status": "scheduled", "run_id": run_id, "reason": "Outside allowed hours"})

    # 학습 시작
    conn = _get_training_db()
    try:
        conn.execute(
            "INSERT INTO training_runs (base_model, status, priority, trigger_type, started_at) VALUES (?, 'running', ?, ?, datetime('now'))",
            (base_model, "P1" if force else "P2", trigger_type))
        conn.commit()
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()

    _training_status["running"] = True
    _training_status["run_id"] = run_id
    _training_status["paused"] = False

    # 실제 학습 파이프라인 백그라운드 실행
    import threading
    def _run_pipeline(rid, trigger):
        try:
            r = subprocess.run(
                [sys.executable, "training/pipeline.py", trigger],
                capture_output=True, text=True, timeout=7200, cwd=os.path.dirname(os.path.abspath(__file__))
            )
            _training_status["running"] = False
            _training_status["run_id"] = None
            if r.returncode == 0:
                # 로그 기록
                for line in (r.stdout or "").split("\n")[-20:]:
                    if line.strip():
                        print(f"[training] {line}")
            else:
                print(f"[training] Pipeline failed: {r.stderr[-500:] if r.stderr else 'unknown'}")
        except Exception as e:
            _training_status["running"] = False
            _training_status["run_id"] = None
            print(f"[training] Pipeline error: {e}")

    threading.Thread(target=_run_pipeline, args=(run_id, trigger_type), daemon=True).start()

    return jsonify({"status": "running", "run_id": run_id})

@training_bp.route("/api/training/pause", methods=["POST"])
def training_pause():
    """Pause running training
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            status: {type: string}
    """
    if not _training_status["running"]:
        return jsonify({"status": "not_running"}), 404
    _training_status["paused"] = True
    conn = _get_training_db()
    try:
        conn.execute("UPDATE training_runs SET status='paused' WHERE id=?", (_training_status["run_id"],))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "paused", "run_id": _training_status["run_id"]})

@training_bp.route("/api/training/resume", methods=["POST"])
def training_resume():
    """Resume paused training
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            status: {type: string}
    """
    if not _training_status["paused"]:
        return jsonify({"status": "not_paused"}), 404
    _training_status["paused"] = False
    conn = _get_training_db()
    try:
        conn.execute("UPDATE training_runs SET status='running' WHERE id=?", (_training_status["run_id"],))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "resumed", "run_id": _training_status["run_id"]})

@training_bp.route("/api/training/stop", methods=["POST"])
def training_stop():
    """Force stop training
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            status: {type: string}
    """
    run_id = _training_status.get("run_id")
    _training_status["running"] = False
    _training_status["paused"] = False
    _training_status["run_id"] = None
    if run_id:
        conn = _get_training_db()
        try:
            conn.execute("UPDATE training_runs SET status='failed', error_message='Force stopped', completed_at=datetime('now') WHERE id=?", (run_id,))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"status": "stopped", "run_id": run_id})

@training_bp.route("/api/training/runs")
def training_runs():
    """List training runs
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: limit, in: query, type: integer, default: 20}
    responses:
      200:
        schema:
          type: object
          properties:
            runs: {type: array, items: {type: object}}
    """
    limit = min(int(request.args.get("limit", 20)), 100)
    conn = _get_training_db()
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM training_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()
    return jsonify({"runs": runs})

@training_bp.route("/api/training/runs/<int:run_id>")
def training_run_detail(run_id):
    """Get training run detail
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_training_db()
    try:
        run = conn.execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(run))


@training_bp.route("/api/training/runs/<int:run_id>", methods=["DELETE"])
def training_run_delete(run_id):
    """Delete a training run
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: integer, required: true}
    responses:
      200:
        schema: {type: object}
    """
    conn = _get_training_db()
    try:
        conn.execute("DELETE FROM training_runs WHERE id=?", (run_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted": run_id})


@training_bp.route("/api/training/runs/<int:run_id>/report")
def training_run_report(run_id):
    """Comprehensive training run report
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: integer, required: true}
    responses:
      200:
        schema: {type: object}
    """
    conn = _get_training_db()
    try:
        run = conn.execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    if not run:
        return jsonify({"error": "not found"}), 404

    report = dict(run)

    # train_result.json
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", "outputs")
    tr_path = os.path.join(output_dir, "train_result.json")
    if os.path.exists(tr_path):
        with open(tr_path) as f:
            report["train_detail"] = json.load(f)

    # eval_result.json
    ev_path = os.path.join(output_dir, "eval_result.json")
    if os.path.exists(ev_path):
        with open(ev_path) as f:
            report["eval_detail"] = json.load(f)

    # 자동 진단
    report["diagnosis"] = _diagnose_training(report)

    return jsonify(report)

@training_bp.route("/api/training/runs/<int:run_id>/misclassified")
def training_run_misclassified(run_id):
    """Get misclassified samples
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: integer, required: true}
      - {name: type, in: query, type: string, description: "FP or FN"}
      - {name: limit, in: query, type: integer, default: 50}
    responses:
      200:
        schema: {type: object}
    """
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", "outputs")
    ev_path = os.path.join(output_dir, "eval_result.json")
    if not os.path.exists(ev_path):
        return jsonify({"error": "eval result not found"}), 404

    with open(ev_path) as f:
        ev = json.load(f)

    mis = ev.get("misclassified", [])
    filter_type = request.args.get("type", "").upper()
    if filter_type in ("FP", "FN"):
        mis = [m for m in mis if m.get("type") == filter_type]

    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"misclassified": mis[:limit], "total": len(mis)})

def _diagnose_training(report):
    """학습 결과 자동 진단"""
    issues = []
    recommendations = []

    ev = report.get("eval_detail", {})
    tr = report.get("train_detail", {})
    f1 = ev.get("f1_score", 0)
    recall = ev.get("recall", 0)
    precision = ev.get("precision", 0)
    fn = ev.get("fn", 0)
    fp = ev.get("fp", 0)
    total = ev.get("total", 1)
    loss = tr.get("loss", 0)
    samples = tr.get("samples", 0)

    if f1 == 0 and recall < 0.01:
        issues.append("모델이 유해 콘텐츠를 전혀 감지하지 못합니다")
        issues.append("JSON 형식 출력이 불안정합니다")
        recommendations.append("모델 크기를 3b 이상으로 변경하세요")
        recommendations.append("Chat Template 포맷으로 재학습하세요")
    elif recall < 0.5:
        issues.append(f"유해 텍스트 감지율이 낮습니다 ({recall*100:.0f}%)")
        recommendations.append("유해 사례 데이터를 더 추가하세요")
        recommendations.append("카테고리 가중치를 높여보세요")

    if precision < 0.8 and precision > 0:
        issues.append(f"정상 텍스트 오차단율이 높습니다 (정확도 {precision*100:.0f}%)")
        recommendations.append("정상(clean) 사례를 더 추가하세요")

    if fn > total * 0.3:
        issues.append(f"놓친 유해 텍스트가 {fn}건 ({fn/total*100:.0f}%)입니다")

    if fp > total * 0.1:
        issues.append(f"잘못 차단한 정상 텍스트가 {fp}건입니다")

    if loss > 1.5:
        issues.append("학습이 충분히 수렴하지 않았습니다")
        recommendations.append("에포크 수를 늘려보세요 (3→5)")

    if samples < 500:
        issues.append(f"학습 데이터가 부족합니다 ({samples}건)")
        recommendations.append("최소 500건 이상 필요합니다")

    if not issues:
        issues.append("특별한 문제가 발견되지 않았습니다")

    return {"issues": issues, "recommendations": recommendations, "passed": f1 >= 0.85}


@training_bp.route("/api/training/config", methods=["GET", "POST"])
def training_config_api():
    """Get/set training configuration
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    if request.method == "GET":
        return jsonify({
            "allowed_hours": _training_config("allowed_hours", "00-06"),
            "gpu_max_pct": int(_training_config("gpu_max_pct", "80")),
            "auto_pause": _training_config("auto_pause", "true") == "true",
            "max_duration_min": int(_training_config("max_duration_min", "120")),
            "auto_trigger_threshold": int(_training_config("auto_trigger_threshold", "50")),
            "priority": _training_config("priority", "P2"),
        })

    body = request.json or {}
    # 🔴 모르는 키는 400으로 거절한다 — 조용히 버리고 {"ok": true}를 주면 안 된다.
    #    2026-04-21 '학습 방향' 카드(preset·system_prompt·category_weights)가 이 경로로 저장했는데
    #    아래 허용 목록에 없어 **한 번도 저장되지 않았고** 화면은 '✅ 저장됨'을 띄웠다(v2.125.64에서 발견·5개월).
    #    거절은 **아무것도 쓰기 전에** 한다(일부만 저장된 채 400이 나가면 호출자가 상태를 알 수 없다).
    #    ⚠️ 새 학습 설정 키를 추가하면 여기 목록과 GET 응답 둘 다에 넣을 것.
    allowed = ["allowed_hours", "gpu_max_pct", "auto_pause", "max_duration_min", "auto_trigger_threshold", "priority"]
    unknown = sorted(k for k in body if k not in allowed)
    if unknown:
        return jsonify({"ok": False, "error": "unknown_keys", "keys": unknown,
                        "reason": "저장하지 않는 키: " + ", ".join(unknown)}), 400
    for key in allowed:
        if key in body:
            _set_training_config(key, body[key])
    return jsonify({"ok": True})

@training_bp.route("/api/training/deploy/<int:run_id>", methods=["POST"])
def training_deploy(run_id):
    """Deploy a trained model
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_training_db()
    try:
        run = conn.execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return jsonify({"error": "not found"}), 404
        conn.execute("UPDATE training_runs SET status='deployed', deployed_at=datetime('now') WHERE id=?", (run_id,))
        # 이전 deployed 롤백
        conn.execute("UPDATE training_runs SET status='completed' WHERE status='deployed' AND id!=?", (run_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "deployed", "run_id": run_id})

@training_bp.route("/api/training/rollback/<int:run_id>", methods=["POST"])
def training_rollback(run_id):
    """Rollback to a previous model
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: run_id, in: path, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_training_db()
    try:
        run = conn.execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return jsonify({"error": "not found"}), 404
        # 현재 deployed 해제
        conn.execute("UPDATE training_runs SET status='rolled_back' WHERE status='deployed'")
        # 대상 배포
        conn.execute("UPDATE training_runs SET status='deployed', deployed_at=datetime('now') WHERE id=?", (run_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "rolled_back_to", "run_id": run_id})


@training_bp.route("/api/training/cli", methods=["POST"])
def training_cli():
    """Execute content safety CLI command
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: body, in: body, schema: {type: object, properties: {command: {type: string}}}}
    responses:
      200:
        schema: {type: object, properties: {output: {type: string}}}
    """
    body = request.json or {}
    cmd = (body.get("command") or "").strip()
    if not cmd:
        return jsonify({"output": u"\ub3c4\uc6c0\ub9d0\uc744 \uc785\ub825\ud558\uba74 \uc0ac\uc6a9\ubc95\uc744 \ubcfc \uc218 \uc788\uc2b5\ub2c8\ub2e4."})
    out = _handle_cli(cmd)
    return jsonify({"output": out})

def _handle_cli(cmd):
    global _queue_paused
    p = cmd.split()
    m, args = p[0], p[1:]

    HELP = (
        u"\U0001f4cb Content Safety CLI \ub3c4\uc6c0\ub9d0\n\n"
        u"\u2501\u2501 \uc2dc\uc2a4\ud15c \u2501\u2501\n"
        u"  \uc0c1\ud0dc              \uc804\uccb4 \ud604\ud669\n\n"
        u"\u2501\u2501 \ud328\ud134 \u2501\u2501\n"
        u"  \ud328\ud134 \ud14c\uc2a4\ud2b8 <\ud14d\uc2a4\ud2b8>  \uc695\uc124/\ube44\uc18d\uc5b4 \ud328\ud134 \uac80\uc0ac\n"
        u"  \ud328\ud134 \uc81c\uc548 <\ub2e8\uc5b4>      \uc6b0\ud68c \ubcc0\ud615 \uc790\ub3d9 \uc0dd\uc131\n\n"
        u"\u2501\u2501 \ud559\uc2b5 \u2501\u2501\n"
        u"  \ud559\uc2b5 \ud1b5\uacc4     \ub370\uc774\ud130 \ud604\ud669\n"
        u"  \ud559\uc2b5 \uc2dc\uc791     Fine-tuning \uc2e4\ud589\n"
        u"  \ud559\uc2b5 \uc911\uc9c0     \ud559\uc2b5 \uc911\ub2e8\n"
        u"  \ud559\uc2b5 \uc774\ub825     \uc2e4\ud589 \uc774\ub825\n"
        u"  \ud559\uc2b5 \uc9c4\ud589\ub960   \uacb0\uacfc \ud655\uc778\n\n"
        u"\u2501\u2501 \ud14c\uc2a4\ud2b8 \u2501\u2501\n"
        u"  \ud14c\uc2a4\ud2b8 <\ud14d\uc2a4\ud2b8>     AI \uc720\ud574\uc131 \ud310\uc815\n\n"
        u"\u2501\u2501 \ubaa8\ub378 \u2501\u2501\n"
        u"  \ubaa8\ub378 \ubaa9\ub85d     \uc124\uce58\ub41c \ubaa8\ub378\n"
        u"  \ubaa8\ub378 \uc2e4\ud589     \ud604\uc7ac \ub85c\ub4dc\ub41c \ubaa8\ub378\n"
        u"  \ubaa8\ub378 \ubcc0\uacbd <\ubaa8\ub378\uba85>  \uae30\ubcf8 \ubaa8\ub378 \uc804\ud658\n\n"
        u"\u2501\u2501 GPU \u2501\u2501\n"
        u"  GPU \uc0c1\ud0dc      \uc0ac\uc6a9\ub960/\uc628\ub3c4/VRAM\n"
        u"  GPU \uac15\uc81c\ud559\uc2b5  \uc11c\ube44\uc2a4 \ubb34\uc2dc \uc989\uc2dc \ud559\uc2b5\n\n"
        u"\u2501\u2501 \uc11c\ube44\uc2a4 \u2501\u2501\n"
        u"  \ud050 \uc0c1\ud0dc / \uc77c\uc2dc\uc815\uc9c0 / \uc7ac\uac1c\n"
        u"  \ucc44\ud305 <\ud14d\uc2a4\ud2b8>     \ubaa8\ub378\uacfc \ub300\ud654\n"
        u"  \uc11c\ube44\uc2a4 \uc0c1\ud0dc / \uc7ac\uc2dc\uc791\n\n"
        u"\u2501\u2501 \uc218\uc9d1/\uc815\uc81c \u2501\u2501\n"
        u"  \uc218\uc9d1 \ud604\ud669     \uc218\uc9d1/\uc815\uc81c/\ubcf4\uc815 \uac74\uc218\n"
        u"  \uc815\uc81c \uc2e4\ud589     \ub85c\uc6b0 \ub370\uc774\ud130 \uc815\uc81c\n"
        u"  \ub370\uc774\ud130 \uac74\uac15\ub3c4   \ud488\uc9c8 \uc810\uc218\n\n"
        u"\u2501\u2501 \uc790\uc5f0\uc5b4 \u2501\u2501\n"
        u"  \uc790\uc720\ub86d\uac8c \ud55c\uad6d\uc5b4\ub85c \uc9c8\ubb38\ud558\uba74 AI\uac00 \ud574\uc11d\ud558\uc5ec \uc2e4\ud589\ud569\ub2c8\ub2e4.\n"
        u"  \uc608) \'\uc695\uc124 \uac80\uc0ac\ud574\uc918 \u314e\u3142\', \'GPU \ub6f0\uace0 \uc788\uc5b4?\'"
    )

    if m in (u"\ub3c4\uc6c0\ub9d0", "help", "?"):
        return HELP

    if m == u"\uc0c1\ud0dc":
        idle = _is_gpu_idle()
        hrs = _is_allowed_hours()
        tr = u"\uc2e4\ud589 \uc911" if _training_status["running"] else u"\ub300\uae30"
        conn = _get_main_db()
        try:
            pend = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            run_j = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
        finally:
            conn.close()
        return (
            u"\U0001f4ca Content Safety \ud604\ud669\n\n"
            + u"\ud559\uc2b5: " + tr + u" (Run #" + str(_training_status.get("run_id") or "-") + u")\n"
            + u"GPU: " + (u"\uc720\ud734" if idle else u"\uc0ac\uc6a9 \uc911") + u" | \ud5c8\uc6a9\uc2dc\uac04: " + (u"OK" if hrs else u"\uc2dc\uac04\uc678") + u"\n"
            + u"\ud050: \ub300\uae30 " + str(pend) + u" / \uc2e4\ud589 " + str(run_j)
        )

    if m == u"\ud328\ud134":
        sub = args[0] if args else ""
        if sub == u"\ud14c\uc2a4\ud2b8":
            text = " ".join(args[1:])
            if not text:
                return u"\uc0ac\uc6a9\ubc95: \ud328\ud134 \ud14c\uc2a4\ud2b8 <\ud14d\uc2a4\ud2b8>"
            try:
                r = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/generate",
                    "-d", json.dumps({"model": "qwen2.5:1.5b", "prompt": u'\ucf58\ud150\uce20 \ubaa8\ub354\ub808\uc774\ud130. \uc720\ud574\uc131 JSON \ud310\uc815: "' + text + u'"\n{"flagged":true/false,"action":"allow/warn/block","reason":"\ud55c\uc904\uc0ac\uc720"}', "stream": False})],
                    capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    resp = json.loads(r.stdout)
                    return u"\U0001f9ea \ud310\uc815 \uacb0\uacfc:\n" + resp.get("response", u"\uc751\ub2f5 \uc5c6\uc74c")
            except Exception as e:
                return u"\ud310\uc815 \uc2e4\ud328: " + str(e)
            return u"Ollama \uc5f0\uacb0 \uc2e4\ud328"

        if sub == u"\uc81c\uc548":
            word = args[1] if len(args) > 1 else ""
            if not word:
                return u"\uc0ac\uc6a9\ubc95: \ud328\ud134 \uc81c\uc548 <\ub2e8\uc5b4>"
            chars = list(word)
            vs = []
            for i in range(1, len(chars)):
                vs.append("".join(chars[:i]) + " " + "".join(chars[i:]))
                vs.append("".join(chars[:i]) + "1" + "".join(chars[i:]))
                vs.append("".join(chars[:i]) + "." + "".join(chars[i:]))
            rx = "\\s*".join(chars)
            return u"\u2728 '" + word + u"' \ubcc0\ud615 " + str(len(vs)) + u"\uac1c:\n" + ", ".join(vs) + u"\n\n\uc81c\uc548 \uc815\uaddc\uc2dd: /" + rx + "/gi"

        return HELP

    if m == u"\ud559\uc2b5":
        sub = args[0] if args else u"\ud1b5\uacc4"
        if sub == u"\ud1b5\uacc4":
            conn = _get_training_db()
            try:
                runs = conn.execute("SELECT COUNT(*) FROM training_runs").fetchone()[0]
                dep = conn.execute("SELECT id, f1_score FROM training_runs WHERE status='deployed'").fetchone()
            finally:
                conn.close()
            d = u"\ubc30\ud3ec \uc911: Run #" + str(dep[0]) + u" (F1: " + str(dep[1]) + u")" if dep else u"\ubc30\ud3ec\ub41c \ubaa8\ub378 \uc5c6\uc74c"
            return u"\U0001f4ca \ud559\uc2b5 \ud1b5\uacc4\n\uc2e4\ud589 \uc774\ub825: " + str(runs) + u"\uac74\n" + d

        if sub == u"\uc2dc\uc791":
            if _training_status["running"]:
                return u"\uc774\ubbf8 \ud559\uc2b5 \uc2e4\ud589 \uc911"
            conn = _get_training_db()
            try:
                conn.execute("INSERT INTO training_runs (base_model,status,trigger_type,started_at) VALUES ('qwen2.5:1.5b','running','web-cli',datetime('now'))")
                conn.commit()
                rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            finally:
                conn.close()
            _training_status["running"] = True
            _training_status["run_id"] = rid
            return u"\U0001f680 \ud559\uc2b5 \uc2dc\uc791 (Run #" + str(rid) + u")"

        if sub == u"\uc911\uc9c0":
            rid = _training_status.get("run_id")
            _training_status["running"] = False
            _training_status["paused"] = False
            _training_status["run_id"] = None
            if rid:
                conn = _get_training_db()
                try:
                    conn.execute("UPDATE training_runs SET status='failed',error_message='CLI stop',completed_at=datetime('now') WHERE id=?", (rid,))
                    conn.commit()
                finally:
                    conn.close()
            return u"\u23f9 \ud559\uc2b5 \uc911\uc9c0 (Run #" + str(rid or "-") + u")"

        if sub == u"\uc9c4\ud589\ub960" or sub == u"\uc9c4\ud589":
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", "outputs")
            parts = []
            tr = os.path.join(output_dir, "train_result.json")
            if os.path.exists(tr):
                with open(tr) as f:
                    d = json.load(f)
                parts.append(u"\ud559\uc2b5: " + d.get("status","?") + u" loss=" + str(round(d.get("loss",0),4)) + u" " + str(round(d.get("runtime_sec",0)))+ u"\ucd08")
            ev = os.path.join(output_dir, "eval_result.json")
            if os.path.exists(ev):
                with open(ev) as f:
                    d = json.load(f)
                parts.append(u"\ud3c9\uac00: F1=" + str(d.get("f1_score",0)) + u" P=" + str(d.get("precision",0)) + u" R=" + str(d.get("recall",0)) + u" " + (u"\u2705 PASS" if d.get("passed") else u"\u274c FAIL"))
            dep = os.path.join(output_dir, "deploy_result.json")
            if os.path.exists(dep):
                with open(dep) as f:
                    d = json.load(f)
                parts.append(u"\ubc30\ud3ec: " + d.get("model_name","?"))
            if not parts:
                return u"\ud559\uc2b5 \uacb0\uacfc \uc5c6\uc74c"
            return u"\U0001f4ca \ud559\uc2b5 \uc9c4\ud589\ub960\n\n" + u"\n".join(parts)

        if sub == u"\uc774\ub825":
            conn = _get_training_db()
            try:
                rows = conn.execute("SELECT id,status,base_model,f1_score,sample_count,created_at FROM training_runs ORDER BY id DESC LIMIT 10").fetchall()
            finally:
                conn.close()
            if not rows:
                return u"\ud559\uc2b5 \uc774\ub825 \uc5c6\uc74c"
            lines = [u"\U0001f4cb \ud559\uc2b5 \uc774\ub825\n"]
            for r in rows:
                lines.append(u"#" + str(r[0]) + u" | " + str(r[1]) + u" | " + str(r[2]) + u" | F1:" + str(r[3] or "-") + u" | " + str(r[4]) + u"\uac74 | " + str(r[5] or ""))
            return "\n".join(lines)

        return HELP

    if m == u"\ud14c\uc2a4\ud2b8":
        text = " ".join(args)
        if not text:
            return u"\uc0ac\uc6a9\ubc95: \ud14c\uc2a4\ud2b8 <\ud14d\uc2a4\ud2b8>"
        try:
            r = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/generate",
                "-d", json.dumps({"model": "qwen2.5:1.5b", "prompt": u'\ucf58\ud150\uce20 \ubaa8\ub354\ub808\uc774\ud130. \uc720\ud574\uc131 JSON: "' + text + u'"\n{"flagged":bool,"action":"allow|warn|block","categories":[],"confidence":0-1,"explanation":"\uc0ac\uc720"}', "stream": False})],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                resp = json.loads(r.stdout)
                return u"\U0001f9ea \ud30c\uc774\ud504\ub77c\uc778 \ud14c\uc2a4\ud2b8\n\uc785\ub825: \"" + text + u"\"\n\n\uacb0\uacfc:\n" + resp.get("response", u"\uc751\ub2f5 \uc5c6\uc74c")
        except Exception as e:
            return u"\ud14c\uc2a4\ud2b8 \uc2e4\ud328: " + str(e)
        return u"Ollama \uc5f0\uacb0 \uc2e4\ud328"

    if m == "GPU":
        sub = args[0] if args else u"\uc0c1\ud0dc"
        if sub == u"\uc0c1\ud0dc":
            try:
                r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    pts = [x.strip() for x in r.stdout.strip().split(",")]
                    return u"\U0001f5a5 GPU \uc0c1\ud0dc\nGPU: " + pts[0] + u"% | VRAM: " + pts[1] + u"MB / " + pts[2] + u"MB | \uc628\ub3c4: " + pts[3] + u"\u00b0C\n\uc720\ud734: " + (u"\uc608" if _is_gpu_idle() else u"\uc544\ub2c8\uc624") + u" | \ud5c8\uc6a9\uc2dc\uac04: " + (u"OK" if _is_allowed_hours() else u"\uc2dc\uac04\uc678")
            except Exception as e:
                logger.debug("GPU status CLI failed: %s", e)
            return u"GPU \uc0c1\ud0dc \uc870\ud68c \uc2e4\ud328"

        if sub == u"\uac15\uc81c\ud559\uc2b5":
            conn = _get_training_db()
            try:
                conn.execute("INSERT INTO training_runs (base_model,status,priority,trigger_type,started_at) VALUES ('qwen2.5:1.5b','running','P1','web-force',datetime('now'))")
                conn.commit()
                rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            finally:
                conn.close()
            _training_status["running"] = True
            _training_status["run_id"] = rid
            return u"\u26a1 \uac15\uc81c \ud559\uc2b5 P1 (Run #" + str(rid) + u")"

        return HELP

    # ── 모델 ──
    if m == u"\ubaa8\ub378":
        sub = args[0] if args else u"\ubaa9\ub85d"
        if sub == u"\ubaa9\ub85d":
            try:
                r = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/tags"], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    data = json.loads(r.stdout)
                    models = data.get("models", [])
                    if not models:
                        return u"\uc124\uce58\ub41c \ubaa8\ub378 \uc5c6\uc74c"
                    lines = [u"\U0001f4e6 \uc124\uce58\ub41c \ubaa8\ub378 (" + str(len(models)) + u"\uac1c)\n"]
                    for md in models:
                        sz = round(md.get("size", 0) / 1024 / 1024 / 1024, 1)
                        lines.append(u"  " + md.get("name", "?") + u"  (" + str(sz) + u"GB)")
                    return "\n".join(lines)
            except Exception as e:
                return u"\ubaa8\ub378 \ubaa9\ub85d \uc870\ud68c \uc2e4\ud328: " + str(e)
            return u"Ollama \uc5f0\uacb0 \uc2e4\ud328"

        if sub == u"\uc2e4\ud589":
            try:
                r = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/ps"], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    data = json.loads(r.stdout)
                    models = data.get("models", [])
                    if not models:
                        return u"\ub85c\ub4dc\ub41c \ubaa8\ub378 \uc5c6\uc74c"
                    lines = [u"\U0001f680 \uc2e4\ud589 \uc911 \ubaa8\ub378\n"]
                    for md in models:
                        sz = round(md.get("size", 0) / 1024 / 1024 / 1024, 1)
                        vram = round(md.get("size_vram", 0) / 1024 / 1024 / 1024, 1)
                        lines.append(u"  " + md.get("name", "?") + u"  (" + str(sz) + u"GB, VRAM: " + str(vram) + u"GB)")
                    return "\n".join(lines)
            except Exception as e:
                return u"\uc2e4\ud589 \ubaa8\ub378 \uc870\ud68c \uc2e4\ud328: " + str(e)

        if sub == u"\ubcc0\uacbd":
            model = args[1] if len(args) > 1 else ""
            if not model:
                return u"\uc0ac\uc6a9\ubc95: \ubaa8\ub378 \ubcc0\uacbd <\ubaa8\ub378\uba85>\n\uc608: \ubaa8\ub378 \ubcc0\uacbd qwen2.5:7b"
            _set_training_config("default_model", model)
            return u"\u2705 \uae30\ubcf8 \ubaa8\ub378 \ubcc0\uacbd: " + model

        return HELP

    # ── 큐 ──
    if m == u"\ud050":
        sub = args[0] if args else u"\uc0c1\ud0dc"
        if sub == u"\uc0c1\ud0dc":
            conn = _get_main_db()
            try:
                pend = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
                run_j = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
                comp = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='completed'").fetchone()[0]
                fail = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0]
            finally:
                conn.close()
            paused = u"\uc77c\uc2dc\uc815\uc9c0" if _queue_paused else u"\uc815\uc0c1"
            return u"\U0001f4e8 \ud050 \uc0c1\ud0dc\n\n\uc0c1\ud0dc: " + paused + u"\n\ub300\uae30: " + str(pend) + u" | \uc2e4\ud589: " + str(run_j) + u" | \uc644\ub8cc: " + str(comp) + u" | \uc2e4\ud328: " + str(fail)

        if sub == u"\uc77c\uc2dc\uc815\uc9c0":
            _queue_paused = True
            return u"\u23f8 \ud050 \uc77c\uc2dc\uc815\uc9c0"

        if sub == u"\uc7ac\uac1c":
            _queue_paused = False
            return u"\u25b6 \ud050 \uc7ac\uac1c"

        return HELP

    # ── 채팅 ──
    if m == u"\ucc44\ud305":
        text = " ".join(args)
        if not text:
            return u"\uc0ac\uc6a9\ubc95: \ucc44\ud305 <\ud14d\uc2a4\ud2b8>"
        model = _training_config("default_model", "qwen2.5:1.5b")
        try:
            r = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/generate",
                "-d", json.dumps({"model": model, "prompt": text, "stream": False})],
                capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                resp = json.loads(r.stdout)
                return u"\U0001f4ac " + model + u":\n" + resp.get("response", u"\uc751\ub2f5 \uc5c6\uc74c")
        except Exception as e:
            return u"\ucc44\ud305 \uc2e4\ud328: " + str(e)
        return u"Ollama \uc5f0\uacb0 \uc2e4\ud328"

    # ── 서비스 ──
    if m == u"\uc11c\ube44\uc2a4":
        sub = args[0] if args else u"\uc0c1\ud0dc"
        if sub == u"\uc0c1\ud0dc":
            ok = get_ollama_status()
            return u"\U0001f527 Ollama \uc5d4\uc9c4: " + (u"\u2705 \uc2e4\ud589 \uc911" if ok else u"\u274c \uc911\uc9c0")

        if sub == u"\uc7ac\uc2dc\uc791":
            try:
                subprocess.run(["systemctl", "restart", "ollama"], capture_output=True, timeout=10)
                return u"\U0001f504 Ollama \uc7ac\uc2dc\uc791 \uc694\uccad\ub428"
            except Exception as e:
                return u"\uc7ac\uc2dc\uc791 \uc2e4\ud328: " + str(e)

        return HELP


    # 수집/정제
    if m == "수집":
        conn = _get_mod_db()
        try:
            raw = conn.execute("SELECT COUNT(*) FROM raw_collections").fetchone()[0]
            pend = conn.execute("SELECT COUNT(*) FROM raw_collections WHERE refine_status='pending'").fetchone()[0]
            corr = conn.execute("SELECT COUNT(*) FROM raw_collections WHERE is_correction=1").fetchone()[0]
            ref = conn.execute("SELECT COUNT(*) FROM refined_samples").fetchone()[0]
        finally:
            conn.close()
        return "📦 수집 현황\n수집: " + str(raw) + "건 | 정제: " + str(ref) + "건 | 대기: " + str(pend) + "건 | 보정: " + str(corr) + "건"

    if m == "정제":
        sub = args[0] if args else "실행"
        if sub == "실행":
            conn = _get_mod_db()
            try:
                rows = conn.execute("SELECT * FROM raw_collections WHERE refine_status='pending'").fetchall()
                seen = set(r["text_hash"] for r in conn.execute("SELECT text_hash FROM refined_samples"))
                count = 0
                for row in rows:
                    if len(row["text"]) < 5 or row["text_hash"] in seen:
                        conn.execute("UPDATE raw_collections SET refine_status='excluded' WHERE id=?", (row["id"],))
                        continue
                    label = row["admin_action"] or row["ai_action"] or "allow"
                    conn.execute("INSERT OR IGNORE INTO refined_samples (text, text_hash, label, categories, source) VALUES (?, ?, ?, ?, ?)",
                        (row["text"], row["text_hash"], label, row["ai_categories"] or "[]", row["source"]))
                    conn.execute("UPDATE raw_collections SET refine_status='included' WHERE id=?", (row["id"],))
                    seen.add(row["text_hash"])
                    count += 1
                conn.commit()
            finally:
                conn.close()
            return "🔧 정제 완료: " + str(count) + "건"
        return HELP

    return _llm_route(cmd)

# ── 대화 히스토리 (세션 내 최근 10턴) ──
_chat_history = []
_MAX_HISTORY = 10

# ── 분석 함수 ──
def _run_analysis(target, metric=""):
    """사전 정의 분석 쿼리 실행"""
    conn = _get_main_db()
    try:
        if target == "today_stats":
            r = conn.execute("""SELECT COUNT(*) as total,
                SUM(CASE WHEN action='BLOCKED' THEN 1 ELSE 0 END) as blocked,
                SUM(CASE WHEN action='WARNED' THEN 1 ELSE 0 END) as warned
                FROM moderation_logs WHERE date(created_at)=date('now')""").fetchone()
            return {"total": r[0], "blocked": r[1], "warned": r[2]}
        elif target == "queue_stats":
            r = conn.execute("SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status").fetchall()
            return {row[0]: row[1] for row in r}
        elif target == "training_stats":
            tc = _get_training_db()
            try:
                runs = tc.execute("SELECT COUNT(*) FROM training_runs").fetchone()[0]
                deployed = tc.execute("SELECT id, f1_score, base_model FROM training_runs WHERE status='deployed'").fetchone()
            finally:
                tc.close()
            return {"runs": runs, "deployed": {"id": deployed[0], "f1": deployed[1], "model": deployed[2]} if deployed else None}
        elif target == "model_info":
            try:
                r = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/tags"], capture_output=True, text=True, timeout=10)
                data = json.loads(r.stdout)
                models = [{"name": m["name"], "size_gb": round(m.get("size", 0)/1024**3, 1)} for m in data.get("models", [])]
                r2 = subprocess.run(["curl", "-s", "http://127.0.0.1:11434/api/ps"], capture_output=True, text=True, timeout=10)
                running = [m["name"] for m in json.loads(r2.stdout).get("models", [])]
                return {"installed": models, "running": running}
            except Exception:
                return {"error": "ollama unavailable"}
        elif target == "gpu_info":
            try:
                r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                return {"gpu_pct": int(parts[0]), "vram_used": int(parts[1]), "vram_total": int(parts[2]), "temp": int(parts[3]), "idle": _is_gpu_idle(), "allowed_hours": _is_allowed_hours()}
            except Exception:
                return {"error": "nvidia-smi unavailable"}
        elif target == "system_status":
            idle = _is_gpu_idle()
            hrs = _is_allowed_hours()
            pend = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            run_j = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
            return {"training": _training_status.copy(), "gpu_idle": idle, "allowed_hours": hrs, "queue_pending": pend, "queue_running": run_j}
        elif target == "training_history":
            tc = _get_training_db()
            try:
                rows = tc.execute("SELECT id, status, base_model, f1_score, sample_count, created_at FROM training_runs ORDER BY id DESC LIMIT 10").fetchall()
            finally:
                tc.close()
            return [{"id": r[0], "status": r[1], "model": r[2], "f1": r[3], "samples": r[4], "date": r[5]} for r in rows]
        else:
            return {"error": "unknown target: " + target}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _llm_route(user_input):
    """2\ub2e8\uacc4 \uc758\ub3c4 \ubd84\uae30: LLM\uc774 \uc790\uc5f0\uc5b4\ub97c \ud574\uc11d\ud558\uc5ec COMMAND/ANALYSIS/EXPLAIN/CHAT \ubd84\ub958"""
    global _chat_history
    model = _training_config("cli_model", "qwen2.5:14b")

    SYSP = """Content Safety \uad00\ub9ac \uc5b4\uc2dc\uc2a4\ud134\ud2b8. \uc0ac\uc6a9\uc790 \ud55c\uad6d\uc5b4 \uc785\ub825\uc744 \ubd84\uc11d\ud558\uc5ec \uc751\ub2f5.

\uc751\ub2f5 \ud615\uc2dd (\ubc18\ub4dc\uc2dc JSON):
{"intent":"<COMMAND|ANALYSIS|EXPLAIN|CHAT>", ...}

1) COMMAND \u2014 \uc2dc\uc2a4\ud15c \uba85\ub839 \uc2e4\ud589
{"intent":"COMMAND","action":"<\uba85\ub839>","args":"<\uc778\uc790>"}
action: \uc0c1\ud0dc, \ud328\ud134 \ud14c\uc2a4\ud2b8, \ud328\ud134 \uc81c\uc548, \ud559\uc2b5 \ud1b5\uacc4, \ud559\uc2b5 \uc2dc\uc791, \ud559\uc2b5 \uc911\uc9c0, \ud559\uc2b5 \uc774\ub825, \ud14c\uc2a4\ud2b8, GPU \uc0c1\ud0dc, GPU \uac15\uc81c\ud559\uc2b5, \ubaa8\ub378 \ubaa9\ub85d, \ubaa8\ub378 \uc2e4\ud589, \ubaa8\ub378 \ubcc0\uacbd, \ud050 \uc0c1\ud0dc, \ud050 \uc77c\uc2dc\uc815\uc9c0, \ud050 \uc7ac\uac1c, \ucc44\ud305, \uc11c\ube44\uc2a4 \uc0c1\ud0dc, \uc11c\ube44\uc2a4 \uc7ac\uc2dc\uc791

2) ANALYSIS \u2014 \ub370\uc774\ud130 \ubd84\uc11d/\ubcf4\uace0
{"intent":"ANALYSIS","target":"<today_stats|queue_stats|training_stats|model_info|gpu_info|system_status|training_history>","question":"<\uc6d0\ub798 \uc9c8\ubb38>"}

3) EXPLAIN \u2014 \uc2dc\uc2a4\ud15c/\uc791\uc5c5 \uc124\uba85
{"intent":"EXPLAIN","answer":"<\uc124\uba85 \ub0b4\uc6a9>"}

4) CHAT \u2014 \uc77c\ubc18 \ub300\ud654
{"intent":"CHAT","answer":"<\ub2f5\ubcc0>"}

=== \uc2dc\uc2a4\ud15c \uc815\ubcf4 ===
Content Safety Learning System 7 Phase \uad6c\ud604 \uc644\ub8cc.
Phase 1: DB + \ud328\ud134 \uc5d4\uc9c4, Phase 2: 3\ub2e8\uacc4 \ud30c\uc774\ud504\ub77c\uc778, Phase 3: \ud559\uc2b5 \ub370\uc774\ud130 \uad00\ub9ac,
Phase 4: GPU Training API, Phase 5: \ub300\uc2dc\ubcf4\ub4dc + \ub9ac\ubdf0, Phase 6: CLI + AI \uc5b4\uc2dc\uc2a4\ud134\ud2b8, Phase 7: AI \uc5d0\uc774\uc804\ud2b8 6\uac1c.
\ud30c\uc774\ud504\ub77c\uc778: Stage1(\ud328\ud134) > Stage2(Ollama) > Stage3(Claude).
GPU \uc6b0\uc120\uc21c\uc704: \uc11c\ube44\uc2a4(P0) > \ubc30\uce58(P1) > \ud559\uc2b5(P2), \ud5c8\uc6a9\uc2dc\uac04 00-06\uc2dc.
6 Agent: Auto Trainer, Patrol, Pattern Evolve, Anomaly Detector, Adversarial Generator, Report.
\uc774 \uc2dc\uc2a4\ud15c\uc740 Ollama \uae30\ubc18 \ub85c\uceec LLM\uc73c\ub85c \uc720\ud574 \ucf58\ud150\uce20\ub97c \uac10\uc9c0\ud558\uace0, \uad00\ub9ac\uc790 \ud53c\ub4dc\ubc31\uc73c\ub85c \uc9c0\uc18d \uac1c\uc120\ub418\ub294 \ud559\uc2b5\ud615 \uc2dc\uc2a4\ud15c.
\ubc18\ub4dc\uc2dc JSON\uc73c\ub85c\ub9cc \uc751\ub2f5\ud558\uc138\uc694."""

    # \ub300\ud654 \ud788\uc2a4\ud1a0\ub9ac \uad6c\uc131
    messages = [{"role": "system", "content": SYSP}]
    for h in _chat_history[-_MAX_HISTORY:]:
        messages.append(h)
    messages.append({"role": "user", "content": user_input})

    try:
        r = subprocess.run(
            ["curl", "-s", "http://127.0.0.1:11434/api/chat",
             "-d", json.dumps({"model": model, "messages": messages, "stream": False})],
            capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            return "\U0001f916 AI \uc5f0\uacb0 \uc2e4\ud328"

        resp = json.loads(r.stdout)
        content = (resp.get("message", {}).get("content", "") or resp.get("response", "")).strip()
        if not content:
            return "\U0001f916 \uc751\ub2f5 \uc5c6\uc74c"

        # JSON \ucd94\ucd9c
        parsed = None
        if "{" in content:
            start = content.index("{")
            depth = 0
            for i in range(start, len(content)):
                if content[i] == "{": depth += 1
                elif content[i] == "}": depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(content[start:i+1])
                    except json.JSONDecodeError:
                        pass
                    break

        if not parsed:
            # JSON \ud30c\uc2f1 \uc2e4\ud328 \u2014 \uc9c1\uc811 \uc751\ub2f5
            _save_history(user_input, content)
            return "\U0001f916 " + content

        intent = parsed.get("intent", "CHAT")

        # \u2500\u2500 COMMAND \u2500\u2500
        if intent == "COMMAND":
            action = parsed.get("action", "")
            act_args = str(parsed.get("args", ""))
            if action:
                cmd_str = action + (" " + act_args if act_args else "")
                result = _handle_cli(cmd_str)
                _save_history(user_input, result)
                return "\U0001f916 AI \ud574\uc11d: " + cmd_str + "\n\n" + result

        # \u2500\u2500 ANALYSIS \u2500\u2500
        if intent == "ANALYSIS":
            target = parsed.get("target", "system_status")
            question = parsed.get("question", user_input)
            # 1\ucc28: \ub370\uc774\ud130 \uc218\uc9d1
            data = _run_analysis(target)
            # 2\ucc28: LLM\uc774 \uacb0\uacfc \ud574\uc11d
            interpret_prompt = (
                "\ub2e4\uc74c \ub370\uc774\ud130\ub97c \ubd84\uc11d\ud558\uc5ec \ud55c\uad6d\uc5b4\ub85c \ubcf4\uace0\ud558\uc138\uc694.\n"
                "\uc9c8\ubb38: " + question + "\n"
                "\ub370\uc774\ud130: " + json.dumps(data, ensure_ascii=False, default=str) + "\n\n"
                "\uac04\uacb0\ud558\uac8c \ud575\uc2ec\ub9cc \ub2f5\ubcc0. \uc774\ubaa8\uc9c0 \uc0ac\uc6a9 \uac00\ub2a5. JSON \uc544\ub2cc \uc77c\ubc18 \ud14d\uc2a4\ud2b8\ub85c \uc751\ub2f5."
            )
            try:
                r2 = subprocess.run(
                    ["curl", "-s", "http://127.0.0.1:11434/api/generate",
                     "-d", json.dumps({"model": model, "prompt": interpret_prompt, "stream": False})],
                    capture_output=True, text=True, timeout=60)
                r2_resp = json.loads(r2.stdout)
                interpretation = r2_resp.get("response", "\ubd84\uc11d \uc2e4\ud328")
            except Exception:
                interpretation = "\ub370\uc774\ud130:\n" + json.dumps(data, ensure_ascii=False, indent=2, default=str)

            result = "\U0001f4ca " + interpretation
            _save_history(user_input, result)
            return result

        # \u2500\u2500 EXPLAIN \u2500\u2500
        if intent == "EXPLAIN":
            answer = parsed.get("answer", content)
            _save_history(user_input, answer)
            return "\U0001f4a1 " + answer

        # \u2500\u2500 CHAT \u2500\u2500
        answer = parsed.get("answer", content)
        _save_history(user_input, answer)
        return "\U0001f916 " + answer

    except Exception as e:
        return "\U0001f916 AI \uc624\ub958: " + str(e)


def _save_history(user_msg, assistant_msg):
    global _chat_history
    _chat_history.append({"role": "user", "content": user_msg})
    _chat_history.append({"role": "assistant", "content": assistant_msg})
    if len(_chat_history) > _MAX_HISTORY * 2:
        _chat_history = _chat_history[-_MAX_HISTORY * 2:]




# ═══════════════════════════════════════════════════════════════════════════════
# Moderation API — 포털 연동 (판정 + 수집 + 정제 + 통계)
# ═══════════════════════════════════════════════════════════════════════════════

import hashlib as _mod_hashlib
import sqlite3 as _mod_sqlite3

MOD_DB_PATH = os.path.join(os.path.dirname(DB_PATH), "moderation.db")

def _get_mod_db():
    conn = _mod_sqlite3.connect(MOD_DB_PATH, timeout=10)
    conn.row_factory = _mod_sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS raw_collections (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
        text TEXT NOT NULL,
        text_hash TEXT NOT NULL,
        source TEXT NOT NULL,
        source_id TEXT,
        cafe_id TEXT,
        author_id TEXT,
        ai_action TEXT NOT NULL,
        ai_stage TEXT,
        ai_confidence REAL,
        ai_categories TEXT DEFAULT '[]',
        admin_action TEXT,
        admin_reason TEXT,
        is_correction INTEGER DEFAULT 0,
        correction_type TEXT,
        refine_status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS refined_samples (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
        text TEXT NOT NULL,
        text_hash TEXT NOT NULL,
        label TEXT NOT NULL,
        categories TEXT DEFAULT '[]',
        confidence REAL DEFAULT 0.9,
        source TEXT,
        is_augmented INTEGER DEFAULT 0,
        refine_batch TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_refine ON raw_collections(refine_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_correction ON raw_collections(is_correction) WHERE is_correction = 1")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rs_hash ON refined_samples(text_hash)")
    return conn



# ═══ 모더레이션 응답 캐시 (인메모리 LRU) ═══
from collections import OrderedDict as _OD
import time as _cache_time

class _ModCache:
    def __init__(self, max_size=5000, ttl=300):
        self._cache = _OD()
        self._max = max_size
        self._ttl = ttl

    def get(self, key):
        if key in self._cache:
            val, ts = self._cache[key]
            if _cache_time.time() - ts < self._ttl:
                self._cache.move_to_end(key)
                return val
            del self._cache[key]
        return None

    def set(self, key, val):
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = (val, _cache_time.time())
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)

    @property
    def size(self):
        return len(self._cache)

_mod_cache = _ModCache(max_size=5000, ttl=300)


@training_bp.route("/api/system/info")
def api_system_info():
    """System environment info + recommended settings
    ---
    tags: [Config]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    return jsonify(_SYSTEM_INFO)

@training_bp.route("/api/system/apply-recommended", methods=["POST"])
def api_apply_recommended():
    """Apply recommended settings based on detected environment
    ---
    tags: [Config]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    rec = _SYSTEM_INFO.get("recommended", {})
    applied = []

    # Training config 업데이트
    if rec.get("model"):
        _set_training_config("default_model", rec["model"])
        _set_training_config("cli_model", rec["model"])
        applied.append(f"default_model → {rec['model']}")

    if rec.get("training_model"):
        _set_training_config("training_model", rec["training_model"])
        applied.append(f"training_model → {rec['training_model']}")

    return jsonify({"ok": True, "applied": applied, "recommended": rec})

@training_bp.route("/api/mod/judge", methods=["POST"])
def mod_judge():
    """콘텐츠 실시간 판정 (포털 호출)
    ---
    tags: [Moderation]
    security: [{Bearer: []}]
    parameters:
      - {name: body, in: body, schema: {type: object, properties: {
          text: {type: string}, source: {type: string},
          source_id: {type: string}, cafe_id: {type: string},
          author_id: {type: string}
        }}}
    responses:
      200:
        schema: {type: object}
    """
    body = request.json or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"action": "allow", "stage": "none", "latency_ms": 0})

    import time as _t
    start = _t.time()

    # 캐시 체크
    cache_key = _mod_hashlib.md5(text.encode()).hexdigest()
    cached = _mod_cache.get(cache_key)
    if cached:
        cached["latency_ms"] = 0
        cached["cached"] = True
        return jsonify(cached)

    # Stage 1: 패턴 매칭 (간단한 정규식)
    action = "allow"
    stage = "none"
    confidence = 1.0
    categories = []

    # 간단한 패턴 체크
    import re
    profanity_patterns = [
        (r"시\s*발|씨\s*발|씨\s*팔|ㅅ\s*ㅂ", "profanity"),
        (r"개\s*새\s*끼|ㄱ\s*ㅅ\s*ㄲ|개\s*새", "profanity"),
        (r"병\s*신|ㅂ\s*ㅅ", "profanity"),
        (r"지\s*랄|ㅈ\s*ㄹ", "profanity"),
        (r"존\s*나|졸\s*라|ㅈ\s*ㄴ", "profanity"),
        (r"죽여|죽일|찔러", "harassment"),
    ]
    for pattern, cat in profanity_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            action = "block"
            stage = "S1"
            categories.append(cat)
            break

    # Stage 2: Ollama 판정 (S1에서 미감지 시)
    if action == "allow" and len(text) > 3:
        try:
            r = subprocess.run(
                ["curl", "-s", "http://127.0.0.1:11434/api/generate",
                 "-d", json.dumps({"model": _training_config("default_model", "qwen2.5:1.5b"),
                    "prompt": f'콘텐츠 모더레이터. 유해성 판정. "{text[:300]}"\n응답: allow 또는 warn 또는 block (한 단어만)',
                    "stream": False})],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                resp = json.loads(r.stdout).get("response", "").strip().lower()
                if "block" in resp:
                    action = "block"; stage = "S2"; confidence = 0.8
                elif "warn" in resp:
                    action = "warn"; stage = "S2"; confidence = 0.7
                else:
                    stage = "S2"; confidence = 0.9
        except Exception:
            pass  # Ollama 실패 시 allow 유지

    latency = int((_t.time() - start) * 1000)

    # 수집 저장
    text_hash = _mod_hashlib.sha256(text.encode()).hexdigest()
    col_id = None
    try:
        conn = _get_mod_db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO raw_collections (text, text_hash, source, source_id, cafe_id, author_id, ai_action, ai_stage, ai_confidence, ai_categories) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (text[:2000], text_hash, body.get("source", "unknown"), body.get("source_id"),
                 body.get("cafe_id"), body.get("author_id"), action, stage, confidence, json.dumps(categories)))
            conn.commit()
            row = conn.execute("SELECT id FROM raw_collections WHERE text_hash=? ORDER BY created_at DESC LIMIT 1", (text_hash,)).fetchone()
            col_id = row["id"] if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.debug("moderation collection lookup failed: %s", e)

    result = {
        "action": action,
        "severity": "high" if action == "block" else ("medium" if action == "warn" else "none"),
        "confidence": confidence,
        "categories": list(set(categories)),
        "stage": stage,
        "latency_ms": latency,
        "collection_id": col_id,
        "cached": False,
    }
    _mod_cache.set(cache_key, result.copy())
    return jsonify(result)


@training_bp.route("/api/mod/collect", methods=["POST"])
def mod_collect():
    """관리자 보정 수집
    ---
    tags: [Moderation]
    security: [{Bearer: []}]
    parameters:
      - {name: body, in: body, schema: {type: object}}
    responses:
      200:
        schema: {type: object}
    """
    body = request.json or {}
    col_id = body.get("collection_id")
    text = (body.get("text") or "").strip()
    ai_action = body.get("ai_action", "allow")
    admin_action = body.get("admin_action", "allow")

    if not text and not col_id:
        return jsonify({"error": "text 또는 collection_id 필수"}), 400

    is_correction = ai_action != admin_action
    correction_type = None
    if is_correction:
        if ai_action in ("block", "warn") and admin_action == "allow":
            correction_type = "FP"
        elif ai_action == "allow" and admin_action in ("block", "warn"):
            correction_type = "FN"

    conn = _get_mod_db()
    try:
        if col_id:
            conn.execute(
                "UPDATE raw_collections SET admin_action=?, admin_reason=?, is_correction=?, correction_type=? WHERE id=?",
                (admin_action, body.get("admin_reason"), 1 if is_correction else 0, correction_type, col_id))
        elif text:
            text_hash = _mod_hashlib.sha256(text.encode()).hexdigest()
            conn.execute(
                "INSERT OR REPLACE INTO raw_collections (text, text_hash, source, source_id, ai_action, admin_action, admin_reason, is_correction, correction_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (text[:2000], text_hash, body.get("source", "manual"), body.get("source_id"),
                 ai_action, admin_action, body.get("admin_reason"), 1 if is_correction else 0, correction_type))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "is_correction": is_correction, "correction_type": correction_type})


@training_bp.route("/api/mod/stats")
def mod_stats():
    """수집 + 정제 통계
    ---
    tags: [Moderation]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    conn = _get_mod_db()
    try:
        raw = conn.execute("""SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE date(created_at) = date('now')) as today_collected,
            COUNT(*) FILTER (WHERE is_correction = 1) as corrections,
            COUNT(*) FILTER (WHERE refine_status = 'pending') as pending,
            COUNT(*) FILTER (WHERE refine_status = 'included') as included,
            COUNT(*) FILTER (WHERE refine_status = 'excluded') as excluded,
            COUNT(*) FILTER (WHERE ai_action = 'block') as blocked,
            COUNT(*) FILTER (WHERE ai_action = 'warn') as warned
        FROM raw_collections""").fetchone()

        refined = conn.execute("SELECT COUNT(*) as total FROM refined_samples").fetchone()
    finally:
        conn.close()

    # 학습 모델 정보
    tc = _get_training_db()
    try:
        deployed = tc.execute("SELECT id, f1_score, base_model FROM training_runs WHERE status='deployed'").fetchone()
    finally:
        tc.close()

    return jsonify({
        "raw": dict(raw) if raw else {},
        "refined": {"total": refined["total"] if refined else 0},
        "model": {"name": f"custom:v{deployed['id']}" if deployed else "base", "f1": deployed["f1_score"] if deployed else None} if deployed else {"name": "base", "f1": None},
    })


@training_bp.route("/api/mod/refine", methods=["POST"])
def mod_refine():
    """정제 파이프라인 실행
    ---
    tags: [Moderation]
    security: [{Bearer: []}]
    parameters:
      - {name: body, in: body, schema: {type: object, properties: {
          action: {type: string, default: "run"},
          options: {type: object}
        }}}
    responses:
      200:
        schema: {type: object}
    """
    body = request.json or {}
    action = body.get("action", "run")

    conn = _get_mod_db()
    try:
        if action == "status":
            pending = conn.execute("SELECT COUNT(*) FROM raw_collections WHERE refine_status='pending'").fetchone()[0]
            refined = conn.execute("SELECT COUNT(*) FROM refined_samples").fetchone()[0]
            return jsonify({"pending": pending, "refined": refined})

        # 정제 실행
        opts = body.get("options", {})
        min_length = opts.get("min_length", 5)
        dedup = opts.get("dedup", True)

        # pending 데이터 로드
        rows = conn.execute("SELECT * FROM raw_collections WHERE refine_status='pending'").fetchall()
        total_input = len(rows)

        removed = {"duplicates": 0, "too_short": 0, "low_quality": 0}
        accepted = []
        seen_hashes = set()

        # 기존 refined 해시
        for r in conn.execute("SELECT text_hash FROM refined_samples"):
            seen_hashes.add(r["text_hash"])

        for row in rows:
            text = row["text"].strip()
            text_hash = row["text_hash"]

            # 최소 길이
            if len(text) < min_length:
                conn.execute("UPDATE raw_collections SET refine_status='excluded' WHERE id=?", (row["id"],))
                removed["too_short"] += 1
                continue

            # 중복
            if dedup and text_hash in seen_hashes:
                conn.execute("UPDATE raw_collections SET refine_status='duplicate' WHERE id=?", (row["id"],))
                removed["duplicates"] += 1
                continue

            # 라벨 결정 (관리자 > AI)
            label = row["admin_action"] or row["ai_action"] or "allow"
            categories = row["ai_categories"] or "[]"

            # refined에 추가
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO refined_samples (text, text_hash, label, categories, confidence, source) VALUES (?, ?, ?, ?, ?, ?)",
                    (text, text_hash, label, categories, row["ai_confidence"] or 0.9, row["source"]))
                conn.execute("UPDATE raw_collections SET refine_status='included' WHERE id=?", (row["id"],))
                seen_hashes.add(text_hash)
                accepted.append(label)
            except Exception:
                removed["low_quality"] += 1
                conn.execute("UPDATE raw_collections SET refine_status='excluded' WHERE id=?", (row["id"],))

        conn.commit()

        # 라벨 분포
        from collections import Counter
        label_dist = dict(Counter(accepted))

        refined_total = conn.execute("SELECT COUNT(*) FROM refined_samples").fetchone()[0]
    finally:
        conn.close()

    return jsonify({
        "status": "completed",
        "input": total_input,
        "output": len(accepted),
        "removed": removed,
        "label_distribution": label_dist,
        "refined_total": refined_total,
    })



TRAINING_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", "data")
os.makedirs(TRAINING_DATA_DIR, exist_ok=True)


# ═══ 사용자 관리 API ══════════════════════════════════════════════════════════

@training_bp.route("/api/admin/users")
# @require_web_login  # handled by before_request
def admin_users_list():
    """사용자 목록
    ---
    tags: [Admin]
    responses:
      200:
        schema: {type: object}
    """
    username = session.get("web_user", "")
    conn = _get_users_db()
    try:
        me = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not me or me["role"] != "admin":
            return jsonify({"error": "관리자만 접근 가능"}), 403
        users = [dict(r) for r in conn.execute("SELECT id, username, display_name, role, permissions, is_active, last_login, created_at FROM users ORDER BY id").fetchall()]
    finally:
        conn.close()
    return jsonify({"users": users})

@training_bp.route("/api/admin/users", methods=["POST"])
# @require_web_login  # handled by before_request
def admin_users_create():
    """사용자 추가
    ---
    tags: [Admin]
    parameters:
      - {name: body, in: body, schema: {type: object, required: [username, password], properties: {
          username: {type: string},
          password: {type: string, minLength: 8, description: "최소 8자"},
          display_name: {type: string},
          role: {type: string, enum: [admin, operator, viewer]}
        }}}
    responses:
      201:
        schema: {type: object}
    """
    username = session.get("web_user", "")
    conn = _get_users_db()
    try:
        me = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not me or me["role"] != "admin":
            return jsonify({"error": "관리자만 접근 가능"}), 403

        body = request.json or {}
        new_user = body.get("username", "").strip()
        password = body.get("password", "")
        display_name = body.get("display_name", new_user)
        role = body.get("role", "viewer")

        if not new_user or not password:
            return jsonify({"error": "아이디와 비밀번호 필수"}), 400
        if len(password) < 8:
            return jsonify({"error": "비밀번호 8자 이상"}), 400
        if role not in ROLE_DEFAULTS:
            return jsonify({"error": "유효하지 않은 역할: " + role}), 400

        pw_hash = _core_hash_password(password)
        perms = json.dumps(ROLE_DEFAULTS.get(role, {}))

        try:
            conn.execute("INSERT INTO users (username, password_hash, display_name, role, permissions) VALUES (?, ?, ?, ?, ?)",
                (new_user, pw_hash, display_name, role, perms))
            conn.commit()
            uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return jsonify({"ok": True, "id": uid, "username": new_user, "role": role}), 201
        except Exception as e:
            if "UNIQUE" in str(e):
                return jsonify({"error": "이미 존재하는 아이디"}), 409
            return internal_error(e)
    finally:
        conn.close()

@training_bp.route("/api/admin/users/<int:uid>", methods=["PUT"])
# @require_web_login  # handled by before_request
def admin_users_update(uid):
    """사용자 수정 (역할/권한/활성화/비밀번호)
    ---
    tags: [Admin]
    parameters:
      - {name: uid, in: path, type: integer, required: true}
      - {name: body, in: body, schema: {type: object, properties: {
          role: {type: string, enum: [admin, operator, viewer]},
          permissions: {type: object},
          is_active: {type: boolean},
          password: {type: string, minLength: 8, description: "변경 시 최소 8자"}
        }}}
    responses:
      200:
        schema: {type: object}
    """
    username = session.get("web_user", "")
    conn = _get_users_db()
    try:
        me = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not me or me["role"] != "admin":
            return jsonify({"error": "관리자만 접근 가능"}), 403

        body = request.json or {}
        sets, vals = [], []
        if "display_name" in body:
            sets.append("display_name=?"); vals.append(body["display_name"])
        # 🔴 v2.125.67 전에는 여기서 역할을 검증하지 않았고(생성은 검증한다) permissions 문자열을 **그대로** 저장했다 —
        #    JSON이 아닌 값이면 app.py index()가 그 사용자 화면에 깨진 스크립트를 주입했다. 둘 다 400으로 거절.
        if "role" in body:
            if body["role"] not in ROLE_DEFAULTS:
                return jsonify({"error": "유효하지 않은 역할: " + str(body["role"])}), 400
            sets.append("role=?"); vals.append(body["role"])
            sets.append("permissions=?"); vals.append(json.dumps(ROLE_DEFAULTS[body["role"]]))
        if "permissions" in body:
            p = body["permissions"]
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except ValueError:
                    return jsonify({"error": "permissions는 JSON 객체여야 합니다"}), 400
            if not isinstance(p, dict):
                return jsonify({"error": "permissions는 JSON 객체여야 합니다"}), 400
            sets.append("permissions=?"); vals.append(json.dumps(p))
        if "is_active" in body:
            sets.append("is_active=?"); vals.append(1 if body["is_active"] else 0)
        if "password" in body and body["password"]:
            if len(body["password"]) < 8:
                return jsonify({"error": "비밀번호 8자 이상"}), 400
            pw_hash = _core_hash_password(body["password"])
            sets.append("password_hash=?"); vals.append(pw_hash)

        if not sets:
            return jsonify({"error": "변경 항목 없음"}), 400

        sets.append("updated_at=datetime('now')")
        vals.append(uid)
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})

@training_bp.route("/api/admin/users/<int:uid>", methods=["DELETE"])
# @require_web_login  # handled by before_request
def admin_users_delete(uid):
    """사용자 삭제
    ---
    tags: [Admin]
    parameters:
      - {name: uid, in: path, type: integer, required: true}
    responses:
      200:
        schema: {type: object}
    """
    username = session.get("web_user", "")
    conn = _get_users_db()
    try:
        me = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not me or me["role"] != "admin":
            return jsonify({"error": "관리자만 접근 가능"}), 403
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if target and target["username"] == "admin":
            return jsonify({"error": "기본 admin 삭제 불가"}), 400
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})

@training_bp.route("/api/admin/users/me")
# @require_web_login  # handled by before_request
def admin_users_me():
    """현재 로그인 사용자 정보
    ---
    tags: [Admin]
    responses:
      200:
        schema: {type: object}
    """
    username = session.get("web_user", "admin")
    conn = _get_users_db()
    try:
        user = conn.execute("SELECT id, username, display_name, role, permissions FROM users WHERE username=?", (username,)).fetchone()
    finally:
        conn.close()
    if not user:
        return jsonify({"username": username, "role": "admin", "permissions": ROLE_DEFAULTS["admin"]})
    return jsonify(dict(user))


@training_bp.route("/api/training/files")
def training_files_list():
    """List training data files
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    files = []
    for f in sorted(os.listdir(TRAINING_DATA_DIR)):
        path = os.path.join(TRAINING_DATA_DIR, f)
        if os.path.isfile(path):
            stat = os.stat(path)
            # 줄 수 카운트
            lines = 0
            try:
                with open(path, "r") as fh:
                    lines = sum(1 for _ in fh)
            except Exception: pass
            files.append({
                "name": f,
                "size_bytes": stat.st_size,
                "size_display": f"{stat.st_size / 1024:.1f}KB" if stat.st_size < 1024*1024 else f"{stat.st_size / 1024 / 1024:.1f}MB",
                "lines": lines,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return jsonify({"files": files, "dir": TRAINING_DATA_DIR})

@training_bp.route("/api/training/files/upload", methods=["POST"])
def training_files_upload():
    """Upload training data file
    ---
    tags: [Training]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - {name: file, in: formData, type: file, required: true}
    responses:
      200:
        schema: {type: object}
    """
    if "file" not in request.files:
        return jsonify({"error": "파일 없음"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "파일명 없음"}), 400
    # 허용 확장자
    allowed = {".jsonl", ".json", ".csv", ".tsv", ".txt"}
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed:
        return jsonify({"error": f"허용 형식: {', '.join(allowed)}"}), 400
    # 크기 제한 10MB
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 10 * 1024 * 1024:
        return jsonify({"error": "10MB 초과"}), 400
    # 저장
    safe_name = secure_filename(f.filename)
    if not safe_name:
        return jsonify({"error": "invalid filename"}), 400
    path = os.path.join(TRAINING_DATA_DIR, safe_name)
    f.save(path)
    lines = 0
    try:
        with open(path) as fh:
            lines = sum(1 for _ in fh)
    except Exception: pass
    return jsonify({"ok": True, "name": safe_name, "size": size, "lines": lines})

@training_bp.route("/api/training/files/download/<path:filename>")
def training_files_download(filename):
    """Download training data file
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: filename, in: path, type: string, required: true}
    responses:
      200:
        description: File content
    """
    safe = os.path.basename(filename)
    path = os.path.join(TRAINING_DATA_DIR, safe)
    if not os.path.exists(path):
        return jsonify({"error": "파일 없음"}), 404
    from flask import send_file
    return send_file(path, as_attachment=True, download_name=safe)

@training_bp.route("/api/training/files/<path:filename>", methods=["DELETE"])
def training_files_delete(filename):
    """Delete training data file
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: filename, in: path, type: string, required: true}
    responses:
      200:
        schema: {type: object}
    """
    safe = os.path.basename(filename)
    path = os.path.join(TRAINING_DATA_DIR, safe)
    if not os.path.exists(path):
        return jsonify({"error": "파일 없음"}), 404
    os.remove(path)
    return jsonify({"ok": True, "deleted": safe})

@training_bp.route("/api/training/files/preview/<path:filename>")
def training_files_preview(filename):
    """Preview first 20 lines of a training file
    ---
    tags: [Training]
    security: [{Bearer: []}]
    parameters:
      - {name: filename, in: path, type: string, required: true}
    responses:
      200:
        schema: {type: object}
    """
    safe = os.path.basename(filename)
    path = os.path.join(TRAINING_DATA_DIR, safe)
    if not os.path.exists(path):
        return jsonify({"error": "파일 없음"}), 404
    lines = []
    with open(path, "r") as fh:
        for i, line in enumerate(fh):
            if i >= 20: break
            lines.append(line.rstrip())
    return jsonify({"name": safe, "preview": lines, "count": len(lines)})


@training_bp.route("/api/training/progress")
def training_progress():
    """Training progress (logs + metrics)
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", "outputs")
    result = {"running": _training_status["running"], "run_id": _training_status["run_id"]}

    # train_result.json
    train_result = os.path.join(output_dir, "train_result.json")
    if os.path.exists(train_result):
        with open(train_result) as f:
            result["train"] = json.load(f)

    # eval_result.json
    eval_result = os.path.join(output_dir, "eval_result.json")
    if os.path.exists(eval_result):
        with open(eval_result) as f:
            result["eval"] = json.load(f)

    # deploy_result.json
    deploy_result = os.path.join(output_dir, "deploy_result.json")
    if os.path.exists(deploy_result):
        with open(deploy_result) as f:
            result["deploy"] = json.load(f)

    return jsonify(result)

@training_bp.route("/api/training/gpu")
def training_gpu_status():
    """GPU status for training priority
    ---
    tags: [Training]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            idle: {type: boolean}
            allowed_hours: {type: boolean}
            gpu_pct: {type: number}
            queue_pending: {type: integer}
            training_running: {type: boolean}
    """
    gpu_pct = 0
    try:
        if shutil.which("nvidia-smi"):
            r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                gpu_pct = int(parts[0])
    except Exception as e:
        logger.debug("GPU utilization check failed: %s", e)

    conn = _get_main_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]
    finally:
        conn.close()

    return jsonify({
        "idle": _is_gpu_idle(),
        "allowed_hours": _is_allowed_hours(),
        "gpu_pct": gpu_pct,
        "queue_pending": pending,
        "training_running": _training_status["running"],
        "training_paused": _training_status["paused"],
        "training_run_id": _training_status["run_id"],
    })

