"""
Database connection management for WeRUBLLMManager
Thread-local SQLite3 connection pool
"""

import sqlite3
import threading
from .config import DB_PATH

# Thread-local storage for persistent DB connections
_db_local = threading.local()

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [30, 60, 120]  # seconds


def get_db():
    """
    Get thread-local SQLite3 connection with optimized PRAGMAs

    Returns:
        sqlite3.Connection: Persistent connection (one per thread)
    """
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        # Optimization PRAGMAs
        # ⚠️ busy_timeout을 가장 먼저 — 이후 PRAGMA(journal_mode 등)/쿼리가 락에 무한 대기하지
        #    않도록(5s 후 OperationalError). 순서 버그 시 첫 PRAGMA가 무방비로 wedge되어
        #    /api/chat 세션메모리(get_db)가 멈추던 문제 수정.
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")    # 8MB page cache
        conn.execute("PRAGMA temp_store=MEMORY")   # In-memory temp tables
        conn.execute("PRAGMA mmap_size=134217728") # 128MB mmap reads
        conn.execute("PRAGMA foreign_keys=ON")    # FK constraints enforced
        _db_local.conn = conn

    # Validate connection
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except Exception:
            pass
        _db_local.conn = None
        return get_db()  # Recursive retry

    return conn


def init_db():
    """
    Initialize database schema with all required tables and indexes
    Includes schema migration for backward compatibility
    """
    # ⚠️ 종전 `from . import logger`는 로거 객체가 아니라 core/logger.py **모듈**을 가져왔다 —
    # 신규 DB에서 ALTER가 처음 성공하는 순간 `logger.info`가 AttributeError로 터졌다(v2.125.53 발견).
    # 이 함수는 현재 호출처가 없다(app.py init_db가 정본) — 쓰일 때 틀리지 않게만 맞춘다.
    import logging
    logger = logging.getLogger("ollama-admin")

    conn = get_db()

    # Jobs table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            batch_id TEXT,
            status TEXT DEFAULT 'pending',
            model TEXT NOT NULL,
            system_prompt TEXT,
            prompt TEXT NOT NULL,
            response TEXT,
            error TEXT,
            tokens INTEGER,
            duration_ms INTEGER,
            priority INTEGER DEFAULT 5,
            retry_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            metadata TEXT
        )
    """)


    # API keys table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            active INTEGER DEFAULT 1
        )
    """)

    # Indexes for jobs table
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, priority, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_completed ON jobs(status, completed_at)")

    # Stats history table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            pending INTEGER DEFAULT 0,
            running INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            tokens INTEGER DEFAULT 0,
            avg_speed REAL DEFAULT 0
        )
    """)

    # Alerts history table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            channel TEXT,
            success INTEGER DEFAULT 1
        )
    """)

    # API activity log table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status_code INTEGER,
            duration_ms INTEGER,
            ip TEXT,
            model TEXT,
            request_size INTEGER DEFAULT 0,
            response_size INTEGER DEFAULT 0,
            user TEXT,
            key_name TEXT
        )
    """)

    # Indexes for activity table
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_ts ON api_activity(ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_path ON api_activity(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_key ON api_activity(key_name)")

    # Characters table (image consistency)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            reference_image TEXT NOT NULL,
            thumbnail TEXT,
            tags TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            metadata TEXT
        )
    """)

    # Schema migration (backward compatibility)
    # ⚠️ v2.125.53: 모든 CREATE TABLE 뒤에서 돌아야 한다(종전엔 api_keys 생성 전이라 신규 DB에서
    # expires_at이 조용히 누락됐다 — app.py init_db와 같은 결함). 회귀: tests/test_init_db_fresh_schema.py
    for tbl, col, typedef in [
        ("jobs", "retry_count", "INTEGER DEFAULT 0"),
        ("jobs", "options", "TEXT"),
        ("jobs", "webhook_url", "TEXT"),
        ("jobs", "job_type", "TEXT DEFAULT 'llm'"),
        ("api_keys", "expires_at", "TEXT"),
        # ("api_keys", "key_value", "TEXT") — 평문 저장 폐지(v2.124.46)
        ("api_activity", "key_name", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typedef}")
            conn.commit()
            logger.info(f"Added {col} column to {tbl} table")
        except sqlite3.OperationalError:
            pass

    # Reset stuck running jobs from previous crash
    conn.execute("UPDATE jobs SET status='pending', started_at=NULL WHERE status='running'")
    conn.commit()


def init_chat_memory_tables():
    """Initialize chat_sessions and chat_messages tables with indexes

    Raises:
        Exception: If table initialization fails
    """
    import logging
    logger = logging.getLogger("ollama-admin")

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
