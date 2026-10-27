"""Story Engine Blueprint — 스토리/소설 생성 엔진 (문체 시스템 + 컨텍스트 + 이어쓰기 + 분기)"""
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, request, jsonify, Response

from core.config import OLLAMA_BASE
from core.korean import force_korean, force_korean_token
from core.korean_humanize import compute_korean_quality, get_quality_summary, get_s1_prompt_rules
from core.cache import TTLCache
from core.validator import validate

logger = logging.getLogger("ollama-admin")

story_bp = Blueprint("story", __name__)

_ollama_session = None
_OLLAMA_BASE = None
_story_cache = TTLCache(maxsize=300, ttl=120)  # 읽기 API 캐시 (2분)
_db_path = None

# ---------------------------------------------------------------------------
# 모델 설정
# ---------------------------------------------------------------------------

STORY_MODEL = "qwen2.5:14b"
STRUCTURE_MODEL = "exaone-deep:7.8b"
POLISH_MODEL = "dnotitia/dna:latest"
FAST_MODEL = "qwen2.5:7b"

# ---------------------------------------------------------------------------
# 스타일 프리셋 (9종)
# ---------------------------------------------------------------------------

STYLE_PROMPTS = {
    "novel": {
        "name": "소설체",
        "desc": "섬세한 묘사, 내면 독백, 은유와 감각 묘사",
        "prompt": "소설가로서 작성하세요. 섬세한 묘사, 내면 독백, 감각적 표현(시각/청각/후각/촉각)을 활용하세요. "
                  "Show Don't Tell 원칙을 따르세요. '슬펐다' 대신 '시야가 흐려졌다'처럼 감정을 행동으로 보여주세요.",
    },
    "game": {
        "name": "게임 나레이션",
        "desc": "간결하고 임팩트 있는 게임 텍스트",
        "prompt": "게임 나레이터로서 작성하세요. 짧고 강렬한 문장, 액션 동사 중심. "
                  "플레이어가 몰입할 수 있도록 현장감 있게 묘사하세요.",
    },
    "script": {
        "name": "시나리오/대본",
        "desc": "대사 + 지시문 형식",
        "prompt": "시나리오 작가로서 작성하세요. '인물명: 대사' 형식, (행동 지시)를 괄호로 표기. "
                  "카메라 워크와 분위기 지시를 포함하세요.",
    },
    "fairy_tale": {
        "name": "동화/우화",
        "desc": "서정적이고 교훈이 있는 이야기",
        "prompt": "동화 작가로서 작성하세요. '옛날 옛적에'로 시작하는 서정적 문체, "
                  "반복 구조와 운율감, 따뜻한 교훈을 담으세요.",
    },
    "horror": {
        "name": "공포",
        "desc": "불안감, 서스펜스, 반전",
        "prompt": "공포 소설가로서 작성하세요. 짧은 호흡의 문장, 감각을 자극하는 묘사, "
                  "일상의 균열에서 오는 공포. 직접적 설명보다 암시와 여운을 활용하세요.",
    },
    "romance": {
        "name": "로맨스",
        "desc": "감정 중심, 설렘과 갈등",
        "prompt": "로맨스 소설가로서 작성하세요. 인물 간 감정선과 내면 묘사에 집중하세요. "
                  "설렘/갈등/화해의 리듬감, 대화 비중을 높이세요.",
    },
    "epic": {
        "name": "서사시",
        "desc": "웅장하고 운명적인 이야기",
        "prompt": "서사시 작가로서 작성하세요. 장대한 묘사, 역사적 어투, 운명과 영웅의 서사. "
                  "웅장한 배경 묘사와 숭고한 분위기를 연출하세요.",
    },
    "noir": {
        "name": "느와르",
        "desc": "건조하고 하드보일드한 문체",
        "prompt": "느와르 소설가로서 작성하세요. 1인칭 독백, 건조한 관찰, 비관적 세계관. "
                  "짧은 문장과 비유를 활용한 하드보일드 문체로 작성하세요.",
    },
    "comedy": {
        "name": "코미디",
        "desc": "유머와 반전이 있는 이야기",
        "prompt": "코미디 작가로서 작성하세요. 과장, 패러디, 예상치 못한 반전. "
                  "대사 위주로 유쾌하게, 타이밍과 리듬감을 살리세요.",
    },
}

# ---------------------------------------------------------------------------
# 톤 프리셋 (9종)
# ---------------------------------------------------------------------------

TONE_PROMPTS = {
    "melancholy": {"name": "쓸쓸함", "prompt": "쓸쓸하고 여운 있는 분위기. 과거 회상, 상실감, 잔잔한 슬픔."},
    "heroic": {"name": "영웅적", "prompt": "결의와 용기가 넘치는 고양된 분위기. 도전과 극복의 서사."},
    "humorous": {"name": "유쾌함", "prompt": "유쾌하고 가벼운 분위기. 위트 있는 표현과 밝은 에너지."},
    "dark": {"name": "어둠", "prompt": "어둡고 절망적인 분위기. 위기감과 불안, 희망 없는 상황."},
    "warm": {"name": "따뜻함", "prompt": "따뜻하고 안도감 있는 분위기. 일상의 소소함, 인간미."},
    "tense": {"name": "긴장감", "prompt": "긴장감 넘치는 서스펜스 분위기. 위기와 갈등, 빠른 호흡."},
    "dreamy": {"name": "몽환", "prompt": "몽환적이고 비현실적 분위기. 환상과 꿈 사이의 경계."},
    "nostalgic": {"name": "향수", "prompt": "향수와 그리움이 가득한 분위기. 돌아갈 수 없는 시간."},
    "cynical": {"name": "냉소", "prompt": "냉소적이고 비판적 시선. 날카로운 관찰과 풍자."},
}

# ---------------------------------------------------------------------------
# 시점 프리셋
# ---------------------------------------------------------------------------

POV_PROMPTS = {
    "first_person": "1인칭 시점으로 작성하세요. '나'의 감각과 생각만 서술합니다. 주인공이 모르는 것은 독자도 모릅니다.",
    "third_person": "3인칭 제한 시점으로 작성하세요. 시점 인물의 감각과 생각만 서술합니다.",
    "omniscient": "전지적 시점으로 작성하세요. 여러 인물의 내면을 자유롭게 서술하되, 한 장면 내에서는 시점을 고정하세요.",
}

# ---------------------------------------------------------------------------
# 길이 프리셋
# ---------------------------------------------------------------------------

LENGTH_CONFIG = {
    "short": {"chars": 500, "num_predict": 600, "desc": "짧게 (~500자)"},
    "medium": {"chars": 1500, "num_predict": 1800, "desc": "보통 (~1500자)"},
    "long": {"chars": 3000, "num_predict": 3500, "desc": "길게 (~3000자)"},
    "chapter": {"chars": 5000, "num_predict": 6000, "desc": "챕터 (~5000자)"},
}

# ---------------------------------------------------------------------------
# 소설 작법 규칙 (시스템 프롬프트 주입)
# ---------------------------------------------------------------------------

NOVEL_CRAFT_RULES = """[소설 작법 규칙]
■ Show, Don't Tell: 감정을 직접 말하지 말고 행동/감각으로 보여주세요.
■ 감각 묘사: 시각, 청각, 후각, 촉각을 활용하세요.
■ 대화: "말했다"만 반복하지 말고 행동 묘사로 대체하세요.
■ 문장 리듬: 긴 묘사 → 짧은 액션 → 대화를 교차하세요.
■ 복선: 자연스럽게 심고, 충분한 임팩트로 회수하세요.
■ 장면 전환: 감각 묘사로 새 환경을 인지시키세요."""


# ---------------------------------------------------------------------------
# 초기화
# ---------------------------------------------------------------------------

def init_story(*, ollama_session, ollama_base, base_dir):
    global _ollama_session, _OLLAMA_BASE, _db_path
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _db_path = os.path.join(base_dir, "story.db")
    _init_story_db()


def _get_db():
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_story_db():
    conn = _get_db()
    try:
        # 스토리 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stories (
                id TEXT PRIMARY KEY,
                game_id TEXT,
                chain_id TEXT,
                parent_id TEXT,
                sequence_num INTEGER DEFAULT 1,
                title TEXT,
                content TEXT NOT NULL,
                content_summary TEXT,
                style TEXT DEFAULT 'novel',
                tone TEXT DEFAULT 'warm',
                pov TEXT DEFAULT 'third_person',
                word_count INTEGER DEFAULT 0,
                characters_json TEXT,
                world_context TEXT,
                custom_context TEXT,
                branch_suggestions TEXT,
                model TEXT,
                tokens INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stories_game ON stories(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stories_chain ON stories(chain_id, sequence_num)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stories_parent ON stories(parent_id)")

        # 스토리 체인 (시리즈)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_chains (
                id TEXT PRIMARY KEY,
                title TEXT,
                game_id TEXT,
                style TEXT DEFAULT 'novel',
                tone TEXT DEFAULT 'warm',
                pov TEXT DEFAULT 'third_person',
                world_context TEXT,
                characters_json TEXT,
                episode_count INTEGER DEFAULT 0,
                total_words INTEGER DEFAULT 0,
                last_story_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)

        # 스토리 요약 캐시
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_summaries (
                id TEXT PRIMARY KEY,
                story_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                summary_hash TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # 세계관
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_worlds (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                era TEXT,
                rules_json TEXT,
                geography TEXT,
                technology TEXT,
                tone_guide TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)

        # 인물
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_characters (
                id TEXT PRIMARY KEY,
                chain_id TEXT,
                world_id TEXT,
                name TEXT NOT NULL,
                tier TEXT DEFAULT 'supporting',
                type TEXT DEFAULT 'npc',
                profile_json TEXT,
                speech_matrix_json TEXT,
                current_state_json TEXT,
                state_history_json TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_char_chain ON story_characters(chain_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_char_world ON story_characters(world_id)")

        # 인물 관계
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain_id TEXT NOT NULL,
                from_character TEXT NOT NULL,
                to_character TEXT NOT NULL,
                type TEXT NOT NULL,
                state TEXT,
                history_json TEXT,
                tension TEXT,
                mutual INTEGER DEFAULT 0,
                episode_updated INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_chain ON story_relationships(chain_id)")

        # 복선
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_foreshadowing (
                id TEXT PRIMARY KEY,
                chain_id TEXT NOT NULL,
                planted_episode INTEGER,
                hint TEXT NOT NULL,
                secret TEXT,
                importance TEXT DEFAULT 'minor',
                resolved INTEGER DEFAULT 0,
                resolved_episode INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_foreshadow_chain ON story_foreshadowing(chain_id, resolved)")

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# LLM 호출 헬퍼
# ---------------------------------------------------------------------------

def _get_session():
    global _ollama_session, _OLLAMA_BASE
    if _ollama_session is None:
        _ollama_session = requests.Session()
        _OLLAMA_BASE = OLLAMA_BASE
    return _ollama_session, _OLLAMA_BASE


def _generate(model, prompt, system, options=None, timeout=None):
    """비스트림 생성 + CJK 표류 가드(v2.125.52) — raw가 한자·가나로 표류하면 강화 지시로 재생성.

    force_korean이 표류분을 걷어내 **뼈대만 200**으로 나가던 것(실측 30%)을 막는다. 반환 계약 불변.
    """
    from core.korean import generate_with_drift_guard
    return generate_with_drift_guard(
        lambda sys_prompt: _generate_once(model, prompt, sys_prompt, options, timeout),
        system, tag="[story]")


def _generate_once(model, prompt, system, options=None, timeout=None):
    """LLM 호출 (non-streaming)"""
    from core.llm_resilience import get_adaptive_timeout, ollama_breaker
    sess, base = _get_session()
    opts = options or {}

    if timeout is None:
        timeout = get_adaptive_timeout(model, "draft")

    if not ollama_breaker.can_execute():
        return "Error: Ollama temporarily unavailable", 0, 0

    start = time.time()
    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_chat_story_triage.py)
        # 종전 `raise_for_status()` 층 = `Result.ok`(HTTP≥400·전송 예외 모두 ok=False) → 예외 분기로.
        # 호출자 세션·base를 넘긴다 — 어댑터가 자기 세션을 만들면 ChatFairQueue 게이트를 우회한다.
        from core import model_router
        res = model_router.generate(
            "story.generate", prompt, model=model, system=system, options=opts,
            timeout=timeout, adapter=model_router.OllamaAdapter(base=base, session=sess))
        if not res.ok:
            from core.llm_resilience import result_error
            raise result_error(res, "generate failed")
        elapsed = int((time.time() - start) * 1000)
        response = res.text
        tokens = res.tokens
        ollama_breaker.record_success()
        return response, tokens, elapsed
    except Exception as e:
        # v2.125.68: 대기열 거절(busy)은 공용 브레이커 실패로 세지 않는다(core/llm_resilience.LLMBusyError)
        from core.llm_resilience import record_failure_unless_busy
        record_failure_unless_busy(e, ollama_breaker)
        logger.error(f"Story _generate error: {e}")
        return f"Error: {e}", 0, int((time.time() - start) * 1000)


def _generate_stream(model, prompt, system, options=None, timeout=None):
    """LLM SSE 스트리밍"""
    from core.llm_resilience import get_adaptive_timeout
    sess, base = _get_session()
    opts = options or {}
    if timeout is None:
        timeout = get_adaptive_timeout(model, "draft") + 60

    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_chat_story_triage.py)
    # `StreamChunk` 제너레이터를 돌려준다(text·done·tokens). 종전 `raise_for_status()`는
    # 어댑터가 첫 조각 전에 RuntimeError로 던진다(호출자 SSE의 error 이벤트 분기 그대로).
    from core import model_router
    return model_router.stream(
        "story.generate", prompt, model=model, system=system, options=opts,
        timeout=timeout, adapter=model_router.OllamaAdapter(base=base, session=sess))


# ---------------------------------------------------------------------------
# 컨텍스트 빌더 (5계층)
# ---------------------------------------------------------------------------

def _build_story_context(story_id=None, chain_id=None, characters=None,
                          world_context=None, custom_context=None, max_context_chars=12000):
    """기존 스토리를 LLM 컨텍스트로 구성 (5계층)"""
    context_parts = []

    # L1: 세계관
    if world_context:
        context_parts.append(f"[세계관]\n{world_context}")

    # L2: 등장인물
    if characters:
        if isinstance(characters, str):
            try:
                characters = json.loads(characters)
            except (json.JSONDecodeError, TypeError):
                characters = []
        if characters:
            char_desc = "\n".join(
                f"- {c['name']} ({c.get('role', '')}): {c.get('traits', '')}"
                + (f" | 현재: {c['current_state']}" if c.get('current_state') else "")
                for c in characters
            )
            context_parts.append(f"[등장인물]\n{char_desc}")

    # L3+L4: 이전 스토리
    stories = []
    if chain_id:
        stories = _load_chain_stories(chain_id)
    elif story_id:
        stories = _load_story_with_ancestors(story_id)

    if stories:
        total_chars = sum(len(s.get("content", "")) for s in stories)
        if total_chars > max_context_chars:
            # 이전 편 → 요약, 최근 2편 전문
            old_stories = stories[:-2]
            recent_stories = stories[-2:]
            if old_stories:
                summary = _get_or_create_summary(old_stories)
                context_parts.append(f"[이전 스토리 요약]\n{summary}")
            for s in recent_stories:
                label = s.get("title") or "직전 장면"
                content = s.get("content", "")[-4000:]  # 최대 4000자
                context_parts.append(f"[{label}]\n{content}")
        else:
            for s in stories:
                label = s.get("title") or "스토리"
                context_parts.append(f"[{label}]\n{s.get('content', '')}")

    # L5: 커스텀 컨텍스트
    if custom_context:
        context_parts.append(f"[추가 배경]\n{custom_context}")

    return "\n\n---\n\n".join(context_parts)


def _load_chain_stories(chain_id, limit=20):
    """체인 내 모든 스토리 로드 (시간순)"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, content, characters_json, sequence_num "
            "FROM stories WHERE chain_id=? ORDER BY sequence_num ASC LIMIT ?",
            (chain_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_story_with_ancestors(story_id, max_depth=10):
    """story_id → parent_id 체인 역추적 → 시간순 반환"""
    conn = _get_db()
    try:
        stories = []
        current_id = story_id
        for _ in range(max_depth):
            row = conn.execute(
                "SELECT id, title, content, parent_id, characters_json FROM stories WHERE id=?",
                (current_id,)
            ).fetchone()
            if not row:
                break
            stories.insert(0, dict(row))
            if not row["parent_id"]:
                break
            current_id = row["parent_id"]
        return stories
    finally:
        conn.close()


def _get_or_create_summary(stories, max_length=500):
    """여러 스토리 → 요약 (캐시)"""
    combined = "\n\n".join((s.get("content") or "")[:500] for s in stories)
    cache_key = hashlib.md5(combined[:300].encode()).hexdigest()

    conn = _get_db()
    try:
        cached = conn.execute(
            "SELECT summary FROM story_summaries WHERE summary_hash=?", (cache_key,)
        ).fetchone()
        if cached:
            return cached["summary"]
    finally:
        conn.close()

    # LLM으로 요약 생성
    prompt = f"""아래 스토리들을 300자 이내로 요약하세요.
핵심 사건, 인물 변화, 관계 변화, 복선을 보존하세요.

{combined}"""

    summary, _, _ = _generate(FAST_MODEL, prompt,
                               "소설 편집자입니다. 핵심만 간결하게 요약하세요.",
                               {"num_predict": 400})
    summary = force_korean(summary).strip()

    # 캐시 저장
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO story_summaries (id, story_id, summary, summary_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), stories[-1].get("id", ""), summary, cache_key,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    return summary


def _build_system_prompt(style, tone, pov, extra=""):
    """스타일+톤+시점을 조합한 시스템 프롬프트 구성"""
    parts = [NOVEL_CRAFT_RULES]

    style_cfg = STYLE_PROMPTS.get(style, STYLE_PROMPTS["novel"])
    parts.append(f"[문체] {style_cfg['prompt']}")

    tone_cfg = TONE_PROMPTS.get(tone, TONE_PROMPTS["warm"])
    parts.append(f"[톤] {tone_cfg['prompt']}")

    pov_prompt = POV_PROMPTS.get(pov, POV_PROMPTS["third_person"])
    parts.append(f"[시점] {pov_prompt}")

    parts.append("반드시 한국어로 작성하세요. 중국어/일본어를 절대 사용하지 마세요.")

    if extra:
        parts.append(extra)

    return "\n\n".join(parts)


def _save_story(conn, story_id, content, data, model, tokens, duration_ms,
                parent_id=None, chain_id=None, sequence_num=1, branch_suggestions=None):
    """스토리 DB 저장"""
    word_count = len(content)
    chars_json = json.dumps(data.get("characters"), ensure_ascii=False) if data.get("characters") else None

    conn.execute(
        "INSERT INTO stories (id, game_id, chain_id, parent_id, sequence_num, title, content, "
        "style, tone, pov, word_count, characters_json, world_context, custom_context, "
        "branch_suggestions, model, tokens, duration_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (story_id, data.get("game_id"), chain_id, parent_id, sequence_num,
         data.get("title"), content,
         data.get("style", "novel"), data.get("tone", "warm"), data.get("pov", "third_person"),
         word_count, chars_json, data.get("world_context"), data.get("custom_context"),
         json.dumps(branch_suggestions, ensure_ascii=False) if branch_suggestions else None,
         model, tokens, duration_ms,
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

    # 체인 업데이트
    if chain_id:
        conn.execute(
            "UPDATE story_chains SET episode_count = episode_count + 1, "
            "total_words = total_words + ?, last_story_id = ?, "
            "updated_at = ? WHERE id = ?",
            (word_count, story_id, datetime.now(timezone.utc).isoformat(), chain_id)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# API: 스토리 생성
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/generate", methods=["POST"])
def story_generate():
    """스토리/에피소드 생성
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string, description: "스토리 주제/방향"}
            style: {type: string, enum: [novel, game, script, fairy_tale, horror, romance, epic, noir, comedy], default: novel}
            tone: {type: string, enum: [melancholy, heroic, humorous, dark, warm, tense, dreamy, nostalgic, cynical], default: warm}
            pov: {type: string, enum: [first_person, third_person, omniscient], default: third_person}
            length: {type: string, enum: [short, medium, long, chapter], default: medium}
            characters: {type: array, items: {type: object}, description: "등장인물 배열"}
            world_context: {type: string, description: "세계관 설정"}
            custom_context: {type: string, description: "추가 배경"}
            story_id: {type: string, description: "이전 스토리 ID (컨텍스트)"}
            chain_id: {type: string, description: "시리즈 ID (컨텍스트)"}
            title: {type: string, description: "스토리 제목"}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        schema:
          type: object
          properties:
            story_id: {type: string}
            story: {type: string}
            metadata: {type: object}
            branch_suggestions: {type: array}
      502:
        description: "korean_generation_failed — 재생성 후에도 한국어 본문이 최소 길이(길이 설정의 20%) 미만(모델 언어 표류). retryable"
    """
    data = request.json or {}
    errors = validate(data, {
        "prompt": {"required": True, "type": "str", "max_length": 50000},
        "style": {"enum": list(STYLE_PROMPTS.keys()), "default": "novel"},
        "tone": {"enum": list(TONE_PROMPTS.keys()), "default": "warm"},
        "pov": {"enum": list(POV_PROMPTS.keys()), "default": "third_person"},
        "length": {"enum": list(LENGTH_CONFIG.keys()), "default": "medium"},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    prompt = data["prompt"].strip()
    style = data.get("style", "novel")
    tone = data.get("tone", "warm")
    pov = data.get("pov", "third_person")
    length = data.get("length", "medium")
    model = data.get("model") or STORY_MODEL

    length_cfg = LENGTH_CONFIG.get(length, LENGTH_CONFIG["medium"])

    # 컨텍스트 빌드
    context = _build_story_context(
        story_id=data.get("story_id"),
        chain_id=data.get("chain_id"),
        characters=data.get("characters"),
        world_context=data.get("world_context"),
        custom_context=data.get("custom_context"),
    )

    system = _build_system_prompt(style, tone, pov,
                                   f"약 {length_cfg['chars']}자로 작성하세요.")

    user_prompt = prompt
    if context:
        user_prompt = f"{context}\n\n---\n\n[요청]\n{prompt}"

    response, tokens, elapsed = _generate(model, user_prompt, system,
                                           {"num_predict": length_cfg["num_predict"]})
    response = force_korean(response).strip()

    if response.startswith("Error:"):
        return jsonify({"error": response}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(response, genre="essay")
    quality_metrics = get_quality_summary(quality_result)
    quality_metrics["retry_count"] = 0

    # risk_band=high → S1 규칙 삽입 후 1회 재생성
    if quality_result["risk_band"] == "high":
        logger.info(f"Story quality high-risk (score={quality_result['risk_score']}), regenerating with S1 rules")
        enhanced_system = system + "\n\n" + get_s1_prompt_rules()
        response2, tokens2, elapsed2 = _generate(model, user_prompt, enhanced_system,
                                                   {"num_predict": length_cfg["num_predict"]})
        response2 = force_korean(response2).strip()
        if not response2.startswith("Error:"):
            response = response2
            tokens += tokens2
            elapsed += elapsed2
            quality_result = compute_korean_quality(response, genre="essay")
            quality_metrics = get_quality_summary(quality_result)
            quality_metrics["retry_count"] = 1

    # v2.125.52: 표류 가드 후에도 한국어 필터가 본문을 뼈대만 남겼다면 200으로 내보내지 않는다
    # (종전: '…늦\n\n“”\n\n“”' 24자가 200+DB 저장됐다 — 반복 클래스 #1)
    min_chars = max(50, int(length_cfg["chars"] * 0.2))
    if len(response) < min_chars:
        logger.warning("[story] 한국어 후처리 후 본문 %d자 < %d — 502(저장 안 함)", len(response), min_chars)
        return jsonify({"error": "korean_generation_failed",
                        "detail": f"한국어 본문이 {len(response)}자뿐이다(최소 {min_chars}) — 모델 언어 표류",
                        "retryable": True}), 502

    # 분기 제안 생성 (간단 후처리)
    branch_suggestions = _generate_branch_suggestions(response, data.get("characters"))

    # DB 저장
    story_id = str(uuid.uuid4())
    conn = _get_db()
    try:
        _save_story(conn, story_id, response, data, model, tokens, elapsed,
                    parent_id=data.get("story_id"),
                    chain_id=data.get("chain_id"),
                    branch_suggestions=branch_suggestions)
    finally:
        conn.close()

    return jsonify({
        "story_id": story_id,
        "story": response,
        "metadata": {
            "style": style, "tone": tone, "pov": pov,
            "word_count": len(response),
            "characters_used": [c["name"] for c in (data.get("characters") or [])],
            "model": model, "tokens": tokens, "duration_ms": elapsed,
        },
        "quality_metrics": quality_metrics,
        "continuation_available": True,
        "branch_suggestions": branch_suggestions,
    })


def _generate_branch_suggestions(story_text, characters=None):
    """스토리에서 분기 제안 3개 추출 (경량 LLM)"""
    if not story_text or len(story_text) < 100:
        return []
    try:
        prompt = f"""아래 스토리의 다음 전개로 가능한 선택지 3개를 한 줄씩 작성하세요.
각 선택지는 20자 이내로 간결하게.

{story_text[-1000:]}"""

        result, _, _ = _generate(FAST_MODEL, prompt,
                                  "스토리 기획자입니다. 분기 선택지를 제안하세요.",
                                  {"num_predict": 150, "temperature": 0.8})
        result = force_korean(result).strip()
        lines = [force_korean(l.strip().lstrip("0123456789.-) ")) for l in result.split("\n") if l.strip()]
        return [l for l in lines if l][:3]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# API: 스토리 SSE 스트리밍
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/generate/stream", methods=["POST"])
def story_generate_stream():
    """스토리 SSE 스트리밍 생성
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string}
            style: {type: string, default: novel}
            tone: {type: string, default: warm}
            pov: {type: string, default: third_person}
            length: {type: string, default: medium}
            characters: {type: array}
            world_context: {type: string}
            model: {type: string}
    responses:
      200:
        description: SSE stream
    """
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    style = data.get("style", "novel")
    tone = data.get("tone", "warm")
    pov = data.get("pov", "third_person")
    length = data.get("length", "medium")
    model = data.get("model") or STORY_MODEL

    length_cfg = LENGTH_CONFIG.get(length, LENGTH_CONFIG["medium"])

    context = _build_story_context(
        story_id=data.get("story_id"),
        chain_id=data.get("chain_id"),
        characters=data.get("characters"),
        world_context=data.get("world_context"),
        custom_context=data.get("custom_context"),
    )

    system = _build_system_prompt(style, tone, pov,
                                   f"약 {length_cfg['chars']}자로 작성하세요.")

    user_prompt = prompt
    if context:
        user_prompt = f"{context}\n\n---\n\n[요청]\n{prompt}"

    def sse_gen():
        try:
            # v2.125.13 D1 이관 — 라우터 조각(text/done/tokens) 소비. SSE 이벤트 형태는 종전과 동일.
            full_text = []
            for chunk in _generate_stream(model, user_prompt, system,
                                          {"num_predict": length_cfg["num_predict"]}):
                token = force_korean_token(chunk.text)
                if token:
                    full_text.append(token)
                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                if chunk.done:
                    story_text = force_korean("".join(full_text))
                    story_id = str(uuid.uuid4())
                    # 비동기 저장 (필터링된 텍스트)
                    conn = _get_db()
                    try:
                        _save_story(conn, story_id, story_text, data, model,
                                    chunk.tokens, 0)
                    finally:
                        conn.close()
                    yield f"data: {json.dumps({'done': True, 'story_id': story_id, 'word_count': len(story_text)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(sse_gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# API: 이어쓰기
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/continue", methods=["POST"])
def story_continue():
    """이전 스토리 이어쓰기
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [story_id]
          properties:
            story_id: {type: string, description: "이전 스토리 ID"}
            direction: {type: string, description: "이어쓰기 방향/지시"}
            length: {type: string, default: medium}
            model: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    story_id = data.get("story_id", "").strip()
    if not story_id:
        return jsonify({"error": "story_id required"}), 400

    # 이전 스토리 로드
    conn = _get_db()
    try:
        prev = conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
    finally:
        conn.close()

    if not prev:
        return jsonify({"error": "story not found"}), 404

    prev = dict(prev)
    style = prev.get("style", "novel")
    tone = prev.get("tone", "warm")
    pov = prev.get("pov", "third_person")
    length = data.get("length", "medium")
    model = data.get("model") or prev.get("model") or STORY_MODEL
    length_cfg = LENGTH_CONFIG.get(length, LENGTH_CONFIG["medium"])

    # 컨텍스트 빌드 (이전 스토리 체인 자동 로드)
    characters = None
    if prev.get("characters_json"):
        try:
            characters = json.loads(prev["characters_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    context = _build_story_context(
        story_id=story_id,
        chain_id=prev.get("chain_id"),
        characters=characters,
        world_context=prev.get("world_context"),
        custom_context=prev.get("custom_context"),
    )

    direction = data.get("direction", "자연스럽게 이어서 계속 써주세요.")
    system = _build_system_prompt(style, tone, pov,
                                   f"약 {length_cfg['chars']}자로 이어쓰세요. "
                                   "이전 장면의 분위기와 문체를 유지하세요.")

    user_prompt = f"{context}\n\n---\n\n[이어쓰기 지시]\n{direction}"

    response, tokens, elapsed = _generate(model, user_prompt, system,
                                           {"num_predict": length_cfg["num_predict"]})
    response = force_korean(response).strip()

    if response.startswith("Error:"):
        return jsonify({"error": response}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(response, genre="essay")
    quality_metrics = get_quality_summary(quality_result)
    quality_metrics["retry_count"] = 0

    if quality_result["risk_band"] == "high":
        logger.info(f"Story continue quality high-risk (score={quality_result['risk_score']}), regenerating")
        enhanced_system = system + "\n\n" + get_s1_prompt_rules()
        response2, tokens2, elapsed2 = _generate(model, user_prompt, enhanced_system,
                                                   {"num_predict": length_cfg["num_predict"]})
        response2 = force_korean(response2).strip()
        if not response2.startswith("Error:"):
            response = response2
            tokens += tokens2
            elapsed += elapsed2
            quality_result = compute_korean_quality(response, genre="essay")
            quality_metrics = get_quality_summary(quality_result)
            quality_metrics["retry_count"] = 1

    branch_suggestions = _generate_branch_suggestions(response, characters)

    # 새 스토리 저장 (parent_id = 이전 스토리)
    new_id = str(uuid.uuid4())
    chain_id = prev.get("chain_id")

    # 체인이 없으면 자동 생성
    if not chain_id:
        chain_id = str(uuid.uuid4())
        conn = _get_db()
        try:
            conn.execute(
                "INSERT INTO story_chains (id, title, style, tone, pov, world_context, "
                "characters_json, episode_count, total_words, last_story_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (chain_id, prev.get("title", "무제 시리즈"), style, tone, pov,
                 prev.get("world_context"), prev.get("characters_json"),
                 prev.get("word_count", 0), story_id,
                 datetime.now(timezone.utc).isoformat())
            )
            # 원본 스토리에도 chain_id 부여
            conn.execute("UPDATE stories SET chain_id=?, sequence_num=1 WHERE id=?",
                         (chain_id, story_id))
            conn.commit()
        finally:
            conn.close()

    # 시퀀스 번호
    conn = _get_db()
    try:
        max_seq = conn.execute(
            "SELECT MAX(sequence_num) as m FROM stories WHERE chain_id=?", (chain_id,)
        ).fetchone()
        seq = (max_seq["m"] or 0) + 1

        cont_data = {
            "style": style, "tone": tone, "pov": pov,
            "characters": characters,
            "world_context": prev.get("world_context"),
            "custom_context": prev.get("custom_context"),
        }
        _save_story(conn, new_id, response, cont_data, model, tokens, elapsed,
                    parent_id=story_id, chain_id=chain_id, sequence_num=seq,
                    branch_suggestions=branch_suggestions)
    finally:
        conn.close()

    return jsonify({
        "story_id": new_id,
        "chain_id": chain_id,
        "sequence_num": seq,
        "story": response,
        "metadata": {
            "style": style, "tone": tone, "pov": pov,
            "word_count": len(response),
            "model": model, "tokens": tokens, "duration_ms": elapsed,
        },
        "quality_metrics": quality_metrics,
        "branch_suggestions": branch_suggestions,
    })


# ---------------------------------------------------------------------------
# API: 분기 생성
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/branch", methods=["POST"])
def story_branch():
    """선택지 분기 생성
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [story_id]
          properties:
            story_id: {type: string}
            branch_count: {type: integer, default: 3}
            branch_style: {type: string, enum: [dramatic, subtle, humorous, moral_dilemma], default: dramatic}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    story_id = data.get("story_id", "").strip()
    if not story_id:
        return jsonify({"error": "story_id required"}), 400

    conn = _get_db()
    try:
        story = conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
    finally:
        conn.close()

    if not story:
        return jsonify({"error": "story not found"}), 404

    story = dict(story)
    count = data.get("branch_count", 3)
    branch_style = data.get("branch_style", "dramatic")

    style_desc = {
        "dramatic": "극적이고 감정적 갈등이 있는",
        "subtle": "섬세하고 미묘한 차이가 있는",
        "humorous": "유머러스하고 예상치 못한",
        "moral_dilemma": "도덕적 딜레마가 있는",
    }.get(branch_style, "극적인")

    prompt = f"""아래 스토리의 다음 전개로 가능한 {style_desc} 선택지 {count}개를 생성하세요.

[스토리]
{story['content'][-2000:]}

각 선택지에 대해 다음 JSON 배열로 출력하세요:
[{{"id": "A", "choice_text": "선택 설명 (15자 이내)", "preview": "선택 시 전개 미리보기 (100자)", "tone_shift": "톤 변화", "risk": "high/medium/low"}}]

JSON만 출력하세요."""

    result, tokens, elapsed = _generate(STORY_MODEL, prompt,
                                         "스토리 기획자입니다. 분기 선택지를 JSON으로 생성하세요.",
                                         {"num_predict": 800, "temperature": 0.8})
    result = force_korean(result).strip()

    # JSON 파싱
    branches = []
    try:
        # JSON 블록 추출
        if "```" in result:
            result = result.split("```")[1].strip()
            if result.startswith("json"):
                result = result[4:].strip()
        branches = json.loads(result)
    except (json.JSONDecodeError, IndexError):
        # 파싱 실패 → 텍스트 분기
        lines = [l.strip() for l in result.split("\n") if l.strip()]
        for i, line in enumerate(lines[:count]):
            branches.append({
                "id": chr(65 + i),
                "choice_text": line[:30],
                "preview": line,
                "tone_shift": "",
                "risk": "medium",
            })

    return jsonify({
        "story_id": story_id,
        "branches": branches,
        "metadata": {"tokens": tokens, "duration_ms": elapsed},
    })


# ---------------------------------------------------------------------------
# API: 문체 변환 (Rewrite)
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/rewrite", methods=["POST"])
def story_rewrite():
    """문체/톤 변환
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "변환할 텍스트"}
            target_style: {type: string, default: novel}
            target_tone: {type: string, default: warm}
            expand: {type: boolean, default: true, description: "묘사 확장 여부"}
            model: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    target_style = data.get("target_style", "novel")
    target_tone = data.get("target_tone", "warm")
    expand = data.get("expand", True)
    model = data.get("model") or STORY_MODEL

    system = _build_system_prompt(target_style, target_tone, "third_person")

    action = "묘사를 확장하고 감각적으로 다시 작성하세요" if expand else "톤과 문체만 변환하세요. 분량은 유지하세요"
    prompt = f"""아래 텍스트를 지정된 문체와 톤으로 변환하세요.
{action}.

[원문]
{text}"""

    num_predict = len(text) * 3 if expand else len(text) * 2
    num_predict = min(max(num_predict, 500), 6000)

    response, tokens, elapsed = _generate(model, prompt, system,
                                           {"num_predict": num_predict})
    response = force_korean(response).strip()

    return jsonify({
        "original": text,
        "rewritten": response,
        "target_style": target_style,
        "target_tone": target_tone,
        "expanded": expand,
        "metadata": {"tokens": tokens, "duration_ms": elapsed, "model": model},
    })


# ---------------------------------------------------------------------------
# API: 대화 장면 생성
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/dialogue/scene", methods=["POST"])
def story_dialogue_scene():
    """대화 장면 생성 (지문 + 대사)
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [characters, situation]
          properties:
            characters: {type: array, items: {type: object}}
            situation: {type: string}
            style: {type: string, default: novel}
            include_narration: {type: boolean, default: true}
            story_id: {type: string, description: "컨텍스트용 이전 스토리"}
            model: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    characters = data.get("characters", [])
    situation = data.get("situation", "").strip()
    if not characters or not situation:
        return jsonify({"error": "characters and situation required"}), 400

    style = data.get("style", "novel")
    include_narration = data.get("include_narration", True)
    model = data.get("model") or STORY_MODEL

    # 인물별 말투 정보 포함
    char_desc = "\n".join(
        f"- {c['name']}: 감정={c.get('emotion', '보통')}, "
        f"말투={c.get('speech_style', '기본')}"
        for c in characters
    )

    narr_inst = "지문(행동/표정/분위기 묘사)을 풍부하게 포함하세요." if include_narration else "대사만 작성하세요."

    # 컨텍스트
    context = ""
    if data.get("story_id"):
        context = _build_story_context(story_id=data.get("story_id"))

    prompt = f"""{context}

[등장인물]
{char_desc}

[상황]
{situation}

{narr_inst}
대화 장면을 작성하세요."""

    system = _build_system_prompt(style, "tense", "third_person",
                                   "대화 전문가입니다. 인물별 말투 차이를 확실히 구분하세요. "
                                   "\"말했다\"를 반복하지 말고 행동 묘사로 대체하세요.")

    response, tokens, elapsed = _generate(model, prompt, system,
                                           {"num_predict": 2000})
    response = force_korean(response).strip()

    return jsonify({
        "scene": response,
        "characters": [c["name"] for c in characters],
        "situation": situation,
        "metadata": {"tokens": tokens, "duration_ms": elapsed, "model": model},
    })


# ---------------------------------------------------------------------------
# API: 세계관 CRUD
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/world/create", methods=["POST"])
def story_world_create():
    """세계관 등록
    ---
    tags: [Story Engine - World]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name]
          properties:
            name: {type: string}
            era: {type: string}
            rules: {type: array}
            geography: {type: string}
            technology: {type: string}
            tone_guide: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    world_id = str(uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO story_worlds (id, name, era, rules_json, geography, technology, tone_guide, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (world_id, name, data.get("era"),
             json.dumps(data.get("rules", []), ensure_ascii=False),
             data.get("geography"), data.get("technology"), data.get("tone_guide"),
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"world_id": world_id, "name": name})


@story_bp.route("/api/story/world/<world_id>", methods=["GET"])
def story_world_get(world_id):
    """세계관 조회
    ---
    tags: [Story Engine - World]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: world_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    _ck = f"world:{world_id}"
    _cached = _story_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM story_worlds WHERE id=?", (world_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "world not found"}), 404

    d = dict(row)
    if d.get("rules_json"):
        try:
            d["rules"] = json.loads(d["rules_json"])
        except (json.JSONDecodeError, TypeError):
            d["rules"] = []
    _story_cache.set(_ck, d)
    return jsonify(d)


@story_bp.route("/api/story/world/<world_id>", methods=["PUT"])
def story_world_update(world_id):
    """세계관 수정
    ---
    tags: [Story Engine - World]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: world_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    conn = _get_db()
    try:
        existing = conn.execute("SELECT id FROM story_worlds WHERE id=?", (world_id,)).fetchone()
        if not existing:
            return jsonify({"error": "world not found"}), 404

        fields = []
        values = []
        for key in ("name", "era", "geography", "technology", "tone_guide"):
            if key in data:
                fields.append(f"{key}=?")
                values.append(data[key])
        if "rules" in data:
            fields.append("rules_json=?")
            values.append(json.dumps(data["rules"], ensure_ascii=False))
        if fields:
            fields.append("updated_at=?")
            values.append(datetime.now(timezone.utc).isoformat())
            values.append(world_id)
            conn.execute(f"UPDATE story_worlds SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "world_id": world_id})


# ---------------------------------------------------------------------------
# API: 인물 CRUD
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/character/create", methods=["POST"])
def story_character_create():
    """인물 프로필 등록
    ---
    tags: [Story Engine - Character]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name]
          properties:
            name: {type: string}
            chain_id: {type: string}
            world_id: {type: string}
            tier: {type: string, enum: [main, supporting, minor, deceased, hidden], default: supporting}
            type: {type: string, enum: [protagonist, heroine, antagonist, mentor, npc, deceased], default: npc}
            profile: {type: object, description: "나이, 성격, 외모 등"}
            speech_matrix: {type: object, description: "상대별 말투"}
            current_state: {type: object, description: "현재 감정/위치/관계"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    char_id = str(uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO story_characters (id, chain_id, world_id, name, tier, type, "
            "profile_json, speech_matrix_json, current_state_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (char_id, data.get("chain_id"), data.get("world_id"), name,
             data.get("tier", "supporting"), data.get("type", "npc"),
             json.dumps(data.get("profile", {}), ensure_ascii=False),
             json.dumps(data.get("speech_matrix", {}), ensure_ascii=False),
             json.dumps(data.get("current_state", {}), ensure_ascii=False),
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"character_id": char_id, "name": name})


@story_bp.route("/api/story/character/<char_id>", methods=["GET"])
def story_character_get(char_id):
    """인물 조회
    ---
    tags: [Story Engine - Character]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: char_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM story_characters WHERE id=?", (char_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "character not found"}), 404

    d = dict(row)
    for key in ("profile_json", "speech_matrix_json", "current_state_json", "state_history_json"):
        if d.get(key):
            try:
                d[key.replace("_json", "")] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return jsonify(d)


@story_bp.route("/api/story/character/<char_id>/state", methods=["PUT"])
def story_character_update_state(char_id):
    """인물 상태 업데이트
    ---
    tags: [Story Engine - Character]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: char_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            current_state: {type: object}
            append_history: {type: boolean, default: true}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    new_state = data.get("current_state", {})
    if not new_state:
        return jsonify({"error": "current_state required"}), 400

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM story_characters WHERE id=?", (char_id,)).fetchone()
        if not row:
            return jsonify({"error": "character not found"}), 404

        # 이력 추가
        if data.get("append_history", True):
            history = []
            if row["state_history_json"]:
                try:
                    history = json.loads(row["state_history_json"])
                except (json.JSONDecodeError, TypeError):
                    history = []
            history.append(new_state)
            conn.execute(
                "UPDATE story_characters SET current_state_json=?, state_history_json=?, updated_at=? WHERE id=?",
                (json.dumps(new_state, ensure_ascii=False),
                 json.dumps(history, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(), char_id)
            )
        else:
            conn.execute(
                "UPDATE story_characters SET current_state_json=?, updated_at=? WHERE id=?",
                (json.dumps(new_state, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(), char_id)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "character_id": char_id})


@story_bp.route("/api/story/characters", methods=["GET"])
def story_characters_list():
    """인물 목록 (chain_id 또는 world_id 필터)
    ---
    tags: [Story Engine - Character]
    security: [{Bearer: []}]
    parameters:
      - {in: query, name: chain_id, type: string}
      - {in: query, name: world_id, type: string}
      - {in: query, name: tier, type: string}
    responses:
      200:
        schema:
          type: object
    """
    chain_id = request.args.get("chain_id")
    world_id = request.args.get("world_id")
    tier = request.args.get("tier")

    _ck = f"chars:{chain_id}:{world_id}:{tier}"
    _cached = _story_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    conditions = []
    params = []
    if chain_id:
        conditions.append("chain_id=?")
        params.append(chain_id)
    if world_id:
        conditions.append("world_id=?")
        params.append(world_id)
    if tier:
        conditions.append("tier=?")
        params.append(tier)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = _get_db()
    try:
        rows = conn.execute(
            f"SELECT id, name, tier, type, chain_id, world_id, current_state_json, created_at "
            f"FROM story_characters{where} ORDER BY created_at DESC LIMIT 100", params
        ).fetchall()
    finally:
        conn.close()

    characters = []
    for r in rows:
        d = dict(r)
        if d.get("current_state_json"):
            try:
                d["current_state"] = json.loads(d["current_state_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        characters.append(d)

    resp = {"characters": characters, "count": len(characters)}
    _story_cache.set(_ck, resp)
    return jsonify(resp)


# ---------------------------------------------------------------------------
# API: 스타일/톤 목록
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/styles", methods=["GET"])
def story_styles():
    """사용 가능한 스타일/톤/시점/길이 목록
    ---
    tags: [Story Engine]
    responses:
      200:
        schema:
          type: object
    """
    _cached = _story_cache.get("styles")
    if _cached is not None:
        return jsonify(_cached)
    resp = {
        "styles": {k: {"name": v["name"], "desc": v["desc"]} for k, v in STYLE_PROMPTS.items()},
        "tones": {k: v["name"] for k, v in TONE_PROMPTS.items()},
        "povs": {
            "first_person": "1인칭",
            "third_person": "3인칭 제한",
            "omniscient": "전지적",
        },
        "lengths": {k: v["desc"] for k, v in LENGTH_CONFIG.items()},
        "combinations": f"{len(STYLE_PROMPTS)} styles × {len(TONE_PROMPTS)} tones = {len(STYLE_PROMPTS) * len(TONE_PROMPTS)} combinations",
    }
    _story_cache.set("styles", resp)
    return jsonify(resp)


# ---------------------------------------------------------------------------
# API: 생성 이력
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/history", methods=["GET"])
def story_history():
    """스토리 생성 이력
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - {in: query, name: chain_id, type: string}
      - {in: query, name: style, type: string}
      - {in: query, name: limit, type: integer, default: 50}
    responses:
      200:
        schema:
          type: object
    """
    chain_id = request.args.get("chain_id")
    style = request.args.get("style")
    limit = min(int(request.args.get("limit", 50)), 200)

    conditions = []
    params = []
    if chain_id:
        conditions.append("chain_id=?")
        params.append(chain_id)
    if style:
        conditions.append("style=?")
        params.append(style)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    conn = _get_db()
    try:
        rows = conn.execute(
            f"SELECT id, chain_id, parent_id, sequence_num, title, style, tone, pov, "
            f"word_count, model, tokens, duration_ms, created_at "
            f"FROM stories{where} ORDER BY created_at DESC LIMIT ?", params
        ).fetchall()
    finally:
        conn.close()

    return jsonify({"stories": [dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# API: 스토리 조회
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/<story_id>", methods=["GET"])
def story_get(story_id):
    """스토리 상세 조회
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: story_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "story not found"}), 404

    d = dict(row)
    for key in ("characters_json", "branch_suggestions"):
        if d.get(key):
            try:
                d[key.replace("_json", "").replace("branch_suggestions", "branches")] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return jsonify(d)


# ---------------------------------------------------------------------------
# API: 체인(시리즈) 관리
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/chains", methods=["GET"])
def story_chains_list():
    """스토리 체인(시리즈) 목록
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - {in: query, name: limit, type: integer, default: 50}
    responses:
      200:
        schema:
          type: object
    """
    limit = min(int(request.args.get("limit", 50)), 200)

    _ck = f"chains:{limit}"
    _cached = _story_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM story_chains ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    finally:
        conn.close()

    chains = []
    for r in rows:
        d = dict(r)
        if d.get("characters_json"):
            try:
                d["characters"] = json.loads(d["characters_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        chains.append(d)

    resp = {"chains": chains, "count": len(chains)}
    _story_cache.set(_ck, resp)
    return jsonify(resp)


@story_bp.route("/api/story/chain/<chain_id>", methods=["GET"])
def story_chain_get(chain_id):
    """체인 상세 + 에피소드 목록
    ---
    tags: [Story Engine]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chain_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    _ck = f"chain:{chain_id}"
    _cached = _story_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    conn = _get_db()
    try:
        chain = conn.execute("SELECT * FROM story_chains WHERE id=?", (chain_id,)).fetchone()
        if not chain:
            return jsonify({"error": "chain not found"}), 404

        episodes = conn.execute(
            "SELECT id, sequence_num, title, style, tone, word_count, created_at "
            "FROM stories WHERE chain_id=? ORDER BY sequence_num ASC",
            (chain_id,)
        ).fetchall()
    finally:
        conn.close()

    d = dict(chain)
    if d.get("characters_json"):
        try:
            d["characters"] = json.loads(d["characters_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    d["episodes"] = [dict(e) for e in episodes]
    _story_cache.set(_ck, d)
    return jsonify(d)


# ---------------------------------------------------------------------------
# API: 소설 아웃라인 + 챕터 (기본)
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/novel/outline", methods=["POST"])
def story_novel_outline():
    """소설 전체 구조 설계
    ---
    tags: [Story Engine - Novel]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [premise]
          properties:
            premise: {type: string, description: "소설 전제/컨셉"}
            genre: {type: string, default: fantasy}
            chapters: {type: integer, default: 10}
            themes: {type: array, items: {type: string}}
            structure: {type: string, enum: [three_act, hero_journey, kishoten], default: three_act}
            model: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    premise = data.get("premise", "").strip()
    if not premise:
        return jsonify({"error": "premise required"}), 400

    genre = data.get("genre", "fantasy")
    chapters = data.get("chapters", 10)
    themes = data.get("themes", [])
    structure = data.get("structure", "three_act")
    model = data.get("model") or STORY_MODEL

    themes_str = ", ".join(themes) if themes else "자유"

    prompt = f"""소설 전체 구조를 설계하세요.

[전제] {premise}
[장르] {genre}
[챕터 수] {chapters}
[테마] {themes_str}
[구조] {structure}

다음 JSON 형식으로 출력:
{{
  "title_suggestions": ["제목 후보 3개"],
  "logline": "한 줄 요약",
  "chapters": [
    {{"chapter": 1, "title": "챕터 제목", "synopsis": "줄거리 (100자)", "key_events": ["핵심 사건"]}}
  ],
  "character_suggestions": [{{"name": "이름", "role": "역할", "traits": "성격"}}],
  "world_setting": "세계관 요약",
  "foreshadowing_plan": [{{"plant_chapter": 1, "resolve_chapter": 8, "hint": "복선 내용"}}]
}}

JSON만 출력하세요."""

    system = (f"소설 기획자입니다. {genre} 장르 전문가로서 "
              f"{structure} 구조에 맞춰 설계하세요. 한국어로 작성하세요.")

    response, tokens, elapsed = _generate(model, prompt, system,
                                           {"num_predict": 4000, "temperature": 0.7})
    response = force_korean(response).strip()

    # JSON 파싱
    result = {}
    try:
        if "```" in response:
            response = response.split("```")[1].strip()
            if response.startswith("json"):
                response = response[4:].strip()
        result = json.loads(response)
    except (json.JSONDecodeError, IndexError):
        result = {"raw_output": response}

    result["metadata"] = {"tokens": tokens, "duration_ms": elapsed, "model": model}
    return jsonify(result)


@story_bp.route("/api/story/novel/chapter", methods=["POST"])
def story_novel_chapter():
    """소설 챕터 생성
    ---
    tags: [Story Engine - Novel]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [synopsis]
          properties:
            synopsis: {type: string, description: "챕터 줄거리/지시"}
            chapter_num: {type: integer, default: 1}
            characters: {type: array}
            world_context: {type: string}
            story_id: {type: string, description: "이전 챕터 story_id"}
            chain_id: {type: string}
            style: {type: string, default: novel}
            tone: {type: string, default: warm}
            pov: {type: string, default: third_person}
            model: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    synopsis = data.get("synopsis", "").strip()
    if not synopsis:
        return jsonify({"error": "synopsis required"}), 400

    style = data.get("style", "novel")
    tone = data.get("tone", "warm")
    pov = data.get("pov", "third_person")
    model = data.get("model") or STORY_MODEL
    chapter_num = data.get("chapter_num", 1)

    context = _build_story_context(
        story_id=data.get("story_id"),
        chain_id=data.get("chain_id"),
        characters=data.get("characters"),
        world_context=data.get("world_context"),
        custom_context=data.get("custom_context"),
    )

    system = _build_system_prompt(style, tone, pov,
                                   f"{NOVEL_CRAFT_RULES}\n\n약 5000자로 작성하세요. "
                                   "장면 3개 이상, 대화:묘사:내면 비율 3:4:3.")

    prompt = f"""{context}

[챕터 {chapter_num} 지시]
{synopsis}

이 챕터를 소설 수준으로 작성하세요."""

    response, tokens, elapsed = _generate(model, prompt, system,
                                           {"num_predict": 6000})
    response = force_korean(response).strip()

    if response.startswith("Error:"):
        return jsonify({"error": response}), 500

    # DB 저장
    story_id = str(uuid.uuid4())
    save_data = {
        "style": style, "tone": tone, "pov": pov,
        "title": f"Chapter {chapter_num}",
        "characters": data.get("characters"),
        "world_context": data.get("world_context"),
    }
    conn = _get_db()
    try:
        _save_story(conn, story_id, response, save_data, model, tokens, elapsed,
                    parent_id=data.get("story_id"),
                    chain_id=data.get("chain_id"),
                    sequence_num=chapter_num)
    finally:
        conn.close()

    return jsonify({
        "story_id": story_id,
        "chapter_num": chapter_num,
        "content": response,
        "metadata": {
            "word_count": len(response),
            "tokens": tokens, "duration_ms": elapsed, "model": model,
        },
    })


# ---------------------------------------------------------------------------
# API: 세계관 기반 스토리 확장
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/world/expand", methods=["POST"])
def story_world_expand():
    """세계관 기반 스토리 확장
    ---
    tags: [Story Engine - World]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [world_id, prompt]
          properties:
            world_id: {type: string}
            prompt: {type: string}
            style: {type: string, default: novel}
            model: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    world_id = data.get("world_id", "").strip()
    prompt = data.get("prompt", "").strip()
    if not world_id or not prompt:
        return jsonify({"error": "world_id and prompt required"}), 400

    conn = _get_db()
    try:
        world = conn.execute("SELECT * FROM story_worlds WHERE id=?", (world_id,)).fetchone()
    finally:
        conn.close()

    if not world:
        return jsonify({"error": "world not found"}), 404

    world = dict(world)
    world_context = f"세계: {world['name']}\n시대: {world.get('era', '')}\n지리: {world.get('geography', '')}\n기술: {world.get('technology', '')}"
    if world.get("rules_json"):
        try:
            rules = json.loads(world["rules_json"])
            world_context += "\n규칙:\n" + "\n".join(f"- {r.get('rule', r) if isinstance(r, dict) else r}" for r in rules)
        except (json.JSONDecodeError, TypeError):
            pass

    style = data.get("style", "novel")
    model = data.get("model") or STORY_MODEL

    system = _build_system_prompt(style, "warm", "third_person",
                                   "세계관을 기반으로 스토리를 확장하세요. "
                                   "세계관 규칙을 절대 위반하지 마세요.")

    user_prompt = f"[세계관]\n{world_context}\n\n[요청]\n{prompt}"

    response, tokens, elapsed = _generate(model, user_prompt, system,
                                           {"num_predict": 3000})
    response = force_korean(response).strip()

    return jsonify({
        "story": response,
        "world_id": world_id,
        "world_name": world["name"],
        "metadata": {"tokens": tokens, "duration_ms": elapsed, "model": model},
    })


# ---------------------------------------------------------------------------
# API: 인물 관계 CRUD
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/relationship/set", methods=["POST"])
def story_relationship_set():
    """인물 간 관계 등록/수정
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [chain_id, from_character, to_character, type]
          properties:
            chain_id: {type: string}
            from_character: {type: string}
            to_character: {type: string}
            type: {type: string, enum: [romantic, comrade, rival, mentor, family, acquaintance, hostile]}
            state: {type: string}
            tension: {type: string}
            mutual: {type: boolean, default: false}
            history: {type: array, items: {type: string}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    chain_id = data.get("chain_id", "").strip()
    from_char = data.get("from_character", "").strip()
    to_char = data.get("to_character", "").strip()
    rel_type = data.get("type", "").strip()

    if not all([chain_id, from_char, to_char, rel_type]):
        return jsonify({"error": "chain_id, from_character, to_character, type required"}), 400

    conn = _get_db()
    try:
        # 기존 관계 확인
        existing = conn.execute(
            "SELECT id FROM story_relationships WHERE chain_id=? AND from_character=? AND to_character=?",
            (chain_id, from_char, to_char)
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()
        history_json = json.dumps(data.get("history", []), ensure_ascii=False)
        mutual = 1 if data.get("mutual", False) else 0

        if existing:
            conn.execute(
                "UPDATE story_relationships SET type=?, state=?, tension=?, mutual=?, "
                "history_json=?, updated_at=? WHERE id=?",
                (rel_type, data.get("state"), data.get("tension"),
                 mutual, history_json, now, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO story_relationships (chain_id, from_character, to_character, type, "
                "state, history_json, tension, mutual, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chain_id, from_char, to_char, rel_type,
                 data.get("state"), history_json, data.get("tension"), mutual, now)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "from": from_char, "to": to_char, "type": rel_type})


@story_bp.route("/api/story/chain/<chain_id>/relationship-map", methods=["GET"])
def story_relationship_map(chain_id):
    """전체 관계도 그래프 조회
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chain_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        # 인물 노드
        chars = conn.execute(
            "SELECT id, name, tier, type, current_state_json FROM story_characters WHERE chain_id=?",
            (chain_id,)
        ).fetchall()

        # 관계 엣지
        rels = conn.execute(
            "SELECT from_character, to_character, type, state, tension, mutual "
            "FROM story_relationships WHERE chain_id=?",
            (chain_id,)
        ).fetchall()

        # 세력
        factions = conn.execute(
            "SELECT * FROM story_factions WHERE chain_id=?",
            (chain_id,)
        ).fetchall()
    except sqlite3.OperationalError:
        # story_factions 테이블 없을 수 있음
        factions = []
    finally:
        conn.close()

    nodes = []
    for c in chars:
        d = {"id": c["name"], "tier": c["tier"], "type": c["type"]}
        if c["current_state_json"]:
            try:
                state = json.loads(c["current_state_json"])
                d["emotion"] = state.get("emotion", "")
            except (json.JSONDecodeError, TypeError):
                pass
        nodes.append(d)

    edges = [{"from": r["from_character"], "to": r["to_character"],
              "type": r["type"], "state": r["state"], "tension": r["tension"],
              "mutual": bool(r["mutual"])} for r in rels]

    faction_list = []
    for f in factions:
        fd = dict(f)
        for key in ("members_json", "attitudes_json"):
            if fd.get(key):
                try:
                    fd[key.replace("_json", "")] = json.loads(fd[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        faction_list.append(fd)

    return jsonify({"nodes": nodes, "edges": edges, "factions": faction_list})


@story_bp.route("/api/story/chain/<chain_id>/relationship-update", methods=["POST"])
def story_relationship_auto_update(chain_id):
    """에피소드 후 관계 자동 갱신 (LLM 분석)
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chain_id, type: string, required: true}
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [story_id]
          properties:
            story_id: {type: string, description: "분석할 에피소드 ID"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    story_id = data.get("story_id", "").strip()
    if not story_id:
        return jsonify({"error": "story_id required"}), 400

    conn = _get_db()
    try:
        story = conn.execute("SELECT content FROM stories WHERE id=?", (story_id,)).fetchone()
        if not story:
            return jsonify({"error": "story not found"}), 404

        rels = conn.execute(
            "SELECT from_character, to_character, type, state FROM story_relationships WHERE chain_id=?",
            (chain_id,)
        ).fetchall()
    finally:
        conn.close()

    current_rels = "\n".join(f"- {r['from_character']} → {r['to_character']}: {r['type']}, {r['state']}"
                             for r in rels)

    prompt = f"""아래 에피소드를 읽고 인물 간 관계 변화를 JSON으로 출력하세요.

[기존 관계]
{current_rels}

[새 에피소드]
{story['content'][-3000:]}

변화된 관계만 출력:
[{{"from": "인물A", "to": "인물B", "previous_state": "이전", "new_state": "변경", "cause": "원인"}}]

변화 없으면 빈 배열 [] 출력. JSON만 출력."""

    result, tokens, elapsed = _generate(FAST_MODEL, prompt,
                                         "소설 편집자입니다. 관계 변화를 정확히 분석하세요.",
                                         {"num_predict": 500})
    result = force_korean(result).strip()

    changes = []
    try:
        if "```" in result:
            result = result.split("```")[1].strip()
            if result.startswith("json"):
                result = result[4:].strip()
        changes = json.loads(result)
    except (json.JSONDecodeError, IndexError):
        pass

    # 관계 업데이트 적용
    if changes:
        conn = _get_db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            for change in changes:
                conn.execute(
                    "UPDATE story_relationships SET state=?, updated_at=? "
                    "WHERE chain_id=? AND from_character=? AND to_character=?",
                    (change.get("new_state"), now, chain_id,
                     change.get("from"), change.get("to"))
                )
            conn.commit()
        finally:
            conn.close()

    return jsonify({
        "changes": changes,
        "updated": len(changes),
        "metadata": {"tokens": tokens, "duration_ms": elapsed},
    })


# ---------------------------------------------------------------------------
# API: 복선/떡밥 관리
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/foreshadowing/add", methods=["POST"])
def story_foreshadowing_add():
    """복선 수동 등록
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [chain_id, hint]
          properties:
            chain_id: {type: string}
            hint: {type: string, description: "복선 내용"}
            secret: {type: string, description: "숨겨진 진실"}
            planted_episode: {type: integer}
            importance: {type: string, enum: [major, minor], default: minor}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    chain_id = data.get("chain_id", "").strip()
    hint = data.get("hint", "").strip()
    if not chain_id or not hint:
        return jsonify({"error": "chain_id and hint required"}), 400

    fs_id = str(uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO story_foreshadowing (id, chain_id, planted_episode, hint, secret, "
            "importance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fs_id, chain_id, data.get("planted_episode"),
             hint, data.get("secret"), data.get("importance", "minor"),
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"foreshadowing_id": fs_id, "hint": hint})


@story_bp.route("/api/story/foreshadowing/detect", methods=["POST"])
def story_foreshadowing_detect():
    """복선 자동 감지 (LLM)
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [story_id]
          properties:
            story_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    story_id = data.get("story_id", "").strip()
    if not story_id:
        return jsonify({"error": "story_id required"}), 400

    conn = _get_db()
    try:
        story = conn.execute("SELECT content, chain_id FROM stories WHERE id=?", (story_id,)).fetchone()
    finally:
        conn.close()

    if not story:
        return jsonify({"error": "story not found"}), 404

    prompt = f"""아래 스토리에서 아직 해결되지 않은 복선/떡밥을 추출하세요:
- 언급되었지만 설명되지 않은 사건
- 인물의 비밀 또는 숨긴 감정
- 의미심장한 물건/장소/대사
- 향후 전개에 영향을 줄 수 있는 요소

[스토리]
{story['content'][-3000:]}

JSON 배열로 출력:
[{{"hint": "복선 내용", "importance": "major 또는 minor", "potential_resolution": "가능한 전개"}}]

JSON만 출력."""

    result, tokens, elapsed = _generate(STORY_MODEL, prompt,
                                         "소설 편집자입니다. 복선과 떡밥을 정확히 찾아내세요.",
                                         {"num_predict": 600})
    result = force_korean(result).strip()

    detected = []
    try:
        if "```" in result:
            result = result.split("```")[1].strip()
            if result.startswith("json"):
                result = result[4:].strip()
        detected = json.loads(result)
    except (json.JSONDecodeError, IndexError):
        pass

    # 자동 등록 (chain_id 있으면)
    saved = 0
    if detected and story.get("chain_id"):
        conn = _get_db()
        try:
            for fs in detected:
                conn.execute(
                    "INSERT INTO story_foreshadowing (id, chain_id, hint, importance, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), story["chain_id"],
                     fs.get("hint", ""), fs.get("importance", "minor"),
                     datetime.now(timezone.utc).isoformat())
                )
                saved += 1
            conn.commit()
        finally:
            conn.close()

    return jsonify({
        "detected": detected,
        "saved": saved,
        "metadata": {"tokens": tokens, "duration_ms": elapsed},
    })


@story_bp.route("/api/story/foreshadowing/<fs_id>/resolve", methods=["PUT"])
def story_foreshadowing_resolve(fs_id):
    """복선 해소 표시
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: fs_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            resolved_episode: {type: integer}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE story_foreshadowing SET resolved=1, resolved_episode=? WHERE id=?",
            (data.get("resolved_episode"), fs_id)
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "foreshadowing_id": fs_id, "resolved": True})


@story_bp.route("/api/story/chain/<chain_id>/foreshadowing", methods=["GET"])
def story_foreshadowing_list(chain_id):
    """시리즈 복선 목록
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chain_id, type: string, required: true}
      - {in: query, name: resolved, type: string, description: "all/resolved/unresolved"}
    responses:
      200:
        schema:
          type: object
    """
    resolved_filter = request.args.get("resolved", "all")

    conn = _get_db()
    try:
        if resolved_filter == "resolved":
            rows = conn.execute(
                "SELECT * FROM story_foreshadowing WHERE chain_id=? AND resolved=1 ORDER BY planted_episode",
                (chain_id,)
            ).fetchall()
        elif resolved_filter == "unresolved":
            rows = conn.execute(
                "SELECT * FROM story_foreshadowing WHERE chain_id=? AND resolved=0 ORDER BY planted_episode",
                (chain_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM story_foreshadowing WHERE chain_id=? ORDER BY resolved, planted_episode",
                (chain_id,)
            ).fetchall()
    finally:
        conn.close()

    items = [dict(r) for r in rows]
    return jsonify({
        "foreshadowing": items,
        "total": len(items),
        "unresolved": sum(1 for i in items if not i.get("resolved")),
    })


# ---------------------------------------------------------------------------
# API: 일관성 검증
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/validate", methods=["POST"])
def story_validate():
    """스토리 일관성 검증 (세계관/인물/관계 위반 검사)
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [story_id]
          properties:
            story_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    story_id = data.get("story_id", "").strip()
    if not story_id:
        return jsonify({"error": "story_id required"}), 400

    conn = _get_db()
    try:
        story = conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
        if not story:
            return jsonify({"error": "story not found"}), 404
        story = dict(story)

        # 이전 스토리 컨텍스트 수집
        prev_context = ""
        if story.get("parent_id"):
            prev = conn.execute("SELECT content, characters_json FROM stories WHERE id=?",
                                (story["parent_id"],)).fetchone()
            if prev:
                prev_context = f"[이전 에피소드 (발췌)]\n{prev['content'][-1500:]}"
                if prev.get("characters_json"):
                    prev_context += f"\n\n[이전 인물 상태]\n{prev['characters_json']}"

        # 세계관 규칙 (world_context)
        world_rules = story.get("world_context", "")
    finally:
        conn.close()

    prompt = f"""아래 스토리의 일관성을 검증하세요.

{prev_context}

[현재 에피소드]
{story['content'][:4000]}

[세계관 규칙]
{world_rules or '(없음)'}

[등장인물]
{story.get('characters_json', '(없음)')}

검사 항목:
1. 세계관 위반 (규칙과 모순되는 내용)
2. 인물 모순 (이전 상태와 불일치 — 부상, 위치, 소지품 등)
3. 관계 모순 (적대 관계인데 친밀, 등)
4. 말투 위반 (인물별 어투 불일치)
5. 시간/위치 모순

JSON으로 출력:
{{
  "valid": true/false,
  "issues": [
    {{"type": "카테고리", "severity": "error/warning", "detail": "설명", "suggestion": "수정 제안"}}
  ]
}}

문제 없으면 {{"valid": true, "issues": []}}. JSON만 출력."""

    result, tokens, elapsed = _generate(STORY_MODEL, prompt,
                                         "소설 편집자입니다. 일관성 오류를 꼼꼼히 찾아내세요.",
                                         {"num_predict": 800})
    result = force_korean(result).strip()

    validation = {"valid": True, "issues": []}
    try:
        if "```" in result:
            result = result.split("```")[1].strip()
            if result.startswith("json"):
                result = result[4:].strip()
        validation = json.loads(result)
    except (json.JSONDecodeError, IndexError):
        validation = {"valid": True, "issues": [], "raw": result}

    # DB 저장
    conn = _get_db()
    try:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS story_validations (
                    id TEXT PRIMARY KEY,
                    story_id TEXT NOT NULL,
                    issues_json TEXT,
                    valid INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
        except sqlite3.OperationalError:
            pass

        conn.execute(
            "INSERT INTO story_validations (id, story_id, issues_json, valid, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), story_id,
             json.dumps(validation.get("issues", []), ensure_ascii=False),
             1 if validation.get("valid", True) else 0,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    validation["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(validation)


# ---------------------------------------------------------------------------
# API: 컨텍스트 미리보기
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/chain/<chain_id>/context", methods=["GET"])
def story_context_preview(chain_id):
    """현재 컨텍스트 미리보기 (LLM에 주입될 내용 확인)
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chain_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        chain = conn.execute("SELECT * FROM story_chains WHERE id=?", (chain_id,)).fetchone()
        if not chain:
            return jsonify({"error": "chain not found"}), 404
        chain = dict(chain)

        characters = None
        if chain.get("characters_json"):
            try:
                characters = json.loads(chain["characters_json"])
            except (json.JSONDecodeError, TypeError):
                pass
    finally:
        conn.close()

    context = _build_story_context(
        chain_id=chain_id,
        characters=characters,
        world_context=chain.get("world_context"),
    )

    return jsonify({
        "context": context,
        "context_length": len(context),
        "chain_id": chain_id,
        "episode_count": chain.get("episode_count", 0),
    })


# ---------------------------------------------------------------------------
# API: 시리즈 요약 수동 갱신
# ---------------------------------------------------------------------------

@story_bp.route("/api/story/chain/<chain_id>/summarize", methods=["POST"])
def story_chain_summarize(chain_id):
    """시리즈 요약 수동 갱신
    ---
    tags: [Story Engine - Context]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chain_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    stories = _load_chain_stories(chain_id)
    if not stories:
        return jsonify({"error": "no stories in chain"}), 404

    summary = _get_or_create_summary(stories)
    return jsonify({
        "chain_id": chain_id,
        "summary": summary,
        "episode_count": len(stories),
    })


# Korean API는 routes/korean.py Blueprint로 이관 (v2.60.0)
# /api/korean/analyze, /api/korean/rules, /api/korean/spellcheck, /api/korean/readability
# + /api/korean/pipeline, /api/korean/filter, /api/korean/check, /api/korean/metrics 추가
