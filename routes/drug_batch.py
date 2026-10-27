"""Drug Batch Pipeline API — 배치 실행 + 스테이징 관리

Blueprint: drug_batch_bp
URL prefix: /api/drug-batch
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

drug_batch_bp = Blueprint("drug_batch", __name__)
logger = logging.getLogger("ollama-admin")

CLINIC_DB = None


def _get_db() -> sqlite3.Connection:
    path = CLINIC_DB or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clinic_codes.db"
    )
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── Pipeline Execution ──────────────────────────

@drug_batch_bp.route("/api/drug-batch/run", methods=["POST"])
def batch_run():
    """배치 파이프라인 수동 실행."""
    from core.drug_batch_scheduler import run_pipeline

    data = request.get_json(silent=True) or {}
    run_type = data.get("type", "daily_collect")
    source = data.get("source")
    limit = data.get("limit", 50)
    dry_run = data.get("dry_run", False)
    model = data.get("model", "werub-medical:7b")

    # 비동기 실행
    def _run():
        run_pipeline(run_type=run_type, trigger="api", source=source,
                     limit=limit, dry_run=dry_run, model=model)

    t = threading.Thread(target=_run, daemon=True, name="drug-batch-api")
    t.start()

    return jsonify({"ok": True, "message": f"Pipeline {run_type} started", "trigger": "api"})


@drug_batch_bp.route("/api/drug-batch/run-sync", methods=["POST"])
def batch_run_sync():
    """배치 파이프라인 동기 실행 (테스트용)."""
    from core.drug_batch_scheduler import run_pipeline

    data = request.get_json(silent=True) or {}
    result = run_pipeline(
        run_type=data.get("type", "daily_collect"),
        trigger="api-sync",
        source=data.get("source"),
        limit=data.get("limit", 50),
        dry_run=data.get("dry_run", False),
        model=data.get("model", "werub-medical:7b"),
    )
    return jsonify(result)


@drug_batch_bp.route("/api/drug-batch/stop", methods=["POST"])
def batch_stop():
    """진행 중 배치 중단 (running → cancelled)."""
    conn = _get_db()
    updated = conn.execute(
        "UPDATE batch_run SET status='cancelled', completed_at=datetime('now') WHERE status='running'"
    ).rowcount
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "cancelled": updated})


# ── Status & History ──────────────────────────

@drug_batch_bp.route("/api/drug-batch/status")
def batch_status():
    """현재 실행 상태 + DB 요약."""
    conn = _get_db()

    # 현재 실행 중
    current = conn.execute(
        "SELECT * FROM batch_run WHERE status='running' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    # 진행 카운터 (실행 중이면)
    progress = None
    if current:
        run_id = current["id"]
        counts = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM staging_drug
            WHERE batch_run_id=? GROUP BY status
        """, [run_id]).fetchall()
        progress = {r["status"]: r["cnt"] for r in counts}

    # 야간 모드 상태
    from core.drug_batch_scheduler import SCHEDULES, get_active_mode, NIGHT_MODE, _night_state
    active_mode = get_active_mode()

    # 다음 스케줄
    next_sched = None
    now = datetime.now()
    for name, sched in SCHEDULES.items():
        if sched["interval"] == "daily":
            next_sched = {"type": name, "hour": sched["hour"], "interval": "daily"}
            break

    # DB 요약
    drug_count = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
    inter_count = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    denial_count = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]

    conn.close()

    return jsonify({
        "current_run": dict(current) if current else None,
        "progress": progress,
        "next_scheduled": next_sched,
        "active_mode": active_mode,
        "db_summary": {
            "drug_codes": drug_count,
            "drug_interactions": inter_count,
            "denial_reasons": denial_count,
        },
    })


@drug_batch_bp.route("/api/drug-batch/history")
def batch_history():
    """실행 이력 목록."""
    conn = _get_db()
    limit = request.args.get("limit", 20, type=int)
    rows = conn.execute(
        "SELECT * FROM batch_run ORDER BY started_at DESC LIMIT ?", [limit]
    ).fetchall()
    conn.close()
    return jsonify({"runs": [dict(r) for r in rows]})


@drug_batch_bp.route("/api/drug-batch/history/<int:run_id>")
def batch_history_detail(run_id):
    """특정 실행 상세."""
    conn = _get_db()
    run = conn.execute("SELECT * FROM batch_run WHERE id=?", [run_id]).fetchone()
    if not run:
        conn.close()
        return jsonify({"error": "not found"}), 404

    staging = conn.execute(
        "SELECT id, source, source_tier, status, target_table, error, confidence, created_at FROM staging_drug WHERE batch_run_id=?",
        [run_id],
    ).fetchall()

    provenance = conn.execute(
        "SELECT * FROM data_provenance WHERE batch_run_id=? ORDER BY created_at",
        [run_id],
    ).fetchall()

    conn.close()
    return jsonify({
        "run": dict(run),
        "staging": [dict(r) for r in staging],
        "provenance": [dict(r) for r in provenance],
    })


# ── Staging Management ──────────────────────────

@drug_batch_bp.route("/api/drug-batch/staging")
def staging_list():
    """스테이징 데이터 조회."""
    conn = _get_db()
    status = request.args.get("status")
    source = request.args.get("source")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    where = []
    params = []
    if status:
        where.append("status=?")
        params.append(status)
    if source:
        where.append("source=?")
        params.append(source)

    where_clause = " AND ".join(where) if where else "1=1"
    offset = (page - 1) * per_page

    total = conn.execute(f"SELECT COUNT(*) FROM staging_drug WHERE {where_clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM staging_drug WHERE {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()
    conn.close()

    return jsonify({
        "staging": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    })


@drug_batch_bp.route("/api/drug-batch/staging/<int:staging_id>/approve", methods=["POST"])
def staging_approve(staging_id):
    """수동 승인 → drug_codes에 커밋."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM staging_drug WHERE id=?", [staging_id]).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404

    parsed = json.loads(row["parsed_data"]) if row["parsed_data"] else None
    if not parsed:
        conn.close()
        return jsonify({"error": "no parsed data"}), 400

    from core.drug_batch_scheduler import _commit_drug
    _commit_drug(conn, dict(row), parsed, row["batch_run_id"] or 0)
    conn.execute("UPDATE staging_drug SET status='committed' WHERE id=?", [staging_id])
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "code": parsed.get("code")})


@drug_batch_bp.route("/api/drug-batch/staging/<int:staging_id>/reject", methods=["POST"])
def staging_reject(staging_id):
    """수동 거부."""
    conn = _get_db()
    reason = (request.get_json(silent=True) or {}).get("reason", "manual reject")
    conn.execute("UPDATE staging_drug SET status='skipped', error=? WHERE id=?", [reason, staging_id])
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ── Stats & Schedule ──────────────────────────

@drug_batch_bp.route("/api/drug-batch/stats")
def batch_stats():
    """데이터 증가 통계."""
    conn = _get_db()
    days = request.args.get("days", 30, type=int)

    drug_count = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
    inter_count = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    denial_count = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]

    # 소스 분포
    source_dist = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM drug_codes GROUP BY source ORDER BY cnt DESC"
    ).fetchall()

    tier_dist = conn.execute(
        "SELECT source_tier, COUNT(*) as cnt FROM drug_codes GROUP BY source_tier ORDER BY cnt DESC"
    ).fetchall()

    avg_conf = conn.execute("SELECT AVG(confidence) FROM drug_codes").fetchone()[0] or 0

    # 최근 batch runs
    recent_runs = conn.execute(
        "SELECT id, run_type, status, total_committed, started_at FROM batch_run ORDER BY started_at DESC LIMIT 10"
    ).fetchall()

    # 신뢰도 분포
    conf_dist = {}
    for label, low, high in [("0.9+", 0.9, 2.0), ("0.8-0.9", 0.8, 0.9), ("0.7-0.8", 0.7, 0.8), ("<0.7", 0, 0.7)]:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM drug_codes WHERE confidence >= ? AND confidence < ?", [low, high]
        ).fetchone()[0]
        conf_dist[label] = cnt

    conn.close()

    return jsonify({
        "drug_codes": {"total": drug_count},
        "drug_interactions": {"total": inter_count},
        "denial_reasons": {"total": denial_count},
        "by_source": {r["source"]: r["cnt"] for r in source_dist},
        "by_tier": {r["source_tier"]: r["cnt"] for r in tier_dist},
        "avg_confidence": round(avg_conf, 4),
        "confidence_distribution": conf_dist,
        "recent_runs": [dict(r) for r in recent_runs],
    })


@drug_batch_bp.route("/api/drug-batch/schedule")
def get_schedule():
    from core.drug_batch_scheduler import SCHEDULES, NIGHT_MODE, DAY_MODE, get_active_mode
    return jsonify({
        "schedules": SCHEDULES,
        "night_mode": NIGHT_MODE,
        "day_mode": DAY_MODE,
        "active": get_active_mode(),
    })


@drug_batch_bp.route("/api/drug-batch/schedule", methods=["PUT"])
def update_schedule():
    from core.drug_batch_scheduler import SCHEDULES, NIGHT_MODE
    data = request.get_json(silent=True) or {}

    # 일반 스케줄 업데이트
    for name, sched in data.get("schedules", {}).items():
        if name in SCHEDULES:
            SCHEDULES[name].update(sched)

    # 야간 모드 설정 업데이트
    nm = data.get("night_mode")
    if nm:
        for k in ("enabled", "hours", "limit", "model", "priority", "batch_size",
                   "poll_timeout", "interval_minutes", "max_runs_per_night"):
            if k in nm:
                NIGHT_MODE[k] = nm[k]

    return jsonify({"ok": True, "schedules": SCHEDULES, "night_mode": NIGHT_MODE})


@drug_batch_bp.route("/api/drug-batch/night-mode", methods=["POST"])
def toggle_night_mode():
    """야간 모드 ON/OFF 토글."""
    from core.drug_batch_scheduler import NIGHT_MODE, get_active_mode
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        NIGHT_MODE["enabled"] = bool(data["enabled"])
    else:
        NIGHT_MODE["enabled"] = not NIGHT_MODE["enabled"]
    return jsonify({"ok": True, "night_mode_enabled": NIGHT_MODE["enabled"], "active": get_active_mode()})
