"""Data Provenance API — 출처 추적 조회

Blueprint: provenance_bp
URL prefix: /api/provenance
"""
import logging
import os
import sqlite3

from flask import Blueprint, jsonify, request

provenance_bp = Blueprint("provenance", __name__)
logger = logging.getLogger("ollama-admin")


def _get_db() -> sqlite3.Connection:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clinic_codes.db"
    )
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── Record-Level Provenance ──────────────────────────

@provenance_bp.route("/api/provenance/<table>/<key>")
def record_provenance(table, key):
    """특정 레코드의 전체 출처 이력."""
    if table not in ("drug_codes", "drug_interactions", "denial_reasons"):
        return jsonify({"error": "invalid table"}), 400

    conn = _get_db()

    # 현재 레코드
    current = None
    if table == "drug_codes":
        row = conn.execute("SELECT * FROM drug_codes WHERE code=?", [key]).fetchone()
        if row:
            current = dict(row)

    # 이력
    history = conn.execute(
        """SELECT * FROM data_provenance
           WHERE table_name=? AND record_key=?
           ORDER BY created_at DESC""",
        [table, key],
    ).fetchall()

    conn.close()

    if not current and not history:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "table": table,
        "record_key": key,
        "current": current,
        "history": [dict(r) for r in history],
        "total_changes": len(history),
    })


@provenance_bp.route("/api/provenance/<table>/<key>/timeline")
def record_timeline(table, key):
    """타임라인 형태 (UI 최적화)."""
    if table not in ("drug_codes", "drug_interactions", "denial_reasons"):
        return jsonify({"error": "invalid table"}), 400

    conn = _get_db()

    # 현재 레코드 요약
    current = None
    if table == "drug_codes":
        row = conn.execute(
            "SELECT code, name_ko, name_generic, source, source_tier, source_url, confidence, updated_at FROM drug_codes WHERE code=?",
            [key],
        ).fetchone()
        if row:
            current = dict(row)

    # 이력 → 타임라인 이벤트
    rows = conn.execute(
        """SELECT action, field, old_value, new_value, source, source_tier,
                  source_url, batch_run_id, actor, confidence, created_at
           FROM data_provenance
           WHERE table_name=? AND record_key=?
           ORDER BY created_at DESC""",
        [table, key],
    ).fetchall()

    timeline = []
    for r in rows:
        rd = dict(r)
        summary = _build_summary(rd)
        timeline.append({
            "at": rd["created_at"],
            "action": rd["action"],
            "source": rd["source"],
            "source_tier": rd["source_tier"],
            "actor": rd["actor"],
            "batch_run_id": rd["batch_run_id"],
            "field": rd["field"],
            "old_value": rd["old_value"],
            "new_value": rd["new_value"],
            "confidence": rd["confidence"],
            "summary": summary,
        })

    conn.close()

    return jsonify({
        "table": table,
        "record_key": key,
        "current": current,
        "timeline": timeline,
    })


# ── Global Provenance ──────────────────────────

@provenance_bp.route("/api/provenance/summary")
def provenance_summary():
    """전체 출처 분포 통계."""
    conn = _get_db()

    # drug_codes 통계
    drug_total = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
    drug_by_tier = conn.execute(
        "SELECT source_tier, COUNT(*) as cnt FROM drug_codes GROUP BY source_tier ORDER BY cnt DESC"
    ).fetchall()
    drug_by_source = conn.execute(
        "SELECT source, COUNT(*) as cnt, MIN(source_url) as url FROM drug_codes GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    avg_conf = conn.execute("SELECT AVG(confidence) FROM drug_codes").fetchone()[0] or 0

    # confidence distribution
    conf_dist = {}
    for label, low, high in [("0.9+", 0.9, 2.0), ("0.8-0.9", 0.8, 0.9), ("0.7-0.8", 0.7, 0.8), ("<0.7", 0, 0.7)]:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM drug_codes WHERE confidence >= ? AND confidence < ?", [low, high]
        ).fetchone()[0]
        conf_dist[label] = cnt

    # drug_interactions 통계
    inter_total = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    inter_by_source = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM drug_interactions GROUP BY source ORDER BY cnt DESC"
    ).fetchall()

    # denial_reasons 통계
    denial_total = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]
    denial_by_source = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM denial_reasons GROUP BY source ORDER BY cnt DESC"
    ).fetchall()

    # 최근 batch_run
    recent = conn.execute(
        "SELECT id, run_type, status, total_committed, started_at FROM batch_run ORDER BY started_at DESC LIMIT 5"
    ).fetchall()

    conn.close()

    tier_labels = {"T1": "Authoritative", "T2": "Evidence-Based", "T3": "Curated", "T4": "AI-Generated"}

    return jsonify({
        "drug_codes": {
            "total": drug_total,
            "by_tier": {
                r["source_tier"]: {"count": r["cnt"], "pct": round(r["cnt"] / max(drug_total, 1) * 100, 1),
                                   "label": tier_labels.get(r["source_tier"], "")}
                for r in drug_by_tier
            },
            "by_source": {r["source"]: {"count": r["cnt"], "url": r["url"]} for r in drug_by_source},
            "avg_confidence": round(avg_conf, 4),
            "confidence_distribution": conf_dist,
        },
        "drug_interactions": {
            "total": inter_total,
            "by_source": {(r["source"] or "unset"): r["cnt"] for r in inter_by_source},
        },
        "denial_reasons": {
            "total": denial_total,
            "by_source": {(r["source"] or "unset"): r["cnt"] for r in denial_by_source},
        },
        "recent_batch_runs": [dict(r) for r in recent],
    })


@provenance_bp.route("/api/provenance/recent")
def provenance_recent():
    """최근 변경 이력."""
    conn = _get_db()
    limit = request.args.get("limit", 50, type=int)
    rows = conn.execute(
        "SELECT * FROM data_provenance ORDER BY created_at DESC LIMIT ?", [limit]
    ).fetchall()
    conn.close()
    return jsonify({"changes": [dict(r) for r in rows], "total": len(rows)})


@provenance_bp.route("/api/provenance/search")
def provenance_search():
    """조건부 검색."""
    conn = _get_db()
    source = request.args.get("source")
    action = request.args.get("action")
    table = request.args.get("table")
    limit = request.args.get("limit", 50, type=int)

    where = []
    params = []
    if source:
        where.append("source=?")
        params.append(source)
    if action:
        where.append("action=?")
        params.append(action)
    if table:
        where.append("table_name=?")
        params.append(table)

    where_clause = " AND ".join(where) if where else "1=1"
    rows = conn.execute(
        f"SELECT * FROM data_provenance WHERE {where_clause} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()

    return jsonify({"results": [dict(r) for r in rows], "total": len(rows)})


@provenance_bp.route("/api/provenance/batch/<int:run_id>")
def provenance_by_batch(run_id):
    """특정 배치의 전체 이력."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM data_provenance WHERE batch_run_id=? ORDER BY created_at",
        [run_id],
    ).fetchall()
    conn.close()
    return jsonify({"batch_run_id": run_id, "changes": [dict(r) for r in rows]})


# ── Helpers ──────────────────────────

def _build_summary(row: dict) -> str:
    action = row.get("action", "")
    source = row.get("source", "")
    field = row.get("field")

    if action == "CREATE":
        return f"{source}에서 초기 생성"
    elif action == "UPDATE" and field:
        return f"{source} → {field} 갱신"
    elif action == "ENRICH":
        return f"{source}로 데이터 보강" + (f" ({field})" if field else "")
    elif action == "VERIFY":
        return "검증 통과"
    elif action == "DEACTIVATE":
        return "비활성화"
    return f"{action} by {source}"
