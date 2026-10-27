#!/usr/bin/env python3
"""
Chat Memory Management - Session & Message Storage

Provides server-side conversation history persistence with optional
message compression for long-running sessions.

Functions:
  - init_chat_db_tables(): Initialize DB schema
  - get_or_create_session(): Session CRUD
  - append_chat_message(): Message persistence
  - get_session_messages(): Message retrieval
  - compress_chat_history(): LLM-based compression
"""

import uuid
import datetime
from typing import Optional, Callable, List, Dict, Any
from core.db import get_db
import logging


def _summary_model() -> str:
    """히스토리 요약에 쓸 모델 — **이미 떠 있는 상주/기본 모델을 재사용**한다.

    2026-08-04: 여기에 `qwen2.5:7b`가 하드코딩돼 있어, 세션이 길어질 때마다 요약기가
    7b(5.2GB)를 **추가로** 적재했다. 상주 정책이 `llm_resident=false`(요청 시 탑재)라
    기본 모델 14b(10.5GB)와 겹치면 `MAX_LOADED_MODELS=2`에서 15.7GB로 VRAM이 거의 차고,
    heavy tier·배치 STT가 들어올 때 축출→콜드 로드(2.5~3.9초)를 유발했다.
    요약은 품질 요구가 낮아 어떤 모델이든 되므로 **적재 0**인 쪽이 낫다.
    설정 조회 실패 시엔 종전 기본값으로 폴백한다(동작 보존).
    """
    try:
        from core.config import load_config
        from core.llm_residency import resident_model
        return resident_model(load_config()) or "qwen2.5:7b"
    except Exception:  # noqa: BLE001 — 요약은 보조 기능, 설정 조회 실패가 막으면 안 된다
        return "qwen2.5:7b"

logger = logging.getLogger("ollama-admin")


def init_chat_db_tables() -> None:
    """Initialize chat database tables via core/db.py

    Raises:
        Exception: If table initialization fails
    """
    try:
        from core.db import init_chat_memory_tables
        init_chat_memory_tables()
    except Exception as e:
        logger.error(f"Failed to initialize chat tables: {e}")
        raise


def get_or_create_session(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    model: str = "qwen2.5:7b"
) -> Dict[str, Any]:
    """Create or retrieve a chat session

    Args:
        session_id: UUID format session ID (None → create new)
        user_id: User identifier (optional, for future use)
        model: Model name for this session

    Returns:
        dict with keys: id, user_id, model, created_at, last_active_at, message_count, metadata

    Raises:
        ValueError: If session_id format is invalid
        Exception: If database operation fails
    """
    conn = get_db()

    try:
        if session_id is None:
            # Create new session
            new_id = str(uuid.uuid4())
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()

            conn.execute("""
                INSERT INTO chat_sessions (id, user_id, model, created_at, last_active_at, message_count)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (new_id, user_id, model, now, now))
            conn.commit()

            logger.info(f"Created new session: {new_id}")

            return {
                "id": new_id,
                "user_id": user_id,
                "model": model,
                "created_at": now,
                "last_active_at": now,
                "message_count": 0,
                "metadata": None
            }
        else:
            # Load existing session
            if len(session_id) != 36:
                raise ValueError(f"Invalid session_id format (expected UUID, got {len(session_id)} chars)")

            # Update last_active_at
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute("""
                UPDATE chat_sessions
                SET last_active_at = ?
                WHERE id = ?
            """, (now, session_id))
            conn.commit()

            # Fetch session
            cursor = conn.execute("""
                SELECT id, user_id, model, created_at, last_active_at, message_count, metadata
                FROM chat_sessions
                WHERE id = ?
            """, (session_id,))

            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Session not found: {session_id}")

            logger.info(f"Loaded session: {session_id}")

            return {
                "id": row[0],
                "user_id": row[1],
                "model": row[2],
                "created_at": row[3],
                "last_active_at": row[4],
                "message_count": row[5],
                "metadata": row[6]
            }

    except Exception as e:
        conn.rollback()
        logger.error(f"Error in get_or_create_session: {e}")
        raise


def append_chat_message(
    session_id: str,
    role: str,
    content: str,
    korean_ratio: Optional[float] = None
) -> Dict[str, Any]:
    """Append a message to a session

    Args:
        session_id: Session UUID
        role: Message role ('system', 'user', 'assistant')
        content: Message body (max 50KB)
        korean_ratio: Korean language ratio (0.0-1.0)

    Returns:
        dict with keys: id, session_id, role, content, korean_ratio, created_at

    Raises:
        ValueError: If validation fails
        Exception: If database operation fails
    """
    # Validation
    if not session_id or len(session_id) != 36:
        raise ValueError(f"Invalid session_id: {session_id}")
    if role not in ("system", "user", "assistant"):
        raise ValueError(f"Invalid role: {role}")
    if not content or len(content) > 50000:
        raise ValueError("content must be 1-50000 chars")
    if korean_ratio is not None and not (0.0 <= korean_ratio <= 1.0):
        raise ValueError("korean_ratio must be 0.0-1.0")

    conn = get_db()

    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Insert message
        cursor = conn.execute("""
            INSERT INTO chat_messages (session_id, role, content, korean_ratio, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, role, content, korean_ratio, now))

        msg_id = cursor.lastrowid

        # Update session metadata
        conn.execute("""
            UPDATE chat_sessions
            SET message_count = message_count + 1, last_active_at = ?
            WHERE id = ?
        """, (now, session_id))

        conn.commit()

        logger.info(f"Message {msg_id} appended to session {session_id}")

        return {
            "id": msg_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "korean_ratio": korean_ratio,
            "created_at": now
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Error in append_chat_message: {e}")
        raise


def get_session_messages(
    session_id: str,
    limit: int = 10,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Retrieve messages from a session

    Args:
        session_id: Session UUID
        limit: Number of messages (default 10)
        offset: Pagination offset (default 0)

    Returns:
        List of message dicts (newest first)

    Raises:
        ValueError: If session_id is invalid
        Exception: If database operation fails
    """
    if not session_id or len(session_id) != 36:
        raise ValueError(f"Invalid session_id: {session_id}")

    conn = get_db()

    try:
        cursor = conn.execute("""
            SELECT id, session_id, role, content, korean_ratio, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (session_id, limit, offset))

        rows = cursor.fetchall()

        messages = [
            {
                "id": row[0],
                "session_id": row[1],
                "role": row[2],
                "content": row[3],
                "korean_ratio": row[4],
                "created_at": row[5]
            }
            for row in rows
        ]

        logger.info(f"Retrieved {len(messages)} messages from session {session_id}")

        return messages

    except Exception as e:
        logger.error(f"Error in get_session_messages: {e}")
        raise


def list_sessions(
    user_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """세션 목록 조회

    Args:
        user_id: 특정 사용자 필터 (None → 전체)
        limit: 조회 수 (기본 20, 최대 100)
        offset: 페이지네이션

    Returns:
        List of session dicts (최신순)
    """
    conn = get_db()
    limit = min(limit, 100)

    try:
        if user_id:
            cursor = conn.execute("""
                SELECT id, user_id, model, created_at, last_active_at, message_count
                FROM chat_sessions WHERE user_id = ?
                ORDER BY last_active_at DESC LIMIT ? OFFSET ?
            """, (user_id, limit, offset))
        else:
            cursor = conn.execute("""
                SELECT id, user_id, model, created_at, last_active_at, message_count
                FROM chat_sessions
                ORDER BY last_active_at DESC LIMIT ? OFFSET ?
            """, (limit, offset))

        return [
            {
                "id": row[0], "user_id": row[1], "model": row[2],
                "created_at": row[3], "last_active_at": row[4],
                "message_count": row[5],
            }
            for row in cursor.fetchall()
        ]
    except Exception as e:
        logger.error(f"Error in list_sessions: {e}")
        raise


def delete_session(session_id: str) -> bool:
    """세션 삭제 (CASCADE로 메시지도 삭제)"""
    if not session_id or len(session_id) != 36:
        raise ValueError(f"Invalid session_id: {session_id}")

    conn = get_db()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        conn.commit()
        logger.info(f"Deleted session: {session_id}")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error in delete_session: {e}")
        raise


def purge_old_sessions(retention_days: int = 90) -> Dict[str, Any]:
    """비활성 retention_days일 초과 챗 세션+메시지 영구 삭제 (PHI 최소화/거버넌스).

    last_active_at(없으면 created_at) 기준으로 now-retention_days 이전 활동 세션을
    대상으로, 메시지를 먼저 삭제한 뒤 세션을 삭제한다(2단계, 명시적 카운트 —
    FK CASCADE pragma 의존을 피하고 삭제 건수를 정확히 보고). 시각 비교는 chat_memory
    저장 포맷(UTC isoformat, 동일 +00:00 오프셋)이라 사전식 비교=시간순 비교.

    Args:
        retention_days: 비활성 보관 일수. <=0 이면 무동작(정책 비활성).

    Returns:
        dict: {retention_days, cutoff, sessions_deleted, messages_deleted, skipped?}

    Raises:
        Exception: DB 작업 실패 시(롤백 후 재발생)
    """
    if not retention_days or retention_days <= 0:
        return {
            "retention_days": retention_days,
            "cutoff": None,
            "sessions_deleted": 0,
            "messages_deleted": 0,
            "skipped": True,
        }

    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=retention_days)
    ).isoformat()

    conn = get_db()
    try:
        # 메시지 먼저 — 대상 세션(비활성) 하위 전부
        msg_cur = conn.execute(
            """
            DELETE FROM chat_messages
            WHERE session_id IN (
                SELECT id FROM chat_sessions
                WHERE COALESCE(last_active_at, created_at) < ?
            )
            """,
            (cutoff,),
        )
        messages_deleted = msg_cur.rowcount

        sess_cur = conn.execute(
            "DELETE FROM chat_sessions WHERE COALESCE(last_active_at, created_at) < ?",
            (cutoff,),
        )
        sessions_deleted = sess_cur.rowcount

        conn.commit()

        if sessions_deleted:
            logger.info(
                "Purged %d inactive chat sessions / %d messages (>%dd)",
                sessions_deleted, messages_deleted, retention_days,
            )

        return {
            "retention_days": retention_days,
            "cutoff": cutoff,
            "sessions_deleted": sessions_deleted,
            "messages_deleted": messages_deleted,
        }
    except Exception as e:
        conn.rollback()
        logger.error(f"Error in purge_old_sessions: {e}")
        raise


def compress_chat_history(
    session_id: str,
    recent_turns: int = 10,
    llm_func: Optional[Callable] = None
) -> Dict[str, Any]:
    """Compress old messages using LLM summarization

    Args:
        session_id: Session UUID
        recent_turns: Number of recent turns to preserve
        llm_func: Optional LLM function (default: uses Ollama)

    Returns:
        dict with compression results

    Raises:
        ValueError: If session_id is invalid
        Exception: If compression fails
    """
    if not session_id or len(session_id) != 36:
        raise ValueError(f"Invalid session_id: {session_id}")

    conn = get_db()

    try:
        # Get total message count
        cursor = conn.execute("""
            SELECT COUNT(*) FROM chat_messages WHERE session_id = ?
        """, (session_id,))
        total_count = cursor.fetchone()[0]

        if total_count <= recent_turns:
            logger.info(f"Session {session_id} has {total_count} messages, no compression needed")
            return {
                "session_id": session_id,
                "compressed": False,
                "reason": "Total messages less than recent_turns"
            }

        # Get old messages (excluding recent_turns)
        cursor = conn.execute("""
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?
        """, (session_id, total_count - recent_turns))

        old_messages = cursor.fetchall()
        old_message_ids = [msg[0] for msg in old_messages]

        if not old_message_ids:
            return {"session_id": session_id, "compressed": False, "reason": "No old messages to compress"}

        # Build summarization prompt (truncate per message to keep prompt within token limits)
        conversation_text = "\n".join([
            f"{msg[1]}: {msg[2][:500]}" for msg in old_messages
        ])

        summary_prompt = f"""다음 대화 히스토리를 간결하게 요약해주세요 (3문장 이내):

{conversation_text}"""

        # Call LLM (default or custom)
        if llm_func is None:
            # Default: call Ollama
            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
            # v2.125.16 무성 실패 일괄(2차): HTTP 오류도 전송 예외와 같이 **던진다**.
            # 🔴 종전엔 빈 요약이 저장됐는데, 이 함수는 요약 뒤 **원본 메시지를 삭제**한다
            #    → 빈 요약 + 원본 삭제 = 대화 이력이 조용히 사라진다. 던지면 호출자 except가
            #    받고 원본은 그대로 남는다(요약은 다음 기회에).
            from core import model_router
            from core.model_router import OllamaAdapter
            res = model_router.generate(
                "chat.memory_summary", summary_prompt, model=_summary_model(), timeout=60,
                adapter=OllamaAdapter(base="http://localhost:11434"))
            if not res.ok:
                raise RuntimeError(res.error or ("HTTP %s" % res.status))
            summary = res.text.strip()
        else:
            summary = llm_func(summary_prompt)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Insert summary as system message
        cursor = conn.execute("""
            INSERT INTO chat_messages (session_id, role, content, korean_ratio, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, "system", f"[요약]\n{summary}", None, now))

        summary_id = cursor.lastrowid

        # Delete old messages
        placeholders = ",".join("?" * len(old_message_ids))
        conn.execute(f"""
            DELETE FROM chat_messages
            WHERE id IN ({placeholders})
        """, old_message_ids)

        # Update message_count
        new_count = recent_turns + 1  # recent messages + summary
        conn.execute("""
            UPDATE chat_sessions
            SET message_count = ?
            WHERE id = ?
        """, (new_count, session_id))

        conn.commit()

        logger.info(f"Compressed {len(old_message_ids)} messages in session {session_id}")

        return {
            "session_id": session_id,
            "compressed": True,
            "old_message_count": total_count,
            "new_message_count": new_count,
            "summary": summary,
            "summary_id": summary_id,
            "message_ids_deleted": old_message_ids,
            "compression_ratio": 1.0 - (new_count / total_count)
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Error in compress_chat_history: {e}")
        raise
