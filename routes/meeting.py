"""Meeting Analysis Blueprint — 화상회의 녹화본 AI 분석

hospital-jitsi 연동: recording.completed → STT → LLM 요약 → 콜백

  POST /api/meeting/analyze           — 분석 요청 접수
  GET  /api/meeting/job/<id>          — 작업 상태/결과 조회
  GET  /api/meeting/jobs              — 작업 목록
  POST /api/meeting/job/<id>/retry    — 실패 작업 재시도
  DELETE /api/meeting/job/<id>        — 작업 삭제
  GET  /api/meeting/status            — 시스템 상태
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from core.rate_limit import heavy_limit

from core.security import validate_webhook_url, validate_callback_url

logger = logging.getLogger("ollama-admin")

meeting_bp = Blueprint("meeting", __name__)

# ---------------------------------------------------------------------------
# State (injected by init_meeting)
# ---------------------------------------------------------------------------

_pipeline = None
_db_path = None


def init_meeting(*, base_dir, ollama_session=None, ollama_base=None):
    global _pipeline, _db_path
    from core.config import OLLAMA_BASE
    from core.meeting_pipeline import MeetingPipeline

    _db_path = os.path.join(base_dir, "meeting.db")
    tmp_dir = os.path.join(base_dir, "tmp", "meeting")
    _init_db()

    _pipeline = MeetingPipeline(
        db_path=_db_path,
        ollama_base=ollama_base or OLLAMA_BASE,
        ollama_session=ollama_session,
        tmp_dir=tmp_dir,
    )
    logger.info("Meeting Analysis Blueprint initialized")


def _get_db():
    conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")
    return conn


def _init_db():
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meeting_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            file_url TEXT NOT NULL,
            callback_url TEXT,
            options TEXT DEFAULT '{}',
            metadata TEXT DEFAULT '{}',
            transcript TEXT,
            segments TEXT,
            summary TEXT,
            action_items TEXT,
            highlights TEXT,
            speaker_segments TEXT,
            speakers_detected INTEGER DEFAULT 0,
            slides TEXT,
            korean_quality TEXT,
            error TEXT,
            error_stage TEXT,
            stt_duration_ms INTEGER DEFAULT 0,
            diarize_duration_ms INTEGER DEFAULT 0,
            vision_duration_ms INTEGER DEFAULT 0,
            llm_duration_ms INTEGER DEFAULT 0,
            total_duration_ms INTEGER DEFAULT 0,
            callback_sent INTEGER DEFAULT 0,
            callback_attempts INTEGER DEFAULT 0,
            callback_error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            submitted_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_jobs_status ON meeting_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_meeting_jobs_created ON meeting_jobs(created_at);
    """)
    # 기존 DB 마이그레이션(멱등) — 호출자별 접수 상한 판정용.
    # ⚠️ 인덱스는 **컬럼 추가 이후에** 만들어야 한다. CREATE TABLE 스크립트 안에 두면 기존
    # DB(컬럼 없음)에서 "no such column"으로 **부팅이 실패**한다(2026-07-29 실제 발생).
    try:
        conn.execute("ALTER TABLE meeting_jobs ADD COLUMN submitted_by TEXT")
    except sqlite3.OperationalError:
        pass
    # 용어 힌트 처리 결과(사용/누락 개수) — 무성 절단 금지(v2.124.63, edu 실측).
    try:
        conn.execute("ALTER TABLE meeting_jobs ADD COLUMN hint_report TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_meeting_jobs_submitter "
                     "ON meeting_jobs(submitted_by, status)")
    except sqlite3.OperationalError as e:
        logger.warning("submitted_by 인덱스 생성 skip: %s", e)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _job_to_dict(row, include_result=True):
    """Convert DB row to API response dict."""
    row = dict(row)  # sqlite3.Row → dict (for .get() support)
    d = {
        "id": row["id"],
        "status": row["status"],
        "metadata": json.loads(row["metadata"] or "{}"),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }

    if row["status"] == "completed" and include_result:
        d["result"] = {
            "transcript": {
                "full_text": row["transcript"] or "",
                "segments": json.loads(row["segments"] or "[]"),
                "word_count": len((row["transcript"] or "").split()),
            },
            "speaker_segments": json.loads(row["speaker_segments"] or "[]") if row.get("speaker_segments") else [],
            "speakers_detected": row.get("speakers_detected", 0),
            "slides": json.loads(row["slides"] or "[]") if row.get("slides") else [],
            "summary": row["summary"] or "",
            "action_items": json.loads(row["action_items"] or "[]"),
            "highlights": json.loads(row["highlights"] or "[]"),
            "korean_quality": json.loads(row["korean_quality"] or "{}"),
        }
        d["pipeline"] = {
            "stt_model": "faster-whisper/large-v3",
            "llm_model": "qwen3:8b",
            "stt_duration_ms": row["stt_duration_ms"],
            "diarize_duration_ms": row.get("diarize_duration_ms", 0),
            "vision_duration_ms": row.get("vision_duration_ms", 0),
            "llm_duration_ms": row["llm_duration_ms"],
            "total_duration_ms": row["total_duration_ms"],
        }
        d["callback_sent"] = bool(row["callback_sent"])

    elif row["status"] == "failed":
        d["error"] = row["error"]
        d["error_stage"] = row["error_stage"]

    elif row["status"] in ("downloading", "transcribing", "diarizing", "analyzing_vision", "analyzing"):
        stage_map = {"downloading": "1/5", "transcribing": "2/5", "diarizing": "3/5",
                     "analyzing_vision": "4/5", "analyzing": "5/5"}
        d["progress"] = f"처리 중 ({stage_map.get(row['status'], '?')} 단계)"

    return d


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@meeting_bp.route("/api/meeting/analyze", methods=["POST"])
@heavy_limit("10/minute")
def meeting_analyze():
    """분석 요청 접수
    ---
    tags: [Meeting Analysis]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [file_url]
          properties:
            file_url: {type: string, description: "S3 Presigned URL (MP4)"}
            callback_url: {type: string, description: "분석 완료 콜백 URL"}
            options: {type: object, description: "분석 옵션"}
            metadata: {type: object, description: "회의 메타데이터"}
    responses:
      202: {description: "작업 접수됨"}
      400: {description: "file_url 누락"}
      503: {description: "동시 작업 한도 초과"}
    """
    data = request.get_json(force=True, silent=True) or {}
    file_url = data.get("file_url", "").strip()
    if not file_url:
        return jsonify({"error": "file_url is required"}), 400

    # SSRF/로컬파일 read 차단: file_url은 공인 http(s)만 (정상 사용 = S3 presigned https).
    # file://·절대경로·내부망/메타데이터(169.254) IP 차단.
    if not validate_webhook_url(file_url):
        return jsonify({"error": "file_url must be a public http(s) URL (file://·내부망 차단)"}), 400

    # 호출자 식별(키 이름) — 전체 상한과 **키당 상한**을 함께 본다(한 병원이 큐를 독점하면
    # 다른 병원이 막힌다는 edu 제보 반영).
    from flask import g
    submitted_by = getattr(g, "act_key_name", None)
    ok, reason = _pipeline.can_accept(submitted_by)
    if not ok:
        return jsonify({"error": f"Service busy — {reason}", "retry_after": 300}), 503

    callback_url = data.get("callback_url", "").strip() or None
    # 콜백은 내부 서비스(hospital-jitsi) 허용 → 스킴만 검증(file:// 등 비-HTTP 차단).
    if callback_url and not validate_callback_url(callback_url):
        return jsonify({"error": "callback_url must be an http(s) URL"}), 400
    options = data.get("options", {})
    metadata = data.get("metadata", {})
    if not isinstance(options, dict):
        return jsonify({"error": "options must be an object"}), 400
    # language 접수 시점 검증(X7): 종전엔 무검증 통과 → 다운로드·오디오 추출을 다 마친
    # transcribe 단계에서야 ValueError로 실패했다. "auto"는 자동 감지로 정규화된다.
    from core.meeting_stt import normalize_language
    _, _lang_err = normalize_language(options.get("language"))
    if _lang_err:
        return jsonify({"error": f"options.language: {_lang_err}"}), 400

    job_id = _pipeline.submit(file_url, callback_url, options, metadata,
                              submitted_by=submitted_by)

    # 예상 시간: 회의 길이의 1/4 (기본 15분)
    duration_sec = metadata.get("duration_sec", 3600)
    est_min = max(5, duration_sec // 240)

    return jsonify({
        "job_id": job_id,
        "status": "pending",
        "estimated_minutes": est_min,
        "poll_url": f"/api/meeting/job/{job_id}",
    }), 202


@meeting_bp.route("/api/meeting/job/<job_id>", methods=["GET"])
def meeting_job(job_id):
    """작업 상태/결과 조회
    ---
    tags: [Meeting Analysis]
    parameters:
      - in: path
        name: job_id
        type: string
        required: true
    responses:
      200: {description: "작업 상태/결과"}
      404: {description: "작업 없음"}
    """
    conn = _get_db()
    row = conn.execute("SELECT * FROM meeting_jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_job_to_dict(row))


@meeting_bp.route("/api/meeting/jobs", methods=["GET"])
def meeting_jobs():
    """작업 목록 (최근 50건)
    ---
    tags: [Meeting Analysis]
    parameters:
      - in: query
        name: limit
        type: integer
        default: 50
      - in: query
        name: status
        type: string
    responses:
      200: {description: "작업 목록"}
    """
    limit = min(int(request.args.get("limit", 50)), 200)
    status_filter = request.args.get("status", "").strip()

    conn = _get_db()
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM meeting_jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status_filter, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM meeting_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM meeting_jobs").fetchone()[0]
    conn.close()

    return jsonify({
        "jobs": [_job_to_dict(r, include_result=False) for r in rows],
        "total": total,
    })


@meeting_bp.route("/api/meeting/job/<job_id>/retry", methods=["POST"])
def meeting_retry(job_id):
    """실패 작업 재시도
    ---
    tags: [Meeting Analysis]
    parameters:
      - in: path
        name: job_id
        type: string
        required: true
    responses:
      200: {description: "재시도 시작됨"}
      400: {description: "재시도 불가 (failed 상태가 아님)"}
      404: {description: "작업 없음"}
    """
    conn = _get_db()
    row = conn.execute("SELECT status FROM meeting_jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Job not found"}), 404
    if row["status"] != "failed":
        return jsonify({"error": f"Cannot retry job in '{row['status']}' status"}), 400

    _pipeline.retry(job_id)
    return jsonify({"ok": True, "job_id": job_id, "status": "pending"})


@meeting_bp.route("/api/meeting/job/<job_id>", methods=["DELETE"])
def meeting_delete(job_id):
    """작업 삭제
    ---
    tags: [Meeting Analysis]
    parameters:
      - in: path
        name: job_id
        type: string
        required: true
    responses:
      200: {description: "삭제됨"}
      400: {description: "진행 중 작업 삭제 불가"}
      404: {description: "작업 없음"}
    """
    conn = _get_db()
    row = conn.execute("SELECT status FROM meeting_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Job not found"}), 404
    if row["status"] in ("downloading", "transcribing", "analyzing"):
        conn.close()
        return jsonify({"error": "Cannot delete active job"}), 400

    conn.execute("DELETE FROM meeting_jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@meeting_bp.route("/api/meeting/status", methods=["GET"])
def meeting_status():
    """시스템 상태
    ---
    tags: [Meeting Analysis]
    responses:
      200: {description: "시스템 상태"}
    """
    from core.meeting_stt import is_available as whisper_available, ffmpeg_available

    conn = _get_db()
    active = conn.execute(
        "SELECT COUNT(*) FROM meeting_jobs WHERE status IN ('downloading','transcribing','analyzing')"
    ).fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM meeting_jobs WHERE status='completed'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM meeting_jobs WHERE status='failed'").fetchone()[0]
    conn.close()

    from core.meeting_diarize import is_available as diarize_available
    from core import meeting_stt as meeting_stt_mod
    from core.meeting_stt import WHISPER_MODEL_SIZE
    from core.meeting_pipeline import (LLM_MODEL, MAX_CONCURRENT, MAX_QUEUE,
                                       MAX_QUEUE_PER_KEY, QUEUE_WAIT_S)

    return jsonify({
        "service": "meeting-analysis",
        "whisper_available": whisper_available(),
        "whisper_model": WHISPER_MODEL_SIZE,
        # 처리량 레버 현재 상태(무재배포 토글) — A/B 시 어떤 설정으로 돌았는지 식별용.
        "stt_batched": meeting_stt_mod.stt_batched(),
        "stt_batch_size": meeting_stt_mod.stt_batch_size(),
        "whisper_compute": meeting_stt_mod._resolve_device_compute()[1],
        "llm_model": LLM_MODEL,
        "ffmpeg_available": ffmpeg_available(),
        # 화자분리는 옵트인(options.diarize) — 호출자가 가용 여부를 먼저 확인할 수 있도록 노출.
        "diarize_available": diarize_available(),
        "supported_options": ["diarize", "num_speakers", "max_speakers", "vision",
                              "keyframe_interval", "language", "initial_prompt",
                              "vocabulary", "hotwords"],
        "active_jobs": active,
        "max_concurrent": MAX_CONCURRENT,
        "queued_jobs": max(0, _pipeline.backlog() - active) if _pipeline else 0,
        "max_queue": MAX_QUEUE,
        "max_queue_per_caller": MAX_QUEUE_PER_KEY,
        "queue_wait_s": QUEUE_WAIT_S,
        "total_completed": completed,
        "total_failed": failed,
    })
