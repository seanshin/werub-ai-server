"""Game NPC API Blueprint — 게임 NPC 대화/성장/히로인 시스템"""
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime

import requests
from core import model_router
from core.config import get_self_base as _get_self_base
from flask import Blueprint, request, jsonify
from core.errors import internal_error

logger = logging.getLogger("ollama-admin")

game_bp = Blueprint("game", __name__)


def _escape_like(val: str) -> str:
    """LIKE 패턴 특수문자 이스케이프"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# app.py에서 init_game()로 주입
_load_config = None
_ollama_session = None
_llm_generate_korean = None
_force_korean_token = None
_OLLAMA_BASE = None
_BASE_DIR = None


def init_game(*, load_config, ollama_session, llm_generate_korean,
              force_korean_token, ollama_base, base_dir):
    global _load_config, _ollama_session, _llm_generate_korean
    global _force_korean_token, _OLLAMA_BASE, _BASE_DIR
    _load_config = load_config
    _ollama_session = ollama_session
    _llm_generate_korean = llm_generate_korean
    _force_korean_token = force_korean_token
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir


_game_db_local = threading.local()


def _router_adapter():
    """호출자 세션(`_ollama_session`=ChatFairQueue 게이트)을 어댑터에 넘긴다 — 안 넘기면 우회."""
    return model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)


def _router_response(res, default=""):
    """성공 응답의 `response`를 꺼낸다 — 실패는 **전부 던진다**.

    v2.125.15 무성 실패 일괄: 종전엔 상태 코드를 안 봐서 HTTP 오류(`{"error":…}`)가
    `default`로 조용히 흘렀다(200인데 빈 결과 클래스). 이제 HTTP 오류도 전송 예외와
    **같은 분기**로 던져 호출자의 기존 `except`(500/폴백)에 닿는다 — 새 계약을 만들지
    않고 이미 있던 실패 경로에 합류시키는 것이 이 일괄의 원칙이다.
    """
    if not res.ok:
        raise RuntimeError(res.error or ("HTTP %s" % res.status))
    return res.raw.get("response", default)

def _get_game_db():
    conn = getattr(_game_db_local, "conn", None)
    if conn is None:
        _gpath = os.path.join(_BASE_DIR, "game.db")
        conn = sqlite3.connect(_gpath, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _game_db_local.conn = conn
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        try: conn.close()
        except Exception: pass
        _game_db_local.conn = None
        return _get_game_db()
    return conn

def _resolve_game(request):
    """X-Game-Key 헤더로 게임사 식별"""
    key = request.headers.get("X-Game-Key") or request.args.get("game_key", "")
    if not key:
        return None
    conn = _get_game_db()
    t = conn.execute("SELECT * FROM game_tenants WHERE api_key=? AND is_active=1", (key,)).fetchone()
    return dict(t) if t else None

def _get_or_create_instance(game_id, player_id, template_id):
    """NPC 인스턴스 조회 (없으면 템플릿에서 자동 생성)"""
    conn = _get_game_db()
    inst = conn.execute("SELECT * FROM npc_instances WHERE template_id=? AND player_id=?",
                        (template_id, player_id)).fetchone()
    if inst:
        return dict(inst)
    # 템플릿에서 복사
    tpl = conn.execute("SELECT * FROM npc_templates WHERE id=? AND game_id=?",
                       (template_id, game_id)).fetchone()
    if not tpl:
        return None
    import uuid as _u
    inst_id = str(_u.uuid4())
    conn.execute("""INSERT INTO npc_instances
        (id, template_id, game_id, player_id, level, exp, stats, skills, relationships, created_at, updated_at)
        VALUES (?,?,?,?,?,0,?,?,?,datetime('now'),datetime('now'))""",
        (inst_id, template_id, game_id, player_id, tpl["base_level"] or 1,
         tpl["base_stats"] or '{}', tpl["base_skills"] or '[]', '{}'))
    conn.commit()
    return dict(conn.execute("SELECT * FROM npc_instances WHERE id=?", (inst_id,)).fetchone())

GAME_NPC_PROMPT = """\ub2f9\uc2e0\uc740 {name}\uc785\ub2c8\ub2e4. \ubc18\ub4dc\uc2dc \ud55c\uad6d\uc5b4\ub85c \ub2f5\ubcc0\ud558\uc138\uc694.
\ud074\ub798\uc2a4: {npc_class} | \ub808\ubca8: {level}
\uc131\uaca9: {personality}
\ubc30\uacbd: {background}

[\ub2a5\ub825\uce58] {stats}
[\uc2a4\ud0ac] {skills}
[\uad00\uacc4] {relationships}
[\uae30\uc5b5] {memories}
[\uc138\uacc4\uad00] {world}

[\uaddc\uce59]
1. {name}\uc758 \uc131\uaca9\uacfc \ub9d0\ud22c\ub97c \uc720\uc9c0\ud558\uc138\uc694
2. \uac10\uc815/\ud589\ub3d9 \ubb18\uc0ac\ub294 *\ubcc4\ud45c*\ub85c \ud45c\ud604
3. \ub808\ubca8\uacfc \ub2a5\ub825\uc5d0 \ub9de\ub294 \uc9c0\uc2dd \uc218\uc900\uc73c\ub85c \ub300\ud654
4. \uad00\uacc4\uc5d0 \ub530\ub77c \ud0dc\ub3c4 \uc870\uc808 (\uce5c\ubc00\ub3c4 \ub192\uc73c\uba74 \uce5c\uadfc, \ub0ae\uc73c\uba74 \uacbd\uacc4)
5. \uc0c8\ub85c\uc6b4 \uc815\ubcf4\ub97c \uc790\uc5f0\uc2a4\ub7fd\uac8c \uc81c\uacf5
"""

EXP_TABLE = [0, 100, 300, 600, 1000, 1500, 2200, 3000, 4000, 5500,
             7000, 9000, 11500, 14500, 18000, 22000, 27000, 33000, 40000, 50000]

def _exp_for_level(level):
    if level <= 0: return 0
    if level <= len(EXP_TABLE): return EXP_TABLE[level - 1]
    return EXP_TABLE[-1] + (level - len(EXP_TABLE)) * 15000


# === Game Tenant API ===

@game_bp.route("/api/game/register", methods=["POST"])
def game_register():
    """\uac8c\uc784\uc0ac \ub4f1\ub85d (X-Game-Key \ubc1c\uae09)
    ---
    tags: [Game]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [name]
          properties:
            name: {type: string, description: "\uac8c\uc784\uba85/\ud504\ub85c\uc81d\ud2b8\uba85"}
            plan: {type: string, enum: [basic, standard, premium]}
            max_players: {type: integer, default: 1000}
            max_npcs: {type: integer, default: 100}
    responses:
      200:
        schema:
          type: object
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    import uuid as _u, secrets as _s
    gid = str(_u.uuid4())[:12]
    key = "game-" + _s.token_hex(16)
    conn = _get_game_db()
    conn.execute("INSERT INTO game_tenants (id,name,api_key,plan,max_players,max_npcs,created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
                 (gid, name, key, data.get("plan","basic"), data.get("max_players",1000), data.get("max_npcs",100)))
    conn.commit()
    return jsonify({"game_id": gid, "name": name, "api_key": key, "plan": data.get("plan","basic")})

@game_bp.route("/api/game/tenants")
def game_tenants():
    """\uac8c\uc784\uc0ac \ubaa9\ub85d
    ---
    tags: [Game]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            tenants: {type: array}
    """
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM game_tenants WHERE is_active=1 ORDER BY created_at DESC").fetchall()
    return jsonify({"tenants": [dict(r) for r in rows]})


# === NPC Template API ===

@game_bp.route("/api/game/npc/template", methods=["POST"])
def game_npc_template_create():
    """NPC \ud15c\ud50c\ub9bf \ub4f1\ub85d
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [name, class]
          properties:
            name: {type: string}
            class: {type: string}
            personality: {type: object, description: "{traits, speech_style, tone}"}
            background: {type: string}
            base_stats: {type: object, description: "{strength, intelligence, charisma, wisdom}"}
            base_skills: {type: array, items: {type: string}}
            base_level: {type: integer, default: 1}
            appearance: {type: object, description: "{description, character_id}"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "name and class required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    if not data.get("name") or not data.get("class"):
        return jsonify({"error": "name and class required"}), 400
    import uuid as _u
    tid = str(_u.uuid4())
    conn = _get_game_db()
    conn.execute("""INSERT INTO npc_templates (id,game_id,name,class,base_level,personality,background,base_stats,base_skills,appearance,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (tid, game["id"], data["name"], data["class"], data.get("base_level",1),
         json.dumps(data.get("personality",{}),ensure_ascii=False), data.get("background",""),
         json.dumps(data.get("base_stats",{})), json.dumps(data.get("base_skills",[]),ensure_ascii=False),
         json.dumps(data.get("appearance",{}),ensure_ascii=False)))
    conn.commit()
    return jsonify({"template_id": tid, "name": data["name"], "class": data["class"]})

@game_bp.route("/api/game/npc/templates")
def game_npc_templates():
    """NPC \ud15c\ud50c\ub9bf \ubaa9\ub85d
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM npc_templates WHERE game_id=? ORDER BY name", (game["id"],)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        for f in ("personality","base_stats","base_skills","appearance","growth_curve"):
            if d.get(f):
                try: d[f] = json.loads(d[f])
                except Exception: pass
        results.append(d)
    return jsonify({"templates": results})



@game_bp.route("/api/game/npc/template/<template_id>", methods=["PUT"])
def game_npc_template_update(template_id):
    """NPC 템플릿 수정
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: path
        name: template_id
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "no fields to update"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    conn = _get_game_db()
    updates = []
    params = []
    for field in ["name", "class", "base_level", "background"]:
        if field in data:
            updates.append(f"{field}=?")
            params.append(data[field])
    for field in ["personality", "base_stats", "base_skills", "appearance", "growth_curve"]:
        if field in data:
            updates.append(f"{field}=?")
            params.append(json.dumps(data[field], ensure_ascii=False) if isinstance(data[field], (dict, list)) else data[field])
    if "is_shared" in data:
        updates.append("is_shared=?")
        params.append(1 if data["is_shared"] else 0)
        if data["is_shared"]:
            conn.execute("INSERT OR IGNORE INTO shared_npc_state (npc_id, game_id, mood, mood_score, updated_at) VALUES (?, ?, 'neutral', 50, datetime('now'))", (template_id, game["id"]))
    if not updates:
        return jsonify({"error": "no fields to update"}), 400
    updates.append("updated_at=datetime('now')")
    params.append(template_id)
    params.append(game["id"])
    conn.execute(f"UPDATE npc_templates SET {','.join(updates)} WHERE id=? AND game_id=?", params)
    conn.commit()
    return jsonify({"ok": True})


@game_bp.route("/api/game/npc/template/<template_id>", methods=["DELETE"])
def game_npc_template_delete(template_id):
    """NPC 템플릿 삭제
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: path
        name: template_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    conn.execute("DELETE FROM npc_instances WHERE template_id=? AND game_id=?", (template_id, game["id"]))
    conn.execute("DELETE FROM npc_templates WHERE id=? AND game_id=?", (template_id, game["id"]))
    conn.execute("DELETE FROM shared_npc_state WHERE npc_id=?", (template_id,))
    conn.commit()
    return jsonify({"ok": True})


@game_bp.route("/api/game/npc/template/<template_id>/instances")
def game_npc_template_instances(template_id):
    """NPC 인스턴스 목록 (플레이어별 상태)
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: path
        name: template_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    rows = conn.execute("""SELECT i.*, (SELECT COUNT(*) FROM dialogues d WHERE d.player_id=i.player_id AND d.game_id=i.game_id) as dialogue_count
        FROM npc_instances i WHERE i.template_id=? AND i.game_id=? ORDER BY i.level DESC""",
        (template_id, game["id"])).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        for f in ("stats", "skills", "relationships"):
            if d.get(f):
                try: d[f] = json.loads(d[f])
                except Exception: pass
        d["next_level_exp"] = _exp_for_level(d["level"] + 1)
        results.append(d)
    return jsonify({"instances": results})


@game_bp.route("/api/game/npc/template/generate", methods=["POST"])
def game_npc_template_generate():
    """NPC \uc790\ub3d9 \uc0dd\uc131 (LLM)
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [class]
          properties:
            class: {type: string, description: "\ud074\ub798\uc2a4 (\uae30\uc0ac, \ub9c8\ubc95\uc0ac, \ub3c4\uc801 \ub4f1)"}
            level: {type: integer, default: 1}
            genre: {type: string, description: "\uc7a5\ub974 (fantasy, scifi, modern)"}
            keywords: {type: string, description: "\ud2b9\uc9d5 \ud0a4\uc6cc\ub4dc"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "class required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      500:
        description: "서버 오류"
        schema:
          type: object
          properties: {error: {type: string, example: "JSON 파싱 실패"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    npc_class = data.get("class","")
    if not npc_class: return jsonify({"error": "class required"}), 400
    genre = data.get("genre","fantasy")
    level = data.get("level",1)
    keywords = data.get("keywords","")
    # 8가지 조합 요소
    traits_parts = []
    for k, label in [("origin","출신"),("motivation","동기"),("flaw","결함"),("strength","강점"),
                      ("speech","말투"),("appearance_trait","외형특징"),("secret","비밀"),("temperament","기질")]:
        v = data.get(k,"")
        if v: traits_parts.append(f"{label}: {v}")
    traits_desc = "\n".join(traits_parts)

    keywords_line = f"\ud2b9\uc9d5: {keywords}" if keywords else ""
    prompt = f"""\uac8c\uc784 NPC \ud504\ub85c\ud544\uc744 JSON\uc73c\ub85c \uc0dd\uc131\ud558\uc138\uc694. \ubc18\ub4dc\uc2dc \ud55c\uad6d\uc5b4\ub85c \uc791\uc131.
\ud074\ub798\uc2a4: {npc_class}, \ub808\ubca8: {level}, \uc7a5\ub974: {genre}
{keywords_line}

JSON \ud615\uc2dd:
{{
  "name": "\ud55c\uad6d\uc5b4 \uc774\ub984",
  "class": "{npc_class}",
  "personality": {{"traits": ["\uc131\uaca9 3\uac1c"], "speech_style": "\ub9d0\ud22c", "tone": "\ubd84\uc704\uae30"}},
  "background": "\ubc30\uacbd \uc2a4\ud1a0\ub9ac (3~5\ubb38\uc7a5)",
  "base_stats": {{"strength": 0-100, "intelligence": 0-100, "charisma": 0-100, "wisdom": 0-100}},
  "base_skills": ["\uc2a4\ud0ac 2~3\uac1c"],
  "appearance": {{"description": "\uc678\ubaa8 \uc124\uba85"}}
}}
JSON\ub9cc \ucd9c\ub825\ud558\uc138\uc694."""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.npc_generate", prompt, model=model,
            options={"temperature": 0.8, "num_predict": 1000}, timeout=60, adapter=_router_adapter())
        text = _router_response(res)
        # JSON 추출
        import re
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            npc_data = json.loads(m.group())
            npc_data["class"] = npc_class
            npc_data["base_level"] = level
            return jsonify({"generated": npc_data, "note": "\ud655\uc778 \ud6c4 /api/game/npc/template\uc73c\ub85c \ub4f1\ub85d\ud558\uc138\uc694"})
        return jsonify({"error": "JSON \ud30c\uc2f1 \uc2e4\ud328", "raw": text[:500]}), 500
    except Exception as e:
        return internal_error(e)


# === NPC Instance API ===

@game_bp.route("/api/game/npc/instances")
def game_npc_instances():
    """\ud50c\ub808\uc774\uc5b4\uc758 NPC \uc778\uc2a4\ud134\uc2a4 \ubaa9\ub85d
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    pid = request.args.get("player_id","")
    if not pid: return jsonify({"error": "player_id required"}), 400
    conn = _get_game_db()
    rows = conn.execute("""SELECT i.*, t.name, t.class, t.personality, t.background, t.appearance
        FROM npc_instances i JOIN npc_templates t ON i.template_id=t.id
        WHERE i.game_id=? AND i.player_id=?""", (game["id"], pid)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        for f in ("personality","stats","skills","relationships","appearance","base_stats","base_skills","memory_keys","custom_data"):
            if d.get(f):
                try: d[f] = json.loads(d[f])
                except Exception: pass
        d["next_level_exp"] = _exp_for_level(d["level"] + 1)
        results.append(d)
    return jsonify({"player_id": pid, "instances": results})

@game_bp.route("/api/game/npc/instance/init", methods=["POST"])
def game_npc_instance_init():
    """\ud50c\ub808\uc774\uc5b4 \uc2dc\uc791 \u2014 \ubaa8\ub4e0 \ud15c\ud50c\ub9bf\uc744 \uc778\uc2a4\ud134\uc2a4\ub85c \ucd08\uae30\ud654
    ---
    tags: [Game NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id]
          properties:
            player_id: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id","")
    if not pid: return jsonify({"error": "player_id required"}), 400
    conn = _get_game_db()
    templates = conn.execute("SELECT id FROM npc_templates WHERE game_id=?", (game["id"],)).fetchall()
    created = 0
    for t in templates:
        inst = _get_or_create_instance(game["id"], pid, t["id"])
        if inst: created += 1
    return jsonify({"player_id": pid, "instances_created": created})


# === Dialogue API ===

@game_bp.route("/api/game/dialogue", methods=["POST"])
def game_dialogue():
    """NPC 1:1 \ub300\ud654 \uc0dd\uc131
    ---
    tags: [Game Dialogue]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, npc_a, npc_b]
          properties:
            player_id: {type: string}
            npc_a: {type: string, description: "NPC \ud15c\ud50c\ub9bf ID"}
            npc_b: {type: string, description: "NPC \ud15c\ud50c\ub9bf ID"}
            context: {type: string, description: "\uc0c1\ud669 \uc124\uba85"}
            mood: {type: string, description: "\ubd84\uc704\uae30 (\uae34\uc7a5, \uc720\ucf8c \ub4f1)"}
            turns: {type: integer, default: 4}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id, npc_a, npc_b required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC template not found"}}
      500:
        description: "서버 오류"
        schema:
          type: object
          properties: {error: {type: string, example: "LLM 처리 중 오류"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id","")
    npc_a_id = data.get("npc_a","")
    npc_b_id = data.get("npc_b","")
    if not pid or not npc_a_id or not npc_b_id:
        return jsonify({"error": "player_id, npc_a, npc_b required"}), 400

    context = data.get("context","")
    mood = data.get("mood","")
    turns = min(int(data.get("turns", 4)), 10)

    # 인스턴스 조회/생성
    inst_a = _get_or_create_instance(game["id"], pid, npc_a_id)
    inst_b = _get_or_create_instance(game["id"], pid, npc_b_id)
    if not inst_a or not inst_b:
        return jsonify({"error": "NPC template not found"}), 404

    conn = _get_game_db()
    tpl_a = dict(conn.execute("SELECT * FROM npc_templates WHERE id=?", (npc_a_id,)).fetchone())
    tpl_b = dict(conn.execute("SELECT * FROM npc_templates WHERE id=?", (npc_b_id,)).fetchone())

    # 대화 프롬프트 구성
    def npc_desc(tpl, inst):
        p = tpl.get("personality","")
        try: p = json.loads(p) if isinstance(p,str) else p
        except Exception: p = {}
        return f"{tpl['name']} (Lv.{inst['level']} {tpl['class']}), \uc131\uaca9: {p.get('traits','')}, \ub9d0\ud22c: {p.get('speech_style','')}"

    context_str = context or "일상 대화"
    mood_str = mood or "자연스러운"
    prompt = f"""\ub450 NPC\uc758 \ub300\ud654\ub97c {turns}\ud134 \uc0dd\uc131\ud558\uc138\uc694. \ubc18\ub4dc\uc2dc \ud55c\uad6d\uc5b4.

[NPC A] {npc_desc(tpl_a, inst_a)}
\ubc30\uacbd: {tpl_a.get('background','')}

[NPC B] {npc_desc(tpl_b, inst_b)}
\ubc30\uacbd: {tpl_b.get('background','')}

[\uc0c1\ud669] {context_str}
[\ubd84\uc704\uae30] {mood_str}

\uac01 \ud134\uc744 JSON \ubc30\uc5f4\ub85c \ucd9c\ub825:
[
  {{"speaker": "A", "name": "{tpl_a['name']}", "text": "\ub300\uc0ac", "emotion": "\uac10\uc815", "action": "*\ud589\ub3d9*"}},
  {{"speaker": "B", "name": "{tpl_b['name']}", "text": "\ub300\uc0ac", "emotion": "\uac10\uc815", "action": "*\ud589\ub3d9*"}}
]
JSON\ub9cc \ucd9c\ub825."""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.npc_dialogue", prompt, model=model,
            options={"temperature": 0.8, "num_predict": 2000}, timeout=120, adapter=_router_adapter())
        text = _router_response(res)
        import re
        m = re.search(r'\[\s*\{[\s\S]*\}\s*\]', text)
        if m:
            dlg_turns = json.loads(m.group())
            # 대사 텍스트 한국어 필터링 (v2.62.1)
            from core.korean import force_korean as _fk_game
            for turn in dlg_turns:
                if turn.get("text"):
                    turn["text"] = _fk_game(turn["text"])
        else:
            dlg_turns = [{"speaker":"A","name":tpl_a["name"],"text":text[:200],"emotion":"","action":""}]
    except Exception as e:
        return jsonify({"error": "LLM 처리 중 오류"}), 500

    # EXP 계산 (턴 수 기반 간단 공식)
    exp_a = len(dlg_turns) * 5 + (10 if context else 0)
    exp_b = len(dlg_turns) * 5 + (10 if context else 0)

    # 인스턴스 EXP 업데이트 + 레벨업 체크
    level_ups = []
    for inst, exp_gain, tpl in [(inst_a, exp_a, tpl_a), (inst_b, exp_b, tpl_b)]:
        new_exp = inst["exp"] + exp_gain
        new_level = inst["level"]
        while new_exp >= _exp_for_level(new_level + 1) and new_level < 99:
            new_level += 1
            level_ups.append({"npc": tpl["name"], "old_level": inst["level"], "new_level": new_level})
        conn.execute("UPDATE npc_instances SET exp=?, level=?, updated_at=datetime('now') WHERE id=?",
                     (new_exp, new_level, inst["id"]))

    # 대화 저장
    import uuid as _u
    dlg_id = str(_u.uuid4())
    conn.execute("INSERT INTO dialogues (id,game_id,player_id,participants,context,turns,exp_awarded,quality_score,created_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                 (dlg_id, game["id"], pid, json.dumps([npc_a_id, npc_b_id]),
                  context, json.dumps(dlg_turns, ensure_ascii=False),
                  json.dumps({inst_a["id"]: exp_a, inst_b["id"]: exp_b}), len(dlg_turns) * 2.0))

    # 성장 로그
    for inst, exp_gain in [(inst_a, exp_a), (inst_b, exp_b)]:
        conn.execute("INSERT INTO growth_log (instance_id,player_id,event_type,exp_gained,level_before,level_after,details,created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
                     (inst["id"], pid, "dialogue", exp_gain, inst["level"], inst["level"], json.dumps({"dialogue_id": dlg_id})))
    conn.commit()

    return jsonify({
        "dialogue_id": dlg_id,
        "player_id": pid,
        "turns": dlg_turns,
        "exp_earned": {tpl_a["name"]: exp_a, tpl_b["name"]: exp_b},
        "level_ups": level_ups,
    })


@game_bp.route("/api/game/dialogue/player", methods=["POST"])
def game_dialogue_player():
    """\ud50c\ub808\uc774\uc5b4 \u2194 NPC \ub300\ud654
    ---
    tags: [Game Dialogue]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, npc_id, message]
          properties:
            player_id: {type: string}
            npc_id: {type: string, description: "NPC \ud15c\ud50c\ub9bf ID"}
            message: {type: string, description: "\ud50c\ub808\uc774\uc5b4 \uba54\uc2dc\uc9c0"}
            player_name: {type: string, default: "\ubaa8\ud5d8\uac00"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id, npc_id, message required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id","")
    npc_id = data.get("npc_id","")
    message = data.get("message","").strip()
    if not pid or not npc_id or not message:
        return jsonify({"error": "player_id, npc_id, message required"}), 400

    inst = _get_or_create_instance(game["id"], pid, npc_id)
    if not inst: return jsonify({"error": "NPC not found"}), 404

    conn = _get_game_db()
    tpl = dict(conn.execute("SELECT * FROM npc_templates WHERE id=?", (npc_id,)).fetchone())

    p = tpl.get("personality","")
    try: p = json.loads(p) if isinstance(p,str) else p
    except Exception: p = {}

    system = GAME_NPC_PROMPT.format(
        name=tpl["name"], npc_class=tpl["class"], level=inst["level"],
        personality=p.get("traits",""), background=tpl.get("background",""),
        stats=inst.get("stats",""), skills=inst.get("skills",""),
        relationships=inst.get("relationships","{}"), memories="", world="")

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        response, _meta = _llm_generate_korean(
            model=model, prompt=message, system=system,
            options={"temperature": 0.8}, timeout=60)
    except Exception as e:
        return internal_error(e)

    # EXP
    exp = 8
    new_exp = inst["exp"] + exp
    new_level = inst["level"]
    level_up = None
    if new_exp >= _exp_for_level(new_level + 1) and new_level < 99:
        new_level += 1
        level_up = {"old": inst["level"], "new": new_level}
    conn.execute("UPDATE npc_instances SET exp=?, level=?, updated_at=datetime('now') WHERE id=?",
                 (new_exp, new_level, inst["id"]))
    conn.commit()

    return jsonify({
        "npc": tpl["name"],
        "level": new_level,
        "response": response,
        "exp_earned": exp,
        "total_exp": new_exp,
        "level_up": level_up,
    })


@game_bp.route("/api/game/dialogue/history")
def game_dialogue_history():
    """\ub300\ud654 \uc774\ub825
    ---
    tags: [Game Dialogue]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
      - in: query
        name: limit
        type: integer
        default: 20
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    pid = request.args.get("player_id","")
    limit = int(request.args.get("limit","20"))
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM dialogues WHERE game_id=? AND player_id=? ORDER BY created_at DESC LIMIT ?",
                        (game["id"], pid, limit)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        for f in ("participants","turns","exp_awarded"):
            if d.get(f):
                try: d[f] = json.loads(d[f])
                except Exception: pass
        results.append(d)
    return jsonify({"dialogues": results})


# === Growth API ===

@game_bp.route("/api/game/growth/status")
def game_growth_status():
    """NPC \uc131\uc7a5 \ud604\ud669
    ---
    tags: [Game Growth]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
      - in: query
        name: npc_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    pid = request.args.get("player_id","")
    npc_id = request.args.get("npc_id","")
    inst = _get_or_create_instance(game["id"], pid, npc_id)
    if not inst: return jsonify({"error": "NPC not found"}), 404
    conn = _get_game_db()
    tpl = dict(conn.execute("SELECT name,class FROM npc_templates WHERE id=?", (npc_id,)).fetchone())
    logs = conn.execute("SELECT * FROM growth_log WHERE instance_id=? ORDER BY created_at DESC LIMIT 10",
                        (inst["id"],)).fetchall()
    return jsonify({
        "npc": tpl["name"],
        "class": tpl["class"],
        "level": inst["level"],
        "exp": inst["exp"],
        "next_level_exp": _exp_for_level(inst["level"] + 1),
        "exp_remaining": max(0, _exp_for_level(inst["level"] + 1) - inst["exp"]),
        "recent_events": [dict(l) for l in logs],
    })




# === Phase 2: Group Dialogue + Skills + Level-up ===

@game_bp.route("/api/game/dialogue/group", methods=["POST"])
def game_dialogue_group():
    """다자 대화 (2~6명 파티)
    ---
    tags: [Game Dialogue]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, participants]
          properties:
            player_id: {type: string}
            participants: {type: array, items: {type: string}}
            context: {type: string}
            mood: {type: string}
            leader: {type: string}
            turns: {type: integer, default: 6}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "max 6"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id", "")
    parts = data.get("participants", [])
    if not pid or len(parts) < 2: return jsonify({"error": "player_id and 2+ participants required"}), 400
    if len(parts) > 6: return jsonify({"error": "max 6"}), 400
    context = data.get("context", "")
    mood = data.get("mood", "")
    turns = min(int(data.get("turns", 6)), 12)
    conn = _get_game_db()
    npcs = []
    for tid in parts:
        inst = _get_or_create_instance(game["id"], pid, tid)
        tpl = conn.execute("SELECT * FROM npc_templates WHERE id=?", (tid,)).fetchone()
        if inst and tpl:
            tpl = dict(tpl)
            p = tpl.get("personality", "")
            try: p = json.loads(p) if isinstance(p, str) else p
            except Exception: p = {}
            npcs.append({"tid": tid, "inst": inst, "tpl": tpl, "p": p})
    if len(npcs) < 2: return jsonify({"error": "NPC not found"}), 404
    npc_descs = "\n".join([f"- {n['tpl']['name']} (Lv.{n['inst']['level']} {n['tpl']['class']}): {n['p'].get('traits','')}, {n['p'].get('speech_style','')}" for n in npcs])
    prompt = f"다자 대화 {turns}턴. 한국어.\n[참가자]\n{npc_descs}\n[상황] {context or '회의'}\n[분위기] {mood or '자연스러운'}\nJSON 배열: [{{\"speaker\": \"이름\", \"text\": \"대사\", \"emotion\": \"감정\", \"action\": \"*행동*\"}}]\nJSON만."
    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.npc_dialogue", prompt, model=model,
            options={"temperature": 0.85, "num_predict": 3000}, timeout=120, adapter=_router_adapter())
        text = _router_response(res)
        import re as _re
        m = _re.search(r'\[\s*\{[\s\S]*\}\s*\]', text)
        dlg_turns = json.loads(m.group()) if m else [{"speaker": npcs[0]["tpl"]["name"], "text": text[:200]}]
    except Exception as e:
        return internal_error(e)
    exp_per = len(dlg_turns) * 4 + (15 if context else 0)
    exp_map = {}
    level_ups = []
    for n in npcs:
        new_exp = n["inst"]["exp"] + exp_per
        new_level = n["inst"]["level"]
        while new_exp >= _exp_for_level(new_level + 1) and new_level < 99:
            new_level += 1
            level_ups.append({"npc": n["tpl"]["name"], "old": n["inst"]["level"], "new": new_level})
        conn.execute("UPDATE npc_instances SET exp=?, level=?, updated_at=datetime('now') WHERE id=?", (new_exp, new_level, n["inst"]["id"]))
        exp_map[n["tpl"]["name"]] = exp_per
    import uuid as _u2
    dlg_id = str(_u2.uuid4())
    conn.execute("INSERT INTO dialogues (id,game_id,player_id,participants,context,turns,exp_awarded,quality_score,created_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                 (dlg_id, game["id"], pid, json.dumps(parts), context, json.dumps(dlg_turns, ensure_ascii=False), json.dumps(exp_map, ensure_ascii=False), len(dlg_turns) * 2.5))
    conn.commit()
    return jsonify({"dialogue_id": dlg_id, "participants": [n["tpl"]["name"] for n in npcs], "turns": dlg_turns, "exp_earned": exp_map, "level_ups": level_ups})


@game_bp.route("/api/game/growth/level-up", methods=["POST"])
def game_growth_levelup():
    """레벨업 (스탯/스킬 자동)
    ---
    tags: [Game Growth]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, npc_id]
          properties:
            player_id: {type: string}
            npc_id: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid, npc_id = data.get("player_id",""), data.get("npc_id","")
    inst = _get_or_create_instance(game["id"], pid, npc_id)
    if not inst: return jsonify({"error": "NPC not found"}), 404
    needed = _exp_for_level(inst["level"] + 1)
    if inst["exp"] < needed: return jsonify({"error": f"EXP insufficient ({inst['exp']}/{needed})"}), 400
    conn = _get_game_db()
    tpl = dict(conn.execute("SELECT * FROM npc_templates WHERE id=?", (npc_id,)).fetchone())
    old_level, new_level = inst["level"], inst["level"] + 1
    # LLM skill gen
    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.levelup_skill", f"레벨업! {tpl['name']}({tpl['class']}) Lv.{old_level}->{new_level}. 새 스킬 1개를 JSON으로 생성. skill_name과 description 포함. JSON만 출력.", model=model,
            options={"temperature": 0.8, "num_predict": 200}, timeout=60, adapter=_router_adapter())
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', _router_response(res))
        new_skill = json.loads(m.group()) if m else {"skill_name": f"Lv.{new_level} skill"}
    except Exception: new_skill = {"skill_name": f"Lv.{new_level} skill"}
    skills = json.loads(inst.get("skills","[]")) if isinstance(inst.get("skills"),str) else (inst.get("skills") or [])
    skills.append(new_skill.get("skill_name",""))
    stats = json.loads(inst.get("stats","{}")) if isinstance(inst.get("stats"),str) else (inst.get("stats") or {})
    for k in ["strength","intelligence","charisma","wisdom"]:
        stats[k] = stats.get(k, 50) + 3
    conn.execute("UPDATE npc_instances SET level=?, skills=?, stats=?, updated_at=datetime('now') WHERE id=?", (new_level, json.dumps(skills,ensure_ascii=False), json.dumps(stats), inst["id"]))
    conn.execute("INSERT INTO growth_log (instance_id,player_id,event_type,exp_gained,level_before,level_after,details,created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))", (inst["id"], pid, "level_up", 0, old_level, new_level, json.dumps({"new_skill": new_skill},ensure_ascii=False)))
    conn.commit()
    return jsonify({"npc": tpl["name"], "old_level": old_level, "new_level": new_level, "new_skill": new_skill, "stats": stats, "skills": skills})


# === Phase 3: Scenario ===

@game_bp.route("/api/game/scenario/generate", methods=["POST"])
def game_scenario_generate():
    """시나리오/퀘스트 자동 생성
    ---
    tags: [Game Scenario]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id]
          properties:
            player_id: {type: string}
            type: {type: string, enum: [quest, event, side], default: quest}
            theme: {type: string}
            difficulty: {type: string, enum: [easy, normal, hard], default: normal}
            participants: {type: array, items: {type: string}}
            branch_count: {type: integer, default: 3}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id","")
    if not pid: return jsonify({"error": "player_id required"}), 400
    stype = data.get("type","quest")
    theme = data.get("theme","")
    diff = data.get("difficulty","normal")
    branches = min(int(data.get("branch_count",3)),5)
    conn = _get_game_db()
    npc_names = []
    for tid in data.get("participants",[]):
        tpl = conn.execute("SELECT name,class FROM npc_templates WHERE id=?", (tid,)).fetchone()
        if tpl: npc_names.append(f"{tpl['name']}({tpl['class']})")
    prompt = f"게임 시나리오 JSON. 한국어. 유형:{stype}, 난이도:{diff}, 테마:{theme or '자유'}, NPC:{','.join(npc_names) or '자유'}, 분기:{branches}개\n{{\n\"title\": \"제목\",\n\"synopsis\": \"줄거리\",\n\"objectives\": [{{\"id\":1,\"text\":\"목표\",\"reward_exp\":50}}],\n\"branches\": [{{\"id\":\"A\",\"choice\":\"선택\",\"consequence\":\"결과\",\"risk\":\"medium\"}}],\n\"rewards\": {{\"exp\":200,\"items\":[\"아이템\"]}}\n}}\nJSON만."
    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.scenario", prompt, model=model,
            options={"temperature": 0.85, "num_predict": 2000}, timeout=90, adapter=_router_adapter())
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', _router_response(res))
        scenario = json.loads(m.group()) if m else {"title": "생성 실패"}
    except Exception as e:
        return internal_error(e)
    import uuid as _u3
    sid = str(_u3.uuid4())
    conn.execute("INSERT INTO scenarios (id,game_id,player_id,type,title,content,created_at) VALUES (?,?,?,?,?,?,datetime('now'))", (sid, game["id"], pid, stype, scenario.get("title",""), json.dumps(scenario,ensure_ascii=False)))
    conn.commit()
    return jsonify({"scenario_id": sid, **scenario})


@game_bp.route("/api/game/scenarios")
def game_scenarios_list():
    """시나리오 목록
    ---
    tags: [Game Scenario]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    pid = request.args.get("player_id","")
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM scenarios WHERE game_id=? AND player_id=? ORDER BY created_at DESC LIMIT 20", (game["id"],pid)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        if d.get("content"):
            try: d["content"] = json.loads(d["content"])
            except Exception: pass
        results.append(d)
    return jsonify({"scenarios": results})


# === Phase 4: Illustrate ===

@game_bp.route("/api/game/illustrate/character", methods=["POST"])
def game_illustrate_character():
    """NPC 캐릭터 일러스트
    ---
    tags: [Game Illustrate]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [npc_id]
          properties:
            npc_id: {type: string}
            pose: {type: string, default: portrait}
            expression: {type: string}
            style: {type: string, default: anime}
            model: {type: string, default: animagine}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "npc_id required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    npc_id = data.get("npc_id","")
    if not npc_id: return jsonify({"error": "npc_id required"}), 400
    conn = _get_game_db()
    tpl = conn.execute("SELECT * FROM npc_templates WHERE id=? AND game_id=?", (npc_id, game["id"])).fetchone()
    if not tpl: return jsonify({"error": "NPC not found"}), 404
    tpl = dict(tpl)
    appearance = tpl.get("appearance","")
    try: appearance = json.loads(appearance) if isinstance(appearance,str) else appearance
    except Exception: appearance = {}
    desc = appearance.get("description","")
    character_id = appearance.get("character_id","")
    pose = data.get("pose","portrait")
    expression = data.get("expression","")
    # LLM translate to English prompt
    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.image_prompt", f"Translate to English image prompt: {tpl['name']}, {tpl['class']}, {desc}, {pose}, {expression}. Output only the prompt.", model=_load_config().get("default_model","qwen2.5:14b"),
            options={"num_predict": 200}, timeout=60, adapter=_router_adapter())
        eng = _clean_english_prompt(_router_response(res, f"{desc}, {pose}"))
    except Exception: eng = f"fantasy character, {pose}, {desc}"
    img_body = {"prompt": eng, "model": data.get("model","animagine"), "style": data.get("style","anime"), "width": int(data.get("width",768)), "height": int(data.get("height",1024))}
    if character_id:
        img_body["character_id"] = character_id
        img_body["ip_adapter_strength"] = float(data.get("ip_adapter_strength",0.7))
    try:
        r = requests.post(f"{_get_self_base()}/api/image/generate", json=img_body, timeout=10)
        result = r.json()
    except Exception as e: return internal_error(e)
    return jsonify({"npc": tpl["name"], "prompt_used": eng, **result})


@game_bp.route("/api/game/illustrate/scene", methods=["POST"])
def game_illustrate_scene():
    """장면 일러스트
    ---
    tags: [Game Illustrate]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [description]
          properties:
            description: {type: string}
            style: {type: string, default: concept-art}
            model: {type: string, default: dreamshaper}
            mood: {type: string}
            width: {type: integer, default: 1280}
            height: {type: integer, default: 720}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "description required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    desc = data.get("description","")
    if not desc: return jsonify({"error": "description required"}), 400
    mood = data.get("mood","")
    try:
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.image_prompt", f"Translate to English image prompt: {desc}. {f'Mood: {mood}.' if mood else ''} Output only prompt.", model=_load_config().get("default_model","qwen2.5:14b"),
            options={"num_predict": 150}, timeout=60, adapter=_router_adapter())
        eng = _clean_english_prompt(_router_response(res, desc))
    except Exception: eng = desc
    try:
        r = requests.post(f"{_get_self_base()}/api/image/generate", json={"prompt": eng, "model": data.get("model","dreamshaper"), "style": data.get("style","concept-art"), "width": int(data.get("width",1280)), "height": int(data.get("height",720))}, timeout=10)
        result = r.json()
    except Exception as e: return internal_error(e)
    return jsonify({"prompt_used": eng, **result})


# === World Setting (RAG) ===

@game_bp.route("/api/game/world/setting", methods=["POST"])
def game_world_setting():
    """세계관 설정 등록
    ---
    tags: [Game World]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [title, content]
          properties:
            title: {type: string}
            content: {type: string}
            category: {type: string, enum: [geography, history, faction, race, magic, item]}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "title and content required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    title = data.get("title","").strip()
    text = data.get("content","").strip()
    if not title or not text: return jsonify({"error": "title and content required"}), 400
    import uuid as _u4
    wid = str(_u4.uuid4())
    cat = data.get("category","history")
    conn = _get_game_db()
    conn.execute("INSERT INTO world_settings (id,game_id,category,title,content,created_at) VALUES (?,?,?,?,?,datetime('now'))", (wid, game["id"], cat, title, text))
    rag_id = f"world_{game['id']}_{wid[:8]}"
    try:
        r = requests.post(f"{_get_self_base()}/api/rag/index", json={"id": rag_id, "text": f"[World: {title}]\n{text}", "metadata": {"game_id": game["id"], "type": "world", "category": cat}}, timeout=60)
        if r.status_code == 200: conn.execute("UPDATE world_settings SET indexed=1 WHERE id=?", (wid,))
    except Exception: pass
    conn.commit()
    return jsonify({"world_id": wid, "title": title, "indexed": True})


@game_bp.route("/api/game/world/search", methods=["POST"])
def game_world_search():
    """세계관 검색
    ---
    tags: [Game World]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string}
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    q = (request.json or {}).get("query","")
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM world_settings WHERE game_id=? AND content LIKE ? ESCAPE '\\' LIMIT 10", (game["id"], f"%{_escape_like(q)}%")).fetchall()
    return jsonify({"results": [dict(r) for r in rows]})




# === Shared NPC — 사용자 간 영향 전파 + 추억 시스템 ===

MOOD_LABELS = {
    (0, 20): "분노", (20, 35): "불쾌", (35, 45): "우울",
    (45, 55): "평온", (55, 65): "기분좋음", (65, 80): "행복", (80, 101): "감격"
}

def _get_mood_label(score):
    for (lo, hi), label in MOOD_LABELS.items():
        if lo <= score < hi:
            return label
    return "평온"

REPUTATION_TITLES = {
    (-100, -60): "원수", (-60, -30): "적대자", (-30, -10): "경계대상",
    (-10, 10): "낯선이", (10, 30): "지인", (30, 50): "동료",
    (50, 70): "친구", (70, 85): "절친", (85, 101): "영혼의 동반자"
}

def _get_rep_title(rep):
    for (lo, hi), title in REPUTATION_TITLES.items():
        if lo <= rep < hi:
            return title
    return "낯선이"


@game_bp.route("/api/game/shared/designate", methods=["POST"])
def game_shared_designate():
    """NPC를 공유 NPC로 지정
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [npc_id]
          properties:
            npc_id: {type: string, description: "NPC 템플릿 ID"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "npc_id required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    npc_id = data.get("npc_id", "")
    if not npc_id: return jsonify({"error": "npc_id required"}), 400
    conn = _get_game_db()
    tpl = conn.execute("SELECT name FROM npc_templates WHERE id=? AND game_id=?", (npc_id, game["id"])).fetchone()
    if not tpl: return jsonify({"error": "NPC not found"}), 404
    conn.execute("UPDATE npc_templates SET is_shared=1 WHERE id=?", (npc_id,))
    conn.execute("""INSERT OR IGNORE INTO shared_npc_state (npc_id, game_id, mood, mood_score, updated_at)
        VALUES (?, ?, 'neutral', 50, datetime('now'))""", (npc_id, game["id"]))
    conn.commit()
    return jsonify({"npc_id": npc_id, "name": tpl["name"], "is_shared": True})


@game_bp.route("/api/game/shared/interact", methods=["POST"])
def game_shared_interact():
    """공유 NPC와 상호작용 (대화 + 영향 + 추억 생성)
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, npc_id, message]
          properties:
            player_id: {type: string}
            npc_id: {type: string}
            message: {type: string}
            action_type: {type: string, enum: [talk, gift, help, betray, attack], default: talk}
            player_name: {type: string, default: "모험가"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id, npc_id, message required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "Shared NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id", "")
    npc_id = data.get("npc_id", "")
    message = data.get("message", "").strip()
    action_type = data.get("action_type", "talk")
    player_name = data.get("player_name", "모험가")
    if not pid or not npc_id or not message:
        return jsonify({"error": "player_id, npc_id, message required"}), 400

    conn = _get_game_db()
    tpl = conn.execute("SELECT * FROM npc_templates WHERE id=? AND game_id=? AND is_shared=1", (npc_id, game["id"])).fetchone()
    if not tpl: return jsonify({"error": "Shared NPC not found"}), 404
    tpl = dict(tpl)

    # 공유 상태 조회
    shared = conn.execute("SELECT * FROM shared_npc_state WHERE npc_id=?", (npc_id,)).fetchone()
    shared = dict(shared) if shared else {"mood": "neutral", "mood_score": 50, "total_interactions": 0}

    # 평판 조회/생성
    rep = conn.execute("SELECT * FROM player_reputation WHERE npc_id=? AND player_id=?", (npc_id, pid)).fetchone()
    if rep:
        rep = dict(rep)
    else:
        conn.execute("INSERT INTO player_reputation (npc_id, game_id, player_id) VALUES (?,?,?)", (npc_id, game["id"], pid))
        conn.commit()
        rep = {"reputation": 50, "interactions": 0, "title": ""}

    # 이 플레이어에 대한 추억 조회 (최근 5개)
    memories = conn.execute(
        "SELECT content, sentiment, intensity FROM npc_memories WHERE npc_id=? AND player_id=? ORDER BY created_at DESC LIMIT 5",
        (npc_id, pid)).fetchall()
    memory_text = "\n".join([f"- [{m['sentiment']}] {m['content']}" for m in memories]) if memories else "첫 만남"

    # 다른 플레이어의 최근 영향 (최근 10개)
    recent_influences = conn.execute(
        "SELECT source_player, influence_type, description FROM influence_log WHERE npc_id=? AND source_player!=? ORDER BY created_at DESC LIMIT 5",
        (npc_id, pid)).fetchall()
    influence_text = "\n".join([f"- {i['source_player']}이(가) {i['description']}" for i in recent_influences]) if recent_influences else "없음"

    # 시스템 프롬프트 구성
    p = tpl.get("personality", "")
    try: p = json.loads(p) if isinstance(p, str) else p
    except Exception: p = {}

    system = f"""당신은 {tpl['name']}입니다. 반드시 한국어로 답변하세요.
클래스: {tpl['class']}
성격: {p.get('traits', '')}
말투: {p.get('speech_style', '')}
배경: {tpl.get('background', '')}

[현재 기분] {_get_mood_label(shared['mood_score'])} (점수: {shared['mood_score']}/100)
[총 만남 횟수] {shared['total_interactions']}회

[{player_name}에 대한 기억]
{memory_text}

[{player_name}과의 관계] {_get_rep_title(rep['reputation'])} (호감도: {rep['reputation']})
만남 횟수: {rep['interactions']}회

[최근 다른 모험가들의 영향]
{influence_text}

[규칙]
1. 현재 기분과 {player_name}에 대한 기억/관계에 따라 태도를 조절하세요
2. 좋은 기억이 많으면 친근하게, 나쁜 기억이 있으면 경계하세요
3. 다른 모험가들의 영향으로 기분이 변했다면 자연스럽게 언급하세요
4. 감정과 행동을 *별표*로 묘사하세요
5. 이 대화가 좋은 기억인지 나쁜 기억인지 판단할 수 있는 내용을 포함하세요
"""

    # LLM 응답 생성
    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.npc_interact", message, model=model, system=system,
            options={"temperature": 0.8}, timeout=60, adapter=_router_adapter())
        response = _router_response(res)
    except Exception as e:
        return internal_error(e)

    # 감정 분석 (간단 키워드 기반)
    positive_kw = ["감사", "고마", "기뻐", "좋아", "사랑", "축하", "도와", "선물", "용감", "멋"]
    negative_kw = ["화나", "실망", "배신", "공격", "거짓", "미워", "짜증", "무시", "위협"]
    pos_count = sum(1 for kw in positive_kw if kw in message or kw in response)
    neg_count = sum(1 for kw in negative_kw if kw in message or kw in response)

    # 행동 타입별 영향
    action_effects = {
        "talk": {"mood": 1, "rep": 2},
        "gift": {"mood": 5, "rep": 10},
        "help": {"mood": 8, "rep": 15},
        "betray": {"mood": -15, "rep": -30},
        "attack": {"mood": -20, "rep": -50},
    }
    effect = action_effects.get(action_type, {"mood": 1, "rep": 2})

    # 감정에 따른 보정
    sentiment = "positive" if pos_count > neg_count else ("negative" if neg_count > pos_count else "neutral")
    if sentiment == "positive":
        mood_change = effect["mood"] + pos_count * 2
        rep_change = effect["rep"] + pos_count * 3
    elif sentiment == "negative":
        mood_change = effect["mood"] - neg_count * 3
        rep_change = effect["rep"] - neg_count * 5
    else:
        mood_change = effect["mood"]
        rep_change = effect["rep"]

    # 글로벌 상태 업데이트
    new_mood = max(0, min(100, shared["mood_score"] + mood_change))
    new_rep = max(-100, min(100, rep["reputation"] + rep_change))
    new_title = _get_rep_title(new_rep)

    conn.execute("UPDATE shared_npc_state SET mood_score=?, mood=?, total_interactions=total_interactions+1, updated_at=datetime('now') WHERE npc_id=?",
                 (new_mood, _get_mood_label(new_mood), npc_id))

    conn.execute("""INSERT OR REPLACE INTO player_reputation (npc_id, game_id, player_id, reputation, title, interactions, last_interaction)
        VALUES (?,?,?,?,?,COALESCE((SELECT interactions FROM player_reputation WHERE npc_id=? AND player_id=?),0)+1,datetime('now'))""",
        (npc_id, game["id"], pid, new_rep, new_title, npc_id, pid))

    # 추억 저장
    memory_content = f"{player_name}이(가) {action_type}: {message[:100]}"
    intensity = max(1, min(10, abs(mood_change)))
    conn.execute("INSERT INTO npc_memories (npc_id, game_id, player_id, memory_type, content, sentiment, intensity, created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
                 (npc_id, game["id"], pid, action_type, memory_content, sentiment, intensity))

    # 영향 전파 로그
    conn.execute("INSERT INTO influence_log (npc_id, game_id, source_player, influence_type, description, mood_change, reputation_change, created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
                 (npc_id, game["id"], player_name, action_type,
                  f"{action_type} — 기분 {'+' if mood_change >= 0 else ''}{mood_change}", mood_change, rep_change))
    conn.commit()

    return jsonify({
        "npc": tpl["name"],
        "response": response,
        "mood": {"before": shared["mood_score"], "after": new_mood, "label": _get_mood_label(new_mood), "change": mood_change},
        "reputation": {"before": rep["reputation"], "after": new_rep, "title": new_title, "change": rep_change},
        "sentiment": sentiment,
        "memory_saved": memory_content,
        "action_type": action_type,
    })


@game_bp.route("/api/game/shared/status")
def game_shared_status():
    """공유 NPC 글로벌 상태
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: npc_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "Shared NPC not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    npc_id = request.args.get("npc_id", "")
    conn = _get_game_db()
    tpl = conn.execute("SELECT name, class FROM npc_templates WHERE id=?", (npc_id,)).fetchone()
    shared = conn.execute("SELECT * FROM shared_npc_state WHERE npc_id=?", (npc_id,)).fetchone()
    if not shared: return jsonify({"error": "Shared NPC not found"}), 404
    shared = dict(shared)
    # 상위 평판 플레이어
    top_rep = conn.execute("SELECT player_id, reputation, title, interactions FROM player_reputation WHERE npc_id=? ORDER BY reputation DESC LIMIT 5", (npc_id,)).fetchall()
    # 최근 영향
    recent = conn.execute("SELECT source_player, influence_type, description, mood_change, created_at FROM influence_log WHERE npc_id=? ORDER BY created_at DESC LIMIT 10", (npc_id,)).fetchall()
    return jsonify({
        "npc": dict(tpl) if tpl else {},
        "mood": _get_mood_label(shared["mood_score"]),
        "mood_score": shared["mood_score"],
        "total_interactions": shared["total_interactions"],
        "top_players": [dict(r) for r in top_rep],
        "recent_influences": [dict(r) for r in recent],
    })


@game_bp.route("/api/game/shared/memories")
def game_shared_memories():
    """NPC의 특정 플레이어에 대한 추억
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: npc_id
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    npc_id = request.args.get("npc_id", "")
    pid = request.args.get("player_id", "")
    conn = _get_game_db()
    tpl = conn.execute("SELECT name FROM npc_templates WHERE id=?", (npc_id,)).fetchone()
    memories = conn.execute("SELECT * FROM npc_memories WHERE npc_id=? AND player_id=? ORDER BY created_at DESC LIMIT 20", (npc_id, pid)).fetchall()
    rep = conn.execute("SELECT * FROM player_reputation WHERE npc_id=? AND player_id=?", (npc_id, pid)).fetchone()
    good = [dict(m) for m in memories if m["sentiment"] == "positive"]
    bad = [dict(m) for m in memories if m["sentiment"] == "negative"]
    neutral = [dict(m) for m in memories if m["sentiment"] == "neutral"]
    return jsonify({
        "npc": tpl["name"] if tpl else "",
        "player_id": pid,
        "reputation": dict(rep) if rep else {"reputation": 50},
        "memories": {"good": good, "bad": bad, "neutral": neutral, "total": len(memories)},
    })


@game_bp.route("/api/game/shared/reputation")
def game_shared_reputation():
    """플레이어의 전체 공유 NPC 평판
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    pid = request.args.get("player_id", "")
    conn = _get_game_db()
    rows = conn.execute("""SELECT r.*, t.name, t.class FROM player_reputation r
        JOIN npc_templates t ON r.npc_id = t.id
        WHERE r.game_id=? AND r.player_id=? ORDER BY r.reputation DESC""", (game["id"], pid)).fetchall()
    return jsonify({"player_id": pid, "reputations": [dict(r) for r in rows]})


@game_bp.route("/api/game/shared/influences")
def game_shared_influences():
    """영향 전파 로그
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: npc_id
        type: string
        required: true
      - in: query
        name: limit
        type: integer
        default: 20
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    npc_id = request.args.get("npc_id", "")
    limit = int(request.args.get("limit", "20"))
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM influence_log WHERE npc_id=? ORDER BY created_at DESC LIMIT ?", (npc_id, limit)).fetchall()
    return jsonify({"influences": [dict(r) for r in rows]})


@game_bp.route("/api/game/shared/npcs")
def game_shared_npcs():
    """공유 NPC 목록
    ---
    tags: [Game Shared NPC]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    rows = conn.execute("""SELECT t.id, t.name, t.class, s.mood, s.mood_score, s.total_interactions
        FROM npc_templates t LEFT JOIN shared_npc_state s ON t.id = s.npc_id
        WHERE t.game_id=? AND t.is_shared=1""", (game["id"],)).fetchall()
    return jsonify({"shared_npcs": [dict(r) for r in rows]})




# === Heroine NPC — 연애 시뮬레이션 ===

AFFINITY_STAGES = [
    (-100, -30, "enemy", "원수"),
    (-30, 0, "dislike", "싫어함"),
    (0, 20, "stranger", "낯선 사람"),
    (20, 40, "acquaintance", "아는 사이"),
    (40, 60, "friend", "친구"),
    (60, 75, "crush", "설렘"),
    (75, 90, "lover", "연인"),
    (90, 101, "soulmate", "운명의 상대"),
]

def _get_affinity_stage(affinity):
    for lo, hi, code, label in AFFINITY_STAGES:
        if lo <= affinity < hi:
            return code, label
    return "stranger", "낯선 사람"



import re as _re_filter

def _clean_english_prompt(text):
    """Image prompt sanitizer — keep only English/numbers/punctuation"""
    import re
    if not text:
        return text
    text = re.sub(r'[가-힣ㄱ-ㅎㅏ-ㅣ]+', '', text)
    text = re.sub(r'[\u3040-\u309F\u30A0-\u30FF]+', '', text)
    text = re.sub(r'[\u4E00-\u9FFF]+', '', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    text = re.sub(r'[${}]', '', text)
    text = re.sub(r'[*_~`]+', '', text)
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r'\s+', ' ', text).strip()
    return text
HEROINE_SYSTEM_PROMPT = """당신은 {name}입니다. 반드시 한국어로만 답변하세요. 절대로 일본어, 중국어, 영어 등 다른 언어를 사용하지 마세요. 행동 묘사도 반드시 한국어로 작성하세요.
나이: {age}세
성격: {personality}
외모: {appearance}
배경: {background}
좋아하는 것: {likes}
싫어하는 것: {dislikes}
이상형: {ideal_type}
말투: {voice_style}

[{player_name}과의 관계]
호감도: {affinity} ({stage_label})
신뢰도: {trust}
만남 횟수: {interactions}회
데이트: {date_count}회

[최근 대화 기억]
{recent_chat}

[규칙]
1. {name}의 성격과 말투를 완벽하게 유지하세요
2. 현재 호감도 단계에 맞는 반응을 하세요:
   - 낯선 사람(0~20): 경계, 예의바른 거리감
   - 아는 사이(20~40): 가끔 웃음, 기본적 호의
   - 친구(40~60): 편안한 대화, 장난, 고민 상담
   - 설렘(60~75): 얼굴 붉힘, 두근거림, 은근한 관심
   - 연인(75~90): 애정 표현, 스킨십 묘사, 질투
   - 운명의 상대(90+): 깊은 사랑, 헌신, 미래 이야기
3. 감정과 행동은 *별표*로, 내면 독백은 (괄호)로 표현
4. 질투/신뢰도에 따라 태도 변화
5. 절대 호감도 수치를 직접 언급하지 마세요
6. 모든 대사, 행동 묘사, 내면 독백을 100% 한국어로만 작성하세요. 일본어(ひらがな/カタカナ/漢字), 중국어, 영어 단어를 절대 섞지 마세요
7. *행동묘사*도 반드시 한국어로 (예: *얼굴을 붉히며*, *고개를 돌리며*)
"""


@game_bp.route("/api/game/heroine/create", methods=["POST"])
def game_heroine_create():
    """히로인 프로필 등록
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [name]
          properties:
            name: {type: string}
            age: {type: integer}
            personality: {type: string, description: "성격 (밝고 활발/차갑고 도도/수줍고 조용 등)"}
            appearance: {type: string, description: "외모 묘사"}
            background: {type: string, description: "배경 스토리"}
            likes: {type: string, description: "좋아하는 것"}
            dislikes: {type: string, description: "싫어하는 것"}
            ideal_type: {type: string, description: "이상형"}
            voice_style: {type: string, description: "말투 (반말/존댓말/츤데레 등)"}
            routes: {type: array, items: {type: string}, description: "엔딩 루트명"}
            cg_character_id: {type: string, description: "IP-Adapter 참조 이미지 ID"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "name required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name: return jsonify({"error": "name required"}), 400
    import uuid as _u
    hid = str(_u.uuid4())
    conn = _get_game_db()
    conn.execute("""INSERT INTO heroine_profiles
        (id,game_id,name,age,personality,appearance,background,likes,dislikes,ideal_type,voice_style,routes,cg_character_id,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (hid, game["id"], name, data.get("age",18),
         json.dumps(data["personality"],ensure_ascii=False) if isinstance(data.get("personality"),dict) else str(data.get("personality","")), json.dumps(data["appearance"],ensure_ascii=False) if isinstance(data.get("appearance"),dict) else str(data.get("appearance","")),
         data.get("background",""), ", ".join(data["likes"]) if isinstance(data.get("likes"), list) else str(data.get("likes","")), ", ".join(data["dislikes"]) if isinstance(data.get("dislikes"), list) else str(data.get("dislikes","")),
         data.get("ideal_type",""), data.get("voice_style","반말"),
         json.dumps(data.get("routes",[]),ensure_ascii=False),
         data.get("cg_character_id","")))
    conn.commit()
    return jsonify({"heroine_id": hid, "name": name})


@game_bp.route("/api/game/heroine/list")
def game_heroine_list():
    """히로인 목록
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM heroine_profiles WHERE game_id=?", (game["id"],)).fetchall()
    return jsonify({"heroines": [dict(r) for r in rows]})


@game_bp.route("/api/game/heroine/talk", methods=["POST"])
def game_heroine_talk():
    """히로인과 대화 (멀티턴, 호감도 반영)
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, heroine_id, message]
          properties:
            player_id: {type: string}
            heroine_id: {type: string}
            message: {type: string}
            player_name: {type: string, default: "주인공"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id, heroine_id, message required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "Heroine not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid = data.get("player_id","")
    hid = data.get("heroine_id","")
    message = data.get("message","").strip()
    player_name = data.get("player_name","주인공")
    if not pid or not hid or not message:
        return jsonify({"error": "player_id, heroine_id, message required"}), 400

    conn = _get_game_db()
    heroine = conn.execute("SELECT * FROM heroine_profiles WHERE id=? AND game_id=?", (hid, game["id"])).fetchone()
    if not heroine: return jsonify({"error": "Heroine not found"}), 404
    heroine = dict(heroine)

    # 호감도 조회/생성
    aff = conn.execute("SELECT * FROM heroine_affinity WHERE heroine_id=? AND player_id=?", (hid, pid)).fetchone()
    if aff:
        aff = dict(aff)
    else:
        conn.execute("INSERT INTO heroine_affinity (heroine_id,game_id,player_id,first_met) VALUES (?,?,?,datetime('now'))", (hid, game["id"], pid))
        conn.commit()
        aff = {"affinity": 0, "stage": "stranger", "trust": 50, "jealousy": 0, "date_count": 0, "confession_count": 0}

    stage_code, stage_label = _get_affinity_stage(aff["affinity"])

    # 최근 대화 이력 (최대 10턴)
    history = conn.execute("SELECT role, content FROM heroine_chat_history WHERE heroine_id=? AND player_id=? ORDER BY created_at DESC LIMIT 10", (hid, pid)).fetchall()
    history = list(reversed(history))
    recent_chat = "\n".join([f"{'플레이어' if h['role']=='user' else heroine['name']}: {h['content'][:80]}" for h in history]) if history else "첫 대화"

    # 시스템 프롬프트
    system = HEROINE_SYSTEM_PROMPT.format(
        name=heroine["name"], age=heroine.get("age","?"),
        personality=heroine.get("personality",""), appearance=heroine.get("appearance",""),
        background=heroine.get("background",""), likes=heroine.get("likes",""),
        dislikes=heroine.get("dislikes",""), ideal_type=heroine.get("ideal_type",""),
        voice_style=heroine.get("voice_style",""), player_name=player_name,
        affinity=aff["affinity"], stage_label=stage_label, trust=aff["trust"],
        interactions=len(history), date_count=aff["date_count"], recent_chat=recent_chat)

    # LLM 응답
    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        response, _meta = _llm_generate_korean(
            model=model, prompt=message, system=system,
            options={"temperature": 0.85}, timeout=60)
    except Exception as e:
        return internal_error(e)

    # 호감도 변화 계산
    sweet_kw = ["좋아", "사랑", "예쁘", "귀여", "보고싶", "함께", "고마", "선물", "행복", "웃"]
    bad_kw = ["싫어", "바보", "못생", "짜증", "거짓", "무시", "화나", "미워"]
    sweet = sum(1 for kw in sweet_kw if kw in message)
    bad = sum(1 for kw in bad_kw if kw in message)
    aff_change = sweet * 3 - bad * 5 + 1  # 기본 +1 (대화 참여)
    trust_change = 1 if bad == 0 else -3
    new_aff = max(-100, min(100, aff["affinity"] + aff_change))
    new_trust = max(0, min(100, aff["trust"] + trust_change))
    new_stage, new_label = _get_affinity_stage(new_aff)
    stage_changed = new_stage != stage_code

    # DB 업데이트
    conn.execute("""UPDATE heroine_affinity SET affinity=?, stage=?, trust=?, last_interaction=datetime('now')
        WHERE heroine_id=? AND player_id=?""", (new_aff, new_stage, new_trust, hid, pid))

    # 대화 이력 저장
    conn.execute("INSERT INTO heroine_chat_history (heroine_id,player_id,role,content,mood,created_at) VALUES (?,?,?,?,?,datetime('now'))", (hid, pid, "user", message, ""))
    conn.execute("INSERT INTO heroine_chat_history (heroine_id,player_id,role,content,mood,created_at) VALUES (?,?,?,?,?,datetime('now'))", (hid, pid, "assistant", response, new_label))

    # 이벤트 로그
    if stage_changed:
        conn.execute("INSERT INTO heroine_events (heroine_id,game_id,player_id,event_type,description,affinity_change,stage_before,stage_after,created_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                     (hid, game["id"], pid, "stage_change", f"관계 변화: {stage_label} -> {new_label}", aff_change, stage_code, new_stage))
    conn.commit()

    result = {
        "heroine": heroine["name"],
        "response": response,
        "affinity": {"value": new_aff, "change": aff_change, "stage": new_label},
        "trust": new_trust,
    }
    if stage_changed:
        result["stage_change"] = {"from": stage_label, "to": new_label}
    return jsonify(result)


@game_bp.route("/api/game/heroine/date", methods=["POST"])
def game_heroine_date():
    """데이트 이벤트
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, heroine_id, location]
          properties:
            player_id: {type: string}
            heroine_id: {type: string}
            location: {type: string, description: "장소 (카페, 공원, 놀이공원, 해변, 영화관 등)"}
            player_name: {type: string, default: "주인공"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id, heroine_id required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "Heroine not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid, hid = data.get("player_id",""), data.get("heroine_id","")
    location = data.get("location","카페")
    player_name = data.get("player_name","주인공")
    if not pid or not hid: return jsonify({"error": "player_id, heroine_id required"}), 400

    conn = _get_game_db()
    heroine = conn.execute("SELECT * FROM heroine_profiles WHERE id=?", (hid,)).fetchone()
    if not heroine: return jsonify({"error": "Heroine not found"}), 404
    heroine = dict(heroine)
    aff = conn.execute("SELECT * FROM heroine_affinity WHERE heroine_id=? AND player_id=?", (hid, pid)).fetchone()
    if not aff: return jsonify({"error": "먼저 대화를 시작하세요"}), 400
    aff = dict(aff)
    stage_code, stage_label = _get_affinity_stage(aff["affinity"])

    if aff["affinity"] < 30:
        return jsonify({"error": f"호감도 부족 ({aff['affinity']}/30). 더 친해져야 데이트 가능합니다.", "stage": stage_label}), 400

    prompt = f"""{heroine['name']}과(와) {player_name}의 {location} 데이트 장면을 작성하세요. 반드시 한국어.

{heroine['name']}의 성격: {heroine.get('personality','')}
말투: {heroine.get('voice_style','')}
현재 관계: {stage_label} (호감도: {aff['affinity']})

4~6턴의 대화+행동으로 구성. JSON 배열:
[{{"speaker": "이름", "text": "대사", "action": "*행동*", "mood": "감정"}}]
{location}의 분위기를 살려주세요. 로맨틱한 순간 포함.
JSON만 출력."""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.heroine_date", prompt, model=model,
            options={"temperature": 0.9, "num_predict": 2000}, timeout=90, adapter=_router_adapter())
        text = _router_response(res)
        import re as _re
        m = _re.search(r'\[\s*\{[\s\S]*\}\s*\]', text)
        scenes = json.loads(m.group()) if m else [{"speaker": heroine["name"], "text": text[:200], "action": "", "mood": ""}]
    except Exception as e:
        return internal_error(e)

    # 데이트 보너스
    date_bonus = 8 + (5 if "좋아" in str(scenes) else 0)
    new_aff = min(100, aff["affinity"] + date_bonus)
    new_stage, new_label = _get_affinity_stage(new_aff)

    conn.execute("UPDATE heroine_affinity SET affinity=?, stage=?, date_count=date_count+1, last_interaction=datetime('now') WHERE heroine_id=? AND player_id=?",
                 (new_aff, new_stage, hid, pid))
    conn.execute("INSERT INTO heroine_events (heroine_id,game_id,player_id,event_type,description,affinity_change,stage_before,stage_after,created_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                 (hid, game["id"], pid, "date", f"{location} 데이트", date_bonus, stage_code, new_stage))
    conn.commit()

    return jsonify({
        "heroine": heroine["name"],
        "location": location,
        "scenes": scenes,
        "affinity": {"before": aff["affinity"], "after": new_aff, "change": date_bonus, "stage": new_label},
    })


@game_bp.route("/api/game/heroine/gift", methods=["POST"])
def game_heroine_gift():
    """선물하기 (취향 매칭)
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, heroine_id, gift]
          properties:
            player_id: {type: string}
            heroine_id: {type: string}
            gift: {type: string, description: "선물 이름"}
            player_name: {type: string, default: "주인공"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "player_id, heroine_id, gift required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "Heroine not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid, hid = data.get("player_id",""), data.get("heroine_id","")
    gift = data.get("gift","").strip()
    player_name = data.get("player_name","주인공")
    if not pid or not hid or not gift: return jsonify({"error": "player_id, heroine_id, gift required"}), 400

    conn = _get_game_db()
    heroine = conn.execute("SELECT * FROM heroine_profiles WHERE id=?", (hid,)).fetchone()
    if not heroine: return jsonify({"error": "Heroine not found"}), 404
    heroine = dict(heroine)
    aff = conn.execute("SELECT * FROM heroine_affinity WHERE heroine_id=? AND player_id=?", (hid, pid)).fetchone()
    if not aff:
        conn.execute("INSERT INTO heroine_affinity (heroine_id,game_id,player_id,first_met) VALUES (?,?,?,datetime('now'))", (hid, game["id"], pid))
        conn.commit()
        aff = {"affinity": 0, "trust": 50}
    else:
        aff = dict(aff)

    # LLM으로 선물 반응 생성 + 취향 매칭 판단
    prompt = f"""{heroine['name']}이(가) {player_name}에게서 '{gift}'를 선물로 받았습니다. 한국어로 반응 생성.

{heroine['name']}이 좋아하는 것: {heroine.get('likes','')}
싫어하는 것: {heroine.get('dislikes','')}
성격: {heroine.get('personality','')}
말투: {heroine.get('voice_style','')}

JSON 형식:
{{"reaction": "대사 + *행동*", "liked": true/false, "match_reason": "이유"}}
JSON만 출력."""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.heroine_gift", prompt, model=model,
            options={"temperature": 0.8, "num_predict": 300}, timeout=60, adapter=_router_adapter())
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', _router_response(res))
        result = json.loads(m.group()) if m else {"reaction": "고마워!", "liked": True, "match_reason": ""}
    except Exception:
        result = {"reaction": "고마워... *받으며*", "liked": True, "match_reason": ""}

    bonus = 12 if result.get("liked") else -3
    new_aff = max(-100, min(100, aff["affinity"] + bonus))
    new_stage, new_label = _get_affinity_stage(new_aff)

    conn.execute("UPDATE heroine_affinity SET affinity=?, stage=?, last_interaction=datetime('now') WHERE heroine_id=? AND player_id=?",
                 (new_aff, new_stage, hid, pid))
    conn.execute("INSERT INTO heroine_events (heroine_id,game_id,player_id,event_type,description,affinity_change,created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
                 (hid, game["id"], pid, "gift", f"선물: {gift} ({'좋아함' if result.get('liked') else '별로'})", bonus))
    conn.commit()

    return jsonify({
        "heroine": heroine["name"],
        "gift": gift,
        "reaction": result.get("reaction",""),
        "liked": result.get("liked", True),
        "match_reason": result.get("match_reason",""),
        "affinity": {"before": aff["affinity"], "after": new_aff, "change": bonus, "stage": new_label},
    })


@game_bp.route("/api/game/heroine/confess", methods=["POST"])
def game_heroine_confess():
    """고백 (호감도 기반 성공/거절/보류)
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          required: [player_id, heroine_id]
          properties:
            player_id: {type: string}
            heroine_id: {type: string}
            confession_message: {type: string, default: "좋아해"}
            player_name: {type: string, default: "주인공"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "먼저 친해져야 합니다"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "Heroine not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    pid, hid = data.get("player_id",""), data.get("heroine_id","")
    confession = data.get("confession_message","좋아해")
    player_name = data.get("player_name","주인공")

    conn = _get_game_db()
    heroine = conn.execute("SELECT * FROM heroine_profiles WHERE id=?", (hid,)).fetchone()
    if not heroine: return jsonify({"error": "Heroine not found"}), 404
    heroine = dict(heroine)
    aff = conn.execute("SELECT * FROM heroine_affinity WHERE heroine_id=? AND player_id=?", (hid, pid)).fetchone()
    if not aff: return jsonify({"error": "먼저 친해져야 합니다"}), 400
    aff = dict(aff)

    # 고백 결과 판정
    if aff["affinity"] >= 70:
        result_type = "accept"
        aff_change = 15
    elif aff["affinity"] >= 50:
        result_type = "pending"
        aff_change = 5
    elif aff["affinity"] >= 30:
        result_type = "reject_gentle"
        aff_change = -5
    else:
        result_type = "reject_hard"
        aff_change = -15

    # LLM 반응 생성
    result_desc = {"accept": "수락 (기쁨, 설렘)", "pending": "보류 (고민, 아직 확신 없음)",
                   "reject_gentle": "부드러운 거절 (미안, 친구로)", "reject_hard": "단호한 거절"}
    prompt = f"""{heroine['name']}에게 {player_name}이(가) 고백합니다: "{confession}"

{heroine['name']}의 성격: {heroine.get('personality','')}
말투: {heroine.get('voice_style','')}
현재 호감도: {aff['affinity']}
결과: {result_desc[result_type]}

{heroine['name']}의 반응을 대사 + *행동* + (내면 독백)으로 작성. 한국어만."""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.heroine_confess", prompt, model=model,
            options={"temperature": 0.85}, timeout=60, adapter=_router_adapter())
        response = _router_response(res)
    except Exception as e:
        return internal_error(e)

    new_aff = max(-100, min(100, aff["affinity"] + aff_change))
    new_stage, new_label = _get_affinity_stage(new_aff)

    conn.execute("UPDATE heroine_affinity SET affinity=?, stage=?, confession_count=confession_count+1, last_interaction=datetime('now') WHERE heroine_id=? AND player_id=?",
                 (new_aff, new_stage, hid, pid))
    conn.execute("INSERT INTO heroine_events (heroine_id,game_id,player_id,event_type,description,affinity_change,stage_before,stage_after,created_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                 (hid, game["id"], pid, "confession", f"고백: {result_type}", aff_change, aff.get("stage",""), new_stage))
    conn.commit()

    return jsonify({
        "heroine": heroine["name"],
        "confession": confession,
        "result": result_type,
        "response": response,
        "affinity": {"before": aff["affinity"], "after": new_aff, "stage": new_label},
    })


@game_bp.route("/api/game/heroine/status")
def game_heroine_status():
    """히로인과의 관계 상태
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: query
        name: player_id
        type: string
        required: true
      - in: query
        name: heroine_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "not found"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    pid = request.args.get("player_id","")
    hid = request.args.get("heroine_id","")
    conn = _get_game_db()
    heroine = conn.execute("SELECT name,personality,likes,dislikes FROM heroine_profiles WHERE id=?", (hid,)).fetchone()
    if not heroine: return jsonify({"error": "not found"}), 404
    aff = conn.execute("SELECT * FROM heroine_affinity WHERE heroine_id=? AND player_id=?", (hid, pid)).fetchone()
    events = conn.execute("SELECT event_type, description, affinity_change, created_at FROM heroine_events WHERE heroine_id=? AND player_id=? ORDER BY created_at DESC LIMIT 10", (hid, pid)).fetchall()
    stage_code, stage_label = _get_affinity_stage(aff["affinity"]) if aff else ("stranger", "낯선 사람")
    return jsonify({
        "heroine": dict(heroine),
        "affinity": dict(aff) if aff else {"affinity": 0},
        "stage": stage_label,
        "events": [dict(e) for e in events],
    })




@game_bp.route("/api/game/heroine/<heroine_id>", methods=["PUT"])
def game_heroine_update(heroine_id):
    """히로인 프로필 수정
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: path
        name: heroine_id
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "no fields"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    conn = _get_game_db()
    updates = []
    params = []
    for field in ["name","age","personality","appearance","background","likes","dislikes","ideal_type","voice_style"]:
        if field in data:
            updates.append(f"{field}=?")
            params.append(data[field])
    if "routes" in data:
        updates.append("routes=?")
        params.append(json.dumps(data["routes"], ensure_ascii=False))
    if not updates:
        return jsonify({"error": "no fields"}), 400
    params.extend([heroine_id, game["id"]])
    conn.execute(f"UPDATE heroine_profiles SET {','.join(updates)} WHERE id=? AND game_id=?", params)
    conn.commit()
    return jsonify({"ok": True})


@game_bp.route("/api/game/heroine/<heroine_id>", methods=["DELETE"])
def game_heroine_delete(heroine_id):
    """히로인 삭제
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: path
        name: heroine_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    conn.execute("DELETE FROM heroine_affinity WHERE heroine_id=? AND game_id=?", (heroine_id, game["id"]))
    conn.execute("DELETE FROM heroine_events WHERE heroine_id=? AND game_id=?", (heroine_id, game["id"]))
    conn.execute("DELETE FROM heroine_chat_history WHERE heroine_id=?", (heroine_id,))
    conn.execute("DELETE FROM heroine_profiles WHERE id=? AND game_id=?", (heroine_id, game["id"]))
    conn.commit()
    return jsonify({"ok": True})


@game_bp.route("/api/game/heroine/<heroine_id>/players")
def game_heroine_players(heroine_id):
    """히로인과 상호작용한 플레이어 목록 + 호감도
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: path
        name: heroine_id
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    conn = _get_game_db()
    rows = conn.execute("SELECT * FROM heroine_affinity WHERE heroine_id=? AND game_id=? ORDER BY affinity DESC",
                        (heroine_id, game["id"])).fetchall()
    events = conn.execute("SELECT event_type, COUNT(*) as cnt FROM heroine_events WHERE heroine_id=? AND game_id=? GROUP BY event_type",
                          (heroine_id, game["id"])).fetchall()
    return jsonify({
        "players": [dict(r) for r in rows],
        "event_summary": {e["event_type"]: e["cnt"] for e in events},
    })


@game_bp.route("/api/game/heroine/generate", methods=["POST"])
def game_heroine_generate():
    """히로인 자동 생성 (LLM)
    ---
    tags: [Game Heroine]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Game-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            archetype: {type: string, description: "유형 (츤데레, 쿨뷰티, 소꿉친구, 후배, 선배, 얀데레 등)"}
            genre: {type: string, description: "장르 (학원, 판타지, 현대 등)"}
    responses:
      200:
        description: "요청 성공"
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
    """
    game = _resolve_game(request)
    if not game: return jsonify({"error": "X-Game-Key required"}), 401
    data = request.json or {}
    archetype = data.get("archetype","츤데레")
    genre = data.get("genre","학원")
    # 8가지 조합 요소
    h_parts = []
    for k, label in [("family","가정환경"),("hobby","취미"),("trauma","트라우마"),("romance_style","연애스타일"),
                      ("speech","말투"),("appearance_trait","외형"),("secret","비밀"),("charm_point","매력포인트")]:
        v = data.get(k,"")
        if v: h_parts.append(f"{label}: {v}")
    h_traits = "\n".join(h_parts)
    extra_prompt = data.get("extra_prompt","")
    custom_name = data.get("name","")

    prompt = f"""연애 시뮬레이션 히로인 프로필 JSON 생성. 반드시 모든 필드를 한국어로만 작성하세요. 절대 중국어, 일본어, 영어를 사용하지 마세요.
{f'이름은 반드시 {custom_name}(으)로 지정하세요.' if custom_name else '이름은 자유롭게 생성하세요.'}
유형: {archetype}, 장르: {genre}
{h_traits}

{{"name": "이름", "age": 18, "personality": "성격 상세", "appearance": "외모 묘사",
"background": "배경 스토리", "likes": "좋아하는 것 3개", "dislikes": "싫어하는 것 3개",
"ideal_type": "이상형", "voice_style": "말투 스타일"}}
JSON만 출력."""

    try:
        model = _load_config().get("default_model", "qwen2.5:14b")
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_game.py)
        res = model_router.generate("game.heroine_generate", prompt, model=model,
            options={"temperature": 0.9, "num_predict": 800}, timeout=60, adapter=_router_adapter())
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', _router_response(res))
        profile = json.loads(m.group()) if m else {"name": "자동생성 실패"}
    except Exception as e:
        return internal_error(e)

    return jsonify({"generated": profile, "archetype": archetype, "genre": genre,
                    "note": "확인 후 /api/game/heroine/create로 등록하세요"})


# ---------------------------------------------------------------------------

