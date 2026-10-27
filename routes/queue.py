"""Queue API Blueprint — 작업 큐 제출/조회/관리"""
import json
import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response

logger = logging.getLogger("ollama-admin")

queue_bp = Blueprint("queue", __name__)


def _escape_like(val: str) -> str:
    """LIKE 패턴 특수문자 이스케이프"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# app.py에서 init_queue()로 주입
_get_db = None
_load_config = None
_worker_event = None
_image_worker_event = None
_auto_priority = None
_validate_webhook_url = None
_llm_response_cache = None
_row_to_dict = None
_FLUX_CHECKPOINT = None
_FAST_MODELS = None

# 큐 상태 (Blueprint 내부 관리)
_queue_paused = False
worker_alive = True


def init_queue(*, get_db, load_config, worker_event, image_worker_event,
               auto_priority, validate_webhook_url, llm_response_cache,
               row_to_dict, flux_checkpoint, fast_models):
    """app.py에서 의존성 주입"""
    global _get_db, _load_config, _worker_event, _image_worker_event
    global _auto_priority, _validate_webhook_url, _llm_response_cache
    global _row_to_dict, _FLUX_CHECKPOINT, _FAST_MODELS
    _get_db = get_db
    _load_config = load_config
    _worker_event = worker_event
    _image_worker_event = image_worker_event
    _auto_priority = auto_priority
    _validate_webhook_url = validate_webhook_url
    _llm_response_cache = llm_response_cache
    _row_to_dict = row_to_dict
    _FLUX_CHECKPOINT = flux_checkpoint
    _FAST_MODELS = fast_models


def get_queue_paused():
    return _queue_paused


def get_worker_alive():
    return worker_alive


def set_worker_alive(val):
    global worker_alive
    worker_alive = val


@queue_bp.route("/api/queue/submit", methods=["POST"])
def queue_submit():
    """Submit a single job to the queue
    ---
    tags: [Queue]
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
            prompt: {type: string, example: "Translate to English: 안녕하세요"}
            system: {type: string, example: "You are a translator.", description: "시스템 프롬프트 (생략 시 기본 한국어 프롬프트 자동 적용)"}
            priority: {type: integer, default: 5, minimum: 0, maximum: 9}
            metadata: {type: object, example: {"source": "api"}}
            options:
              type: object
              description: "Ollama generation options"
              properties:
                temperature: {type: number, example: 0.7}
                top_p: {type: number, example: 0.9}
                num_predict: {type: integer, example: 512}
    responses:
      200:
        description: Job submitted
        schema:
          type: object
          properties:
            job_id: {type: string}
            status: {type: string}
            position: {type: integer}
      400:
        description: Missing required fields
    """
    data = request.json or {}
    job_type = data.get("job_type", "llm")
    if job_type not in ("llm", "image"):
        return jsonify({"error": "job_type must be 'llm' or 'image'"}), 400

    model = data.get("model", "")
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    if not model:
        if job_type == "image":
            model = _FLUX_CHECKPOINT
        else:
            cfg = _load_config()
            model = cfg.get("default_model", "")
            if not model:
                return jsonify({"error": "model required (no default_model configured)"}), 400

    # LLM 응답 캐시 확인
    if job_type == "llm" and not data.get("no_cache"):
        from core.cache import generate_cache_key
        _ck = generate_cache_key(model, prompt, data.get("system", ""), data.get("options"))
        _cached = _llm_response_cache.get(_ck)
        if _cached:
            return jsonify({"job_id": "cached", "status": "completed", "response": _cached, "cached": True})

    if len(prompt) > 32000:
        return jsonify({"error": "prompt exceeds 32KB limit"}), 413
    system = data.get("system", "")
    if system and len(system) > 8000:
        return jsonify({"error": "system prompt exceeds 8KB limit"}), 413
    meta = json.dumps(data.get("metadata")) if data.get("metadata") else None
    if meta and len(meta) > 4096:
        return jsonify({"error": "metadata exceeds 4KB limit"}), 413

    conn = _get_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        if pending >= 10000:
            return jsonify({"error": "queue full (10,000 pending jobs)"}), 429

        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat() + "Z"
        opts = json.dumps(data["options"]) if data.get("options") else None
        webhook = data.get("webhook_url")
        if webhook and not _validate_webhook_url(webhook):
            return jsonify({"error": "invalid webhook_url (must be public http/https)"}), 400

        conn.execute(
            "INSERT INTO jobs (id, batch_id, model, system_prompt, prompt, priority, created_at, metadata, options, webhook_url, job_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, None, model, system or None, prompt, _auto_priority(model, data.get("priority", 5)), now, meta, opts, webhook, job_type),
        )
        position = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    _worker_event.set()
    _image_worker_event.set()
    return jsonify({"job_id": job_id, "status": "pending", "position": position})


@queue_bp.route("/api/queue/batch", methods=["POST"])
def queue_batch():
    """Submit a batch of jobs
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [jobs]
          properties:
            model: {type: string, description: "모델명 (생략 시 기본 모델 사용)"}
            system: {type: string, description: "시스템 프롬프트 (생략 시 기본 한국어 프롬프트 자동 적용)"}
            priority: {type: integer, default: 5}
            metadata: {type: object}
            jobs:
              type: array
              items:
                type: object
                required: [prompt]
                properties:
                  prompt: {type: string}
                  model: {type: string}
                  system: {type: string}
                  priority: {type: integer}
    responses:
      200:
        schema:
          type: object
          properties:
            batch_id: {type: string}
            job_ids: {type: array, items: {type: string}}
            count: {type: integer}
    """
    data = request.json or {}
    jobs_list = data.get("jobs", [])
    default_model = data.get("model", "") or _load_config().get("default_model", "")
    default_system = data.get("system")
    default_priority = data.get("priority", 5)
    default_job_type = data.get("job_type", "llm")
    if default_job_type not in ("llm", "image"):
        return jsonify({"error": "job_type must be 'llm' or 'image'"}), 400

    if not jobs_list:
        return jsonify({"error": "jobs list required"}), 400
    if len(jobs_list) > 1000:
        return jsonify({"error": "batch exceeds 1,000 jobs limit"}), 413

    conn = _get_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        if pending + len(jobs_list) > 10000:
            return jsonify({"error": f"queue full ({pending} pending + {len(jobs_list)} new > 10,000)"}), 429

        batch_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat() + "Z"
        default_meta = json.dumps(data.get("metadata")) if data.get("metadata") else None
        default_opts = json.dumps(data.get("options")) if data.get("options") else None
        job_ids = []

        for item in jobs_list:
            prompt = item.get("prompt", "")
            if not prompt:
                continue
            jtype = item.get("job_type", default_job_type)
            model = item.get("model", default_model)
            if jtype == "llm" and not model:
                continue
            if jtype == "image" and not model:
                model = _FLUX_CHECKPOINT
            job_id = str(uuid.uuid4())
            system = item.get("system", default_system)
            priority = _auto_priority(model, item.get("priority", default_priority))
            meta = json.dumps(item.get("metadata")) if item.get("metadata") else default_meta
            opts = json.dumps(item.get("options")) if item.get("options") else default_opts

            conn.execute(
                "INSERT INTO jobs (id, batch_id, model, system_prompt, prompt, priority, created_at, metadata, options, job_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, batch_id, model, system, prompt, priority, now, meta, opts, jtype),
            )
            job_ids.append(job_id)

        conn.commit()
    finally:
        conn.close()

    _worker_event.set()
    _image_worker_event.set()
    return jsonify({"batch_id": batch_id, "job_ids": job_ids, "count": len(job_ids)})


@queue_bp.route("/api/queue/batch/upload", methods=["POST"])
def queue_batch_upload():
    """Upload JSONL file as batch
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - {name: file, in: formData, type: file, required: true, description: "JSONL file (one JSON per line with prompt, model, system, options)"}
      - {name: model, in: formData, type: string, description: "Default model"}
      - {name: system, in: formData, type: string, description: "Default system prompt"}
    responses:
      200:
        schema:
          type: object
          properties:
            batch_id: {type: string}
            count: {type: integer}
      400:
        description: Invalid file
      413:
        description: File too large or too many jobs
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400

    content = f.read(10 * 1024 * 1024 + 1)  # 10MB + 1byte
    if len(content) > 10 * 1024 * 1024:
        return jsonify({"error": "file exceeds 10MB limit"}), 413

    default_model = request.form.get("model", "") or _load_config().get("default_model", "")
    default_system = request.form.get("system", "")
    lines = content.decode("utf-8", errors="replace").strip().split("\n")

    if len(lines) > 1000:
        return jsonify({"error": f"file has {len(lines)} lines, max 1,000"}), 413

    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat() + "Z"
    job_ids = []
    conn = _get_db()
    try:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                item = {"prompt": line}

            prompt = item.get("prompt", "")
            if not prompt or len(prompt) > 32000:
                continue
            model = item.get("model", default_model)
            if not model:
                continue

            job_id = str(uuid.uuid4())
            system = item.get("system", default_system) or None
            meta = json.dumps(item.get("metadata")) if item.get("metadata") else None
            opts = json.dumps(item.get("options")) if item.get("options") else None

            conn.execute(
                "INSERT INTO jobs (id, batch_id, model, system_prompt, prompt, priority, created_at, metadata, options) VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, batch_id, model, system, prompt, item.get("priority", 5), now, meta, opts),
            )
            job_ids.append(job_id)

        conn.commit()
    finally:
        conn.close()
    _worker_event.set()
    _image_worker_event.set()
    logger.info(f"Batch {batch_id[:8]} uploaded — {len(job_ids)} jobs from file")
    return jsonify({"batch_id": batch_id, "job_ids": job_ids, "count": len(job_ids)})


@queue_bp.route("/api/queue/job/<job_id>")
def queue_job(job_id):
    """Get job status and result
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: job_id, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            id: {type: string}
            status: {type: string, enum: [pending, running, completed, failed]}
            position: {type: integer, description: "대기 순서 (pending일 때만, 1부터 시작)"}
            queue_pending: {type: integer, description: "전체 대기 작업 수"}
            model: {type: string}
            prompt: {type: string}
            response: {type: string}
            tokens: {type: integer}
            duration_ms: {type: integer}
      404:
        description: Job not found
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return jsonify({"error": "job not found"}), 404
        result = _row_to_dict(row)
        if result.get("status") == "pending":
            pos = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='pending' AND created_at < ? AND id != ?",
                (result.get("created_at", ""), job_id)
            ).fetchone()[0]
            result["position"] = pos + 1
            result["queue_pending"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        elif result.get("status") == "running":
            result["position"] = 0
            result["queue_pending"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
    finally:
        conn.close()
    return jsonify(result)


@queue_bp.route("/api/queue/batch/<batch_id>")
def queue_batch_status(batch_id):
    """Get batch status with all jobs
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: batch_id, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            batch_id: {type: string}
            total: {type: integer}
            pending: {type: integer}
            running: {type: integer}
            completed: {type: integer}
            failed: {type: integer}
            jobs: {type: array, items: {type: object}}
      404:
        description: Batch not found
    """
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM jobs WHERE batch_id=? ORDER BY created_at ASC", (batch_id,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return jsonify({"error": "batch not found"}), 404

    jobs = [_row_to_dict(r) for r in rows]
    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    for j in jobs:
        s = j["status"]
        if s in counts:
            counts[s] += 1

    return jsonify({"batch_id": batch_id, "total": len(jobs), **counts, "jobs": jobs})


@queue_bp.route("/api/queue/batch/<batch_id>/results")
def queue_batch_results(batch_id):
    """Download batch results as NDJSON
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: batch_id, in: path, type: string, required: true}
    responses:
      200:
        description: NDJSON stream (one JSON object per line)
        schema: {type: file}
      404:
        description: Batch not found
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT prompt, response, status, error, tokens, duration_ms, metadata FROM jobs WHERE batch_id=? ORDER BY created_at ASC",
            (batch_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return jsonify({"error": "batch not found"}), 404

    def gen():
        for r in rows:
            d = dict(r)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            yield json.dumps(d, ensure_ascii=False) + "\n"

    return Response(gen(), mimetype="application/x-ndjson",
                    headers={"Content-Disposition": f"attachment; filename=batch_{batch_id}.jsonl"})


@queue_bp.route("/api/queue/status")
def queue_status():
    """Get queue counts and worker status
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            pending: {type: integer}
            running: {type: integer}
            completed: {type: integer}
            failed: {type: integer}
            llm_pending: {type: integer, description: "LLM 대기 작업 수"}
            image_pending: {type: integer, description: "이미지 대기 작업 수"}
            worker_alive: {type: boolean}
    """
    conn = _get_db()
    try:
        counts = {}
        for status in ["pending", "running", "completed", "failed"]:
            counts[status] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]
        llm_pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND (job_type IS NULL OR job_type='llm')").fetchone()[0]
        img_pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND job_type='image'").fetchone()[0]
    finally:
        conn.close()
    return jsonify({**counts, "llm_pending": llm_pending, "image_pending": img_pending,
                    "worker_alive": worker_alive, "paused": _queue_paused})


@queue_bp.route("/api/queue/pause", methods=["POST"])
def queue_pause():
    """Pause queue processing (current job finishes)
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            paused: {type: boolean}
    """
    global _queue_paused
    _queue_paused = True
    logger.info("Queue paused")
    return jsonify({"ok": True, "paused": True})


@queue_bp.route("/api/queue/resume", methods=["POST"])
def queue_resume():
    """Resume queue processing
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            paused: {type: boolean}
    """
    global _queue_paused
    _queue_paused = False
    _worker_event.set()
    _image_worker_event.set()
    logger.info("Queue resumed")
    return jsonify({"ok": True, "paused": False})


@queue_bp.route("/api/queue/retry/<job_id>", methods=["POST"])
def queue_retry(job_id):
    """Retry a failed job
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: job_id, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
      404:
        description: Job not found or not failed
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE jobs SET status='pending', retry_count=0, error=NULL, started_at=NULL, completed_at=NULL, next_retry_at=NULL WHERE id=? AND status='failed'",
            (job_id,),
        )
        conn.commit()
        rowcount = cur.rowcount
    finally:
        conn.close()
    if rowcount > 0:
        _worker_event.set()
        _image_worker_event.set()
        logger.info(f"Job {job_id[:8]} retried manually")
    return jsonify({"ok": rowcount > 0})


@queue_bp.route("/api/queue/stats")
def queue_stats():
    """Get queue performance statistics
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            total_jobs: {type: integer}
            total_tokens: {type: integer}
            total_duration_ms: {type: integer}
            avg_tokens: {type: number}
            avg_duration_ms: {type: number}
            avg_tok_per_sec: {type: number}
            jobs_per_model: {type: object}
            recent_jobs: {type: array, items: {type: object}}
    """
    conn = _get_db()
    try:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   COALESCE(SUM(tokens),0) as total_tokens,
                   COALESCE(SUM(duration_ms),0) as total_duration_ms,
                   COALESCE(AVG(tokens),0) as avg_tokens,
                   COALESCE(AVG(duration_ms),0) as avg_duration_ms
            FROM jobs WHERE status='completed' AND tokens > 0
        """).fetchone()
        stats = dict(row)
        stats["avg_tokens"] = round(stats["avg_tokens"], 1)
        stats["avg_duration_ms"] = round(stats["avg_duration_ms"], 1)
        if stats["total_duration_ms"] > 0:
            stats["avg_tok_per_sec"] = round(stats["total_tokens"] / (stats["total_duration_ms"] / 1000), 1)
        else:
            stats["avg_tok_per_sec"] = 0

        model_rows = conn.execute("""
            SELECT model,
                   COUNT(*) as count,
                   COALESCE(SUM(tokens),0) as tokens,
                   COALESCE(AVG(tokens),0) as avg_tokens,
                   COALESCE(AVG(duration_ms),0) as avg_duration_ms,
                   COALESCE(SUM(duration_ms),0) as total_duration_ms
            FROM jobs WHERE status='completed' AND tokens > 0
            GROUP BY model ORDER BY count DESC
        """).fetchall()
        stats["jobs_per_model"] = {}
        for mr in model_rows:
            d = dict(mr)
            d["avg_tokens"] = round(d["avg_tokens"], 1)
            d["avg_duration_ms"] = round(d["avg_duration_ms"], 1)
            d["tok_per_sec"] = round(d["tokens"] / (d["total_duration_ms"] / 1000), 1) if d["total_duration_ms"] > 0 else 0
            stats["jobs_per_model"][d["model"]] = d

        # ETA 계산
        pending_models = conn.execute(
            "SELECT model, COUNT(*) as cnt FROM jobs WHERE status IN ('pending','running') GROUP BY model"
        ).fetchall()
        eta_ms = 0
        eta_detail = {}
        for pm in pending_models:
            m = dict(pm)
            model_stat = stats["jobs_per_model"].get(m["model"])
            avg_ms = model_stat["avg_duration_ms"] if model_stat else 10000
            model_eta = int(avg_ms * m["cnt"])
            eta_detail[m["model"]] = {"pending": m["cnt"], "eta_ms": model_eta}
            eta_ms += model_eta
        stats["eta_ms"] = eta_ms
        stats["eta_sec"] = round(eta_ms / 1000)
        stats["eta_detail"] = eta_detail

        # Recent 20 jobs
        recent = conn.execute("""
            SELECT id, status, model, prompt, response, error, tokens, duration_ms, retry_count, priority, created_at, completed_at, job_type
            FROM jobs ORDER BY created_at DESC LIMIT 20
        """).fetchall()
        stats["recent_jobs"] = [_row_to_dict(r) for r in recent]
    finally:
        conn.close()

    return jsonify(stats)


@queue_bp.route("/api/queue/jobs")
def queue_jobs_search():
    """Search and filter jobs
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: status, in: query, type: string, enum: [pending, running, completed, failed]}
      - {name: model, in: query, type: string}
      - {name: q, in: query, type: string, description: "Search prompt text"}
      - {name: job_id, in: query, type: string, description: "Exact job ID"}
      - {name: limit, in: query, type: integer, default: 50}
      - {name: offset, in: query, type: integer, default: 0}
    responses:
      200:
        schema:
          type: object
          properties:
            jobs: {type: array, items: {type: object}}
            total: {type: integer}
    """
    status = request.args.get("status")
    model = request.args.get("model")
    q = request.args.get("q")
    job_id = request.args.get("job_id")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400

    conn = _get_db()
    try:
        where, params = [], []
        if status:
            where.append("status=?"); params.append(status)
        if model:
            where.append("model=?"); params.append(model)
        if q:
            where.append("prompt LIKE ? ESCAPE '\\'"); params.append(f"%{_escape_like(q)}%")
        if job_id:
            where.append("id=?"); params.append(job_id)

        clause = " AND ".join(where) if where else "1=1"
        total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"jobs": [_row_to_dict(r) for r in rows], "total": total})


@queue_bp.route("/api/queue/cancel/<job_id>", methods=["POST"])
def queue_cancel(job_id):
    """Cancel a pending job
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: job_id, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
    """
    conn = _get_db()
    try:
        cur = conn.execute("UPDATE jobs SET status='failed', error='cancelled' WHERE id=? AND status='pending'", (job_id,))
        conn.commit()
        rowcount = cur.rowcount
    finally:
        conn.close()
    return jsonify({"ok": rowcount > 0})


@queue_bp.route("/api/queue/cancel/batch/<batch_id>", methods=["POST"])
def queue_cancel_batch(batch_id):
    """Cancel all pending jobs in a batch
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - {name: batch_id, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            cancelled: {type: integer}
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE jobs SET status='failed', error='cancelled' WHERE batch_id=? AND status='pending'", (batch_id,)
        )
        cancelled = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "cancelled": cancelled})


@queue_bp.route("/api/queue/purge", methods=["POST"])
def queue_purge():
    """Purge completed/failed jobs
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            before: {type: string, description: "ISO 8601 datetime - purge jobs completed before this"}
    responses:
      200:
        schema:
          type: object
          properties:
            purged: {type: integer}
    """
    data = request.json or {}
    before = data.get("before")
    conn = _get_db()
    try:
        if before:
            cur = conn.execute("DELETE FROM jobs WHERE status IN ('completed','failed') AND completed_at < ?", (before,))
        else:
            cur = conn.execute("DELETE FROM jobs WHERE status IN ('completed','failed')")
        purged = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({"purged": purged})


@queue_bp.route("/api/queue/batches")
def queue_batches():
    """List all batches (latest 50)
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            batches:
              type: array
              items:
                type: object
                properties:
                  batch_id: {type: string}
                  total: {type: integer}
                  pending: {type: integer}
                  completed: {type: integer}
                  failed: {type: integer}
    """
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT batch_id,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                   SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) as running,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                   MIN(created_at) as created_at,
                   MAX(completed_at) as last_completed
            FROM jobs WHERE batch_id IS NOT NULL
            GROUP BY batch_id ORDER BY MIN(created_at) DESC LIMIT 50
        """).fetchall()
    finally:
        conn.close()
    return jsonify({"batches": [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# Queue Pipeline (v2.70.0) — 다단계 순차 처리
# ---------------------------------------------------------------------------

@queue_bp.route("/api/queue/pipeline", methods=["POST"])
def queue_pipeline():
    """Submit a multi-step pipeline job — 이전 단계 응답이 다음 단계 입력으로 연결
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [steps]
          properties:
            steps:
              type: array
              description: "파이프라인 단계 배열. prompt에 {{prev.response}} 사용 가능"
              items:
                type: object
                required: [prompt]
                properties:
                  model: {type: string, example: "qwen2.5:14b"}
                  prompt: {type: string, example: "다음 글을 다듬어주세요: {{prev.response}}"}
                  system: {type: string}
                  options: {type: object}
            name: {type: string, description: "파이프라인 이름 (선택)"}
            priority: {type: integer, default: 5}
            webhook_url: {type: string, description: "최종 완료 시 콜백 URL"}
    responses:
      200:
        schema:
          type: object
          properties:
            pipeline_id: {type: string}
            batch_id: {type: string}
            steps: {type: integer}
            status: {type: string}
    """
    data = request.json or {}
    steps = data.get("steps", [])
    if not steps or len(steps) < 2:
        return jsonify({"error": "pipeline requires at least 2 steps"}), 400
    if len(steps) > 10:
        return jsonify({"error": "pipeline max 10 steps"}), 400

    cfg = _load_config()
    default_model = cfg.get("default_model", "qwen2.5:14b")
    base_priority = data.get("priority", 5)
    webhook = data.get("webhook_url")
    if webhook and not _validate_webhook_url(webhook):
        return jsonify({"error": "invalid webhook_url"}), 400

    pipeline_id = f"pipe-{uuid.uuid4().hex[:8]}"
    batch_id = f"pipe-batch-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat() + "Z"

    # 파이프라인 메타데이터 — 각 job에 저장하여 워커가 체인 처리
    job_ids = []
    import sqlite3 as _sqlite3
    for _retry in range(3):
        conn = _get_db()
        try:
            for i, step in enumerate(steps):
                prompt = step.get("prompt", "")
                if not prompt:
                    return jsonify({"error": f"step {i}: prompt required"}), 400

                model = step.get("model") or default_model
                system = step.get("system", "")
                opts = json.dumps(step.get("options")) if step.get("options") else None

                job_id = str(uuid.uuid4())
                job_ids.append(job_id)

                pipe_meta = json.dumps({
                    "pipeline_id": pipeline_id,
                    "pipeline_name": data.get("name", ""),
                    "step_index": i,
                    "total_steps": len(steps),
                    "prev_job_id": job_ids[i - 1] if i > 0 else None,
                    "is_last_step": i == len(steps) - 1,
                })

                # 첫 단계만 pending, 나머지는 waiting (이전 단계 완료 대기)
                status = "pending" if i == 0 else "waiting"

                conn.execute(
                    "INSERT INTO jobs (id, batch_id, model, system_prompt, prompt, priority, "
                    "created_at, metadata, options, webhook_url, job_type, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, batch_id, model, system or None, prompt,
                     _auto_priority(model, base_priority), now, pipe_meta, opts,
                     webhook if (i == len(steps) - 1) else None,
                     "llm", status),
                )
            conn.commit()
            break  # 성공 시 루프 탈출
        except _sqlite3.OperationalError as e:
            if "locked" in str(e) and _retry < 2:
                import time as _time
                _time.sleep(0.5)
                job_ids.clear()  # 재시도 시 job_ids 초기화
                continue
            return jsonify({"error": f"database error: {e}"}), 503
        finally:
            conn.close()

    _worker_event.set()
    return jsonify({
        "pipeline_id": pipeline_id,
        "batch_id": batch_id,
        "steps": len(steps),
        "job_ids": job_ids,
        "status": "running",
    })


@queue_bp.route("/api/queue/pipeline/<pipeline_id>")
def queue_pipeline_status(pipeline_id):
    """Get pipeline status and step results
    ---
    tags: [Queue]
    security: [{Bearer: []}]
    parameters:
      - name: pipeline_id
        in: path
        type: string
        required: true
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, model, status, response, tokens, duration_ms, metadata "
            "FROM jobs WHERE metadata LIKE ? ORDER BY created_at ASC",
            (f'%"pipeline_id": "{pipeline_id}"%',),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return jsonify({"error": "pipeline not found"}), 404

    steps = []
    for r in rows:
        try:
            meta = json.loads(r["metadata"] or "{}")
        except Exception:
            meta = {}
        steps.append({
            "step": meta.get("step_index", 0),
            "model": r["model"],
            "status": r["status"],
            "response": r["response"],
            "tokens": r["tokens"],
            "duration_ms": r["duration_ms"],
        })

    total = len(steps)
    completed = sum(1 for s in steps if s["status"] == "completed")
    failed = sum(1 for s in steps if s["status"] == "failed")

    return jsonify({
        "pipeline_id": pipeline_id,
        "total_steps": total,
        "completed": completed,
        "failed": failed,
        "status": "completed" if completed == total else ("failed" if failed > 0 else "running"),
        "steps": steps,
    })
