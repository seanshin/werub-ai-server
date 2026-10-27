"""Counseling Orchestration Blueprint — 상담 오케스트레이션 API

mindwell(상담 전문) + qwen2.5(한국어 자연화) 2단계 파이프라인:

  POST /api/counseling/chat            — 상담 대화 (오케스트레이션)
  POST /api/counseling/chat/stream     — 상담 대화 (SSE 스트리밍)
  GET  /api/counseling/sessions        — 세션 목록
  GET  /api/counseling/session/<id>    — 세션 상세 (대화 이력)
  DELETE /api/counseling/session/<id>  — 세션 삭제
  GET  /api/counseling/status          — 상담 시스템 상태
"""
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, request, jsonify, Response
from core.rate_limit import heavy_limit
from core.llm_resilience import LLMBusyError, busy_payload

from core.config import OLLAMA_BASE
from core.korean import force_korean, korean_quality_check
from core.korean_humanize import (
    compute_korean_quality,
    get_quality_summary,
    get_s1_prompt_rules,
)

logger = logging.getLogger("ollama-admin")

counseling_bp = Blueprint("counseling", __name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_db_path = None
_ollama_session = None
_OLLAMA_BASE = None

COUNSELOR_MODEL = "ALIENTELLIGENCE/mindwell"
KOREAN_MODEL = "qwen3:8b"
MAX_HISTORY_TURNS = 10
SESSION_TTL_SEC = 86400  # 24시간

# ⚠️ 첫 줄이 정체성을 정한다. 종전 "professional ... counselor"가 모델에게 자격을 부여해
#    "저는 전문 상담사입니다"·"저는 전문 치료사입니다"를 낳았다(2026-08-14 실측 5/50=10.0%,
#    5건 전부 이 1단계 모델 원문에 존재 — 한국어 자연화가 만든 것이 아니다).
#    그 정체성을 둔 채 "자격을 주장하지 말라"만 얹으면 지시끼리 싸우므로 정체성을 되돌린다.
#    ⚠️ 함께 넣은 "매 응답마다 AI라고 말하지 말 것"이 없으면 과교정으로 뒤집힌다.
COUNSELOR_SYSTEM_PROMPT = """You are a compassionate Korean-speaking counseling assistant.
You are an AI. You hold no professional license or credential.
Your approach combines CBT (Cognitive Behavioral Therapy) and mindfulness techniques.

Rules:
- ALWAYS respond in Korean (한국어).
- NEVER claim a credential for yourself. Do not call yourself 의사, 치료사, 치료자,
  상담사, 상담원, 전문의, or 전문가. If the user asks who you are, say you are an AI
  that listens and helps organize feelings.
- Do not repeat that you are an AI in every reply. Say it only when the user asks about
  your role or requests a diagnosis.
- Use warm, empathetic language. Avoid clinical or cold tone.
- Ask open-ended questions to understand the user's feelings.
- Never diagnose medical conditions. Recommend professional help when appropriate.
- Keep responses concise (3-5 paragraphs max).
- Use 존댓말 (formal polite speech) consistently.
- Acknowledge emotions before offering suggestions.
- Do NOT start with greetings like "안녕하세요" after the first message.
- If the user expresses suicidal thoughts or self-harm, strongly recommend contacting a crisis hotline (자살예방상담전화 1393, 정신건강위기상담전화 1577-0199)."""

KOREAN_REFINE_PROMPT = """당신은 한국어 교정 전문가입니다.
아래 상담사 응답의 한국어를 자연스럽게 다듬어 주세요.

규칙:
- 상담 내용과 의미는 절대 변경하지 마세요.
- 어색한 한국어 표현만 자연스럽게 교정하세요.
- 존댓말(합쇼체/해요체)을 일관되게 유지하세요.
- 번역투 표현을 자연스러운 한국어로 바꾸세요.
- 원문이 이미 자연스러우면 그대로 출력하세요.
- 교정된 텍스트만 출력하세요. 설명이나 주석은 불필요합니다."""

# 위기 키워드 (자해/자살 감지)
_CRISIS_KEYWORDS = re.compile(
    r"(자살|죽고\s*싶|죽을래|살기\s*싫|목숨|자해|손목|극단적|삶을?\s*끝)"
)

_CRISIS_RESPONSE_SUFFIX = (
    "\n\n---\n"
    "지금 많이 힘드시군요. 혼자 감당하지 않으셔도 됩니다.\n"
    "- 자살예방상담전화: 1393 (24시간)\n"
    "- 정신건강위기상담전화: 1577-0199 (24시간)\n"
    "- 생명의전화: 1588-9191\n"
    "전문 상담사가 언제든 도움을 드릴 수 있습니다."
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_counseling(*, base_dir, ollama_session=None, ollama_base=None):
    global _db_path, _ollama_session, _OLLAMA_BASE
    _db_path = os.path.join(base_dir, "counseling.db")
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base or OLLAMA_BASE
    _init_db()


def _get_db():
    conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS counseling_sessions (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS counseling_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            raw_counselor TEXT,
            quality_score REAL,
            korean_ratio REAL,
            model_used TEXT,
            duration_ms INTEGER,
            is_crisis INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES counseling_sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cm_session ON counseling_messages(session_id);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Ollama helpers (서킷 브레이커 + 적응형 타임아웃)
# ---------------------------------------------------------------------------

def _get_session():
    """Ollama 세션 반환 (init에서 주입된 세션 또는 새로 생성)"""
    return _ollama_session or requests.Session(), _OLLAMA_BASE or OLLAMA_BASE


def _task_for(model):
    """모델 → 라우팅 task. 상담 파이프라인은 2단계이고 **정책은 같다**(local_only).

    ⚠️ 모델 선택은 이관하지 않았다(이관 1단계 규율) — 호출자가 고른 모델을 그대로 쓰고,
    task는 **정책 강제와 관측**을 위해 붙인다. 미지의 모델은 상담 본단계로 본다(보수적).
    """
    return "counseling.refine" if model == KOREAN_MODEL else "counseling.counselor"


def _router_adapter(sess, base):
    """🔴 호출자의 세션·base를 그대로 넘긴다 — 어댑터가 자기 세션을 만들면 주입 세션과
    커넥션 풀을 우회한다(2026-08-10 의료법에서 Fair Queue를 통째로 건너뛴 그 결함)."""
    from core import model_router
    return model_router, model_router.OllamaAdapter(base=base, session=sess)


def _call_ollama_chat(model, messages, action="counseling"):
    """Ollama chat API 호출 (non-streaming, 서킷 브레이커 적용).

    v2.124.98 — task 라우터 경유(Phase 1-d 4단계).
    ⚠️ **서킷 브레이커·적응형 타임아웃은 seam 위에 남는다** — 복원력은 도메인 정책이고
       라우터는 "어느 모델에 어떻게 보내는가"만 안다.
    """
    from core.llm_resilience import get_adaptive_timeout, ollama_breaker

    if not ollama_breaker.can_execute():
        raise RuntimeError("Ollama temporarily unavailable (circuit open)")

    sess, base = _get_session()
    timeout = get_adaptive_timeout(model, action, default=120)

    try:
        mr, adapter = _router_adapter(sess, base)
        r = mr.chat(_task_for(model), messages, model=model, timeout=timeout,
                    adapter=adapter)
        if not r.ok:
            # 종전 `raise_for_status()`와 같은 층 — HTTP 오류를 빈 문자열로 흘리지 않는다.
            from core.llm_resilience import result_error
            raise result_error(r, "chat failed")
        ollama_breaker.record_success()
        return r.text
    except Exception as e:
        # v2.125.68: 대기열 거절(busy)은 공용 브레이커 실패로 세지 않는다(core/llm_resilience.LLMBusyError)
        from core.llm_resilience import record_failure_unless_busy
        record_failure_unless_busy(e, ollama_breaker)
        logger.error(f"Ollama call failed ({model}): {e}")
        raise


def _call_ollama_chat_stream(model, messages, action="counseling"):
    """Ollama chat API 호출 (streaming) — yields (token, is_done).

    ⚠️ 마지막 yield는 토큰이 아니라 **누적 전문**이다(기존 계약 — 호출자가 그렇게 읽는다).
    """
    from core.llm_resilience import get_adaptive_timeout, ollama_breaker

    if not ollama_breaker.can_execute():
        raise RuntimeError("Ollama temporarily unavailable (circuit open)")

    sess, base = _get_session()
    timeout = get_adaptive_timeout(model, action, default=120)

    try:
        mr, adapter = _router_adapter(sess, base)
        ollama_breaker.record_success()

        full_text = []
        for chunk in mr.chat_stream(_task_for(model), messages, model=model,
                                    timeout=timeout, adapter=adapter):
            if chunk.text:
                full_text.append(chunk.text)
                yield chunk.text, False
            if chunk.done:
                yield "".join(full_text), True
    except Exception as e:
        # v2.125.68: 대기열 거절(busy)은 공용 브레이커 실패로 세지 않는다(core/llm_resilience.LLMBusyError)
        from core.llm_resilience import record_failure_unless_busy
        record_failure_unless_busy(e, ollama_breaker)
        logger.error(f"Ollama stream failed ({model}): {e}")
        raise


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _get_or_create_session(session_id=None):
    """세션 조회 또는 생성 (TTL 만료 시 새 세션)"""
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    if session_id:
        row = conn.execute(
            "SELECT id, updated_at FROM counseling_sessions WHERE id = ? AND status = 'active'",
            (session_id,),
        ).fetchone()
        if row:
            # TTL 체크
            try:
                updated = datetime.fromisoformat(row["updated_at"])
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if age > SESSION_TTL_SEC:
                    conn.execute(
                        "UPDATE counseling_sessions SET status = 'expired' WHERE id = ?",
                        (session_id,),
                    )
                    conn.commit()
                    logger.info(f"Session {session_id} expired (age={age:.0f}s)")
                else:
                    conn.close()
                    return session_id
            except Exception:
                conn.close()
                return session_id

    new_id = str(uuid.uuid4())[:12]
    conn.execute(
        "INSERT INTO counseling_sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
        (new_id, now, now),
    )
    conn.commit()
    conn.close()
    return new_id


def _get_history(session_id, limit=None):
    """세션 대화 이력 조회 (최근 N턴)"""
    limit = limit or MAX_HISTORY_TURNS * 2
    conn = _get_db()
    rows = conn.execute(
        "SELECT role, content FROM counseling_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _save_message(session_id, role, content, *,
                  raw_counselor=None, quality_score=None, korean_ratio=None,
                  model_used=None, duration_ms=None, is_crisis=False):
    """메시지 저장"""
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO counseling_messages
           (session_id, role, content, raw_counselor, quality_score, korean_ratio,
            model_used, duration_ms, is_crisis, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, role, content, raw_counselor, quality_score, korean_ratio,
         model_used, duration_ms, 1 if is_crisis else 0, now),
    )
    conn.execute(
        """UPDATE counseling_sessions
           SET updated_at = ?, message_count = message_count + 1,
               title = CASE WHEN title = '' AND ? = 'user'
                       THEN substr(?, 1, 50) ELSE title END
           WHERE id = ?""",
        (now, role, content, session_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Orchestration pipeline
# ---------------------------------------------------------------------------

def _detect_crisis(text):
    """위기 키워드 감지"""
    return bool(_CRISIS_KEYWORDS.search(text))


def _build_counselor_messages(user_message, history):
    """mindwell용 메시지 구성 (시스템 + 이력 + 현재)"""
    messages = [{"role": "system", "content": COUNSELOR_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def _refine_korean(raw_text):
    """qwen2.5로 한국어 자연화"""
    messages = [
        {"role": "system", "content": KOREAN_REFINE_PROMPT},
        {"role": "user", "content": raw_text},
    ]
    return _call_ollama_chat(KOREAN_MODEL, messages, action="translate")


def _orchestrate(user_message, session_id):
    """2단계 오케스트레이션: mindwell → qwen2.5 자연화 → 품질 검증"""
    t0 = time.time()

    is_crisis = _detect_crisis(user_message)

    # 이력 로드
    history = _get_history(session_id)

    # Stage 1: mindwell 상담 응답
    counselor_msgs = _build_counselor_messages(user_message, history)
    raw_response = _call_ollama_chat(COUNSELOR_MODEL, counselor_msgs)

    # Stage 2: qwen2.5 한국어 자연화
    refined_response = _refine_korean(raw_response)
    refined_response = force_korean(refined_response)

    # Stage 3: 품질 검증
    is_valid, korean_ratio, _status = korean_quality_check(refined_response)
    quality = compute_korean_quality(refined_response, genre="essay")
    quality_summary = get_quality_summary(quality)
    risk_band = quality_summary.get("risk_band", "unknown")

    # risk_band가 high면 S1 규칙 주입 후 재자연화
    if risk_band == "high":
        enhanced_prompt = KOREAN_REFINE_PROMPT + "\n\n" + get_s1_prompt_rules()
        messages = [
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": raw_response},
        ]
        refined_response = _call_ollama_chat(KOREAN_MODEL, messages, action="translate")
        refined_response = force_korean(refined_response)
        is_valid, korean_ratio, _status = korean_quality_check(refined_response)
        quality = compute_korean_quality(refined_response, genre="essay")
        quality_summary = get_quality_summary(quality)
        quality_summary["retry_count"] = 1
    else:
        quality_summary["retry_count"] = 0

    # 위기 감지 시 안전 정보 추가
    if is_crisis:
        refined_response += _CRISIS_RESPONSE_SUFFIX

    duration_ms = int((time.time() - t0) * 1000)

    # 메시지 저장
    _save_message(session_id, "user", user_message, is_crisis=is_crisis)
    _save_message(
        session_id, "assistant", refined_response,
        raw_counselor=raw_response,
        quality_score=quality_summary.get("risk_score"),
        korean_ratio=korean_ratio,
        model_used=f"{COUNSELOR_MODEL}+{KOREAN_MODEL}",
        duration_ms=duration_ms,
        is_crisis=is_crisis,
    )

    return {
        "message": refined_response,
        "session_id": session_id,
        "is_crisis": is_crisis,
        "pipeline": {
            "counselor_model": COUNSELOR_MODEL,
            "refine_model": KOREAN_MODEL,
            "raw_response": raw_response,
            "korean_ratio": round(korean_ratio, 3),
            "quality": quality_summary,
        },
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@counseling_bp.route("/api/counseling/chat", methods=["POST"])
@heavy_limit("20/minute")
def counseling_chat():
    """상담 대화 (오케스트레이션)

    2단계 오케스트레이션 — 상담 전문 모델(mindwell)이 낸 답을 한국어 자연화 모델이 다듬고,
    한국어 필터·품질 검증을 거쳐 응답한다. 응답의 `pipeline`에 원본·한국어 비율·위험밴드가 실린다.
    🔴 **안전장치**: 위기 키워드가 감지되면 `is_crisis: true`와 함께 자살예방상담전화(1393)를
    자동 안내한다. 연동 화면은 이 플래그를 반드시 처리할 것.
    스트리밍이 필요하면 `POST /api/counseling/chat/stream`(NDJSON).
    ---
    tags: [Counseling]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string, description: "사용자 메시지"}
            session_id: {type: string, description: "세션 ID (없으면 자동 생성)"}
    responses:
      200:
        description: 상담 응답
    """
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    session_id = _get_or_create_session(data.get("session_id"))

    try:
        result = _orchestrate(message, session_id)
        return jsonify(result)
    except LLMBusyError as e:
        # v2.125.68: 대기열 거절은 '불안정'(503)이 아니라 '잠시 후 다시'(429 + Retry-After) — 아래 RuntimeError보다 먼저
        body, headers = busy_payload(e)
        return jsonify(body), 429, headers
    except RuntimeError as e:
        # 서킷 브레이커 open
        logger.warning(f"Counseling circuit open: {e}")
        return jsonify({"error": "서비스가 일시적으로 불안정합니다. 잠시 후 다시 시도해 주세요."}), 503
    except Exception as e:
        logger.error(f"Counseling chat error: {e}")
        return jsonify({"error": str(e)[:200]}), 500


@counseling_bp.route("/api/counseling/chat/stream", methods=["POST"])
@heavy_limit("20/minute")
def counseling_chat_stream():
    """상담 대화 SSE 스트리밍
    ---
    tags: [Counseling]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string}
            session_id: {type: string}
    responses:
      200:
        description: SSE 스트리밍 응답 (NDJSON)
    """
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    session_id = _get_or_create_session(data.get("session_id"))

    def generate():
        t0 = time.time()
        is_crisis = _detect_crisis(message)

        try:
            # Stage 1: mindwell 상담 응답 (non-streaming — 자연화 필요)
            history = _get_history(session_id)
            counselor_msgs = _build_counselor_messages(message, history)

            yield json.dumps({"event": "stage", "stage": "counseling", "status": "generating"}) + "\n"
            raw_response = _call_ollama_chat(COUNSELOR_MODEL, counselor_msgs)

            # Stage 2: qwen2.5 한국어 자연화 (streaming)
            yield json.dumps({"event": "stage", "stage": "refining", "status": "generating"}) + "\n"
            refine_msgs = [
                {"role": "system", "content": KOREAN_REFINE_PROMPT},
                {"role": "user", "content": raw_response},
            ]

            refined_text = ""
            for token, is_done in _call_ollama_chat_stream(KOREAN_MODEL, refine_msgs, action="translate"):
                if is_done:
                    refined_text = token
                else:
                    yield json.dumps({"event": "token", "token": token}) + "\n"

            refined_text = force_korean(refined_text)

            # 위기 감지 시 안전 정보 추가
            if is_crisis:
                crisis_suffix = _CRISIS_RESPONSE_SUFFIX
                refined_text += crisis_suffix
                yield json.dumps({"event": "token", "token": crisis_suffix}) + "\n"

            # Stage 3: 품질 검증
            is_valid, korean_ratio, _status = korean_quality_check(refined_text)
            quality = compute_korean_quality(refined_text, genre="essay")
            quality_summary = get_quality_summary(quality)

            duration_ms = int((time.time() - t0) * 1000)

            # 저장
            _save_message(session_id, "user", message, is_crisis=is_crisis)
            _save_message(
                session_id, "assistant", refined_text,
                raw_counselor=raw_response,
                quality_score=quality_summary.get("risk_score"),
                korean_ratio=korean_ratio,
                model_used=f"{COUNSELOR_MODEL}+{KOREAN_MODEL}",
                duration_ms=duration_ms,
                is_crisis=is_crisis,
            )

            yield json.dumps({
                "event": "done",
                "session_id": session_id,
                "is_crisis": is_crisis,
                "korean_ratio": round(korean_ratio, 3),
                "quality": quality_summary,
                "duration_ms": duration_ms,
            }) + "\n"

        except Exception as e:
            logger.error(f"Counseling stream error: {e}")
            yield json.dumps({"event": "error", "message": str(e)[:200]}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@counseling_bp.route("/api/counseling/sessions", methods=["GET"])
def counseling_sessions():
    """세션 목록
    ---
    tags: [Counseling]
    parameters:
      - in: query
        name: limit
        type: integer
        default: 20
      - in: query
        name: status
        type: string
        default: active
    responses:
      200:
        description: 세션 목록
        schema:
          type: object
          properties:
            sessions: {type: array}
    """
    limit = request.args.get("limit", 20, type=int)
    status_filter = request.args.get("status", "active")
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM counseling_sessions WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
        (status_filter, limit),
    ).fetchall()
    conn.close()
    return jsonify({"sessions": [dict(r) for r in rows]})


@counseling_bp.route("/api/counseling/session/<session_id>", methods=["GET"])
def counseling_session_detail(session_id):
    """세션 상세 (대화 이력)
    ---
    tags: [Counseling]
    parameters:
      - in: path
        name: session_id
        type: string
        required: true
    responses:
      200:
        description: 세션 상세
        schema:
          type: object
          properties:
            session: {type: object}
            messages: {type: array}
    """
    conn = _get_db()
    session = conn.execute("SELECT * FROM counseling_sessions WHERE id = ?", (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({"error": "session not found"}), 404

    messages = conn.execute(
        """SELECT id, role, content, raw_counselor, quality_score, korean_ratio,
                  model_used, duration_ms, is_crisis, created_at
           FROM counseling_messages WHERE session_id = ? ORDER BY id""",
        (session_id,),
    ).fetchall()
    conn.close()

    return jsonify({
        "session": dict(session),
        "messages": [dict(m) for m in messages],
    })


@counseling_bp.route("/api/counseling/session/<session_id>", methods=["DELETE"])
def counseling_session_delete(session_id):
    """세션 삭제
    ---
    tags: [Counseling]
    parameters:
      - in: path
        name: session_id
        type: string
        required: true
    responses:
      200:
        description: 삭제 완료
        schema:
          type: object
          properties:
            ok: {type: boolean}
            deleted: {}
    """
    conn = _get_db()
    session = conn.execute("SELECT id FROM counseling_sessions WHERE id = ?", (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({"error": "session not found"}), 404

    conn.execute("DELETE FROM counseling_messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM counseling_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted": session_id})


@counseling_bp.route("/api/counseling/status", methods=["GET"])
def counseling_status():
    """상담 시스템 상태
    ---
    tags: [Counseling]
    responses:
      200:
        description: 시스템 상태
        schema:
          type: object
          properties:
            status: {}
            models: {}
            counselor_model: {}
            refine_model: {}
            pipeline: {type: string}
            total_sessions: {}
            total_messages: {}
            crisis_messages: {}
    """
    sess, base = _get_session()

    # 모델 가용성 확인
    models_ok = {}
    for model in [COUNSELOR_MODEL, KOREAN_MODEL]:
        try:
            resp = sess.post(f"{base}/api/show", json={"name": model}, timeout=5)
            models_ok[model] = resp.status_code == 200
        except Exception:
            models_ok[model] = False

    # 세션 통계
    conn = _get_db()
    stats = conn.execute(
        """SELECT COUNT(*) as total_sessions,
                  COALESCE(SUM(message_count), 0) as total_messages
           FROM counseling_sessions"""
    ).fetchone()
    crisis_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM counseling_messages WHERE is_crisis = 1"
    ).fetchone()
    conn.close()

    return jsonify({
        "status": "ready" if all(models_ok.values()) else "degraded",
        "models": models_ok,
        "counselor_model": COUNSELOR_MODEL,
        "refine_model": KOREAN_MODEL,
        "pipeline": "mindwell → qwen2.5 → force_korean → korean_humanize",
        "total_sessions": stats["total_sessions"],
        "total_messages": stats["total_messages"],
        "crisis_messages": crisis_count["cnt"],
    })
