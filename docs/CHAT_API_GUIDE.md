# Chat API Guide — 세션 메모리 + 품질 검증

> WeRUBLLMManager v2.39.0 | 2026-05-14
> Base URL: `https://example.org/ollama`
> Swagger: `https://example.org/ollama/docs` (Ollama 태그)

---

## 1. 개요

`/api/chat` 엔드포인트는 두 가지 모드를 지원합니다:

| 모드 | 파라미터 | 기능 |
|------|---------|------|
| **스트리밍** (기본) | `stream: true` | NDJSON 실시간 응답, Fair Queue, LLM 캐시 |
| **비스트리밍** | `stream: false` 또는 `quality_check: true` | 품질 검증, 재시도, 모델 자동 선택, 세션 메모리 |

### v2.39.0 추가 기능
- **서버 사이드 대화 메모리**: `session_id`로 대화 이력 자동 저장/로드
- **한국어 품질 검증**: 30% 한국어 비율 검증 + 최대 2회 재시도
- **모델 자동 선택**: 질문 복잡도 분석 → simple(qwen2.5:7b) / complex(qwen2.5:14b)
- **LLM 기반 대화 압축**: 오래된 메시지를 요약하여 토큰 절감

> **대형 모델이 필요하면** → 기본 `/api/chat`(상주 qwen2.5:14b fast-lane)과 **별도**로 `/api/chat/heavy`
> 사용. 16GB VRAM 초과 대형 모델을 GPU+RAM 하이브리드(부분 오프로드)로 on-demand 구동하고 응답 후
> 상주 모델을 자동 복원한다. 상세: [API_REFERENCE.md §3-1](API_REFERENCE.md#3-1-heavy-tier--하이브리드-대형-모델-v2118-0) ·
> [hybrid-large-model-inference.plan.md](01-plan/features/hybrid-large-model-inference.plan.md)

---

## 2. 인증

| 방식 | 헤더 | 비고 |
|------|------|------|
| API Key | `Authorization: Bearer sk-xxx` | 필수 (외부 접근) |
| 로컬 바이패스 | 없음 | 127.0.0.1에서 호출 시 인증 불필요 |

---

## 3. 스트리밍 모드 (기본)

기존 방식 그대로. NDJSON 스트림으로 토큰 단위 응답.

### 요청

```bash
curl -X POST https://example.org/ollama/api/chat \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:14b",
    "messages": [
      {"role": "user", "content": "대한민국의 수도는?"}
    ]
  }'
```

### 응답 (NDJSON 스트림)

```json
{"model":"qwen2.5:14b","message":{"role":"assistant","content":"대한"},"done":false}
{"model":"qwen2.5:14b","message":{"role":"assistant","content":"민국"},"done":false}
{"model":"qwen2.5:14b","message":{"role":"assistant","content":"의 수도는 서울입니다."},"done":true}
```

### 캐시
- 동일한 (model + user messages) 조합은 캐시 히트 → 즉시 응답
- `no_cache: true`로 캐시 무시 가능

### Fair Queue
- 글로벌 동시 3건 + 유저별 1건 제한
- 초과 시 429 응답 (`retry_after` 참고)

---

## 4. 비스트리밍 모드 (품질 검증 + 세션)

### 요청 파라미터

| 파라미터 | 타입 | 필수 | 기본값 | 설명 |
|---------|------|:----:|--------|------|
| `messages` | array | ✅ | — | `[{role, content}]` 배열 |
| `model` | string | ❌ | 자동 선택 | 모델명 (생략 시 복잡도 기반 선택) |
| `stream` | boolean | ❌ | `true` | `false`로 설정 시 비스트리밍 |
| `quality_check` | boolean | ❌ | `false` | `true` 시 한국어 검증 + 재시도 활성화 |
| `session_id` | string | ❌ | 새 세션 생성 | UUID 형식 세션 ID |
| `user_id` | string | ❌ | `null` | 사용자 식별자 (세션 추적용) |
| `no_cache` | boolean | ❌ | `false` | 캐시 무시 |

### 4.1 기본 사용 (새 세션)

```bash
curl -X POST https://example.org/ollama/api/chat \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "안녕하세요, 오늘 날씨 어때요?"}],
    "stream": false,
    "quality_check": true
  }'
```

**응답**:
```json
{
  "answer": "안녕하세요! 현재 서울의 날씨 정보를 직접 확인할 수는 없지만...",
  "model_used": "qwen2.5:7b",
  "complexity": "simple",
  "session_id": "3180134b-a4c1-4e2f-b8a3-9d5e7f2c1a0b",
  "message_count": 2,
  "quality_check": {
    "korean_ratio": 0.9234,
    "validated": true,
    "threshold": 0.30,
    "attempts": 1
  }
}
```

### 4.2 대화 이어가기 (기존 세션)

첫 응답에서 받은 `session_id`를 다음 요청에 포함:

```bash
curl -X POST https://example.org/ollama/api/chat \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "그럼 우산 가져갈까요?"}],
    "session_id": "3180134b-a4c1-4e2f-b8a3-9d5e7f2c1a0b",
    "stream": false,
    "quality_check": true
  }'
```

**응답**:
```json
{
  "answer": "네, 비가 올 가능성이 있다면 우산을 챙기시는 것이 좋겠습니다...",
  "model_used": "qwen2.5:7b",
  "complexity": "simple",
  "session_id": "3180134b-a4c1-4e2f-b8a3-9d5e7f2c1a0b",
  "message_count": 4,
  "quality_check": {
    "korean_ratio": 0.9512,
    "validated": true,
    "threshold": 0.30,
    "attempts": 1
  }
}
```

> `message_count`가 2 → 4로 증가. 서버가 이전 대화를 기억합니다.

### 4.3 특정 사용자 추적

```bash
curl -X POST https://example.org/ollama/api/chat \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "계약서 검토해주세요"}],
    "user_id": "user-kim-42",
    "stream": false,
    "quality_check": true
  }'
```

---

## 5. 응답 필드 설명

### 비스트리밍 응답

| 필드 | 타입 | 설명 |
|------|------|------|
| `answer` | string | AI 응답 본문 |
| `model_used` | string | 실제 사용된 모델명 |
| `complexity` | string | 질문 난이도 (`simple` / `complex`) |
| `session_id` | string | 세션 UUID (다음 요청에 재사용 가능) |
| `message_count` | integer | 세션 내 누적 메시지 수 |
| `quality_check.korean_ratio` | float | 한국어 비율 (0.0~1.0) |
| `quality_check.validated` | boolean | 품질 검증 통과 여부 |
| `quality_check.threshold` | float | 검증 기준 (0.30 = 30%) |
| `quality_check.attempts` | integer | 시도 횟수 (1=첫 시도 통과, 2~3=재시도) |

### 에러 응답

| HTTP | 원인 | 응답 |
|------|------|------|
| 400 | messages 누락 | `{"error": "messages required"}` |
| 400 | 스트리밍에서 model 누락 | `{"error": "model required"}` |
| 401 | 인증 실패 | `{"error": "unauthorized"}` |
| 429 | 동시 처리 한도 초과 | `{"error": "server busy", "retry_after": 5}` |
| 500 | 서버 오류 | `{"error": "..."}` |

---

## 6. 모델 자동 선택

`model` 파라미터를 생략하면 질문 복잡도에 따라 자동 선택:

| 복잡도 | 판단 기준 | 선택 모델 | 응답 시간 |
|--------|---------|----------|----------|
| `simple` | 짧은 질문, 사실 확인, 정의 요청 | qwen2.5:7b | 2~3초 |
| `complex` | 긴 질문, 복합 주제, 분석 요청 | qwen2.5:14b | 4~6초 |

**분류 키워드 예시**:
- Simple: "뜻", "정의", "알려줘", "계산", "기준"
- Complex: "판례", "비교", "예외", "분석", "충돌"

> `model`을 명시하면 자동 선택을 무시하고 해당 모델을 사용합니다.

---

## 7. 세션 메모리 상세

### 7.1 동작 방식

```
1. 요청 (session_id 없음) → 새 UUID 세션 생성
2. LLM 응답 생성 + 품질 검증
3. user 메시지 + assistant 응답 → DB 저장
4. 응답에 session_id 포함 → 클라이언트 저장
5. 다음 요청에 session_id 포함 → 기존 세션에 메시지 추가
```

### 7.2 DB 스키마

**chat_sessions** 테이블:
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT (UUID) | 세션 고유 ID |
| user_id | TEXT | 사용자 식별자 |
| model | TEXT | 사용 모델 |
| created_at | TEXT (ISO 8601) | 생성 시각 |
| last_active_at | TEXT | 마지막 활동 |
| message_count | INTEGER | 누적 메시지 수 |

**chat_messages** 테이블:
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 자동 증가 PK |
| session_id | TEXT (FK) | 세션 참조 |
| role | TEXT | system / user / assistant |
| content | TEXT | 메시지 본문 (최대 50KB) |
| korean_ratio | REAL | 한국어 비율 (0.0~1.0) |
| created_at | TEXT (ISO 8601) | 생성 시각 |

### 7.3 대화 압축 (LLM Summarization)

장기 대화 시 토큰 절감을 위한 자동 압축:

| 턴 수 | 저장 방식 |
|------|---------|
| 1~10 | 전체 메시지 그대로 유지 |
| 11~20 | 오래된 1~10번 → LLM 요약 1개 + 최신 10개 |
| 21+ | 기존 요약 갱신 + 최신 10개 |

**압축 효과**: 50개 메시지 → 11개 (요약 1 + 최근 10), 토큰 78% 절감

### 7.4 세션 실패 시 동작

세션 관련 DB 오류가 발생해도 AI 응답은 정상 반환됩니다 (best-effort):
- 세션 저장 실패 → 응답에 `session_id` / `message_count` 없음
- AI 응답 자체는 영향 없음

---

## 8. 한국어 품질 검증

### 검증 프로세스

```
1. LLM 응답 수신
2. validate_korean_quality(response) → korean_ratio 계산
3. korean_ratio >= 0.30 이면 통과
4. 미달 시 RETRY_SUFFIX 시스템 프롬프트 추가 후 재시도
5. 최대 2회 재시도 (총 3회 시도)
6. 최종 결과 반환 (통과/미달 무관)
```

### RETRY_SUFFIX 내용
재시도 시 시스템 프롬프트에 "반드시 한국어로만 답변하세요" 강화 메시지 추가.

### 검증 미적용 사례
- `quality_check: false` (기본값)
- `stream: true` (스트리밍 모드)

---

## 9. Next.js 연동 예시

### 9.1 기본 헬퍼

```typescript
// lib/chatApi.ts
const GPU_API_URL = process.env.GPU_API_URL!;   // http://127.0.0.1:8585
const GPU_API_KEY = process.env.GPU_API_KEY!;    // sk-xxx

interface ChatResponse {
  answer: string;
  model_used: string;
  complexity: string;
  session_id?: string;
  message_count?: number;
  quality_check: {
    korean_ratio: number;
    validated: boolean;
    threshold: number;
    attempts: number;
  };
}

export async function chatWithAI(
  message: string,
  sessionId?: string,
  userId?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${GPU_API_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${GPU_API_KEY}`,
    },
    body: JSON.stringify({
      messages: [{ role: "user", content: message }],
      stream: false,
      quality_check: true,
      ...(sessionId && { session_id: sessionId }),
      ...(userId && { user_id: userId }),
    }),
  });

  if (res.status === 429) {
    const err = await res.json();
    throw new Error(`서버 바쁨: ${err.retry_after}초 후 재시도`);
  }

  if (!res.ok) throw new Error(`Chat API error: ${res.status}`);
  return res.json();
}
```

### 9.2 React Hook

```typescript
// hooks/useChat.ts
import { useState, useCallback } from "react";
import { chatWithAI, ChatResponse } from "@/lib/chatApi";

export function useChat(userId?: string) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Array<{role: string, content: string}>>([]);
  const [loading, setLoading] = useState(false);

  const sendMessage = useCallback(async (content: string) => {
    setLoading(true);
    setMessages(prev => [...prev, { role: "user", content }]);

    try {
      const res = await chatWithAI(content, sessionId ?? undefined, userId);

      if (res.session_id) setSessionId(res.session_id);
      setMessages(prev => [...prev, { role: "assistant", content: res.answer }]);

      return res;
    } finally {
      setLoading(false);
    }
  }, [sessionId, userId]);

  const resetSession = useCallback(() => {
    setSessionId(null);
    setMessages([]);
  }, []);

  return { messages, sendMessage, loading, sessionId, resetSession };
}
```

### 9.3 사용 예시

```tsx
function ChatPage() {
  const { messages, sendMessage, loading, sessionId } = useChat("user-123");

  return (
    <div>
      <p>Session: {sessionId ?? "새 대화"}</p>
      {messages.map((m, i) => (
        <div key={i} className={m.role}>{m.content}</div>
      ))}
      <input
        onKeyDown={(e) => {
          if (e.key === "Enter" && !loading) {
            sendMessage(e.currentTarget.value);
            e.currentTarget.value = "";
          }
        }}
        disabled={loading}
        placeholder="메시지 입력..."
      />
    </div>
  );
}
```

---

## 10. 스트리밍 vs 비스트리밍 비교

| 항목 | 스트리밍 (`stream: true`) | 비스트리밍 (`stream: false`) |
|------|:------------------------:|:---------------------------:|
| 응답 형식 | NDJSON 스트림 | JSON 단일 객체 |
| 실시간 표시 | ✅ 토큰 단위 | ❌ 전체 완성 후 |
| 한국어 검증 | ❌ | ✅ (30% threshold) |
| 재시도 | ❌ | ✅ (최대 2회) |
| 모델 자동 선택 | ❌ (model 필수) | ✅ (model 생략 가능) |
| 세션 메모리 | ❌ | ✅ (session_id) |
| LLM 캐시 | ✅ | ✅ |
| Fair Queue | ✅ | ✅ |
| 응답 메타데이터 | ❌ | ✅ (quality_check, complexity) |
| 적합한 용도 | 실시간 채팅 UI | API 연동, 백엔드 호출 |

---

## 11. 세션 관리 API (v2.39.0)

### GET /api/chat/sessions

세션 목록 조회.

```bash
curl -H "Authorization: Bearer sk-xxx" \
  "https://example.org/ollama/api/chat/sessions?user_id=user123&limit=10"
```

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `user_id` | 전체 | 특정 사용자 필터 |
| `limit` | 20 | 최대 100 |
| `offset` | 0 | 페이지네이션 |

### GET /api/chat/sessions/:id/messages

세션 메시지 조회.

```bash
curl -H "Authorization: Bearer sk-xxx" \
  "https://example.org/ollama/api/chat/sessions/550e8400-.../messages?limit=50"
```

### DELETE /api/chat/sessions/:id

세션 삭제 (메시지 CASCADE 삭제).

```bash
curl -X DELETE -H "Authorization: Bearer sk-xxx" \
  "https://example.org/ollama/api/chat/sessions/550e8400-..."
```

---

## 12. FAQ

**Q: session_id 없이 요청하면?**
A: 새 세션이 자동 생성됩니다. 응답의 `session_id`를 저장하면 이어서 대화 가능.

**Q: 스트리밍 모드에서 세션을 사용할 수 있나요?**
A: 현재 비스트리밍 모드에서만 세션 저장을 지원합니다. 스트리밍 모드는 기존 동작과 동일합니다.

**Q: 세션은 얼마나 유지되나요?**
A: SQLite에 영구 저장됩니다. 서버 재시작 후에도 유지.

**Q: quality_check 없이 session_id만 보내면?**
A: `stream: false`이면 세션 저장은 작동하지만 한국어 검증/재시도는 비활성화됩니다.

**Q: 429 에러가 나면?**
A: Fair Queue 동시 처리 한도 초과. `retry_after` 초 후 재시도하세요.

**Q: 모델을 직접 지정하면 자동 선택은?**
A: `model` 파라미터가 있으면 자동 선택을 무시하고 해당 모델을 사용합니다.

---

## 12. 관련 문서

| 문서 | 설명 |
|------|------|
| [API_REFERENCE.md](API_REFERENCE.md) | 전체 API 레퍼런스 |
| [WEB_INTEGRATION_GUIDE.md](WEB_INTEGRATION_GUIDE.md) | Next.js 웹 연동 통합 가이드 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 서버 아키텍처 |

---

*Last Updated: 2026-05-14 | v2.39.0*
