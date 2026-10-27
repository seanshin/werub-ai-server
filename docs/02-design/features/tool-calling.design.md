# Tool Calling Design Document

> **Summary**: Orchestrator에 LLM Tool Calling 통합 — intent 미매칭 시 qwen3:8b가 도구를 자유 조합
>
> **Project**: WeRU.B AI Server
> **Version**: v2.64.0 (예정)
> **Author**: AI_Server
> **Date**: 2026-06-02
> **Status**: Draft

---

## 1. Overview

### 1.1 Design Goals

- 기존 워크플로우(intent 매칭)는 변경 없이 유지 — 안정성 보장
- intent 미매칭 시 Tool Calling으로 폴백 — 비정형 복합 요청 처리
- 도구 정의를 `config/tools.json` 파일로 분리 — 코드 수정 없이 도구 추가/삭제
- 기존 `_execute_action()` 메커니즘 재사용 — 내부 API 호출 일관성

### 1.2 Design Principles

- **기존 패턴 재사용**: orchestrator의 `_execute_action("ai:/api/...")` 그대로 활용
- **설정 기반 도구 관리**: `config/tools.json`에서 로드, `enabled` 플래그로 on/off
- **안전한 폴백 체인**: intent 매칭 → Tool Calling → general chat (3단계)
- **관찰 가능**: 도구 호출 로그 DB 저장, SSE로 진행 상황 실시간 전송

---

## 2. Architecture

### 2.1 Flow Diagram

```
사용자 메시지 → orch_chat() / orch_chat_stream()
  │
  ├─ _detect_chat_intent()
  │   ├─ 워크플로우 매칭 → _run_workflow() [기존 그대로]
  │   └─ 매칭 실패 ──┐
  │                   ▼
  │        _run_tool_calling_chat()  [NEW]
  │           │
  │           ├─ ToolRegistry.to_ollama_tools()
  │           ├─ POST /api/chat (qwen3:8b, tools=[...])
  │           │
  │           ├─ 응답에 tool_calls 없음 → 텍스트 응답 반환
  │           ├─ 응답에 tool_calls 있음 ──┐
  │           │                           ▼
  │           │                 _execute_tool_call()
  │           │                   → _execute_action("ai:/api/...")
  │           │                   → 결과를 messages에 추가
  │           │                   → 다시 LLM 호출 (반복, 최대 5회)
  │           │
  │           └─ 최종 텍스트 응답 → force_korean → 반환
  │
  └─ 모든 실패 시 → general chat (qwen2.5:14b) [기존 폴백]
```

### 2.2 Component Diagram

```
config/
  tools.json              ← 도구 정의 (설정 파일)

core/
  tool_registry.py        ← ToolRegistry 클래스 (로드/변환/reload)
  tool_executor.py        ← run_tool_calling_chat() + _execute_tool_call()

routes/
  orchestrator.py         ← orch_chat/orch_chat_stream 폴백 연결 (수정)

orchestrator.db           ← tool_call_logs 테이블 추가
```

---

## 3. Detailed Design

### 3.1 config/tools.json

```json
{
  "version": 1,
  "defaults": {
    "model": "qwen3:8b",
    "max_tool_calls": 5,
    "tool_timeout": 30,
    "total_timeout": 120,
    "temperature": 0.3
  },
  "tools": [
    {
      "name": "medical_triage",
      "description": "환자 증상을 분석하여 적합한 진료과를 추천하고 응급도를 판별합니다",
      "action": "ai:/api/medical/triage",
      "parameters": {
        "type": "object",
        "required": ["symptom"],
        "properties": {
          "symptom": {"type": "string", "description": "환자 증상 설명"},
          "age": {"type": "integer", "description": "환자 나이"},
          "sex": {"type": "string", "enum": ["M", "F"], "description": "환자 성별"}
        }
      },
      "enabled": true
    }
  ]
}
```

**설계 결정**:
- `defaults` 섹션으로 전역 설정 관리
- 각 tool의 `action` 필드는 기존 orchestrator의 `_execute_action()` 형식 그대로 사용
- `parameters`는 JSON Schema 형식 → Ollama tools 형식으로 1:1 매핑

### 3.2 core/tool_registry.py

```python
"""Tool Calling 도구 레지스트리 — config/tools.json 로드 + Ollama 변환"""

import json
import logging
import os

logger = logging.getLogger("ollama-admin")


class ToolRegistry:
    """설정 파일 기반 도구 레지스트리"""

    def __init__(self, config_path=None, base_dir=None):
        self._base_dir = base_dir or "."
        self._config_path = config_path or os.path.join(self._base_dir, "config", "tools.json")
        self._tools = {}        # name → tool spec
        self._defaults = {}     # 전역 기본값
        self._version = 0
        self.reload()

    def reload(self):
        """설정 파일 다시 로드 (서버 재시작 없이 갱신)"""
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._defaults = data.get("defaults", {})
            self._version = data.get("version", 0)
            self._tools = {}
            for tool in data.get("tools", []):
                name = tool.get("name")
                if name:
                    self._tools[name] = tool
            logger.info("ToolRegistry loaded: %d tools (v%d)", len(self._tools), self._version)
        except FileNotFoundError:
            logger.warning("tools.json not found: %s", self._config_path)
        except Exception as e:
            logger.error("ToolRegistry load error: %s", e)

    @property
    def defaults(self):
        return self._defaults

    def get(self, name):
        """이름으로 도구 스펙 조회"""
        return self._tools.get(name)

    def get_enabled_tools(self):
        """enabled=true인 도구만 반환"""
        return {k: v for k, v in self._tools.items() if v.get("enabled", True)}

    def to_ollama_tools(self):
        """Ollama /api/chat tools 파라미터 형식으로 변환

        Returns:
            list[dict]: [{"type": "function", "function": {...}}, ...]
        """
        result = []
        for name, spec in self.get_enabled_tools().items():
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec.get("description", ""),
                    "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return result

    def get_action(self, tool_name):
        """도구 이름 → 내부 action 문자열 (ai:/api/... 형식)"""
        spec = self._tools.get(tool_name)
        return spec.get("action") if spec else None

    def __len__(self):
        return len(self._tools)
```

### 3.3 core/tool_executor.py

```python
"""Tool Calling 실행 엔진 — LLM ↔ 도구 반복 루프"""

import json
import logging
import time
from datetime import datetime, timezone

from core.korean import force_korean, force_korean_json
from core.tool_registry import ToolRegistry

logger = logging.getLogger("ollama-admin")

# 상수
_DEFAULT_MODEL = "qwen3:8b"
_DEFAULT_MAX_CALLS = 5
_DEFAULT_TOOL_TIMEOUT = 30
_DEFAULT_TOTAL_TIMEOUT = 120
_DEFAULT_TEMPERATURE = 0.3

_SYSTEM_PROMPT = """당신은 병원 AI 어시스턴트입니다. 한국어로 답변하세요.
사용 가능한 도구가 있으면 적극적으로 활용하여 정확한 정보를 제공하세요.
도구가 필요 없는 일반 질문은 직접 답변하세요."""


def run_tool_calling_chat(
    message,
    *,
    ollama_session,
    ollama_base,
    registry: ToolRegistry,
    execute_action_fn,      # orchestrator._execute_action 주입
    tenant=None,
    session_history=None,   # 이전 대화 컨텍스트
    log_fn=None,            # DB 로깅 함수
):
    """Tool Calling 채팅 실행

    Args:
        message: 사용자 메시지
        registry: ToolRegistry 인스턴스
        execute_action_fn: _execute_action(action, input_data, tenant) 함수

    Returns:
        {
            "type": "tool_calling",
            "message": "최종 응답",
            "tool_calls": [{"name": ..., "args": ..., "result": ..., "duration_ms": ...}],
            "model": "qwen3:8b",
            "total_duration_ms": int,
        }
    """
    defaults = registry.defaults
    model = defaults.get("model", _DEFAULT_MODEL)
    max_calls = defaults.get("max_tool_calls", _DEFAULT_MAX_CALLS)
    tool_timeout = defaults.get("tool_timeout", _DEFAULT_TOOL_TIMEOUT)
    total_timeout = defaults.get("total_timeout", _DEFAULT_TOTAL_TIMEOUT)
    temperature = defaults.get("temperature", _DEFAULT_TEMPERATURE)

    tools = registry.to_ollama_tools()
    if not tools:
        return None  # 도구 없으면 None → 호출부에서 general chat 폴백

    # 메시지 구성
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if session_history:
        messages.extend(session_history)
    messages.append({"role": "user", "content": message})

    tool_call_log = []
    total_start = time.time()
    call_count = 0

    while call_count < max_calls:
        # 전체 타임아웃 체크
        if time.time() - total_start > total_timeout:
            logger.warning("Tool calling total timeout (%ds)", total_timeout)
            break

        # LLM 호출
        try:
            r = ollama_session.post(f"{ollama_base}/api/chat", json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": 2048},
            }, timeout=tool_timeout)
            resp = r.json()
        except Exception as e:
            logger.error("Tool calling LLM error: %s", e)
            return None  # 폴백

        msg = resp.get("message", {})

        # tool_calls가 없으면 최종 응답
        if not msg.get("tool_calls"):
            final_text = force_korean(msg.get("content", ""))
            return {
                "type": "tool_calling",
                "message": final_text,
                "tool_calls": tool_call_log,
                "model": model,
                "total_duration_ms": int((time.time() - total_start) * 1000),
            }

        # tool_calls 처리
        messages.append(msg)  # assistant message with tool_calls

        for tc in msg["tool_calls"]:
            call_count += 1
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})

            action = registry.get_action(fn_name)
            if not action:
                # 미등록 도구
                messages.append({"role": "tool", "content": json.dumps(
                    {"error": f"unknown tool: {fn_name}"}, ensure_ascii=False)})
                continue

            # 도구 실행
            tc_start = time.time()
            try:
                result, duration_ms = execute_action_fn(action, fn_args, tenant)
                result = force_korean_json(result) if result else {}
            except Exception as e:
                logger.error("Tool %s execution error: %s", fn_name, e)
                result = {"error": str(e)[:200]}
                duration_ms = int((time.time() - tc_start) * 1000)

            tc_entry = {
                "name": fn_name,
                "args": fn_args,
                "result_summary": _truncate_result(result),
                "duration_ms": duration_ms,
            }
            tool_call_log.append(tc_entry)

            # 로그 DB 저장
            if log_fn:
                try:
                    log_fn(fn_name, fn_args, result, duration_ms)
                except Exception:
                    pass

            # 결과를 messages에 추가
            messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })

    # max_calls 도달 — 현재까지 결과로 최종 응답 요청
    messages.append({"role": "user", "content": "도구 호출을 마무리하고, 지금까지의 결과를 종합하여 최종 답변해주세요."})
    try:
        r = ollama_session.post(f"{ollama_base}/api/chat", json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 1024},
        }, timeout=tool_timeout)
        final_text = force_korean(r.json().get("message", {}).get("content", ""))
    except Exception:
        final_text = "도구 분석이 완료되었습니다. 결과를 확인해주세요."

    return {
        "type": "tool_calling",
        "message": final_text,
        "tool_calls": tool_call_log,
        "model": model,
        "total_duration_ms": int((time.time() - total_start) * 1000),
    }


def run_tool_calling_chat_stream(
    message,
    *,
    ollama_session,
    ollama_base,
    registry: ToolRegistry,
    execute_action_fn,
    tenant=None,
    session_history=None,
    log_fn=None,
):
    """Tool Calling 채팅 SSE 스트리밍 — 도구 호출 과정을 실시간 전송

    Yields:
        NDJSON 문자열 (event 기반)
    """
    defaults = registry.defaults
    model = defaults.get("model", _DEFAULT_MODEL)
    max_calls = defaults.get("max_tool_calls", _DEFAULT_MAX_CALLS)
    tool_timeout = defaults.get("tool_timeout", _DEFAULT_TOOL_TIMEOUT)
    total_timeout = defaults.get("total_timeout", _DEFAULT_TOTAL_TIMEOUT)
    temperature = defaults.get("temperature", _DEFAULT_TEMPERATURE)

    tools = registry.to_ollama_tools()
    if not tools:
        return  # 도구 없으면 종료 → 호출부에서 general chat 폴백

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if session_history:
        messages.extend(session_history)
    messages.append({"role": "user", "content": message})

    tool_call_log = []
    total_start = time.time()
    call_count = 0

    yield json.dumps({"event": "tool_calling_start", "model": model, "tools_available": len(tools)}) + "\n"

    while call_count < max_calls:
        if time.time() - total_start > total_timeout:
            yield json.dumps({"event": "timeout", "elapsed_ms": int((time.time() - total_start) * 1000)}) + "\n"
            break

        try:
            r = ollama_session.post(f"{ollama_base}/api/chat", json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": 2048},
            }, timeout=tool_timeout)
            resp = r.json()
        except Exception as e:
            yield json.dumps({"event": "error", "message": f"LLM 오류: {str(e)[:200]}"}) + "\n"
            return

        msg = resp.get("message", {})

        if not msg.get("tool_calls"):
            final_text = force_korean(msg.get("content", ""))
            yield json.dumps({
                "event": "summary",
                "type": "tool_calling",
                "message": final_text,
                "tool_calls": tool_call_log,
                "total_duration_ms": int((time.time() - total_start) * 1000),
            }) + "\n"
            return

        messages.append(msg)

        for tc in msg["tool_calls"]:
            call_count += 1
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            action = registry.get_action(fn_name)

            yield json.dumps({"event": "tool_call", "name": fn_name, "args": fn_args, "status": "running"}) + "\n"

            if not action:
                messages.append({"role": "tool", "content": json.dumps({"error": f"unknown tool: {fn_name}"})})
                yield json.dumps({"event": "tool_call", "name": fn_name, "status": "error", "error": "unknown tool"}) + "\n"
                continue

            tc_start = time.time()
            try:
                result, duration_ms = execute_action_fn(action, fn_args, tenant)
                result = force_korean_json(result) if result else {}
            except Exception as e:
                result = {"error": str(e)[:200]}
                duration_ms = int((time.time() - tc_start) * 1000)

            tc_entry = {"name": fn_name, "args": fn_args, "result_summary": _truncate_result(result), "duration_ms": duration_ms}
            tool_call_log.append(tc_entry)

            if log_fn:
                try:
                    log_fn(fn_name, fn_args, result, duration_ms)
                except Exception:
                    pass

            messages.append({"role": "tool", "content": json.dumps(result, ensure_ascii=False, default=str)[:4000]})

            yield json.dumps({"event": "tool_call", "name": fn_name, "status": "completed", "duration_ms": duration_ms, "preview": _truncate_result(result)}) + "\n"

    # max_calls 도달
    messages.append({"role": "user", "content": "도구 호출을 마무리하고, 지금까지의 결과를 종합하여 최종 답변해주세요."})
    try:
        r = ollama_session.post(f"{ollama_base}/api/chat", json={
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_predict": 1024},
        }, timeout=tool_timeout)
        final_text = force_korean(r.json().get("message", {}).get("content", ""))
    except Exception:
        final_text = "도구 분석이 완료되었습니다."

    yield json.dumps({
        "event": "summary",
        "type": "tool_calling",
        "message": final_text,
        "tool_calls": tool_call_log,
        "total_duration_ms": int((time.time() - total_start) * 1000),
    }) + "\n"


def _truncate_result(result, max_len=300):
    """로그/프리뷰용 결과 요약"""
    if not result:
        return {}
    if isinstance(result, dict):
        truncated = {}
        for k, v in result.items():
            if isinstance(v, str) and len(v) > max_len:
                truncated[k] = v[:max_len] + "..."
            elif isinstance(v, (list, dict)):
                s = json.dumps(v, ensure_ascii=False, default=str)
                truncated[k] = json.loads(s[:max_len] + '"]') if len(s) > max_len else v
            else:
                truncated[k] = v
        return truncated
    return result
```

### 3.4 routes/orchestrator.py 수정

```python
# init_orchestrator에 추가
_tool_registry = None

def init_orchestrator(*, ollama_session, ollama_base, base_dir, resolve_tenant, flask_app=None):
    global _tool_registry
    # ... 기존 코드 ...
    from core.tool_registry import ToolRegistry
    _tool_registry = ToolRegistry(base_dir=base_dir)


# orch_chat — general chat 폴백 직전에 삽입
def orch_chat():
    # ... 기존 intent 매칭/triage 코드 ...

    else:
        # ★ Tool Calling 폴백 시도 (intent 미매칭)
        if _tool_registry and len(_tool_registry) > 0:
            from core.tool_executor import run_tool_calling_chat
            tc_result = run_tool_calling_chat(
                message,
                ollama_session=_ollama_session,
                ollama_base=_OLLAMA_BASE,
                registry=_tool_registry,
                execute_action_fn=_execute_action,
                tenant=tenant,
                log_fn=_log_tool_call,
            )
            if tc_result:
                _save_session_turn(session_id, message, tc_result["message"], "tool_calling")
                return jsonify({
                    **tc_result,
                    "suggestions": _get_suggestions(intent),
                    "session_id": session_id,
                })

        # General LLM chat (기존 qwen2.5:14b 폴백 — 변경 없음)
        # ...
```

### 3.5 DB 스키마 (orchestrator.db)

```sql
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

CREATE INDEX IF NOT EXISTS idx_tool_call_logs_session ON tool_call_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_tool ON tool_call_logs(tool_name);
```

---

## 4. API Changes

### 4.1 기존 엔드포인트 변경

| 엔드포인트 | 변경 | 내용 |
|-----------|------|------|
| `POST /api/orchestrator/chat` | 응답 확장 | `type: "tool_calling"` 추가, `tool_calls` 배열 포함 |
| `POST /api/orchestrator/chat/stream` | 이벤트 추가 | `tool_calling_start`, `tool_call`, `summary` 이벤트 |

### 4.2 신규 엔드포인트

| 엔드포인트 | Method | 용도 |
|-----------|--------|------|
| `/api/orchestrator/tools` | GET | 등록된 도구 목록 (enabled만) |
| `/api/orchestrator/tools/reload` | POST | tools.json 다시 로드 (localhost only) |

### 4.3 응답 예시 (tool_calling)

```json
{
  "type": "tool_calling",
  "message": "약물 상호작용 확인 결과, Metformin과 Aspirin 사이에 중등도 상호작용이 있습니다...",
  "tool_calls": [
    {
      "name": "medical_drug_analysis",
      "args": {"medications": ["Metformin", "Aspirin"]},
      "result_summary": {"interactions": [...]},
      "duration_ms": 3200
    }
  ],
  "model": "qwen3:8b",
  "total_duration_ms": 8500,
  "suggestions": ["감별진단", "환자 설명문"],
  "session_id": "abc123"
}
```

---

## 5. Implementation Order

| 순서 | 파일 | 내용 | 의존성 |
|------|------|------|--------|
| 1 | `config/tools.json` | 도구 정의 20개 | 없음 |
| 2 | `core/tool_registry.py` | ToolRegistry 클래스 | tools.json |
| 3 | `core/tool_executor.py` | 실행 루프 (동기 + 스트리밍) | ToolRegistry |
| 4 | `routes/orchestrator.py` | init + chat/stream 폴백 + tools/reload API | 1~3 |
| 5 | DB 스키마 | tool_call_logs 테이블 | orchestrator.py |

---

## 6. Error Handling

| 시나리오 | 처리 |
|---------|------|
| tools.json 없음/파싱 오류 | ToolRegistry 빈 상태 → tool calling 건너뜀 → general chat |
| qwen3:8b 모델 없음 | LLM 호출 실패 → None 반환 → general chat 폴백 |
| 도구 실행 실패 | `{"error": "..."}` 를 LLM에 재주입, LLM이 판단 |
| max_tool_calls 초과 | 현재까지 결과로 최종 응답 강제 생성 |
| total_timeout 초과 | 루프 중단, 현재까지 결과로 응답 |
| 미등록 도구 호출 | `unknown tool` 에러 → LLM에 재주입 |

---

## 7. Monitoring

- `tool_call_logs` 테이블로 도구별 호출 빈도, 성공률, 평균 응답시간 추적
- 기존 Prometheus 메트릭에 추가:
  - `ollama_tool_calls_total{tool_name}` — 도구별 호출 수
  - `ollama_tool_call_duration_ms{tool_name}` — 도구별 평균 응답시간
