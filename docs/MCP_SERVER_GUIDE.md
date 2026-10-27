# MCP Server Guide — Model Context Protocol

> WeRU.B AI Server v2.106.1 | MCP Protocol 2025-03-26 | 28 Tools

## 개요

AI 서버의 도구를 MCP(Model Context Protocol) 표준으로 노출하여,
Claude Desktop/Code, GPT, Cursor 등 외부 AI 에이전트가 직접 호출할 수 있습니다.

## 빠른 시작

### Claude Code에서 연결
```bash
claude mcp add werub-ai --transport http https://example.org/ollama/mcp
```

### Claude Desktop에서 연결
`~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "werub-ai": {
      "type": "http",
      "url": "https://example.org/ollama/mcp",
      "headers": {
        "Authorization": "Bearer sk-your-api-key"
      }
    }
  }
}
```

### localhost 연결 (SSH 터널 사용 시)
```bash
claude mcp add werub-ai --transport http http://localhost:8585/mcp
```

## 엔드포인트

```
POST /mcp  — MCP JSON-RPC 2.0 (Streamable HTTP)
```

- Content-Type: `application/json`
- 인증: `Authorization: Bearer <API_KEY>` (외부 접속 시)
- localhost: 인증 불필요 (local_bypass=true)

## MCP 메서드

| 메서드 | 설명 | 파라미터 |
|--------|------|----------|
| `initialize` | 핸드셰이크 | `{}` |
| `notifications/initialized` | 초기화 완료 알림 | `{}` |
| `ping` | 연결 확인 | `{}` |
| `tools/list` | 도구 목록 (28개) | `{}` |
| `tools/call` | 도구 실행 | `{name, arguments}` |
| `resources/list` | 리소스 목록 | `{}` |
| `resources/read` | 리소스 읽기 | `{uri}` |

## 사용 가능한 도구 (28개)

### 의료 (8개)
| 도구 | 설명 |
|------|------|
| `medical_triage` | 증상 → 진료과 추천 + 응급도 판별 |
| `medical_reasoning` | 5단계 감별진단 추론 체인 |
| `medical_auto_record` | 진료 대화 → SOAP 의무기록 자동 생성 |
| `medical_drug_analysis` | 약물 상호작용/금기/용량 분석 |
| `medical_predict_readmission` | 30일 재입원 위험도 예측 |
| `medical_cross_validate` | 2~3개 모델 독립 감별진단 + 일치도 |
| `medical_research` | 의학 문헌/가이드라인 근거 중심 답변 |
| `clinical_qa` | 임상 전문가 수준 Q&A |

### 한국어 (5개)
| 도구 | 설명 |
|------|------|
| `korean_spellcheck` | 맞춤법 검사 + 교정 |
| `korean_analyze` | AI 생성 패턴 22메트릭 탐지 |
| `korean_readability` | 가독성 5등급 분석 |
| `korean_style_transfer` | 문체 변환 6종 |
| `korean_pipeline` | 전체 파이프라인 (필터+분석+맞춤법+가독성+응집) |

### 글쓰기 (4개)
| 도구 | 설명 |
|------|------|
| `writing_draft` | 주제/톤 기반 글쓰기 초안 |
| `writing_polish` | 텍스트 다듬기 + 교정 |
| `writing_seo` | SEO 점수 분석 + 메타 태그 추천 |
| `writing_translate` | 다국어 번역 |

### 유틸리티 (3개)
| 도구 | 설명 |
|------|------|
| `web_search` | 실시간 웹 검색 (SearXNG) |
| `rag_search` | RAG 벡터 DB 문서 검색 |
| `medical_patient_education` | 환자용 의학 설명 |

## 호출 예시

### 1. 핸드셰이크
```bash
curl -X POST https://example.org/ollama/mcp \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {}
  }'
```
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "tools": {"listChanged": true},
      "resources": {"subscribe": false, "listChanged": false}
    },
    "serverInfo": {"name": "werub-ai", "version": "2.66.0"}
  }
}
```

### 2. 도구 목록 조회
```bash
curl -X POST https://example.org/ollama/mcp \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}'
```

### 3. 웹 검색 도구 실행
```bash
curl -X POST https://example.org/ollama/mcp \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "web_search",
      "arguments": {"query": "오늘 서울 날씨"}
    }
  }'
```
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{"type": "text", "text": "{\"results\": [...], \"returned\": 5}"}],
    "isError": false,
    "_meta": {"tool": "web_search", "duration_ms": 1001}
  }
}
```

### 4. 의료 증상 분류
```bash
curl -X POST https://example.org/ollama/mcp \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "medical_triage",
      "arguments": {"symptom": "두통이 3일째 지속되고 열이 38.5도입니다", "age": 35, "sex": "M"}
    }
  }'
```

### 5. 배치 요청
```bash
curl -X POST https://example.org/ollama/mcp \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '[
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
  ]'
```

## 리소스

| URI | 설명 |
|-----|------|
| `status://server` | 서버 상태 (버전, 도구 수, SearXNG 상태) |

```bash
curl -X POST https://example.org/ollama/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 5,
    "method": "resources/read",
    "params": {"uri": "status://server"}
  }'
```

## 아키텍처

```
[Claude Desktop/Code]     [외부 AI 에이전트]
       │                         │
       │ HTTP              HTTPS (Bearer)
       │                         │
       ▼                         ▼
  POST /mcp ─── JSON-RPC 2.0 ───┘
       │
  routes/mcp.py
       │
  config/tools.json (28 도구)
       │
  _execute_action()
       │
  ┌────┼────┬────────┐
  │    │    │        │
ai:// search:// llm://  내부 API
```

## 에러 코드

| 코드 | 설명 |
|------|------|
| -32601 | 알 수 없는 메서드 |
| -32000 | 도구 실행 에러 |

## 보안

- 외부 접속: `Authorization: Bearer <API_KEY>` 필수
- localhost: 인증 불필요 (local_bypass)
- API 키 발급: `POST /api/keys` (localhost에서만)
