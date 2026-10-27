# WeRU.B AI Server — 통합 API 레퍼런스

> Version: **v2.124.62** | **557 paths**(OpenAPI 실측) | 36 Blueprint | **38 모델**(설치 기준) |
> Tool Calling 28도구 + Meeting/STT + Counseling + Korean 24 + Story/Novel + Medical + Dashboard + OpenAI 호환 `/v1`
> Base URL: `https://example.org/ollama`  
> Swagger: `https://example.org/ollama/docs` — **스펙이 정본**(이 문서는 사용 맥락·계약 주석)  
> 최근 갱신 2026-07-30: `/api/chat` 입력 계약(§3-0-a) · 사전 적재 모델(§3-0-b) · 이미지 포맷·용량

---

## 목차

1. [서버 정보](#1-서버-정보)
2. [인증](#2-인증)
3. [Korean Language (24 API)](#3-korean-language) ← **v2.61.0 신규**
4. [Ollama 프록시 (23 API)](#4-ollama-프록시)
4A. [Heavy Tier — 하이브리드 대형 모델 (3 API)](#3-1-heavy-tier--하이브리드-대형-모델-v2118-0) ← **v2.118.0 신규**
5. [Queue 배치 (16 API)](#5-queue-배치)
6. [Image 생성 (15 API)](#6-image-생성)
7. [HR Law 법률 AI (23 API)](#7-hr-law)
8. [Game NPC AI (40 API)](#8-game-npc)
9. [Medical AI 진료 (22+2 API)](#9-medical-ai)
10. [Medical CLI 업무 자동화 (7 API)](#10-medical-cli)
11. [Symptom Triage 증상 추천 (4 API)](#11-symptom-triage)
12. [Coding AI 코드 생성 (17 API)](#12-coding-ai)
13. [Orchestration Platform (13 API)](#13-orchestration-platform)
14. [Enhanced Chat & Writing (11 API)](#14-enhanced)
15. [System/Activity/RAG (30 API)](#15-system)
16. [Story Engine (31 API)](#16-story-engine)
17. [Novel Writer (82 API)](#17-novel-writer)
18. [Medical Ensemble (1 API)](#18-medical-ensemble)
19. [Medical Law (14 API)](#19-medical-law)
19A. [ERP/Notice 사전심사 + 청구코드 (15 API)](#19a-erp-notice) ← **v2.87~v2.92 신규**
19B. [DUR 7유형 금기/주의 (4 API)](#19b-dur) ← **v2.94.0 / v2.97.0 7유형**
20. [Counseling Orchestration (6 API)](#20-counseling) ← **v2.62.0 신규**
21. [Meeting Analysis (6 API)](#21-meeting-analysis) ← **v2.63.0 신규**
22. [Tool Calling (2 API + 20 도구)](#22-tool-calling) ← **v2.64.0 신규**
23. [Dashboard API (9 API)](#23-dashboard-api)
24. [모델 현황](#24-모델)
25. [에러 코드](#25-에러-코드)

---

## 3. Korean Language (24 API) — v2.61.0

> 상세 가이드: [docs/KOREAN_API_GUIDE.md](KOREAN_API_GUIDE.md)

한국어 텍스트 분석·교정·변환·진단 통합 API. 외부 의존 0건 (순수 Python + Ollama + KSS).

### 오케스트레이션

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/korean/pipeline` | 전체 파이프라인 (filter→analyze→spellcheck→readability→cohesion) |
| POST | `/api/korean/novel-diagnosis` | 소설 종합 진단 8항목 + A~D 등급 |

### 분석 (10건)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/korean/analyze` | AI 패턴 탐지 22메트릭 + z-score + risk_band |
| POST | `/api/korean/readability` | 가독성 5등급 (초등~학술) |
| POST | `/api/korean/cohesion` | 응집성 (ASO/접속사/주제 일관성) |
| POST | `/api/korean/stats` | 텍스트 통계 (어휘 빈도/문장 유형/어휘 풍부도) |
| POST | `/api/korean/honorific` | 경어법 5단계 판별 + 혼용 감지 |
| POST | `/api/korean/repetition` | 반복/중복 탐지 (문장/어구/어미) |
| POST | `/api/korean/pov` | 시점 판별 (1인칭/3인칭/혼합) |
| POST | `/api/korean/sensory` | 감각 묘사 5감 + Show Don't Tell |
| POST | `/api/korean/expressiveness` | 생동감 (의성어 50종 + 의태어 40종) |
| POST | `/api/korean/metrics/detail` | 개별 메트릭 선택 분석 |

### 교정/변환 (3건)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/korean/spellcheck` | 맞춤법 검사 (Ollama LLM) |
| POST | `/api/korean/filter` | 비한국어 제거 + 한국어 비율 |
| POST | `/api/korean/style-transfer` | 문체 변환 6종 (존댓말/반말/문어/구어/나레이션/대화) |

### 소설 전용 (5건)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/korean/dialogue-analysis` | 대사/묘사/내면 비율 + 화자 식별 |
| POST | `/api/korean/character-voice` | 캐릭터 말투 일관성 |
| POST | `/api/korean/diff` | 텍스트 비교 (문장 단위 diff) |
| POST | `/api/korean/feedback` | AI 5항목 글쓰기 피드백 |
| POST | `/api/korean/check` | 한국어 비율 검증 |

### 참조 (4건)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/korean/rules` | S1/S2 패턴 규칙 |
| GET | `/api/korean/rules/prompt` | S1 프롬프트 텍스트 |
| GET | `/api/korean/metrics` | 22개 메트릭 레지스트리 |
| GET | `/api/korean/status` | 시스템 상태 (KSS/모듈/버전) |

---

## 1. 서버 정보

| 항목 | 값 |
|------|-----|
| OS | Ubuntu 24.04, RTX 5080 16GB |
| Framework | Flask + Gunicorn (gthread 2W×8T) |
| LLM Engine | Ollama v0.21.0 |
| Vector DB | ChromaDB (bge-m3 1024d, nomic-embed 768d) |
| Database | SQLite WAL (queue.db, medical.db, clinic_codes.db, chat_sessions/messages) |

---

## 2. 인증

| 방식 | 헤더 | 대상 |
|------|------|------|
| API Key | `Authorization: Bearer sk-xxx` | Ollama, Queue, System, Image 등 |
| Medical Key | `X-Medical-Key: med-xxx` | Medical AI, CLI |
| Game Key | `X-Game-Key: game-xxx` | Game NPC |
| HR Law Key | `X-Tenant-Key: hr-xxx` | HR Law |
| 없음 | — | Triage, Coding, Code RAG |

---

## 3. Ollama 프록시

LLM 추론, 모델 관리, 임베딩.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Ollama 상태 |
| GET | `/api/models` | 설치된 모델 목록 |
| POST | `/api/generate` | 텍스트 생성 (스트리밍) |
| POST | `/api/chat` | 채팅 (스트리밍/세션 메모리) |
| POST | `/api/embed` | 임베딩 생성 |
| POST | `/api/embeddings` | 임베딩 (레거시) |
| POST | `/api/pull` | 모델 다운로드 |
| POST | `/api/delete` | 모델 삭제 |
| GET | `/api/version` | 버전 + 체인지로그 |
| GET/POST | `/api/config` | 설정 조회/변경 |
| GET/POST | `/api/ollama/perf` | Ollama 성능 설정 |
| POST | `/api/ollama/restart` | Ollama 재시작 |
| GET | `/api/gpu/status` | GPU 상태 |
| GET/POST | `/api/models/residency` | **사전 적재(상주) 정책 조회·전환** (v2.124.62, admin) |
| POST | `/api/models/warm` | 모델 예열 (`keep_alive` 지정 가능) |
| POST | `/api/models/unload` | 모델 즉시 반납 — **정책은 그대로 두고 VRAM만 비움** |
| POST | `/api/service/<action>` | 서비스 제어 (start/stop/restart) |
| GET | `/api/chat/queue/stats` | 채팅 큐 통계 |
| GET | `/api/chat/sessions` | 세션 목록 (v2.39.0) |
| GET | `/api/chat/sessions/:id/messages` | 세션 메시지 조회 |
| DELETE | `/api/chat/sessions/:id` | 세션 삭제 |
| CRUD | `/api/keys` | API 키 관리. ⚠️**발급 응답이 원본 키를 보는 유일한 기회**(평문 미저장·2026-07-29). 재확인 엔드포인트(`/reveal`)는 **410으로 폐지** — 분실 시 재발급 |
| POST | `/api/ai/fact-check` | 팩트 체크 |

```bash
# 텍스트 생성
curl -X POST $BASE/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:14b", "prompt":"안녕", "stream":false}'

# 채팅 (비스트리밍 + 품질 검증 + 세션)
curl -X POST $BASE/api/chat \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"안녕하세요"}],
    "stream": false,
    "quality_check": true
  }'
# → {"answer":"...", "model_used":"qwen2.5:7b", "session_id":"uuid", "message_count":2, ...}

# 채팅 (기존 세션 이어가기)
curl -X POST $BASE/api/chat \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"이전 대화 계속"}],
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "stream": false,
    "quality_check": true
  }'

# 임베딩
curl -X POST $BASE/api/embed \
  -d '{"model":"nomic-embed-text", "input":"텍스트"}'
```

Chat 상세: [CHAT_API_GUIDE.md](CHAT_API_GUIDE.md) — 세션 메모리, 품질 검증, Next.js 연동 예시

### 3-0-a. `/api/chat`·`/api/generate` 입력 계약 (2026-07-30 갱신)

호출자가 준 값이 **조용히 버려지던 4건을 봉합**했다(v2.124.59~60). 아래는 이제 전부 전달된다.

| 필드 | 동작 | 비고 |
|------|------|------|
| `messages[].images[]` / `images[]` | 비전 입력 전달 | `data:image/...;base64,` **접두는 서버가 제거** |
| 이미지 포맷 | PNG · JPEG · GIF · **WEBP** | WEBP는 **서버가 PNG로 변환**(v2.124.60). 원격 URL은 미지원(SSRF 정책) |
| `think` | thinking 지원 모델에만 전달 | 미지원 모델엔 **자동 생략**(그대로 보내면 업스트림이 요청 전체를 거절). ⚠️`false`여도 완전 억제되지 않음 = **엔진·모델 특성** |
| `options` | temperature·num_ctx 등 그대로 전달 | 종전 `/api/chat`은 무시했음 |

**응답 캐시**(1시간): 캐시 키는 **모델 · 전 역할 메시지 · 이미지 지문(sha256) · `think` · `options`
· `format` · `tools`** 를 포함한다. 하나라도 다르면 별개 응답이다(v2.124.59 이전에는 모델 +
사용자 텍스트만 보아 **같은 프롬프트에 다른 이미지를 보내면 직전 응답이 반환**됐다).
매번 새로 생성하려면 `no_cache: true`. ⚠️`stream:false`는 캐시를 **쓰기만** 하고 읽지 않는다.

**요청 크기**: 앱 상한 20MB, **외부 경유 실효 상한 10MB**(게이트웨이 nginx). base64는 원본의
4/3로 커지므로 **이미지 원본 총 ~7.4MB**가 한계다. 초과 시 413 — 앱은 JSON, **게이트웨이는
HTML**을 반환하므로 **상태 코드로 판별**할 것.

### 3-0-b. 사전 적재(상주) 모델 — `/api/models/residency` (v2.124.61~62)

기본 모델을 **영구 상주**시킬지, **요청 시 탑재**할지 무재배포로 전환한다. 파인튜닝처럼
VRAM이 필요한 작업과 서빙을 맞바꾸는 스위치다.

```bash
# 조회
curl $BASE/api/models/residency
# → {"resident":false,"model":"qwen2.5:14b","explicit_model":null,
#    "chat_default_model":"qwen2.5:14b","loaded_models":[...],"available_models":[...],"note":"..."}

# 요청 시 탑재로 전환(전환 즉시 반납) — 파인튜닝 전
curl -X POST $BASE/api/models/residency -H "Content-Type: application/json" \
  -d '{"resident": false}'

# 사전 적재 + 대상 모델을 채팅 기본값과 다르게(품질 14b · 상주 7b)
curl -X POST $BASE/api/models/residency -H "Content-Type: application/json" \
  -d '{"resident": true, "model": "qwen2.5:7b"}'

# 정책은 유지하고 지금 VRAM만 비우기(실트래픽이 다시 올리기 전 확보)
curl -X POST $BASE/api/models/unload -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5:14b"}'
```

- `model` 빈 문자열 = 명시 해제(채팅 기본 모델을 따름). **없는 모델은 400 + `available` 목록**.
- 모델을 바꾸면 **이전 상주 모델을 먼저 반납**한다(안 하면 VRAM이 넘어가지 않음).
- 성능: 콜드(유휴 후 첫 요청) **TTFT 2.5~3.9초** ↔ 웜 **0.13초**. 즉 첫 요청에만 +2.4~3.8초.
- ⚠️"요청 시 탑재"는 *유휴 시 반납*을 보장할 뿐이다 — 실트래픽이 있으면 계속 적재돼 보인다.
- Admin 콘솔 **"사전 적재 모델" 카드**에서 동일 조작 가능(드롭다운·전환·지금 반납).
- 설계 근거·VRAM 예산표: [DESIGN_MODEL_SERVING.md](DESIGN_MODEL_SERVING.md)

---

## 3-1. Heavy Tier — 하이브리드 대형 모델 (v2.118.0)

16GB VRAM에 안 들어가는 **대형 모델을 GPU VRAM + 시스템 RAM 하이브리드(부분 오프로드)로 on-demand 구동**.
기본 채팅 API(`/api/chat`, 상주 qwen2.5:14b fast-lane)와 **완전히 분리된 별도 경로**.
Blueprint: `routes/heavy.py` | 설계·PoC: [hybrid-large-model-inference.plan.md](01-plan/features/hybrid-large-model-inference.plan.md)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/heavy` | 대형 모델 채팅 (스트림/비스트림·`model` 오버라이드). 응답 후 상주 자동 복원 |
| GET | `/api/heavy/status` | 활성·heavy 모델·실행중·로드 모델 VRAM/CPU 오프로드% |
| POST | `/api/heavy/settings` | 토글/모델/keep_alive/num_gpu (admin) |

**동작 원리**: MAX_LOADED=1이라 heavy 요청 시 상주 모델을 일시 축출 → 대형 모델을 하이브리드로 로드·응답 →
**응답 후 상주 fast-lane 자동 복원**. 16GB 초과 모델은 Ollama 자동 레이어 오프로드(GPU+CPU RAM), `heavy_num_gpu`로
수동 분할 가능. **single-flight**(동시 heavy 2개=OOM 방지 → 409). 품질 우선·속도 비이슈 tier(하이브리드는 느림).

**Config** (전부 opt-in): `heavy_enabled`(기본 **false**=`/api/chat/heavy` 400, 기존 동작 불변) ·
`heavy_model`(기본 `qwen3.6:35b-a3b` — grounded A/B N=40서 상주와 at-parity, thinking 모델이라 `think=false` 기본) · `heavy_keep_alive`(기본 `"0"`=응답 후 언로드) · `heavy_num_gpu`(기본 null=자동).

```bash
# 1) 활성화 (localhost/admin)
curl -X POST $BASE/api/heavy/settings \
  -H "Content-Type: application/json" \
  -d '{"heavy_enabled":true, "heavy_model":"qwen3.6:35b-a3b"}'

# 2) 대형 모델로 질의 (상주 축출→하이브리드 구동→상주 복원). 스트리밍 NDJSON
curl -N -X POST $BASE/api/chat/heavy \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"복잡한 추론 질문", "stream":true}'

# 다른 대형 모델 즉석 지정 (medgemma:27b 등)
curl -X POST $BASE/api/chat/heavy \
  -d '{"messages":[{"role":"user","content":"..."}], "model":"medgemma:27b", "stream":false}'

# 3) 상태 (로드 모델의 CPU 오프로드% 포함)
curl $BASE/api/heavy/status
```

> ⚠️ PoC 실측(설계 문서 §5.5): 현 30GB RAM 내 후보(30B급)는 상주 14B 대비 품질 이득이 없고 대형 dense는 느림
> (50~88s/문항). 본 API는 **향후 더 나은 대형 모델·RAM 증설 대비 구동 인프라**로 제공된다.

---

## 4. Queue 배치

대량 LLM 작업 큐.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/queue/submit` | 단건 작업 제출 |
| POST | `/api/queue/batch` | 배치 제출 |
| POST | `/api/queue/batch/upload` | JSONL 파일 업로드 |
| GET | `/api/queue/job/<id>` | 작업 상태 |
| GET | `/api/queue/status` | 큐 상태 |
| GET | `/api/queue/stats` | 큐 통계 + ETA |
| GET | `/api/queue/jobs` | 작업 검색/필터 |
| GET | `/api/queue/batches` | 배치 목록 |
| POST | `/api/queue/pause` | 큐 일시정지 |
| POST | `/api/queue/resume` | 큐 재개 |
| POST | `/api/queue/retry/<id>` | 실패 작업 재시도 |
| POST | `/api/queue/cancel/<id>` | 작업 취소 |
| POST | `/api/queue/purge` | 완료/실패 정리 |

---

## 5. Image 생성

ComfyUI 기반 이미지 생성 (on-demand GPU).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/image/generate` | 이미지 생성 |
| GET | `/api/image/status/<id>` | 생성 상태 |
| GET | `/api/image/view/<file>` | 이미지 조회 |
| GET | `/api/image/models` | 모델/스타일/LoRA 목록 |
| GET | `/api/image/storage` | 스토리지 통계 |
| POST | `/api/image/cleanup` | 이미지 정리 |
| GET | `/api/image/gallery` | 갤러리 |
| DELETE | `/api/image/delete/<file>` | 이미지 삭제 |
| POST | `/api/image/settings` | 스토리지 설정 |
| CRUD | `/api/image/character/*` | 캐릭터 관리 (5 API) |

모델 9종, 스타일 15종, LoRA 4종. 상세: [IMAGE_API_GUIDE.md](IMAGE_API_GUIDE.md)

---

## 6. HR Law

한국 근로기준법 AI 어시스턴트. 멀티테넌트.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/hr-law/ask` | 법률 질의응답 |
| POST | `/api/hr-law/ask/stream` | 스트리밍 |
| POST | `/api/hr-law/search` | 법률 검색 |
| GET | `/api/hr-law/sources` | 법률 소스 목록 |
| GET | `/api/hr-law/stats` | 통계 |
| GET | `/api/hr-law/history` | 질의 이력 |
| CRUD | `/api/hr-law/tenant/*` | 테넌트 관리 (6 API) |
| CRUD | `/api/hr-law/rules/*` | 취업규칙 관리 (5 API) |
| POST | `/api/hr-law/calculate/*` | 연차/초과근무/퇴직금 계산 (3 API) |

---

## 7. Game NPC

게임 NPC 대화/성장/히로인 시스템. 멀티테넌트.

| Category | Count | Description |
|----------|-------|-------------|
| Tenant | 2 | 게임사 등록/목록 |
| NPC Template | 5 | NPC 원형 CRUD + 자동 생성 |
| NPC Instance | 2 | 플레이어별 인스턴스 |
| Dialogue | 4 | 1:1, 그룹, 플레이어 대화 |
| Growth | 2 | 레벨업, 스킬 |
| Scenario | 2 | 시나리오 생성/목록 |
| Illustrate | 2 | 캐릭터/장면 일러스트 |
| World | 2 | 세계관 설정/검색 |
| Shared NPC | 5 | 공유 NPC, 추억/평판/영향 |
| Heroine | 10 | 연애 시뮬레이션 (호감도, 데이트, 선물, 고백) |

---

## 8. Medical AI

병원 진료 지원. 멀티테넌트 (`X-Medical-Key`).

### 진료 지원 (한국어)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/summarize` | 진료 기록 → SOAP 요약 |
| POST | `/api/medical/summarize/stream` | SOAP 스트리밍 |
| POST | `/api/medical/analyze` | 감별진단 (Top 3 + ICD-10) |
| POST | `/api/medical/reference` | 의학 참고 (RAG 벡터 검색) |
| POST | `/api/medical/explain` | 환자 설명문 |
| POST | `/api/medical/draft` | 의무기록 초안 |
| POST | `/api/medical/drug-interactions` | 약물 상호작용 검사 (v2.39.0) |
| GET | `/api/medical/history` | 상담 이력 |

### 의료진 전용 (영어 모델 + 한국어 번역)

| Method | Endpoint | Model |
|--------|----------|-------|
| POST | `/api/medical/clinical/analyze` | medllama2 |
| POST | `/api/medical/clinical/drug` | meditron |
| POST | `/api/medical/clinical/translate` | qwen2.5:14b |

### 테넌트/지식/통계

| Method | Endpoint | Description |
|--------|----------|-------------|
| CRUD | `/api/medical/tenant/*` | 병원 관리 (6 API) |
| CRUD | `/api/medical/knowledge/*` | 의학 지식 (4 API) |
| GET | `/api/medical/usage` | 사용량 |
| GET | `/api/medical/audit` | 감사 로그 |

의학 지식: 156건 (ICD-10 50, 약물 50, 가이드라인 20, 질환 16, 검사 20)  
상세: [MEDICAL_API_GUIDE.md](MEDICAL_API_GUIDE.md), [MEDICAL_AI_INTEGRATION.md](MEDICAL_AI_INTEGRATION.md)

### Medical Advanced AI (9 API) — Phase 4 v2.41.0+

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/reasoning/chain` | 5단계 추론 체인 (증상→감별→순위→검사→평가) |
| POST | `/api/medical/reasoning/chain/stream` | 추론 체인 SSE 스트리밍 |
| POST | `/api/medical/auto-record` | 진료 대화 → NER → SOAP + ICD-10 + 처방 자동 |
| POST | `/api/medical/auto-record/stream` | 자동 의무기록 SSE 스트리밍 |
| POST | `/api/vision/medical/structured-report` | 구조화 영상 판독 (BI-RADS/Lung-RADS) |
| POST | `/api/medical/predict/readmission` | 30일 재입원 위험도 (HOSPITAL Score) |
| POST | `/api/medical/predict/complication` | 합병증 위험도 예측 |
| POST | `/api/medical/predict/adverse-drug` | 약물 부작용 예측 + DB 검증 |
| POST | `/api/medical/reasoning/ensemble` | **Multi-Model 앙상블** — multi(단계별 전문모델) / consensus(3모델 투표) |

### FHIR R4 Interoperability (6 API) — v2.43.0

HIS/EMR 시스템이 FHIR R4 표준으로 AI 결과를 소비할 수 있는 래퍼.

| Method | Endpoint | FHIR Resource |
|--------|----------|---------------|
| POST | `/api/fhir/DiagnosticReport` | 추론 체인 → DiagnosticReport + Observation Bundle |
| POST | `/api/fhir/DocumentReference` | 자동 의무기록 → DocumentReference + MedicationRequest |
| POST | `/api/fhir/RiskAssessment` | 예측 → RiskAssessment (readmission/complication/ADR) |
| POST | `/api/fhir/Observation/drug-check` | 약물 상호작용 → Observation Bundle |
| POST | `/api/fhir/Condition/search` | KCD 코드 검색 → Condition searchset |
| GET | `/api/fhir/metadata` | CapabilityStatement (서버 지원 리소스) |

### GPU Scheduler / Priority (8 API) — v2.42.0

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/gpu/scheduler` | GPU 스케줄러 상태 (프로파일, VRAM, 모델) |
| POST | `/api/gpu/scheduler/apply` | 스케줄 프로파일 즉시 적용 |
| POST | `/api/gpu/model/load` | 특정 모델 VRAM 적재 |
| POST | `/api/gpu/model/unload` | 특정 모델 VRAM 해제 |
| GET | `/api/priority/info` | 우선순위 시스템 정보 |
| POST | `/api/priority/classify` | 콘텐츠 긴급도 분류 |
| POST | `/api/ws/chat` | SSE 토큰 스트리밍 채팅 |
| POST | `/api/ws/generate` | SSE 토큰 스트리밍 생성 |
| GET | `/api/ws/status` | 실시간 상태 스트리밍 |

---

## 9. Medical CLI

역할별 AI 업무 자동화. 자연어 → 즉시 처리.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/cli/execute` | CLI 명령 실행 |
| POST | `/api/medical/cli/suggest` | 입력 자동완성 |
| GET | `/api/medical/cli/intents/<role>` | 역할별 인텐트 |
| GET | `/api/medical/cli/history` | CLI 이력 |
| POST | `/api/medical/code/kcd` | KCD 질병코드 검색 |
| POST | `/api/medical/code/edi` | EDI 수가코드 검색 |
| POST | `/api/medical/code/mapping` | 질병→검사/처방 매핑 |

### 역할 (5개)

| Role | 전용 핸들러 |
|------|-----------|
| `reception` | 접수, 증상→진료과 |
| `billing` | **진료비 자동 계산** (KCD→EDI→본인부담) |
| `nurse` | 바이탈 판정, **약물 상호작용 DB** |
| `doctor` | **가이드라인 처방 추천**, 코드 검색 |
| `claims` | **청구 검증** (묶음/제한/금기 규칙 DB) |

```bash
# 접수
{"role": "reception", "command": "김영희 850315 내과 초진"}

# 진료비
{"role": "billing", "command": "위장염 진료비 계산"}

# 약물 상호작용
{"role": "nurse", "command": "와파린 아스피린 상호작용"}

# 처방 추천
{"role": "doctor", "command": "고혈압 처방 추천"}

# 청구 검증
{"role": "claims", "command": "위내시경 조직검사 청구 검증"}
```

코드 DB: KCD 469, EDI 256, 약품 159, 상호작용 61, 매핑 100, 청구규칙 39, 삭감사유 34 (총 1,118건)

---

## 10. Symptom Triage

증상 → 진료과 추천. **인증 불필요** (공개 API).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/triage` | 증상→진료과 Top 3 + 응급 감지 |
| POST | `/api/medical/triage/follow-up` | 대화형 추가 질문 |
| GET | `/api/medical/triage/departments` | 18개 진료과 목록 |
| GET | `/api/medical/triage/common-symptoms` | 14개 자주 묻는 증상 |

```bash
# 기본
{"symptom": "머리가 아프고 열이 나요", "age": 45}
# → 1순위: 내과(85%), 2순위: 신경과(70%)

# 응급
{"symptom": "가슴 아프고 숨쉬기 힘들어요", "age": 55}
# → ⚠️ 119 즉시 연락 (심근경색/폐색전 가능)
```

---

## 11. Coding AI

소프트웨어 개발 전용. **인증 불필요**.

### 코드 생성/분석 (10 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/code/generate` | 코드 생성 |
| POST | `/api/code/generate/stream` | 코드 생성 (스트리밍) |
| POST | `/api/code/review` | 코드 리뷰 (버그/보안/성능) |
| POST | `/api/code/refactor` | 리팩토링 |
| POST | `/api/code/debug` | 디버깅 (에러 분석) |
| POST | `/api/code/test` | 테스트 생성 (pytest/jest/junit) |
| POST | `/api/code/explain` | 코드 설명 (한국어) |
| POST | `/api/code/convert` | 언어 변환 |
| POST | `/api/code/architect` | 아키텍처 설계 (한국어) |
| POST | `/api/code/chat` | 코딩 채팅 (멀티턴) |
| GET | `/api/code/models` | 코딩 모델 목록 |

### Code RAG (6 API)

코드베이스 색인 + 기존 코드 참조 생성.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/code/rag/index` | 코드/문서 색인 |
| POST | `/api/code/rag/index/batch` | 배치 색인 |
| POST | `/api/code/rag/search` | 벡터 검색 (언어/프로젝트 필터) |
| POST | `/api/code/rag/ask` | **RAG 증강 코드 생성** |
| DELETE | `/api/code/rag/delete/<id>` | 색인 삭제 |
| GET | `/api/code/rag/stats` | 통계 |

```bash
# 코드 생성
{"prompt": "FastAPI JWT 인증", "language": "python"}

# 코드 리뷰
{"code": "def f(x): return eval(x)", "focus": "security"}
# → 🔴 critical: SQL Injection / Code Injection

# RAG 질의 (기존 코드 참조)
{"question": "기존 UserService 참고해서 PostService 만들어줘", "project": "myapp"}
```

모델: qwen2.5-coder:14b (기본, 88% HumanEval), deepseek-coder-v2:16b (빠른, 122tok/s)  
상세: [CODING_API_GUIDE.md](CODING_API_GUIDE.md)

---

## 12. Orchestration Platform (16 API) — v2.52.0

커스텀 다중 모델 파이프라인 — 생성/실행/테스트/비교/버전 관리.

### CRUD

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orchestration/pipelines` | 파이프라인 목록 (builtin 4 + custom) |
| GET | `/api/orchestration/pipeline/<name>` | 파이프라인 상세 |
| POST | `/api/orchestration/pipeline` | 파이프라인 생성 (최대 5단계) |
| PUT | `/api/orchestration/pipeline/<name>` | 수정 (이전 버전 자동 백업 @v1) |
| DELETE | `/api/orchestration/pipeline/<name>` | 삭제 |

### 실행 + 테스트

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/orchestration/run` | **실행** — `mode:sync/stream/async`, debug, 에러 정책, DB 이력 |
| GET | `/api/orchestration/run/<run_id>/status` | **비동기 폴링** — async 모드 진행 상태 조회 |
| POST | `/api/orchestration/test` | **테스트** — `validate`(모델+VRAM) / `dry_run`(1단계) / `full_test`(전체+품질) |
| GET | `/api/orchestration/runs` | 실행 이력 목록 |
| GET | `/api/orchestration/run/<run_id>` | 실행 결과 상세 |
| POST | `/api/orchestration/compare` | **A/B 비교** — 2~3개 파이프라인 동시 실행 비교 |

### 버전 + 복제

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orchestration/pipeline/<name>/versions` | 버전 목록 |
| POST | `/api/orchestration/pipeline/<name>/rollback` | 이전 버전 복원 (`?version=1`) |
| POST | `/api/orchestration/pipeline/<name>/clone` | 파이프라인 복제 (builtin 포함) |

### Builtin 파이프라인

| Name | Stages | 모델 | 용도 |
|------|--------|------|------|
| `medical` | 3 | werub → Med42 → werub | 감별진단 (한/영 합산) |
| `coding` | 2 | deepseek-coder → qwen2.5:14b | 코드 생성+리뷰 |
| `writing` | 3 | qwen2.5:14b → exaone-deep → dna | 고품질 글쓰기 |
| `legal` | 2 | qwen2.5:14b → exaone-deep | 법률 답변+논증 |

### 에러 정책 (단계별)

```json
{"model": "med42:8b", "role": "감별진단",
 "on_fail": "continue", "retry": 1, "fallback_model": "meditron:latest"}
```
- `on_fail`: `abort`(중단) / `continue`(계속) / `skip`(건너뜀)
- `retry`: 실패 시 재시도 횟수
- `fallback_model`: 재시도 실패 시 대체 모델 자동 전환

### 사용 예시

```bash
# 1. 커스텀 생성
curl -X POST /api/orchestration/pipeline -d '{"name":"my-pipe","stages":[
  {"model":"qwen2.5:14b","role":"초안","system":"..."},
  {"model":"exaone-deep:7.8b","role":"개선","on_fail":"continue","fallback_model":"qwen2.5:7b"}
]}'

# 2. 검증
curl -X POST /api/orchestration/test -d '{"pipeline":"my-pipe","mode":"validate"}'

# 3. 실행 (debug)
curl -X POST /api/orchestration/run -d '{"pipeline":"my-pipe","input":"...","debug":true}'

# 4. 비교
curl -X POST /api/orchestration/compare -d '{"pipelines":["my-pipe","writing"],"input":"..."}'

# 5. 버전 관리
PUT /api/orchestration/pipeline/my-pipe → 자동 백업 @v1
POST /api/orchestration/pipeline/my-pipe/rollback?version=1
```

### 실행 모드 (v2.52.0)

| 모드 | 설명 | 응답 |
|------|------|------|
| `sync` (기본) | 완료까지 대기 | JSON 결과 |
| `stream` | SSE 단계별 스트리밍 | text/event-stream (stage_start → stage_complete → result) |
| `async` | 즉시 run_id 반환 | 폴링: GET `/api/orchestration/run/<id>/status` |

```bash
# SSE 스트리밍
curl -X POST /api/orchestration/run -d '{"pipeline":"medical","input":"흉통","mode":"stream"}'
# → data: {"type":"stage_start","stage":1,...}
# → data: {"type":"stage_complete","stage":1,...}
# → data: {"type":"result","content":"최종 결과"}

# 비동기
curl -X POST /api/orchestration/run -d '{"pipeline":"writing","input":"AI 트렌드","mode":"async"}'
# → {"run_id":"uuid","status":"running"}
curl /api/orchestration/run/<run_id>/status
# → {"status":"completed","result":"...","stages":[...]}
```

**안정성:** Semaphore(1) 동시 실행 제한 + 실행 이력 DB 자동 기록 + 30일 자동 정리.

---

## 13. Enhanced Chat & Writing (5 API) — v2.55.0

도메인별 다중 모델 오케스트레이션 채팅 + 3모델 고품질 글쓰기 + 번역.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/enhanced` | **강화 채팅** — 도메인 자동 감지 → 2~3모델 순차 파이프라인 |
| POST | `/api/writing/craft` | **고품질 글쓰기** — qwen(초안) → exaone-deep(구조) → dna(다듬기) |
| POST | `/api/writing/craft/stream` | **3모델 SSE 스트리밍** — stage_start/token/stage_done/done 이벤트 |
| POST | `/api/writing/translate` | **텍스트 번역** — 7개 언어 (en/ja/zh/es/fr/de/ko) |
| GET | `/api/writing/languages` | 지원 번역 언어 목록 |

### /api/chat/enhanced 도메인별 파이프라인

| 도메인 | Stage 1 | Stage 2 | Stage 3 |
|--------|---------|---------|---------|
| medical | werub (정규화) | Med42 (감별진단) | werub (한국어 종합) |
| coding | deepseek-coder (코드) | qwen2.5:14b (리뷰) | — |
| writing | qwen2.5:14b (초안) | exaone-deep (구조) | dna (다듬기) |
| legal | qwen2.5:14b (법률) | exaone-deep (논증) | — |

```bash
curl -X POST /api/chat/enhanced -d '{"messages":[{"role":"user","content":"흉통 감별진단"}]}'
# → domain_detected: "medical", enhanced: true, stages: [{model,role,tokens,duration_ms}...]
```

### /api/writing/craft

```bash
curl -X POST /api/writing/craft -d '{"prompt":"AI 트렌드 블로그","tone":"professional","length":"long"}'
# → result: "최종 글", stages: [S1 초안, S2 구조, S3 다듬기], total_duration_ms: ~25000
```

### /api/writing/craft/stream (v2.55.0)

3모델 SSE 스트리밍 파이프라인. 단계별 진행 상태와 토큰을 실시간으로 전달.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| prompt | string | Y | 글쓰기 주제/요청 |
| tone | string | N | 톤앤매너 |
| structure | string | N | 구조 지정 |
| length | string | N | 분량 (short/medium/long) |
| context | string | N | 추가 컨텍스트 |

```bash
curl -X POST /api/writing/craft/stream -d '{"prompt":"AI 미래 에세이","tone":"academic"}'
# → SSE events:
# data: {"type":"stage_start","stage":1,"model":"qwen2.5:14b","role":"초안"}
# data: {"type":"token","stage":1,"content":"인공지능은"}
# data: {"type":"stage_done","stage":1,"tokens":320,"duration_ms":8500}
# data: {"type":"stage_start","stage":2,...}
# ...
# data: {"type":"done","result":"최종 글","total_duration_ms":25000}
```

### /api/writing/translate (v2.55.0)

텍스트 번역. 7개 언어 지원 (en/ja/zh/es/fr/de/ko).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| text | string | Y | 번역할 텍스트 |
| target | string | Y | 대상 언어 코드 (en/ja/zh/es/fr/de/ko) |
| tone | string | N | 번역 톤 (formal/casual/literary) |

```bash
curl -X POST /api/writing/translate -d '{"text":"안녕하세요","target":"en","tone":"formal"}'
# → {"translation":"Hello","source_lang":"ko","target_lang":"en","target_name":"English","tone":"formal","tokens":42,"duration_ms":1200}
```

### /api/writing/languages (v2.55.0)

지원 번역 언어 목록 조회.

```bash
curl /api/writing/languages
# → {"languages":[{"code":"en","name":"English"},{"code":"ja","name":"Japanese"},{"code":"zh","name":"Chinese"},{"code":"es","name":"Spanish"},{"code":"fr","name":"French"},{"code":"de","name":"German"},{"code":"ko","name":"Korean"}]}
```

---

## 14. System / Activity / RAG

### Admin Pages (v2.55.0)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/model-manager` | Model Manager 전용 페이지 (모델 목록/Pull/삭제/GPU 상태) |
| GET | `/orchestration-editor` | Orchestration Editor 전용 페이지 (워크플로우 CRUD/실행/이력) |

### System (14 API)

| Endpoint | Description |
|----------|-------------|
| `/api/health` | 헬스체크 (8항목) |
| `/api/logs` | 엔진 로그 |
| `/api/server/stats` | 서버 통계 (CPU/RAM/GPU) |
| `/api/prod/health`, `/api/prod/stats` | 프로덕션 모니터링 |
| `/api/stats/history` | 통계 히스토리 |
| `/api/alerts/history` | 알림 이력 |
| `/api/benchmark/<model>` | 모델 벤치마크 |
| `/api/models/init-config`, `/api/models/init` | 모델 초기화 |
| `/api/stt/chunk` | 실시간 청크 STT (raw PCM·헤더 `X-Mode`/`X-Session-Id`/`X-Targets`/`X-Hotwords`) |
| `/api/stt/chunk/vocab` | **세션 단위 어휘 등록**(hotwords·상한 200개/600자·TTL 1h·워커 재생성 생존) |
| `/api/stt/chunk/status`, `/api/stt/chunk/warm` | 상태(loaded·idle_seconds·vocab_sessions) / 사전 워밍 |

**실시간 STT 주의**
- `partial`은 증분이 아니라 **누적분 전체 재인식** → 화면은 직전 결과를 **교체**할 것.
- 세션 버퍼는 **최근 30초 슬라이딩**이며, 초과분은 응답 `dropped_s`(초)로 통지된다.
  **`dropped_s > 0` = 앞부분 유실** → 더 짧은 주기로 `final`을 끊어야 한다.
- 서버 VAD 없음(무음 게이팅은 호출자 책임)·`confidence < 0.5`면 번역 보류(`translation_held`).
- 한글 용어는 HTTP 헤더(latin-1)로 못 보낸다 → **퍼센트 인코딩** 또는 `/vocab` JSON 경로 사용.
| `/api/system/info` | 시스템 정보 |

### Activity (6 API)

| Endpoint | Description |
|----------|-------------|
| `/api/activity` | API 활동 로그 |
| `/api/activity/servers` | 서버별 통계 |
| `/api/cache/stats` | 캐시 통계 |
| `/api/cache/clear` | 캐시 초기화 |
| `/api/imac/status` | iMac 상태 |
| `/metrics` | Prometheus 메트릭 |

### RAG (5 API)

| Endpoint | Description |
|----------|-------------|
| `/api/rag/index` | 문서 색인 — 단건/배치(`documents` ≤500·upsert 멱등)·`collection` 지정(v2.124.37)·`chunk_strategy` 스마트 청킹(v2.39.0)·FTS 동시 기록(v2.124.39) |
| `/api/rag/search` | 벡터 검색 (`collection`·`filter` 메타 필터, v2.39.0: `merge_chunks` 인접 청크 병합) |
| `/api/rag/ask` | RAG 질의응답 (`collection`·`filter` 지원, v2.124.37) |
| `/api/rag/document/<id>` | 문서 삭제 — `?collection=` 지정·멱등·청크/FTS 동반 정리(v2.124.38~39) |
| `/api/rag/stats` | 색인 현황 (`?collection=`, 컬렉션 단위 count) |

### Training (32 API)

QLoRA 학습 시작/중지/모니터링, 데이터 업로드, 모더레이션, 사용자/역할 관리.

### Vision (5 API)

#### 일반 비전
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/vision/describe` | 이미지 설명 생성 (기본 **`qwen3-vl:8b`**·폴백 minicpm-v — v2.125.10. 사고형이라 서버가 `num_predict ≥ 2048`로 보정 · **`model` 폼 파라미터로 선택 가능**) |
| GET | `/api/vision/models` | 비전 모델 목록 |

**이미지 입력 경로 3종** (2026-07-29 정합)

| 경로 | 형식 |
|------|------|
| `POST /api/chat` · `POST /api/generate` | `images: ["<base64>"]` — `data:image/png;base64,` 접두는 서버가 제거 |
| `POST /v1/chat/completions` | OpenAI 멀티모달 `content:[{type:"text"},{type:"image_url",image_url:{url:"data:..."}}]` |
| `POST /api/vision/describe` | multipart `image` + `model`/`prompt`/`language` |

⚠️ 원격 `http(s)` URL 이미지는 **지원하지 않는다**(요청 경로 SSRF 표면 차단) — 400으로 사유를
반환하므로 base64 data URI로 보낼 것.

**포맷·용량 (2026-07-30 실측)** — 세 경로 공통(`core/images.py` 단일 소스)

| 항목 | 값 |
|------|-----|
| 지원 포맷 | PNG · JPEG · GIF · **WEBP**(서버가 PNG로 변환·v2.124.60) |
| `data:` 접두 | 서버가 제거 |
| 요청 본문 상한 | 앱 20MB · **외부 경유 10MB**(게이트웨이) → **이미지 원본 총 ~7.4MB**(base64 4/3) |
| 초과 시 | 413 — 앱은 JSON, 게이트웨이는 HTML → **상태 코드로 판별** |
| 최대 픽셀 / 자동 리사이즈 | 제한 없음(3000×2000 정상) / **없음** |
| 1회 장수 | 서버 제한 없음. ⚠️다중 이미지는 **정확도가 떨어진다**(3장 실측 1/3 정답·n=1) → **장당 1요청 권장** |
| 지연 | 비전 모델은 상주 LLM과 **스왑 경쟁** — 유휴 8~27초, 피크 47~103초 → **타임아웃 180초+** |

#### 의료 영상 (MedGemma 4B)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/vision/medical/analyze` | 의료 영상 분석 (소견/판독소견/권고사항) |
| GET | `/api/vision/medical/models` | 의료 영상 모델 목록 |
| POST | `/api/vision/medical/report` | 비교 판독 보고서 (현재 vs 이전 영상) |

**의료 영상 분석 파라미터:**
| Parameter | Type | Description |
|-----------|------|-------------|
| image | file | 의료 영상 파일 (jpg/png) |
| modality | string | xray, ct, mri, ultrasound, pathology, fundus, dermoscopy |
| body_part | string | chest, abdomen, brain, spine, extremity 등 |
| clinical_info | string | 임상 정보 (증상, 병력) |
| language | string | ko (기본) / en |

**응답 구조:**
```json
{
  "findings": "소견...",
  "impression": "판독 소견...",
  "recommendations": "권고 사항...",
  "model": "medgemma:4b",
  "modality": "xray",
  "disclaimer": "AI 참고용 분석입니다."
}
```

---

## 15. 모델

### LLM (8개)

| Model | VRAM | Use Case |
|-------|------|----------|
| **qwen2.5:14b** | 9GB | 메인 — 한국어 99%, 상주 |
| qwen2.5-coder:14b | 9GB | 코딩 — HumanEval 88% |
| deepseek-coder-v2:16b | 8.9GB | 빠른 코딩 — 122tok/s |
| exaone3.5:7.8b | 4.8GB | fallback — 한/영 이중 |
| medllama2 | 3.8GB | 의료 임상 분석 (영어) |
| meditron | 3.8GB | 약물/가이드라인 (영어) |
| deepseek-r1:14b | 9GB | 추론/코딩 |
| phi4:14b | 9.1GB | 수학/논리 |

### Embedding (3개)

| Model | Dim | Use Case |
|-------|-----|----------|
| bge-m3 | 1024 | 의학 RAG (다국어) |
| nomic-embed-text | 768 | 코드 RAG, 범용 |
| mxbai-embed-large | 1024 | 영문 특화 |

### Image (9종, ComfyUI on-demand)

FLUX.1 Schnell/Dev, SDXL, Juggernaut XL, DreamShaper XL 등

---

## 16. 에러 코드

| Code | Description |
|------|-------------|
| 200 | 성공 |
| 400 | 필수 파라미터 누락 |
| 401 | 인증 실패 (API Key / Medical Key) |
| 404 | 리소스 없음 |
| 413 | 입력 크기 초과 |
| 429 | 요청 한도 초과 |
| 503 | LLM/ComfyUI 서버 오류 |

---

## 15. Story Engine (31 API) — v2.52.0

스토리/소설 생성 엔진. 5계층 컨텍스트 + 이어쓰기 + 분기 + 세계관/인물/관계/복선 관리.

### 스토리 생성

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/story/generate` | 스토리 생성 (9 style × 9 tone = 81 조합) |
| POST | `/api/story/generate/stream` | SSE 스트리밍 생성 |
| POST | `/api/story/continue` | 이어쓰기 (자동 체인 생성) |
| POST | `/api/story/branch` | 선택지 분기 (dramatic/subtle/humorous/moral_dilemma) |
| POST | `/api/story/rewrite` | 문체/톤 변환 |
| POST | `/api/story/dialogue/scene` | 대화 장면 (지문 + 대사) |
| GET | `/api/story/styles` | 스타일/톤/시점/길이 목록 |
| GET | `/api/story/history` | 생성 이력 |
| GET | `/api/story/<id>` | 스토리 상세 |

### 세계관/인물/관계

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/story/world/create` | 세계관 등록 |
| GET/PUT | `/api/story/world/<id>` | 세계관 조회/수정 |
| POST | `/api/story/world/expand` | 세계관 기반 스토리 확장 |
| POST | `/api/story/character/create` | 인물 등록 |
| GET | `/api/story/character/<id>` | 인물 조회 |
| PUT | `/api/story/character/<id>/state` | 인물 상태 업데이트 |
| GET | `/api/story/characters` | 인물 목록 |
| POST | `/api/story/relationship/set` | 관계 등록/수정 |
| GET | `/api/story/chain/<id>/relationship-map` | 관계도 그래프 |
| POST | `/api/story/chain/<id>/relationship-update` | 관계 자동 갱신 (LLM) |

### 복선/검증/시리즈

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/story/foreshadowing/add` | 복선 등록 |
| POST | `/api/story/foreshadowing/detect` | 복선 자동 감지 (LLM) |
| PUT | `/api/story/foreshadowing/<id>/resolve` | 복선 해소 |
| GET | `/api/story/chain/<id>/foreshadowing` | 복선 목록 |
| POST | `/api/story/validate` | 일관성 검증 (세계관/인물/관계 모순) |
| GET | `/api/story/chain/<id>/context` | 컨텍스트 미리보기 |
| POST | `/api/story/chain/<id>/summarize` | 시리즈 요약 갱신 |
| GET | `/api/story/chains` | 체인 목록 |
| GET | `/api/story/chain/<id>` | 체인 상세 |
| POST | `/api/story/novel/outline` | 소설 아웃라인 |
| POST | `/api/story/novel/chapter` | 소설 챕터 생성 |

```bash
# 스토리 생성
curl -X POST /api/story/generate -d '{
  "prompt": "전쟁이 끝난 후 고향으로 돌아온 병사",
  "style": "novel", "tone": "melancholy", "pov": "first_person", "length": "medium"
}'

# 이어쓰기
curl -X POST /api/story/continue -d '{"story_id": "uuid", "direction": "에린을 만나러 간다"}'

# 분기
curl -X POST /api/story/branch -d '{"story_id": "uuid", "branch_count": 3, "branch_style": "dramatic"}'
```

---

## 16. Novel Writer (78 API) — v2.54.0

장편 소설 집필 플랫폼. 기획→설계→집필→퇴고→분석→분해→재활용→시리즈.
7장르 (fantasy/sf/romance/mystery/horror/literary/web_novel), 8계층 컨텍스트, 3모델 오케스트레이션.

### 프로젝트 관리 (5 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/project/create` | 프로젝트 생성 (7장르) |
| GET | `/api/novel/projects` | 프로젝트 목록 |
| GET | `/api/novel/project/<id>` | 프로젝트 상세 (챕터 포함) |
| PUT | `/api/novel/project/<id>` | 프로젝트 수정 |
| DELETE | `/api/novel/project/<id>` | 프로젝트 삭제 |

### 기획/설계 (8 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/synopsis/generate` | 시놉시스 생성 (brief/standard/detailed, component_refs 지원) |
| GET | `/api/novel/synopsis/<id>` | 시놉시스 조회 |
| PUT | `/api/novel/synopsis/<id>` | 시놉시스 수정 |
| POST | `/api/novel/plot/generate` | 플롯 구조 설계 (3막/히어로저니/기승전결, component_refs 지원) |
| POST | `/api/novel/plot/validate` | 플롯 일관성 검증 |
| PUT | `/api/novel/plot/<id>` | 플롯 수정 |
| POST | `/api/novel/outline/generate` | 챕터 아웃라인 생성 |
| GET | `/api/novel/outline/<id>` | 아웃라인 조회 |
| PUT | `/api/novel/outline/chapter/<num>` | 개별 챕터 아웃라인 수정 |

### 챕터 집필 (5 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/chapter/write` | 챕터 집필 (quick/orchestrated, references+reference_strength 지원) |
| POST | `/api/novel/chapter/write/stream` | 챕터 SSE 스트리밍 생성 |
| POST | `/api/novel/chapter/rewrite` | 챕터/장면 재작성 (direction, keep_elements) |
| GET | `/api/novel/chapter/<proj>/<num>` | 챕터 조회 |
| POST | `/api/novel/chapter/summary` | 챕터 요약 생성 (DB 캐시) |

### 장면(Scene) 전문 생성 (6 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/scene/write` | 개별 장면 생성 |
| POST | `/api/novel/scene/transition` | 장면 전환문 생성 |
| POST | `/api/novel/scene/dialogue` | 대화 장면 전문 생성 (대화:지문 6:4) |
| POST | `/api/novel/scene/description` | 묘사/배경 전문 생성 (5감) |
| POST | `/api/novel/scene/inner_voice` | 내면 독백 전문 생성 |
| POST | `/api/novel/scene/write-with-ref` | 레퍼런스 기반 장면 집필 (reference_strength 3단계) |

### 퇴고 (5 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/revise/consistency` | 일관성 검증 (인물/세계관/시간/관계) |
| POST | `/api/novel/revise/style` | 문체 통일 검사 |
| POST | `/api/novel/revise/foreshadowing` | 복선 회수 확인 |
| POST | `/api/novel/revise/polish` | 문장력 다듬기 |
| POST | `/api/novel/revise/full` | 전체 퇴고 (4단계 순차: 일관성→문체→복선→교정) |

### 집필 보조 (7 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/brainstorm` | 아이디어 브레인스토밍 (키워드→로그라인+시놉시스 후보) |
| POST | `/api/novel/chapter/expand` | 챕터 분량 확장 (description/dialogue/inner_voice/all) |
| POST | `/api/novel/chapter/condense` | 챕터 압축 (핵심 유지) |
| POST | `/api/novel/naming` | 이름 생성기 (character/place/magic/tech/org × 5스타일) |
| POST | `/api/novel/prompt/unblock` | 작가의 벽 돌파 (3가지 전개 방향 제안) |
| POST | `/api/novel/timeline` | 타임라인 자동 추출 (chronological/character/location) |
| POST | `/api/novel/compare/versions` | 초안 vs 퇴고본 비교 분석 |

### 분석/평가 (7 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/analyze/pacing` | 페이싱 분석 (긴장도 곡선) |
| POST | `/api/novel/analyze/character` | 캐릭터 분석 (등장 빈도, 감정 아크) |
| POST | `/api/novel/analyze/readability` | 가독성 분석 (정량+정성) |
| POST | `/api/novel/analyze/dialogue` | 대화 분석 (말투 일관성, 서브텍스트) |
| POST | `/api/novel/analyze/worldbuilding` | 세계관 일관성 분석 |
| POST | `/api/novel/analyze/emotion_map` | 감정 지도 (chapter/scene 단위) |
| POST | `/api/novel/score` | 종합 점수 (6영역 100점) |

### 출판/내보내기 (3 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/export` | 내보내기 (text/markdown) |
| POST | `/api/novel/cover/generate` | 표지 이미지 생성 (LLM 프롬프트→ComfyUI) |
| GET | `/api/novel/revisions/<id>` | 퇴고 이력 |

### 시리즈 관리 (6 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/series/create` | 시리즈 생성 |
| GET | `/api/novel/series` | 시리즈 목록 |
| GET | `/api/novel/series/<id>` | 시리즈 상세 (진행률) |
| PUT | `/api/novel/series/<id>/add` | 프로젝트 추가 |
| POST | `/api/novel/series/recap` | "지금까지의 이야기" 생성 (narrator/character/chronicle) |
| POST | `/api/novel/series/continuity` | 시리즈 연속성 검증 |

### 소설 분해기 (15 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/decompose/upload` | 소설 텍스트 등록 |
| GET | `/api/novel/decompose/sources` | 등록 소설 목록 |
| GET | `/api/novel/decompose/source/<id>` | 등록 소설 상세 |
| DELETE | `/api/novel/decompose/source/<id>` | 원본 삭제 (cascade) |
| POST | `/api/novel/decompose/run/<id>` | 전체 분해 실행 (8종 컴포넌트) |
| POST | `/api/novel/decompose/run/<id>/stream` | 분해 SSE 스트리밍 |
| GET | `/api/novel/decompose/run/<id>/status` | 분해 상태 조회 |
| GET | `/api/novel/components` | 컴포넌트 목록 (type/genre/tag 필터) |
| GET | `/api/novel/components/<id>` | 컴포넌트 상세 |
| PUT | `/api/novel/components/<id>` | 컴포넌트 수동 편집 |
| DELETE | `/api/novel/components/<id>` | 컴포넌트 삭제 |
| GET | `/api/novel/components/source/<id>` | 소설별 컴포넌트 |
| GET | `/api/novel/components/genre/<genre>` | 장르별 컴포넌트 |
| POST | `/api/novel/components/search` | 시맨틱 검색 |
| POST | `/api/novel/components/apply` | 프로젝트에 적용 |
| POST | `/api/novel/components/remix` | 컴포넌트 조합→새 설정 |
| POST | `/api/novel/components/adapt` | 장르 변환 |

### 통합 파이프라인 (9 API)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/novel/scaffold` | 컴포넌트→프로젝트 뼈대 자동 생성 (7단계) |
| GET | `/api/novel/scaffold/<id>/status` | 뼈대 생성 상태 |
| POST | `/api/novel/create-from-components` | 컴포넌트 기반 프로젝트 생성 |
| POST | `/api/novel/create-from-components/stream` | 위 SSE 버전 |
| POST | `/api/novel/recipe/create` | 레시피 생성 (컴포넌트 슬롯+설정) |
| GET | `/api/novel/recipes` | 레시피 목록 |
| DELETE | `/api/novel/recipe/<id>` | 레시피 삭제 |
| POST | `/api/novel/recipe/<id>/execute` | 레시피 실행→프로젝트 자동 생성 |

```bash
# E2E: 소설 분해 → 컴포넌트 → scaffold → 집필
curl -X POST /api/novel/decompose/upload -d '{"title":"원본소설","genre":"literary","text":"..."}'
curl -X POST /api/novel/decompose/run/{source_id} -d '{"depth":"full"}'
curl GET /api/novel/components/source/{source_id}
curl -X POST /api/novel/scaffold -d '{"title":"새소설","genre":"sf","components":{"character_refs":[{"component_id":"...","adapt":"SF변환"}]}}'
curl -X POST /api/novel/chapter/write -d '{"project_id":"...","chapter_num":1,"quality":"orchestrated","references":{"style":"comp-id"}}'

# 종합 분석
curl -X POST /api/novel/score -d '{"project_id":"uuid"}'
```

---

## 17. Medical Ensemble (1 API) — v2.52.0

다중 모델 교차 검증 추론 체인.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/reasoning/ensemble` | Multi-Model 앙상블 (multi/consensus) |

| 모드 | 모델 | 스왑 | 소요 | 정확도 |
|------|------|------|------|--------|
| `multi` | werub(KO)→Med42(EN)→werub(KO) | 2회 | 30~40초 | 높음 |
| `consensus` | werub+Med42+meditron 3모델 투표 | 4회 | 50~70초 | 최고 |

```bash
curl -X POST /api/medical/reasoning/ensemble \
  -H "X-Medical-Key: med-xxx" \
  -d '{"patient_text":"55세 남성 흉통 3일","chain_mode":"consensus"}'
# → consensus: {merged_diagnoses: [...], models_used: [...], unique_diagnoses: 8}
```

---

## 18. Medical Law (14 API) — v2.53.0

의료법 AI 어시스턴트. 법률 질의응답 + 자동 검사 + 추론체인 연동.

### 질의응답/검색

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical-law/ask` | 의료법 질의응답 (RAG 조문 검색 + LLM) |
| POST | `/api/medical-law/search` | 법률 조문 검색 (카테고리/법률명 필터) |
| GET | `/api/medical-law/sources` | 수집된 법률 목록 |
| GET | `/api/medical-law/stats` | 의료법 DB 통계 |
| GET | `/api/medical-law/history` | 상담 이력 |

### 자동 검사

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical-law/check/record` | 의무기록 기재사항 검사 (의료법 제22조) |
| POST | `/api/medical-law/check/report` | 감염병 신고 의무 확인 (20종 감염병 DB) |
| POST | `/api/medical-law/check/consent` | 동의서 요건 확인 (서면동의 필요 판단) |
| POST | `/api/medical-law/check/privacy` | 개인정보 처리 적법성 (민감정보/제3자 제공) |
| POST | `/api/medical-law/check/emergency` | 응급의료 의무 준수 확인 |
| POST | `/api/medical-law/auto-check` | **통합 법적 검사** (기록+감염병+동의서 일괄) |

### 가이드/계산

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical-law/guide/informed-consent` | 인폼드 컨센트 가이드 (LLM, 시술별 위험/대안) |
| POST | `/api/medical-law/guide/retention` | 기록 보관 기간 안내 (10종 유형별) |
| POST | `/api/medical-law/guide/advertisement` | 의료 광고 적법성 검토 (LLM + 금지키워드) |
| POST | `/api/medical-law/calculate/penalty` | 위반 벌칙/과태료 조회 |

```bash
# 추론체인 결과 통합 법적 검사
curl -X POST /api/medical-law/auto-check -d '{
  "record": "S: 두통 3일\nO: BP 140/90\nA: 긴장성 두통\nP: 진통제 처방",
  "diagnoses": [{"diagnosis_ko": "결핵", "icd10": "A15"}],
  "procedures": ["내시경 검사"]
}'
# → checks: [record_compliance, reportable_disease(결핵→24시간 신고), informed_consent(내시경)]
```

DB: 20개 법률 조문, 20종 감염병, 의료법/약사법/감염병예방법/응급의료법/환자안전법

> **의료법 RAG 자문 레이어(v2.88.0+)**: 위 정적 검사와 별개로 `/api/medical-law/ask`·`/ask/stream`·`/review`·
> `/corpus-status`가 법령·고시·발간물 RAG 코퍼스(`medical_law_knowledge` 9,593 + `medical_pub_knowledge` 15,285)를
> 근거 인용한다. 코퍼스 구성·권위 위계·호출 규칙은 **`docs/MEDICAL_LAW_RAG_GUIDE.md`(SoT)** 참조.

---

<a id="19a-erp-notice"></a>
## 19A. ERP/Notice 사전심사 + 청구코드 (15 API) — v2.87~v2.92

ERP 사전심사 통합. 고시 수명주기 관리 → 색인 → 시행 중 심사기준 검색(P0) → 삭감위험 평가(P0.5).
청구코드·약가코드·저가약 대체조제 구조적 조회 포함. 저장: `notice.db`, 검색 코퍼스 `notice_knowledge`.
인증: Bearer(localhost 우회). 상세 규칙 → `docs/MEDICAL_LAW_RAG_GUIDE.md`.

### 공시 수명주기 (5)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/notice` | 공시 등록 (멱등 — 동일 (erp_ref_id,revision) 재등록 시 해시 비교 200/409) |
| POST | `/api/notice/batch` | 일괄 등록 (최대 100건) |
| PUT | `/api/notice/<nid>` | 개정 (supersedes — 구버전 SUPERSEDED 처리) |
| POST | `/api/notice/<nid>/revoke` | 폐지 (REVOKED) |
| DELETE | `/api/notice/<nid>` | 삭제 (벡터·FTS 동반 정리) |

### 색인 (3)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/notice/index-status` | 색인 잡 현황 (컬렉션 카운트) |
| GET | `/api/notice/<nid>/index-status` | 개별 공시 색인 상태 폴링 |
| POST | `/api/notice/process-jobs` | pending 색인 잡 수동 처리 (immediate 백그라운드+nightly 03 UTC 외 수동 트리거) |

### 검색·평가 (2)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/notice/search` | 시행 중 심사기준 RAG 검색 (`active_only`·`notice_type`·`effective_on`[진료일 시행여부] 필터, `code` 자동해석) |
| POST | `/api/notice/diagnose` | 삭감위험 평가 (qwen2.5:14b 구조화, 위험밴드 HIGH≥0.70/MED/LOW, **근거인용 강제**=has_evidence 없으면 보류·환각방지) |

### 청구코드 → notice_type (3)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/notice/fee-codes/import` | 코드마스터 적재 (`rows[]` 또는 `csv`+`columns` 매핑, INSERT OR REPLACE 멱등) |
| GET | `/api/notice/fee-codes/stats` | 적재 현황 (분류별 — TARIFF 7,138 + DRUG 21,898 = 29,036) |
| GET | `/api/notice/fee-codes/<code>` | 청구/약가 코드 → notice_type 해석 (`TARIFF`→[REVIEW_CRITERIA,TARIFF], `DRUG`/`MATERIAL`→[DRUG_MATERIAL,REVIEW_CRITERIA]) |

### 저가약 대체조제 (2) — **v2.92.0 신규**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/notice/drug-substitution/<code>` | 제품코드 → 장려금 대상 여부 + **대체 후보**(동일 주성분코드·대표코드 상이·eligible·저가순) lookup |
| POST | `/api/notice/drug-substitution` | 동일 lookup (body `{"code"}` — 오케스트레이터 도구 POST 디스패치용, v2.93.0) |
| GET | `/api/notice/drug-substitution/stats` | 적재 현황 (총 10,979 / 장려금대상 10,412 / 급여정지 17 / 성분코드 1,237종) |

### 의약품 코드 크로스워크 (3) — **v2.95.0 신규**

약가마스터 의약품표준코드(305,522행) 기반 코드 해석. 표준코드(바코드)↔제품코드(보험)↔품목기준코드↔대표코드↔성분명코드↔ATC.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/notice/drug-master/resolve` | 코드(`{"code"}`) → 의약품 정체성(상품명·전 코드·성분코드·ATC, 포장 그룹화) |
| GET | `/api/notice/drug-master/<code>` | 동일 해석 (REST) |
| GET | `/api/notice/drug-master/stats` | 적재 현황 (총 코드/제품코드/취소) |

```bash
curl http://localhost:8585/api/notice/drug-master/658700100   # 보험 제품코드
# → 바라크루드정0.5mg, ingr_code 487202ATB(=drug_substitution 연계), atc J05AF10, 표준코드(바코드) 목록
```

> 🔑 `제품코드`=fee_codes.code, `성분명코드`=drug_substitution.ingr_code 연계 다리 — HIS가 스캔 바코드/임의 코드를 줘도
> 약가(fee-codes)·대체조제(drug-substitution)·DUR(성분명) 통합 라우팅. 오케스트레이터 도구 `drug_resolve`.

```bash
# 청구코드 → notice_type 해석
curl http://localhost:8585/api/notice/fee-codes/645302132
# → {"code":"645302132","notice_type":["DRUG_MATERIAL","REVIEW_CRITERIA"],"resolved":true}

# 저가약 대체조제 — 장려금 대상 + 대체 후보(동일성분 저가약)
curl http://localhost:8585/api/notice/drug-substitution/651900330
# → {"found":true,"eligible":true,"ingr_code":"160601ATB","price":59,
#    "alternatives":[{"code":"657200130","product":"라제팜정...","price":60,"eligible":true}], ...}

# 삭감위험 평가 (진료일 시행 기준)
curl -X POST http://localhost:8585/api/notice/diagnose \
  -H "Content-Type: application/json" \
  -d '{"claim":"...","code":"645302132","effective_on":"2026-06-01"}'
```

> **대체 후보 임상안전 규칙**: 정확한 주성분코드 동일(성분+함량+제형 동일)+대표코드 상이만 후보로 제시
> (함량 혼입 회피). 최종 대체조제 판단은 ERP/약사. 청구 방법(약가차액 30% 등)은 `/api/medical-law/ask`로 자문.

> **오케스트레이션 (v2.93.0)**: 위 능력은 챗 오케스트레이터(`/api/orchestrator/chat`) 도구로도 노출된다
> (`medical_law_ask`·`notice_search`·`notice_diagnose`·`drug_substitution`, config/tools.json) — qwen3:8b가
> 자연어 질의("이 약 대체조제 돼?" 등)를 자동 라우팅. 또한 **`/diagnose`는 DRUG 청구 시 응답에 `substitution`
> 필드**(eligible+저가 대체 후보)를 동반해 삭감위험과 대체 기회를 한 번에 제공한다.

---

<a id="19b-dur"></a>
## 19B. DUR 7유형 금기/주의 (4 API) — v2.94.0 / v2.97.0(7유형)

식약처 DUR(의약품 안전사용서비스) **성분정보 정본 7유형** 기반 약물 안전 점검 — 식약처
오픈 API(`data.go.kr 1471000/DURIrdntInfoService03`)로 적재. 저장: `dur.db`(`dur_interaction`
병용금기 성분 pair + `dur_single` 단일성분 금기/주의 + `dur_product` 품목 색인).
**비식별**(코드/성분명만) · **자문 보조**(실시간 차단 아님, 최종 판단 처방의·약사).

**7유형**: 병용금기 · 임부금기 · 특정연령대금기 · 노인주의 · 용량주의 · 투여기간주의 · 효능군중복.
**환자 컨텍스트**(선택 `age`/`pregnant`)로 임부·연령·노인 점검을 게이팅(미제공 시 해당 유형 생략).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/dur/check` | 약물 리스트 DUR 점검 (`{"drugs":[...], "age":?, "pregnant":?}`, 최대 50종) |
| POST | `/api/medical/dur/drug` | 단일 약물 병용금기 상대 성분 목록 (`{"drug": ...}`) |
| GET | `/api/medical/dur/drug/<code>` | 단일 약물 병용금기 프로파일 (REST) |
| GET | `/api/medical/dur/stats` | 적재 현황 (병용금기 룰·단일유형별·품목·성분 수) |

```bash
# 병용금기
curl -X POST http://localhost:8585/api/medical/dur/check \
  -H "Content-Type: application/json" -d '{"drugs": ["파록세틴", "셀레길린염산염"]}'
# → {"has_contraindication":true,"contraindications":[{"reason":"세로토닌성 증후군...",...}], ...}

# 환자 컨텍스트(임부·고령) 반영
curl -X POST http://localhost:8585/api/medical/dur/check \
  -H "Content-Type: application/json" -d '{"drugs": ["프로게스테론"], "pregnant": true, "age": 70}'
# → {"pregnancy_warnings":[...], "elderly_warnings":[...], "age_warnings":[...],
#    "duplicate_therapy":[...], "advisories":[...], "has_warning":true, ...}
```

응답 필드: `contraindications`(병용금기) · `pregnancy_warnings` · `age_warnings` · `elderly_warnings`(≥65) ·
`duplicate_therapy`(효능군중복) · `advisories`(용량/투여기간 정보성) · `has_contraindication`/`has_warning`.

> 오케스트레이터 도구 `dur_check`(age/pregnant 포함)로도 노출. 데이터는 식약처 오픈 API 정본
> (서비스키는 운영 시크릿, `scripts/ingest_dur_api.py`로 갱신 적재).

### 의약품개요정보 e약은요 (3) — **v2.98.0 신규**

식약처 e약은요 정본(4,806품목) — 환자 친화 약 정보. 자문 보조(최종 판단 의사·약사).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/drug-info` | 제품명/품목일련번호 → 효능·사용법·주의사항·상호작용·부작용·보관법 (`{"query": ...}`, 부분일치) |
| GET | `/api/medical/drug-info/<item_seq>` | 품목일련번호 직접 조회 (REST) |
| GET | `/api/medical/drug-info/stats` | 적재 현황 |

```bash
curl -X POST http://localhost:8585/api/medical/drug-info -d '{"query":"타이레놀"}'
# → {"found":true,"matches":[{"name":"타이레놀정500밀리그램","efficacy":"...","usage":"...","side_effect":"..."}], ...}
```

> 오케스트레이터 도구 `drug_info` — "이 약 어떻게 먹어요/부작용?" 자연어 자동 조회(LLM 환각 없이 정본).

---

## 20. Counseling Orchestration (6 API) — v2.62.0

상담 오케스트레이션. mindwell(상담 전문 LLM) + qwen2.5:7b(한국어 자연화) 2단계 파이프라인.

**파이프라인**: mindwell → qwen2.5 → force_korean → korean_humanize(품질 검증)
**안전장치**: 위기 키워드(자해/자살) 감지 → 자살예방상담전화(1393) 자동 안내
**복원력**: 서킷 브레이커 + 적응형 타임아웃 + risk_band=high 시 S1 규칙 재자연화

### 상담 대화

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/counseling/chat` | 상담 대화 (2단계 오케스트레이션) |
| POST | `/api/counseling/chat/stream` | 상담 대화 NDJSON 스트리밍 |

### 세션 관리

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/counseling/sessions` | 세션 목록 (limit, status 쿼리) |
| GET | `/api/counseling/session/{id}` | 세션 상세 (대화 이력) |
| DELETE | `/api/counseling/session/{id}` | 세션 삭제 |

### 시스템

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/counseling/status` | 시스템 상태 (모델 가용성 + 위기 통계) |

```bash
# 상담 대화
curl -X POST /api/counseling/chat \
  -d '{"message": "요즘 회사에서 스트레스를 많이 받아요."}'
# → {message: "...", session_id: "abc123", is_crisis: false, pipeline: {quality: {risk_band: "low"}}}

# 이어서 대화 (세션 유지)
curl -X POST /api/counseling/chat \
  -d '{"message": "잠도 잘 못 자고 식욕도 없어요.", "session_id": "abc123"}'

# SSE 스트리밍
curl -X POST /api/counseling/chat/stream \
  -d '{"message": "불안감이 심해요.", "session_id": "abc123"}'
# → NDJSON: {event:"stage"} → {event:"token"} → {event:"done"}

# 세션 이력
curl /api/counseling/session/abc123

# 시스템 상태
curl /api/counseling/status
# → {status: "ready", models: {...}, crisis_messages: 0}
```

DB: counseling.db (counseling_sessions + counseling_messages)
모델: ALIENTELLIGENCE/mindwell (9.3GB, CBT/마인드풀니스) + qwen2.5:7b (4.7GB, 한국어 자연화)
세션: 최근 10턴 컨텍스트, 24시간 TTL 자동 만료

---

## 21. Meeting Analysis (6 API) — v2.63.0

화상회의 녹화본(MP4) AI 분석. hospital-jitsi 연동.

**파이프라인**: S3 다운로드 → ffmpeg 음성 추출 → faster-whisper STT(large-v3) → qwen3:8b 요약 → force_korean → callback
**동시성**: 세마포어 2건 제한 (GPU VRAM 관리)
**콜백**: 분석 완료 시 hospital-jitsi에 POST (3회 지수 백오프 재시도)

### 분석 요청/조회

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/meeting/analyze` | 분석 요청 접수 (file_url + callback_url) |
| GET | `/api/meeting/job/{id}` | 작업 상태/결과 조회 |
| GET | `/api/meeting/jobs` | 작업 목록 (최근 50건, ?status= 필터) |
| POST | `/api/meeting/job/{id}/retry` | 실패 작업 재시도 |
| DELETE | `/api/meeting/job/{id}` | 작업 삭제 (completed/failed만) |
| GET | `/api/meeting/status` | 시스템 상태 (Whisper/ffmpeg/GPU 가용성) |

**options 계약** (2026-07-29 기준)

| 키 | 기본 | 설명 |
|----|------|------|
| `diarize` | false | **화자분리 옵트인**. 켜면 `result.speaker_segments[]`와 `transcript.segments[].speaker`가 함께 채워진다(길이 불일치 시 speaker=null로 두고 병합 생략) |
| `num_speakers` / `max_speakers` | 자동 / 10 | 화자 수 힌트 |
| `vocabulary` (또는 `hotwords`) | — | **용어 바이어싱**(배열·CSV). whisper `hotwords`로 전달돼 유사 발음을 해당 표기로 교정한다. `initial_prompt`(문맥 프롬프트)와 **역할이 다르다** |
| `initial_prompt` | — | 문체·주제 프롬프트. 용어 교정에는 `vocabulary`를 쓸 것 |
| `vision` / `keyframe_interval` | false / 30 | 슬라이드 분석 |
| `language` | ko | |

**접수·대기 정책**: 동시 처리 2건, 초과분은 **대기열로 흡수**(최대 4시간). 접수 상한은
**전체 50건 AND 호출자(키)당 30건** — 한 호출자가 큐를 독점하지 못한다. 초과 시 503 응답에
어느 상한인지 명시된다. 현재 값은 `GET /api/meeting/status`의 `max_queue`·
`max_queue_per_caller`·`queued_jobs`로 확인.

```bash
# 분석 요청 (hospital-jitsi → AI 서버)
curl -X POST /api/meeting/analyze \
  -d '{
    "file_url": "https://s3.wasabisys.com/.../recording.mp4?X-Amz-...",
    "callback_url": "https://hospital-jitsi/meet-api/internal/ai-callback",
    "options": {"summary": true, "action_items": true, "highlights": true,
                "diarize": true, "num_speakers": 2,
                "vocabulary": ["요로감염", "중심정맥관", "클로르헥시딘"]},
    "metadata": {"meeting_id": "mtg-001", "room_name": "주간정례회의", "participants": [{"name": "김팀장"}], "duration_sec": 3600}
  }'
# → {job_id: "meet-xxx", status: "pending", estimated_minutes: 15}

# 결과 폴링
curl /api/meeting/job/meet-xxx
# → {status: "completed", result: {transcript: {...}, summary: "...", action_items: [...], highlights: [...]}}

# 시스템 상태
curl /api/meeting/status
# → {whisper_available: true, ffmpeg_available: true, active_jobs: 0, max_concurrent: 2}
```

DB: meeting.db (meeting_jobs)
모델: faster-whisper/large-v3 (STT) + qwen3:8b (LLM 요약)
콜백 결과: transcript(녹취록) + summary(요약) + action_items(액션아이템) + highlights(하이라이트) + korean_quality

---

## 22. Tool Calling (2 API + 20 도구) — v2.64.0

Orchestrator에 LLM Tool Calling 통합. intent 미매칭 시 qwen3:8b가 도구를 자유 조합하여 복합 요청 처리.

**3단계 폴백**: 워크플로우 매칭 → Tool Calling(qwen3:8b) → General Chat(qwen2.5:14b)
**도구 설정**: `config/tools.json` — 코드 수정 없이 도구 추가/삭제, `enabled` 플래그로 on/off
**안전 제한**: max_tool_calls=5, tool_timeout=30초, total_timeout=120초

### 관리 API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orchestrator/tools` | 등록된 도구 목록 (enabled/disabled) |
| POST | `/api/orchestrator/tools/reload` | tools.json 다시 로드 (localhost only) |

### 등록 도구 (20개, enabled 19개)

| 카테고리 | 도구 | 내부 API |
|---------|------|----------|
| 의료 (8) | medical_triage, medical_reasoning, medical_auto_record, medical_drug_analysis, medical_predict_readmission, medical_cross_validate, medical_research, medical_patient_education, clinical_qa | /api/medical/* |
| 한국어 (5) | korean_spellcheck, korean_analyze, korean_readability, korean_style_transfer, korean_pipeline | /api/korean/* |
| 글쓰기 (4) | writing_draft, writing_polish, writing_seo, writing_translate | /api/writing/* |
| 유틸 (2) | rag_search, vision_describe (disabled) | /api/rag/*, /api/vision/* |

```bash
# 도구 목록
curl /api/orchestrator/tools
# → {tools: [...], count: 20, enabled: 19, version: 1}

# 도구 리로드
curl -X POST /api/orchestrator/tools/reload
# → {ok: true, before: 19, after: 19, version: 1}

# Tool Calling 자동 동작 (기존 chat 엔드포인트, intent 미매칭 시)
curl -X POST /api/orchestrator/chat \
  -d '{"message": "Metformin과 Aspirin 상호작용 확인해줘"}'
# → {type: "tool_calling", message: "...", tool_calls: [{name: "medical_drug_analysis", ...}], model: "qwen3:8b"}
```

Core: `core/tool_registry.py` (ToolRegistry) + `core/tool_executor.py` (실행 루프)
Config: `config/tools.json` (도구 정의, JSON Schema)

---

## 23. Node Orchestration / iMac 연계 (GPU↔iMac 자동 분산) — v2.84~2.86.0

> Blueprint: `routes/imac_link.py` | Core: `core/orchestrator/` | 가이드: [ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md)
> **호출자는 GPU만 호출** → 오케스트레이터가 capability를 iMac에 자동 배정(현재 detect+STT LIVE), 실패 시 GPU 로컬 폴백. 호출 코드 무변경.

| # | Method | Endpoint | 설명 |
|---|--------|----------|------|
| 1 | GET | `/api/orchestrator/nodes` | 노드×capability×health×라우팅 실적(위임/폴백/오류)·지연·breaker |
| 2 | POST | `/api/orchestrator/registry/reload` | capability/정책 무재배포 리로드 (localhost only) |
| 3 | GET | `/api/imac-link/status` | 터널 3채널 + 워커 health + 위임 config/실적 |

```bash
# 평소처럼 GPU만 호출 → detect/STT 자동 iMac 위임(engine=imac-worker), iMac 다운 시 폴백
curl -X POST http://localhost:8585/api/detect/image -F 'image=@x.png'   # ml.engine = imac-worker | gpu-local

# 라우팅 상태/실적
curl http://localhost:8585/api/orchestrator/nodes
# → {capabilities:{detect:{policy:prefer_imac},...}, nodes:{gpu-local:{...}, imac-worker:{reachable,breaker,latency_ewma_ms,...}},
#    metrics:{counts:{"detect|imac-worker|delegated":N,...}}}

# 정책/capability 리로드 (config/orchestrator.json 수정 후)
curl -X POST http://localhost:8585/api/orchestrator/registry/reload
```

- **자동 분산 capability**: detect(LIVE) / stt(LIVE, meeting_pipeline) / mark(위임가능·미배선) / embed(기획)
- **활성화**: iMac 워커+터널 기동 후 `scripts/activate_imac_delegation.sh --delegate detect,mark,stt`
- **보안**: 외부(비-localhost)는 Bearer 필요(무인증 401). 상세 토폴로지/운영: [ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md)
- 인증: 모든 엔드포인트 localhost bypass + 외부 Bearer (다른 Blueprint와 동일)

> ⚠️ 이 문서는 v2.64.0 이후 일부 신규 Blueprint(imac_link/notice)가 누락돼 있었음 — orchestrator/imac-link는 위에 반영(2026-06-08). 전체 엔드포인트는 `/openapi.json`·`/docs`(Swagger) 자동생성이 정본.

---

## 개별 상세 문서

| Document | Description |
|----------|-------------|
| [ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md) | Node Orchestrator 호출법/토폴로지/활성화/정책/운영 |
| [CHAT_API_GUIDE.md](CHAT_API_GUIDE.md) | 채팅 API 세션 메모리 + 품질 검증 가이드 |
| [IMAGE_API_GUIDE.md](IMAGE_API_GUIDE.md) | 이미지 생성 상세 (9 모델, 15 스타일) |
| [MEDICAL_API_GUIDE.md](MEDICAL_API_GUIDE.md) | 의료 AI 22 API + 구조화 질의 |
| [MEDICAL_AI_INTEGRATION.md](MEDICAL_AI_INTEGRATION.md) | HIS 연동 가이드 |
| [CODING_API_GUIDE.md](CODING_API_GUIDE.md) | 코딩 AI 17 API 레퍼런스 |
| [WEB_INTEGRATION_GUIDE.md](WEB_INTEGRATION_GUIDE.md) | 웹 연동 가이드 |
| [IMAGE_API_QUICKREF.md](IMAGE_API_QUICKREF.md) | 이미지 API 빠른 참조 |
