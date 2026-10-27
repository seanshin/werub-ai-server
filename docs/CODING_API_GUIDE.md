# Coding AI API Guide

> WeRU.B AI Server v2.33.0 | 16 APIs | qwen2.5-coder:14b + deepseek-coder-v2:16b  
> Swagger: `https://example.org/ollama/docs` (Coding AI 태그)

---

## Quick Start

```bash
# 코드 생성
curl -X POST https://example.org/ollama/api/code/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Python REST API with FastAPI", "language": "python"}'

# 코드 리뷰
curl -X POST https://example.org/ollama/api/code/review \
  -H "Content-Type: application/json" \
  -d '{"code": "def login(u,p): return db.execute(f\"SELECT * FROM users WHERE n='{u}'\")", "focus": "security"}'
```

---

## 모델

| 모델 | 용도 | 속도 | 특징 |
|------|------|------|------|
| `qwen2.5-coder:14b` | **기본** | 51 tok/s | HumanEval 88%, 128K 컨텍스트 |
| `deepseek-coder-v2:16b` | 빠른 생성 | 122 tok/s | MoE, HumanEval 90% |
| `qwen2.5:14b` | 범용 (코드+한국어) | 82 tok/s | 한국어 99% |

모든 API에서 `"model": "deepseek-coder-v2:16b"` 파라미터로 모델 변경 가능.

---

## API Reference

### 1. 코드 생성

#### POST `/api/code/generate`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| prompt | string | ✅ | 코드 생성 요청 |
| language | string | | 프로그래밍 언어 (python/typescript/java/go/rust) |
| framework | string | | 프레임워크 (react/fastapi/spring) |
| style | string | | concise/detailed/production (기본: production) |
| model | string | | 모델 (기본: qwen2.5-coder:14b) |

**Request:**
```json
{
  "prompt": "JWT 인증 미들웨어를 구현해줘",
  "language": "python",
  "framework": "fastapi"
}
```

**Response:**
```json
{
  "code": "from fastapi import Depends, HTTPException...",
  "raw": "```python\nfrom fastapi import ...\n```\n\n이 미들웨어는 ...",
  "model": "qwen2.5-coder:14b",
  "tokens": 450,
  "duration_ms": 8500,
  "language": "python",
  "syntax_valid": true
}
```

> **`code` vs `raw`** (v2.125.1) — `code`는 **마크다운 펜스와 설명문을 제거한 순수 코드**다.
> 그대로 파일에 쓰거나 파싱할 수 있다. `raw`는 LLM 원문(설명 포함)이다.
>
> ⚠️ v2.125.0까지는 `code`에 원문이 실려 있었고, 그래서 `syntax_valid`·`security_scan`이
> **호출자가 받은 문자열과 다른 것**(펜스를 벗긴 블록)을 검사한 값이었다 —
> 받은 걸 그대로 파싱하면 실패하는데 플래그는 `true`였다. 지금은 **검사 대상과 반환 값이 같다**.
> `/api/code/refactor`(`refactored`)·`/api/code/convert`(`converted`)도 동일하게 정리됐다.

#### POST `/api/code/generate/stream`
스트리밍 버전. NDJSON으로 토큰 단위 실시간 반환.

---

### 2. 코드 리뷰

#### POST `/api/code/review`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | ✅ | 리뷰할 코드 |
| language | string | | 프로그래밍 언어 |
| focus | string | | security/performance/all (기본: all) |

**Response:**
```json
{
  "review": "...",
  "issues": [
    {
      "Severity": "🔴 critical",
      "Location": "login() line 2",
      "Problem": "SQL Injection vulnerability",
      "Fix": "Use parameterized queries"
    }
  ],
  "summary": {"critical": 1, "warning": 0, "suggestion": 2, "total": 3}
}
```

---

### 3. 리팩토링

#### POST `/api/code/refactor`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | ✅ | 리팩토링할 코드 |
| goals | array | | readability/performance/dry/typing |

---

### 4. 디버깅

#### POST `/api/code/debug`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | ✅ | 문제 코드 |
| error | string | | 에러 메시지/스택 트레이스 |

**Response:**
```json
{
  "analysis": "Root cause: KeyError at line 2. The dictionary does not contain key 'b'.\nFix: Use dict.get('b', default) or check key existence..."
}
```

---

### 5. 테스트 생성

#### POST `/api/code/test`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | ✅ | 테스트할 코드 |
| framework | string | | pytest/jest/junit (기본: auto) |

---

### 6. 코드 설명 (한국어)

#### POST `/api/code/explain`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | ✅ | 설명할 코드 |
| level | string | | beginner/intermediate/advanced |

응답은 한국어로 제공됩니다. 개요, 단계별 설명, 복잡도 분석 포함.

---

### 7. 언어 변환

#### POST `/api/code/convert`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | ✅ | 원본 코드 |
| source | string | | 원본 언어 (auto-detect) |
| target | string | ✅ | 대상 언어 |

지원: Python, TypeScript, JavaScript, Java, Go, Rust, C#, Kotlin, Swift 등

---

### 8. 아키텍처 설계

#### POST `/api/code/architect`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| requirements | string | ✅ | 시스템 요구사항 |
| stack | string | | 기술 스택 제한 |
| scale | string | | small/medium/large |

응답 (한국어): 컴포넌트 다이어그램, API 설계, 데이터 모델, 기술 스택, 확장성, 보안.

---

### 9. 코딩 채팅 (멀티턴)

#### POST `/api/code/chat`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| messages | array | ✅ | 대화 이력 [{role, content}] |
| model | string | | 모델 |

```json
{
  "messages": [
    {"role": "user", "content": "FastAPI로 CRUD API 만들어줘"},
    {"role": "assistant", "content": "...이전 답변..."},
    {"role": "user", "content": "여기에 인증 추가해줘"}
  ]
}
```

---

### 10. 모델 목록

#### GET `/api/code/models`

사용 가능한 코딩 모델 목록 반환.

---

## Code RAG (코드 지식 검색 + 증강 생성)

코드베이스를 색인하고, 기존 코드를 참조하여 새 코드를 생성합니다.

### 11. 코드 색인

#### POST `/api/code/rag/index`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| content | string | ✅ | 코드 또는 문서 내용 |
| title | string | | 제목/파일명 |
| language | string | | 프로그래밍 언어 |
| category | string | | code/doc/api/pattern/snippet/config |
| tags | array | | 태그 |
| project | string | | 프로젝트명 |

```json
{
  "content": "class UserService:\n  def get_user(self, id): ...",
  "title": "UserService.py",
  "language": "python",
  "category": "code",
  "project": "my-backend"
}
```

### 12. 배치 색인

#### POST `/api/code/rag/index/batch`

```json
{
  "documents": [
    {"content": "...", "title": "api.ts", "language": "typescript"},
    {"content": "...", "title": "schema.sql", "language": "sql"}
  ],
  "project": "my-backend"
}
```

### 13. 코드 검색

#### POST `/api/code/rag/search`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | ✅ | 검색 쿼리 |
| n_results | int | | 결과 수 (기본 5, 최대 20) |
| language | string | | 언어 필터 |
| category | string | | 카테고리 필터 |
| project | string | | 프로젝트 필터 |

```json
{"query": "사용자 생성 함수", "project": "my-backend", "n_results": 5}
```

**Response:**
```json
{
  "results": [
    {"id": "xxx", "title": "UserService.py", "language": "python", "relevance": 0.85, "content": "..."},
    {"id": "yyy", "title": "api.ts", "relevance": 0.72, "content": "..."}
  ]
}
```

### 14. RAG 증강 코드 생성

#### POST `/api/code/rag/ask`

기존 코드를 검색하여 컨텍스트로 제공하고, LLM이 참조하여 답변합니다.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| question | string | ✅ | 코드 관련 질문 |
| n_context | int | | 참조 문서 수 (기본 5) |
| language | string | | 언어 필터 |
| project | string | | 프로젝트 필터 |

```json
{"question": "기존 UserService를 참고해서 PostService를 만들어줘", "project": "my-backend"}
```

**Response:**
```json
{
  "answer": "class PostService:\n  def get_post(self, id): ...",
  "sources": [
    {"id": "xxx", "title": "UserService.py", "relevance": 0.85}
  ],
  "model": "qwen2.5-coder:14b",
  "tokens": 680,
  "duration_ms": 10500
}
```

### 15. 색인 삭제

#### DELETE `/api/code/rag/delete/<doc_id>`

### 16. RAG 통계

#### GET `/api/code/rag/stats`

```json
{
  "total": 6,
  "by_category": {"code": 4, "schema": 1, "doc": 1},
  "by_language": {"python": 2, "typescript": 2, "sql": 2},
  "by_project": {"my-backend": 3, "my-frontend": 3}
}
```

---

## 활용 시나리오

### 시나리오 1: 새 프로젝트 시작
```
1. /api/code/architect → 아키텍처 설계
2. /api/code/generate → 핵심 코드 생성
3. /api/code/test → 테스트 생성
4. /api/code/rag/index → 생성된 코드 색인
```

### 시나리오 2: 기존 코드베이스 활용
```
1. /api/code/rag/index/batch → 기존 코드 전체 색인
2. /api/code/rag/ask → "기존 패턴 참고해서 새 기능 만들어줘"
3. /api/code/review → 생성된 코드 리뷰
4. /api/code/refactor → 리팩토링
```

### 시나리오 3: 코드 리뷰 파이프라인
```
1. /api/code/review → 이슈 감지
2. /api/code/debug → 버그 분석
3. /api/code/refactor → 수정
4. /api/code/test → 테스트 추가
```

---

## TypeScript 연동

```typescript
const CODE_AI_URL = 'https://example.org/ollama';

async function codeGenerate(prompt: string, language: string) {
  const res = await fetch(`${CODE_AI_URL}/api/code/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, language }),
  });
  return res.json();
}

async function codeReview(code: string, focus = 'all') {
  const res = await fetch(`${CODE_AI_URL}/api/code/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, focus }),
  });
  const { issues, summary } = await res.json();
  console.log(`Issues: ${summary.critical} critical, ${summary.warning} warning`);
  return issues;
}

async function ragAsk(question: string, project: string) {
  const res = await fetch(`${CODE_AI_URL}/api/code/rag/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, project }),
  });
  const { answer, sources } = await res.json();
  console.log(`Answer based on: ${sources.map(s => s.title).join(', ')}`);
  return answer;
}
```
