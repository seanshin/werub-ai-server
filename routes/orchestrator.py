"""AI Orchestrator Blueprint — 워크플로우 엔진 + 통합 채팅"""
import json
import logging
import re
import time
import uuid
import sqlite3
import threading
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import hashlib

import requests
from flask import Blueprint, request, jsonify, Response
from core.korean import force_korean, force_korean_json
from core.cache import TTLCache

logger = logging.getLogger("ollama-admin")

orchestrator_bp = Blueprint("orchestrator", __name__)

_ollama_session = None
_OLLAMA_BASE = None
_BASE_DIR = None
_resolve_medical_tenant = None

_executor = ThreadPoolExecutor(max_workers=4)
_orch_db_local = threading.local()
_intent_cache = TTLCache(maxsize=500, ttl=60)  # intent 탐지 캐시 (60초)


_flask_app = None
_tool_registry = None

# General LLM chat 폴백 설정 (config/tools.json 의 "general_chat" 블록으로 오버라이드)
# 기본값 = 현행 동작(qwen2.5:14b). 모델 교체 시 코드 수정 없이 tools.json 만 변경 → /api/orchestrator/tools/reload
_GENERAL_CHAT_DEFAULTS = {
    "model": "qwen2.5:14b",
    "temperature": 0.5,
    "num_predict": 500,
    "timeout": 60,
    "system": "당신은 병원 AI 어시스턴트입니다. 한국어로 친절하게 답변하세요.",
}
_general_chat_cfg = dict(_GENERAL_CHAT_DEFAULTS)

# 워크플로 액션(ai:/llm:) 기본 모델 — 스텝에 model 미지정 시 사용. tools.json "workflow_default_model" 로 오버라이드.
_WORKFLOW_DEFAULT_MODEL = "qwen2.5:14b"
_workflow_default_model = _WORKFLOW_DEFAULT_MODEL




def _json_acceptable(text):
    """JSON 계약 경로의 **캐시 수용** 판정 — 정본은 `routes/medical._json_acceptable`.

    깨진 출력을 캐시하면 일과성 결함이 TTL 동안 고정 오답이 된다(2026-08-10 ADR 실측).
    여기서는 위임만 한다 — 판정 규칙이 두 곳에 생기면 곧 갈라진다.
    """
    from routes.medical import _json_acceptable as _impl
    return _impl(text)


def _load_general_chat_cfg(base_dir):
    """config/tools.json 의 general_chat 블록을 검증·병합 (잘못된 값은 기본값 유지).

    타입/범위 가드: 운영자가 timeout=null 등 잘못 넣어도 requests 무한대기(스레드 고갈) 방지.
    """
    global _general_chat_cfg, _workflow_default_model
    path = os.path.join(base_dir, "config", "tools.json")
    cfg = dict(_GENERAL_CHAT_DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            _raw = json.load(f)
        gc = _raw.get("general_chat")
        # 워크플로 기본 모델(선택)
        _wm = _raw.get("workflow_default_model")
        _workflow_default_model = _wm.strip() if isinstance(_wm, str) and _wm.strip() else _WORKFLOW_DEFAULT_MODEL
        if isinstance(gc, dict):
            if isinstance(gc.get("model"), str) and gc["model"].strip():
                cfg["model"] = gc["model"].strip()
            if isinstance(gc.get("system"), str) and gc["system"].strip():
                cfg["system"] = gc["system"]
            # 숫자 필드: 유효 범위 내 양수만 수용, 아니면 기본값 유지
            # (bool 은 int 하위클래스 → 명시 배제. 예: timeout=True 가 1초로 새는 것 방지)
            def _num(v):
                return isinstance(v, (int, float)) and not isinstance(v, bool)
            if _num(gc.get("temperature")) and 0 <= gc["temperature"] <= 2:
                cfg["temperature"] = float(gc["temperature"])
            if _num(gc.get("num_predict")) and isinstance(gc["num_predict"], int) and 1 <= gc["num_predict"] <= 8192:
                cfg["num_predict"] = gc["num_predict"]
            if _num(gc.get("timeout")) and 0 < gc["timeout"] <= 600:
                cfg["timeout"] = gc["timeout"]
        _general_chat_cfg = cfg
        logger.info("general_chat config loaded: model=%s timeout=%s", cfg["model"], cfg["timeout"])
    except Exception as e:
        _general_chat_cfg = dict(_GENERAL_CHAT_DEFAULTS)
        logger.warning("failed to load general_chat config (using defaults): %s", e)


def init_orchestrator(*, ollama_session, ollama_base, base_dir, resolve_tenant, flask_app=None):
    global _ollama_session, _OLLAMA_BASE, _BASE_DIR, _resolve_medical_tenant, _flask_app, _tool_registry
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir
    _resolve_medical_tenant = resolve_tenant
    _flask_app = flask_app

    from core.tool_registry import ToolRegistry
    _tool_registry = ToolRegistry(base_dir=base_dir)
    _load_general_chat_cfg(base_dir)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_orch_db():
    conn = getattr(_orch_db_local, "conn", None)
    if conn is None:
        db_path = os.path.join(_BASE_DIR, "orchestrator.db")
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS orchestrator_runs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                workflow TEXT NOT NULL,
                input TEXT,
                status TEXT DEFAULT 'running',
                steps_completed INTEGER DEFAULT 0,
                steps_total INTEGER DEFAULT 0,
                result TEXT,
                total_duration_ms INTEGER,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orchestrator_step_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT,
                duration_ms INTEGER,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                tenant_id TEXT,
                history TEXT DEFAULT '[]',
                last_intent TEXT,
                turn_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tool_call_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                tool_name TEXT NOT NULL,
                tool_args TEXT,
                tool_result TEXT,
                duration_ms INTEGER,
                success INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_tool_call_logs_tool ON tool_call_logs(tool_name);
            CREATE TABLE IF NOT EXISTS custom_workflows (
                name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                description TEXT DEFAULT '',
                domain TEXT DEFAULT 'custom',
                steps TEXT NOT NULL,
                triggers TEXT DEFAULT '[]',
                created_by TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        _orch_db_local.conn = conn
    return conn


# ---------------------------------------------------------------------------
# Custom workflow helpers
# ---------------------------------------------------------------------------

_VALID_ACTION_PREFIXES = ("ai:", "llm:", "internal:", "workflow:")

# 🔴 `ai:` 대상 엔드포인트 allowlist (v2.125.17 · 보안)
#
# **왜 필요한가**: `_execute_action`은 `test_request_context(environ_base={"REMOTE_ADDR":
# "127.0.0.1"})`로 **localhost를 위조**해 디스패치한다(아래 `:527` 부근). localhost는 인증
# bypass이자 `_is_admin_request()` 통과 조건이므로, 접두사만 검사하면 워크플로 한 줄
# (`ai:/api/keys`·`ai:/api/config`)이 **관리자 API를 그대로 실행**한다 — 유효 API 키 하나면
# 영구 키 발급·전 시크릿 열람·`auth_enabled:false`(인증 킬스위치)까지 간다.
# 즉 이 한 곳이 코드베이스의 모든 `_is_admin_request()` 게이트를 무효화할 수 있었다.
#
# **왜 allowlist인가**: deny 목록은 새 관리자 엔드포인트가 생길 때마다 뚫린다(빠뜨린 사실이
# 조용하다). 이 레포는 이미 같은 결론을 냈다 — `core/model_router`의 "미등록 task는 즉시 실패".
# 목록은 2026-08-25에 `config/*.json`·`routes/*.py`의 실사용 `ai:` 액션을 전수로 뜬 것이고,
# 그 시점 DB 등록 커스텀 워크플로는 **0건**이라 도입으로 깨지는 것이 없음을 확인했다.
#
# ⚠️ 새 워크플로가 새 엔드포인트를 쓰면 **여기 추가해야 한다**. 그때 물을 것:
#    "이 엔드포인트가 위조된 localhost 권한으로 실행돼도 되는가?"
_AI_ACTION_ALLOWLIST = frozenset({
    "/api/hr-law/ask",
    "/api/korean/analyze", "/api/korean/pipeline", "/api/korean/readability",
    "/api/korean/spellcheck", "/api/korean/style-transfer",
    "/api/medical-law/ask",
    "/api/medical/analyze", "/api/medical/auto-record", "/api/medical/cli/execute",
    "/api/medical/clinical-qa", "/api/medical/cross-validate", "/api/medical/draft",
    "/api/medical/drug-analysis", "/api/medical/drug-info", "/api/medical/dur/check",
    "/api/medical/explain", "/api/medical/patient-education",
    "/api/medical/predict/readmission", "/api/medical/reasoning/chain",
    "/api/medical/reference", "/api/medical/research", "/api/medical/summarize",
    "/api/medical/triage",
    "/api/notice/diagnose", "/api/notice/drug-master/resolve",
    "/api/notice/drug-substitution", "/api/notice/search",
    "/api/rag/ask", "/api/rag/search",
    "/api/vision/describe",
    "/api/writing/draft", "/api/writing/evaluate", "/api/writing/polish",
    "/api/writing/seo", "/api/writing/translate",
})


def _ai_endpoint_allowed(endpoint: str) -> bool:
    """`ai:` 액션이 가리키는 엔드포인트가 allowlist에 있는가.

    쿼리스트링·중복 슬래시를 정규화한 뒤 **정확 일치**로 본다(접두 일치를 쓰면
    `/api/rag/search/../keys` 류가 통과한다 — 경로 순회 봉합과 같은 규율).
    """
    ep = (endpoint or "").split("?", 1)[0].split("#", 1)[0]
    while "//" in ep:
        ep = ep.replace("//", "/")
    if len(ep) > 1 and ep.endswith("/"):
        ep = ep[:-1]
    if ".." in ep:
        return False
    return ep in _AI_ACTION_ALLOWLIST


def _validate_workflow_def(data):
    """커스텀 워크플로 정의 검증"""
    errors = []
    name = data.get("name", "")
    if not name or not re.match(r'^[a-z][a-z0-9_]{2,39}$', name):
        errors.append("name: 영문 소문자+숫자+밑줄, 3-40자 (예: my_workflow)")
    if not data.get("display_name"):
        errors.append("display_name 필수")
    steps = data.get("steps", [])
    if not steps or not isinstance(steps, list):
        errors.append("steps: 최소 1개 이상의 단계 필요")
    step_ids = set()
    for i, step in enumerate(steps):
        sid = step.get("id", "")
        if not sid:
            errors.append(f"steps[{i}]: id 필수")
        elif sid in step_ids:
            errors.append(f"steps[{i}]: id '{sid}' 중복")
        step_ids.add(sid)
        action = step.get("action", "")
        if not any(action.startswith(p) for p in _VALID_ACTION_PREFIXES):
            errors.append(f"steps[{i}]: action은 {_VALID_ACTION_PREFIXES} 중 하나로 시작해야 함")
        elif action.startswith("ai:") and not _ai_endpoint_allowed(action[len("ai:"):]):
            # 등록 시 1차 차단(디스패치에도 같은 검사가 있다 — 기존 DB 행은 여기를 안 거친다)
            errors.append(f"steps[{i}]: 허용되지 않은 ai: 엔드포인트 '{action}' — "
                          f"위조된 localhost 권한으로 실행되므로 allowlist에 등록된 것만 쓸 수 있다")
        for dep in step.get("depends_on", []):
            if dep not in step_ids:
                errors.append(f"steps[{i}]: depends_on '{dep}' 이전 스텝에 없음")
    return errors


def _get_all_workflows():
    """Builtin + custom(active) 워크플로 통합 반환"""
    merged = dict(WORKFLOWS)
    try:
        db = _get_orch_db()
        rows = db.execute("SELECT * FROM custom_workflows WHERE is_active=1").fetchall()
        for row in rows:
            merged[row["name"]] = {
                "name": row["display_name"],
                "description": row["description"],
                "domain": row["domain"],
                "steps": json.loads(row["steps"]),
                "_custom": True,
            }
    except (sqlite3.Error, json.JSONDecodeError) as e:
        logger.debug("Failed to load custom workflows: %s", e)
    return merged


# ---------------------------------------------------------------------------
# P2-2: 멀티턴 대화 세션
# ---------------------------------------------------------------------------

_SESSION_MAX_TURNS = 10
_SESSION_TTL_SEC = 1800  # 30분
_SESSION_MAX_CONTEXT_CHARS = 2000


def _get_session_history(session_id):
    """세션 히스토리 조회 (TTL 초과 시 자동 삭제)"""
    if not session_id:
        return []
    db = _get_orch_db()
    row = db.execute("SELECT history, updated_at FROM chat_sessions WHERE session_id=?",
                     (session_id,)).fetchone()
    if not row:
        return []
    # TTL 체크
    try:
        from datetime import datetime as _dt
        updated = _dt.fromisoformat(row["updated_at"])
        age = (datetime.now() - updated).total_seconds()
        if age > _SESSION_TTL_SEC:
            db.execute("DELETE FROM chat_sessions WHERE session_id=?", (session_id,))
            db.commit()
            return []
    except Exception:
        pass
    try:
        return json.loads(row["history"])
    except (json.JSONDecodeError, TypeError):
        return []


def _save_session_turn(session_id, user_msg, assistant_msg, intent=""):
    """세션에 턴 추가"""
    if not session_id:
        return
    db = _get_orch_db()
    history = _get_session_history(session_id)

    # 최대 턴 수 제한
    history.append({"role": "user", "content": user_msg[:500]})
    history.append({"role": "assistant", "content": assistant_msg[:500]})
    if len(history) > _SESSION_MAX_TURNS * 2:
        history = history[-_SESSION_MAX_TURNS * 2:]

    history_json = json.dumps(history, ensure_ascii=False)
    db.execute("""
        INSERT INTO chat_sessions (session_id, history, last_intent, turn_count, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(session_id) DO UPDATE SET
            history=excluded.history, last_intent=excluded.last_intent,
            turn_count=turn_count+1, updated_at=datetime('now')
    """, (session_id, history_json, intent, len(history) // 2))
    db.commit()


def _build_context_from_history(history):
    """세션 히스토리를 LLM 컨텍스트 문자열로 변환"""
    if not history:
        return ""
    lines = []
    total_chars = 0
    for turn in reversed(history):
        line = f"{'사용자' if turn['role'] == 'user' else 'AI'}: {turn['content']}"
        if total_chars + len(line) > _SESSION_MAX_CONTEXT_CHARS:
            break
        lines.insert(0, line)
        total_chars += len(line)
    if not lines:
        return ""
    return "\n[이전 대화]\n" + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Context Pipeline — {{step.field}} template resolution
# ---------------------------------------------------------------------------

def _resolve_template(template, context):
    """Resolve {{step.field}} templates from context"""
    if template is None:
        return None
    if isinstance(template, dict):
        return {k: _resolve_template(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [_resolve_template(v, context) for v in template]
    if not isinstance(template, str):
        return template

    def replacer(match):
        path = match.group(1).strip()
        # Handle default: {{input.x || 'default'}}
        if '||' in path:
            parts = path.split('||')
            path = parts[0].strip()
            default = parts[1].strip().strip("'\"")
        else:
            default = ''

        obj = context
        for part in path.split('.'):
            # Array access: field[0]
            arr = re.match(r'(\w+)\[(\d+)\]', part)
            if arr:
                key, idx = arr.group(1), int(arr.group(2))
                obj = obj.get(key, []) if isinstance(obj, dict) else []
                obj = obj[idx] if isinstance(obj, list) and len(obj) > idx else None
            else:
                obj = obj.get(part) if isinstance(obj, dict) else None
            if obj is None:
                return default
        return str(obj) if obj is not None else default

    return re.sub(r'\{\{(.+?)\}\}', replacer, template)


# ---------------------------------------------------------------------------
# Internal action executor
# ---------------------------------------------------------------------------

def _get_direct_call_map():
    """직접 호출 맵 — test_request_context 우회 (32ms→<1ms/호출)"""
    try:
        from routes.triage import (
            _extract_symptoms, _extract_symptoms_llm, _check_emergency,
            _get_dept_recommendations, _generate_guidance, _age_group, DEPARTMENTS,
        )
        from routes.medical import _medical_generate, _require_tenant as _med_tenant
        from core.quality import force_json, force_json_array

        def _direct_triage(data, tenant):
            symptom_text = data.get("symptom", "").strip()
            if not symptom_text:
                return {"error": "symptom is required"}
            age = data.get("age")
            if age is not None:
                try: age = int(age)
                except (ValueError, TypeError): age = None
            sex, duration, severity = data.get("sex"), data.get("duration"), data.get("severity")
            symptoms = _extract_symptoms(symptom_text) or _extract_symptoms_llm(symptom_text)
            emergency = _check_emergency(symptoms, age)
            recs_raw = _get_dept_recommendations(symptoms, age, sex)
            recommendations = []
            for dept, confidence, code in recs_raw[:3]:
                dept_info = DEPARTMENTS.get(dept, {})
                recommendations.append({
                    "rank": len(recommendations) + 1, "department": dept,
                    "department_code": code, "department_full": dept_info.get("full", dept),
                    "confidence": confidence, "reason": f"{dept}에서 관련 증상을 진료합니다.",
                    "possible_conditions": [],
                    "urgency": "high" if emergency else ("moderate" if severity == "severe" else "low"),
                })
            guidance = _generate_guidance(symptom_text, symptoms, recommendations, age, sex, duration, severity) if symptoms and recommendations else ""
            return {
                "recommendations": recommendations,
                "symptom_analysis": {"input": symptom_text, "extracted_symptoms": symptoms,
                                     "duration": duration, "severity": severity,
                                     "age_group": _age_group(age) if age else None},
                "guidance": guidance,
                "emergency_warning": {"level": emergency["level"], "message": emergency["message"],
                                       "call_119": emergency.get("call_119", False)} if emergency else None,
                "disclaimer": "이 추천은 AI 참고 자료이며, 정확한 진단은 의사의 진료를 통해 이루어집니다.",
            }

        def _direct_summarize(data, tenant):
            text = data.get("text", "").strip()
            if not text:
                return {"error": "text is required"}
            fmt = data.get("format", "soap")
            specialty = data.get("specialty", "내과")
            model = data.get("model", _workflow_default_model)
            from routes.medical import SUMMARIZE_SYSTEM, DISCLAIMER, validate_soap_fields
            from core.quality import generate_with_validation_ex
            system = SUMMARIZE_SYSTEM.format(specialty=specialty, format=fmt.upper())
            opts = {"temperature": 0.2, "num_predict": 2000}
            response, tokens, duration_ms, retries, passed = generate_with_validation_ex(
                generate_fn=lambda p, s, o: _medical_generate(model, p, s, o or opts, accept=_json_acceptable),
                prompt=text, system=system, options=opts,
                validators=[validate_soap_fields], max_retries=2,
            )
            # v2.125.72: 워크플로 단계도 `/api/medical/summarize`와 같은 계약 — 파싱 실패면 단계 실패(`error`).
            #   종전엔 `{"raw": 원문}`이 '완료'로 다음 단계에 흘렀다(단계는 `error` 키가 있을 때만 실패로 본다).
            from core.quality import parse_json_strict
            result = parse_json_strict(response)
            if not isinstance(result, dict):
                logger.warning("[orchestrator] summarize 단계 구조 미충족 — 단계 실패: model=%s len=%d", model, len(response or ""))
                return {"error": "summarize: model output is not the promised JSON"}
            from core.quality import validate_icd_codes
            icd_codes = result.get("icd_codes", [])
            if icd_codes:
                result["icd_validation"] = validate_icd_codes(icd_codes)
            result["quality"] = {"retries": retries, "validated": passed}
            result["disclaimer"] = DISCLAIMER.strip()
            result["model"] = model
            result["tokens"] = tokens
            result["duration_ms"] = duration_ms
            return result

        def _direct_analyze(data, tenant):
            from routes.medical import ANALYZE_SYSTEM, DISCLAIMER
            from core.quality import generate_with_validation_ex, validate_json_array_response, validate_icd_codes
            model = data.get("model", _workflow_default_model)
            prompt_parts = []
            if data.get("symptoms"):
                prompt_parts.append(f"Symptoms: {', '.join(data['symptoms'])}")
            if data.get("vitals"):
                prompt_parts.append(f"Vitals: {json.dumps(data['vitals'])}")
            if data.get("history"):
                prompt_parts.append(f"History: {data['history']}")
            prompt = "\n".join(prompt_parts)
            if not prompt.strip():
                return {"error": "at least one clinical data field required"}
            opts = {"temperature": 0.2, "num_predict": 2000}
            response, tokens, duration_ms, retries, passed = generate_with_validation_ex(
                generate_fn=lambda p, s, o: _medical_generate(model, p, s, o or opts, accept=_json_acceptable),
                prompt=prompt, system=ANALYZE_SYSTEM, options=opts,
                validators=[validate_json_array_response], max_retries=2,
            )
            # v2.125.72: `force_json_array`는 실패 시 `[]`('감별진단 없음') — 단계 실패로.
            from core.quality import parse_json_array_strict
            differential = parse_json_array_strict(response)
            if differential is None:
                logger.warning("[orchestrator] analyze 단계 구조 미충족 — 단계 실패: model=%s len=%d", model, len(response or ""))
                return {"error": "analyze: model output is not the promised JSON"}
            all_icd = []
            for dx in differential:
                if isinstance(dx, dict):
                    for key in ("icd10", "icd_code", "code", "icd"):
                        if key in dx and dx[key]:
                            all_icd.append(str(dx[key]))
                            break
            result = {
                "differential_diagnosis": differential,
                "model": model, "tokens": tokens, "duration_ms": duration_ms,
                "disclaimer": DISCLAIMER.strip(),
                "quality": {"retries": retries, "validated": passed},
            }
            if all_icd:
                result["icd_validation"] = validate_icd_codes(all_icd)
            return result

        return {
            "/api/medical/triage": _direct_triage,
            "/api/medical/summarize": _direct_summarize,
            "/api/medical/analyze": _direct_analyze,
        }
    except ImportError as e:
        logger.warning(f"Direct call map init failed: {e}")
        return {}


_direct_calls = None


def _execute_action(action, input_data, tenant, headers=None):
    """Execute a single action (ai:/his:/llm:/internal:) — 직접 호출 우선"""
    global _direct_calls
    if _direct_calls is None:
        _direct_calls = _get_direct_call_map()

    start = time.time()

    try:
        if action.startswith("ai:") or action.startswith("/api/"):
            endpoint = action.replace("ai:", "")
            if not endpoint.startswith("/"):
                endpoint = "/" + endpoint
            # 🔴 최종 방어선(v2.125.17): 아래 디스패치가 REMOTE_ADDR을 127.0.0.1로 **위조**하므로
            #    allowlist 밖 엔드포인트는 여기서 막는다. 등록 검증(`_validate_workflow_def`)만으로는
            #    부족하다 — 그 전에 저장된 DB 행·내부 호출자는 등록 경로를 거치지 않는다.
            if not _ai_endpoint_allowed(endpoint):
                logger.warning("[orchestrator] allowlist 밖 ai: 엔드포인트 차단: %s", endpoint)
                return {"error": "허용되지 않은 엔드포인트: %s" % endpoint}, \
                       int((time.time() - start) * 1000)

            # Direct call (bypass test_request_context, ~32ms savings)
            direct_fn = _direct_calls.get(endpoint)
            if direct_fn:
                result = direct_fn(input_data, tenant)
                elapsed = int((time.time() - start) * 1000)
                return result, elapsed

            # Fallback: test_request_context (localhost bypass를 위해 REMOTE_ADDR 설정)
            h = {"Content-Type": "application/json"}
            if tenant and '/triage' not in endpoint:
                h["X-Medical-Key"] = tenant.get("api_key", "")
            try:
                with _flask_app.test_request_context(endpoint, method='POST', json=input_data, headers=h,
                                                     environ_base={"REMOTE_ADDR": "127.0.0.1"}):
                    from flask import current_app
                    rv = current_app.full_dispatch_request()
                    elapsed = int((time.time() - start) * 1000)
                    try:
                        result = rv.get_json()
                        return result if result else {"error": "empty response"}, elapsed
                    except Exception:
                        return {"error": rv.get_data(as_text=True)[:200]}, elapsed
            except Exception as e:
                elapsed = int((time.time() - start) * 1000)
                return {"error": f"dispatch failed: {str(e)[:100]}"}, elapsed

        elif action.startswith("llm:"):
            # Direct LLM call
            sub = action.replace("llm:", "")
            model = input_data.get("model", _workflow_default_model)
            prompt = input_data.get("prompt", "")
            system = input_data.get("system", "")
            options = input_data.get("options", {"temperature": 0.3, "num_predict": 1500})

            # v2.124.99 task 라우터 경유 — 🔴 호출자의 세션·base를 넘긴다
            from core import model_router
            _res = model_router.generate(
                "orchestrator.workflow_step", prompt, model=model, system=system,
                options=options, timeout=120,
                adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE,
                                                   session=_ollama_session))
            if not _res.ok:      # v2.125.16: HTTP 오류 → 워크플로 오류 분기(종전엔 빈 응답이 다음 단계로)
                raise RuntimeError(_res.error or ("HTTP %s" % _res.status))
            elapsed = _res.duration_ms or int((time.time() - start) * 1000)
            return {"response": _res.text, "tokens": _res.tokens}, elapsed

        elif action.startswith("internal:"):
            # Internal function
            func_name = action.replace("internal:", "")
            if func_name == "detect_intent":
                result = _detect_chat_intent(input_data.get("message", ""), input_data.get("role", ""))
                elapsed = int((time.time() - start) * 1000)
                return result, elapsed
            return {"error": f"unknown internal function: {func_name}"}, 0

        elif action.startswith("workflow:"):
            # Nested workflow
            wf_name = action.replace("workflow:", "")
            result = _run_workflow(wf_name, input_data, tenant)
            elapsed = int((time.time() - start) * 1000)
            return result, elapsed

        elif action.startswith("search://"):
            # SearXNG 웹 검색
            from core.web_search import search as web_search
            query = input_data.get("query", "")
            if not query:
                return {"error": "query is required"}, 0
            result = web_search(
                query,
                category=input_data.get("category", "general"),
                language=input_data.get("language", "ko-KR"),
                max_results=input_data.get("max_results", 5),
            )
            elapsed = int((time.time() - start) * 1000)
            return result, elapsed

        else:
            return {"error": f"unknown action type: {action}"}, 0

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {"error": str(e)[:200]}, elapsed


# ---------------------------------------------------------------------------
# Workflow definitions
# ---------------------------------------------------------------------------

WORKFLOWS = {
    "quick_reception": {
        "name": "빠른 접수",
        "description": "증상 → 진료과 → 접수 (3단계)",
        "steps": [
            {"id": "triage", "action": "ai:/api/medical/triage",
             "input": {"symptom": "{{input.symptom}}", "age": "{{input.age}}", "sex": "{{input.sex}}"}},
            {"id": "register", "action": "ai:/api/medical/cli/execute",
             "input": {"role": "reception",
                       "command": "{{input.patient_name}} {{triage.recommendations[0].department}} 초진"},
             "depends_on": ["triage"]},
        ]
    },
    "full_consultation": {
        "name": "전체 진료",
        "description": "증상 분석 → 접수 → 요약 → 감별진단 → 참고 → 설명문",
        "steps": [
            {"id": "triage", "action": "ai:/api/medical/triage",
             "input": {"symptom": "{{input.symptom}}", "age": "{{input.age}}", "sex": "{{input.sex}}"}},
            {"id": "summarize", "action": "ai:/api/medical/summarize",
             "input": {"text": "{{input.clinical_note}}", "specialty": "{{triage.recommendations[0].department}}"},
             "depends_on": ["triage"], "optional": True,
             "condition": "{{input.clinical_note}}"},
            {"id": "analyze", "action": "ai:/api/medical/analyze",
             "input": {"symptoms": "{{input.symptoms}}", "age": "{{input.age}}", "sex": "{{input.sex}}"},
             "depends_on": ["triage"]},
            {"id": "reference", "action": "ai:/api/medical/reference",
             "input": {"question": "{{input.symptom}} 치료", "n_context": 3},
             "depends_on": ["triage"], "parallel_with": ["analyze"]},
            {"id": "explain", "action": "ai:/api/medical/explain",
             "input": {"diagnosis": "{{input.symptom}}", "patient_level": "{{input.patient_level || general}}"},
             "depends_on": ["analyze"], "optional": True},
        ]
    },
    "nurse_assessment": {
        "name": "간호 평가",
        "description": "바이탈 → 약물 확인 → 간호 기록",
        "steps": [
            {"id": "vitals", "action": "ai:/api/medical/cli/execute",
             "input": {"role": "nurse", "command": "{{input.patient_name}} {{input.vitals}}"}},
            {"id": "drug_check", "action": "ai:/api/medical/cli/execute",
             "input": {"role": "nurse", "command": "{{input.medications}} 상호작용"},
             "depends_on": ["vitals"], "optional": True,
             "condition": "{{input.medications}}"},
            {"id": "nursing_note", "action": "ai:/api/medical/draft",
             "input": {"type": "progress_note",
                       "data": {"chief_complaint": "{{input.chief_complaint}}",
                                "findings": "바이탈: {{input.vitals}}",
                                "assessment": "{{vitals.result.assessment.level || 정상}}"}},
             "depends_on": ["vitals"], "optional": True},
        ]
    },
    "bill_calculate": {
        "name": "진료비 계산",
        "description": "질환 → 코드 매핑 → 비용 계산",
        "steps": [
            {"id": "calc", "action": "ai:/api/medical/cli/execute",
             "input": {"role": "billing", "command": "{{input.disease}} 진료비 계산"}},
        ]
    },
    "claim_review": {
        "name": "청구 검증",
        "description": "코드 검증 → 문제 감지 → 수정 제안",
        "steps": [
            {"id": "validate", "action": "ai:/api/medical/cli/execute",
             "input": {"role": "claims", "command": "{{input.codes}} 청구 검증"}},
        ]
    },

    # ===== Phase 3: 블로그/콘텐츠 워크플로 =====

    "blog_write": {
        "name": "블로그 글 작성",
        "description": "RAG 참고 검색 → 초안 작성 → SEO 메타 생성",
        "domain": "blog",
        "steps": [
            {"id": "research", "action": "ai:/api/rag/ask",
             "input": {"question": "{{input.topic}}", "n_context": 5},
             "optional": True},
            {"id": "draft", "action": "ai:/api/writing/draft",
             "input": {
                 "prompt": "{{input.topic}}",
                 "tone": "{{input.tone || professional}}",
                 "structure": "{{input.structure || essay}}",
                 "length": "{{input.length || long}}",
                 "context": "{{research.answer || }}",
             },
             "depends_on": ["research"]},
            {"id": "seo", "action": "ai:/api/writing/seo",
             "input": {"text": "{{draft.draft}}"},
             "depends_on": ["draft"]},
        ]
    },
    "blog_edit": {
        "name": "블로그 글 교정",
        "description": "원문 → AI 교정/다듬기",
        "domain": "blog",
        "steps": [
            {"id": "polish", "action": "ai:/api/writing/polish",
             "input": {
                 "text": "{{input.text}}",
                 "instructions": "{{input.instructions || 문맥과 흐름을 자연스럽게 다듬어주세요}}",
             }},
        ]
    },
    "blog_seo": {
        "name": "SEO 최적화",
        "description": "글 분석 → SEO 메타/키워드/가독성 점수",
        "domain": "blog",
        "steps": [
            {"id": "seo", "action": "ai:/api/writing/seo",
             "input": {"text": "{{input.text}}"}},
            {"id": "evaluate", "action": "ai:/api/writing/evaluate",
             "input": {"text": "{{input.text}}"},
             "parallel_with": ["seo"]},
        ]
    },
    "blog_translate": {
        "name": "블로그 번역",
        "description": "한국어 글 → 지정 언어 번역",
        "domain": "blog",
        "steps": [
            {"id": "translate", "action": "llm:generate",
             "input": {
                 "prompt": "다음 한국어 글을 {{input.target_lang || English}}로 자연스럽게 번역하세요. 의미를 정확히 전달하면서 해당 언어의 관용적 표현을 사용하세요.\n\n{{input.text}}",
                 "system": "당신은 전문 번역가입니다. 원문의 톤과 구조를 유지하면서 자연스러운 번역을 제공합니다.",
                 "options": {"temperature": 0.3, "num_predict": 4000},
             }},
        ]
    },
    "blog_series": {
        "name": "시리즈 기획",
        "description": "주제 → 시리즈 구성 → 각 편 개요 작성",
        "domain": "blog",
        "steps": [
            {"id": "research", "action": "ai:/api/rag/ask",
             "input": {"question": "{{input.topic}} 주요 하위 주제", "n_context": 5},
             "optional": True},
            {"id": "plan", "action": "llm:generate",
             "input": {
                 "prompt": "\"{{input.topic}}\" 주제로 {{input.count || 5}}편 블로그 시리즈를 기획하세요.\n\n각 편의 제목, 핵심 키워드, 개요(200자), 예상 분량을 JSON 배열로 작성하세요.\n\n{{research.answer || }}",
                 "system": "당신은 콘텐츠 전략가입니다. 시리즈 글이 논리적으로 연결되도록 구성하고, SEO와 독자 관심을 고려합니다.",
                 "options": {"temperature": 0.5, "num_predict": 3000},
             },
             "depends_on": ["research"]},
        ]
    },
    "content_repurpose": {
        "name": "콘텐츠 재가공",
        "description": "원문 → SNS/요약/뉴스레터 등 형태 변환",
        "domain": "blog",
        "steps": [
            {"id": "repurpose", "action": "llm:generate",
             "input": {
                 "prompt": "다음 글을 {{input.format || SNS 포스트}} 형태로 변환하세요. 핵심 메시지를 유지하면서 해당 형식에 맞게 압축/재구성합니다.\n\n{{input.text}}",
                 "system": "당신은 콘텐츠 마케터입니다. 플랫폼별 최적 형식을 알고 있습니다.\n\n형식별 가이드:\n- SNS: 280자 이내, 해시태그 5개\n- 뉴스레터: 제목 + 요약 3줄 + CTA\n- 카드뉴스: 슬라이드별 제목+본문(50자)\n- 이메일: 제목 + 본문 + CTA 버튼 텍스트",
                 "options": {"temperature": 0.6, "num_predict": 1500},
             }},
        ]
    },

    # ===== Phase 4: 카페/커뮤니티 워크플로 =====

    "cafe_post": {
        "name": "카페 글 작성",
        "description": "주제 → AI 초안 → 카테고리 분류",
        "domain": "cafe",
        "steps": [
            {"id": "draft", "action": "ai:/api/writing/draft",
             "input": {
                 "prompt": "{{input.topic}}",
                 "tone": "{{input.tone || friendly}}",
                 "structure": "{{input.structure || essay}}",
                 "length": "{{input.length || medium}}",
                 "context": "카페/커뮤니티 게시글. 독자와 소통하는 톤으로 작성.",
             }},
            {"id": "categorize", "action": "llm:generate",
             "input": {
                 "prompt": "다음 글의 적절한 카테고리를 1개 선택하고, 태그 5개를 JSON으로 반환하세요.\n카테고리: [자유게시판, 질문답변, 정보공유, 후기, 공지]\n\n{{draft.draft}}",
                 "system": "JSON 형식: {\"category\": \"...\", \"tags\": [...]}",
                 "options": {"temperature": 0.2, "num_predict": 200},
             },
             "depends_on": ["draft"]},
        ]
    },
    "cafe_reply": {
        "name": "댓글/답변 생성",
        "description": "원문 분석 → 맥락에 맞는 답변 생성",
        "domain": "cafe",
        "steps": [
            {"id": "analyze", "action": "llm:generate",
             "input": {
                 "prompt": "다음 게시글을 분석하고 핵심 질문/주제를 파악하세요:\n\n{{input.post_content}}",
                 "system": "게시글의 핵심 질문, 감정 톤, 필요한 정보를 간결하게 정리하세요.",
                 "options": {"temperature": 0.3, "num_predict": 500},
             }},
            {"id": "reply", "action": "llm:generate",
             "input": {
                 "prompt": "위 분석을 바탕으로 {{input.reply_tone || 친절하고 도움이 되는}} 톤으로 답변을 작성하세요.\n\n원문 분석: {{analyze.response}}\n\n원문: {{input.post_content}}",
                 "system": "커뮤니티 답변 작성자입니다. 정확하고 친절하게 답변하되, 불확실한 정보는 명시합니다.",
                 "options": {"temperature": 0.5, "num_predict": 1500},
             },
             "depends_on": ["analyze"]},
        ]
    },
    "cafe_summary": {
        "name": "인기글 요약",
        "description": "게시글 목록 → AI 요약 → 트렌드 인사이트",
        "domain": "cafe",
        "steps": [
            {"id": "summarize", "action": "llm:generate",
             "input": {
                 "prompt": "다음 게시글 목록을 분석하여 핵심 내용을 요약하세요:\n\n{{input.posts}}",
                 "system": "각 글의 제목, 핵심 내용 1-2줄, 반응(댓글/좋아요)을 정리하세요. 표 형식으로 작성.",
                 "options": {"temperature": 0.3, "num_predict": 2000},
             }},
            {"id": "insights", "action": "llm:generate",
             "input": {
                 "prompt": "위 인기글에서 트렌드와 인사이트를 3가지 도출하고, 다음 주 추천 주제를 2가지 제안하세요.\n\n{{summarize.response}}",
                 "system": "커뮤니티 매니저 관점에서 분석합니다.",
                 "options": {"temperature": 0.5, "num_predict": 1000},
             },
             "depends_on": ["summarize"]},
        ]
    },
    "cafe_moderate": {
        "name": "콘텐츠 검수",
        "description": "게시글 → 커뮤니티 가이드라인 위반 확인",
        "domain": "cafe",
        "steps": [
            {"id": "check", "action": "llm:generate",
             "input": {
                 "prompt": "다음 게시글이 커뮤니티 가이드라인을 위반하는지 검수하세요:\n\n{{input.post_content}}",
                 "system": "검수 기준:\n1. 욕설/비속어\n2. 개인정보 노출 (전화번호, 주소, 주민번호)\n3. 스팸/광고성 콘텐츠\n4. 허위정보/의료 오정보\n5. 저작권 침해 의심\n\nJSON 형식: {\"safe\": true/false, \"violations\": [...], \"severity\": \"none/low/medium/high\", \"recommendation\": \"...\"}",
                 "options": {"temperature": 0.1, "num_predict": 500},
             }},
        ]
    },
    "cafe_announce": {
        "name": "공지 작성",
        "description": "주제 → 공지 초안 → 요약",
        "domain": "cafe",
        "steps": [
            {"id": "draft", "action": "ai:/api/writing/draft",
             "input": {
                 "prompt": "{{input.topic}}",
                 "tone": "formal",
                 "structure": "{{input.structure || list}}",
                 "length": "{{input.length || medium}}",
                 "context": "카페/커뮤니티 공지사항. 명확하고 간결하게.",
             }},
            {"id": "summary", "action": "llm:generate",
             "input": {
                 "prompt": "다음 공지를 한 줄(50자 이내)로 요약하세요:\n\n{{draft.draft}}",
                 "system": "알림 메시지에 사용할 한 줄 요약",
                 "options": {"temperature": 0.2, "num_predict": 100},
             },
             "depends_on": ["draft"]},
        ]
    },
    "cafe_wiki_update": {
        "name": "위키 보강",
        "description": "기존 위키 문서 → AI 보강/업데이트",
        "domain": "cafe",
        "steps": [
            {"id": "enhance", "action": "llm:generate",
             "input": {
                 "prompt": "다음 위키 문서를 보강하세요. 빠진 내용을 추가하고, 오래된 정보를 업데이트하며, 가독성을 개선하세요.\n\n제목: {{input.title}}\n\n{{input.content}}",
                 "system": "위키 편집자입니다. 원본 구조를 유지하면서 내용을 보강합니다. 추가/수정 부분은 [NEW] 또는 [UPDATED]로 표시합니다.",
                 "options": {"temperature": 0.4, "num_predict": 3000},
             }},
        ]
    },
    "cafe_faq": {
        "name": "FAQ 생성",
        "description": "질문 목록 수집 → AI 답변 생성",
        "domain": "cafe",
        "steps": [
            {"id": "collect", "action": "llm:generate",
             "input": {
                 "prompt": "\"{{input.topic}}\" 주제에 대해 자주 묻는 질문 {{input.count || 10}}개를 생성하세요.\n\n{{input.context || }}",
                 "system": "커뮤니티에서 실제로 자주 올라오는 질문 형태로 작성하세요. 초보자부터 경험자까지 포함.",
                 "options": {"temperature": 0.6, "num_predict": 1500},
             }},
            {"id": "answer", "action": "llm:generate",
             "input": {
                 "prompt": "다음 FAQ 질문들에 대해 정확하고 친절한 답변을 작성하세요:\n\n{{collect.response}}",
                 "system": "각 질문에 2-4문장으로 답변합니다. 불확실한 내용은 '확인이 필요합니다'로 표시. Q&A 형식으로.",
                 "options": {"temperature": 0.4, "num_predict": 3000},
             },
             "depends_on": ["collect"]},
        ]
    },

    # ===== Phase 5: 워크스페이스 워크플로 =====

    "todo_smart": {
        "name": "스마트 할일 정리",
        "description": "자연어 → TODO 항목 추출 → 우선순위 분류",
        "domain": "workspace",
        "steps": [
            {"id": "extract", "action": "llm:generate",
             "input": {
                 "prompt": "다음 텍스트에서 할일 항목을 추출하고 우선순위를 매기세요:\n\n{{input.text}}",
                 "system": "JSON 배열로 반환: [{\"task\": \"...\", \"priority\": \"high/medium/low\", \"due_hint\": \"...\", \"category\": \"...\"}]\n우선순위 기준: 긴급+중요=high, 긴급 or 중요=medium, 나머지=low",
                 "options": {"temperature": 0.2, "num_predict": 1500},
             }},
            {"id": "organize", "action": "llm:generate",
             "input": {
                 "prompt": "위 할일 목록을 카테고리별로 정리하고, 추천 실행 순서를 제안하세요:\n\n{{extract.response}}",
                 "system": "체크리스트 형식([ ] task)으로 정리합니다. 카테고리별 그룹핑 + 추천 순서 번호.",
                 "options": {"temperature": 0.3, "num_predict": 1000},
             },
             "depends_on": ["extract"]},
        ]
    },
    "calendar_smart": {
        "name": "스마트 일정 등록",
        "description": "자연어 → 일정 파싱 → 충돌 확인",
        "domain": "workspace",
        "steps": [
            {"id": "parse", "action": "llm:generate",
             "input": {
                 "prompt": "다음 텍스트에서 일정 정보를 추출하세요:\n\n{{input.text}}",
                 "system": "JSON 형식: {\"title\": \"...\", \"date\": \"YYYY-MM-DD\", \"time\": \"HH:MM\", \"duration_min\": 60, \"location\": \"...\", \"participants\": [...], \"reminder_min\": 30, \"notes\": \"...\"}\n날짜가 상대적(내일, 다음주 등)이면 오늘 기준으로 계산. 오늘: {{input.today}}",
                 "options": {"temperature": 0.1, "num_predict": 500},
             }},
        ]
    },
    "meeting_prep": {
        "name": "회의 준비",
        "description": "안건 → 참고자료 검색 → 브리핑 자료 생성",
        "domain": "workspace",
        "steps": [
            {"id": "research", "action": "ai:/api/rag/ask",
             "input": {"question": "{{input.agenda}}", "n_context": 5},
             "optional": True},
            {"id": "briefing", "action": "llm:generate",
             "input": {
                 "prompt": "다음 회의 안건에 대한 브리핑 자료를 작성하세요.\n\n안건: {{input.agenda}}\n참석자: {{input.participants || 미정}}\n목적: {{input.purpose || 논의}}\n\n참고자료:\n{{research.answer || (없음)}}",
                 "system": "회의 브리핑 자료를 작성합니다:\n1. 안건별 핵심 포인트 (3줄 이내)\n2. 논의 필요 사항\n3. 의사결정 필요 항목\n4. 참고 데이터/자료\n간결하고 실행 가능한 형태로.",
                 "options": {"temperature": 0.4, "num_predict": 2000},
             },
             "depends_on": ["research"]},
        ]
    },
    "meeting_summary": {
        "name": "회의록 정리",
        "description": "회의 내용 → 요약 → 할일 추출",
        "domain": "workspace",
        "steps": [
            {"id": "summarize", "action": "llm:generate",
             "input": {
                 "prompt": "다음 회의 내용을 정리하세요:\n\n{{input.meeting_text}}",
                 "system": "회의록 형식:\n## 회의 요약\n- 일시/참석자/주제\n\n## 핵심 안건\n1. ...\n\n## 결정 사항\n- ...\n\n## 할일 (Action Items)\n- [ ] 담당자: 내용 (기한)\n\n## 다음 회의\n- 예정 일시/안건",
                 "options": {"temperature": 0.3, "num_predict": 2500},
             }},
            {"id": "todos", "action": "llm:generate",
             "input": {
                 "prompt": "위 회의록에서 할일을 JSON 배열로 추출하세요:\n\n{{summarize.response}}",
                 "system": "형식: [{\"task\": \"...\", \"assignee\": \"...\", \"due_date\": \"...\", \"priority\": \"high/medium/low\"}]",
                 "options": {"temperature": 0.1, "num_predict": 1000},
             },
             "depends_on": ["summarize"]},
        ]
    },
    "daily_report": {
        "name": "일일 업무 보고서",
        "description": "업무 내용 → 구조화된 일일 보고서",
        "domain": "workspace",
        "steps": [
            {"id": "report", "action": "llm:generate",
             "input": {
                 "prompt": "다음 업무 내용을 일일 보고서로 정리하세요:\n\n{{input.work_content}}",
                 "system": "일일 업무 보고서 형식:\n\n## {{input.date || 오늘}} 업무 보고\n\n### 완료\n- ...\n\n### 진행 중\n- ... (진행률 %)\n\n### 예정\n- ...\n\n### 이슈/블로커\n- ...\n\n### 내일 계획\n- ...",
                 "options": {"temperature": 0.3, "num_predict": 1500},
             }},
        ]
    },
    "file_organize": {
        "name": "파일 분류 제안",
        "description": "파일 목록 → AI 분류 → 폴더 구조 제안",
        "domain": "workspace",
        "steps": [
            {"id": "classify", "action": "llm:generate",
             "input": {
                 "prompt": "다음 파일 목록을 분석하고 폴더 분류를 제안하세요:\n\n{{input.files}}",
                 "system": "파일명/확장자/크기를 분석하여 논리적 폴더 구조를 제안합니다.\nJSON: {\"folders\": [{\"name\": \"...\", \"files\": [...], \"reason\": \"...\"}], \"duplicates\": [...], \"cleanup_suggestions\": [...]}",
                 "options": {"temperature": 0.3, "num_predict": 2000},
             }},
        ]
    },

    # ===== Phase 6: 결재/휴가 + HIS 확장 =====

    "smart_approval": {
        "name": "스마트 결재",
        "description": "결재 요청 분석 → 규정 검토 → 승인/반려 근거 생성",
        "domain": "his",
        "steps": [
            {"id": "analyze", "action": "llm:generate",
             "input": {
                 "prompt": "다음 결재 요청을 분석하세요:\n\n종류: {{input.type}}\n제목: {{input.title}}\n내용: {{input.content}}\n금액: {{input.amount || -}}\n요청자: {{input.requester}}",
                 "system": "결재 검토자입니다. 다음을 분석합니다:\n1. 요청 적정성\n2. 관련 규정/전례\n3. 예산 영향\n4. 리스크 요인\n5. 승인/보류/반려 권고 + 근거\n\nJSON: {\"recommendation\": \"approve/hold/reject\", \"reason\": \"...\", \"risks\": [...], \"conditions\": [...]}",
                 "options": {"temperature": 0.2, "num_predict": 1000},
             }},
        ]
    },
    "leave_request": {
        "name": "휴가 신청 도우미",
        "description": "자연어 → 휴가 양식 생성 → 근로기준법 확인",
        "domain": "his",
        "steps": [
            {"id": "parse", "action": "llm:generate",
             "input": {
                 "prompt": "다음 휴가 요청에서 정보를 추출하세요:\n\n{{input.text}}",
                 "system": "JSON 형식: {\"type\": \"연차/반차/병가/경조사/특별\", \"start_date\": \"YYYY-MM-DD\", \"end_date\": \"YYYY-MM-DD\", \"days\": N, \"reason\": \"...\", \"handover\": \"...\"}\n오늘: {{input.today}}",
                 "options": {"temperature": 0.1, "num_predict": 300},
             }},
            {"id": "check_law", "action": "ai:/api/hr-law/ask",
             "input": {"question": "{{parse.response}} 관련 근로기준법 규정", "category": "leave"},
             "depends_on": ["parse"], "optional": True},
            {"id": "form", "action": "llm:generate",
             "input": {
                 "prompt": "위 정보로 휴가 신청서를 작성하세요.\n\n파싱 결과: {{parse.response}}\n관련 규정: {{check_law.answer || (확인 불가)}}",
                 "system": "정식 휴가 신청서 형식으로 작성합니다. 인수인계 사항과 관련 법적 근거를 포함.",
                 "options": {"temperature": 0.3, "num_predict": 1000},
             },
             "depends_on": ["parse", "check_law"]},
        ]
    },
    "discharge": {
        "name": "퇴원 처리",
        "description": "퇴원 요약 → 투약 안내 → 환자 설명문",
        "domain": "his",
        "steps": [
            {"id": "summary", "action": "ai:/api/medical/summarize",
             "input": {"text": "{{input.clinical_note}}", "specialty": "{{input.specialty || 내과}}"}},
            {"id": "medication", "action": "ai:/api/medical/cli/execute",
             "input": {"role": "nurse", "command": "{{input.medications}} 투약 안내"},
             "depends_on": ["summary"], "optional": True,
             "condition": "{{input.medications}}"},
            {"id": "explain", "action": "ai:/api/medical/explain",
             "input": {"diagnosis": "{{input.diagnosis}}", "patient_level": "general"},
             "depends_on": ["summary"]},
        ]
    },
    "schedule_management": {
        "name": "진료 일정 관리",
        "description": "자연어 → 일정 파싱 → 충돌 확인",
        "domain": "his",
        "steps": [
            {"id": "parse", "action": "llm:generate",
             "input": {
                 "prompt": "다음 진료 일정 요청을 파싱하세요:\n\n{{input.text}}",
                 "system": "JSON: {\"patient_name\": \"...\", \"department\": \"...\", \"doctor\": \"...\", \"date\": \"YYYY-MM-DD\", \"time\": \"HH:MM\", \"duration_min\": 30, \"type\": \"초진/재진/검사/수술\", \"notes\": \"...\"}\n오늘: {{input.today}}",
                 "options": {"temperature": 0.1, "num_predict": 400},
             }},
        ]
    },
    "morning_round": {
        "name": "조회/회진 준비",
        "description": "입원 환자 목록 → 상태 요약 → 회진 브리핑",
        "domain": "his",
        "steps": [
            {"id": "briefing", "action": "llm:generate",
             "input": {
                 "prompt": "다음 입원 환자 정보로 회진 브리핑 자료를 작성하세요:\n\n{{input.patients}}",
                 "system": "회진 브리핑 형식:\n환자별:\n- 이름/나이/입원일/진단명\n- 주요 검사 결과\n- 현재 투약\n- 주의 사항\n- 오늘 계획\n\n우선 회진 순서: 중증도 기준 정렬",
                 "options": {"temperature": 0.3, "num_predict": 3000},
             }},
        ]
    },

    # ===== Phase 7: 크로스도메인 워크플로 =====

    "health_campaign": {
        "name": "건강 캠페인",
        "description": "의료 참고 → 블로그 글 → 환자 설명문 (크로스도메인)",
        "domain": "cross",
        "steps": [
            {"id": "research", "action": "ai:/api/medical/reference",
             "input": {"question": "{{input.health_topic}} 예방법과 관리", "n_context": 5}},
            {"id": "blog", "action": "ai:/api/writing/draft",
             "input": {
                 "prompt": "{{input.health_topic}} 예방법과 관리 가이드",
                 "tone": "friendly",
                 "length": "long",
                 "context": "건강 캠페인용 블로그 글.\n\n참고자료:\n{{research.answer}}",
             },
             "depends_on": ["research"]},
            {"id": "leaflet", "action": "ai:/api/medical/explain",
             "input": {"diagnosis": "{{input.health_topic}}", "patient_level": "general"},
             "parallel_with": ["blog"]},
            {"id": "seo", "action": "ai:/api/writing/seo",
             "input": {"text": "{{blog.draft}}"},
             "depends_on": ["blog"]},
        ]
    },
    "patient_education": {
        "name": "환자 교육 자료",
        "description": "질환 → 의료 설명문 + 블로그 게시용 + 요약 카드",
        "domain": "cross",
        "steps": [
            {"id": "explain", "action": "ai:/api/medical/explain",
             "input": {"diagnosis": "{{input.topic}}", "patient_level": "{{input.level || general}}"}},
            {"id": "article", "action": "ai:/api/writing/draft",
             "input": {
                 "prompt": "{{input.topic}} 환자 교육 자료",
                 "tone": "friendly",
                 "structure": "list",
                 "length": "medium",
                 "context": "환자가 이해하기 쉬운 교육 자료.\n\n의료 설명:\n{{explain.explanation}}",
             },
             "depends_on": ["explain"]},
            {"id": "card", "action": "llm:generate",
             "input": {
                 "prompt": "위 교육 자료를 카드뉴스 형태(5장)로 재구성하세요.\n\n{{article.draft}}",
                 "system": "슬라이드 형식: [제목 + 핵심 포인트 2줄 + 아이콘 제안] x 5장",
                 "options": {"temperature": 0.5, "num_predict": 1500},
             },
             "depends_on": ["article"]},
        ]
    },
    "weekly_digest": {
        "name": "주간 다이제스트",
        "description": "진료 통계 + 인기글 + 업무 현황 → 통합 보고서",
        "domain": "cross",
        "steps": [
            {"id": "medical_stats", "action": "llm:generate",
             "input": {
                 "prompt": "다음 진료 통계를 요약하세요:\n\n{{input.medical_data || (데이터 없음)}}",
                 "system": "주간 진료 통계 요약: 진료 건수, 주요 질환, 특이사항을 간결하게.",
                 "options": {"temperature": 0.3, "num_predict": 800},
             }},
            {"id": "community", "action": "llm:generate",
             "input": {
                 "prompt": "다음 커뮤니티 활동을 요약하세요:\n\n{{input.community_data || (데이터 없음)}}",
                 "system": "주간 커뮤니티 활동: 인기글, 신규 회원, 주요 토론 요약.",
                 "options": {"temperature": 0.3, "num_predict": 800},
             },
             "parallel_with": ["medical_stats"]},
            {"id": "digest", "action": "llm:generate",
             "input": {
                 "prompt": "다음 섹션들을 하나의 주간 다이제스트로 통합하세요:\n\n## 진료\n{{medical_stats.response}}\n\n## 커뮤니티\n{{community.response}}\n\n## 업무\n{{input.work_data || (데이터 없음)}}",
                 "system": "주간 다이제스트 형식:\n# 주간 다이제스트 ({{input.week || 이번 주}})\n\n핵심 지표 → 진료 → 커뮤니티 → 업무 → 다음 주 포커스\n간결하고 액션 지향적으로.",
                 "options": {"temperature": 0.4, "num_predict": 2000},
             },
             "depends_on": ["medical_stats", "community"]},
        ]
    },
    "onboarding": {
        "name": "신규 직원 온보딩",
        "description": "직원 정보 → 체크리스트 → 가이드 문서 → 환영 메시지",
        "domain": "cross",
        "steps": [
            {"id": "checklist", "action": "llm:generate",
             "input": {
                 "prompt": "{{input.department}} 부서 {{input.position}} 직급 신규 직원({{input.name}})의 온보딩 체크리스트를 작성하세요.",
                 "system": "온보딩 체크리스트:\n## 첫째 주\n- [ ] 시스템 계정/접근권한\n- [ ] 부서 소개/팀원 인사\n- [ ] 업무 도구 설정\n\n## 둘째 주\n- [ ] 업무 프로세스 교육\n- [ ] 멘토 배정\n\n## 첫 달\n- [ ] 독립 업무 시작\n- [ ] 피드백 면담\n\n부서와 직급에 맞게 커스터마이즈.",
                 "options": {"temperature": 0.4, "num_predict": 2000},
             }},
            {"id": "guide", "action": "llm:generate",
             "input": {
                 "prompt": "{{input.department}} 부서 신규 직원을 위한 가이드 문서를 작성하세요.\n\n부서 정보: {{input.dept_info || (일반)}}\n주요 업무: {{input.responsibilities || (미정)}}",
                 "system": "신규 직원 가이드:\n1. 부서 소개\n2. 주요 업무 프로세스\n3. 자주 사용하는 시스템/도구\n4. 연락처/담당자\n5. FAQ",
                 "options": {"temperature": 0.4, "num_predict": 2500},
             },
             "parallel_with": ["checklist"]},
            {"id": "welcome", "action": "llm:generate",
             "input": {
                 "prompt": "{{input.name}}님 환영 메시지를 작성하세요. {{input.department}} 부서 {{input.position}} 직급.",
                 "system": "따뜻하고 전문적인 환영 메시지. 50-100자. 카페/사내 메신저용.",
                 "options": {"temperature": 0.7, "num_predict": 200},
             },
             "depends_on": ["checklist"]},
        ]
    },
}


# ---------------------------------------------------------------------------
# Workflow engine
# ---------------------------------------------------------------------------

def _run_workflow(workflow_name, input_data, tenant):
    """Execute a workflow with context pipeline"""
    all_wf = _get_all_workflows()
    wf = all_wf.get(workflow_name)
    if not wf:
        return {"error": f"workflow '{workflow_name}' not found"}

    run_id = f"orch-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}"
    steps = wf["steps"]
    context = {"input": input_data}
    results = []
    total_start = time.time()

    # Save run
    db = _get_orch_db()
    db.execute(
        "INSERT INTO orchestrator_runs (id, tenant_id, workflow, input, steps_total) VALUES (?,?,?,?,?)",
        (run_id, tenant["id"] if tenant else None, workflow_name, json.dumps(input_data, ensure_ascii=False), len(steps)))
    db.commit()

    completed = 0
    for step in steps:
        step_id = step["id"]
        action = step["action"]

        # Check condition
        condition = step.get("condition")
        if condition:
            resolved = _resolve_template(condition, context)
            if not resolved or resolved == '' or resolved == 'None':
                results.append({"id": step_id, "status": "skipped", "reason": "condition not met"})
                continue

        # Resolve input templates
        step_input = _resolve_template(step.get("input", {}), context)

        # Execute
        result, duration_ms = _execute_action(action, step_input, tenant)

        status = "completed" if result and "error" not in result else "failed"
        if status == "failed" and step.get("optional"):
            status = "skipped"

        # Log step
        db.execute(
            "INSERT INTO orchestrator_step_logs (run_id, step_id, action, status, duration_ms, error) VALUES (?,?,?,?,?,?)",
            (run_id, step_id, action, status, duration_ms, (result or {}).get("error") if status == "failed" else None))
        db.commit()

        # Store result in context
        if result and status == "completed":
            result = force_korean_json(result)
        context[step_id] = result or {}
        completed += 1

        results.append({
            "id": step_id,
            "status": status,
            "duration_ms": duration_ms,
            "result": result if status == "completed" else None,
            "error": (result or {}).get("error") if status == "failed" else None,
        })

        # Update progress
        db.execute("UPDATE orchestrator_runs SET steps_completed=? WHERE id=?", (completed, run_id))
        db.commit()

        # Abort on required step failure
        if status == "failed" and not step.get("optional"):
            break

    total_ms = int((time.time() - total_start) * 1000)

    # Summary
    summary = {
        "workflow": workflow_name,
        "workflow_name": wf["name"],
        "total_steps": len(steps),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "total_duration_ms": total_ms,
    }

    final_status = "completed" if summary["failed"] == 0 else "partial"

    # Update run
    db.execute(
        "UPDATE orchestrator_runs SET status=?, result=?, total_duration_ms=?, completed_at=datetime('now') WHERE id=?",
        (final_status, json.dumps({"steps": results, "summary": summary}, ensure_ascii=False)[:5000], total_ms, run_id))
    db.commit()

    return {
        "run_id": run_id,
        "workflow": workflow_name,
        "status": final_status,
        "steps": results,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Chat intent detection
# ---------------------------------------------------------------------------

CHAT_INTENTS = {
    "reception": {"patterns": ["접수", "초진", "재진", "등록"], "workflow": "quick_reception"},
    "consultation": {"patterns": ["진료", "분석", "감별", "진단", "검사"], "workflow": "full_consultation"},
    "triage": {"patterns": ["아프", "아파", "증상", "어디가", "통증", "진료과"], "workflow": None},
    "vitals": {"patterns": ["바이탈", "혈압", "체온", "활력"], "workflow": "nurse_assessment"},
    "billing": {"patterns": ["진료비", "수납", "계산", "비용", "얼마"], "workflow": "bill_calculate"},
    "claims": {"patterns": ["청구", "삭감", "검증", "이의"], "workflow": "claim_review"},
    "drug": {"patterns": ["약", "처방", "상호작용", "복용"], "workflow": None},
    "schedule": {"patterns": ["진료 일정", "예약 잡아", "진료 예약"], "workflow": "schedule_management"},
    "document": {"patterns": ["문서", "보고서"], "workflow": None},
    "leave": {"patterns": ["휴가", "연차", "반차", "병가"], "workflow": "leave_request"},
    "approval": {"patterns": ["결재", "승인", "품의"], "workflow": "smart_approval"},
    "discharge": {"patterns": ["퇴원", "퇴원 처리", "퇴원 요약"], "workflow": "discharge"},
    "morning_round": {"patterns": ["회진", "조회", "입원 환자"], "workflow": "morning_round"},
    "health_campaign": {"patterns": ["캠페인", "건강 캠페인", "홍보"], "workflow": "health_campaign"},
    "patient_education": {"patterns": ["교육 자료", "환자 교육", "설명 자료"], "workflow": "patient_education"},
    "weekly_digest": {"patterns": ["주간 보고", "주간 다이제스트", "위클리"], "workflow": "weekly_digest"},
    "onboarding": {"patterns": ["온보딩", "신규 직원", "입사"], "workflow": "onboarding"},
    "blog": {"patterns": ["블로그", "글 써", "포스트", "글 작성"], "workflow": "blog_write"},
    "blog_edit": {"patterns": ["다듬", "교정", "퇴고", "수정해"], "workflow": "blog_edit"},
    "blog_seo": {"patterns": ["SEO", "최적화", "키워드 분석", "메타"], "workflow": "blog_seo"},
    "blog_translate": {"patterns": ["번역", "영어로", "translate"], "workflow": "blog_translate"},
    "blog_series": {"patterns": ["시리즈", "연재", "기획"], "workflow": "blog_series"},
    "content_repurpose": {"patterns": ["SNS", "재가공", "카드뉴스", "뉴스레터"], "workflow": "content_repurpose"},
    "cafe_post": {"patterns": ["카페", "게시판", "커뮤니티", "글 올려"], "workflow": "cafe_post"},
    "cafe_reply": {"patterns": ["답변 달", "댓글", "답글"], "workflow": "cafe_reply"},
    "cafe_summary": {"patterns": ["인기글", "요약해", "주간 요약"], "workflow": "cafe_summary"},
    "cafe_moderate": {"patterns": ["검수", "모더레이션", "위반"], "workflow": "cafe_moderate"},
    "cafe_announce": {"patterns": ["공지", "공지사항", "안내문"], "workflow": "cafe_announce"},
    "cafe_faq": {"patterns": ["FAQ", "자주 묻는", "질문답변"], "workflow": "cafe_faq"},
    "todo": {"patterns": ["할일", "투두", "TODO", "할 일"], "workflow": "todo_smart"},
    "calendar": {"patterns": ["일정", "예약", "스케줄", "캘린더"], "workflow": "calendar_smart"},
    "meeting_prep": {"patterns": ["회의 준비", "브리핑", "안건"], "workflow": "meeting_prep"},
    "meeting_summary": {"patterns": ["회의록", "미팅 정리", "회의 요약"], "workflow": "meeting_summary"},
    "daily_report": {"patterns": ["업무 보고", "일일 보고", "오늘 정리"], "workflow": "daily_report"},
    "file_organize": {"patterns": ["파일 정리", "폴더 분류", "파일 분류"], "workflow": "file_organize"},
}


def _detect_chat_intent(message, role=""):
    """Detect intent from natural language message (캐시 적용)"""
    _ck = hashlib.md5((message[:200] + role).encode("utf-8", errors="replace")).hexdigest()
    cached = _intent_cache.get(_ck)
    if cached is not None:
        return cached
    scores = {}
    for intent, config in CHAT_INTENTS.items():
        score = 0
        for pattern in config["patterns"]:
            if pattern in message:
                score += 10
        if score > 0:
            scores[intent] = score

    if scores:
        best = max(scores, key=scores.get)
        confidence = min(scores[best] / max(sum(scores.values()), 1), 1.0)
        result = {
            "intent": best,
            "confidence": round(confidence, 2),
            "workflow": CHAT_INTENTS[best].get("workflow"),
        }
        _intent_cache.set(_ck, result)
        return result

    result = {"intent": "general", "confidence": 0.5, "workflow": None}
    _intent_cache.set(_ck, result)
    return result


from core.medical_router import medical_direct_route as _medical_direct_route  # noqa: E402
from core.medical_router import summarize_medical_route as _summarize_medical_route  # noqa: E402


def _extract_entities_from_chat(message):
    """Extract entities from chat message for workflow input"""
    entities = {}
    # Name (2-4 Korean chars)
    name_match = re.search(r'([가-힯]{2,4})\s', message)
    if name_match:
        entities["patient_name"] = name_match.group(1)

    # Birth (6 digits)
    birth_match = re.search(r'\b(\d{6})\b', message)
    if birth_match:
        entities["birth"] = birth_match.group(1)

    # Age
    age_match = re.search(r'(\d{1,3})\s*[세살]', message)
    if age_match:
        entities["age"] = int(age_match.group(1))

    # Symptom keywords
    symptom_parts = []
    for kw in ["아프", "아파", "통증", "열", "기침", "두통", "복통", "어지러"]:
        if kw in message:
            symptom_parts.append(kw)
    if symptom_parts:
        entities["symptom"] = message  # Use full message as symptom

    # Vitals
    bp_match = re.search(r'(\d{2,3})/(\d{2,3})', message)
    if bp_match:
        entities["vitals"] = message

    # Blog/Content entities
    # Topic: extract after trigger keywords
    for trigger in ["에 대해", "주제로", "관련", "에 관한"]:
        idx = message.find(trigger)
        if idx > 0:
            entities["topic"] = message[:idx].strip()
            break
    if "topic" not in entities and any(kw in message for kw in ["블로그", "글 써", "시리즈"]):
        # Use the full message as topic (cleaned)
        cleaned = message
        for kw in ["블로그", "글 써줘", "글 작성", "시리즈 기획", "에 올려", "해줘", "해 줘"]:
            cleaned = cleaned.replace(kw, "")
        cleaned = cleaned.strip()
        if cleaned:
            entities["topic"] = cleaned

    # Tone
    for tone_kw, tone_val in [("전문", "professional"), ("친근", "friendly"),
                               ("공식", "formal"), ("캐주얼", "casual"),
                               ("학술", "academic"), ("마케팅", "marketing")]:
        if tone_kw in message:
            entities["tone"] = tone_val
            break

    # Target language
    for lang_kw, lang_val in [("영어", "English"), ("일본어", "Japanese"),
                               ("중국어", "Chinese"), ("스페인어", "Spanish")]:
        if lang_kw in message:
            entities["target_lang"] = lang_val
            break

    # Text (for edit/translate/repurpose — if quoted)
    quoted = re.search(r'[""「](.+?)[""」]', message)
    if quoted:
        entities["text"] = quoted.group(1)

    # Workspace: date context
    from datetime import date as _date
    entities["today"] = _date.today().isoformat()

    # Meeting text (after "회의록" or "정리")
    for kw in ["회의록:", "회의 내용:", "미팅:"]:
        idx = message.find(kw)
        if idx >= 0:
            entities["meeting_text"] = message[idx + len(kw):].strip()
            break

    # Work content (after "업무" keywords)
    for kw in ["업무 내용:", "오늘 한 일:", "업무:"]:
        idx = message.find(kw)
        if idx >= 0:
            entities["work_content"] = message[idx + len(kw):].strip()
            break

    return entities


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@orchestrator_bp.route("/api/orchestrator/run", methods=["POST"])
def orch_run():
    """Execute a workflow
    ---
    tags: [Orchestrator]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [workflow]
          properties:
            workflow: {type: string, description: "워크플로우 이름"}
            input: {type: object, description: "워크플로우 입력"}
    responses:
      200:
        description: 워크플로우 실행 결과
    """
    tenant = _resolve_medical_tenant(request) if _resolve_medical_tenant else None
    data = request.json or {}
    workflow = data.get("workflow", "")
    input_data = data.get("input", {})

    all_wf = _get_all_workflows()
    if workflow not in all_wf:
        return jsonify({"error": f"unknown workflow: {workflow}", "available": list(all_wf.keys())}), 400

    result = _run_workflow(workflow, input_data, tenant)
    return jsonify(result)


@orchestrator_bp.route("/api/orchestrator/run/stream", methods=["POST"])
def orch_run_stream():
    """Execute a workflow with SSE streaming
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
    """
    tenant = _resolve_medical_tenant(request) if _resolve_medical_tenant else None
    data = request.json or {}
    workflow_name = data.get("workflow", "")
    input_data = data.get("input", {})

    all_wf = _get_all_workflows()
    wf = all_wf.get(workflow_name)
    if not wf:
        return jsonify({"error": f"unknown workflow: {workflow_name}"}), 400

    def generate():
        context = {"input": input_data}
        for step in wf["steps"]:
            step_id = step["id"]
            yield json.dumps({"step": step_id, "status": "running"}) + "\n"

            step_input = _resolve_template(step.get("input", {}), context)
            result, duration_ms = _execute_action(step["action"], step_input, tenant)

            status = "completed" if result and "error" not in result else "failed"
            if result and status == "completed":
                result = force_korean_json(result)
            context[step_id] = result or {}

            yield json.dumps({
                "step": step_id, "status": status,
                "duration_ms": duration_ms,
                "result": result if status == "completed" else None,
            }) + "\n"

        yield json.dumps({"status": "completed"}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@orchestrator_bp.route("/api/orchestrator/chat", methods=["POST"])
def orch_chat():
    """Smart chat — natural language to workflow auto-selection
    ---
    tags: [Orchestrator]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string}
            session_id: {type: string}
            context: {type: object}
    responses:
      200:
        description: 채팅 응답
    """
    tenant = _resolve_medical_tenant(request) if _resolve_medical_tenant else None
    data = request.json or {}
    message = data.get("message", "").strip()
    session_id = data.get("session_id")
    user_context = data.get("context", {})

    if not message:
        return jsonify({"error": "message is required"}), 400

    if not session_id:
        session_id = str(uuid.uuid4())[:12]

    # P2-2: 세션 히스토리 로드
    history = _get_session_history(session_id)
    context_prefix = _build_context_from_history(history)

    # 0. 의료 정본 도구 결정적 우선 라우팅 (LLM 불확실성·일반 워크플로우 가로채기 우회)
    _md_route = _medical_direct_route(message)
    if _md_route:
        _tool, _action, _inp = _md_route
        try:
            _res, _ms = _execute_action(_action, _inp, tenant)
        except Exception as _e:  # noqa: BLE001
            _res = {"error": str(_e)[:160]}
        _msg = force_korean(_summarize_medical_route(_tool, _res))
        _save_session_turn(session_id, message, _msg, "medical_direct")
        return jsonify({"type": "tool_calling", "intent": "medical_direct", "tool": _tool,
                        "tool_calls": [{"name": _tool}], "tool_result": _res,
                        "message": _msg, "session_id": session_id})

    # 1. Detect intent
    intent_result = _detect_chat_intent(message, user_context.get("role", ""))
    intent = intent_result["intent"]
    workflow_name = intent_result.get("workflow")

    # 2. Extract entities
    entities = _extract_entities_from_chat(message)

    # 3. Execute workflow or LLM direct
    all_wf = _get_all_workflows()
    if workflow_name and workflow_name in all_wf:
        wf_input = {**entities, **user_context}
        if "symptom" not in wf_input:
            wf_input["symptom"] = message

        wf_result = _run_workflow(workflow_name, wf_input, tenant)
        wf_result = force_korean_json(wf_result)
        summary_text = force_korean(_generate_chat_summary(workflow_name, wf_result))

        # P2-2: 세션에 턴 저장
        _save_session_turn(session_id, message, summary_text, intent)

        return jsonify({
            "type": "workflow",
            "intent": intent,
            "confidence": intent_result["confidence"],
            "workflow": workflow_name,
            "workflow_result": wf_result,
            "message": summary_text,
            "suggestions": _get_suggestions(intent),
            "session_id": session_id,
        })

    elif intent == "triage":
        triage_input = {"symptom": message}
        if entities.get("age"):
            triage_input["age"] = entities["age"]
        result, _ = _execute_action("ai:/api/medical/triage", triage_input, tenant)

        recs = (result or {}).get("recommendations", [])
        dept = recs[0]["department"] if recs else "내과"
        msg = f"증상 분석 결과, {dept} 진료를 추천합니다."
        if (result or {}).get("emergency_warning"):
            msg = f"⚠️ {result['emergency_warning']['message']}"

        _save_session_turn(session_id, message, msg, intent)

        return jsonify({
            "type": "triage",
            "intent": intent,
            "message": msg,
            "result": result,
            "suggestions": [f"{dept} 접수", "다른 증상 추가", "자세한 설명"],
            "session_id": session_id,
        })

    else:
        # Tool Calling 폴백 시도 (intent 미매칭, qwen3:8b)
        if _tool_registry and len(_tool_registry) > 0:
            from core.tool_executor import run_tool_calling_chat
            tc_result = run_tool_calling_chat(
                message,
                ollama_session=_ollama_session,
                ollama_base=_OLLAMA_BASE,
                registry=_tool_registry,
                execute_action_fn=_execute_action,
                tenant=tenant,
                log_fn=lambda name, args, result, ms: _log_tool_call(session_id, name, args, result, ms),
            )
            if tc_result:
                _save_session_turn(session_id, message, tc_result["message"], "tool_calling")
                return jsonify({
                    **tc_result,
                    "intent": "tool_calling",
                    "suggestions": _get_suggestions(intent),
                    "session_id": session_id,
                })

        # General LLM chat 폴백 (모델/옵션은 config/tools.json 의 general_chat 블록)
        _gc = _general_chat_cfg
        system = _gc["system"]
        if context_prefix:
            system += context_prefix

        try:
            from core import model_router
            _res = model_router.generate(
                "orchestrator.general_chat", message, model=_gc["model"], system=system,
                options={"temperature": _gc["temperature"],
                         "num_predict": _gc["num_predict"]},
                timeout=_gc["timeout"],
                adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE,
                                                   session=_ollama_session))
            if not _res.ok:
                # v2.125.16: HTTP 오류 → 아래 안내문. 종전엔 **빈 응답이 사용자에게 나가고
                # 세션에도 그대로 저장**됐다(200인데 빈 결과 클래스).
                raise RuntimeError(_res.error or ("HTTP %s" % _res.status))
            response = force_korean(_res.text)
        except Exception:
            response = "죄송합니다. 잠시 후 다시 시도해주세요."

        _save_session_turn(session_id, message, response, intent)

        return jsonify({
            "type": "chat",
            "intent": intent,
            "message": response,
            "suggestions": ["진료 접수", "증상 상담", "진료비 계산"],
            "session_id": session_id,
        })


def _run_workflow_streaming(workflow_name, input_data, tenant):
    """Execute a workflow yielding NDJSON events per step"""
    all_wf = _get_all_workflows()
    wf = all_wf.get(workflow_name)
    if not wf:
        yield json.dumps({"event": "error", "message": f"workflow '{workflow_name}' not found"}) + "\n"
        return

    run_id = f"orch-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}"
    steps = wf["steps"]
    context = {"input": input_data}

    yield json.dumps({
        "event": "start",
        "run_id": run_id,
        "workflow": workflow_name,
        "workflow_name": wf["name"],
        "total_steps": len(steps),
    }) + "\n"

    db = _get_orch_db()
    db.execute(
        "INSERT INTO orchestrator_runs (id, tenant_id, workflow, input, steps_total) VALUES (?,?,?,?,?)",
        (run_id, tenant["id"] if tenant else None, workflow_name,
         json.dumps(input_data, ensure_ascii=False), len(steps)))
    db.commit()

    completed = 0
    total_start = time.time()

    for i, step in enumerate(steps):
        step_id = step["id"]

        # Check condition
        condition = step.get("condition")
        if condition:
            resolved = _resolve_template(condition, context)
            if not resolved or resolved == '' or resolved == 'None':
                yield json.dumps({"event": "step", "step": step_id, "index": i, "status": "skipped"}) + "\n"
                continue

        yield json.dumps({
            "event": "step",
            "step": step_id,
            "index": i,
            "status": "running",
            "action": step["action"].split(":")[0],
        }) + "\n"

        step_input = _resolve_template(step.get("input", {}), context)
        result, duration_ms = _execute_action(step["action"], step_input, tenant)

        status = "completed" if result and "error" not in result else "failed"
        if status == "failed" and step.get("optional"):
            status = "skipped"

        db.execute(
            "INSERT INTO orchestrator_step_logs (run_id, step_id, action, status, duration_ms, error) VALUES (?,?,?,?,?,?)",
            (run_id, step_id, step["action"], status, duration_ms,
             (result or {}).get("error") if status == "failed" else None))
        db.commit()

        if result and status == "completed":
            result = force_korean_json(result)
        context[step_id] = result or {}
        completed += 1

        # Emit step result (truncate large outputs)
        step_result = result if status == "completed" else None
        if step_result:
            # Truncate long text fields for streaming
            step_result = {k: (v[:500] + "..." if isinstance(v, str) and len(v) > 500 else v)
                          for k, v in step_result.items()}

        yield json.dumps({
            "event": "step",
            "step": step_id,
            "index": i,
            "status": status,
            "duration_ms": duration_ms,
            "preview": step_result,
        }) + "\n"

        db.execute("UPDATE orchestrator_runs SET steps_completed=? WHERE id=?", (completed, run_id))
        db.commit()

        if status == "failed" and not step.get("optional"):
            break

    total_ms = int((time.time() - total_start) * 1000)
    final_status = "completed" if all(
        r.get("status") != "failed" for r in [json.loads("{}")]  # simplified
    ) else "partial"

    # Build full result for DB
    db.execute(
        "UPDATE orchestrator_runs SET status=?, total_duration_ms=?, completed_at=datetime('now') WHERE id=?",
        ("completed", total_ms, run_id))
    db.commit()

    yield json.dumps({
        "event": "complete",
        "run_id": run_id,
        "total_duration_ms": total_ms,
        "steps_completed": completed,
        "steps_total": len(steps),
        "context_keys": list(context.keys()),
    }) + "\n"


def _generate_chat_summary(workflow_name, result):
    """Generate natural language summary from workflow result"""
    steps = result.get("steps", [])
    summary = result.get("summary", {})

    if workflow_name == "quick_reception":
        triage = next((s["result"] for s in steps if s["id"] == "triage" and s["status"] == "completed"), {})
        register = next((s["result"] for s in steps if s["id"] == "register" and s["status"] == "completed"), {})
        dept = ""
        if triage and triage.get("recommendations"):
            dept = triage["recommendations"][0].get("department", "")
        reg_id = register.get("result", {}).get("visit", {}).get("registration_id", "") if register else ""
        return f"{'접수 완료' if reg_id else '접수 준비'}. 추천 진료과: {dept}. {f'접수번호: {reg_id}' if reg_id else ''}"

    elif workflow_name == "full_consultation":
        completed = summary.get("completed", 0)
        total = summary.get("total_steps", 0)
        return f"진료 분석 완료 ({completed}/{total} 단계). 결과를 확인하세요."

    elif workflow_name == "nurse_assessment":
        vitals = next((s["result"] for s in steps if s["id"] == "vitals" and s["status"] == "completed"), {})
        level = vitals.get("result", {}).get("assessment", {}).get("level", "정상") if vitals else "정상"
        return f"바이탈 체크 완료. 판정: {level}"

    elif workflow_name == "bill_calculate":
        calc = next((s["result"] for s in steps if s["id"] == "calc" and s["status"] == "completed"), {})
        return calc.get("display", "진료비 계산 완료") if calc else "계산 완료"

    return f"워크플로우 '{workflow_name}' 완료 ({summary.get('completed', 0)}/{summary.get('total_steps', 0)} 단계)"


def _get_suggestions(intent):
    """Get follow-up suggestions based on intent"""
    suggestions_map = {
        "reception": ["보험 확인", "예약 조회"],
        "consultation": ["환자 설명문 생성", "의뢰서 작성"],
        "vitals": ["간호 기록 작성", "약물 확인"],
        "billing": ["영수증 발행", "비급여 안내"],
        "claims": ["이의신청서 작성", "청구 현황"],
    }
    return suggestions_map.get(intent, ["진료 접수", "증상 상담"])


@orchestrator_bp.route("/api/orchestrator/chat/stream", methods=["POST"])
def orch_chat_stream():
    """Smart chat with SSE streaming — 인텐트 감지 → 워크플로 실시간 진행
    ---
    tags: [Orchestrator]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [message]
          properties:
            message: {type: string}
            session_id: {type: string}
            context: {type: object}
    responses:
      200:
        description: NDJSON 스트리밍 (event 기반)
    """
    tenant = _resolve_medical_tenant(request) if _resolve_medical_tenant else None
    data = request.json or {}
    message = data.get("message", "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())[:12]
    user_context = data.get("context", {})

    if not message:
        return jsonify({"error": "message is required"}), 400

    def generate():
        # 0. 의료 정본 도구 결정적 우선 라우팅 (비스트림 orch_chat 과 동일 —
        #    LLM 불확실성·일반 워크플로우 가로채기 우회, 의료 경로 모델무관)
        _md_route = _medical_direct_route(message)
        if _md_route:
            _tool, _action, _inp = _md_route
            yield json.dumps({
                "event": "intent",
                "intent": "medical_direct",
                "confidence": 1.0,
                "tool": _tool,
                "session_id": session_id,
            }) + "\n"
            yield json.dumps({"event": "step", "step": _tool, "status": "running"}) + "\n"
            try:
                _res, _ms = _execute_action(_action, _inp, tenant)
            except Exception as _e:  # noqa: BLE001
                _res, _ms = {"error": str(_e)[:160]}, 0
            _msg = force_korean(_summarize_medical_route(_tool, _res))
            yield json.dumps({
                "event": "step", "step": _tool, "status": "completed",
                "duration_ms": _ms, "preview": _res,
            }) + "\n"
            _save_session_turn(session_id, message, _msg, "medical_direct")
            yield json.dumps({
                "event": "summary",
                "type": "tool_calling",
                "intent": "medical_direct",
                "tool": _tool,
                "tool_calls": [{"name": _tool}],
                "tool_result": _res,
                "message": _msg,
                "session_id": session_id,
            }) + "\n"
            return

        # 1. Intent detection
        intent_result = _detect_chat_intent(message, user_context.get("role", ""))
        intent = intent_result["intent"]
        workflow_name = intent_result.get("workflow")

        yield json.dumps({
            "event": "intent",
            "intent": intent,
            "confidence": intent_result["confidence"],
            "workflow": workflow_name,
            "session_id": session_id,
        }) + "\n"

        # 2. Entity extraction
        entities = _extract_entities_from_chat(message)

        yield json.dumps({
            "event": "entities",
            "entities": {k: (v[:100] if isinstance(v, str) and len(v) > 100 else v)
                        for k, v in entities.items()},
        }) + "\n"

        # 3. Execute
        all_wf_s = _get_all_workflows()
        if workflow_name and workflow_name in all_wf_s:
            wf_input = {**entities, **user_context}
            if "symptom" not in wf_input:
                wf_input["symptom"] = message

            # Stream workflow execution
            for event in _run_workflow_streaming(workflow_name, wf_input, tenant):
                yield event

            # Generate summary
            summary_text = f"{all_wf_s[workflow_name]['name']} 완료"
            suggestions = _get_suggestions(intent)

            yield json.dumps({
                "event": "summary",
                "type": "workflow",
                "message": summary_text,
                "suggestions": suggestions,
                "session_id": session_id,
            }) + "\n"

        elif intent == "triage":
            yield json.dumps({"event": "step", "step": "triage", "status": "running"}) + "\n"
            triage_input = {"symptom": message}
            if entities.get("age"):
                triage_input["age"] = entities["age"]
            result, dur = _execute_action("ai:/api/medical/triage", triage_input, tenant)

            recs = (result or {}).get("recommendations", [])
            dept = recs[0]["department"] if recs else "내과"
            msg = f"증상 분석 결과, {dept} 진료를 추천합니다."
            if (result or {}).get("emergency_warning"):
                msg = f"⚠️ {result['emergency_warning']['message']}"

            yield json.dumps({
                "event": "step", "step": "triage", "status": "completed",
                "duration_ms": dur, "preview": result,
            }) + "\n"

            yield json.dumps({
                "event": "summary",
                "type": "triage",
                "message": msg,
                "suggestions": [f"{dept} 접수", "다른 증상 추가", "자세한 설명"],
                "session_id": session_id,
            }) + "\n"

        else:
            # Tool Calling 폴백 시도 (qwen3:8b) — 제너레이터를 직접 순회해 증분 스트리밍.
            # (이전 list() materialize는 전체 완료까지 블로킹 → 스트림 무력화 결함)
            tc_used = False
            if _tool_registry and len(_tool_registry) > 0:
                from core.tool_executor import run_tool_calling_chat_stream
                for event in run_tool_calling_chat_stream(
                    message,
                    ollama_session=_ollama_session,
                    ollama_base=_OLLAMA_BASE,
                    registry=_tool_registry,
                    execute_action_fn=_execute_action,
                    tenant=tenant,
                    log_fn=lambda name, args, result, ms: _log_tool_call(session_id, name, args, result, ms),
                ):
                    tc_used = True
                    yield event

            if not tc_used:
                # General LLM 폴백 — stream tokens (모델/옵션은 config/tools.json 의 general_chat 블록)
                _gc = _general_chat_cfg
                yield json.dumps({"event": "step", "step": "llm", "status": "running"}) + "\n"
                try:
                    from core import model_router
                    full_response = ""
                    for chunk in model_router.stream(
                            "orchestrator.general_chat", message, model=_gc["model"],
                            system=_gc["system"],
                            options={"temperature": _gc["temperature"],
                                     "num_predict": _gc["num_predict"]},
                            timeout=_gc["timeout"],
                            adapter=model_router.OllamaAdapter(
                                base=_OLLAMA_BASE, session=_ollama_session)):
                        full_response += chunk.text
                        if chunk.done:
                            break
                        if chunk.text:
                            yield json.dumps({"event": "token",
                                              "token": chunk.text}) + "\n"

                    full_response = force_korean(full_response)
                    yield json.dumps({
                        "event": "summary",
                        "type": "chat",
                        "message": full_response,
                        "suggestions": ["진료 접수", "증상 상담", "진료비 계산"],
                        "session_id": session_id,
                    }) + "\n"
                except Exception as e:
                    yield json.dumps({
                        "event": "error",
                        "message": f"LLM 오류: {str(e)[:200]}",
                    }) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@orchestrator_bp.route("/api/orchestrator/workflows")
def orch_workflows():
    """List available workflows
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            workflows: {}
            count: {type: integer}
    """
    wfs = []
    for name, wf in _get_all_workflows().items():
        wfs.append({
            "name": name,
            "display_name": wf["name"],
            "description": wf["description"],
            "steps": len(wf["steps"]),
        })
    return jsonify({"workflows": wfs, "count": len(wfs)})


@orchestrator_bp.route("/api/orchestrator/workflow/<name>")
def orch_workflow_detail(name):
    """Get workflow detail
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "workflow not found"}}
    """
    wf = _get_all_workflows().get(name)
    if not wf:
        return jsonify({"error": "workflow not found"}), 404

    steps = []
    for s in wf["steps"]:
        steps.append({
            "id": s["id"],
            "action": s["action"],
            "depends_on": s.get("depends_on", []),
            "optional": s.get("optional", False),
        })

    return jsonify({"name": name, "display_name": wf["name"], "description": wf["description"], "steps": steps})


@orchestrator_bp.route("/api/orchestrator/status/<run_id>")
def orch_status(run_id):
    """Get workflow run status
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "run not found"}}
    """
    db = _get_orch_db()
    run = db.execute("SELECT * FROM orchestrator_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        return jsonify({"error": "run not found"}), 404
    return jsonify(dict(run))


@orchestrator_bp.route("/api/orchestrator/history")
def orch_history():
    """Get workflow execution history
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            history: {type: array}
            count: {type: integer}
    """
    limit = min(int(request.args.get("limit", 20)), 100)
    db = _get_orch_db()
    rows = db.execute(
        "SELECT id, workflow, status, steps_completed, steps_total, total_duration_ms, created_at FROM orchestrator_runs ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()
    return jsonify({"history": [dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# Custom Workflow CRUD
# ---------------------------------------------------------------------------

@orchestrator_bp.route("/api/orchestrator/workflow/register", methods=["POST"])
def orch_workflow_register():
    """커스텀 워크플로 등록
    ---
    tags: [Orchestrator]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name, display_name, steps]
          properties:
            name: {type: string, description: "워크플로 ID (영문소문자+숫자+밑줄)"}
            display_name: {type: string, description: "표시 이름"}
            description: {type: string}
            domain: {type: string, default: custom}
            steps: {type: array, description: "워크플로 단계 배열"}
            triggers: {type: array, description: "채팅 인텐트 트리거 키워드"}
    responses:
      201:
        description: 등록 성공
        schema:
          type: object
          properties:
            ok: {type: boolean}
            name: {}
            display_name: {}
      400:
        description: 검증 실패
    """
    data = request.json or {}

    # Validate
    errors = _validate_workflow_def(data)
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    name = data["name"]

    # Check builtin conflict
    if name in WORKFLOWS:
        return jsonify({"error": f"'{name}' is a builtin workflow (cannot override)"}), 409

    steps_json = json.dumps(data["steps"], ensure_ascii=False)
    triggers_json = json.dumps(data.get("triggers", []), ensure_ascii=False)

    db = _get_orch_db()
    try:
        db.execute(
            "INSERT INTO custom_workflows (name, display_name, description, domain, steps, triggers, created_by) VALUES (?,?,?,?,?,?,?)",
            (name, data["display_name"], data.get("description", ""),
             data.get("domain", "custom"), steps_json, triggers_json,
             request.headers.get("X-Medical-Key", "")[:20]))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"workflow '{name}' already exists (use PUT to update)"}), 409

    return jsonify({"ok": True, "name": name, "display_name": data["display_name"]}), 201


@orchestrator_bp.route("/api/orchestrator/workflow/<name>", methods=["PUT"])
def orch_workflow_update(name):
    """커스텀 워크플로 수정
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "validation failed"}}
      403:
        description: "권한 없음"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "custom workflow not found"}}
    """
    # Builtin은 수정 불가
    if name in WORKFLOWS:
        return jsonify({"error": f"'{name}' is a builtin workflow (cannot modify)"}), 403

    db = _get_orch_db()
    existing = db.execute("SELECT * FROM custom_workflows WHERE name=?", (name,)).fetchone()
    if not existing:
        return jsonify({"error": "custom workflow not found"}), 404

    data = request.json or {}

    # Merge with existing
    new_steps = data.get("steps", json.loads(existing["steps"]))
    new_display = data.get("display_name", existing["display_name"])
    new_desc = data.get("description", existing["description"])
    new_domain = data.get("domain", existing["domain"])
    new_triggers = data.get("triggers", json.loads(existing["triggers"]))

    # Validate if steps changed
    if "steps" in data:
        check_data = {"name": name, "display_name": new_display, "steps": new_steps}
        errors = _validate_workflow_def(check_data)
        if errors:
            return jsonify({"error": "validation failed", "details": errors}), 400

    db.execute(
        "UPDATE custom_workflows SET display_name=?, description=?, domain=?, steps=?, triggers=?, updated_at=datetime('now') WHERE name=?",
        (new_display, new_desc, new_domain,
         json.dumps(new_steps, ensure_ascii=False),
         json.dumps(new_triggers, ensure_ascii=False), name))
    db.commit()

    return jsonify({"ok": True, "name": name})


@orchestrator_bp.route("/api/orchestrator/workflow/<name>", methods=["DELETE"])
def orch_workflow_delete(name):
    """커스텀 워크플로 삭제
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
      403:
        description: "권한 없음"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "custom workflow not found"}}
    """
    if name in WORKFLOWS:
        return jsonify({"error": f"'{name}' is a builtin workflow (cannot delete)"}), 403

    db = _get_orch_db()
    deleted = db.execute("DELETE FROM custom_workflows WHERE name=?", (name,)).rowcount
    db.commit()

    if not deleted:
        return jsonify({"error": "custom workflow not found"}), 404
    return jsonify({"ok": True, "deleted": name})


@orchestrator_bp.route("/api/orchestrator/workflow/custom")
def orch_workflow_custom_list():
    """커스텀 워크플로 목록
    ---
    tags: [Orchestrator]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            workflows: {type: array}
            count: {type: integer}
    """
    db = _get_orch_db()
    rows = db.execute("SELECT name, display_name, description, domain, triggers, is_active, created_at, updated_at FROM custom_workflows ORDER BY created_at DESC").fetchall()
    return jsonify({"workflows": [dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# Tool Calling — 도구 관리 API + 로깅
# ---------------------------------------------------------------------------

def _log_tool_call(session_id, tool_name, args, result, duration_ms):
    """도구 호출 로그 DB 저장"""
    try:
        db = _get_orch_db()
        db.execute(
            "INSERT INTO tool_call_logs (session_id, tool_name, tool_args, tool_result, duration_ms, success) VALUES (?,?,?,?,?,?)",
            (session_id, tool_name,
             json.dumps(args, ensure_ascii=False, default=str)[:2000],
             json.dumps(result, ensure_ascii=False, default=str)[:2000],
             duration_ms,
             0 if isinstance(result, dict) and "error" in result else 1))
        db.commit()
    except Exception as e:
        logger.warning("tool_call_log write error: %s", e)


@orchestrator_bp.route("/api/orchestrator/tools")
def orch_tools():
    """등록된 Tool Calling 도구 목록
    ---
    tags: [Orchestrator - Tools]
    responses:
      200:
        description: 도구 목록
        schema:
          type: object
          properties:
            tools: {type: array}
            count: {type: integer}
            enabled: {type: integer}
            version: {}
    """
    if not _tool_registry:
        return jsonify({"tools": [], "count": 0})
    tools = _tool_registry.list_tools()
    return jsonify({
        "tools": tools,
        "count": len(tools),
        "enabled": len(_tool_registry),
        "version": _tool_registry.version,
    })


@orchestrator_bp.route("/api/orchestrator/tools/reload", methods=["POST"])
def orch_tools_reload():
    """Tool Calling 도구 설정 다시 로드 (localhost only)
    ---
    tags: [Orchestrator - Tools]
    responses:
      200:
        description: 리로드 결과
        schema:
          type: object
          properties:
            ok: {type: boolean}
            before: {}
            after: {}
            version: {}
            general_chat_model: {}
    """
    if not _tool_registry:
        return jsonify({"error": "tool registry not initialized"}), 500
    before = len(_tool_registry)
    _tool_registry.reload()
    after = len(_tool_registry)
    _load_general_chat_cfg(_BASE_DIR)  # general_chat 블록도 함께 갱신
    return jsonify({
        "ok": True,
        "before": before,
        "after": after,
        "version": _tool_registry.version,
        "general_chat_model": _general_chat_cfg.get("model"),
    })

# Web Search는 routes/search.py (Search Blueprint)로 분리됨 (v2.66.0)
