---
name: P4-2 Chat Server Memory - Do Phase
description: Step-by-step implementation guide for server-side conversation history with session management
type: do
---

# P4-2: 채팅 서버 메모리 — 구현 가이드

**Status**: Do Phase (Implementation)  
**Feature**: P4-2-Chat-Server-Memory  
**Target Duration**: 3-5 days  
**Date**: 2026-05-14

---

## 준비 사항

### 사전 검증
```bash
# Design 문서 확인
cat docs/02-design/features/P4-2-Chat-Server-Memory.design.md | head -20

# 의존 파일 확인
ls -la core/db.py core/korean.py core/hr_law.py routes/chat.py
```

### 기존 함수 참고 (재사용 패턴)
- `core.db.get_db()` — SQLite 연결
- `core.korean.validate_korean_quality()` — 한국어 검증 (이미 routes/chat.py에서 사용)
- `core.hr_law.classify_question()`, `select_model_by_difficulty()` — 모델 선택

---

## Step 1: DB 테이블 생성 (core/db.py)

**예상 시간**: 10분  
**목표**: chat_sessions, chat_messages 테이블 + 인덱스 생성

### 1.1 현재 파일 확인
```bash
wc -l core/db.py
grep -n "CREATE TABLE" core/db.py | head -5
```

### 1.2 core/db.py에 함수 추가

**위치**: 파일 끝부분 (기존 테이블 생성 로직 다음)

```python
# core/db.py 추가 함수

def init_chat_memory_tables():
    """Initialize chat_sessions and chat_messages tables with indexes"""
    conn = get_db()
    try:
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        
        # chat_sessions 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                model TEXT NOT NULL DEFAULT 'qwen2.5:7b',
                created_at TEXT NOT NULL,
                last_active_at TEXT,
                message_count INTEGER DEFAULT 0,
                metadata TEXT,
                CHECK(length(id) = 36),
                CHECK(message_count >= 0)
            )
        """)
        
        # chat_sessions 인덱스
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id 
            ON chat_sessions(user_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_created_at 
            ON chat_sessions(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_last_active_at 
            ON chat_sessions(last_active_at)
        """)
        
        # chat_messages 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                korean_ratio REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE,
                CHECK(role IN ('system', 'user', 'assistant')),
                CHECK(korean_ratio IS NULL OR (korean_ratio >= 0.0 AND korean_ratio <= 1.0)),
                CHECK(length(content) > 0)
            )
        """)
        
        # chat_messages 인덱스
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id 
            ON chat_messages(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created 
            ON chat_messages(session_id, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at 
            ON chat_messages(created_at)
        """)
        
        conn.commit()
        logger.info("Chat memory tables initialized successfully")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to initialize chat memory tables: {e}")
        raise
```

### 1.3 검증
```bash
python3 -c "
from core.db import init_chat_memory_tables
init_chat_memory_tables()
print('✓ chat_sessions, chat_messages 테이블 생성 완료')
"
```

**체크리스트**:
- [ ] init_chat_memory_tables() 함수 추가됨
- [ ] CREATE TABLE IF NOT EXISTS 포함 (멱등성)
- [ ] 외래키 제약 활성화
- [ ] 모든 인덱스 생성
- [ ] 문법 검증 (python3 -m py_compile core/db.py)

---

## Step 2: core/chat_memory.py 모듈 작성

**예상 시간**: 1시간  
**목표**: 5개 함수 + 초기화 구현

### 2.1 파일 생성
```bash
touch core/chat_memory.py
chmod 644 core/chat_memory.py
```

### 2.2 전체 코드 작성

```python
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
import json
import datetime
from typing import Optional, Callable, List, Dict, Any
from core.db import get_db
import logging

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
            now = datetime.datetime.utcnow().isoformat() + "Z"
            
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
            now = datetime.datetime.utcnow().isoformat() + "Z"
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
        now = datetime.datetime.utcnow().isoformat() + "Z"
        
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
        
        # Build summarization prompt
        conversation_text = "\n".join([
            f"{msg[1]}: {msg[2][:200]}" for msg in old_messages
        ])
        
        summary_prompt = f"""다음 대화 히스토리를 간결하게 요약해주세요 (3문장 이내):

{conversation_text}"""
        
        # Call LLM (default or custom)
        if llm_func is None:
            # Default: call Ollama
            import requests
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "qwen2.5:7b", "prompt": summary_prompt, "stream": False},
                timeout=60
            )
            summary = response.json().get("response", "").strip()
        else:
            summary = llm_func(summary_prompt)
        
        now = datetime.datetime.utcnow().isoformat() + "Z"
        
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
            "summary_id": summary_id,
            "message_ids_deleted": old_message_ids,
            "compression_ratio": 1.0 - (new_count / total_count)
        }
    
    except Exception as e:
        logger.error(f"Error in compress_chat_history: {e}")
        raise
```

### 2.3 문법 검증
```bash
python3 -m py_compile core/chat_memory.py
echo "✓ core/chat_memory.py 문법 검증 완료"
```

**체크리스트**:
- [ ] core/chat_memory.py 파일 생성됨
- [ ] init_chat_db_tables() 구현됨
- [ ] get_or_create_session() 구현됨
- [ ] append_chat_message() 구현됨
- [ ] get_session_messages() 구현됨
- [ ] compress_chat_history() 구현됨
- [ ] 문법 검증 통과

---

## Step 3: core/db.py에서 init_chat_memory_tables 임포트 추가

**예상 시간**: 5분

### 3.1 core/db.py 상단 확인
```bash
head -20 core/db.py
```

### 3.2 logging import 확인
```python
# core/db.py 상단에 이미 있는지 확인
import logging
logger = logging.getLogger("ollama-admin")
```

**체크리스트**:
- [ ] logging import 확인
- [ ] init_chat_memory_tables 함수가 core/db.py에 있음

---

## Step 4: core/__init__.py exports 추가

**예상 시간**: 5분  
**목표**: chat_memory 함수들을 core 모듈에서 export

### 4.1 현재 파일 확인
```bash
cat core/__init__.py
```

### 4.2 추가할 코드
```python
# core/__init__.py에 추가

from core.chat_memory import (
    init_chat_db_tables,
    get_or_create_session,
    append_chat_message,
    get_session_messages,
    compress_chat_history
)

# __all__에 추가
__all__ = [
    # ... 기존 exports ...
    "init_chat_db_tables",
    "get_or_create_session",
    "append_chat_message",
    "get_session_messages",
    "compress_chat_history"
]
```

### 4.3 검증
```bash
python3 -c "
from core import init_chat_db_tables, get_or_create_session, append_chat_message, get_session_messages, compress_chat_history
print('✓ 모든 chat_memory 함수가 core에서 export됨')
"
```

**체크리스트**:
- [ ] 5개 함수 모두 import됨
- [ ] __all__ 리스트 갱신됨
- [ ] 임포트 테스트 통과

---

## Step 5: routes/chat.py 통합

**예상 시간**: 30분  
**목표**: session_id 파라미터 처리 및 메시지 저장

### 5.1 현재 파일 확인
```bash
grep -n "def api_chat" routes/chat.py
grep -n "_chat_non_streaming" routes/chat.py
```

### 5.2 routes/chat.py 상단에 임포트 추가

```python
# routes/chat.py 임포트 섹션에 추가 (기존 import 아래)
from core.chat_memory import (
    init_chat_db_tables,
    get_or_create_session,
    append_chat_message
)
```

### 5.3 api_chat 함수 수정

**현재 코드 위치**: `@bp.route("/api/chat", methods=["POST"])` (대략 368줄)

**수정 사항**: 비스트리밍 모드에서 session 처리 추가

```python
@bp.route("/api/chat", methods=["POST"])
@require_auth
def api_chat():
    """Chat with model (streaming or quality-checked)
    ...
    """
    try:
        data = request.json or {}
        messages = data.get("messages", [])
        client_model = data.get("model", "")
        stream = data.get("stream", True)
        quality_check = data.get("quality_check", False)
        
        # NEW: session_id 파라미터 추가
        session_id = data.get("session_id", None)
        user_id = data.get("user_id", None)
        
        # Validate messages
        if not messages:
            return jsonify({"error": "messages required"}), 400

        # Ensure system message exists
        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            cfg = load_config()
            if cfg.get("default_system_prompt_enabled", True):
                messages = [{"role": "system", "content": build_init_system_prompt()}] + messages

        # STREAMING MODE (default, backward compatible)
        if stream and not quality_check:
            model = client_model or load_config().get("default_model", "")
            if not model:
                return jsonify({"error": "model required (set default_model in config)"}), 400

            payload = {"model": model, "messages": messages, "stream": True}

            def stream_response():
                try:
                    r = requests.post(
                        f"{OLLAMA_BASE}/api/chat",
                        json=payload, stream=True, timeout=600,
                    )
                    for line in r.iter_lines():
                        if line:
                            yield line.decode() + "\n"
                except Exception as e:
                    logger.error(f"Streaming error: {e}")
                    yield json.dumps({"error": str(e)}) + "\n"

            return Response(stream_response(), mimetype="application/x-ndjson")

        # NON-STREAMING MODE (new, with quality validation + session management)
        else:
            # Get or create session
            selected_model = client_model or load_config().get("default_model", "")
            session = get_or_create_session(session_id, user_id, selected_model)
            
            # Get last user message for extraction
            last_user_msg = _get_last_user_message(messages)
            
            # Call non-streaming handler
            result = _chat_non_streaming(messages, client_model)
            
            # NEW: Append messages to session
            try:
                append_chat_message(
                    session["id"],
                    "user",
                    last_user_msg,
                    None  # korean_ratio for user message (optional)
                )
                append_chat_message(
                    session["id"],
                    "assistant",
                    result["answer"],
                    result["quality_check"]["korean_ratio"]
                )
                
                # Add session info to response
                result["session_id"] = session["id"]
                
                # Get updated message count
                session_updated = get_or_create_session(session["id"], user_id, selected_model)
                result["message_count"] = session_updated["message_count"]
                
            except Exception as e:
                logger.warning(f"Failed to save message to session: {e}")
                # Continue anyway, don't break the response
            
            return jsonify(result)

    except Exception as e:
        logger.error(f"Chat API error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
```

### 5.4 앱 초기화 시 테이블 생성

**위치**: app_flask.py의 app 초기화 섹션 또는 init_chat_globals 함수

```python
# app_flask.py에서 Blueprint 등록 후
def init_app():
    """Initialize Flask app"""
    # ... 기존 코드 ...
    
    # Initialize chat memory tables
    with app.app_context():
        try:
            init_chat_db_tables()
            logger.info("Chat memory tables initialized")
        except Exception as e:
            logger.warning(f"Chat memory tables may already exist: {e}")
```

또는 routes/chat.py의 init_chat_globals 함수에 추가:

```python
# routes/chat.py
def init_chat_globals(session=None):
    """Initialize chat blueprint globals"""
    try:
        init_chat_db_tables()
        logger.info("Chat memory tables initialized")
    except Exception as e:
        logger.warning(f"Chat memory initialization: {e}")
```

### 5.5 문법 검증
```bash
python3 -m py_compile routes/chat.py
echo "✓ routes/chat.py 문법 검증 완료"
```

**체크리스트**:
- [ ] session_id, user_id 파라미터 추출됨
- [ ] get_or_create_session() 호출 추가됨
- [ ] append_chat_message() 호출 (user + assistant) 추가됨
- [ ] 응답에 session_id, message_count 추가됨
- [ ] 스트리밍 모드는 변경 없음 (하위 호환)
- [ ] 문법 검증 통과

---

## Step 6: 전체 문법 검증

**예상 시간**: 5분

```bash
# 모든 파일 컴파일 검증
python3 -m py_compile core/chat_memory.py
python3 -m py_compile core/db.py
python3 -m py_compile routes/chat.py
python3 -m py_compile core/__init__.py
python3 -m py_compile app_flask.py

echo "✓ 모든 파일 문법 검증 완료"
```

**체크리스트**:
- [ ] core/chat_memory.py 컴파일 성공
- [ ] core/db.py 컴파일 성공
- [ ] routes/chat.py 컴파일 성공
- [ ] core/__init__.py 컴파일 성공
- [ ] app_flask.py 컴파일 성공

---

## Step 7: 통합 테스트

**예상 시간**: 20분

### 7.1 DB 테이블 생성 테스트
```bash
python3 << 'EOF'
from core.db import get_db, init_chat_memory_tables

# Initialize tables
init_chat_memory_tables()

# Verify tables exist
conn = get_db()
cursor = conn.execute("""
    SELECT name FROM sqlite_master 
    WHERE type='table' AND name IN ('chat_sessions', 'chat_messages')
    ORDER BY name
""")

tables = [row[0] for row in cursor.fetchall()]
print(f"✓ 테이블 생성 완료: {', '.join(tables)}")
assert 'chat_sessions' in tables
assert 'chat_messages' in tables
EOF
```

### 7.2 세션 생성 테스트
```bash
python3 << 'EOF'
from core import get_or_create_session, append_chat_message, get_session_messages

# Test 1: Create new session
session = get_or_create_session(None, "test_user", "qwen2.5:7b")
print(f"✓ 새 세션 생성: {session['id']}")
assert session["message_count"] == 0

# Test 2: Append messages
msg1 = append_chat_message(session["id"], "user", "안녕하세요", 1.0)
print(f"✓ 사용자 메시지 저장: {msg1['id']}")

msg2 = append_chat_message(session["id"], "assistant", "안녕하세요! 무엇을 도와드릴까요?", 0.95)
print(f"✓ 어시스턴트 메시지 저장: {msg2['id']}")

# Test 3: Retrieve messages
messages = get_session_messages(session["id"], limit=10)
print(f"✓ 메시지 조회: {len(messages)}개")
assert len(messages) == 2

# Test 4: Load existing session
session2 = get_or_create_session(session["id"], "test_user", "qwen2.5:7b")
print(f"✓ 기존 세션 로드: {session2['id']}")
assert session2["message_count"] == 2

print("\n✓ 모든 기본 테스트 통과!")
EOF
```

### 7.3 API 통합 테스트 (스트리밍 모드 - 하위 호환)
```bash
# Flask 앱 실행 (백그라운드)
# python3 app_flask.py &

# 스트리밍 요청 (session_id 없음, stream=true)
curl -s -X POST http://localhost:5000/api/chat \
  -H "Authorization: Bearer <YOUR_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "안녕하세요"}],
    "stream": true
  }' | head -3

echo "✓ 스트리밍 모드 (하위 호환) 동작 확인"
```

### 7.4 API 통합 테스트 (비스트리밍 모드 + 세션)
```bash
curl -s -X POST http://localhost:5000/api/chat \
  -H "Authorization: Bearer <YOUR_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "안녕하세요"}],
    "stream": false,
    "quality_check": true
  }' | python3 -m json.tool

# 응답에 session_id와 message_count 포함되는지 확인
# "session_id": "550e8400-...",
# "message_count": 2
```

### 7.5 세션 재사용 테스트
```bash
# 1단계에서 얻은 session_id로 다시 요청
SESSION_ID="550e8400-e29b-41d4-a716-446655440000"  # 실제 ID로 변경

curl -s -X POST http://localhost:5000/api/chat \
  -H "Authorization: Bearer <YOUR_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{
    \"messages\": [{\"role\": \"user\", \"content\": \"두 번째 질문입니다\"}],
    \"session_id\": \"$SESSION_ID\",
    \"stream\": false,
    \"quality_check\": true
  }" | python3 -m json.tool

# message_count가 4 이상인지 확인 (2개 메시지 x 2 요청)
```

**체크리스트**:
- [ ] DB 테이블 생성 확인
- [ ] 새 세션 생성 성공
- [ ] 메시지 저장 성공 (user + assistant)
- [ ] 메시지 조회 성공
- [ ] 기존 세션 로드 성공
- [ ] 스트리밍 모드 동작 (하위 호환)
- [ ] 비스트리밍 모드 동작
- [ ] 세션 재사용 동작

---

## Step 8: 코드 리뷰 및 정리

**예상 시간**: 10분

### 8.1 임포트 확인
```bash
# 불필요한 임포트 제거
grep -n "^import\|^from" core/chat_memory.py
grep -n "^import\|^from" routes/chat.py
```

### 8.2 로깅 확인
```bash
# 모든 주요 동작에 로깅이 있는지 확인
grep -n "logger\." core/chat_memory.py | wc -l
```

### 8.3 에러 처리 확인
```bash
# 모든 예외가 처리되는지 확인
grep -n "except\|raise" core/chat_memory.py | wc -l
```

**체크리스트**:
- [ ] 불필요한 임포트 없음
- [ ] 모든 주요 함수에 로깅 추가됨
- [ ] 모든 예외 케이스 처리됨

---

## Step 9: 최종 검증 및 커밋 준비

**예상 시간**: 10분

### 9.1 파일 변경 확인
```bash
git status
git diff --stat
```

### 9.2 변경 내용 요약
```bash
# 추가된 파일
# - core/chat_memory.py (~250줄)
# - docs/02-design/features/P4-2-Chat-Server-Memory.design.md
# - docs/03-implementation/P4-2-Chat-Server-Memory.do.md (현재)

# 수정된 파일
# - core/db.py (~80줄 추가)
# - routes/chat.py (~35줄 추가)
# - core/__init__.py (~6줄 추가)
```

### 9.3 최종 문법 검증
```bash
python3 << 'EOF'
import py_compile
import sys

files = [
    "core/chat_memory.py",
    "core/db.py",
    "routes/chat.py",
    "core/__init__.py",
    "app_flask.py"
]

errors = []
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"✓ {f}")
    except Exception as e:
        print(f"✗ {f}: {e}")
        errors.append(f)

if errors:
    print(f"\n❌ {len(errors)} 파일 오류")
    sys.exit(1)
else:
    print(f"\n✓ 모든 {len(files)} 파일 검증 통과")
EOF
```

**체크리스트**:
- [ ] 모든 파일 문법 검증 통과
- [ ] git status 확인됨
- [ ] 변경 내용 이해됨

---

## 구현 완료 후 다음 단계

1. **Gap Analysis 실행**:
   ```bash
   /pdca analyze P4-2-Chat-Server-Memory
   ```

2. **Match Rate 확인**:
   - 90% 이상: Report 단계로 진행
   - 90% 미만: Iterate 단계로 자동 개선

3. **최종 보고서 생성**:
   ```bash
   /pdca report P4-2-Chat-Server-Memory
   ```

---

## 주요 파일 위치 요약

| 파일 | 역할 | 라인 수 |
|------|------|--------|
| `core/chat_memory.py` | 세션/메시지 관리 | ~250 |
| `core/db.py` | DDL 추가 | ~80 |
| `routes/chat.py` | API 통합 | ~35 |
| `core/__init__.py` | Exports | ~6 |
| **Total** | | **~371** |

---

## 문제 해결 가이드

### 에러: "sqlite3.OperationalError: database is locked"
**해결책**:
```python
# core/db.py의 get_db() 함수에서 타임아웃 설정
conn.execute("PRAGMA busy_timeout = 5000")  # 5초 재시도
```

### 에러: "session_id not found"
**확인사항**:
- UUID 포맷 확인 (36자)
- DB에 실제 세션이 저장되었는지 확인
```bash
python3 -c "
from core.db import get_db
conn = get_db()
cursor = conn.execute('SELECT COUNT(*) FROM chat_sessions')
print(f'세션 수: {cursor.fetchone()[0]}')
"
```

### 에러: "Foreign key constraint failed"
**해결책**:
```python
# 세션이 삭제되지 않았는지 확인
# DELETE CASCADE 설정 확인 (이미 DDL에 포함됨)
```

---

**구현 시작**: 위의 Step 1부터 차례대로 진행하세요.
