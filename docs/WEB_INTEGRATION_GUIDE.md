# WeRUBLLMManager 메인 웹 연동 통합 가이드

> **버전**: v2.39.0 | **최종 수정**: 2026-05-14
> **GPU 서버 Base URL**: `http://127.0.0.1:8585` (웹 서버에서 SSH 터널 경유)
> **외부 Base URL**: `https://example.org/ollama`
> **Swagger**: https://example.org/ollama/docs

---

## 1. 연동 아키텍처

```
[사용자 브라우저]
    ↓ HTTPS
[example.org] (Next.js :3000)
    ↓ 내부 HTTP (서버 측)
    ├── Authorization: Bearer {GPU_API_KEY}      ← 서버 간 인증 (env)
    ├── X-Tenant-Key: {TENANT_KEY}               ← 회사별 식별 (선택)
    ↓
[127.0.0.1:8585] (SSH 터널 → GPU 서버)
    ├── LLM 추론 (Ollama, qwen2.5:14b 등 10개 모델)
    ├── 이미지 생성 (ComfyUI on-demand, 9개 모델, 15개 스타일)
    ├── RAG 검색 (ChromaDB, bge-m3 1024차원)
    ├── HR Law (법률 86건 + 회사별 취업규칙 + 테넌트 통계)
    ├── Training (QLoRA Fine-tuning)
    └── Moderation (콘텐츠 판정)
```

---

## 2. 환경 설정

### 2.1 Next.js 환경 변수 (.env.local)

```env
# GPU 서버 (SSH 터널 경유)
GPU_API_URL=http://127.0.0.1:8585

# GPU 서버 API Key (WeRUBLLMManager에서 발급)
GPU_API_KEY=CHANGE_ME

# HR Law 테넌트 키 (회사별, 선택)
HR_LAW_TENANT_KEY=CHANGE_ME
```

### 2.2 API Key 발급

```bash
# GPU 서버에서 발급
ssh deploy@203.0.113.10 \
  "curl -s -X POST http://127.0.0.1:8585/api/keys \
    -H 'Content-Type: application/json' \
    -d '{\"name\": \"web-frontend\"}'"
# → {"key": "sk-xxxx...", "name": "web-frontend"}
```

### 2.3 공통 프록시 헬퍼

```typescript
// lib/gpuApi.ts
const GPU = process.env.GPU_API_URL!;
const KEY = process.env.GPU_API_KEY!;

export async function gpuFetch(
  path: string,
  options: {
    method?: string;
    body?: any;
    tenantKey?: string;
    stream?: boolean;
  } = {}
) {
  const headers: Record<string, string> = {
    "Authorization": `Bearer ${KEY}`,
  };
  if (options.body) {
    headers["Content-Type"] = "application/json";
  }
  if (options.tenantKey) {
    headers["X-Tenant-Key"] = options.tenantKey;
  }

  const res = await fetch(`${GPU}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  if (options.stream) return res;
  return res.json();
}
```

---

## 3. LLM 텍스트 생성 API

### 3.1 텍스트 생성 (Generate)

```
POST /api/generate
```

```typescript
// app/api/llm/generate/route.ts
export async function POST(req: Request) {
  const body = await req.json();

  // 스트리밍 응답
  const res = await gpuFetch("/api/generate", {
    method: "POST",
    body: {
      model: body.model || "qwen2.5:14b",
      prompt: body.prompt,
      system: body.system,     // 시스템 프롬프트 (선택)
      stream: true,            // NDJSON 스트리밍
    },
    stream: true,
  });

  // 스트리밍 프록시
  return new Response(res.body, {
    headers: { "Content-Type": "application/x-ndjson" },
  });
}
```

**비스트리밍 (한 번에 응답):**
```json
POST /api/generate
{"model": "qwen2.5:14b", "prompt": "한국의 수도는?", "stream": false}

→ {"response": "한국의 수도는 서울입니다.", "eval_count": 15, "total_duration": 1200000000}
```

### 3.2 채팅 (Chat) — v2.39.0 세션 메모리 지원

```
POST /api/chat
```

**스트리밍 모드** (기본, 하위 호환):
```json
{
  "model": "qwen2.5:14b",
  "messages": [
    {"role": "user", "content": "안녕하세요"}
  ],
  "stream": true
}
```
→ NDJSON 스트림 응답 (기존 동작 동일)

**비스트리밍 + 품질 검증 + 세션 메모리** (v2.39.0):
```json
{
  "messages": [
    {"role": "user", "content": "오늘 날씨 알려줘"}
  ],
  "stream": false,
  "quality_check": true,
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "user123"
}
```

| 파라미터 | 필수 | 기본값 | 설명 |
|---------|:----:|--------|------|
| `messages` | ✅ | — | 메시지 배열 |
| `model` | ❌ | 자동 선택 | 모델명 (생략 시 복잡도 기반 자동 선택) |
| `stream` | ❌ | `true` | 스트리밍 응답 여부 |
| `quality_check` | ❌ | `false` | 한국어 품질 검증 + 재시도 |
| `session_id` | ❌ | 새 세션 | UUID 세션 ID (비스트리밍 전용) |
| `user_id` | ❌ | `null` | 사용자 식별자 (세션 추적용) |

**비스트리밍 응답**:
```json
{
  "answer": "서울은 현재 맑은 날씨입니다...",
  "model_used": "qwen2.5:7b",
  "complexity": "simple",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "message_count": 4,
  "quality_check": {
    "korean_ratio": 0.92,
    "validated": true,
    "threshold": 0.30,
    "attempts": 1
  }
}
```

**Next.js 세션 연동 예시**:
```typescript
// 첫 대화 (새 세션 생성)
const res1 = await gpuFetch("/api/chat", {
  method: "POST",
  body: {
    messages: [{ role: "user", content: "안녕하세요" }],
    stream: false,
    quality_check: true,
  },
});
const data1 = await res1.json();
const sessionId = data1.session_id; // 저장

// 이어서 대화 (기존 세션)
const res2 = await gpuFetch("/api/chat", {
  method: "POST",
  body: {
    messages: [{ role: "user", content: "이전 대화 이어서" }],
    session_id: sessionId,
    stream: false,
    quality_check: true,
  },
});
const data2 = await res2.json();
console.log(data2.message_count); // 4 (누적)
```
```

### 3.3 사용 가능한 모델

```
GET /api/models
```

| 모델 | 크기 | 용도 |
|------|------|------|
| qwen2.5:14b | 9.0GB | 한국어 최고 품질 (기본) |
| qwen2.5:7b | 4.7GB | 한국어 대량 배치 |
| deepseek-r1:14b | 9.0GB | 추론/코딩 |
| phi4:14b | 9.1GB | 수학/논리 |
| gemma3:12b | 8.1GB | 다국어 분류 |
| llama3.2:3b | 2.0GB | 초고속 범용 |
| gemma2:2b | 1.6GB | 초고속 분류 |
| nomic-embed-text | 0.3GB | RAG 임베딩 (768차원) |
| bge-m3 | 1.2GB | RAG 임베딩 (1024차원, 다국어, **현재 기본**) |
| mxbai-embed-large | 0.7GB | RAG 임베딩 (1024차원, 영문) |

---

## 4. 배치 큐 API

대량 작업을 큐에 넣고 비동기 처리.

### 4.1 단건 제출

```
POST /api/queue/submit
{"prompt": "요약해줘: ...", "model": "qwen2.5:14b"}

→ {"job_id": "uuid", "status": "pending", "position": 3}
```

### 4.2 결과 조회

```
GET /api/queue/job/{job_id}

→ pending:    {"status": "pending", "position": 3, "queue_pending": 5, ...}
→ running:    {"status": "running", "position": 0, "queue_pending": 4, ...}
→ completed:  {"status": "completed", "response": "...", "tokens": 150, "duration_ms": 2500}
```

### 4.3 배치 제출

```
POST /api/queue/batch
{
  "jobs": [
    {"prompt": "질문 1", "model": "qwen2.5:7b"},
    {"prompt": "질문 2", "model": "qwen2.5:7b"},
    {"prompt": "질문 3", "model": "qwen2.5:7b"}
  ]
}

→ {"batch_id": "uuid", "job_ids": [...], "count": 3}
```

### 4.4 배치 상태/결과

```
GET /api/queue/batch/{batch_id}
GET /api/queue/batch/{batch_id}/results    ← NDJSON 다운로드
```

### 4.5 큐 상태

```
GET /api/queue/status
→ {"pending": 5, "running": 1, "completed": 534, "llm_pending": 3, "image_pending": 2, "worker_alive": true}
```

---

## 5. 이미지 생성 API

### 5.1 이미지 생성

```
POST /api/image/generate
{
  "prompt": "a knight in enchanted forest",
  "model": "dreamshaper",       // flux-schnell, flux-dev, sdxl, juggernaut, juggernaut-ragnarok, animagine, realvis, cyberrealistic
  "style": "concept-art",       // anime, illustration, photorealistic, pixel-art-character, lcm-fast, flux-realism 등
  "negative_prompt": "",        // 선택
  "width": 1024,                // 선택
  "height": 1024,               // 선택
  "steps": 8,                   // 선택
  "seed": 12345,                // 선택 (재현성)
  "character_id": "uuid",       // 선택 (캐릭터 일관성)
  "ip_adapter_strength": 0.7    // 선택 (캐릭터 강도)
}

→ {"job_id": "uuid", "model": "dreamshaper", "status": "pending", "position": 3}
```

### 5.2 상태 확인 + 이미지 조회

```
GET /api/image/status/{job_id}
→ pending:    {"status": "pending", "position": 2, "queue_pending": 3}
→ running:    {"status": "running", "position": 0, "queue_pending": 2}
→ completed:  {"status": "completed", "images": [{"filename": "ollama-admin_00520_.png"}]}

GET /api/image/view/{filename}
→ (PNG 이미지 바이너리)
```

### 5.3 프론트엔드 훅 (폴링)

```typescript
// hooks/useImageGenerate.ts
export function useImageGenerate() {
  const [status, setStatus] = useState<"idle"|"generating"|"completed"|"failed">("idle");
  const [imageUrl, setImageUrl] = useState<string|null>(null);

  const generate = async (params: ImageParams) => {
    setStatus("generating");
    const { job_id } = await gpuFetch("/api/image/generate", { method: "POST", body: params });

    const poll = setInterval(async () => {
      const s = await gpuFetch(`/api/image/status/${job_id}`);
      if (s.status === "completed") {
        clearInterval(poll);
        setImageUrl(`/api/image/view/${s.images[0].filename}`);
        setStatus("completed");
      } else if (s.status === "failed") {
        clearInterval(poll);
        setStatus("failed");
      }
    }, 2000);
  };

  return { generate, status, imageUrl };
}
```

### 5.4 모델/스타일 목록

```
GET /api/image/models
→ {models: [...], styles: [...], loras: [...]}
```

### 5.5 모델 추천

| 용도 | model | style |
|------|-------|-------|
| 만능 (빠름) | dreamshaper | concept-art |
| 초고속 | dreamshaper | lcm-fast |
| 애니메이션 | animagine | anime |
| 픽셀 아트 | dreamshaper | pixel-art-character |
| 실사 | juggernaut | photorealistic |
| 고품질 | flux-dev | none |
| 빠른 테스트 | flux-schnell | none |

### 5.6 캐릭터 일관성

```
POST /api/image/character/create        ← multipart (image + name)
GET  /api/image/characters
POST /api/image/generate                ← character_id 포함
GET  /api/image/character/{id}/images   ← 캐릭터별 생성 이력
```

### 5.7 갤러리/저장소

```
GET    /api/image/gallery               ← 이미지 목록
GET    /api/image/storage               ← 저장소 통계
POST   /api/image/cleanup               ← 오래된 이미지 정리
DELETE /api/image/delete/{filename}      ← 이미지 삭제
```

---

## 6. RAG (지식 검색) API

### 6.1 문서 색인

```
POST /api/rag/index
{
  "id": "doc-001",
  "text": "문서 내용...",
  "metadata": {"source": "manual", "category": "hr"},
  "collection": "werub_docs"
}
```

> `collection`으로 대상 컬렉션 지정(미지정=기본 `werub_docs`, v2.124.37 — index/ask/
> search/delete/stats 전 경로 동일). 같은 id 재색인은 upsert(멱등). 배치는
> `documents` 배열 요청당 최대 500건(초과 413).

**배치 색인:**
```json
{"documents": [
  {"id": "doc-001", "text": "...", "metadata": {}},
  {"id": "doc-002", "text": "...", "metadata": {}}
]}
```

### 6.2 유사 문서 검색

```
POST /api/rag/search
{"query": "연차 휴가 발생 기준", "top_k": 5}

→ {"results": [{"text": "...", "metadata": {...}, "score": 0.85}, ...]}
```

### 6.3 검색 + LLM 답변

```
POST /api/rag/ask
{"question": "연차 사용촉진 절차는?", "top_k": 5}

→ {"answer": "...", "sources": [...]}
```

### 6.4 통계/삭제

```
GET    /api/rag/stats?collection=werub_docs        → {"name","count","metadata"}
DELETE /api/rag/document/{doc_id}?collection=...   → {"ok": true}
```

> 삭제는 멱등(없는 id도 ok:true)이며 청크 색인분·키워드(FTS) 색인을 동반
> 정리한다(v2.124.38~39). DELETE도 레이트리밋(120/min)에 포함되므로 대량 정리는
> 분당 ~100건 페이스 + 429 백오프 권장.

---

## 7. HR Law (법률 SaaS) API

> 상세: [HR_LAW_API_GUIDE.md](HR_LAW_API_GUIDE.md)

### 7.1 법률 질의응답

```
POST /api/hr-law/ask
X-Tenant-Key: {TENANT_KEY}     ← 선택 (있으면 회사 취업규칙 포함)

{"question": "연차 사용촉진 절차를 알려주세요", "category": "annual_leave"}

→ {"answer": "...", "sources": [...], "tenant": "ABC주식회사"}
```

### 7.2 계산기

```
POST /api/hr-law/calculate/annual-leave    ← {"start_date": "2023-03-15"}
POST /api/hr-law/calculate/overtime        ← {"hourly_wage": 15000, "extended_hours": 10, ...}
POST /api/hr-law/calculate/severance       ← {"start_date": "2020-01-01", "end_date": "2026-04-29", "avg_daily_wage": 150000}
```

### 7.3 회사별 취업규칙

```
GET  /api/hr-law/rules/template         ← 표준 양식 (11섹션, 45항목)
GET  /api/hr-law/rules/completeness     ← 완성도 체크 (%)
POST /api/hr-law/rules/generate         ← 섹션별 초안 자동 생성
POST /api/hr-law/rules                  ← 등록
GET  /api/hr-law/rules                  ← 목록
```

### 7.4 테넌트 관리 (관리자)

```
POST /api/hr-law/tenant/register        ← 회사 등록 → API 키 발급
GET  /api/hr-law/tenants                ← 목록
GET  /api/hr-law/tenant/usage           ← 사용량
```

---

## 8. 콘텐츠 모더레이션 API

### 8.1 실시간 판정

```
POST /api/mod/judge
{"text": "판정할 텍스트"}

→ {"action": "allow", "confidence": 0.95, "categories": ["normal"]}
```

### 8.2 보정 수집

```
POST /api/mod/collect
{"text": "텍스트", "ai_action": "block", "admin_action": "allow"}
```

### 8.3 통계

```
GET /api/mod/stats
→ {"raw_count": 500, "refined_count": 320, "pending": 180}
```

---

## 9. Training API

### 9.1 학습 실행

```
POST /api/training/start
{
  "model": "qwen2.5:7b",
  "data_file": "training_data.jsonl",
  "epochs": 3,
  "direction_preset": "comprehensive"
}
```

### 9.2 학습 상태/결과

```
GET /api/training/status                    ← 현재 학습 상태
GET /api/training/progress                  ← 로그 + 메트릭
GET /api/training/runs                      ← 학습 이력
GET /api/training/runs/{id}/report          ← 종합 리포트
GET /api/training/runs/{id}/misclassified   ← 오분류 분석
```

### 9.3 모델 적용/롤백

```
POST /api/training/deploy/{run_id}    ← 학습된 모델 배포
POST /api/training/rollback/{run_id}  ← 이전 모델로 복원
```

### 9.4 학습 자료 관리

```
GET    /api/training/files
POST   /api/training/files/upload         ← multipart
GET    /api/training/files/download/{f}
GET    /api/training/files/preview/{f}     ← 처음 20줄
DELETE /api/training/files/{f}
```

---

## 10. 시스템/모니터링 API

### 10.1 헬스 체크

```
GET /api/health
→ {"healthy": true, "checks": {"ollama": {"ok": true}, "queue": {"pending": 0}, ...}}
```

### 10.2 서버 리소스

```
GET /api/server/stats
→ {"cpu": {"usage_pct": 12.3}, "ram": {...}, "gpu": {"name": "RTX 5080", "memory_used_mb": 10800}, "disk": {...}}
```

### 10.3 버전

```
GET /api/version
→ {"version": "2.17.0", "changelog": [...]}
```

### 10.4 Prometheus 메트릭

```
GET /metrics
→ ollama_queue_pending 0
   ollama_queue_completed_total 534
   ollama_gpu_memory_used_mb 10800
   ...
```

---

## 11. Next.js API Routes 전체 구조 (권장)

```
app/api/
├── llm/
│   ├── generate/route.ts         → POST /api/generate
│   ├── chat/route.ts             → POST /api/chat
│   └── models/route.ts           → GET  /api/models
├── queue/
│   ├── submit/route.ts           → POST /api/queue/submit
│   ├── job/[jobId]/route.ts      → GET  /api/queue/job/{id}
│   ├── batch/route.ts            → POST /api/queue/batch
│   └── status/route.ts           → GET  /api/queue/status
├── image/
│   ├── generate/route.ts         → POST /api/image/generate
│   ├── status/[jobId]/route.ts   → GET  /api/image/status/{id}
│   ├── view/[filename]/route.ts  → GET  /api/image/view/{f}
│   ├── models/route.ts           → GET  /api/image/models
│   ├── gallery/route.ts          → GET  /api/image/gallery
│   └── character/
│       ├── create/route.ts       → POST (multipart)
│       └── [id]/images/route.ts  → GET
├── rag/
│   ├── index/route.ts            → POST /api/rag/index
│   ├── search/route.ts           → POST /api/rag/search
│   └── ask/route.ts              → POST /api/rag/ask
├── hr-law/
│   ├── ask/route.ts              → POST /api/hr-law/ask
│   ├── search/route.ts           → POST /api/hr-law/search
│   ├── calculate/
│   │   ├── annual-leave/route.ts → POST
│   │   ├── overtime/route.ts     → POST
│   │   └── severance/route.ts    → POST
│   ├── rules/
│   │   ├── route.ts              → POST/GET
│   │   ├── template/route.ts     → GET
│   │   ├── completeness/route.ts → GET
│   │   └── generate/route.ts     → POST
│   └── tenant/
│       ├── register/route.ts     → POST
│       └── usage/route.ts        → GET
├── mod/
│   ├── judge/route.ts            → POST /api/mod/judge
│   └── stats/route.ts            → GET  /api/mod/stats
├── training/
│   ├── status/route.ts           → GET  /api/training/status
│   └── runs/route.ts             → GET  /api/training/runs
└── system/
    ├── health/route.ts           → GET  /api/health
    └── stats/route.ts            → GET  /api/server/stats
```

---

## 12. 에러 코드

| 코드 | 설명 |
|------|------|
| 200 | 정상 |
| 400 | 필수 파라미터 누락 |
| 401 | 인증 실패 (Bearer/X-Tenant-Key) |
| 404 | 리소스 없음 (job_id, model 등) |
| 413 | 요청 크기 초과 (프롬프트 32KB, 배치 1000건) |
| 429 | Rate limit 또는 일일 한도 초과 |
| 500 | 서버 오류 (LLM 타임아웃 등) |
| 503 | 서비스 불가 (Ollama 중단, GPU 과부하) |

---

## 13. 보안 가이드

| 항목 | 구현 |
|------|------|
| API Key 은닉 | Next.js 서버 측 env, 프론트엔드 미노출 |
| 프롬프트 검증 | 길이 제한 32KB, 특수문자 필터링 |
| Rate Limiting | GPU 서버: 120/분 기본, 프론트엔드: 사용자별 추가 제한 |
| CORS | Next.js API Route 경유 → 동일 도메인, CORS 불필요 |
| 이미지 접근 | /api/image/view → 인증 필요 (또는 서명 URL) |
| 회사 데이터 분리 | X-Tenant-Key로 테넌트 격리 |

---

## 14. 엔터프라이즈 웹 연동 — HR Law + Game NPC

### 14.1 HR Law 엔터프라이즈 연동

엔터프라이즈 웹에서 HR Law 기능을 호출하여 SaaS 형태로 서비스합니다.

```
[엔터프라이즈 웹]
    ↓ 내부 HTTP
    ├── Authorization: Bearer {GPU_API_KEY}    ← 서버 간 인증
    ├── X-Tenant-Key: {TENANT_KEY}            ← 회사별 식별
    ↓
[GPU 서버 /api/hr-law/*]
```

#### 회사 온보딩 플로우

```typescript
// 1. 회사 등록 → API 키 발급
const tenant = await gpuFetch("/api/hr-law/tenant/register", {
  method: "POST",
  body: { name: "ABC주식회사", plan: "standard", contact_email: "hr@abc.com" },
});
// → tenant.api_key: "CHANGE_ME"

// 2. 취업규칙 등록
await gpuFetch("/api/hr-law/rules", {
  method: "POST",
  headers: { "X-Tenant-Key": tenant.api_key },
  body: { title: "연차규정", content: "제15조...", category: "annual_leave" },
});

// 3. 법률 질의 (스트리밍)
const res = await fetch(`${GPU_URL}/api/hr-law/ask/stream`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${GPU_API_KEY}`,
    "X-Tenant-Key": tenant.api_key,
  },
  body: JSON.stringify({ question: "연차 규정 알려줘", language: "ko" }),
});
// SSE 스트리밍: data: {type:"token", token:"..."} → 실시간 표시
```

#### HR Law API 전체 목록 (18개)

| 메서드 | 엔드포인트 | X-Tenant-Key | 설명 |
|--------|-----------|:---:|------|
| POST | `/api/hr-law/ask` | 선택 | 법률 질의응답 (RAG + LLM) |
| POST | `/api/hr-law/ask/stream` | 선택 | 법률 질의 (SSE 스트리밍) |
| POST | `/api/hr-law/search` | - | 법률 키워드 검색 |
| POST | `/api/hr-law/calculate/annual-leave` | - | 연차 계산 |
| POST | `/api/hr-law/calculate/overtime` | - | 초과근무수당 계산 |
| POST | `/api/hr-law/calculate/severance` | - | 퇴직금 계산 |
| GET | `/api/hr-law/rules/template` | - | 표준 취업규칙 양식 |
| GET | `/api/hr-law/rules/completeness` | 필수 | 완성도 체크 |
| POST | `/api/hr-law/rules/generate` | 필수 | 초안 LLM 자동 생성 |
| POST | `/api/hr-law/rules/review` | 필수 | 법위반 검토 |
| POST/GET | `/api/hr-law/rules` | 필수 | 취업규칙 CRUD |
| POST | `/api/hr-law/tenant/register` | - | 회사 등록 (관리자) |
| GET | `/api/hr-law/tenants` | - | 회사 목록 (관리자) |
| GET | `/api/hr-law/tenant/usage` | 선택 | 사용량 조회 |

### 14.2 Game NPC 엔터프라이즈 연동

게임 서버에서 NPC AI를 호출하여 대화/성장/시나리오를 제공합니다.

```
[게임 서버]
    ↓ HTTP
    ├── Authorization: Bearer {GPU_API_KEY}
    ├── X-Game-Key: {GAME_KEY}               ← 게임사별 식별
    ├── player_id: "uuid"                    ← 플레이어별 식별
    ↓
[GPU 서버 /api/game/*]
```

#### 게임 연동 예시

```typescript
// 1. NPC 대화 (플레이어별 독립)
const dialogue = await gpuFetch("/api/game/dialogue/player", {
  method: "POST",
  headers: { "X-Game-Key": GAME_KEY },
  body: {
    player_id: "player-uuid-123",
    npc_id: "knight-001",
    message: "이 검을 받아줘",
    player_name: "용사",
  },
});
// → { npc: "아르테미스", response: "감사합니다...", exp_earned: 8, level: 2 }

// 2. 히로인 대화 (호감도 시스템)
const talk = await gpuFetch("/api/game/heroine/talk", {
  method: "POST",
  headers: { "X-Game-Key": GAME_KEY },
  body: {
    player_id: "player-uuid-123",
    heroine_id: "heroine-001",
    message: "오늘 예쁘다",
    player_name: "주인공",
  },
});
// → { heroine: "유키", response: "흥! 바보...", affinity: { value: 35, stage: "아는 사이" } }

// 3. 공유 NPC (사용자 간 영향 전파)
const interact = await gpuFetch("/api/game/shared/interact", {
  method: "POST",
  headers: { "X-Game-Key": GAME_KEY },
  body: {
    player_id: "player-A",
    npc_id: "shared-npc-001",
    message: "선물을 드립니다",
    action_type: "gift",
    player_name: "용사A",
  },
});
// → { mood: { after: 65, label: "기분좋음" }, reputation: { after: 70, title: "친구" } }
// → 다른 플레이어가 대화할 때 NPC의 기분 변화가 반영됨
```

#### Game NPC API 전체 목록 (34개)

| 그룹 | API 수 | 설명 |
|------|--------|------|
| 게임사 관리 | 2 | 등록/목록 |
| NPC 템플릿 | 4 | CRUD + AI 생성 |
| NPC 인스턴스 | 3 | 플레이어별 독립 |
| 대화 | 4 | 1:1/다자/플레이어/이력 |
| 성장 | 2 | 레벨업/상태 |
| 시나리오 | 2 | 생성/목록 |
| 일러스트 | 2 | 캐릭터/장면 |
| 세계관 | 2 | 등록/검색 |
| 공유 NPC | 7 | 상호작용/추억/평판/영향 |
| 히로인 | 8 | 대화/데이트/선물/고백 |

### 14.3 Admin Console (관리 UI)

| URL | 설명 |
|-----|------|
| `/ollama/admin-console/` | 통합 관리 콘솔 (Next.js) |
| `/ollama/admin-console/hr-law/chat/` | 법률 상담 (스트리밍) |
| `/ollama/admin-console/hr-law/calc/` | 계산기 (달력 컴포넌트) |
| `/ollama/admin-console/game/chat/` | NPC 대화 테스트 |
| `/ollama/admin-console/game/heroine-chat/` | 히로인 대화 테스트 |

기술 스택: Next.js 15 + TypeScript + Tailwind CSS (정적 export)


---

## 10. Chat Fair Queue (v2.24.0)

> 모든 LLM 호출에 공정 큐가 자동 적용됩니다. 별도 연동 불필요.

### 10.1 동작 방식
- **글로벌 동시 3건** + **유저별 동시 1건** 제한
- 초과 시 FIFO 대기열 진입 (최대 20건, 60초 타임아웃)
- 429 응답 시 `retry_after` 필드 참고

### 10.2 큐 상태 조회

```typescript
const stats = await gpuFetch("/api/chat/queue/stats");
// { active_total, max_global, per_user, wait_queue, max_wait_queue }
```

### 10.3 429 처리 예시

```typescript
const res = await gpuFetch("/api/chat", { method: "POST", body: {...} });
if (res.status === 429) {
  const data = await res.json();
  console.log(`서버 바쁨: ${data.message}, ${data.retry_after}초 후 재시도`);
  await sleep(data.retry_after * 1000);
  // 재시도
}
```

---

## 11. GPU Resource Manager (v2.25.0)

> ComfyUI는 on-demand 모드입니다. 이미지 생성 시 자동 시작/종료됩니다.

### 11.1 상태 조회

```typescript
const status = await gpuFetch("/api/gpu/status");
// {
//   gpu: { vram_used_mb, vram_total_mb, gpu_util_pct, temp_c },
//   resource_manager: { comfyui_running, idle_timer_active },
//   chat_queue: { active_total, wait_queue, ... }
// }
```

### 11.2 이미지 생성 시 주의사항
- ComfyUI 콜드스타트 ~5초 소요 (첫 이미지 요청 시)
- 이미지 생성 중 LLM VRAM 자동 해제 → 이미지 완료 후 LLM 자동 복원
- 5분 idle 후 ComfyUI 자동 종료

---

## 12. 한국어 품질 검증 (v2.29.0)

> NPC 대화, 히로인 대화, HR Law 질의에 자동 적용됩니다.

### 12.1 동작 방식
1. LLM 응답 → `_force_korean` (한자/CJK 구두점 제거)
2. `_korean_quality_check` (한글 비율 30% 미만 감지)
3. 불완전 시 시스템 프롬프트 강화 후 최대 2회 자동 재시도

### 12.2 웹 프론트엔드에서 추가 검증이 필요한 경우

```typescript
function hasKorean(text: string): boolean {
  const korean = text.match(/[가-힣]/g)?.length || 0;
  const total = text.replace(/\s/g, "").length;
  return total > 0 && korean / total > 0.3;
}
```

---

## 13. HR Law 테넌트 관리 API (v2.29.0)

### 13.1 API 키 재발급

```typescript
const result = await gpuFetch(`/api/hr-law/tenant/${tenantId}/regenerate-key`, {
  method: "POST"
});
// { ok: true, api_key: "hr-새로운키", tenant_id: "..." }
// ⚠️ 기존 키는 즉시 무효화
```

### 13.2 테넌트 호출 통계

```typescript
const stats = await gpuFetch(`/api/hr-law/tenant/${tenantId}/stats?days=30&limit=20`);
// {
//   total_queries: 150,
//   daily: [{ date: "2026-05-05", count: 12 }, ...],
//   by_category: [{ category: "annual_leave", count: 45 }, ...],
//   recent_queries: [{ question: "연차...", created_at: "...", ... }, ...]
// }
```

### 13.3 테넌트 목록 (페이지네이션)

```typescript
const data = await gpuFetch("/api/hr-law/tenants?page=1&per_page=10&search=위루비");
// { tenants: [...], pagination: { page, per_page, total, total_pages } }
```


---

## Embedding API (v2.29.0)

```bash
# New API
POST /api/embed
{"model": "nomic-embed-text", "input": "텍스트"}
# → {"embeddings": [[0.1, 0.2, ...]], "model": "nomic-embed-text"}

# Legacy API
POST /api/embeddings
{"model": "nomic-embed-text", "prompt": "텍스트"}
# → {"embedding": [0.1, 0.2, ...]}
```

## Fact Check API (v2.29.0)

```bash
POST /api/ai/fact-check
{
  "text": "검증할 글 본문",
  "context": "참조 데이터 (선택, 정확도 향상)",
  "model": "qwen2.5:14b"
}
# → {"claims": [...], "summary": {"total": 3, "correct": 1, "incorrect": 2, "score": 0.0}}
```

Verdicts: `correct`, `incorrect`, `unverifiable`, `misleading`


---

## Medical AI API (v2.29.0)

### 테넌트 등록


### 진료 요약 (SOAP)


### 감별진단


### 의학 참고 (RAG 벡터 검색)


### 의료진 전용 (영어 + 한국어 번역)


상세: [Medical API Guide](MEDICAL_API_GUIDE.md) 참조


---

## Symptom Triage API (v2.29.0, 공개 API)

```
POST /api/medical/triage
{"symptom": "머리가 아프고 열이 나요", "age": 45}
```

응답:
- `recommendations[].department` / `department_code` / `confidence`
- `emergency_warning` (응급 시 119 안내)
- `guidance` (자연어 안내문)

대화형: `POST /api/medical/triage/follow-up`
진료과 목록: `GET /api/medical/triage/departments`


---

## Orchestrator API (v2.31.0)

```
POST /api/orchestrator/run
{"workflow": "quick_reception", "input": {"patient_name": "김영희", "symptom": "두통"}}

POST /api/orchestrator/chat
{"message": "김영희 머리 아프다고 접수해줘"}

GET /api/orchestrator/workflows
```

워크플로우: quick_reception, full_consultation, nurse_assessment, bill_calculate, claim_review


---

## Coding AI API (v2.32.0)

```
POST /api/code/generate
{"prompt": "Python LRU Cache class", "language": "python"}

POST /api/code/review
{"code": "def login(u,p): ...", "focus": "security"}

POST /api/code/debug
{"code": "...", "error": "TypeError: ..."}

POST /api/code/convert
{"code": "const add = ...", "source": "typescript", "target": "python"}

POST /api/code/chat
{"messages": [{"role": "user", "content": "FastAPI로 CRUD API 만들어줘"}]}

GET /api/code/models
```

기본 모델: qwen2.5-coder:14b (코드 특화, 128K 컨텍스트)
빠른 모델: deepseek-coder-v2:16b (MoE, 122tok/s)


---

## Code RAG API (v2.33.0)

코드베이스를 색인하고, 기존 코드를 참조하여 새 코드를 생성합니다.

```
POST /api/code/rag/index
{"content": "class UserService: ...", "title": "UserService.py", "language": "python", "project": "myapp"}

POST /api/code/rag/index/batch
{"documents": [...], "project": "myapp"}

POST /api/code/rag/search
{"query": "사용자 생성", "project": "myapp"}

POST /api/code/rag/ask
{"question": "기존 UserService 참고해서 PostService 만들어줘", "project": "myapp"}

GET /api/code/rag/stats
```

상세: [Coding API Guide](CODING_API_GUIDE.md) 참조
