# MCP 서버 노출 — 외부 AI 에이전트 연동

> **Feature**: mcp-server
> **Created**: 2026-06-02
> **Version Target**: v2.67.0
> **Priority**: P1 (High — AI 에코시스템 연동)

## 1. 목적

AI 서버의 531+ API를 MCP(Model Context Protocol) 표준으로 노출하여,
Claude Desktop/Code, GPT, Cursor 등 외부 AI 에이전트가 우리 API를 도구로 직접 사용할 수 있게 한다.

## 2. 현황

| 항목 | 상태 |
|------|------|
| OpenAPI 스펙 | ✅ `/openapi.json`, `/docs` (Flasgger) |
| AI Plugin manifest | ✅ `/.well-known/ai-plugin.json` |
| Tool Calling 도구 정의 | ✅ `config/tools.json` 21개 도구 |
| MCP 서버 | ✅ HTTP 라이브(`routes/mcp.py` — 기획 후 구현·2026-08-26 정정) · **stdio는 미구현** |

### config/tools.json → MCP 변환 가능성
```
tools.json: {"name": "medical_triage", "description": "...", "parameters": {...}}
MCP tools:  {"name": "medical_triage", "description": "...", "inputSchema": {...}}
```
거의 1:1 매핑 가능. `parameters` → `inputSchema`로 키만 변경.

## 3. 구현 방식: Streamable HTTP MCP

MCP 프로토콜은 3가지 전송 방식을 지원:
- **stdio**: 로컬 프로세스 (Claude Desktop 로컬 연결)
- **SSE**: Server-Sent Events (레거시)
- **Streamable HTTP**: POST 기반 (최신, 원격 서버에 적합)

우리 서버는 **원격(HTTPS)** 접근이 주 사용 사례이므로 **Streamable HTTP**를 구현한다.
~~로컬 개발용으로 stdio 모드도 지원한다.~~ ⚠️ **stdio는 미구현(컷)** — 2026-08-26(X11) 확인: `mcp_stdio.py` 부재·`routes/mcp.py`는 HTTP 전용·transport 추상화 없음. §4-4·Step 4·성공기준의 stdio 항목은 전부 미이행분이다. Claude Desktop 연동은 HTTP transport로 대체(빠른시작 참조).

## 4. 구현 범위

### 4-1. MCP 서버 코어 (routes/mcp.py)

```
POST /mcp  — MCP JSON-RPC 엔드포인트 (Streamable HTTP)
```

MCP JSON-RPC 메서드:
| 메서드 | 설명 |
|--------|------|
| `initialize` | 클라이언트 핸드셰이크 |
| `tools/list` | 사용 가능한 도구 목록 |
| `tools/call` | 도구 실행 |
| `resources/list` | 리소스 목록 (RAG 컬렉션 등) |
| `resources/read` | 리소스 읽기 |

### 4-2. 도구 매핑 (config/tools.json → MCP tools)

config/tools.json 21개 도구를 MCP tools로 자동 변환:
- `name`, `description` 그대로
- `parameters` → `inputSchema`
- `action` → 내부 라우팅 (orchestrator `_execute_action` 재사용)

### 4-3. 리소스 (선택)

| 리소스 | URI | 설명 |
|--------|-----|------|
| RAG 문서 | `rag://werub_docs/{doc_id}` | RAG 색인 문서 |
| 서버 상태 | `status://server` | 시스템 상태 |

### 4-4. stdio 모드 (로컬 개발)

```bash
# Claude Desktop settings.json
{
  "mcpServers": {
    "werub-ai": {
      "command": "python3",
      "args": ["mcp_stdio.py"],
      "cwd": "/path/to/WeRUBLLMManager"
    }
  }
}
```

## 5. 아키텍처

```
[Claude Desktop/Code]                [외부 AI]
       │                                 │
       │ stdio (로컬)           HTTPS (원격)
       │                                 │
       ▼                                 ▼
  mcp_stdio.py              POST /mcp (Flask)
       │                                 │
       └────────── MCP JSON-RPC ─────────┘
                        │
                  routes/mcp.py
                        │
              ┌─────────┴─────────┐
              │ config/tools.json │
              │   21 도구 정의    │
              └─────────┬─────────┘
                        │
              _execute_action() (orchestrator)
                        │
           ┌────────────┼────────────┐
           │            │            │
      ai:// API    search://     llm://
      (내부 API)   (SearXNG)    (직접 LLM)
```

## 6. 수정 파일 목록

| 파일 | 변경 | 설명 |
|------|------|------|
| `routes/mcp.py` | **신규** | MCP Blueprint (Streamable HTTP) |
| `mcp_stdio.py` | **신규** | stdio 모드 진입점 (로컬용) |
| `app.py` | 소폭 | mcp_bp 등록 |

## 7. 구현 순서

```
Step 1: routes/mcp.py — MCP JSON-RPC 엔드포인트 (initialize, tools/list, tools/call)
  ↓
Step 2: tools.json → MCP tools 자동 변환 로직
  ↓
Step 3: tools/call → _execute_action 연동
  ↓
Step 4: mcp_stdio.py — stdio 래퍼 (로컬용)
  ↓
Step 5: app.py에 Blueprint 등록
  ↓
Step 6: E2E 테스트 (curl JSON-RPC + Claude Desktop)
  ↓
Step 7: 배포 + 문서 갱신
```

## 8. 성공 기준

- [ ] `POST /mcp` — initialize 핸드셰이크 성공
- [ ] `tools/list` — 21개 도구 목록 반환
- [ ] `tools/call` — medical_triage 도구 실행 성공
- [ ] `tools/call` — web_search 도구 실행 성공
- [ ] Claude Desktop에서 stdio 모드 연결 성공
- [ ] 외부 HTTPS에서 Streamable HTTP 연결 성공
