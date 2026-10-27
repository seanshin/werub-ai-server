"""Novel Writer Blueprint — 장편 소설 집필 플랫폼 (기획→설계→집필→퇴고→출판)"""
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, request, jsonify, Response
from core.errors import internal_error

from core.config import OLLAMA_BASE
from core.korean import force_korean, force_korean_token
from core.korean_humanize import compute_korean_quality, get_quality_summary, get_s1_prompt_rules
from core.korean_spellcheck import spellcheck_long, get_spellcheck_summary
from core.cache import TTLCache
from core.validator import validate

logger = logging.getLogger("ollama-admin")

novel_bp = Blueprint("novel", __name__)

_ollama_session = None
_OLLAMA_BASE = None
_novel_cache = TTLCache(maxsize=300, ttl=120)  # 읽기 API 캐시 (2분)
_db_path = None

# ---------------------------------------------------------------------------
# 모델 설정
# ---------------------------------------------------------------------------

NOVEL_MODEL = "qwen2.5:14b"
STRUCTURE_MODEL = "exaone-deep:7.8b"
POLISH_MODEL = "dnotitia/dna:latest"
FAST_MODEL = "qwen2.5:7b"

# ---------------------------------------------------------------------------
# 장르별 프롬프트
# ---------------------------------------------------------------------------

GENRE_PROMPTS = {
    "fantasy": {
        "world_emphasis": "마법 체계, 종족, 고유 용어",
        "style_note": "웅장한 묘사, 고어체 허용, 전투 장면 역동적",
        "avoid": "현대 슬랭, 과학 용어",
    },
    "sf": {
        "world_emphasis": "기술 수준, 과학 설정의 일관성",
        "style_note": "논리적 전개, 과학적 사고, 철학적 질문",
        "avoid": "마법/초자연, 과학 법칙 위반",
    },
    "romance": {
        "world_emphasis": "인물 간 감정선, 관계 발전 단계",
        "style_note": "감정 묘사 세밀, 설렘/갈등/화해 리듬, 대화 비중 높음",
        "avoid": "과도한 전투/액션",
    },
    "mystery": {
        "world_emphasis": "단서 배치, 레드 헤링, 논리적 추리",
        "style_note": "서스펜스 유지, 독자가 추리할 수 있는 공정한 단서",
        "avoid": "데우스 엑스 마키나, 설명 없는 해결",
    },
    "horror": {
        "world_emphasis": "불안감, 미지의 공포, 제한된 정보",
        "style_note": "짧은 문장, 감각 자극, 일상의 균열",
        "avoid": "과도한 고어, 공포 요소의 조기 노출",
    },
    "literary": {
        "world_emphasis": "인물의 내면, 사회 구조, 관계의 복잡성",
        "style_note": "문장 하나하나의 완성도, 은유/상징, 여백과 여운",
        "avoid": "과도한 설명, 직접적 교훈, 급한 전개",
    },
    "web_novel": {
        "world_emphasis": "회차 hook, 성장 시스템, 먼치킨 요소, 클리프행어",
        "style_note": "짧은 문단, 대화 비중 60%+, 가독성 극대화, 빠른 전개",
        "avoid": "긴 묘사, 복잡한 은유, 느린 전개, 문학적 실험",
    },
}

# ---------------------------------------------------------------------------
# 플롯 구조 템플릿
# ---------------------------------------------------------------------------

PLOT_STRUCTURES = {
    "three_act": {
        "name": "3막 구조",
        "rules": [
            "1막 (25%): 일상 → 사건 발생 → 세계 진입",
            "2막 (50%): 갈등 심화 → 중반 반전 → 최악의 순간",
            "3막 (25%): 클라이맥스 → 해결 → 여운",
        ],
    },
    "hero_journey": {
        "name": "히어로 저니",
        "rules": [
            "일상 세계 → 모험의 부름 → 부름의 거부 → 멘토 만남",
            "첫 관문 → 시련/동맹/적 → 가장 깊은 동굴",
            "시련(죽음과 부활) → 보상 → 귀환의 길 → 부활 → 엘릭서",
        ],
    },
    "kishoten": {
        "name": "기승전결",
        "rules": [
            "기(起): 상황 설정, 인물 소개",
            "승(承): 사건 발전, 갈등 심화",
            "전(轉): 위기, 반전, 전환점",
            "결(結): 마무리, 여운, 주제 완성",
        ],
    },
}

# 소설 작법 규칙
NOVEL_CRAFT_RULES = """[소설 작법 규칙]
■ Show, Don't Tell: '슬펐다' → '시야가 흐려졌다. 손에 힘이 풀렸다.'
■ 감각 묘사: 시각/청각/후각/촉각 최소 2가지 이상
■ 대화: '말했다' 반복 금지 → 행동 묘사로 대체
■ 문장 리듬: 긴 묘사 → 짧은 액션 → 대화 교차
■ 시점 일관성: 1인칭=주인공 시야만, 3인칭=시점인물 감각만
■ 복선: 자연스럽게 심고, 임팩트 있게 회수
■ 장면 전환: 감각 묘사로 새 환경 인지"""

# ---------------------------------------------------------------------------
# 컨텍스트 예산 관리 (8계층)
# ---------------------------------------------------------------------------

_CONTEXT_BUDGET = {
    "L1_world": 1500,
    "L2_characters": 2000,
    "L3_synopsis_plot": 1500,
    "L4_prev_chapter": 2000,
    "L5_outline": 1000,
    "L6_style_ref": 800,
    "L7_scene_ref": 500,
    "L8_dialogue_ref": 600,
}
_MAX_CONTEXT_CHARS = 10000


def _build_novel_context(project, chapter_num, conn, component_refs=None):
    """8계층 컨텍스트 구성 — 예산 내 우선순위 기반 할당.

    L1: 세계관 (story_worlds)
    L2: 인물+관계도+복선 (story_characters + relationships + foreshadowing)
    L3: 시놉시스 + 플롯 구조
    L4: 이전 챕터 마지막
    L5: 이번 챕터 아웃라인 + 장면 계획
    L6: 레퍼런스 문체 프로필 (선택)
    L7: 레퍼런스 장면 기법 (선택)
    L8: 레퍼런스 대화 패턴 (선택)
    """
    parts = []
    budget = dict(_CONTEXT_BUDGET)
    has_refs = component_refs and any(component_refs.values()) if component_refs else False

    # 레퍼런스 없으면 L6~L8 예산을 L4에 재분배
    if not has_refs:
        extra = budget["L6_style_ref"] + budget["L7_scene_ref"] + budget["L8_dialogue_ref"]
        budget["L4_prev_chapter"] += extra
        budget["L6_style_ref"] = 0
        budget["L7_scene_ref"] = 0
        budget["L8_dialogue_ref"] = 0

    # --- L1: 세계관 ---
    world_id = project.get("world_id")
    if world_id:
        world = conn.execute("SELECT * FROM story_worlds WHERE id=?", (world_id,)).fetchone()
        if world:
            world = dict(world)
            w_parts = [f"[세계관: {world.get('name', '')}]"]
            if world.get("era"):
                w_parts.append(f"시대: {world['era']}")
            if world.get("geography"):
                w_parts.append(f"지리: {world['geography']}")
            if world.get("technology"):
                w_parts.append(f"기술: {world['technology']}")
            if world.get("rules_json"):
                try:
                    rules = json.loads(world["rules_json"])
                    if isinstance(rules, list):
                        w_parts.append("규칙: " + ", ".join(rules))
                    elif isinstance(rules, dict):
                        w_parts.append("규칙: " + json.dumps(rules, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    pass
            if world.get("tone_guide"):
                w_parts.append(f"분위기: {world['tone_guide']}")
            text = "\n".join(w_parts)[:budget["L1_world"]]
            parts.append(text)

    # --- L2: 인물 + 관계도 + 미회수 복선 ---
    chain_id = project.get("chain_id")
    if chain_id:
        # 인물
        chars = conn.execute(
            "SELECT name, tier, type, profile_json, speech_matrix_json, current_state_json "
            "FROM story_characters WHERE chain_id=? ORDER BY "
            "CASE tier WHEN 'protagonist' THEN 0 WHEN 'main' THEN 1 "
            "WHEN 'supporting' THEN 2 ELSE 3 END",
            (chain_id,)
        ).fetchall()
        if chars:
            char_lines = []
            for c in chars:
                c = dict(c)
                line = f"- {c['name']} ({c.get('tier', '')}/{c.get('type', '')})"
                if c.get("profile_json"):
                    try:
                        p = json.loads(c["profile_json"])
                        if p.get("traits"):
                            line += f" | 성격: {p['traits']}"
                        if p.get("motivation"):
                            line += f" | 동기: {p['motivation']}"
                    except (json.JSONDecodeError, TypeError):
                        pass
                if c.get("current_state_json"):
                    try:
                        st = json.loads(c["current_state_json"])
                        if st.get("status"):
                            line += f" | 현재: {st['status']}"
                    except (json.JSONDecodeError, TypeError):
                        pass
                char_lines.append(line)
            char_text = "[등장인물]\n" + "\n".join(char_lines)
            parts.append(char_text[:budget["L2_characters"] // 2])

        # 관계도
        rels = conn.execute(
            "SELECT from_character, to_character, type, state, tension "
            "FROM story_relationships WHERE chain_id=? ORDER BY tension DESC",
            (chain_id,)
        ).fetchall()
        if rels:
            rel_lines = [f"- {r['from_character']}→{r['to_character']}: "
                         f"{r['type']}" + (f" ({r['state']})" if r.get("state") else "")
                         + (f" [긴장:{r['tension']}]" if r.get("tension") else "")
                         for r in rels]
            rel_text = "[관계도]\n" + "\n".join(rel_lines)
            parts.append(rel_text[:budget["L2_characters"] // 4])

        # 미회수 복선
        foreshadows = conn.execute(
            "SELECT hint, importance FROM story_foreshadowing "
            "WHERE chain_id=? AND resolved=0 ORDER BY importance DESC",
            (chain_id,)
        ).fetchall()
        if foreshadows:
            fs_lines = [f"- [{f['importance']}] {f['hint']}" for f in foreshadows[:5]]
            fs_text = "[미회수 복선 — 자연스럽게 언급하세요]\n" + "\n".join(fs_lines)
            parts.append(fs_text[:budget["L2_characters"] // 4])

    # --- L3: 시놉시스 + 플롯 구조 ---
    l3_parts = []
    if project.get("synopsis_json"):
        try:
            syn = json.loads(project["synopsis_json"])
            premise = syn.get("premise", "")
            conflict = syn.get("act2_conflict", "")
            themes = ", ".join(syn.get("themes", []))
            l3_parts.append(f"[시놉시스] {premise}")
            if conflict:
                l3_parts.append(f"갈등: {conflict}")
            if themes:
                l3_parts.append(f"테마: {themes}")
        except (json.JSONDecodeError, TypeError):
            pass

    if project.get("plot_json"):
        try:
            plot = json.loads(project["plot_json"])
            if isinstance(plot, dict) and plot.get("acts"):
                acts_summary = []
                for act in plot["acts"]:
                    acts_summary.append(
                        f"{act.get('name', '')}: ch{act.get('chapters', '')} — {act.get('purpose', '')}"
                    )
                l3_parts.append("[플롯 구조]\n" + "\n".join(acts_summary))
        except (json.JSONDecodeError, TypeError):
            pass

    if l3_parts:
        parts.append("\n".join(l3_parts)[:budget["L3_synopsis_plot"]])

    # --- L4: 이전 챕터 컨텍스트 (요약 우선 + 캐릭터 경험 + 원문 보조) ---
    if chapter_num > 1:
        l4_parts = []
        l4_used = 0

        # L4a: 이전 챕터들 요약 (최근 3챕터)
        summaries = conn.execute(
            "SELECT chapter_num, summary FROM novel_chapter_summaries "
            "WHERE project_id=? AND chapter_num < ? ORDER BY chapter_num DESC LIMIT 3",
            (project["id"], chapter_num)
        ).fetchall()
        if summaries:
            sum_lines = [f"ch{s['chapter_num']}: {s['summary']}" for s in reversed(summaries)]
            sum_text = "[이전 챕터 요약]\n" + "\n".join(sum_lines)
            l4_parts.append(sum_text[:budget["L4_prev_chapter"] // 2])
            l4_used += len(l4_parts[-1])

        # L4b: 캐릭터 경험 로그 (최근 챕터)
        experiences = conn.execute(
            "SELECT character_name, experience, emotion, growth FROM novel_character_experiences "
            "WHERE project_id=? AND chapter_num=? ORDER BY character_name",
            (project["id"], chapter_num - 1)
        ).fetchall()
        if experiences:
            exp_lines = []
            for e in experiences:
                line = f"- {e['character_name']}: {e['experience']}"
                if e.get("emotion"):
                    line += f" (감정: {e['emotion']})"
                if e.get("growth"):
                    line += f" → {e['growth']}"
                exp_lines.append(line)
            exp_text = "[캐릭터 최근 경험]\n" + "\n".join(exp_lines)
            l4_parts.append(exp_text[:budget["L4_prev_chapter"] // 4])
            l4_used += len(l4_parts[-1])

        # L4c: 직전 챕터 마지막 (남은 예산으로 원문 보조)
        remaining = budget["L4_prev_chapter"] - l4_used
        if remaining > 200:
            prev = conn.execute(
                "SELECT content FROM novel_chapters WHERE project_id=? AND chapter_num=?",
                (project["id"], chapter_num - 1)
            ).fetchone()
            if prev and prev["content"]:
                last_part = prev["content"][-remaining:]
                l4_parts.append(f"[직전 챕터 마지막]\n{last_part}")

        if l4_parts:
            parts.extend(l4_parts)

    # --- L5: 이번 챕터 아웃라인 + 장면 계획 ---
    ch_info = conn.execute(
        "SELECT * FROM novel_chapters WHERE project_id=? AND chapter_num=?",
        (project["id"], chapter_num)
    ).fetchone()
    ch_dict = dict(ch_info) if ch_info else {}

    if ch_dict.get("scenes_json"):
        try:
            scenes = json.loads(ch_dict["scenes_json"])
            scenes_text = "\n".join(
                f"장면 {s.get('scene', i+1)}: {s.get('purpose', '')} "
                f"({s.get('location', '')})"
                + (f" [{s.get('emotion_arc', '')}]" if s.get("emotion_arc") else "")
                for i, s in enumerate(scenes)
            )
            parts.append(f"[이번 챕터 장면 계획]\n{scenes_text}"[:budget["L5_outline"]])
        except (json.JSONDecodeError, TypeError):
            pass

    # --- L6~L8: 레퍼런스 컴포넌트 (선택) ---
    if has_refs:
        _inject_component_refs(parts, component_refs, budget, conn)

    # 전체 예산 제한
    context = "\n\n".join(parts)
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS]

    return context, ch_dict


def _inject_component_refs(parts, refs, budget, conn):
    """L6~L8 레퍼런스 컴포넌트를 컨텍스트에 주입."""
    if not refs:
        return

    def _load_component(comp_id):
        """novel_components 테이블에서 로드."""
        try:
            row = conn.execute(
                "SELECT data_json, type, name FROM novel_components WHERE id=?",
                (comp_id,)
            ).fetchone()
            if row:
                data = json.loads(row["data_json"]) if row["data_json"] else {}
                return {"type": row["type"], "name": row["name"], "data": data}
        except Exception:
            pass
        return None

    # L6: 문체 프로필
    style_id = refs.get("style")
    if style_id and budget["L6_style_ref"] > 0:
        comp = _load_component(style_id)
        if comp and comp["data"]:
            d = comp["data"]
            style_lines = ["[레퍼런스 문체 — 참고하되 독자적 스타일 유지]"]
            for k, v in d.items():
                style_lines.append(f"  {k}: {v}")
            parts.append("\n".join(style_lines)[:budget["L6_style_ref"]])

    # L7: 장면 기법
    scene_id = refs.get("scene_technique")
    if scene_id and budget["L7_scene_ref"] > 0:
        comp = _load_component(scene_id)
        if comp and comp["data"]:
            d = comp["data"]
            technique = d.get("technique", "")
            why = d.get("why_effective", "")
            parts.append(
                f"[레퍼런스 장면 기법]\n기법: {technique}\n효과: {why}"
                [:budget["L7_scene_ref"]]
            )

    # L8: 대화 패턴
    dial_refs = refs.get("dialogue_pattern", [])
    if isinstance(dial_refs, str):
        dial_refs = [dial_refs]
    if dial_refs and budget["L8_dialogue_ref"] > 0:
        dial_lines = ["[레퍼런스 대화 패턴]"]
        for did in dial_refs[:3]:
            comp = _load_component(did)
            if comp and comp["data"]:
                d = comp["data"]
                char = d.get("character", "")
                style = d.get("speech_style", {})
                dial_lines.append(f"  {char}: {json.dumps(style, ensure_ascii=False)}")
        parts.append("\n".join(dial_lines)[:budget["L8_dialogue_ref"]])


# ---------------------------------------------------------------------------
# 초기화
# ---------------------------------------------------------------------------

def init_novel(*, ollama_session, ollama_base, base_dir):
    global _ollama_session, _OLLAMA_BASE, _db_path
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _db_path = os.path.join(base_dir, "story.db")  # story.db 공유
    _init_novel_tables()


def _get_db():
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_novel_tables():
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                genre TEXT,
                sub_genres TEXT,
                logline TEXT,
                synopsis_json TEXT,
                plot_json TEXT,
                outline_json TEXT,
                world_id TEXT,
                chain_id TEXT,
                settings_json TEXT,
                status TEXT DEFAULT 'planning',
                total_words INTEGER DEFAULT 0,
                total_chapters INTEGER DEFAULT 0,
                completed_chapters INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_chapters (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                title TEXT,
                content TEXT,
                scenes_json TEXT,
                word_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'outline',
                revision_count INTEGER DEFAULT 0,
                pov_character TEXT,
                story_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT,
                UNIQUE(project_id, chapter_num)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_revisions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_num INTEGER,
                type TEXT,
                issues_json TEXT,
                score INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Sprint 3 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_chapter_versions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_tag TEXT NOT NULL,
                content TEXT,
                word_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, chapter_num, version_tag)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_chapter_summaries (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                summary TEXT,
                key_events TEXT,
                emotion_tags TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, chapter_num)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_timeline_events (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_num INTEGER,
                scene_num INTEGER,
                event_time TEXT,
                event_desc TEXT,
                characters TEXT,
                location TEXT,
                importance TEXT DEFAULT 'normal',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Sprint 4 테이블 — 시리즈
        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_series (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                genre TEXT,
                total_volumes INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ongoing',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_series_volumes (
                series_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                volume_num INTEGER NOT NULL,
                PRIMARY KEY (series_id, project_id)
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_novel_ch_proj ON novel_chapters(project_id, chapter_num)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_novel_rev_proj ON novel_revisions(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_novel_cv_proj ON novel_chapter_versions(project_id, chapter_num)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_novel_cs_proj ON novel_chapter_summaries(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_novel_te_proj ON novel_timeline_events(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_novel_sv_series ON novel_series_volumes(series_id)")

        # Sprint 5 테이블 — 소설 분해기
        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_sources (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT,
                genre TEXT NOT NULL,
                sub_genres TEXT,
                language TEXT DEFAULT 'ko',
                text_content TEXT,
                chapters_json TEXT,
                word_count INTEGER DEFAULT 0,
                metadata_json TEXT,
                decompose_status TEXT DEFAULT 'uploaded',
                decompose_result_json TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_components (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                type TEXT NOT NULL,
                genre TEXT NOT NULL,
                sub_type TEXT,
                name TEXT,
                tags TEXT,
                data_json TEXT NOT NULL,
                quality_score REAL,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_component_usage (
                id TEXT PRIMARY KEY,
                component_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                usage_type TEXT,
                adaptation_note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_comp_source ON novel_components(source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comp_type ON novel_components(type, genre)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comp_genre ON novel_components(genre)")

        # Sprint 6 테이블 — 통합 파이프라인
        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_recipes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                genre TEXT,
                component_slots_json TEXT,
                default_settings_json TEXT,
                adaptation_rules_json TEXT,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_project_refs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                component_id TEXT NOT NULL,
                ref_type TEXT NOT NULL,
                ref_role TEXT,
                adaptation_note TEXT,
                layer TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS novel_project_recipe (
                project_id TEXT PRIMARY KEY,
                recipe_id TEXT NOT NULL,
                overrides_json TEXT,
                executed_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # NovelWriter 패턴 이식: 캐릭터 경험 추적 + 챕터 스냅샷
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS novel_character_experiences (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                character_name TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                experience TEXT NOT NULL,
                emotion TEXT,
                growth TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, character_name, chapter_num)
            );

            CREATE TABLE IF NOT EXISTS novel_chapter_snapshots (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, chapter_num)
            );
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_projref_proj ON novel_project_refs(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projref_comp ON novel_project_refs(component_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_charexp_proj ON novel_character_experiences(project_id, character_name)")
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
        system, tag="[novel]")


def _generate_once(model, prompt, system, options=None, timeout=None):
    from core.llm_resilience import get_adaptive_timeout, ollama_breaker
    sess, base = _get_session()
    opts = options or {}
    if timeout is None:
        timeout = get_adaptive_timeout(model, "draft")
    if not ollama_breaker.can_execute():
        return "Error: Ollama temporarily unavailable", 0, 0

    start = time.time()
    try:
        # v2.124.99 task 라우터 경유 — 🔴 호출자의 세션·base를 넘긴다.
        # ⚠️ 서킷 브레이커·적응형 타임아웃은 seam 위에 남는다(복원력은 도메인 정책).
        from core import model_router
        res = model_router.generate(
            "novel.draft", prompt, model=model, system=system, options=opts,
            timeout=timeout,
            adapter=model_router.OllamaAdapter(base=base, session=sess))
        if not res.ok:
            # 종전 `raise_for_status()`와 같은 층 — 빈 문자열로 흘리지 않는다.
            from core.llm_resilience import result_error
            raise result_error(res, "generate failed")
        elapsed = res.duration_ms or int((time.time() - start) * 1000)
        ollama_breaker.record_success()
        return res.text, res.tokens, elapsed
    except Exception as e:
        # v2.125.68: 대기열 거절(busy)은 공용 브레이커 실패로 세지 않는다(core/llm_resilience.LLMBusyError)
        from core.llm_resilience import record_failure_unless_busy
        record_failure_unless_busy(e, ollama_breaker)
        logger.error(f"Novel _generate error: {e}")
        return f"Error: {e}", 0, int((time.time() - start) * 1000)


def _generate_stream(model, prompt, system, options=None, timeout=None):
    """LLM 스트리밍 호출 — **`StreamChunk` 제너레이터**를 돌려준다.

    ⚠️ v2.124.99 이관에서 반환 계약이 바뀌었다: 종전엔 requests 응답 객체라
    호출자가 `iter_lines()`로 직접 풀었다. 지금은 조각(`text`/`done`/`tokens`)이 온다.
    소비자는 한 곳뿐이라 함께 고쳤다. 커넥션 회수는 어댑터의 `finally`가 보장한다.
    """
    from core.llm_resilience import get_adaptive_timeout
    sess, base = _get_session()
    opts = options or {}
    if timeout is None:
        timeout = get_adaptive_timeout(model, "draft") + 60
    from core import model_router
    return model_router.stream(
        "novel.draft", prompt, model=model, system=system, options=opts,
        timeout=timeout,
        adapter=model_router.OllamaAdapter(base=base, session=sess))


# ---------------------------------------------------------------------------
# NovelWriter 패턴 이식: 챕터 후처리 (요약 + 캐릭터 경험 + 스냅샷)
# ---------------------------------------------------------------------------


def _post_chapter_process(project_id, chapter_num, content, conn):
    """챕터 집필 완료 후 자동 후처리 (백그라운드 품질).

    1. 챕터 요약 자동 생성 (3-4문장)
    2. 캐릭터 경험 추출 (어떤 캐릭터가 무엇을 겪었는지)
    3. 스냅샷 저장 (현재 프로젝트 상태)
    """
    if not content or len(content) < 100:
        return

    try:
        # 1. 챕터 요약 생성
        summary_prompt = f"""아래 소설 챕터를 3-4문장으로 요약하세요.
핵심 사건, 인물 행동, 감정 변화를 포함하세요.
요약만 출력하세요.

[챕터 내용]
{content[:3000]}"""

        summary_text, _, _ = _generate(
            FAST_MODEL, summary_prompt,
            "소설 편집자입니다. 챕터 핵심을 간결하게 요약하세요.",
            {"temperature": 0.2, "num_predict": 300}, timeout=30
        )
        summary_text = force_korean(summary_text).strip()

        if summary_text and not summary_text.startswith("Error:"):
            conn.execute(
                "INSERT OR REPLACE INTO novel_chapter_summaries "
                "(id, project_id, chapter_num, summary, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (f"{project_id}_ch{chapter_num}_sum", project_id, chapter_num, summary_text)
            )

        # 2. 캐릭터 경험 추출
        exp_prompt = f"""아래 소설 챕터에서 각 캐릭터의 경험을 JSON으로 추출하세요.

형식:
[{{"name": "캐릭터명", "experience": "이 챕터에서 겪은 일", "emotion": "감정 상태", "growth": "성장/변화"}}]

등장하지 않은 캐릭터는 제외하세요.

[챕터 내용]
{content[:3000]}"""

        exp_text, _, _ = _generate(
            FAST_MODEL, exp_prompt,
            "소설 분석가입니다. 캐릭터 경험을 JSON으로 추출하세요.",
            {"temperature": 0.1, "num_predict": 500}, timeout=30
        )

        if exp_text and not exp_text.startswith("Error:"):
            exp_data = _parse_json_response(exp_text)
            if isinstance(exp_data, list):
                for char in exp_data:
                    if isinstance(char, dict) and char.get("name"):
                        conn.execute(
                            "INSERT OR REPLACE INTO novel_character_experiences "
                            "(id, project_id, character_name, chapter_num, "
                            "experience, emotion, growth, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                            (f"{project_id}_{char['name']}_ch{chapter_num}",
                             project_id, char["name"], chapter_num,
                             char.get("experience", ""),
                             char.get("emotion", ""),
                             char.get("growth", ""))
                        )

        # 3. 스냅샷 저장
        snapshot = {
            "chapter_num": chapter_num,
            "word_count": len(content),
            "summary": summary_text if summary_text and not summary_text.startswith("Error:") else None,
            "characters_experienced": [c.get("name") for c in (exp_data if isinstance(exp_data, list) else [])
                                        if isinstance(c, dict) and c.get("name")],
        }
        conn.execute(
            "INSERT OR REPLACE INTO novel_chapter_snapshots "
            "(id, project_id, chapter_num, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (f"{project_id}_snap_ch{chapter_num}", project_id, chapter_num,
             json.dumps(snapshot, ensure_ascii=False))
        )

        conn.commit()
        logger.info(f"Post-chapter process done: {project_id} ch{chapter_num}")

    except Exception as e:
        logger.warning(f"Post-chapter process failed: {e}")


def _parse_json_response(text):
    """LLM 응답에서 JSON 추출 — 앞뒤 텍스트, 코드블록, 중첩 대응"""
    if not text:
        return None
    text = text.strip()

    # 1) ```json ... ``` 코드블록
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:  # 홀수 인덱스 = 코드블록 내부
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue

    # 2) 전체가 JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 3) 첫 번째 { 또는 [ 부터 마지막 } 또는 ] 까지 추출
    for open_ch, close_ch in [('{', '}'), ('[', ']')]:
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue

    return None


# ---------------------------------------------------------------------------
# API: 프로젝트 CRUD
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/project/create", methods=["POST"])
def novel_project_create():
    """소설 프로젝트 생성
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title]
          properties:
            title: {type: string}
            genre: {type: string, enum: [fantasy, sf, romance, mystery, horror, literary, web_novel], default: fantasy}
            sub_genres: {type: array, items: {type: string}}
            logline: {type: string, description: "한 줄 요약"}
            tone: {type: string, default: warm}
            pov: {type: string, default: third_person}
            estimated_chapters: {type: integer, default: 12}
            chapter_length: {type: integer, default: 5000}
            language: {type: string, default: ko}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400

    project_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    settings = {
        "tone": data.get("tone", "warm"),
        "pov": data.get("pov", "third_person"),
        "estimated_chapters": data.get("estimated_chapters", 12),
        "chapter_length": data.get("chapter_length", 5000),
        "language": data.get("language", "ko"),
    }

    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_projects (id, title, genre, sub_genres, logline, settings_json, "
            "total_chapters, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'planning', ?)",
            (project_id, title, data.get("genre", "fantasy"),
             json.dumps(data.get("sub_genres", []), ensure_ascii=False),
             data.get("logline"),
             json.dumps(settings, ensure_ascii=False),
             data.get("estimated_chapters", 12), now)
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"project_id": project_id, "title": title, "status": "planning"})


@novel_bp.route("/api/novel/projects", methods=["GET"])
def novel_projects_list():
    """프로젝트 목록
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: query, name: status, type: string}
      - {in: query, name: limit, type: integer, default: 50}
    responses:
      200:
        schema:
          type: object
    """
    status = request.args.get("status")
    limit = min(int(request.args.get("limit", 50)), 200)

    _ck = f"proj_list:{status}:{limit}"
    _cached = _novel_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    conn = _get_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM novel_projects WHERE status=? ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM novel_projects ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    finally:
        conn.close()

    projects = []
    for r in rows:
        d = dict(r)
        for key in ("sub_genres", "settings_json", "synopsis_json", "plot_json", "outline_json"):
            if d.get(key):
                try:
                    d[key.replace("_json", "").replace("sub_genres", "sub_genres")] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        projects.append(d)

    resp = {"projects": projects, "count": len(projects)}
    _novel_cache.set(_ck, resp)
    return jsonify(resp)


@novel_bp.route("/api/novel/project/<project_id>", methods=["GET"])
def novel_project_get(project_id):
    """프로젝트 상세
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    _ck = f"proj:{project_id}"
    _cached = _novel_cache.get(_ck)
    if _cached is not None:
        return jsonify(_cached)

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "project not found"}), 404

        chapters = conn.execute(
            "SELECT id, chapter_num, title, word_count, status, pov_character, created_at "
            "FROM novel_chapters WHERE project_id=? ORDER BY chapter_num",
            (project_id,)
        ).fetchall()
    finally:
        conn.close()

    d = dict(row)
    for key in ("sub_genres", "settings_json", "synopsis_json", "plot_json", "outline_json"):
        if d.get(key):
            try:
                clean_key = key.replace("_json", "") if key.endswith("_json") else key
                d[clean_key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    d["chapters"] = [dict(c) for c in chapters]
    _novel_cache.set(_ck, d)
    return jsonify(d)


@novel_bp.route("/api/novel/project/<project_id>", methods=["PUT"])
def novel_project_update(project_id):
    """프로젝트 수정
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    conn = _get_db()
    try:
        existing = conn.execute("SELECT id FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not existing:
            return jsonify({"error": "project not found"}), 404

        fields = []
        values = []
        for key in ("title", "genre", "logline", "status", "world_id", "chain_id"):
            if key in data:
                fields.append(f"{key}=?")
                values.append(data[key])
        for key in ("sub_genres", "settings"):
            if key in data:
                fields.append(f"{'sub_genres' if key == 'sub_genres' else key + '_json'}=?")
                values.append(json.dumps(data[key], ensure_ascii=False))
        if fields:
            fields.append("updated_at=?")
            values.append(datetime.now(timezone.utc).isoformat())
            values.append(project_id)
            conn.execute(f"UPDATE novel_projects SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "project_id": project_id})


@novel_bp.route("/api/novel/project/<project_id>", methods=["DELETE"])
def novel_project_delete(project_id):
    """프로젝트 삭제
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        conn.execute("DELETE FROM novel_chapters WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM novel_revisions WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM novel_projects WHERE id=?", (project_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "deleted": project_id})


# ---------------------------------------------------------------------------
# API: 시놉시스 생성
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/synopsis/generate", methods=["POST"])
def novel_synopsis_generate():
    """시놉시스 자동 생성
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            depth: {type: string, enum: [brief, standard, detailed], default: standard}
            include_ending: {type: boolean, default: true}
            component_refs:
              type: object
              description: "레퍼런스 컴포넌트 (선택)"
              properties:
                plot_ref: {type: string}
                character_refs: {type: array, items: {type: string}}
                theme_ref: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()

    if not project:
        return jsonify({"error": "project not found"}), 404
    project = dict(project)

    depth = data.get("depth", "standard")
    include_ending = data.get("include_ending", True)
    genre = project.get("genre", "fantasy")

    depth_inst = {
        "brief": "1문단으로 간결하게",
        "standard": "1페이지 (500~800자)로",
        "detailed": "2~3페이지 (1500~2000자)로 상세하게",
    }.get(depth, "1페이지로")

    genre_cfg = GENRE_PROMPTS.get(genre, {})
    ending_inst = "결말까지 포함하세요." if include_ending else "결말은 포함하지 마세요 (스포일러 방지)."

    # component_refs 로드
    ref_context = ""
    comp_refs = data.get("component_refs")
    if comp_refs:
        ref_parts = []
        conn2 = _get_db()
        try:
            for key in ("plot_ref", "theme_ref"):
                cid = comp_refs.get(key)
                if cid:
                    r = conn2.execute("SELECT data_json, type, name FROM novel_components WHERE id=?", (cid,)).fetchone()
                    if r:
                        ref_parts.append(f"[레퍼런스 {r['type']}: {r['name']}] {r['data_json'][:400]}")
            for cid in (comp_refs.get("character_refs") or []):
                r = conn2.execute("SELECT data_json, name FROM novel_components WHERE id=?", (cid,)).fetchone()
                if r:
                    ref_parts.append(f"[레퍼런스 캐릭터: {r['name']}] {r['data_json'][:300]}")
        finally:
            conn2.close()
        if ref_parts:
            ref_context = "\n\n[레퍼런스 — 참고하되 새로운 시놉시스를 생성하세요]\n" + "\n".join(ref_parts)

    prompt = f"""소설 시놉시스를 {depth_inst} 작성하세요.

[제목] {project['title']}
[장르] {genre}
[로그라인] {project.get('logline', '(미정)')}
{ending_inst}
{ref_context}

다음 JSON으로 출력:
{{
  "premise": "전제/배경",
  "act1_setup": "1막: 설정과 시작 사건",
  "act2_conflict": "2막: 갈등과 위기",
  "act3_resolution": "3막: 해결과 결말",
  "themes": ["테마 3개"],
  "ending_type": "happy/bittersweet/open/tragic"
}}

JSON만 출력."""

    system = f"소설 기획자입니다. {genre} 장르 전문가. {genre_cfg.get('style_note', '')} 한국어로 작성하세요."

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt, system,
                                         {"num_predict": 2500, "temperature": 0.7})
    result = force_korean(result).strip()

    synopsis = _parse_json_response(result)
    if not synopsis:
        synopsis = {"raw_output": result}

    # DB 저장
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE novel_projects SET synopsis_json=?, updated_at=? WHERE id=?",
            (json.dumps(synopsis, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), project_id)
        )
        conn.commit()
    finally:
        conn.close()

    synopsis["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(synopsis)


@novel_bp.route("/api/novel/synopsis/<project_id>", methods=["GET"])
def novel_synopsis_get(project_id):
    """시놉시스 조회
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT synopsis_json FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "project not found"}), 404
    if not row["synopsis_json"]:
        return jsonify({"error": "synopsis not generated yet"}), 404

    try:
        return jsonify(json.loads(row["synopsis_json"]))
    except (json.JSONDecodeError, TypeError):
        return jsonify({"raw": row["synopsis_json"]})


# ---------------------------------------------------------------------------
# API: 플롯 구조 생성
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/plot/generate", methods=["POST"])
def novel_plot_generate():
    """플롯 구조 자동 생성
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            structure: {type: string, enum: [three_act, hero_journey, kishoten], default: three_act}
            subplot_count: {type: integer, default: 2}
            turning_points: {type: integer, default: 5}
            component_refs:
              type: object
              description: "레퍼런스 컴포넌트 (선택)"
              properties:
                plot_ref: {type: string}
                foreshadowing_refs: {type: array, items: {type: string}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()

    if not project:
        return jsonify({"error": "project not found"}), 404
    project = dict(project)

    structure = data.get("structure", "three_act")
    subplot_count = data.get("subplot_count", 2)
    turning_points = data.get("turning_points", 5)
    total_chapters = project.get("total_chapters", 12)

    struct_cfg = PLOT_STRUCTURES.get(structure, PLOT_STRUCTURES["three_act"])

    # 시놉시스 로드
    synopsis_text = ""
    if project.get("synopsis_json"):
        try:
            syn = json.loads(project["synopsis_json"])
            synopsis_text = f"전제: {syn.get('premise', '')}\n1막: {syn.get('act1_setup', '')}\n2막: {syn.get('act2_conflict', '')}\n3막: {syn.get('act3_resolution', '')}"
        except (json.JSONDecodeError, TypeError):
            pass

    # component_refs 로드
    ref_context = ""
    comp_refs = data.get("component_refs")
    if comp_refs:
        ref_parts = []
        conn2 = _get_db()
        try:
            cid = comp_refs.get("plot_ref")
            if cid:
                r = conn2.execute("SELECT data_json, name FROM novel_components WHERE id=?", (cid,)).fetchone()
                if r:
                    ref_parts.append(f"[레퍼런스 플롯: {r['name']}] {r['data_json'][:500]}")
            for cid in (comp_refs.get("foreshadowing_refs") or []):
                r = conn2.execute("SELECT data_json, name FROM novel_components WHERE id=?", (cid,)).fetchone()
                if r:
                    ref_parts.append(f"[레퍼런스 복선: {r['name']}] {r['data_json'][:300]}")
        finally:
            conn2.close()
        if ref_parts:
            ref_context = "\n\n[레퍼런스 — 참고하되 새로운 플롯을 설계하세요]\n" + "\n".join(ref_parts)

    prompt = f"""소설의 플롯 구조를 설계하세요.

[제목] {project['title']}
[장르] {project.get('genre', 'fantasy')}
[구조] {struct_cfg['name']}
[규칙] {chr(10).join(struct_cfg['rules'])}
[총 챕터] {total_chapters}
[서브플롯] {subplot_count}개
[전환점] {turning_points}개

{f'[시놉시스]{chr(10)}{synopsis_text}' if synopsis_text else ''}
{ref_context}

JSON으로 출력:
{{
  "structure": "{structure}",
  "acts": [
    {{
      "act": 1, "name": "막 이름", "chapters": [1, 2, 3],
      "purpose": "이 막의 목적",
      "key_events": ["핵심 사건"],
      "ending_hook": "다음 막으로 연결"
    }}
  ],
  "subplots": [
    {{"name": "서브플롯", "chapters": [4, 6, 8], "purpose": "목적"}}
  ],
  "turning_points": [
    {{"chapter": 5, "event": "전환점 사건", "impact": "영향"}}
  ],
  "foreshadowing_plan": [
    {{"plant_chapter": 2, "resolve_chapter": 10, "hint": "복선"}}
  ]
}}

JSON만 출력."""

    system = f"소설 구조 전문가입니다. {struct_cfg['name']} 구조의 원칙을 지키세요. 한국어로 작성."

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt, system,
                                         {"num_predict": 3000, "temperature": 0.7})
    result = force_korean(result).strip()

    plot = _parse_json_response(result)
    if not plot:
        plot = {"raw_output": result}

    # DB 저장
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE novel_projects SET plot_json=?, updated_at=? WHERE id=?",
            (json.dumps(plot, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), project_id)
        )
        conn.commit()
    finally:
        conn.close()

    plot["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(plot)


# ---------------------------------------------------------------------------
# API: 챕터 아웃라인
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/outline/generate", methods=["POST"])
def novel_outline_generate():
    """전체 챕터 아웃라인 생성
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()

    if not project:
        return jsonify({"error": "project not found"}), 404
    project = dict(project)
    total_chapters = project.get("total_chapters", 12)

    # 시놉시스 + 플롯 로드
    context_parts = [f"[제목] {project['title']}", f"[장르] {project.get('genre', 'fantasy')}"]
    if project.get("synopsis_json"):
        try:
            syn = json.loads(project["synopsis_json"])
            context_parts.append(f"[시놉시스] {syn.get('premise', '')} / {syn.get('act2_conflict', '')}")
        except (json.JSONDecodeError, TypeError):
            pass
    if project.get("plot_json"):
        try:
            plot = json.loads(project["plot_json"])
            if plot.get("turning_points"):
                tp = ", ".join(f"ch{t['chapter']}: {t['event']}" for t in plot["turning_points"][:5])
                context_parts.append(f"[전환점] {tp}")
        except (json.JSONDecodeError, TypeError):
            pass

    context = "\n".join(context_parts)

    prompt = f"""{context}

{total_chapters}개 챕터의 아웃라인을 JSON으로 작성하세요.

[
  {{
    "chapter": 1,
    "title": "챕터 제목",
    "synopsis": "줄거리 100자",
    "scenes": [
      {{"scene": 1, "location": "장소", "characters": ["인물"], "purpose": "목적", "emotion_arc": "감정 변화"}}
    ],
    "chapter_hook": "챕터 끝 훅",
    "pov_character": "시점 인물",
    "foreshadowing_to_plant": ["심을 복선"]
  }}
]

JSON만 출력."""

    system = "소설 기획자입니다. 각 챕터가 유기적으로 연결되도록 설계하세요. 한국어로 작성."

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt, system,
                                         {"num_predict": 5000, "temperature": 0.7})
    result = force_korean(result).strip()

    outline = _parse_json_response(result)
    if not outline:
        outline = {"raw_output": result}

    # DB 저장
    conn = _get_db()
    try:
        outline_data = outline if isinstance(outline, list) else outline.get("chapters", [outline])
        conn.execute(
            "UPDATE novel_projects SET outline_json=?, updated_at=? WHERE id=?",
            (json.dumps(outline_data, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), project_id)
        )

        # 챕터 레코드 자동 생성
        for ch in (outline_data if isinstance(outline_data, list) else []):
            if isinstance(ch, dict) and ch.get("chapter"):
                ch_id = str(uuid.uuid4())
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO novel_chapters (id, project_id, chapter_num, title, "
                        "scenes_json, pov_character, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'outline', ?)",
                        (ch_id, project_id, ch["chapter"], ch.get("title"),
                         json.dumps(ch.get("scenes", []), ensure_ascii=False),
                         ch.get("pov_character"),
                         datetime.now(timezone.utc).isoformat())
                    )
                except sqlite3.IntegrityError:
                    pass  # 이미 존재
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "chapters": outline_data if isinstance(outline_data, list) else [outline],
        "total": total_chapters,
        "metadata": {"tokens": tokens, "duration_ms": elapsed},
    })


@novel_bp.route("/api/novel/outline/<project_id>", methods=["GET"])
def novel_outline_get(project_id):
    """아웃라인 조회
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT outline_json FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "project not found"}), 404
    if not row["outline_json"]:
        return jsonify({"error": "outline not generated yet"}), 404

    try:
        return jsonify({"chapters": json.loads(row["outline_json"])})
    except (json.JSONDecodeError, TypeError):
        return jsonify({"raw": row["outline_json"]})


# ---------------------------------------------------------------------------
# API: 챕터 집필
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/chapter/write", methods=["POST"])
def novel_chapter_write():
    """챕터 생성 (집필)
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            additional_direction: {type: string, description: "추가 지시사항"}
            quality: {type: string, enum: [quick, orchestrated], default: quick}
            model: {type: string}
            references:
              type: object
              description: "레퍼런스 컴포넌트 (선택)"
              properties:
                style: {type: string, description: "문체 컴포넌트 ID"}
                scene_technique: {type: string, description: "장면기법 컴포넌트 ID"}
                dialogue_pattern:
                  type: array
                  items: {type: string}
                  description: "대화패턴 컴포넌트 ID 목록"
            reference_strength: {type: string, enum: [strong, moderate, subtle], default: moderate}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    errors = validate(data, {
        "project_id": {"required": True, "type": "str"},
        "chapter_num": {"required": True, "type": "int", "min": 1, "max": 999},
        "quality": {"enum": ["quick", "orchestrated"], "default": "quick"},
        "reference_strength": {"enum": ["strong", "moderate", "subtle"], "default": "moderate"},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    project_id = data["project_id"].strip()
    chapter_num = data["chapter_num"]

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404
        project = dict(project)

        # E1: 외부 컨텍스트 주입 (LibriScribe 연동)
        external_context = data.get("external_context")
        if external_context:
            # 외부 컨텍스트 사용 — _build_novel_context() + outline 쿼리 스킵
            context = external_context
            ch_title_override = data.get("chapter_title")
            if ch_title_override:
                ch_dict = {"title": ch_title_override, "synopsis": ""}
            else:
                outline = conn.execute(
                    "SELECT * FROM novel_outlines WHERE project_id=? AND chapter_num=?",
                    (project_id, chapter_num)
                ).fetchone()
                ch_dict = dict(outline) if outline else {"title": f"챕터 {chapter_num}", "synopsis": ""}
        else:
            # 기존 동작 유지 — 8계층 컨텍스트 구성 (L1~L8)
            component_refs = data.get("references")
            context, ch_dict = _build_novel_context(
                project, chapter_num, conn, component_refs=component_refs
            )
    finally:
        conn.close()

    quality = data.get("quality", "quick")
    model = data.get("model") or NOVEL_MODEL
    ref_strength = data.get("reference_strength", "moderate")

    # 설정 로드
    settings = {}
    if project.get("settings_json"):
        try:
            settings = json.loads(project["settings_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    tone = settings.get("tone", "warm")
    pov = settings.get("pov", "third_person")
    chapter_length = settings.get("chapter_length", 5000)

    # 장르 프롬프트
    genre = project.get("genre", "fantasy")
    genre_cfg = GENRE_PROMPTS.get(genre, {})

    # reference_strength를 시스템 프롬프트에 반영
    ref_note = ""
    if component_refs and any(v for v in (component_refs or {}).values()):
        strength_desc = {
            "strong": "레퍼런스 문체/기법을 최대한 충실하게 모방하세요.",
            "moderate": "레퍼런스의 에센스를 참고하되 자체 색깔을 유지하세요.",
            "subtle": "레퍼런스에서 영감만 받고 독자적 스타일로 작성하세요.",
        }
        ref_note = f"\n[레퍼런스 강도] {strength_desc.get(ref_strength, strength_desc['moderate'])}"

    system = f"""{NOVEL_CRAFT_RULES}

[장르] {genre} — {genre_cfg.get('style_note', '')}
[피해야 할 것] {genre_cfg.get('avoid', '')}
[톤] {tone}
[시점] {pov}
{ref_note}
약 {chapter_length}자로 작성하세요. 장면 3개 이상.
대화:묘사:내면 비율 = 3:4:3.
반드시 한국어로 작성하세요."""

    additional = data.get("additional_direction", "")
    ch_title = ch_dict.get("title", f"챕터 {chapter_num}")

    # E6: 회차 구조 지시 파라미터
    episode_structure = data.get("episode_structure")
    if episode_structure:
        system += f"""
[회차 구조 지시]
- 오프닝 Hook: {episode_structure.get('hook_lines', 3)}줄 이내
- 도입: ~{episode_structure.get('intro_pct', 15)}%
- 전개: ~{episode_structure.get('develop_pct', 50)}%
- 절정: ~{episode_structure.get('climax_pct', 25)}%
- 클리프행어: ~{episode_structure.get('cliff_pct', 10)}%
- 목표 긴장도: {episode_structure.get('tension_target', 0.7)}"""

    prompt = f"""{context}

[챕터 {chapter_num}: {ch_title}]
{ch_dict.get('synopsis', f'챕터 {chapter_num}을 작성하세요.')}

{f'[추가 지시] {additional}' if additional else ''}

이 챕터를 소설 수준으로 작성하세요."""

    # E7: orchestrated_write 단계별 상세 반환
    return_detail = data.get("return_detail", False)

    if quality == "orchestrated":
        # 3단계 오케스트레이션
        orch_result = _orchestrated_write(prompt, system, chapter_length, return_detail=return_detail)
        if return_detail and isinstance(orch_result, tuple):
            content, orch_stages = orch_result
        else:
            content = orch_result
            orch_stages = None
        model_used = "orchestrated (3-model)"
        tokens_total = 0
        elapsed_total = 0
    else:
        content, tokens_total, elapsed_total = _generate(
            model, prompt, system,
            {"num_predict": int(chapter_length * 1.2)},
            timeout=180
        )
        content = force_korean(content).strip()
        model_used = model
        orch_stages = None

    if content.startswith("Error:"):
        return jsonify({"error": content}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(content, genre="essay")
    quality_metrics_data = get_quality_summary(quality_result)
    quality_metrics_data["retry_count"] = 0

    if quality_result["risk_band"] == "high" and quality != "orchestrated":
        logger.info(f"Novel chapter quality high-risk (score={quality_result['risk_score']}), regenerating")
        enhanced_system = system + "\n\n" + get_s1_prompt_rules()
        content2, tokens2, elapsed2 = _generate(
            model, prompt, enhanced_system,
            {"num_predict": int(chapter_length * 1.2)}, timeout=180
        )
        content2 = force_korean(content2).strip()
        if not content2.startswith("Error:"):
            content = content2
            tokens_total += tokens2
            elapsed_total += elapsed2
            quality_result = compute_korean_quality(content, genre="essay")
            quality_metrics_data = get_quality_summary(quality_result)
            quality_metrics_data["retry_count"] = 1

    # DB 저장
    word_count = len(content)
    now = datetime.now(timezone.utc).isoformat()
    ch_id = ch_dict.get("id") or str(uuid.uuid4())

    conn = _get_db()
    try:
        if ch_dict.get("id"):
            conn.execute(
                "UPDATE novel_chapters SET content=?, word_count=?, status='draft', updated_at=? "
                "WHERE project_id=? AND chapter_num=?",
                (content, word_count, now, project_id, chapter_num)
            )
        else:
            conn.execute(
                "INSERT INTO novel_chapters (id, project_id, chapter_num, title, content, "
                "word_count, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                (ch_id, project_id, chapter_num, ch_title, content, word_count, now)
            )

        # 프로젝트 통계 업데이트
        stats = conn.execute(
            "SELECT COUNT(*) as done, SUM(word_count) as words "
            "FROM novel_chapters WHERE project_id=? AND content IS NOT NULL",
            (project_id,)
        ).fetchone()

        conn.execute(
            "UPDATE novel_projects SET completed_chapters=?, total_words=?, "
            "status='writing', updated_at=? WHERE id=?",
            (stats["done"], stats["words"] or 0, now, project_id)
        )
        conn.commit()

        # 챕터 후처리: 요약 + 캐릭터 경험 + 스냅샷 (NovelWriter 패턴)
        _post_chapter_process(project_id, chapter_num, content, conn)
    finally:
        conn.close()

    resp = {
        "chapter_id": ch_id,
        "chapter_num": chapter_num,
        "title": ch_title,
        "content": content,
        "metadata": {
            "word_count": word_count,
            "model": model_used,
            "tokens": tokens_total,
            "duration_ms": elapsed_total,
            "quality": quality,
        },
        "quality_metrics": quality_metrics_data,
    }
    # E7: orchestration_detail 포함
    if orch_stages:
        resp["metadata"]["orchestration_detail"] = orch_stages
    return jsonify(resp)


def _orchestrated_write(prompt, system, target_length, return_detail=False):
    """3단계 오케스트레이션 집필 (초안→구조→다듬기)"""
    stages = []

    # Stage 1: 초안 (qwen2.5:14b)
    t0 = time.time()
    draft, tokens1, _ = _generate(
        NOVEL_MODEL, prompt, system,
        {"num_predict": int(target_length * 1.2)}, timeout=180
    )
    draft = force_korean(draft).strip()
    stages.append({"stage": 1, "model": NOVEL_MODEL, "name": "draft",
                    "duration_ms": int((time.time() - t0) * 1000),
                    "tokens": tokens1, "word_count": len(draft)})
    if draft.startswith("Error:"):
        return (draft, stages) if return_detail else draft

    # Stage 2: 구조 강화 (exaone-deep)
    t1 = time.time()
    stage2_prompt = f"""아래 소설 초안의 서사 구조를 강화하세요.
- 복선을 자연스럽게 심기
- 장면 전환 매끄럽게
- 갈등/긴장감 보강
- 내용은 유지하되 구조만 개선

[초안]
{draft}"""

    enhanced, tokens2, _ = _generate(
        STRUCTURE_MODEL, stage2_prompt,
        "소설 편집자입니다. 서사 구조와 복선을 강화하세요. 한국어 유지.",
        {"num_predict": int(target_length * 1.3)}, timeout=180
    )
    enhanced = force_korean(enhanced).strip()
    stages.append({"stage": 2, "model": STRUCTURE_MODEL, "name": "structure",
                    "duration_ms": int((time.time() - t1) * 1000),
                    "tokens": tokens2, "word_count": len(enhanced)})
    if enhanced.startswith("Error:"):
        result = draft
    else:
        # Stage 3: 문장력 다듬기 (dna)
        t2 = time.time()
        stage3_prompt = f"""아래 소설의 문장력을 다듬으세요.
- 감각 묘사 (시각/청각/후각/촉각) 추가
- 문장 리듬 (긴→짧 교차)
- 번역투 제거
- 내면 묘사 깊이 추가

[원문]
{enhanced}"""

        polished, tokens3, _ = _generate(
            POLISH_MODEL, stage3_prompt,
            "문체 전문가입니다. 소설 수준의 한국어로 다듬으세요.",
            {"num_predict": int(target_length * 1.3)}, timeout=180
        )
        polished = force_korean(polished).strip()
        stages.append({"stage": 3, "model": POLISH_MODEL, "name": "polish",
                        "duration_ms": int((time.time() - t2) * 1000),
                        "tokens": tokens3, "word_count": len(polished)})
        result = polished if not polished.startswith("Error:") else enhanced

    return (result, stages) if return_detail else result


# ---------------------------------------------------------------------------
# API: 챕터 조회
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/chapter/<project_id>/<int:chapter_num>", methods=["GET"])
def novel_chapter_get(project_id, chapter_num):
    """챕터 조회
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
      - {in: path, name: chapter_num, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "chapter not found"}), 404

    d = dict(row)
    if d.get("scenes_json"):
        try:
            d["scenes"] = json.loads(d["scenes_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify(d)


# ---------------------------------------------------------------------------
# API: 퇴고 시스템
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/revise/consistency", methods=["POST"])
def novel_revise_consistency():
    """전체 일관성 검증
    ---
    tags: [Novel Writer - Revision]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapters: {type: array, items: {type: integer}, description: "검사할 챕터 (null=전체)"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    target_chapters = data.get("chapters")

    # E4: 단일 회차 + 외부 컨텍스트 기반 빠른 검증
    focus_chapter = data.get("focus_chapter")
    context_summary = data.get("context_summary")

    conn = _get_db()
    try:
        if focus_chapter and context_summary:
            # 단일 회차 집중 검증 (LibriScribe 연동)
            chapter = conn.execute(
                "SELECT chapter_num, title, content FROM novel_chapters "
                "WHERE project_id=? AND chapter_num=? AND content IS NOT NULL",
                (project_id, focus_chapter)
            ).fetchone()
            if not chapter:
                return jsonify({"error": f"chapter {focus_chapter} not found"}), 404
            chapters = [chapter]
        elif target_chapters:
            placeholders = ",".join("?" * len(target_chapters))
            chapters = conn.execute(
                f"SELECT chapter_num, title, content FROM novel_chapters "
                f"WHERE project_id=? AND chapter_num IN ({placeholders}) AND content IS NOT NULL "
                f"ORDER BY chapter_num",
                [project_id] + target_chapters
            ).fetchall()
        else:
            chapters = conn.execute(
                "SELECT chapter_num, title, content FROM novel_chapters "
                "WHERE project_id=? AND content IS NOT NULL ORDER BY chapter_num",
                (project_id,)
            ).fetchall()
    finally:
        conn.close()

    if not chapters:
        return jsonify({"error": "no chapters to check"}), 404

    # E4: focus_chapter 모드 — 외부 컨텍스트 + 단일 회차 전문
    if focus_chapter and context_summary:
        ch = chapters[0]
        prompt = f"""다음 컨텍스트를 기반으로 챕터 {focus_chapter}의 일관성을 검증하세요.

[이전 이야기 컨텍스트]
{context_summary[:1500]}

[검증 대상: 챕터 {focus_chapter} '{ch['title']}']
{ch['content'][:2500]}"""
    else:
        # 기존 동작 — 전체 챕터 요약
        summaries = []
        for ch in chapters:
            content = ch["content"][:1000]
            summaries.append(f"Ch{ch['chapter_num']} '{ch['title']}': {content}")
        prompt = f"""아래 소설 챕터들의 일관성을 검증하세요.

{chr(10).join(summaries)}"""

    prompt += """

검사 항목:
1. 인물 모순 (행동/감정/위치 불일치)
2. 시간 순서 오류
3. 세계관 규칙 위반
4. 관계 모순
5. 톤/문체 급변

JSON으로 출력:
{{
  "issues": [
    {{"chapter": 2, "type": "인물 모순", "severity": "error", "detail": "설명", "suggestion": "수정 제안"}}
  ],
  "score": 85
}}

문제 없으면 {{"issues": [], "score": 100}}. JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
                                         "소설 편집자입니다. 일관성 오류를 꼼꼼히 찾으세요.",
                                         {"num_predict": 1000})
    result = force_korean(result).strip()

    validation = _parse_json_response(result)
    if not validation:
        validation = {"issues": [], "score": 0, "raw": result}

    # DB 저장
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_revisions (id, project_id, type, issues_json, score, created_at) "
            "VALUES (?, ?, 'consistency', ?, ?, ?)",
            (str(uuid.uuid4()), project_id,
             json.dumps(validation.get("issues", []), ensure_ascii=False),
             validation.get("score", 0),
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    validation["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(validation)


@novel_bp.route("/api/novel/revise/polish", methods=["POST"])
def novel_revise_polish():
    """고급 퇴고 — 한국어 자연화 + 변경률 가드 + 장르별 규칙 + 등급 판정
    ---
    tags: [Novel Writer - Revision]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            apply: {type: boolean, default: false, description: "퇴고 결과 적용 여부"}
            intensity: {type: string, enum: [gentle, standard, aggressive], default: standard, description: "퇴고 강도"}
            focus: {type: array, items: {type: string}, description: "집중 영역 (translation/rhythm/description/dialogue/all)"}
            spellcheck: {type: boolean, default: false, description: "맞춤법 검사 병합"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    intensity = data.get("intensity", "standard")
    focus_areas = data.get("focus") or ["all"]

    conn = _get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)
        ).fetchone()
        project = conn.execute(
            "SELECT genre FROM novel_projects WHERE id=?", (project_id,)
        ).fetchone()
    finally:
        conn.close()

    if not ch or not ch["content"]:
        return jsonify({"error": "chapter not found or empty"}), 404

    genre = dict(project).get("genre", "literary") if project else "literary"

    # 퇴고 전 품질 측정
    pre_quality = compute_korean_quality(ch["content"], genre="essay")
    pre_summary = get_quality_summary(pre_quality)

    # 장르별 퇴고 규칙
    genre_rules = {
        "fantasy": "웅장한 묘사 유지, 고어체 허용, 전투 장면 역동적, 마법 용어 보존",
        "sf": "논리적 전개 유지, 과학 용어 보존, 기술 묘사 정확성",
        "romance": "감정 묘사 세밀, 설렘/갈등 리듬 보존, 대화 비중 유지",
        "mystery": "서스펜스 유지, 단서/레드 헤링 보존, 반전 구조 유지",
        "horror": "짧은 문장 호흡, 감각 자극, 공포 분위기 보존",
        "literary": "문장 완성도, 은유/상징 보존, 여백과 여운",
        "web_novel": "가독성 극대화, 대화 비중 유지, 빠른 전개 보존",
    }

    # 강도별 변경률 가이드
    intensity_guide = {
        "gentle": "최소한만 수정. 명백한 오류만 교정. 변경률 10% 이하 목표.",
        "standard": "번역투 제거, 리듬 개선, 묘사 보강. 변경률 15-25% 목표.",
        "aggressive": "적극적 개선. 문장 재구성 허용. 변경률 25-35% 목표. 50% 초과 금지.",
    }

    # 집중 영역별 지시
    focus_instructions = {
        "translation": "번역투 집중 제거: '~에 대해→~를', '~를 통해→~로', '~에 있어서→~에서', '가지고 있다→형용사', 이중 피동 제거",
        "rhythm": "문장 리듬 개선: 동일 종결어미 4연속 금지, 긴→짧 교차, 단문 삽입",
        "description": "감각 묘사 보강: 시각/청각/후각/촉각 추가, Show Don't Tell",
        "dialogue": "'말했다' 반복 제거, 행동+대사 결합, 인물별 어조 차별화",
        "all": "번역투 제거, 리듬 개선, 감각 묘사 보강, 대화 다듬기 종합",
    }

    focus_text = "\n".join(f"- {focus_instructions.get(f, f)}" for f in focus_areas)

    prompt = f"""아래 소설 챕터를 퇴고하세요.

[장르] {genre} — {genre_rules.get(genre, '')}
[강도] {intensity_guide.get(intensity, intensity_guide['standard'])}
[집중 영역]
{focus_text}

[핵심 규칙]
1. 내용·줄거리·인물 행동은 절대 변경 금지
2. 고유명사·지명·인물명 100% 보존
3. 장르 특성 유지 (문체 전환 금지)
4. 원문에 없던 비유·수사 임의 추가 금지

[원문]
{ch['content']}

퇴고된 소설 텍스트만 출력하세요. 설명이나 주석 없이."""

    polish_system = "문체 전문가입니다. 소설 수준의 한국어로 다듬으세요. 내용 변경 금지.\n\n" + get_s1_prompt_rules()

    polished, tokens, elapsed = _generate(
        POLISH_MODEL, prompt, polish_system,
        {"num_predict": int(len(ch["content"]) * 1.3)},
        timeout=180
    )
    polished = force_korean(polished).strip()

    # 변경률 계산
    original_len = len(ch["content"])
    polished_len = len(polished)
    change_rate = 0.0
    if original_len > 0 and polished_len > 0:
        # Levenshtein 근사: 단어 단위 diff
        orig_words = ch["content"].split()
        pol_words = polished.split()
        common = len(set(orig_words) & set(pol_words))
        total = max(len(orig_words), len(pol_words))
        change_rate = round(1.0 - (common / total), 3) if total > 0 else 0.0

    # 과윤문 경고
    change_warning = None
    if change_rate > 0.50:
        change_warning = "과윤문 경고: 변경률 50% 초과 — 원문 훼손 가능"
        logger.warning(f"Polish over-edit: {project_id} ch{chapter_num} rate={change_rate:.0%}")
    elif change_rate > 0.30:
        change_warning = "주의: 변경률 30% 초과"

    # 퇴고 후 품질 측정
    post_quality = compute_korean_quality(polished, genre="essay") if not polished.startswith("Error:") else None
    post_summary = get_quality_summary(post_quality) if post_quality else None

    # 등급 판정 (im-not-ai 기반)
    grade = "D"
    if post_quality and not polished.startswith("Error:"):
        s1_residual = post_quality.get("metrics", {}).get("conclusion_pivot_count", 0)
        if change_rate <= 0.25 and s1_residual == 0:
            grade = "A"
        elif change_rate <= 0.35 and s1_residual <= 2:
            grade = "B"
        elif change_rate <= 0.50:
            grade = "C"

    # 한국어 자연화 품질 체크
    quality_metrics_data = None
    if not polished.startswith("Error:"):
        quality_result = compute_korean_quality(polished, genre="essay")
        quality_metrics_data = get_quality_summary(quality_result)

    # 적용 여부
    if data.get("apply", False) and not polished.startswith("Error:"):
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE novel_chapters SET content=?, word_count=?, status='revised', "
                "revision_count = revision_count + 1, updated_at=? "
                "WHERE project_id=? AND chapter_num=?",
                (polished, len(polished), datetime.now(timezone.utc).isoformat(),
                 project_id, chapter_num)
            )
            conn.commit()
        finally:
            conn.close()

    # 맞춤법 검사 (선택적)
    spell_summary = None
    if data.get("spellcheck", False) and not polished.startswith("Error:"):
        try:
            spell_result = spellcheck_long(polished)
            spell_summary = get_spellcheck_summary(spell_result)
            if spell_result["success"] and spell_result["total_errors"] > 0:
                polished = spell_result["checked"]
        except Exception as e:
            logger.warning(f"Spellcheck in polish failed: {e}")

    resp = {
        "chapter_num": chapter_num,
        "original_length": original_len,
        "polished_length": polished_len,
        "polished": polished,
        "applied": data.get("apply", False),
        "revision": {
            "grade": grade,
            "intensity": intensity,
            "focus": focus_areas,
            "genre": genre,
            "change_rate": change_rate,
            "change_warning": change_warning,
        },
        "quality_before": pre_summary,
        "quality_after": post_summary,
        "quality_improvement": {
            "risk_score_delta": (pre_quality.get("risk_score", 0) - post_quality.get("risk_score", 0)) if post_quality else 0,
            "interference_delta": round(
                pre_quality.get("v2_interference_index", {}).get("weighted_total", 0) -
                (post_quality.get("v2_interference_index", {}).get("weighted_total", 0) if post_quality else 0), 3
            ),
        } if post_quality else None,
        "metadata": {"tokens": tokens, "duration_ms": elapsed},
    }
    if spell_summary:
        resp["spellcheck"] = spell_summary
    return jsonify(resp)


@novel_bp.route("/api/novel/revise/foreshadowing", methods=["POST"])
def novel_revise_foreshadowing():
    """복선 회수 확인
    ---
    tags: [Novel Writer - Revision]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT plot_json FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        chapters = conn.execute(
            "SELECT chapter_num, title, content FROM novel_chapters "
            "WHERE project_id=? AND content IS NOT NULL ORDER BY chapter_num",
            (project_id,)
        ).fetchall()
    finally:
        conn.close()

    if not chapters:
        return jsonify({"error": "no chapters"}), 404

    # 플롯에서 복선 계획 추출
    foreshadowing_plan = ""
    if project and project.get("plot_json"):
        try:
            plot = json.loads(project["plot_json"])
            if plot.get("foreshadowing_plan"):
                foreshadowing_plan = "\n".join(
                    f"- ch{f['plant_chapter']}에서 심기: {f['hint']} → ch{f.get('resolve_chapter', '?')}에서 회수"
                    for f in plot["foreshadowing_plan"]
                )
        except (json.JSONDecodeError, TypeError):
            pass

    # 챕터 요약
    ch_summaries = "\n".join(
        f"Ch{ch['chapter_num']}: {(ch['content'] or '')[:500]}"
        for ch in chapters
    )

    prompt = f"""소설의 복선 상태를 분석하세요.

[복선 계획]
{foreshadowing_plan or '(계획 없음 — 텍스트에서 자동 감지)'}

[챕터 내용 요약]
{ch_summaries[:4000]}

JSON으로 출력:
{{
  "planted": [{{"hint": "복선", "chapter": 2, "status": "planted/resolved/forgotten"}}],
  "unresolved": ["미회수 복선 목록"],
  "suggestions": ["복선 개선 제안"]
}}

JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
                                         "소설 편집자입니다. 복선 회수 상태를 정확히 분석하세요.",
                                         {"num_predict": 800})
    result = force_korean(result).strip()

    analysis = _parse_json_response(result)
    if not analysis:
        analysis = {"raw": result}

    # DB 저장
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_revisions (id, project_id, type, issues_json, created_at) "
            "VALUES (?, ?, 'foreshadowing', ?, ?)",
            (str(uuid.uuid4()), project_id,
             json.dumps(analysis, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    finally:
        conn.close()

    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(analysis)


# ---------------------------------------------------------------------------
# API: 내보내기
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/export", methods=["POST"])
def novel_export():
    """소설 내보내기 (텍스트/마크다운)
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            format: {type: string, enum: [text, markdown], default: markdown}
            author_name: {type: string, default: "WeRU.B AI"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    fmt = data.get("format", "markdown")
    author = data.get("author_name", "WeRU.B AI")

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404
        project = dict(project)

        chapters = conn.execute(
            "SELECT chapter_num, title, content FROM novel_chapters "
            "WHERE project_id=? AND content IS NOT NULL ORDER BY chapter_num",
            (project_id,)
        ).fetchall()
    finally:
        conn.close()

    if not chapters:
        return jsonify({"error": "no chapters to export"}), 404

    title = project["title"]

    if fmt == "markdown":
        parts = [f"# {title}\n\n*{author}*\n"]
        for ch in chapters:
            ch_title = ch["title"] or f"Chapter {ch['chapter_num']}"
            parts.append(f"\n---\n\n## {ch_title}\n\n{ch['content']}")
        output = "\n".join(parts)
    else:
        sep = "─" * 40
        parts = [f"{title}\n{'=' * len(title)}\n{author}\n"]
        for ch in chapters:
            ch_title = ch["title"] or f"Chapter {ch['chapter_num']}"
            parts.append(f"\n{sep}\n\n{ch_title}\n\n{ch['content']}")
        output = "\n".join(parts)

    total_words = sum(len(ch["content"] or "") for ch in chapters)

    return jsonify({
        "title": title,
        "author": author,
        "format": fmt,
        "content": output,
        "total_words": total_words,
        "chapters": len(chapters),
    })


# ---------------------------------------------------------------------------
# API: 퇴고 이력
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/revisions/<project_id>", methods=["GET"])
def novel_revisions_list(project_id):
    """퇴고 이력 조회
    ---
    tags: [Novel Writer - Revision]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM novel_revisions WHERE project_id=? ORDER BY created_at DESC LIMIT 50",
            (project_id,)
        ).fetchall()
    finally:
        conn.close()

    revisions = []
    for r in rows:
        d = dict(r)
        if d.get("issues_json"):
            try:
                d["issues"] = json.loads(d["issues_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        revisions.append(d)

    return jsonify({"revisions": revisions, "count": len(revisions)})


# ===========================================================================
# Sprint 2 — 미구현 API 완성 (14 API)
# ===========================================================================

# ---------------------------------------------------------------------------
# API: 시놉시스 수정
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/synopsis/<project_id>", methods=["PUT"])
def novel_synopsis_update(project_id):
    """시놉시스 수정
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            premise: {type: string}
            act1_setup: {type: string}
            act2_conflict: {type: string}
            act3_resolution: {type: string}
            themes: {type: array, items: {type: string}}
            ending_type: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    conn = _get_db()
    try:
        row = conn.execute("SELECT synopsis_json FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "project not found"}), 404
        existing = {}
        if row["synopsis_json"]:
            try:
                existing = json.loads(row["synopsis_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        existing.update({k: v for k, v in data.items() if v is not None})
        conn.execute(
            "UPDATE novel_projects SET synopsis_json=?, updated_at=? WHERE id=?",
            (json.dumps(existing, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), project_id)
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(existing)


# ---------------------------------------------------------------------------
# API: 플롯 검증 / 수정
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/plot/validate", methods=["POST"])
def novel_plot_validate():
    """플롯 일관성 검증
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()
    if not project:
        return jsonify({"error": "project not found"}), 404
    project = dict(project)

    plot_json = project.get("plot_json", "")
    synopsis_json = project.get("synopsis_json", "")
    if not plot_json:
        return jsonify({"error": "plot not generated yet"}), 404

    prompt = f"""다음 소설의 플롯 구조를 검증하세요.

[시놉시스]
{synopsis_json}

[플롯 구조]
{plot_json}

검증 항목:
1. 각 막의 비율이 적절한가 (1막 25%, 2막 50%, 3막 25%)
2. 전환점이 논리적으로 연결되는가
3. 캐릭터 아크가 플롯과 일치하는가
4. 복선이 적절히 배치되었는가
5. 결말이 갈등을 해결하는가

JSON으로 출력:
{{
  "valid": true/false,
  "score": 0~100,
  "issues": [{{"type": "문제유형", "detail": "설명", "suggestion": "개선안"}}],
  "strengths": ["장점 목록"]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(STRUCTURE_MODEL, prompt,
        "소설 구조 분석가입니다. 플롯의 논리적 일관성을 검증하세요. 한국어로 작성.",
        {"num_predict": 2000, "temperature": 0.3})
    result = force_korean(result).strip()

    validation = _parse_json_response(result) or {"raw_output": result}
    validation["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(validation)


@novel_bp.route("/api/novel/plot/<project_id>", methods=["PUT"])
def novel_plot_update(project_id):
    """플롯 수정
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
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
        row = conn.execute("SELECT plot_json FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return jsonify({"error": "project not found"}), 404
        existing = {}
        if row["plot_json"]:
            try:
                existing = json.loads(row["plot_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(existing, dict):
            existing.update({k: v for k, v in data.items() if v is not None})
        else:
            existing = data
        conn.execute(
            "UPDATE novel_projects SET plot_json=?, updated_at=? WHERE id=?",
            (json.dumps(existing, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), project_id)
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(existing)


# ---------------------------------------------------------------------------
# API: 아웃라인 챕터 수정
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/outline/chapter/<int:chapter_num>", methods=["PUT"])
def novel_outline_chapter_update(chapter_num):
    """개별 챕터 아웃라인 수정
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: chapter_num, type: integer, required: true}
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            title: {type: string}
            synopsis: {type: string}
            scenes: {type: array}
            chapter_hook: {type: string}
            pov_character: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)
        ).fetchone()
        if not ch:
            # 챕터 레코드 없으면 생성
            ch_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO novel_chapters (id, project_id, chapter_num, title, scenes_json, "
                "pov_character, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ch_id, project_id, chapter_num, data.get("title", f"챕터 {chapter_num}"),
                 json.dumps(data.get("scenes", []), ensure_ascii=False),
                 data.get("pov_character"), now)
            )
        else:
            updates = []
            params = []
            for field in ("title", "synopsis", "chapter_hook", "pov_character"):
                if field in data:
                    updates.append(f"{field}=?")
                    params.append(data[field])
            if "scenes" in data:
                updates.append("scenes_json=?")
                params.append(json.dumps(data["scenes"], ensure_ascii=False))
            if updates:
                updates.append("updated_at=?")
                params.append(now)
                params.extend([project_id, chapter_num])
                conn.execute(
                    f"UPDATE novel_chapters SET {', '.join(updates)} "
                    "WHERE project_id=? AND chapter_num=?", params
                )
        conn.commit()

        # 업데이트된 결과 반환
        ch = conn.execute(
            "SELECT * FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)
        ).fetchone()
    finally:
        conn.close()

    result = dict(ch) if ch else {}
    if result.get("scenes_json"):
        try:
            result["scenes"] = json.loads(result["scenes_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 챕터 SSE 스트리밍
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/chapter/write/stream", methods=["POST"])
def novel_chapter_write_stream():
    """챕터 SSE 스트리밍 생성
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            additional_direction: {type: string}
            model: {type: string}
            references:
              type: object
            reference_strength: {type: string, enum: [strong, moderate, subtle], default: moderate}
    responses:
      200:
        description: SSE stream
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404
        project = dict(project)
        component_refs = data.get("references")
        context, ch_dict = _build_novel_context(
            project, chapter_num, conn, component_refs=component_refs
        )
    finally:
        conn.close()

    model = data.get("model") or NOVEL_MODEL
    settings = {}
    if project.get("settings_json"):
        try:
            settings = json.loads(project["settings_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    tone = settings.get("tone", "warm")
    pov = settings.get("pov", "third_person")
    chapter_length = settings.get("chapter_length", 5000)
    genre = project.get("genre", "fantasy")
    genre_cfg = GENRE_PROMPTS.get(genre, {})

    system = f"""{NOVEL_CRAFT_RULES}
[장르] {genre} — {genre_cfg.get('style_note', '')}
[톤] {tone} [시점] {pov}
약 {chapter_length}자로 작성하세요. 한국어로 작성."""

    additional = data.get("additional_direction", "")
    ch_title = ch_dict.get("title", f"챕터 {chapter_num}")
    prompt = f"""{context}

[챕터 {chapter_num}: {ch_title}]
{ch_dict.get('synopsis', f'챕터 {chapter_num}을 작성하세요.')}
{f'[추가 지시] {additional}' if additional else ''}
이 챕터를 소설 수준으로 작성하세요."""

    def sse_gen():
        try:
            # v2.124.99 — `_generate_stream`이 `StreamChunk` 제너레이터를 돌려준다.
            full_text = []
            for chunk in _generate_stream(model, prompt, system,
                                          {"num_predict": int(chapter_length * 1.2)}):
                token = force_korean_token(chunk.text)
                if token:
                    full_text.append(token)
                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                if chunk.done:
                    content = force_korean("".join(full_text))
                    word_count = len(content)
                    ch_id = ch_dict.get("id") or str(uuid.uuid4())
                    now = datetime.now(timezone.utc).isoformat()
                    conn2 = _get_db()
                    try:
                        if ch_dict.get("id"):
                            conn2.execute(
                                "UPDATE novel_chapters SET content=?, word_count=?, "
                                "status='draft', updated_at=? WHERE project_id=? AND chapter_num=?",
                                (content, word_count, now, project_id, chapter_num))
                        else:
                            conn2.execute(
                                "INSERT INTO novel_chapters (id, project_id, chapter_num, title, "
                                "content, word_count, status, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                                (ch_id, project_id, chapter_num, ch_title, content, word_count, now))
                        conn2.commit()
                    finally:
                        conn2.close()
                    yield f"data: {json.dumps({'done': True, 'chapter_id': ch_id, 'word_count': word_count}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(sse_gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# API: 챕터 재작성
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/chapter/rewrite", methods=["POST"])
def novel_chapter_rewrite():
    """챕터/장면 재작성
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            scene_num: {type: integer, description: "특정 장면만 재작성 (선택)"}
            direction: {type: string, description: "재작성 방향 지시"}
            keep_elements: {type: array, items: {type: string}, description: "유지할 요소"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    conn = _get_db()
    try:
        ch = conn.execute(
            "SELECT * FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)
        ).fetchone()
    finally:
        conn.close()

    if not ch or not ch["content"]:
        return jsonify({"error": "chapter content not found — write first"}), 404

    original = ch["content"]
    direction = data.get("direction", "더 나은 품질로 재작성")
    keep = data.get("keep_elements", [])
    scene_num = data.get("scene_num")

    keep_inst = f"유지할 요소: {', '.join(keep)}" if keep else ""
    scope = f"장면 {scene_num}만" if scene_num else "전체"

    prompt = f"""아래 소설 챕터의 {scope}를 재작성하세요.

[재작성 방향] {direction}
{keep_inst}

[원문]
{original}

재작성된 소설 텍스트만 출력하세요."""

    system = f"""{NOVEL_CRAFT_RULES}
재작성 전문가입니다. 원문의 핵심 줄거리를 유지하되 지시에 따라 개선하세요. 한국어로 작성."""

    content, tokens, elapsed = _generate(
        NOVEL_MODEL, prompt, system,
        {"num_predict": int(len(original) * 1.3)}, timeout=180)
    content = force_korean(content).strip()
    if content.startswith("Error:"):
        return jsonify({"error": content}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(content, genre="essay")
    quality_metrics_data = get_quality_summary(quality_result)
    quality_metrics_data["retry_count"] = 0

    if quality_result["risk_band"] == "high":
        logger.info(f"Novel rewrite quality high-risk (score={quality_result['risk_score']}), regenerating")
        enhanced_system = system + "\n\n" + get_s1_prompt_rules()
        content2, tokens2, elapsed2 = _generate(
            NOVEL_MODEL, prompt, enhanced_system,
            {"num_predict": int(len(original) * 1.3)}, timeout=180)
        content2 = force_korean(content2).strip()
        if not content2.startswith("Error:"):
            content = content2
            tokens += tokens2
            elapsed += elapsed2
            quality_result = compute_korean_quality(content, genre="essay")
            quality_metrics_data = get_quality_summary(quality_result)
            quality_metrics_data["retry_count"] = 1

    # DB 저장
    word_count = len(content)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE novel_chapters SET content=?, word_count=?, "
            "revision_count=revision_count+1, status='draft', updated_at=? "
            "WHERE project_id=? AND chapter_num=?",
            (content, word_count, now, project_id, chapter_num))
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "chapter_num": chapter_num,
        "content": content,
        "metadata": {"word_count": word_count, "tokens": tokens, "duration_ms": elapsed},
        "quality_metrics": quality_metrics_data,
    })


# ---------------------------------------------------------------------------
# API: 장면(Scene) 전문 생성 (5종)
# ---------------------------------------------------------------------------

_SCENE_TYPE_PROMPTS = {
    "write": {
        "system": "소설가입니다. 개별 장면을 집필하세요.",
        "instruction": "이 장면을 소설 수준으로 작성하세요.",
    },
    "transition": {
        "system": "소설가입니다. 장면 전환문을 작성하세요.",
        "instruction": "이전 장면에서 다음 장면으로 자연스럽게 전환하는 문단을 작성하세요. "
                       "시간 경과, 장소 이동, 분위기 변화를 감각 묘사로 표현.",
    },
    "dialogue": {
        "system": "대화 전문 소설가입니다. 인물 간 대화 장면을 작성하세요. "
                  "대화 태그 대신 행동 묘사, 서브텍스트, 인물별 말투 차별화.",
        "instruction": "이 대화 장면을 작성하세요. 대화:지문 비율 = 6:4.",
    },
    "description": {
        "system": "묘사 전문 소설가입니다. 배경/풍경/분위기를 감각적으로 묘사하세요. "
                  "시각/청각/후각/촉각/미각 중 3가지 이상 활용.",
        "instruction": "이 장면의 배경과 분위기를 소설 수준으로 묘사하세요.",
    },
    "inner_voice": {
        "system": "내면 묘사 전문 소설가입니다. 의식의 흐름, 감정의 파동, 기억의 편린을 "
                  "풍부하게 표현하세요. Show Don't Tell.",
        "instruction": "이 인물의 내면 독백을 작성하세요. 감정을 직접 명명하지 말고 "
                       "신체 반응, 감각, 연상으로 표현.",
    },
}


def _scene_handler(scene_type):
    """장면 5종 API의 공통 핸들러."""
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404
        project = dict(project)

        context, ch_dict = _build_novel_context(
            project, chapter_num or 1, conn, component_refs=data.get("references")
        )
    finally:
        conn.close()

    cfg = _SCENE_TYPE_PROMPTS[scene_type]
    genre = project.get("genre", "fantasy")
    genre_cfg = GENRE_PROMPTS.get(genre, {})

    target_length = data.get("target_length", 1500)
    characters = data.get("characters", [])
    situation = data.get("situation", "")
    emotion_arc = data.get("emotion_arc", "")

    char_inst = f"[등장인물] {', '.join(characters)}" if characters else ""
    situation_inst = f"[상황] {situation}" if situation else ""
    emotion_inst = f"[감정 변화] {emotion_arc}" if emotion_arc else ""
    direction = data.get("additional_direction", "")

    prompt = f"""{context}

{char_inst}
{situation_inst}
{emotion_inst}
{f'[추가 지시] {direction}' if direction else ''}

{cfg['instruction']}
약 {target_length}자로 작성하세요."""

    system = f"""{NOVEL_CRAFT_RULES}
{cfg['system']}
[장르] {genre} — {genre_cfg.get('style_note', '')}
한국어로 작성하세요."""

    quality = data.get("quality", "quick")
    if quality == "orchestrated":
        content = _orchestrated_write(prompt, system, target_length)
        model_used = "orchestrated"
        tokens, elapsed = 0, 0
    else:
        content, tokens, elapsed = _generate(
            NOVEL_MODEL, prompt, system,
            {"num_predict": int(target_length * 1.3)}, timeout=120)
        content = force_korean(content).strip()
        model_used = NOVEL_MODEL

    if content.startswith("Error:"):
        return jsonify({"error": content}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(content, genre="essay")
    quality_metrics_data = get_quality_summary(quality_result)
    quality_metrics_data["retry_count"] = 0

    if quality_result["risk_band"] == "high" and quality != "orchestrated":
        logger.info(f"Scene quality high-risk (score={quality_result['risk_score']}), regenerating")
        enhanced_system = system + "\n\n" + get_s1_prompt_rules()
        content2, tokens2, elapsed2 = _generate(
            NOVEL_MODEL, prompt, enhanced_system,
            {"num_predict": int(target_length * 1.3)}, timeout=120)
        content2 = force_korean(content2).strip()
        if not content2.startswith("Error:"):
            content = content2
            tokens += tokens2
            elapsed += elapsed2
            quality_result = compute_korean_quality(content, genre="essay")
            quality_metrics_data = get_quality_summary(quality_result)
            quality_metrics_data["retry_count"] = 1

    return jsonify({
        "scene_type": scene_type,
        "content": content,
        "metadata": {"word_count": len(content), "model": model_used,
                     "tokens": tokens, "duration_ms": elapsed},
        "quality_metrics": quality_metrics_data,
    })


@novel_bp.route("/api/novel/scene/write", methods=["POST"])
def novel_scene_write():
    """개별 장면 생성
    ---
    tags: [Novel Writer - Scene]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            characters: {type: array, items: {type: string}}
            situation: {type: string}
            emotion_arc: {type: string}
            target_length: {type: integer, default: 1500}
            quality: {type: string, enum: [quick, orchestrated], default: quick}
            additional_direction: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    return _scene_handler("write")


@novel_bp.route("/api/novel/scene/transition", methods=["POST"])
def novel_scene_transition():
    """장면 전환문 생성
    ---
    tags: [Novel Writer - Scene]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            from_scene: {type: string, description: "이전 장면 요약"}
            to_scene: {type: string, description: "다음 장면 요약"}
            target_length: {type: integer, default: 500}
    responses:
      200:
        schema:
          type: object
    """
    return _scene_handler("transition")


@novel_bp.route("/api/novel/scene/dialogue", methods=["POST"])
def novel_scene_dialogue():
    """대화 장면 전문 생성
    ---
    tags: [Novel Writer - Scene]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, characters]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            characters: {type: array, items: {type: string}}
            situation: {type: string}
            emotion_arc: {type: string}
            target_length: {type: integer, default: 2000}
            quality: {type: string, enum: [quick, orchestrated], default: quick}
    responses:
      200:
        schema:
          type: object
    """
    return _scene_handler("dialogue")


@novel_bp.route("/api/novel/scene/description", methods=["POST"])
def novel_scene_description():
    """묘사/배경 전문 생성
    ---
    tags: [Novel Writer - Scene]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            situation: {type: string, description: "묘사할 대상/장소/상황"}
            target_length: {type: integer, default: 1000}
            quality: {type: string, enum: [quick, orchestrated], default: quick}
    responses:
      200:
        schema:
          type: object
    """
    return _scene_handler("description")


@novel_bp.route("/api/novel/scene/inner_voice", methods=["POST"])
def novel_scene_inner_voice():
    """내면 독백 전문 생성
    ---
    tags: [Novel Writer - Scene]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            characters: {type: array, items: {type: string}, description: "시점 인물"}
            situation: {type: string}
            emotion_arc: {type: string}
            target_length: {type: integer, default: 1200}
            quality: {type: string, enum: [quick, orchestrated], default: quick}
    responses:
      200:
        schema:
          type: object
    """
    return _scene_handler("inner_voice")


# ---------------------------------------------------------------------------
# API: 퇴고 — 문체 통일 / 전체 퇴고
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/revise/style", methods=["POST"])
def novel_revise_style():
    """문체 통일 검사
    ---
    tags: [Novel Writer - Revision]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapters: {type: array, items: {type: integer}, description: "검사할 챕터 (null=전체)"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404

        target_chapters = data.get("chapters")
        if target_chapters:
            chapters = conn.execute(
                f"SELECT chapter_num, content FROM novel_chapters "
                f"WHERE project_id=? AND chapter_num IN ({','.join('?' * len(target_chapters))}) "
                f"AND content IS NOT NULL ORDER BY chapter_num",
                [project_id] + target_chapters
            ).fetchall()
        else:
            chapters = conn.execute(
                "SELECT chapter_num, content FROM novel_chapters "
                "WHERE project_id=? AND content IS NOT NULL ORDER BY chapter_num",
                (project_id,)
            ).fetchall()
    finally:
        conn.close()

    if not chapters:
        return jsonify({"error": "no written chapters found"}), 404

    # 각 챕터 첫 500자로 문체 비교
    samples = "\n\n".join(
        f"[챕터 {c['chapter_num']}]\n{c['content'][:500]}..."
        for c in chapters
    )

    prompt = f"""다음 챕터들의 문체 통일성을 검사하세요.

{samples}

JSON으로 출력:
{{
  "consistent": true/false,
  "overall_tone": "전체 톤",
  "issues": [
    {{"chapter": 번호, "type": "tone_shift|vocabulary_shift|pov_shift|rhythm_shift",
      "detail": "설명", "suggestion": "개선안"}}
  ],
  "statistics": {{
    "avg_sentence_length_by_chapter": {{}},
    "dialogue_ratio_by_chapter": {{}}
  }}
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(STRUCTURE_MODEL, prompt,
        "문체 분석가입니다. 챕터 간 문체 일관성을 검사하세요. 한국어.",
        {"num_predict": 2000, "temperature": 0.3})
    result = force_korean(result).strip()

    analysis = _parse_json_response(result) or {"raw_output": result}

    # 퇴고 이력 저장
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_revisions (id, project_id, type, issues_json, score, created_at) "
            "VALUES (?, ?, 'style', ?, ?, ?)",
            (str(uuid.uuid4()), project_id,
             json.dumps(analysis, ensure_ascii=False),
             analysis.get("score") if isinstance(analysis, dict) else None,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()

    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                            "chapters_analyzed": len(chapters)}
    return jsonify(analysis)


@novel_bp.route("/api/novel/revise/full", methods=["POST"])
def novel_revise_full():
    """전체 퇴고 (일관성→문체→복선→교정 순차)
    ---
    tags: [Novel Writer - Revision]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapters: {type: array, items: {type: integer}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    # 4단계 순차 실행 — 각 기존 API를 내부 호출
    results = {}
    total_tokens = 0
    total_elapsed = 0

    # Stage 1: 일관성
    with novel_bp.test_request_context(
        "/api/novel/revise/consistency", method="POST",
        json={"project_id": project_id, "chapters": data.get("chapters")}
    ):
        try:
            resp = novel_revise_consistency()
            if hasattr(resp, "get_json"):
                r = resp.get_json()
            else:
                r = resp[0].get_json() if isinstance(resp, tuple) else {}
            results["consistency"] = r
            meta = r.get("metadata", {})
            total_tokens += meta.get("tokens", 0)
            total_elapsed += meta.get("duration_ms", 0)
        except Exception as e:
            results["consistency"] = {"error": str(e)}

    # Stage 2: 문체
    with novel_bp.test_request_context(
        "/api/novel/revise/style", method="POST",
        json={"project_id": project_id, "chapters": data.get("chapters")}
    ):
        try:
            resp = novel_revise_style()
            if hasattr(resp, "get_json"):
                r = resp.get_json()
            else:
                r = resp[0].get_json() if isinstance(resp, tuple) else {}
            results["style"] = r
            meta = r.get("metadata", {})
            total_tokens += meta.get("tokens", 0)
            total_elapsed += meta.get("duration_ms", 0)
        except Exception as e:
            results["style"] = {"error": str(e)}

    # Stage 3: 복선
    with novel_bp.test_request_context(
        "/api/novel/revise/foreshadowing", method="POST",
        json={"project_id": project_id}
    ):
        try:
            resp = novel_revise_foreshadowing()
            if hasattr(resp, "get_json"):
                r = resp.get_json()
            else:
                r = resp[0].get_json() if isinstance(resp, tuple) else {}
            results["foreshadowing"] = r
            meta = r.get("metadata", {})
            total_tokens += meta.get("tokens", 0)
            total_elapsed += meta.get("duration_ms", 0)
        except Exception as e:
            results["foreshadowing"] = {"error": str(e)}

    # Stage 4: 교정
    with novel_bp.test_request_context(
        "/api/novel/revise/polish", method="POST",
        json={"project_id": project_id, "chapters": data.get("chapters")}
    ):
        try:
            resp = novel_revise_polish()
            if hasattr(resp, "get_json"):
                r = resp.get_json()
            else:
                r = resp[0].get_json() if isinstance(resp, tuple) else {}
            results["polish"] = r
            meta = r.get("metadata", {})
            total_tokens += meta.get("tokens", 0)
            total_elapsed += meta.get("duration_ms", 0)
        except Exception as e:
            results["polish"] = {"error": str(e)}

    # 종합 점수
    scores = []
    for key in ("consistency", "style", "foreshadowing", "polish"):
        r = results.get(key, {})
        if isinstance(r, dict) and r.get("score"):
            scores.append(r["score"])

    overall_score = int(sum(scores) / len(scores)) if scores else None

    # 퇴고 이력 저장
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_revisions (id, project_id, type, issues_json, score, created_at) "
            "VALUES (?, ?, 'full', ?, ?, ?)",
            (str(uuid.uuid4()), project_id,
             json.dumps(results, ensure_ascii=False),
             overall_score,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "revision_report": results,
        "overall_score": overall_score,
        "metadata": {"total_tokens": total_tokens, "total_duration_ms": total_elapsed,
                     "stages_completed": 4},
    })


# ---------------------------------------------------------------------------
# API: 표지 생성 (ComfyUI 연동)
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/cover/generate", methods=["POST"])
def novel_cover_generate():
    """표지 이미지 생성 (ComfyUI 연동)
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            style: {type: string, enum: [realistic, illustration, watercolor, manga, abstract], default: illustration}
            additional_prompt: {type: string}
            width: {type: integer, default: 768}
            height: {type: integer, default: 1024}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()
    if not project:
        return jsonify({"error": "project not found"}), 404
    project = dict(project)

    # LLM으로 표지 프롬프트 생성
    genre = project.get("genre", "fantasy")
    title = project.get("title", "")
    logline = project.get("logline", "")
    synopsis = ""
    if project.get("synopsis_json"):
        try:
            syn = json.loads(project["synopsis_json"])
            synopsis = syn.get("premise", "")
        except (json.JSONDecodeError, TypeError):
            pass

    style = data.get("style", "illustration")
    additional = data.get("additional_prompt", "")

    prompt = f"""소설 표지 이미지를 위한 영어 프롬프트를 생성하세요.

[제목] {title}
[장르] {genre}
[로그라인] {logline}
[시놉시스] {synopsis}
[스타일] {style}
{f'[추가 요청] {additional}' if additional else ''}

다음 JSON으로 출력:
{{
  "prompt": "영어 이미지 생성 프롬프트 (100단어 이내)",
  "negative_prompt": "제외할 요소"
}}
JSON만 출력."""

    result, _, _ = _generate(FAST_MODEL, prompt,
        "이미지 프롬프트 전문가입니다. 소설 표지에 적합한 영어 프롬프트를 생성하세요.",
        {"num_predict": 500, "temperature": 0.7})
    img_prompt_data = _parse_json_response(result)

    if not img_prompt_data or not img_prompt_data.get("prompt"):
        return jsonify({"error": "failed to generate image prompt",
                       "raw_output": result}), 500

    # ComfyUI /api/image/generate 내부 호출
    width = data.get("width", 768)
    height = data.get("height", 1024)

    try:
        sess, base = _get_session()
        img_resp = sess.post(f"http://localhost:8585/api/image/generate", json={
            "prompt": img_prompt_data["prompt"],
            "negative_prompt": img_prompt_data.get("negative_prompt", ""),
            "width": width,
            "height": height,
            "model": "stable-diffusion",
            "metadata": {"project_id": project_id, "type": "novel_cover"},
        }, timeout=120)

        if img_resp.status_code == 200:
            img_result = img_resp.json()
            return jsonify({
                "project_id": project_id,
                "title": title,
                "image": img_result,
                "prompt_used": img_prompt_data,
            })
        else:
            return jsonify({
                "project_id": project_id,
                "prompt_generated": img_prompt_data,
                "image_error": f"ComfyUI returned {img_resp.status_code}",
                "message": "이미지 생성 실패. 프롬프트는 생성됨 — /api/image/generate로 직접 시도 가능",
            })
    except Exception as e:
        return jsonify({
            "project_id": project_id,
            "prompt_generated": img_prompt_data,
            "image_error": str(e),
            "message": "ComfyUI 연결 실패. 프롬프트는 생성됨.",
        })


# ===========================================================================
# Sprint 3 — 집필 보조 (8 API) + 분석/평가 (7 API)
# ===========================================================================

# ---------------------------------------------------------------------------
# 집필 보조: 브레인스토밍
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/brainstorm", methods=["POST"])
def novel_brainstorm():
    """아이디어 브레인스토밍 — 로그라인 + 시놉시스 후보 생성
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            keywords: {type: array, items: {type: string}}
            genre: {type: string, default: fantasy}
            theme: {type: string}
            count: {type: integer, default: 5}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    keywords = data.get("keywords", [])
    genre = data.get("genre", "fantasy")
    theme = data.get("theme", "")
    count = min(data.get("count", 5), 10)
    genre_cfg = GENRE_PROMPTS.get(genre, {})

    prompt = f"""다음 조건으로 소설 아이디어를 {count}개 생성하세요.

[키워드] {', '.join(keywords) if keywords else '자유'}
[장르] {genre} — {genre_cfg.get('style_note', '')}
{f'[테마] {theme}' if theme else ''}

JSON으로 출력:
{{
  "ideas": [
    {{
      "logline": "한 줄 요약 (50자 이내)",
      "synopsis": "시놉시스 (200자)",
      "genre_fit": "이 장르에 적합한 이유",
      "unique_hook": "차별화 포인트",
      "estimated_chapters": 숫자
    }}
  ]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "소설 기획자입니다. 독창적이고 매력적인 소설 아이디어를 생성하세요. 한국어.",
        {"num_predict": 3000, "temperature": 0.9})
    result = force_korean(result).strip()
    ideas = _parse_json_response(result) or {"raw_output": result}
    ideas["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(ideas)


# ---------------------------------------------------------------------------
# 집필 보조: 분량 확장 / 압축
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/chapter/expand", methods=["POST"])
def novel_chapter_expand():
    """챕터 분량 확장 — 감각 묘사/내면/대화 추가
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            target_words: {type: integer, default: 5000}
            focus: {type: string, enum: [description, dialogue, inner_voice, all], default: all}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    conn = _get_db()
    try:
        ch = conn.execute(
            "SELECT content, word_count FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)).fetchone()
    finally:
        conn.close()
    if not ch or not ch["content"]:
        return jsonify({"error": "chapter not found or empty"}), 404

    target = data.get("target_words", 5000)
    focus = data.get("focus", "all")
    focus_map = {
        "description": "감각 묘사 (시각/청각/후각/촉각)를 대폭 추가",
        "dialogue": "대화 장면을 확장하고 지문 추가",
        "inner_voice": "내면 독백과 심리 묘사를 깊이 추가",
        "all": "감각 묘사, 대화, 내면 독백을 균형 있게 추가",
    }

    prompt = f"""아래 소설 텍스트를 약 {target}자로 확장하세요.
현재 {ch['word_count']}자 → 목표 {target}자.

[확장 방향] {focus_map.get(focus, focus_map['all'])}
- 줄거리와 핵심 사건은 변경하지 마세요
- 새로운 장면을 추가하지 말고 기존 장면을 풍부하게
- Show Don't Tell 원칙

[원문]
{ch['content']}

확장된 소설 텍스트만 출력."""

    expand_system = f"{NOVEL_CRAFT_RULES}\n소설 확장 전문가입니다. 한국어로 작성.\n\n{get_s1_prompt_rules()}"
    content, tokens, elapsed = _generate(
        NOVEL_MODEL, prompt, expand_system,
        {"num_predict": int(target * 1.3)}, timeout=180)
    content = force_korean(content).strip()
    if content.startswith("Error:"):
        return jsonify({"error": content}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(content, genre="essay")
    quality_metrics_data = get_quality_summary(quality_result)

    # DB 저장
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE novel_chapters SET content=?, word_count=?, updated_at=? "
            "WHERE project_id=? AND chapter_num=?",
            (content, len(content), now, project_id, chapter_num))
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "chapter_num": chapter_num, "content": content,
        "metadata": {"original_words": ch["word_count"], "new_words": len(content),
                     "focus": focus, "tokens": tokens, "duration_ms": elapsed},
        "quality_metrics": quality_metrics_data,
    })


@novel_bp.route("/api/novel/chapter/condense", methods=["POST"])
def novel_chapter_condense():
    """챕터 압축 — 핵심 유지하며 축약
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            target_words: {type: integer, default: 3000}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    conn = _get_db()
    try:
        ch = conn.execute(
            "SELECT content, word_count FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)).fetchone()
    finally:
        conn.close()
    if not ch or not ch["content"]:
        return jsonify({"error": "chapter not found or empty"}), 404

    target = data.get("target_words", 3000)

    prompt = f"""아래 소설 텍스트를 약 {target}자로 압축하세요.
현재 {ch['word_count']}자 → 목표 {target}자.

- 핵심 사건과 감정 전환점은 반드시 유지
- 불필요한 반복 묘사 제거
- 대화의 핵심만 남기기
- 문장력은 유지하되 분량 축소

[원문]
{ch['content']}

압축된 소설 텍스트만 출력."""

    condense_system = "소설 편집자입니다. 핵심을 보존하며 압축하세요. 한국어.\n\n" + get_s1_prompt_rules()
    content, tokens, elapsed = _generate(
        NOVEL_MODEL, prompt, condense_system,
        {"num_predict": int(target * 1.3)}, timeout=180)
    content = force_korean(content).strip()
    if content.startswith("Error:"):
        return jsonify({"error": content}), 500

    # 한국어 자연화 품질 체크
    quality_result = compute_korean_quality(content, genre="essay")
    quality_metrics_data = get_quality_summary(quality_result)

    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE novel_chapters SET content=?, word_count=?, updated_at=? "
            "WHERE project_id=? AND chapter_num=?",
            (content, len(content), now, project_id, chapter_num))
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "chapter_num": chapter_num, "content": content,
        "metadata": {"original_words": ch["word_count"], "new_words": len(content),
                     "tokens": tokens, "duration_ms": elapsed},
        "quality_metrics": quality_metrics_data,
    })


# ---------------------------------------------------------------------------
# 집필 보조: 이름 생성기
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/naming", methods=["POST"])
def novel_naming():
    """이름 생성기 — 인물/장소/마법/기술/조직
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            project_id: {type: string}
            type: {type: string, enum: [character, place, magic, tech, org], default: character}
            count: {type: integer, default: 10}
            style: {type: string, enum: [korean, western, fantasy, japanese, chinese], default: korean}
            context: {type: string, description: "추가 맥락"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    name_type = data.get("type", "character")
    count = min(data.get("count", 10), 30)
    style = data.get("style", "korean")
    ctx = data.get("context", "")

    # 프로젝트 정보 로드 (선택)
    genre = "fantasy"
    project_id = data.get("project_id", "")
    if project_id:
        conn = _get_db()
        try:
            p = conn.execute("SELECT genre FROM novel_projects WHERE id=?", (project_id,)).fetchone()
            if p:
                genre = p["genre"]
        finally:
            conn.close()

    type_desc = {"character": "인물 이름", "place": "장소/지명", "magic": "마법/능력 이름",
                 "tech": "기술/장비 이름", "org": "조직/세력 이름"}

    prompt = f"""{type_desc.get(name_type, '이름')}을 {count}개 생성하세요.

[장르] {genre}
[스타일] {style}
{f'[맥락] {ctx}' if ctx else ''}

JSON으로 출력:
{{
  "names": [
    {{"name": "이름", "meaning": "의미/유래", "suitable_for": "적합한 용도"}}
  ]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(FAST_MODEL, prompt,
        f"이름 생성 전문가입니다. {genre} 장르, {style} 스타일. 한국어.",
        {"num_predict": 2000, "temperature": 0.9})
    result = force_korean(result).strip()
    names = _parse_json_response(result) or {"raw_output": result}
    names["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(names)


# ---------------------------------------------------------------------------
# 집필 보조: 작가의 벽 돌파
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/prompt/unblock", methods=["POST"])
def novel_prompt_unblock():
    """작가의 벽 돌파 — 막힌 지점 분석 + 3가지 전개 방향 제안
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            stuck_point: {type: string, description: "막힌 지점 설명"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404
        project = dict(project)

        # 최근 챕터 내용
        chapter_num = data.get("chapter_num")
        last_content = ""
        if chapter_num:
            ch = conn.execute(
                "SELECT content FROM novel_chapters WHERE project_id=? AND chapter_num=?",
                (project_id, chapter_num)).fetchone()
            if ch and ch["content"]:
                last_content = ch["content"][-3000:]
    finally:
        conn.close()

    stuck = data.get("stuck_point", "다음 전개를 모르겠음")
    synopsis = project.get("synopsis_json", "")

    prompt = f"""소설가가 집필 중 막혔습니다. 3가지 전개 방향을 제안하세요.

[제목] {project['title']}
[장르] {project.get('genre', 'fantasy')}
[시놉시스] {synopsis[:500]}
[막힌 지점] {stuck}
{f'[최근 내용]' + chr(10) + last_content if last_content else ''}

JSON으로 출력:
{{
  "diagnosis": "막힌 이유 분석",
  "suggestions": [
    {{
      "direction": "방향 1 이름",
      "description": "상세 설명 (200자)",
      "first_sentence": "이 방향의 첫 문장 예시",
      "risk": "이 방향의 장단점"
    }}
  ],
  "writing_tip": "지금 상황에 맞는 작법 팁"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "소설 멘토입니다. 작가의 창작 막힘을 진단하고 구체적인 전개 방향을 제안하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.8})
    result = force_korean(result).strip()
    suggestions = _parse_json_response(result) or {"raw_output": result}
    suggestions["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(suggestions)


# ---------------------------------------------------------------------------
# 집필 보조: 챕터 요약
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/chapter/summary", methods=["POST"])
def novel_chapter_summary():
    """챕터별 요약 생성 — 다음 챕터 컨텍스트용
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            max_length: {type: integer, default: 300}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    conn = _get_db()
    try:
        ch = conn.execute(
            "SELECT content FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)).fetchone()
    finally:
        conn.close()
    if not ch or not ch["content"]:
        return jsonify({"error": "chapter not found or empty"}), 404

    max_len = data.get("max_length", 300)

    prompt = f"""다음 소설 챕터를 {max_len}자 이내로 요약하세요.

[챕터 내용]
{ch['content']}

JSON으로 출력:
{{
  "summary": "요약",
  "key_events": ["핵심 사건 목록"],
  "emotion_tags": ["감정 태그"],
  "characters_appeared": ["등장 인물"],
  "cliffhanger": "다음 챕터로 이어지는 훅"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(FAST_MODEL, prompt,
        "소설 요약 전문가입니다. 핵심만 간결하게. 한국어.",
        {"num_predict": 1500, "temperature": 0.3})
    result = force_korean(result).strip()
    summary_data = _parse_json_response(result) or {"raw_output": result}

    # DB 캐시 저장
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO novel_chapter_summaries "
            "(id, project_id, chapter_num, summary, key_events, emotion_tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, chapter_num,
             summary_data.get("summary", ""),
             json.dumps(summary_data.get("key_events", []), ensure_ascii=False),
             json.dumps(summary_data.get("emotion_tags", []), ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()

    summary_data["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(summary_data)


# ---------------------------------------------------------------------------
# 집필 보조: 타임라인
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/timeline", methods=["POST"])
def novel_timeline():
    """타임라인 자동 생성 — 프로젝트 전체 시간축 추출
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            view: {type: string, enum: [chronological, character, location], default: chronological}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        chapters = conn.execute(
            "SELECT chapter_num, title, content FROM novel_chapters "
            "WHERE project_id=? AND content IS NOT NULL ORDER BY chapter_num",
            (project_id,)).fetchall()
    finally:
        conn.close()

    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    view = data.get("view", "chronological")
    chapter_snippets = "\n".join(
        f"[챕터 {c['chapter_num']}: {c.get('title', '')}]\n{c['content'][:800]}..."
        for c in chapters
    )

    prompt = f"""다음 소설의 타임라인을 추출하세요.
정렬 기준: {view}

{chapter_snippets}

JSON으로 출력:
{{
  "timeline": [
    {{
      "order": 1,
      "event_time": "작중 시간",
      "event": "사건 설명",
      "chapter": 챕터번호,
      "characters": ["관련 인물"],
      "location": "장소",
      "importance": "critical|major|normal|minor"
    }}
  ],
  "time_span": "전체 시간 범위",
  "parallel_events": ["동시에 일어나는 사건 쌍"]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "서사 분석가입니다. 소설의 시간축을 정확히 추출하세요. 한국어.",
        {"num_predict": 3000, "temperature": 0.3})
    result = force_korean(result).strip()
    timeline_data = _parse_json_response(result) or {"raw_output": result}

    # DB 저장
    if isinstance(timeline_data, dict) and timeline_data.get("timeline"):
        conn = _get_db()
        try:
            conn.execute("DELETE FROM novel_timeline_events WHERE project_id=?", (project_id,))
            for evt in timeline_data["timeline"]:
                conn.execute(
                    "INSERT INTO novel_timeline_events "
                    "(id, project_id, chapter_num, event_time, event_desc, characters, "
                    "location, importance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), project_id, evt.get("chapter"),
                     evt.get("event_time", ""), evt.get("event", ""),
                     json.dumps(evt.get("characters", []), ensure_ascii=False),
                     evt.get("location", ""), evt.get("importance", "normal"),
                     datetime.now(timezone.utc).isoformat()))
            conn.commit()
        finally:
            conn.close()

    timeline_data["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                                  "chapters_analyzed": len(chapters), "view": view}
    return jsonify(timeline_data)


# ---------------------------------------------------------------------------
# 집필 보조: 버전 비교
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/compare/versions", methods=["POST"])
def novel_compare_versions():
    """버전 비교 — 초안 vs 퇴고본 분석
    ---
    tags: [Novel Writer - Assistant]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, chapter_num]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            version_a: {type: string, default: "latest"}
            version_b: {type: string, default: "previous"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    chapter_num = data.get("chapter_num")
    if not project_id or chapter_num is None:
        return jsonify({"error": "project_id and chapter_num required"}), 400

    conn = _get_db()
    try:
        # 현재 버전
        current = conn.execute(
            "SELECT content, word_count FROM novel_chapters WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)).fetchone()
        # 저장된 버전들
        versions = conn.execute(
            "SELECT version_tag, content, word_count FROM novel_chapter_versions "
            "WHERE project_id=? AND chapter_num=? ORDER BY created_at DESC",
            (project_id, chapter_num)).fetchall()
    finally:
        conn.close()

    if not current or not current["content"]:
        return jsonify({"error": "current chapter not found"}), 404
    if not versions:
        return jsonify({"error": "no previous versions found — save a version first",
                       "tip": "챕터를 수정할 때 자동으로 이전 버전이 저장됩니다."}), 404

    prev = dict(versions[0])
    text_a = current["content"][:3000]
    text_b = prev["content"][:3000] if prev.get("content") else ""

    prompt = f"""두 버전의 소설 텍스트를 비교 분석하세요.

[버전 A: 현재]
{text_a}

[버전 B: {prev.get('version_tag', 'previous')}]
{text_b}

JSON으로 출력:
{{
  "word_count_change": {{"a": 숫자, "b": 숫자, "diff": 숫자}},
  "improvements": ["개선된 점"],
  "regressions": ["퇴보한 점"],
  "style_shift": "문체 변화 설명",
  "recommendation": "어느 버전이 더 나은지 + 이유"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(FAST_MODEL, prompt,
        "소설 편집자입니다. 두 버전을 객관적으로 비교하세요. 한국어.",
        {"num_predict": 1500, "temperature": 0.3})
    result = force_korean(result).strip()
    comparison = _parse_json_response(result) or {"raw_output": result}
    comparison["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                              "version_a": "current", "version_b": prev.get("version_tag")}
    return jsonify(comparison)


# ===========================================================================
# 분석/평가 시스템 (7 API)
# ===========================================================================

def _load_chapters_for_analysis(project_id, target_chapters=None):
    """분석용 챕터 로드 (공통)."""
    conn = _get_db()
    try:
        if target_chapters:
            placeholders = ",".join("?" * len(target_chapters))
            chapters = conn.execute(
                f"SELECT chapter_num, title, content, word_count FROM novel_chapters "
                f"WHERE project_id=? AND chapter_num IN ({placeholders}) "
                f"AND content IS NOT NULL ORDER BY chapter_num",
                [project_id] + list(target_chapters)
            ).fetchall()
        else:
            chapters = conn.execute(
                "SELECT chapter_num, title, content, word_count FROM novel_chapters "
                "WHERE project_id=? AND content IS NOT NULL ORDER BY chapter_num",
                (project_id,)).fetchall()
    finally:
        conn.close()
    return [dict(c) for c in chapters]


@novel_bp.route("/api/novel/analyze/pacing", methods=["POST"])
def novel_analyze_pacing():
    """페이싱 분석 — 챕터별 긴장도 곡선
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapters: {type: array, items: {type: integer}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id, data.get("chapters"))
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    samples = "\n".join(f"[ch{c['chapter_num']}] ({c['word_count']}자) {c['content'][:400]}..."
                        for c in chapters)

    prompt = f"""다음 소설의 페이싱(리듬)을 분석하세요.

{samples}

JSON으로 출력:
{{
  "chapters": [
    {{"ch": 번호, "tension": 0.0~1.0, "action_ratio": 0.0~1.0,
      "dialogue_ratio": 0.0~1.0, "description_ratio": 0.0~1.0, "pace": "fast|moderate|slow"}}
  ],
  "curve_analysis": "전체 긴장도 곡선 분석",
  "issues": [{{"chapter": 번호, "problem": "느슨함|급함|단조로움", "suggestion": "개선안"}}],
  "ideal_comparison": "이상적 곡선과 비교"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(STRUCTURE_MODEL, prompt,
        "소설 구조 분석가입니다. 페이싱을 정량적으로 분석하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                            "chapters_analyzed": len(chapters)}
    return jsonify(analysis)


@novel_bp.route("/api/novel/analyze/character", methods=["POST"])
def novel_analyze_character():
    """캐릭터 분석 — 등장 빈도, 감정 변화, 아크
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            character_name: {type: string, description: "특정 인물 (선택)"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id)
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    char_name = data.get("character_name", "")
    char_filter = f"특히 '{char_name}' 인물에 집중하세요." if char_name else "모든 주요 인물을 분석하세요."

    samples = "\n".join(f"[ch{c['chapter_num']}] {c['content'][:500]}..." for c in chapters)

    prompt = f"""다음 소설의 캐릭터를 분석하세요. {char_filter}

{samples}

JSON으로 출력:
{{
  "characters": [
    {{
      "name": "이름",
      "role": "protagonist|antagonist|supporting",
      "appearances": [{{"ch": 번호, "scenes": 장면수}}],
      "dialogue_ratio": 0.0~1.0,
      "emotion_arc": ["감정 변화 리스트"],
      "character_arc": "캐릭터 아크 요약",
      "consistency_score": 0~100,
      "issues": ["일관성 문제"]
    }}
  ]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "캐릭터 분석 전문가입니다. 인물의 등장/감정/아크를 정량 분석하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(analysis)


@novel_bp.route("/api/novel/analyze/readability", methods=["POST"])
def novel_analyze_readability():
    """가독성 분석 — 문장 길이, 어휘 다양성, 난이도
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapters: {type: array, items: {type: integer}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id, data.get("chapters"))
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    # 간단한 정량 분석 (LLM 없이)
    stats = []
    for c in chapters:
        content = c["content"]
        sentences = [s.strip() for s in content.replace("!", ".").replace("?", ".").split(".") if s.strip()]
        avg_len = sum(len(s) for s in sentences) / max(len(sentences), 1)
        words = set(content)
        stats.append({
            "chapter": c["chapter_num"],
            "word_count": c["word_count"],
            "sentence_count": len(sentences),
            "avg_sentence_length": round(avg_len, 1),
            "unique_chars": len(words),
        })

    # LLM으로 정성 분석
    samples = "\n".join(f"[ch{c['chapter_num']}] {c['content'][:300]}..." for c in chapters[:5])

    prompt = f"""다음 소설의 가독성을 분석하세요.

{samples}

JSON으로 출력:
{{
  "overall_readability": "easy|moderate|difficult",
  "reading_level": "초등|중등|일반|문학",
  "vocabulary_diversity": 0.0~1.0,
  "issues": [{{"type": "문제유형", "detail": "설명", "chapter": 번호}}],
  "strengths": ["잘된 점"],
  "target_audience_fit": "독자 타겟 적합성"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(FAST_MODEL, prompt,
        "가독성 분석가입니다. 한국어 텍스트의 가독성을 평가하세요.",
        {"num_predict": 1500, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    if isinstance(analysis, dict):
        analysis["quantitative_stats"] = stats
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(analysis)


@novel_bp.route("/api/novel/analyze/dialogue", methods=["POST"])
def novel_analyze_dialogue():
    """대화 분석 — 인물별 말투 일관성, 대화/지문 비율
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id)
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    samples = "\n".join(f"[ch{c['chapter_num']}] {c['content'][:500]}..." for c in chapters)

    prompt = f"""다음 소설의 대화를 분석하세요.

{samples}

JSON으로 출력:
{{
  "overall_dialogue_ratio": 0.0~1.0,
  "characters": [
    {{
      "name": "인물명",
      "speech_style": {{"formality": "high|mid|low", "length": "short|medium|long",
                        "emotion": "restrained|moderate|expressive"}},
      "consistency": 0~100,
      "sample_lines": ["대표 대사"],
      "issues": ["말투 일관성 문제"]
    }}
  ],
  "dialogue_quality": {{
    "subtext_usage": 0~100,
    "tag_variety": 0~100,
    "natural_flow": 0~100
  }},
  "suggestions": ["개선 제안"]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "대화 분석 전문가입니다. 인물별 대화 스타일과 품질을 분석하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(analysis)


@novel_bp.route("/api/novel/analyze/worldbuilding", methods=["POST"])
def novel_analyze_worldbuilding():
    """세계관 일관성 분석 — 설정 모순 탐지
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id)
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    samples = "\n".join(f"[ch{c['chapter_num']}] {c['content'][:500]}..." for c in chapters)

    prompt = f"""다음 소설의 세계관 일관성을 검사하세요.

{samples}

JSON으로 출력:
{{
  "consistency_score": 0~100,
  "issues": [
    {{"type": "distance|time|technology|social|logic",
      "detail": "ch3에서 1시간 거리가 ch7에서 3시간",
      "chapters": [3, 7],
      "severity": "critical|major|minor"}}
  ],
  "world_elements": {{
    "locations": ["발견된 장소"],
    "time_references": ["시간 참조"],
    "rules": ["세계관 규칙"]
  }},
  "suggestions": ["개선 제안"]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(STRUCTURE_MODEL, prompt,
        "세계관 분석가입니다. 설정의 논리적 일관성을 검증하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(analysis)


@novel_bp.route("/api/novel/analyze/emotion_map", methods=["POST"])
def novel_analyze_emotion_map():
    """감정 지도 — 챕터/장면별 감정 흐름 시각화 데이터
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            granularity: {type: string, enum: [chapter, scene], default: chapter}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id)
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    granularity = data.get("granularity", "chapter")
    samples = "\n".join(f"[ch{c['chapter_num']}] {c['content'][:400]}..." for c in chapters)

    prompt = f"""다음 소설의 감정 흐름을 {granularity} 단위로 분석하세요.

{samples}

JSON으로 출력:
{{
  "chapters": [
    {{
      "ch": 번호,
      "dominant_emotion": "주요 감정",
      "intensity": 0.0~1.0,
      "emotions": {{"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                    "surprise": 0.0, "trust": 0.0, "anticipation": 0.0}},
      "transitions": [{{"from": "감정A", "to": "감정B", "trigger": "계기"}}]
    }}
  ],
  "overall_arc": "전체 감정 아크 설명",
  "emotional_peaks": [{{"ch": 번호, "emotion": "감정", "intensity": 값}}],
  "flat_zones": [{{"chapters": [번호], "issue": "감정 변화 부족"}}]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "감정 분석 전문가입니다. 소설의 감정 흐름을 정량 분석하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed, "granularity": granularity}
    return jsonify(analysis)


# ---------------------------------------------------------------------------
# 분석: 종합 점수
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/score", methods=["POST"])
def novel_score():
    """종합 점수 — 6개 분석 통합
    ---
    tags: [Novel Writer - Analysis]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    chapters = _load_chapters_for_analysis(project_id)
    if not chapters:
        return jsonify({"error": "no chapters found"}), 404

    total_words = sum(c["word_count"] for c in chapters)
    samples = "\n".join(f"[ch{c['chapter_num']}] {c['content'][:300]}..." for c in chapters)

    # E3: 웹소설 특화 메트릭 (선택적)
    include_web_novel = data.get("include_web_novel_metrics", False)
    web_novel_section = ""
    if include_web_novel:
        web_novel_section = """
  "web_novel": {{
    "hook_quality": {{"score": 0~100, "comment": "오프닝 3줄의 흡인력"}},
    "cliffhanger_score": {{"score": 0~100, "comment": "마지막 장면의 다음회 기대감"}},
    "pacing_rhythm": {{"score": 0~100, "comment": "회차 내 긴장도 변화 다양성"}},
    "growth_balance": {{"score": 0~100, "comment": "성장 이벤트의 적절한 배치"}}
  }},"""

    prompt = f"""다음 소설을 6개 영역으로 종합 평가하세요.

{samples}

[통계] {len(chapters)}챕터, 총 {total_words}자

JSON으로 출력:
{{
  "total": 0~100,
  "scores": {{
    "pacing": {{"score": 0~100, "comment": "설명"}},
    "character": {{"score": 0~100, "comment": "설명"}},
    "readability": {{"score": 0~100, "comment": "설명"}},
    "dialogue": {{"score": 0~100, "comment": "설명"}},
    "worldbuilding": {{"score": 0~100, "comment": "설명"}},
    "emotion": {{"score": 0~100, "comment": "설명"}}
  }},{web_novel_section}
  "top_strengths": ["가장 잘된 점 3가지"],
  "top_improvements": ["가장 개선할 점 3가지"],
  "overall_comment": "종합 평가 (200자)",
  "next_action": "다음 추천 행동"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "문학 평론가입니다. 소설을 6개 영역으로 객관적이고 건설적으로 평가하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.4})
    result = force_korean(result).strip()
    score_data = _parse_json_response(result) or {"raw_output": result}
    score_data["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                              "chapters": len(chapters), "total_words": total_words}
    return jsonify(score_data)


# ===========================================================================
# Sprint 4 — 시리즈/연재 관리 (6 API)
# ===========================================================================

@novel_bp.route("/api/novel/series/create", methods=["POST"])
def novel_series_create():
    """시리즈 생성
    ---
    tags: [Novel Writer - Series]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title]
          properties:
            title: {type: string}
            description: {type: string}
            genre: {type: string}
            projects: {type: array, items: {type: string}, description: "초기 프로젝트 ID 목록"}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400

    series_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    projects = data.get("projects", [])

    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_series (id, title, description, genre, total_volumes, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, 'ongoing', ?)",
            (series_id, title, data.get("description"),
             data.get("genre"), len(projects), now))

        for i, pid in enumerate(projects):
            conn.execute(
                "INSERT OR IGNORE INTO novel_series_volumes (series_id, project_id, volume_num) "
                "VALUES (?, ?, ?)", (series_id, pid, i + 1))

        conn.commit()
    finally:
        conn.close()

    return jsonify({"series_id": series_id, "title": title,
                    "volumes": len(projects), "status": "ongoing"})


@novel_bp.route("/api/novel/series", methods=["GET"])
def novel_series_list():
    """시리즈 목록
    ---
    tags: [Novel Writer - Series]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    _cached = _novel_cache.get("series_list")
    if _cached is not None:
        return jsonify(_cached)

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM novel_series ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()

    resp = {"series": [dict(r) for r in rows], "count": len(rows)}
    _novel_cache.set("series_list", resp)
    return jsonify(resp)


@novel_bp.route("/api/novel/series/<series_id>", methods=["GET"])
def novel_series_get(series_id):
    """시리즈 상세 (전체 진행률 포함)
    ---
    tags: [Novel Writer - Series]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: series_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        series = conn.execute(
            "SELECT * FROM novel_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return jsonify({"error": "series not found"}), 404

        volumes = conn.execute(
            "SELECT sv.volume_num, sv.project_id, p.title, p.genre, p.status, "
            "p.total_words, p.completed_chapters, p.total_chapters "
            "FROM novel_series_volumes sv "
            "LEFT JOIN novel_projects p ON sv.project_id = p.id "
            "WHERE sv.series_id=? ORDER BY sv.volume_num",
            (series_id,)).fetchall()
    finally:
        conn.close()

    vol_list = [dict(v) for v in volumes]
    total_words = sum(v.get("total_words", 0) or 0 for v in vol_list)
    completed = sum(1 for v in vol_list if v.get("status") == "completed")

    result = dict(series)
    result["volumes"] = vol_list
    result["progress"] = {
        "total_volumes": len(vol_list),
        "completed_volumes": completed,
        "total_words": total_words,
    }
    return jsonify(result)


@novel_bp.route("/api/novel/series/<series_id>/add", methods=["PUT"])
def novel_series_add_volume(series_id):
    """시리즈에 프로젝트 추가
    ---
    tags: [Novel Writer - Series]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: series_id, type: string, required: true}
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            volume_num: {type: integer}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        series = conn.execute(
            "SELECT * FROM novel_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return jsonify({"error": "series not found"}), 404

        # 볼륨 번호 자동 할당
        volume_num = data.get("volume_num")
        if not volume_num:
            max_vol = conn.execute(
                "SELECT MAX(volume_num) as m FROM novel_series_volumes WHERE series_id=?",
                (series_id,)).fetchone()
            volume_num = (max_vol["m"] or 0) + 1

        conn.execute(
            "INSERT OR REPLACE INTO novel_series_volumes (series_id, project_id, volume_num) "
            "VALUES (?, ?, ?)", (series_id, project_id, volume_num))

        total = conn.execute(
            "SELECT COUNT(*) as c FROM novel_series_volumes WHERE series_id=?",
            (series_id,)).fetchone()["c"]
        conn.execute(
            "UPDATE novel_series SET total_volumes=?, updated_at=? WHERE id=?",
            (total, datetime.now(timezone.utc).isoformat(), series_id))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"series_id": series_id, "project_id": project_id,
                    "volume_num": volume_num, "total_volumes": total})


@novel_bp.route("/api/novel/series/recap", methods=["POST"])
def novel_series_recap():
    """시리즈 "지금까지의 이야기" 생성
    ---
    tags: [Novel Writer - Series]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [series_id]
          properties:
            series_id: {type: string}
            up_to_volume: {type: integer, description: "이 권까지 요약 (선택, 기본=전체)"}
            style: {type: string, enum: [narrator, character, chronicle], default: narrator}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    series_id = data.get("series_id", "").strip()
    if not series_id:
        return jsonify({"error": "series_id required"}), 400

    conn = _get_db()
    try:
        series = conn.execute(
            "SELECT * FROM novel_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return jsonify({"error": "series not found"}), 404

        up_to = data.get("up_to_volume")
        query = (
            "SELECT sv.volume_num, p.title, p.genre, p.synopsis_json, p.logline "
            "FROM novel_series_volumes sv "
            "JOIN novel_projects p ON sv.project_id = p.id "
            "WHERE sv.series_id=?"
        )
        params = [series_id]
        if up_to:
            query += " AND sv.volume_num <= ?"
            params.append(up_to)
        query += " ORDER BY sv.volume_num"

        volumes = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    if not volumes:
        return jsonify({"error": "no volumes found in series"}), 404

    style = data.get("style", "narrator")
    style_map = {
        "narrator": "3인칭 나레이터 시점으로 서술",
        "character": "주인공 시점의 회상 형식으로 서술",
        "chronicle": "연대기 형식으로 사건 중심 나열",
    }

    vol_summaries = []
    for v in volumes:
        v = dict(v)
        synopsis = ""
        if v.get("synopsis_json"):
            try:
                s = json.loads(v["synopsis_json"])
                synopsis = s.get("premise", "") + " " + s.get("act2_conflict", "")
            except (json.JSONDecodeError, TypeError):
                pass
        vol_summaries.append(
            f"[{v['volume_num']}권: {v.get('title', '')}] {v.get('logline', '')} — {synopsis}"
        )

    prompt = f"""시리즈 "{series['title']}"의 "지금까지의 이야기"를 작성하세요.
스타일: {style_map.get(style, style_map['narrator'])}

{chr(10).join(vol_summaries)}

독자가 새 권을 읽기 전에 이전 내용을 빠르게 파악할 수 있도록
500~1000자로 작성하세요. 스포일러는 최소화."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "소설 편집자입니다. 시리즈 리캡을 매력적으로 작성하세요. 한국어.",
        {"num_predict": 2000, "temperature": 0.6})
    result = force_korean(result).strip()

    return jsonify({
        "series_id": series_id, "series_title": series["title"],
        "recap": result, "volumes_covered": len(volumes), "style": style,
        "metadata": {"tokens": tokens, "duration_ms": elapsed},
    })


@novel_bp.route("/api/novel/series/continuity", methods=["POST"])
def novel_series_continuity():
    """시리즈 연속성 검증 — 권 간 설정/인물/복선 일관성
    ---
    tags: [Novel Writer - Series]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [series_id]
          properties:
            series_id: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    series_id = data.get("series_id", "").strip()
    if not series_id:
        return jsonify({"error": "series_id required"}), 400

    conn = _get_db()
    try:
        series = conn.execute(
            "SELECT * FROM novel_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return jsonify({"error": "series not found"}), 404

        volumes = conn.execute(
            "SELECT sv.volume_num, p.id as project_id, p.title, p.synopsis_json, "
            "p.plot_json, p.genre "
            "FROM novel_series_volumes sv "
            "JOIN novel_projects p ON sv.project_id = p.id "
            "WHERE sv.series_id=? ORDER BY sv.volume_num",
            (series_id,)).fetchall()
    finally:
        conn.close()

    if len(volumes) < 2:
        return jsonify({"error": "need at least 2 volumes for continuity check"}), 400

    vol_info = []
    for v in volumes:
        v = dict(v)
        synopsis = ""
        if v.get("synopsis_json"):
            try:
                s = json.loads(v["synopsis_json"])
                synopsis = json.dumps(s, ensure_ascii=False)[:500]
            except (json.JSONDecodeError, TypeError):
                pass
        plot = ""
        if v.get("plot_json"):
            try:
                p = json.loads(v["plot_json"])
                plot = json.dumps(p, ensure_ascii=False)[:500]
            except (json.JSONDecodeError, TypeError):
                pass
        vol_info.append(f"[{v['volume_num']}권: {v.get('title', '')}]\n시놉시스: {synopsis}\n플롯: {plot}")

    prompt = f"""시리즈 "{series['title']}"의 권 간 연속성을 검증하세요.

{chr(10).join(vol_info)}

JSON으로 출력:
{{
  "continuity_score": 0~100,
  "issues": [
    {{
      "type": "character|worldbuilding|timeline|foreshadowing|tone",
      "volumes": [1, 2],
      "detail": "1권에서 A가 죽었는데 2권에서 등장",
      "severity": "critical|major|minor",
      "suggestion": "해결 방안"
    }}
  ],
  "cross_volume_foreshadowing": [
    {{"planted_volume": 1, "expected_resolve": 3, "hint": "복선 내용", "status": "resolved|pending|forgotten"}}
  ],
  "character_arcs": [
    {{"name": "인물명", "arc_across_volumes": "1권: 상태 → 2권: 변화"}}
  ],
  "strengths": ["잘 연결된 점"]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "시리즈 편집자입니다. 권 간 연속성과 일관성을 엄밀히 검증하세요. 한국어.",
        {"num_predict": 3000, "temperature": 0.3})
    result = force_korean(result).strip()
    analysis = _parse_json_response(result) or {"raw_output": result}
    analysis["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                            "volumes_checked": len(volumes)}
    return jsonify(analysis)


# ===========================================================================
# Sprint 5 — 소설 분해기 (Novel Decomposer) 18 API
# ===========================================================================

# ---------------------------------------------------------------------------
# 소설 입력/등록 (3 API) + 삭제 (1 API)
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/decompose/upload", methods=["POST"])
def novel_decompose_upload():
    """소설 텍스트 등록
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title, genre, text]
          properties:
            title: {type: string}
            author: {type: string}
            genre: {type: string}
            sub_genres: {type: array, items: {type: string}}
            text: {type: string, description: "전체 소설 텍스트"}
            chapters: {type: array, description: "미리 분할된 챕터 [{title, text}]"}
            language: {type: string, default: ko}
            metadata: {type: object}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    genre = data.get("genre", "").strip()
    text = data.get("text", "").strip()
    if not title or not genre or not text:
        return jsonify({"error": "title, genre, and text required"}), 400

    source_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    chapters = data.get("chapters")
    chapters_json = json.dumps(chapters, ensure_ascii=False) if chapters else None

    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_sources (id, title, author, genre, sub_genres, language, "
            "text_content, chapters_json, word_count, metadata_json, decompose_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?)",
            (source_id, title, data.get("author"),
             genre, json.dumps(data.get("sub_genres", []), ensure_ascii=False),
             data.get("language", "ko"), text, chapters_json, len(text),
             json.dumps(data.get("metadata", {}), ensure_ascii=False), now))
        conn.commit()
    finally:
        conn.close()

    chapter_count = len(chapters) if chapters else 0
    return jsonify({
        "source_id": source_id, "title": title, "genre": genre,
        "word_count": len(text), "chapter_count": chapter_count,
        "status": "uploaded",
        "message": f"분해 시작: POST /api/novel/decompose/run/{source_id}",
    })


@novel_bp.route("/api/novel/decompose/sources", methods=["GET"])
def novel_decompose_sources():
    """등록된 소설 목록
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, author, genre, word_count, decompose_status, created_at "
            "FROM novel_sources ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return jsonify({"sources": [dict(r) for r in rows], "count": len(rows)})


@novel_bp.route("/api/novel/decompose/source/<source_id>", methods=["GET"])
def novel_decompose_source_get(source_id):
    """등록된 소설 상세
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: source_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM novel_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return jsonify({"error": "source not found"}), 404
        comp_count = conn.execute(
            "SELECT COUNT(*) as c FROM novel_components WHERE source_id=?",
            (source_id,)).fetchone()["c"]
    finally:
        conn.close()

    result = dict(row)
    # 텍스트 본문은 미리보기만
    if result.get("text_content"):
        result["text_preview"] = result["text_content"][:500] + "..."
        del result["text_content"]
    if result.get("chapters_json"):
        try:
            result["chapters"] = json.loads(result["chapters_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        del result["chapters_json"]
    if result.get("decompose_result_json"):
        try:
            result["decompose_result"] = json.loads(result["decompose_result_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        del result["decompose_result_json"]
    result["components_count"] = comp_count
    return jsonify(result)


@novel_bp.route("/api/novel/decompose/source/<source_id>", methods=["DELETE"])
def novel_decompose_source_delete(source_id):
    """원본 소설 삭제 (+ 연관 컴포넌트 cascade)
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: source_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM novel_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return jsonify({"error": "source not found"}), 404
        deleted_comps = conn.execute(
            "SELECT COUNT(*) as c FROM novel_components WHERE source_id=?",
            (source_id,)).fetchone()["c"]
        conn.execute("DELETE FROM novel_components WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM novel_sources WHERE id=?", (source_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted_components": deleted_comps})


# ---------------------------------------------------------------------------
# 분해 실행 (3 API)
# ---------------------------------------------------------------------------

_DECOMPOSE_EXTRACT_TYPES = [
    "characters", "worldbuilding", "plot_points", "foreshadowing",
    "key_scenes", "dialogue_patterns", "writing_style", "themes"
]


def _decompose_chunk(text, genre, extract_types, conn, source_id):
    """단일 청크를 분해하여 컴포넌트로 저장. 반환: 저장된 컴포넌트 수."""
    genre_cfg = GENRE_PROMPTS.get(genre, {})
    types_str = ", ".join(extract_types)

    # Stage 1: 구조 + 요소 추출 (프롬프트 간소화 — JSON placeholder 정리)
    prompt = f"""다음 소설 텍스트에서 구성 요소를 추출하세요.
[장르] {genre} — {genre_cfg.get('world_emphasis', '')}

[텍스트]
{text[:5500]}

위 텍스트를 분석하여 아래 JSON 형식으로 출력하세요. 해당 없는 항목은 빈 배열로.

```json
{{
  "characters": [
    {{"name": "인물명", "role": "protagonist", "personality": ["특성1", "특성2"],
      "motivation": "동기", "arc": "변화", "speech_signature": "말투"}}
  ],
  "worldbuilding": {{
    "setting": "배경 설명", "atmosphere": "분위기", "details": ["세부1", "세부2"]
  }},
  "plot_points": [
    {{"event": "사건 설명", "function": "inciting", "tension_level": 0.5}}
  ],
  "foreshadowing": [
    {{"plant": "복선 심기", "payoff": "회수", "technique": "기법명"}}
  ],
  "key_scenes": [
    {{"scene_type": "revelation", "summary": "장면 요약", "emotion_impact": 0.8, "technique": "기법"}}
  ],
  "dialogue_patterns": [
    {{"character": "인물명", "speech_style": {{"formality": "high", "length": "short"}},
      "sample_lines": ["대사 예시"]}}
  ],
  "writing_style": {{
    "avg_sentence_length": 18, "rhythm": "리듬 설명",
    "signature_technique": "특징적 기법", "emotion_method": "감정 표현 방식"
  }},
  "themes": [
    {{"theme": "테마명", "motifs": ["모티프1"], "expression_method": "표현방식"}}
  ]
}}
```"""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        f"문학 분석가입니다. {genre} 장르 소설의 구성 요소를 JSON으로 추출하세요. 반드시 유효한 JSON을 출력하세요.",
        {"num_predict": 5000, "temperature": 0.3}, timeout=240)
    # 분해기는 JSON 구조 보존이 중요 → force_korean 미적용
    result = result.strip()
    extracted = _parse_json_response(result)
    if not extracted:
        logger.warning(f"Decompose JSON parse failed. Raw response ({len(result)} chars): {result[:500]}")
        return 0
    logger.info(f"Decompose extracted keys: {list(extracted.keys()) if isinstance(extracted, dict) else type(extracted)}")

    # 컴포넌트별 저장
    saved = 0
    now = datetime.now(timezone.utc).isoformat()

    def _save_comp(comp_type, sub_type, name, tags, data_dict, quality=0.7):
        nonlocal saved
        comp_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO novel_components (id, source_id, type, genre, sub_type, name, "
            "tags, data_json, quality_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (comp_id, source_id, comp_type, genre, sub_type, name,
             json.dumps(tags, ensure_ascii=False),
             json.dumps(data_dict, ensure_ascii=False), quality, now))
        saved += 1

    # characters
    for ch in (extracted.get("characters") or []):
        if isinstance(ch, dict) and ch.get("name"):
            _save_comp("character", ch.get("role", "supporting"), ch["name"],
                       ch.get("personality", []), ch, 0.8)

    # worldbuilding
    wb = extracted.get("worldbuilding")
    if isinstance(wb, dict) and wb.get("setting"):
        _save_comp("worldbuilding", None, wb.get("setting", "")[:50],
                   wb.get("details", []), wb, 0.7)

    # plot_points
    for pp in (extracted.get("plot_points") or []):
        if isinstance(pp, dict) and pp.get("event"):
            _save_comp("plot_point", pp.get("function"), pp["event"][:50],
                       [pp.get("function", "")], pp, pp.get("tension_level", 0.5))

    # foreshadowing
    for fs in (extracted.get("foreshadowing") or []):
        if isinstance(fs, dict) and fs.get("plant"):
            _save_comp("foreshadowing", fs.get("technique"), fs["plant"][:50],
                       [fs.get("technique", "")], fs, 0.7)

    # key_scenes
    for ks in (extracted.get("key_scenes") or []):
        if isinstance(ks, dict) and ks.get("summary"):
            _save_comp("key_scene", ks.get("scene_type"), ks["summary"][:50],
                       [ks.get("scene_type", ""), ks.get("technique", "")], ks,
                       ks.get("emotion_impact", 0.5))

    # dialogue_patterns
    for dp in (extracted.get("dialogue_patterns") or []):
        if isinstance(dp, dict) and dp.get("character"):
            _save_comp("dialogue_pattern", None, dp["character"],
                       [dp.get("subtext_pattern", "")], dp, 0.7)

    # writing_style
    ws = extracted.get("writing_style")
    if isinstance(ws, dict) and ws.get("signature_technique"):
        _save_comp("writing_style", None, ws.get("signature_technique", "")[:50],
                   [ws.get("vocabulary_level", "")], ws, 0.8)

    # themes
    for th in (extracted.get("themes") or []):
        if isinstance(th, dict) and th.get("theme"):
            _save_comp("theme", None, th["theme"],
                       th.get("motifs", []), th, 0.7)

    conn.commit()
    return saved


@novel_bp.route("/api/novel/decompose/run/<source_id>", methods=["POST"])
def novel_decompose_run(source_id):
    """전체 분해 실행
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: source_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            depth: {type: string, enum: [quick, standard, full], default: full}
            extract: {type: array, items: {type: string}}
            chunk_strategy: {type: string, enum: [by_chapter, by_size, auto], default: auto}
            max_chunk_size: {type: integer, default: 6000}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}

    conn = _get_db()
    try:
        source = conn.execute("SELECT * FROM novel_sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            return jsonify({"error": "source not found"}), 404
        source = dict(source)

        # 상태 업데이트
        conn.execute("UPDATE novel_sources SET decompose_status='running', updated_at=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), source_id))
        conn.commit()

        # 기존 컴포넌트 삭제 (재실행 시)
        conn.execute("DELETE FROM novel_components WHERE source_id=?", (source_id,))
        conn.commit()

        genre = source["genre"]
        text = source.get("text_content", "")
        extract_types = data.get("extract") or _DECOMPOSE_EXTRACT_TYPES
        max_chunk = data.get("max_chunk_size", 6000)
        strategy = data.get("chunk_strategy", "auto")

        # 청크 분할
        chunks = []
        if source.get("chapters_json"):
            try:
                ch_list = json.loads(source["chapters_json"])
                if isinstance(ch_list, list) and ch_list:
                    chunks = [c.get("text", "") for c in ch_list if c.get("text")]
            except (json.JSONDecodeError, TypeError):
                pass

        if not chunks:
            # 자동 분할
            if strategy == "by_size" or (strategy == "auto" and len(text) > max_chunk):
                chunks = [text[i:i+max_chunk] for i in range(0, len(text), max_chunk)]
            else:
                chunks = [text]

        # 청크별 분해
        total_saved = 0
        for chunk in chunks:
            if chunk.strip():
                saved = _decompose_chunk(chunk.strip(), genre, extract_types, conn, source_id)
                total_saved += saved

        # 결과 통계
        type_counts = {}
        rows = conn.execute(
            "SELECT type, COUNT(*) as c FROM novel_components WHERE source_id=? GROUP BY type",
            (source_id,)).fetchall()
        for r in rows:
            type_counts[r["type"]] = r["c"]

        result_json = {
            "chunks_processed": len(chunks),
            "components_saved": total_saved,
            "by_type": type_counts,
        }

        conn.execute(
            "UPDATE novel_sources SET decompose_status='completed', "
            "decompose_result_json=?, updated_at=? WHERE id=?",
            (json.dumps(result_json, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), source_id))
        conn.commit()

    except Exception as e:
        conn.execute(
            "UPDATE novel_sources SET decompose_status='failed', updated_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), source_id))
        conn.commit()
        logger.error(f"Decompose error: {e}")
        return jsonify({"error": "internal server error", "source_id": source_id}), 500
    finally:
        conn.close()

    return jsonify({
        "source_id": source_id, "status": "completed",
        "decomposition": result_json,
    })


@novel_bp.route("/api/novel/decompose/run/<source_id>/stream", methods=["POST"])
def novel_decompose_run_stream(source_id):
    """분해 SSE 스트리밍 (진행률)
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: source_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            extract: {type: array, items: {type: string}}
            max_chunk_size: {type: integer, default: 6000}
    responses:
      200:
        description: SSE stream
    """
    data = request.json or {}

    conn = _get_db()
    try:
        source = conn.execute("SELECT * FROM novel_sources WHERE id=?", (source_id,)).fetchone()
    finally:
        conn.close()

    if not source:
        return jsonify({"error": "source not found"}), 404
    source = dict(source)

    genre = source["genre"]
    text = source.get("text_content", "")
    extract_types = data.get("extract") or _DECOMPOSE_EXTRACT_TYPES
    max_chunk = data.get("max_chunk_size", 6000)

    # 청크 분할
    chunks = []
    if source.get("chapters_json"):
        try:
            ch_list = json.loads(source["chapters_json"])
            if isinstance(ch_list, list):
                chunks = [c.get("text", "") for c in ch_list if c.get("text")]
        except (json.JSONDecodeError, TypeError):
            pass
    if not chunks:
        if len(text) > max_chunk:
            chunks = [text[i:i+max_chunk] for i in range(0, len(text), max_chunk)]
        else:
            chunks = [text]

    def sse_gen():
        conn2 = _get_db()
        try:
            conn2.execute("UPDATE novel_sources SET decompose_status='running', updated_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), source_id))
            conn2.execute("DELETE FROM novel_components WHERE source_id=?", (source_id,))
            conn2.commit()

            total_saved = 0
            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                yield f"data: {json.dumps({'stage': 'chunk', 'current': i+1, 'total': len(chunks)}, ensure_ascii=False)}\n\n"
                saved = _decompose_chunk(chunk.strip(), genre, extract_types, conn2, source_id)
                total_saved += saved
                yield f"data: {json.dumps({'stage': 'chunk_done', 'chunk': i+1, 'saved': saved, 'total_saved': total_saved})}\n\n"

            # 결과 통계
            rows = conn2.execute(
                "SELECT type, COUNT(*) as c FROM novel_components WHERE source_id=? GROUP BY type",
                (source_id,)).fetchall()
            type_counts = {r["type"]: r["c"] for r in rows}
            result_json = {"chunks_processed": len(chunks), "components_saved": total_saved, "by_type": type_counts}

            conn2.execute(
                "UPDATE novel_sources SET decompose_status='completed', decompose_result_json=?, updated_at=? WHERE id=?",
                (json.dumps(result_json, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), source_id))
            conn2.commit()

            yield f"data: {json.dumps({'done': True, 'decomposition': result_json}, ensure_ascii=False)}\n\n"
        except Exception as e:
            conn2.execute("UPDATE novel_sources SET decompose_status='failed', updated_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), source_id))
            conn2.commit()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            conn2.close()

    return Response(sse_gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@novel_bp.route("/api/novel/decompose/run/<source_id>/status", methods=["GET"])
def novel_decompose_run_status(source_id):
    """분해 진행 상태
    ---
    tags: [Novel Decomposer]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: source_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT decompose_status, decompose_result_json, updated_at "
            "FROM novel_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return jsonify({"error": "source not found"}), 404
        comp_count = conn.execute(
            "SELECT COUNT(*) as c FROM novel_components WHERE source_id=?",
            (source_id,)).fetchone()["c"]
    finally:
        conn.close()

    result = {"source_id": source_id, "status": row["decompose_status"],
              "components_extracted": comp_count, "updated_at": row["updated_at"]}
    if row["decompose_result_json"]:
        try:
            result["result"] = json.loads(row["decompose_result_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify(result)


# ---------------------------------------------------------------------------
# 컴포넌트 조회/검색 (5 API) + 삭제 (1 API)
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/components", methods=["GET"])
def novel_components_list():
    """컴포넌트 목록 (필터)
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - {in: query, name: type, type: string}
      - {in: query, name: genre, type: string}
      - {in: query, name: tag, type: string}
      - {in: query, name: limit, type: integer, default: 50}
    responses:
      200:
        schema:
          type: object
    """
    comp_type = request.args.get("type")
    genre = request.args.get("genre")
    tag = request.args.get("tag")
    limit = min(int(request.args.get("limit", 50)), 200)

    query = "SELECT c.*, s.title as source_title FROM novel_components c " \
            "LEFT JOIN novel_sources s ON c.source_id = s.id WHERE 1=1"
    params = []
    if comp_type:
        query += " AND c.type=?"
        params.append(comp_type)
    if genre:
        query += " AND c.genre=?"
        params.append(genre)
    if tag:
        query += " AND c.tags LIKE ?"
        params.append(f"%{tag}%")
    query += " ORDER BY c.quality_score DESC, c.usage_count DESC LIMIT ?"
    params.append(limit)

    conn = _get_db()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    components = []
    for r in rows:
        d = dict(r)
        if d.get("data_json"):
            try:
                d["data"] = json.loads(d["data_json"])
            except (json.JSONDecodeError, TypeError):
                pass
            del d["data_json"]
        if d.get("tags"):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                pass
        components.append(d)

    return jsonify({"components": components, "count": len(components)})


@novel_bp.route("/api/novel/components/<component_id>", methods=["GET"])
def novel_component_get(component_id):
    """컴포넌트 상세
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: component_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT c.*, s.title as source_title FROM novel_components c "
            "LEFT JOIN novel_sources s ON c.source_id = s.id WHERE c.id=?",
            (component_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "component not found"}), 404

    d = dict(row)
    for json_field in ("data_json", "tags"):
        if d.get(json_field):
            try:
                key = "data" if json_field == "data_json" else json_field
                d[key] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
            if json_field == "data_json":
                del d["data_json"]
    return jsonify(d)


@novel_bp.route("/api/novel/components/source/<source_id>", methods=["GET"])
def novel_components_by_source(source_id):
    """소설별 추출 컴포넌트
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: source_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM novel_components WHERE source_id=? ORDER BY type, quality_score DESC",
            (source_id,)).fetchall()
    finally:
        conn.close()

    components = []
    for r in rows:
        d = dict(r)
        if d.get("data_json"):
            try:
                d["data"] = json.loads(d["data_json"])
            except (json.JSONDecodeError, TypeError):
                pass
            del d["data_json"]
        if d.get("tags"):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                pass
        components.append(d)

    by_type = {}
    for c in components:
        by_type.setdefault(c["type"], []).append(c)

    return jsonify({"source_id": source_id, "components": components,
                    "count": len(components), "by_type": {k: len(v) for k, v in by_type.items()}})


@novel_bp.route("/api/novel/components/genre/<genre>", methods=["GET"])
def novel_components_by_genre(genre):
    """장르별 컴포넌트
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: genre, type: string, required: true}
      - {in: query, name: type, type: string}
      - {in: query, name: limit, type: integer, default: 50}
    responses:
      200:
        schema:
          type: object
    """
    comp_type = request.args.get("type")
    limit = min(int(request.args.get("limit", 50)), 200)

    query = "SELECT c.*, s.title as source_title FROM novel_components c " \
            "LEFT JOIN novel_sources s ON c.source_id = s.id WHERE c.genre=?"
    params = [genre]
    if comp_type:
        query += " AND c.type=?"
        params.append(comp_type)
    query += " ORDER BY c.quality_score DESC LIMIT ?"
    params.append(limit)

    conn = _get_db()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    components = []
    for r in rows:
        d = dict(r)
        if d.get("data_json"):
            try:
                d["data"] = json.loads(d["data_json"])
            except (json.JSONDecodeError, TypeError):
                pass
            del d["data_json"]
        components.append(d)

    return jsonify({"genre": genre, "components": components, "count": len(components)})


@novel_bp.route("/api/novel/components/search", methods=["POST"])
def novel_components_search():
    """컴포넌트 시맨틱 검색 (SQLite LIKE 기반, 200개 초과 시 확장 가능)
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string}
            type: {type: string}
            genre: {type: array, items: {type: string}}
            limit: {type: integer, default: 10}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    query_text = data.get("query", "").strip()
    if not query_text:
        return jsonify({"error": "query required"}), 400

    comp_type = data.get("type")
    genres = data.get("genre", [])
    limit = min(data.get("limit", 10), 50)

    # 키워드 분할 후 LIKE 검색
    keywords = query_text.split()
    sql = "SELECT c.*, s.title as source_title FROM novel_components c " \
          "LEFT JOIN novel_sources s ON c.source_id = s.id WHERE ("
    params = []
    keyword_clauses = []
    for kw in keywords[:5]:
        keyword_clauses.append("(c.name LIKE ? OR c.tags LIKE ? OR c.data_json LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
    sql += " OR ".join(keyword_clauses) + ")"

    if comp_type:
        sql += " AND c.type=?"
        params.append(comp_type)
    if genres:
        placeholders = ",".join("?" * len(genres))
        sql += f" AND c.genre IN ({placeholders})"
        params.extend(genres)
    sql += " ORDER BY c.quality_score DESC LIMIT ?"
    params.append(limit)

    conn = _get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    components = []
    for r in rows:
        d = dict(r)
        if d.get("data_json"):
            try:
                d["data"] = json.loads(d["data_json"])
            except (json.JSONDecodeError, TypeError):
                pass
            del d["data_json"]
        components.append(d)

    return jsonify({"query": query_text, "results": components, "count": len(components)})


@novel_bp.route("/api/novel/components/<component_id>", methods=["DELETE"])
def novel_component_delete(component_id):
    """컴포넌트 삭제
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: component_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM novel_components WHERE id=?", (component_id,)).fetchone()
        if not row:
            return jsonify({"error": "component not found"}), 404
        conn.execute("DELETE FROM novel_component_usage WHERE component_id=?", (component_id,))
        conn.execute("DELETE FROM novel_components WHERE id=?", (component_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 컴포넌트 활용 (4 API)
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/components/apply", methods=["POST"])
def novel_components_apply():
    """컴포넌트를 프로젝트에 적용
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id, components]
          properties:
            project_id: {type: string}
            components: {type: array}
            adaptation: {type: string, enum: [exact, blend, inspired], default: blend}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    project_id = data.get("project_id", "").strip()
    components = data.get("components", [])
    adaptation = data.get("adaptation", "blend")
    if not project_id or not components:
        return jsonify({"error": "project_id and components required"}), 400

    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "project not found"}), 404

        # 컴포넌트 로드
        applied = []
        now = datetime.now(timezone.utc).isoformat()
        for comp_ref in components:
            comp_id = comp_ref.get("id", "")
            comp_row = conn.execute("SELECT * FROM novel_components WHERE id=?", (comp_id,)).fetchone()
            if not comp_row:
                continue
            comp_row = dict(comp_row)
            role = comp_ref.get("role", "reference")
            note = comp_ref.get("note", "")

            # 활용 이력 기록
            conn.execute(
                "INSERT INTO novel_component_usage (id, component_id, project_id, "
                "usage_type, adaptation_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), comp_id, project_id, role, note, now))
            conn.execute(
                "UPDATE novel_components SET usage_count=usage_count+1 WHERE id=?", (comp_id,))

            applied.append({
                "component_id": comp_id, "type": comp_row["type"],
                "name": comp_row.get("name"), "role": role, "note": note,
            })

        conn.commit()
    finally:
        conn.close()

    return jsonify({"applied": len(applied), "project_id": project_id,
                    "adaptation": adaptation, "components": applied})


@novel_bp.route("/api/novel/components/remix", methods=["POST"])
def novel_components_remix():
    """여러 컴포넌트 조합 → 새 설정 생성
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [components, target_genre]
          properties:
            components: {type: array}
            target_genre: {type: string}
            direction: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    comp_refs = data.get("components", [])
    target_genre = data.get("target_genre", "fantasy")
    direction = data.get("direction", "")
    if not comp_refs:
        return jsonify({"error": "components required"}), 400

    # 컴포넌트 로드
    conn = _get_db()
    try:
        comp_data = []
        for ref in comp_refs:
            comp_id = ref.get("id", "")
            row = conn.execute(
                "SELECT c.*, s.title as source_title FROM novel_components c "
                "LEFT JOIN novel_sources s ON c.source_id = s.id WHERE c.id=?",
                (comp_id,)).fetchone()
            if row:
                d = dict(row)
                d["data"] = json.loads(d["data_json"]) if d.get("data_json") else {}
                comp_data.append(d)
    finally:
        conn.close()

    if not comp_data:
        return jsonify({"error": "no valid components found"}), 404

    comp_desc = "\n".join(
        f"[{c['type']} from '{c.get('source_title', '')}'] {c.get('name', '')}: "
        f"{json.dumps(c['data'], ensure_ascii=False)[:300]}"
        for c in comp_data
    )

    prompt = f"""다음 소설 컴포넌트들을 조합하여 새로운 소설 설정을 만드세요.
타겟 장르: {target_genre}
{f'방향: {direction}' if direction else ''}

{comp_desc}

JSON으로 출력:
{{
  "logline": "한 줄 요약",
  "character_sketch": {{"주인공": "변환된 캐릭터 설명"}},
  "world_sketch": {{"setting": "변환된 세계관"}},
  "plot_skeleton": {{"structure": "플롯 구조 요약"}},
  "tone": "분위기",
  "source_credits": ["원본 소설 → 사용 요소"]
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "소설 기획자입니다. 여러 소설의 요소를 창의적으로 조합하세요. 한국어.",
        {"num_predict": 2500, "temperature": 0.8})
    result = force_korean(result).strip()
    remix = _parse_json_response(result) or {"raw_output": result}
    remix["metadata"] = {"tokens": tokens, "duration_ms": elapsed,
                         "components_used": len(comp_data)}
    return jsonify(remix)


@novel_bp.route("/api/novel/components/adapt", methods=["POST"])
def novel_components_adapt():
    """장르 변환 적용
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [component_id, to_genre]
          properties:
            component_id: {type: string}
            from_genre: {type: string}
            to_genre: {type: string}
            direction: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    comp_id = data.get("component_id", "").strip()
    to_genre = data.get("to_genre", "").strip()
    if not comp_id or not to_genre:
        return jsonify({"error": "component_id and to_genre required"}), 400

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT c.*, s.title as source_title FROM novel_components c "
            "LEFT JOIN novel_sources s ON c.source_id = s.id WHERE c.id=?",
            (comp_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "component not found"}), 404

    comp = dict(row)
    comp_data = json.loads(comp["data_json"]) if comp.get("data_json") else {}
    from_genre = data.get("from_genre") or comp["genre"]
    direction = data.get("direction", "")
    to_cfg = GENRE_PROMPTS.get(to_genre, {})

    prompt = f"""다음 소설 컴포넌트를 {from_genre} → {to_genre} 장르로 변환하세요.
{f'방향: {direction}' if direction else ''}

[원본 ({from_genre})]
타입: {comp['type']}
이름: {comp.get('name', '')}
데이터: {json.dumps(comp_data, ensure_ascii=False)[:800]}

[타겟 장르 특성] {to_genre}: {to_cfg.get('style_note', '')}

JSON으로 출력:
{{
  "original": {{"name": "원본 이름", "genre": "{from_genre}"}},
  "adapted": {{
    "name": "변환된 이름",
    "genre": "{to_genre}",
    "data": {{변환된 데이터}},
    "preserved_essence": "보존된 핵심",
    "genre_adaptation_notes": "변환 설명"
  }}
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(NOVEL_MODEL, prompt,
        "장르 변환 전문가입니다. 원본의 에센스를 보존하며 타겟 장르로 자연스럽게 변환하세요. 한국어.",
        {"num_predict": 2000, "temperature": 0.7})
    result = force_korean(result).strip()
    adapted = _parse_json_response(result) or {"raw_output": result}
    adapted["metadata"] = {"tokens": tokens, "duration_ms": elapsed}
    return jsonify(adapted)


@novel_bp.route("/api/novel/components/<component_id>", methods=["PUT"])
def novel_component_update(component_id):
    """컴포넌트 수동 편집
    ---
    tags: [Novel Decomposer - Components]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: component_id, type: string, required: true}
      - in: body
        name: body
        schema:
          type: object
          properties:
            name: {type: string}
            tags: {type: array, items: {type: string}}
            data: {type: object}
            quality_score: {type: number}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM novel_components WHERE id=?", (component_id,)).fetchone()
        if not row:
            return jsonify({"error": "component not found"}), 404

        updates = []
        params = []
        if "name" in data:
            updates.append("name=?")
            params.append(data["name"])
        if "tags" in data:
            updates.append("tags=?")
            params.append(json.dumps(data["tags"], ensure_ascii=False))
        if "data" in data:
            updates.append("data_json=?")
            params.append(json.dumps(data["data"], ensure_ascii=False))
        if "quality_score" in data:
            updates.append("quality_score=?")
            params.append(data["quality_score"])

        if updates:
            params.append(component_id)
            conn.execute(f"UPDATE novel_components SET {', '.join(updates)} WHERE id=?", params)
            conn.commit()

        updated = conn.execute("SELECT * FROM novel_components WHERE id=?", (component_id,)).fetchone()
    finally:
        conn.close()

    d = dict(updated)
    if d.get("data_json"):
        try:
            d["data"] = json.loads(d["data_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        del d["data_json"]
    return jsonify(d)


# ===========================================================================
# Sprint 6 — 컴포넌트 기반 소설 생성 파이프라인
# ===========================================================================

def _execute_scaffold(title, genre, target, components, direction, conn):
    """scaffold 내부 — 프로젝트+세계관+인물+시놉시스+플롯+아웃라인 자동 생성."""
    now = datetime.now(timezone.utc).isoformat()
    project_id = str(uuid.uuid4())

    comp_desc_parts = []
    loaded_refs = {}
    for key, refs in (components or {}).items():
        if isinstance(refs, dict):
            refs = [refs]
        if not isinstance(refs, list):
            continue
        for ref in refs:
            cid = ref.get("component_id", "")
            row = conn.execute("SELECT * FROM novel_components WHERE id=?", (cid,)).fetchone()
            if row:
                row = dict(row)
                cdata = json.loads(row["data_json"]) if row.get("data_json") else {}
                adapt = ref.get("adapt", "")
                role = ref.get("role", "reference")
                loaded_refs[cid] = {"type": row["type"], "name": row.get("name"),
                                    "adapt": adapt, "role": role}
                comp_desc_parts.append(
                    f"[{row['type']}: {ref.get('rename') or row.get('name', '')}] "
                    f"변환={adapt or '없음'} 데이터={json.dumps(cdata, ensure_ascii=False)[:300]}")

    comp_context = "\n".join(comp_desc_parts) if comp_desc_parts else "(컴포넌트 없음)"
    settings = target or {}
    est_ch = settings.get("estimated_chapters", 12)
    ch_len = settings.get("chapter_length", 5000)
    tone = settings.get("tone", "warm")
    pov = settings.get("pov", "third_person")
    genre_cfg = GENRE_PROMPTS.get(genre, {})

    prompt = f"""컴포넌트 기반 소설 뼈대를 설계하세요.

[제목] {title}
[장르] {genre} — {genre_cfg.get('style_note', '')}
[방향] {direction or '자유'}
[설정] {est_ch}챕터, {ch_len}자/챕터, {tone} 톤, {pov} 시점
[컴포넌트]
{comp_context}

아래 JSON 형식으로 출력하세요:
```json
{{
  "logline": "한 줄 요약",
  "synopsis": {{
    "premise": "전제",
    "act1_setup": "1막",
    "act2_conflict": "2막",
    "act3_resolution": "3막",
    "themes": ["테마"],
    "ending_type": "bittersweet"
  }},
  "world": {{
    "name": "세계 이름",
    "era": "시대",
    "geography": "지리",
    "technology": "기술 수준",
    "rules": ["규칙"],
    "tone_guide": "분위기"
  }},
  "characters": [
    {{"name": "이름", "role": "protagonist", "traits": "성격", "motivation": "동기", "speech_style": "말투"}}
  ],
  "relationships": [
    {{"from": "인물A", "to": "인물B", "type": "관계", "dynamic": "역학"}}
  ],
  "plot": {{
    "structure": "three_act",
    "acts": [{{"act": 1, "name": "이름", "chapters": [1,2], "purpose": "목적"}}],
    "turning_points": [{{"chapter": 1, "event": "사건"}}],
    "foreshadowing_plan": [{{"plant_chapter": 1, "hint": "복선"}}]
  }},
  "outline": [
    {{"chapter": 1, "title": "제목", "synopsis": "줄거리", "scenes": [{{"scene": 1, "purpose": "목적"}}], "chapter_hook": "끝 훅"}}
  ]
}}
```"""

    # scaffold는 구조 설계 → 빠른 모델 사용 (GPU에서 7b=~30tok/s)
    scaffold_model = FAST_MODEL if est_ch <= 12 else NOVEL_MODEL
    max_tokens = min(est_ch * 800 + 3000, 10000)  # 챕터당 ~800토큰 + 기본 3000

    result, tokens, elapsed = _generate(scaffold_model, prompt,
        "소설 아키텍트입니다. 반드시 위 JSON 형식 전체를 출력하세요. 모든 키를 포함하세요. 한국어로 간결하게.",
        {"num_predict": max_tokens, "temperature": 0.7}, timeout=300)
    # scaffold는 JSON 구조 보존이 중요 → force_korean 미적용
    result = result.strip()
    sd = _parse_json_response(result)
    if not sd:
        return None, "LLM 응답 파싱 실패", result

    sj = json.dumps({"tone": tone, "pov": pov, "estimated_chapters": est_ch,
                     "chapter_length": ch_len, "language": "ko"}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO novel_projects (id,title,genre,logline,synopsis_json,plot_json,"
        "outline_json,settings_json,total_chapters,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'planning',?)",
        (project_id, title, genre, sd.get("logline"),
         json.dumps(sd.get("synopsis", {}), ensure_ascii=False),
         json.dumps(sd.get("plot", {}), ensure_ascii=False),
         json.dumps(sd.get("outline", []), ensure_ascii=False), sj, est_ch, now))

    world_id = None
    wd = sd.get("world")
    if isinstance(wd, dict) and wd.get("name"):
        world_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO story_worlds (id,name,era,geography,technology,rules_json,tone_guide,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (world_id, wd["name"], wd.get("era"), wd.get("geography"), wd.get("technology"),
             json.dumps(wd.get("rules", []), ensure_ascii=False), wd.get("tone_guide"), now))
        conn.execute("UPDATE novel_projects SET world_id=? WHERE id=?", (world_id, project_id))

    chain_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO story_chains (id,title,style,tone,pov,world_context,created_at) "
        "VALUES (?,?,'novel',?,?,?,?)",
        (chain_id, title, tone, pov,
         json.dumps(wd, ensure_ascii=False) if wd else None, now))
    conn.execute("UPDATE novel_projects SET chain_id=? WHERE id=?", (chain_id, project_id))

    char_map = {}
    for ch in (sd.get("characters") or []):
        if isinstance(ch, dict) and ch.get("name"):
            cid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO story_characters (id,chain_id,world_id,name,tier,type,"
                "profile_json,speech_matrix_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, chain_id, world_id, ch["name"],
                 "protagonist" if ch.get("role") == "protagonist" else "supporting", "npc",
                 json.dumps({"traits": ch.get("traits", ""), "motivation": ch.get("motivation", "")}, ensure_ascii=False),
                 json.dumps({"style": ch.get("speech_style", "")}, ensure_ascii=False), now))
            char_map[ch["name"]] = cid

    for rel in (sd.get("relationships") or []):
        if isinstance(rel, dict) and rel.get("from") and rel.get("to"):
            conn.execute(
                "INSERT INTO story_relationships (chain_id,from_character,to_character,type,state,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (chain_id, rel["from"], rel["to"], rel.get("type", ""), rel.get("dynamic", ""), now))

    for co in (sd.get("outline") or []):
        if isinstance(co, dict) and co.get("chapter"):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO novel_chapters (id,project_id,chapter_num,title,scenes_json,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), project_id, co["chapter"], co.get("title"),
                     json.dumps(co.get("scenes", []), ensure_ascii=False), now))
            except Exception:
                pass

    for fs in (sd.get("plot", {}).get("foreshadowing_plan") or []):
        if isinstance(fs, dict) and fs.get("hint"):
            conn.execute(
                "INSERT INTO story_foreshadowing (id,chain_id,planted_episode,hint,importance,created_at) "
                "VALUES (?,?,?,?,'major',?)",
                (str(uuid.uuid4()), chain_id, fs.get("plant_chapter"), fs["hint"], now))

    for cid_ref, info in loaded_refs.items():
        conn.execute(
            "INSERT INTO novel_project_refs (id,project_id,component_id,ref_type,ref_role,adaptation_note,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), project_id, cid_ref, info["type"], info["role"], info["adapt"], now))

    conn.commit()
    return {"project_id": project_id, "title": title, "world_id": world_id, "chain_id": chain_id,
            "characters": [{"name": n, "id": i} for n, i in char_map.items()],
            "synopsis": bool(sd.get("synopsis")), "plot": bool(sd.get("plot")),
            "outline_chapters": len(sd.get("outline", [])),
            "foreshadowing_planted": len(sd.get("plot", {}).get("foreshadowing_plan", [])),
            "component_refs": len(loaded_refs),
            "metadata": {"tokens": tokens, "duration_ms": elapsed}}, None, None


@novel_bp.route("/api/novel/scaffold", methods=["POST"])
def novel_scaffold():
    """컴포넌트 → 프로젝트 뼈대 자동 생성
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title, genre]
          properties:
            title: {type: string}
            genre: {type: string}
            target: {type: object}
            components: {type: object}
            direction: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    genre = data.get("genre", "").strip()
    if not title or not genre:
        return jsonify({"error": "title and genre required"}), 400
    conn = _get_db()
    try:
        result, error, raw = _execute_scaffold(title, genre, data.get("target"),
                                                data.get("components"), data.get("direction"), conn)
    finally:
        conn.close()
    if error:
        return jsonify({"error": error, "raw_output": raw}), 500
    return jsonify({"status": "scaffold_complete", **result})


@novel_bp.route("/api/novel/scaffold/<project_id>/status", methods=["GET"])
def novel_scaffold_status(project_id):
    """뼈대 생성 상태
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        p = conn.execute("SELECT id,title,status,world_id,chain_id FROM novel_projects WHERE id=?",
                         (project_id,)).fetchone()
        if not p:
            return jsonify({"error": "project not found"}), 404
        p = dict(p)
        ch_n = conn.execute("SELECT COUNT(*) as c FROM novel_chapters WHERE project_id=?", (project_id,)).fetchone()["c"]
        ref_n = conn.execute("SELECT COUNT(*) as c FROM novel_project_refs WHERE project_id=?", (project_id,)).fetchone()["c"]
    finally:
        conn.close()
    return jsonify({"project_id": project_id, "status": p["status"],
                    "scaffold": {"world": bool(p.get("world_id")), "chain": bool(p.get("chain_id")),
                                 "chapters_outlined": ch_n, "component_refs": ref_n}})


@novel_bp.route("/api/novel/create-from-components", methods=["POST"])
def novel_create_from_components():
    """컴포넌트 기반 프로젝트 자동 생성
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title, genre]
          properties:
            title: {type: string}
            genre: {type: string}
            target: {type: object}
            components: {type: object}
            direction: {type: string}
            auto_write: {type: boolean, default: false}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    genre = data.get("genre", "").strip()
    if not title or not genre:
        return jsonify({"error": "title and genre required"}), 400
    conn = _get_db()
    try:
        result, error, raw = _execute_scaffold(title, genre, data.get("target"),
                                                data.get("components"), data.get("direction"), conn)
    finally:
        conn.close()
    if error:
        return jsonify({"error": error, "raw_output": raw}), 500
    result["status"] = "scaffold_complete"
    result["next_step"] = f"POST /api/novel/chapter/write {{project_id: '{result['project_id']}', chapter_num: 1}}"
    return jsonify(result)


@novel_bp.route("/api/novel/create-from-components/stream", methods=["POST"])
def novel_create_from_components_stream():
    """컴포넌트 기반 프로젝트 생성 SSE
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title, genre]
    responses:
      200:
        description: SSE stream
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    genre = data.get("genre", "").strip()
    if not title or not genre:
        return jsonify({"error": "title and genre required"}), 400

    def sse_gen():
        yield f"data: {json.dumps({'stage': 'start', 'title': title})}\n\n"
        c = _get_db()
        try:
            res, err, raw = _execute_scaffold(title, genre, data.get("target"),
                                               data.get("components"), data.get("direction"), c)
            if err:
                yield f"data: {json.dumps({'error': err})}\n\n"
            else:
                yield f"data: {json.dumps({'done': True, **res}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            c.close()

    return Response(sse_gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@novel_bp.route("/api/novel/scene/write-with-ref", methods=["POST"])
def novel_scene_write_with_ref():
    """레퍼런스 기반 장면 집필
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [project_id]
          properties:
            project_id: {type: string}
            chapter_num: {type: integer}
            scene_type: {type: string, enum: [write, transition, dialogue, description, inner_voice], default: write}
            characters: {type: array, items: {type: string}}
            situation: {type: string}
            target_length: {type: integer, default: 1500}
            references: {type: object}
            reference_strength: {type: string, enum: [strong, moderate, subtle], default: moderate}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    pid = data.get("project_id", "").strip()
    if not pid:
        return jsonify({"error": "project_id required"}), 400
    conn = _get_db()
    try:
        proj = conn.execute("SELECT * FROM novel_projects WHERE id=?", (pid,)).fetchone()
        if not proj:
            return jsonify({"error": "project not found"}), 404
        proj = dict(proj)
        ctx, _ = _build_novel_context(proj, data.get("chapter_num", 1), conn,
                                       component_refs=data.get("references"))
    finally:
        conn.close()

    st = data.get("scene_type", "write")
    cfg = _SCENE_TYPE_PROMPTS.get(st, _SCENE_TYPE_PROMPTS["write"])
    gcfg = GENRE_PROMPTS.get(proj.get("genre", "fantasy"), {})
    tlen = data.get("target_length", 1500)
    rs = data.get("reference_strength", "moderate")
    sm = {"strong": "레퍼런스를 충실하게 모방.", "moderate": "에센스 참고, 자체 색깔 유지.",
          "subtle": "영감만 받고 독자적 스타일."}

    prompt = f"""{ctx}
{f'[인물] {", ".join(data.get("characters", []))}' if data.get("characters") else ''}
{f'[상황] {data.get("situation", "")}' if data.get("situation") else ''}
{cfg['instruction']} 약 {tlen}자."""

    system = f"{NOVEL_CRAFT_RULES}\n{cfg['system']}\n[장르] {proj.get('genre', '')} — {gcfg.get('style_note', '')}\n[레퍼런스] {sm.get(rs, sm['moderate'])}\n한국어."

    content, tokens, elapsed = _generate(NOVEL_MODEL, prompt, system,
                                          {"num_predict": int(tlen * 1.3)}, timeout=120)
    content = force_korean(content).strip()
    if content.startswith("Error:"):
        return jsonify({"error": content}), 500

    quality_result = compute_korean_quality(content, genre="essay")
    quality_metrics_data = get_quality_summary(quality_result)

    return jsonify({"scene_type": st, "content": content,
                    "metadata": {"word_count": len(content), "tokens": tokens, "duration_ms": elapsed},
                    "quality_metrics": quality_metrics_data})


# ---------------------------------------------------------------------------
# 레시피 CRUD + execute
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/recipe/create", methods=["POST"])
def novel_recipe_create():
    """레시피 생성
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name, genre]
          properties:
            name: {type: string}
            description: {type: string}
            genre: {type: string}
            component_slots: {type: object}
            default_settings: {type: object}
            adaptation_rules: {type: array, items: {type: string}}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    genre = data.get("genre", "").strip()
    if not name or not genre:
        return jsonify({"error": "name and genre required"}), 400
    rid = str(uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO novel_recipes (id,name,description,genre,component_slots_json,"
            "default_settings_json,adaptation_rules_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, name, data.get("description"), genre,
             json.dumps(data.get("component_slots", {}), ensure_ascii=False),
             json.dumps(data.get("default_settings", {}), ensure_ascii=False),
             json.dumps(data.get("adaptation_rules", []), ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"recipe_id": rid, "name": name, "genre": genre})


@novel_bp.route("/api/novel/recipes", methods=["GET"])
def novel_recipes_list():
    """레시피 목록
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM novel_recipes ORDER BY usage_count DESC").fetchall()
    finally:
        conn.close()
    recipes = []
    for r in rows:
        d = dict(r)
        for jf in ("component_slots_json", "default_settings_json", "adaptation_rules_json"):
            if d.get(jf):
                try:
                    d[jf.replace("_json", "")] = json.loads(d[jf])
                except (json.JSONDecodeError, TypeError):
                    pass
                del d[jf]
        recipes.append(d)
    return jsonify({"recipes": recipes, "count": len(recipes)})


@novel_bp.route("/api/novel/recipe/<recipe_id>", methods=["DELETE"])
def novel_recipe_delete(recipe_id):
    """레시피 삭제
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: recipe_id, type: string, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM novel_recipes WHERE id=?", (recipe_id,)).fetchone()
        if not row:
            return jsonify({"error": "recipe not found"}), 404
        conn.execute("DELETE FROM novel_recipes WHERE id=?", (recipe_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@novel_bp.route("/api/novel/recipe/<recipe_id>/execute", methods=["POST"])
def novel_recipe_execute(recipe_id):
    """레시피 실행 → 프로젝트 자동 생성
    ---
    tags: [Novel Writer - Pipeline]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: recipe_id, type: string, required: true}
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [title]
          properties:
            title: {type: string}
            overrides: {type: object}
            direction: {type: string}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    conn = _get_db()
    try:
        recipe = conn.execute("SELECT * FROM novel_recipes WHERE id=?", (recipe_id,)).fetchone()
        if not recipe:
            return jsonify({"error": "recipe not found"}), 404
        recipe = dict(recipe)
        genre = recipe["genre"]
        slots = json.loads(recipe.get("component_slots_json") or "{}") if recipe.get("component_slots_json") else {}
        dsettings = json.loads(recipe.get("default_settings_json") or "{}") if recipe.get("default_settings_json") else {}
        overrides = data.get("overrides", {})
        components = {}
        for sn, sd in slots.items():
            cid = overrides.get(sn) or (sd.get("default_component") if isinstance(sd, dict) else None)
            if cid:
                components[sn] = [{"component_id": cid, "role": sn}]
        target = dict(dsettings)
        for k in ("tone", "pov", "estimated_chapters", "chapter_length"):
            if k in overrides:
                target[k] = overrides[k]
        result, error, raw = _execute_scaffold(title, genre, target, components, data.get("direction"), conn)
        if not error:
            conn.execute("UPDATE novel_recipes SET usage_count=usage_count+1,updated_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), recipe_id))
            conn.execute("INSERT OR REPLACE INTO novel_project_recipe (project_id,recipe_id,overrides_json,executed_at) VALUES (?,?,?,?)",
                         (result["project_id"], recipe_id, json.dumps(overrides, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
            conn.commit()
    finally:
        conn.close()
    if error:
        return jsonify({"error": error, "raw_output": raw}), 500
    result["recipe_used"] = {"id": recipe_id, "name": recipe["name"]}
    result["status"] = "scaffold_complete"
    return jsonify(result)


# ===========================================================================
# LibriScribe 연동 확장 (E2, E5) — v2.55.0
# ===========================================================================

@novel_bp.route("/api/novel/project/<project_id>/snapshot", methods=["GET"])
def novel_project_snapshot(project_id):
    """프로젝트 전체 상태 스냅샷 — 1회 호출로 전체 정보 반환 (E2)
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: path
        name: project_id
        required: true
        type: string
    responses:
      200:
        schema:
          type: object
          properties:
            project: {type: object}
            synopsis: {type: object}
            outlines: {type: array}
            chapters: {type: array}
            world: {type: object}
            characters: {type: array}
            foreshadows: {type: array}
    """
    conn = _get_db()
    try:
        project = conn.execute("SELECT * FROM novel_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return jsonify({"error": "not found"}), 404
        project = dict(project)

        # 시놉시스
        synopsis = conn.execute(
            "SELECT * FROM novel_synopsis WHERE project_id=?", (project_id,)
        ).fetchone()

        # 아웃라인 (챕터별)
        outlines = conn.execute(
            "SELECT chapter_num, title, synopsis, scenes_json FROM novel_outlines "
            "WHERE project_id=? ORDER BY chapter_num", (project_id,)
        ).fetchall()

        # 챕터 메타 (본문 제외 — 크기 절감)
        chapters = conn.execute(
            "SELECT chapter_num, title, word_count, status, created_at, updated_at "
            "FROM novel_chapters WHERE project_id=? ORDER BY chapter_num", (project_id,)
        ).fetchall()

        # 세계관 + 캐릭터 + 복선 (chain_id 기반 일괄 조회)
        world = None
        characters = []
        foreshadows = []
        world_id = project.get("world_id")
        chain_id = project.get("chain_id")
        if world_id or chain_id:
            # 세계관: world_id 우선, 없으면 chain_id 경유
            if world_id:
                world = conn.execute("SELECT * FROM story_worlds WHERE id=?", (world_id,)).fetchone()
            elif chain_id:
                world = conn.execute("SELECT * FROM story_worlds WHERE chain_id=? LIMIT 1", (chain_id,)).fetchone()

        if chain_id:
            # 캐릭터 + 복선 동시 조회 (chain_id 공유)
            characters = conn.execute(
                "SELECT name, tier, type, profile_json, speech_matrix_json, current_state_json "
                "FROM story_characters WHERE chain_id=? "
                "ORDER BY CASE tier WHEN 'protagonist' THEN 0 WHEN 'main' THEN 1 ELSE 2 END",
                (chain_id,)
            ).fetchall()
            foreshadows = conn.execute(
                "SELECT * FROM story_foreshadowing WHERE chain_id=? AND status != 'resolved'",
                (chain_id,)
            ).fetchall()

        return jsonify({
            "project": project,
            "synopsis": dict(synopsis) if synopsis else None,
            "outlines": [dict(o) for o in outlines],
            "chapters": [dict(c) for c in chapters],
            "world": dict(world) if world else None,
            "characters": [dict(c) for c in characters],
            "foreshadows": [dict(f) for f in foreshadows],
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("snapshot error: %s", e)
        return internal_error(e)
    finally:
        conn.close()


@novel_bp.route("/api/novel/extract/metadata", methods=["POST"])
def novel_extract_metadata():
    """텍스트에서 소설 메타데이터 자동 추출 (E5)
    ---
    tags: [Novel Writer]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [content]
          properties:
            content: {type: string, description: "분석할 소설 텍스트"}
            known_characters: {type: array, items: {type: string}, description: "이미 알려진 캐릭터 목록"}
            known_foreshadows: {type: array, items: {type: string}, description: "이미 알려진 떡밥 목록"}
            genre: {type: string, default: web_novel}
    responses:
      200:
        schema:
          type: object
          properties:
            characters_appeared: {type: array}
            new_characters: {type: array}
            foreshadows: {type: object}
            relationship_changes: {type: array}
            growth_events: {type: array}
            tension_level: {type: number}
            dominant_emotion: {type: string}
    """
    data = request.json or {}
    errors = validate(data, {
        "content": {"required": True, "type": "str", "max_length": 100000},
        "genre": {"enum": list(GENRE_PROMPTS.keys()), "default": "web_novel"},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    content = data["content"].strip()
    known_characters = data.get("known_characters", [])
    known_foreshadows = data.get("known_foreshadows", [])

    prompt = f"""다음 소설 텍스트를 분석하여 메타데이터를 추출하세요.

[알려진 캐릭터] {', '.join(known_characters) if known_characters else '없음'}
[알려진 떡밥] {', '.join(known_foreshadows) if known_foreshadows else '없음'}

[텍스트]
{content[:10000]}

JSON으로 출력:
{{
  "characters_appeared": ["등장한 캐릭터명"],
  "new_characters": [{{"name": "이름", "role": "역할 추정"}}],
  "foreshadows": {{
    "planted": [{{"content": "새로 설치된 떡밥", "importance": "normal|major|critical"}}],
    "reminded": ["리마인드된 기존 떡밥"],
    "paid_off": ["회수된 기존 떡밥"]
  }},
  "relationship_changes": [
    {{"characterA": "A", "characterB": "B", "change": "설명", "direction": "positive|negative|neutral"}}
  ],
  "growth_events": [
    {{"type": "level_up|skill_acquire|stat_increase|rank_up", "details": "설명"}}
  ],
  "tension_level": 0.0,
  "dominant_emotion": "긴장감|설렘|슬픔|분노|유머|공포|감동"
}}
JSON만 출력."""

    result, tokens, elapsed = _generate(
        FAST_MODEL, prompt,
        "소설 분석가입니다. 텍스트에서 정확하게 메타데이터를 추출하세요. JSON만 출력.",
        {"num_predict": 800 + min(len(content) // 10, 700), "temperature": 0.2}
    )

    metadata = _parse_json_response(result) or {"raw_output": result}
    metadata["_meta"] = {"tokens": tokens, "duration_ms": elapsed, "model": FAST_MODEL}
    return jsonify(metadata)


# ---------------------------------------------------------------------------
# API: 캐릭터 경험 추적 조회
# ---------------------------------------------------------------------------

@novel_bp.route("/api/novel/character/experiences/<project_id>", methods=["GET"])
def novel_character_experiences(project_id):
    """캐릭터별 경험 추적 조회 — 챕터별 누적 경험 로그
    ---
    tags: [Novel Writer - Character]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
      - {in: query, name: character, type: string, description: "특정 캐릭터만 조회"}
    responses:
      200:
        schema:
          type: object
    """
    character = request.args.get("character")
    conn = _get_db()
    try:
        if character:
            rows = conn.execute(
                "SELECT * FROM novel_character_experiences "
                "WHERE project_id=? AND character_name=? ORDER BY chapter_num",
                (project_id, character)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM novel_character_experiences "
                "WHERE project_id=? ORDER BY character_name, chapter_num",
                (project_id,)
            ).fetchall()
    finally:
        conn.close()

    # 캐릭터별 그룹핑
    characters = {}
    for r in rows:
        r = dict(r)
        name = r["character_name"]
        if name not in characters:
            characters[name] = []
        characters[name].append({
            "chapter": r["chapter_num"],
            "experience": r["experience"],
            "emotion": r.get("emotion"),
            "growth": r.get("growth"),
        })

    return jsonify({
        "project_id": project_id,
        "characters": characters,
        "total_entries": len(rows),
    })


@novel_bp.route("/api/novel/chapter/snapshot/<project_id>/<int:chapter_num>", methods=["GET"])
def novel_chapter_snapshot(project_id, chapter_num):
    """챕터 스냅샷 조회 — 집필 시점의 프로젝트 상태
    ---
    tags: [Novel Writer - Chapter]
    security: [{Bearer: []}]
    parameters:
      - {in: path, name: project_id, type: string, required: true}
      - {in: path, name: chapter_num, type: integer, required: true}
    responses:
      200:
        schema:
          type: object
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM novel_chapter_snapshots WHERE project_id=? AND chapter_num=?",
            (project_id, chapter_num)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "snapshot not found"}), 404

    snapshot = json.loads(row["snapshot_json"]) if row["snapshot_json"] else {}
    snapshot["created_at"] = row["created_at"]
    return jsonify(snapshot)