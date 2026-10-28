# WeRU.B AI Server — 아키텍처 & 기능 명세

> Version: v2.106.1 | 592+ API Routes | 33 Blueprint + 48 Core | 35 Models | **Node Orchestrator (운영 가동 — GPU만 호출하면 detect+STT를 iMac에 자동 배정, prefer_imac, 폴백 never-raise, 노드0=무변경, 엔드포인트 인증)** + **환자 디지털 트윈 아바타 (R1 생리·R2 장기메시 glTF·R3 심장 EP)** + **의료 정본 데이터 (DUR 8유형·e약은요·약가·코드 크로스워크·의료법 RAG)** + **실시간 STT+번역 위임** + **교차 아키텍처 device 통합 (core/device.py)** + **Apple Silicon Worker (iMac M1 :8587 — 판별 MPS/마킹/STT mlx-whisper, 위임+폴백)** + Tool Calling + Meeting Analysis + Counseling + Korean API + Story/Novel + Medical Cross-Validation + Dashboard
> Updated: 2026-06-21

---

## 1. 시스템 아키텍처

### 1-1. 전체 구조

```
                                    ┌─────────────────────────────────────┐
                                    │         GPU Server (RTX 5080)        │
                                    │         Ubuntu 24.04, 16GB VRAM      │
[Web Apps]                          │                                     │
┌──────────────┐     HTTPS/SSH      │  ┌───────────────────────────────┐  │
│ werub-deploy │ ──────────────────>│  │    WeRUBLLMManager v2.86.0    │  │
│ (Next.js)    │     터널 :8585     │  │    Flask + Gunicorn (gthread) │  │
├──────────────┤                    │  │                               │  │
│ Admin Console│                    │  │  app.py (~9.7K줄)              │  │
│ (Next.js)    │                    │  │  ├─ routes/ (29 Blueprint)    │  │
├──────────────┤                    │  │  ├─ core/   (21 modules)      │  │
│ HIS System   │                    │  │  └─ templates/ (4 HTML)       │  │
├──────────────┤                    │  └──────────┬────────────────────┘  │
│ Kiosk/Mobile │                    │             │                       │
└──────────────┘                    │  ┌──────────▼────────────────────┐  │
                                    │  │      Ollama v0.21.0           │  │
[iMac M1 16GB]                      │  │      :11434 (localhost)       │  │
┌────────────────┐    SSH 터널      │  │      19 models loaded         │  │
│ Ollama (Metal) │<───────────────── │  └──────────┬────────────────────┘  │
│ 경량 오프로드  │                  │             │                       │
│ + 특화 워커    │                  │  ┌──────────▼────────────────────┐  │
└────────────────┘                  │  │      ComfyUI :8188             │  │
 3b 22.4·7b 10.9 tok/s (100% GPU)   │  │      (on-demand, 기본 OFF)     │  │
                                    │  │      9 checkpoints, 4 LoRA     │  │
                                    │  └───────────────────────────────┘  │
                                    │                                     │
                                    │  ┌──────────────────────────────┐   │
                                    │  │  Storage                      │   │
                                    │  │  SQLite (10 DB) + ChromaDB    │   │
                                    │  └──────────────────────────────┘   │
                                    └─────────────────────────────────────┘
```

### 1-2. 레이어 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                      API Gateway (Flask)                      │
│  CORS · Rate Limit · Authentication · Request Logging        │
├─────────────────────────────────────────────────────────────┤
│                    Blueprint Layer (25)                       │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────────┐  │
│  │ ollama   │ │ queue   │ │ image   │ │ system/activity  │  │
│  │ (23 API) │ │ (16)    │ │ (15)    │ │ rag/vision (30)  │  │
│  ├─────────┤ ├─────────┤ ├─────────┤ ├──────────────────┤  │
│  │ medical  │ │med_cli  │ │ triage  │ │   training       │  │
│  │ (24)     │ │ (7)     │ │ (4)     │ │   (32)           │  │
│  ├─────────┤ ├─────────┤ ├─────────┤ ├──────────────────┤  │
│  │ hr_law   │ │ game    │ │ coding  │ │  orchestrator    │  │
│  │ (23)     │ │ (40)    │ │ (16)    │ │  (7)             │  │
│  ├─────────┤ ├─────────┤ ├─────────┤ ├──────────────────┤  │
│  │med_adv   │ │ fhir    │ │ws/gpu   │ │  writing         │  │
│  │ (9)      │ │ (6)     │ │ (9)     │ │  (11)            │  │
│  ├─────────┤ ├─────────┤ ├─────────┤ ├──────────────────┤  │
│  │ story    │ │ novel   │ │med_law  │ │  drug/provenance │  │
│  │ (31)     │ │ (78)    │ │ (6)     │ │  (18)            │  │
│  └─────────┘ └─────────┘ └─────────┘ └──────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                    Core Layer (12 modules)                    │
│  config · cache · korean · security · queue_engine · logger  │
│  reasoning_chain · gpu_scheduler · priority_queue            │
│  medical_validator · websocket_handler · llm_resilience      │
├─────────────────────────────────────────────────────────────┤
│                    Data Layer                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ SQLite   │ │ ChromaDB │ │ Ollama   │ │ ComfyUI       │  │
│  │ 10 DBs   │ │ Vector   │ │ 18 LLMs  │ │ 9 checkpoints │  │
│  │ WAL mode │ │ Search   │ │ Embed 3  │ │ 4 LoRA        │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 1-3. 데이터 흐름

```
[요청] → Nginx → SSH 터널 → Gunicorn (2W × 8T = 16 동시)
  │
  ├─ API Key 인증 (Bearer / X-Medical-Key / X-Game-Key)
  │
  ├─ Blueprint 라우팅 → 핸들러 실행
  │     │
  │     ├─ DB 조회 (SQLite WAL, <1ms)
  │     ├─ 벡터 검색 (ChromaDB, ~50ms)
  │     ├─ LLM 추론 (Ollama, 1~10초)
  │     └─ 이미지 생성 (ComfyUI, 10~30초)
  │
  ├─ ChatFairQueue (글로벌 3건 + 유저 1건 동시 제한)
  ├─ GPUResourceManager (LLM ↔ ComfyUI VRAM 자동 전환)
  │
  └─ [응답] JSON / NDJSON(스트리밍) / Image
```

---

## 2. 기능 명세

### 2-1. Blueprint 상세

| # | Blueprint | Routes | 파일 | 주요 기능 |
|---|-----------|--------|------|----------|
| 1 | **ollama** | 23 | routes/ollama.py | LLM 프록시, API 키 관리, 설정, embed, fact-check |
| 2 | **queue** | 16 | routes/queue.py | 배치 큐 (submit/batch/upload), 상태, 통계 |
| 3 | **image** | 15 | routes/image.py | ComfyUI 프록시, 이미지 생성/관리, 캐릭터 일관성 |
| 4 | **hr_law** | 23 | routes/hr_law.py | 근로기준법 AI, 멀티테넌트, 취업규칙, 연차/퇴직금 계산 |
| 5 | **game** | 40 | routes/game.py | NPC 대화/성장, 히로인, 시나리오, 세계관, 공유 NPC |
| 6 | **training** | 32 | routes/training.py | QLoRA 학습, 모더레이션, 사용자/역할 관리 |
| 7 | **medical** | 22 | routes/medical.py | 진료 AI (SOAP, 감별진단, RAG, 설명문, 의무기록) |
| 8 | **medical_cli** | 7 | routes/medical_cli.py | 역할별 CLI (접수/수납/간호/의사/청구), 코드 검색 |
| 9 | **triage** | 4 | routes/triage.py | 증상→진료과 추천, 응급 감지, 대화형 |
| 10 | **coding** | 16 | routes/coding.py | 코드 생성/리뷰/디버그/테스트/변환/RAG |
| 11 | **orchestrator** | 7 | routes/orchestrator.py | 워크플로우 엔진, 스마트 채팅 |
| 12 | **system** | 11 | routes/system.py | 헬스체크, 벤치마크, 모니터링 |
| 13 | **activity** | 6 | routes/activity.py | 활동 로그, 캐시, Prometheus |
| 14 | **rag** | 5 | routes/rag.py | 범용 RAG (색인/검색/질의) |
| 15 | **vision** | 5 | routes/vision.py | 이미지 설명 (qwen3-vl:8b) + 의료 영상 판독 (MedGemma 4B) |
| 16 | **medical_advanced** | 9 | routes/medical_advanced.py | P4: 추론 체인, 자동 의무기록, 예측 분석, **앙상블** |
| 17 | **writing** | 11 | routes/writing.py | 글쓰기 AI (톤앤매너, SEO, 코칭, craft/stream, translate, languages) |
| 18 | **fhir** | 6 | routes/fhir.py | FHIR R4 래퍼 (DiagnosticReport 등) |
| 19 | **ws** | — | core/websocket_handler.py | SSE 스트리밍 |
| 20 | **chat** | — | routes/chat.py | 채팅 API |
| 21 | **medical_training** | — | routes/training.py | 의료 모델 학습 UI |
| 22 | **drug_batch** | 12 | routes/drug_batch.py | 약물 배치 파이프라인 + 스테이징 관리 + Night Mode |
| 23 | **provenance** | 6 | routes/provenance.py | 데이터 출처 추적 (타임라인, 요약, 검색) |
| 24 | **story** | 31 | routes/story.py | **스토리 엔진** — 생성/이어쓰기/분기/세계관/인물/관계/복선/검증 |
| 25 | **novel** | 78 | routes/novel.py | **소설 플랫폼** — 프로젝트/시놉시스/플롯/아웃라인/집필/퇴고/내보내기 + 버전/요약/타임라인/시리즈/소스/컴포넌트/레시피 |
| 26 | **counseling** | 6 | routes/counseling.py | **상담 오케스트레이션** — mindwell+qwen2.5 파이프라인, 세션관리, 위기감지, NDJSON 스트리밍 |
| 27 | **imac_link** | 3 | routes/imac_link.py | **iMac 연계 상태** — 터널 3채널+워커 health+위임 실적 (v2.82.0) + **Node Orchestrator** `GET /api/orchestrator/nodes`·`POST /api/orchestrator/registry/reload` (v2.84.0) |
| 28 | **notice** | 5 | routes/notice.py | **ERP 공시 수명주기** — 등록(멱등)/일괄/개정(supersedes)/폐지/삭제, 해시 검증 (v2.82.0) |
| — | **app.py** | 21 | app.py | 대시보드, 로그인, CORS, Admin UI, Model Manager (/model-manager), Orchestration Editor (/orchestration-editor) |

### 2-2. Core 모듈

| Module | 파일 | 역할 |
|--------|------|------|
| **config** | core/config.py | 설정 로드/저장, OLLAMA_BASE |
| **cache** | core/cache.py | TTLCache (LLM 500건/1h, 이미지 모델 5m) |
| **korean** | core/korean.py | 한국어 필터, 품질 검증, 자동 재시도 |
| **security** | core/security.py | API 키 생성/검증, 해시 |
| **queue_engine** | core/queue_engine.py | ChatFairQueue (글로벌 3, 유저 1) |
| **chat_memory** | core/chat_memory.py | 대화 세션 관리, 메시지 저장, LLM 압축 |
| **db** | core/db.py | SQLite 연결 풀, 테이블 초기화 |
| **logger** | core/logger.py | 로깅 설정, 로테이션 |
| **drug_batch_scheduler** | core/drug_batch_scheduler.py | 약물 배치 스케줄러 + 야간 자동 모드 |
| **drug_collector** | core/drug_collector.py | 수집 어댑터 (LLM_SEED, Manual, MFDS) |
| **drug_enricher** | core/drug_enricher.py | LLM 배치 정제 + JSON 파싱 + 신뢰도 산정 |
| **drug_validator** | core/drug_validator.py | 4-Layer 검증 (Schema/Dup/Clinical/CrossRef) |
| **llm_resilience** | core/llm_resilience.py | LLM 호출 복원력 (타임아웃 관리: gemma4/exaone-deep/dna 등) |
| **korean_humanize** | core/korean_humanize.py | 한국어 분석 30+함수 (메트릭22+가독성+응집+경어+POV+감각+통계+diff) |
| **korean_spellcheck** | core/korean_spellcheck.py | 맞춤법 검사 (Ollama LLM 자체) |
| **reasoning_chain** | core/reasoning_chain.py | 추론 체인 + Multi-Model Ensemble |
| **meeting_stt** | core/meeting_stt.py | faster-whisper STT (large-v3, 30분 청크 분할) |
| **meeting_pipeline** | core/meeting_pipeline.py | 회의 분석 파이프라인 (download→STT→LLM→callback) |
| **tool_registry** | core/tool_registry.py | Tool Calling 도구 레지스트리 (config/tools.json 로드) |
| **tool_executor** | core/tool_executor.py | Tool Calling 실행 엔진 (LLM↔도구 반복 루프, 동기+SSE) |
| **medical_validator** | core/medical_validator.py | 의료 응답 환각 검증 + ICD/약물 교차 체크 |
| **device** | core/device.py | **교차 아키텍처 device 단일화** — resolve(cuda/mps/cpu) + 미가용 폴백, pipeline_arg (v2.84.0) |
| **orchestrator** | core/orchestrator/ | **Node Orchestrator Phase 1** — registry/nodes(Health Plane·EWMA·breaker)/scheduler(게이트+가중+hysteresis)/client/dispatch(폴백 무전파)/metrics. capability 기반 GPU↔iMac 동적 배정, 노드0=무변경 (v2.84.0) |
| **apple_worker** | core/apple_worker.py | GPU→iMac 위임 클라이언트 (config 휴면, Darwin 가드, 폴백 None) — v2.81.0. 오케스트레이터가 config 키 재사용(Phase 2 수렴 예정) |
| **worker_guard** | core/worker_guard.py | iMac 워커 메모리/thermal 가드 + heavy 상호배제 락 (macOS 전용) — v2.81.0 |
| **notice_indexer** | core/notice_indexer.py | 공시 정규화 7항 + content_hash + 조문 청크 (erp-notice-rag) — v2.81.0 |
| **notice_db** | core/notice_db.py | notice.db DDL/연결 (UNIQUE(erp_ref_id,revision_no), WAL) — v2.81.0 |

### 2-3. 데이터베이스

| DB | 크기 | 용도 |
|----|------|------|
| queue.db | 1.9MB | 작업 큐, API 키, 설정, **채팅 세션/메시지** |
| hr_law.db | 408KB | HR Law 테넌트, 법률, 질의 이력 |
| clinic_codes.db | 580KB | KCD 1000, EDI 256, **약품 402**, 상호작용 263, 매핑 100, 삭감 140, **provenance 402**, **batch_run**, **staging_drug** |
| medical.db | 192KB | 의료 테넌트, 상담, 지식 156건 |
| game.db | 172KB | 게임 테넌트, NPC, 히로인 |
| orchestrator.db | 32KB | 워크플로우 실행 이력 + Tool Calling 로그 (tool_call_logs) |
| story.db | — | **스토리/소설** — story 8 테이블 (stories, chains, summaries, worlds, characters, relationships, foreshadowing, validations) + novel 14 테이블 (projects, chapters, revisions, chapter_versions, chapter_summaries, timeline_events, series, series_volumes, sources, components, component_usage, recipes, project_refs, project_recipe) = **22 테이블** |
| training.db | 20KB | 학습 이력 |
| users.db | 16KB | 사용자, 역할, 권한 |
| chromadb_data/ | 2.2MB | 벡터 검색 (medical_knowledge 157건, code_knowledge) |

### 2-4. 인증 체계

| 방식 | 헤더 | 대상 | 비고 |
|------|------|------|------|
| Bearer Token | `Authorization: Bearer sk-xxx` | Ollama, Queue, System, Image | API 키 발급 관리 |
| Medical Key | `X-Medical-Key: med-xxx` | Medical, CLI | 병원별 테넌트 |
| Game Key | `X-Game-Key: game-xxx` | Game NPC | 게임사별 테넌트 |
| HR Law Key | `X-Tenant-Key: hr-xxx` | HR Law | 회사별 테넌트 |
| Web Session | Flask Session (PBKDF2) | Admin UI | 브라우저 로그인 |
| 없음 | — | Triage, Coding, Code RAG | 공개 API |

---

## 3. 모델 아키텍처

### 3-1. GPU VRAM 관리

```
[일반 모드] qwen2.5:14b 상주 (10GB) → 여유 6GB
[의료 모드] + meditron/medllama2 온디맨드 (3.8GB) → 합계 14GB
[코딩 모드] qwen2.5-coder:14b 교체 적재 (9GB)
[이미지 모드] ComfyUI 자동 시작 → LLM VRAM 해제 → 완료 후 복원

OLLAMA_MAX_LOADED_MODELS=2  ← 동시 2모델 적재 가능
OLLAMA_NUM_PARALLEL=4       ← 동시 4요청 처리
OLLAMA_FLASH_ATTENTION=1    ← Flash Attention 활성
OLLAMA_KV_CACHE_TYPE=q4_0   ← KV Cache 양자화 (VRAM 절약)
OLLAMA_KEEP_ALIVE=-1        ← 모델 영구 상주
```

### 3-2. 모델 배정

| 용도 | 모델 | VRAM | 적재 방식 |
|------|------|------|----------|
| 범용 (한국어) | qwen2.5:14b | 9GB | **상주** |
| 코딩 | qwen2.5-coder:14b | 9GB | 온디맨드 |
| 빠른 코딩 | deepseek-coder-v2:16b | 8.9GB | 온디맨드 |
| 한국어 fallback | exaone3.5:7.8b | 4.8GB | 온디맨드 |
| 의료 분석 (EN) | medllama2 | 3.8GB | 온디맨드 |
| 약물 정보 (EN) | meditron | 3.8GB | 온디맨드 |
| 이미지 설명 | **qwen3-vl:8b** | 6.5GB | 온디맨드 (폴백: moondream:1.8b) |
| RAG 임베딩 | bge-m3 (1024d) | 1.1GB | 온디맨드 |
| 코드 임베딩 | nomic-embed-text (768d) | 274MB | 온디맨드 |

---

## 4. 핵심 서비스 상세

### 4-1. ChatFairQueue (v2.24.0)

```
글로벌 동시: 3건 | 유저별: 1건 | 대기열: 20건 | 타임아웃: 60초
GatedOllamaSession: 모든 LLM 호출에 자동 적용
```

### 4-2. GPUResourceManager (v2.25.0)

```
ComfyUI 기본 OFF → 이미지 요청 시 자동 시작 (~5초)
완료 후 5분 idle → ComfyUI 자동 종료 (VRAM 회수)
LLM ↔ ComfyUI 양방향 GPU 스케줄링
```

### 4-3. Korean Quality (core/korean.py)

```
중국어/일본어 필터 → 한글 비율 검증 (30% 미만 시 재시도)
최대 2회 자동 재시도 → 시스템 프롬프트 강화
```

### 4-4. 벡터 검색 (ChromaDB)

| Collection | Docs | Embedding | 용도 |
|------------|------|-----------|------|
| medical_knowledge | 157 | bge-m3 (1024d) | 의학 지식 RAG |
| code_knowledge | 6+ | nomic-embed (768d) | 코드 RAG |
| werub_docs | — | bge-m3 | 범용 RAG |

---

## 5. 보안

| 항목 | 조치 |
|------|------|
| 네트워크 | 8585 → 127.0.0.1 바인딩 (외부 차단) |
| 방화벽 | UFW 활성 (8585/3389 DENY) |
| 인증 | API Key (PBKDF2) + 세션 (8h 만료) |
| 비밀번호 | PBKDF2-HMAC-SHA256 200K rounds |
| SSRF | 웹훅 URL 검증 (내부 IP 차단) |
| Rate Limit | 분당 120회 (IP 기반) |
| 감사 | 모든 API 호출 자동 기록 |
| 의료 면책 | 모든 의료 AI 응답에 자동 포함 |
| SSH | fail2ban + 공개키 인증 |

---

## 6. 운영

### 6-1. 모니터링

| 항목 | 엔드포인트 | 간격 |
|------|-----------|------|
| Health Check | `/api/health` (8항목) | cron 5분 |
| Prometheus | `/metrics` | scrape 15초 |
| GPU 상태 | `/api/gpu/status` | — |
| 서버 통계 | `/api/server/stats` | — |
| 활동 로그 | `/api/activity` | 자동 기록 |

### 6-2. 로그

```
logs/app.log      — 애플리케이션 로그
logs/queue.log    — 큐 워커 + 에러 로그
logs/health_check.log — 헬스체크 결과
logrotate: 일별, 7일 보관, 50MB 임계
```

### 6-3. 백업

| 대상 | 방법 |
|------|------|
| 코드 | GitHub (seanshin/werub-ai-server) |
| DB | SQLite WAL → 파일 복사 |
| 모델 | Ollama 재다운로드 가능 |
| ChromaDB | chromadb_data/ 디렉토리 복사 |

---

## 7. 개선 로드맵

### 7-1. 현재 한계 + 개선 방안

| 한계 | 현재 | 개선 방안 | 트리거 |
|------|------|----------|--------|
| **동시 처리** | Gunicorn gthread 96 (SSE 구독자 상한 24+IP축출) | FastAPI + Uvicorn (async 수백) | 동시 10명+ |
| **캐시** | TTLCache (프로세스 내) | Redis (프로세스 간 공유) | FastAPI 전환 시 |
| **DB** | SQLite (단일 서버) | PostgreSQL (동시 쓰기) | 동시 20명+ |
| **LLM 속도** | Ollama (단일 GPU) | vLLM (배치 추론 2~3배) | 동시 50명+ |
| **모델 교체** | 수동 (`ollama pull`) | 모델 핫스왑 (pending_model) | 운영 자동화 시 |
| **워크플로우** | 진료 5개 | 블로그/카페/워크스페이스 25개 | 수요 발생 시 |
| **테넌트 관리** | 개별 API 조회 | 통합 대시보드 (12 API) | 테넌트 20개+ |
| **VRAM** | 16GB (모델 교대) | 멀티 GPU 또는 24GB+ | 동시 모델 3개+ |

### 7-2. 백로그 (기획서 준비됨)

| 항목 | 기획서 | 공수 | 트리거 |
|------|--------|------|--------|
| FastAPI 전환 | plan_fastapi_migration.md | 5.5일 | 동시 10명+ |
| 테넌트 대시보드 | plan_tenant_dashboard.md | 2.5일 | 테넌트 20개+ |
| 오케스트레이터 Phase 3-7 | plan_orchestrator.md | 9일 | 복합 워크플로우 수요 |

### 7-3. 코드 품질 지표

```
app.py:          ~9,800줄
routes/:         ~26,400줄 (29 Blueprint)
core/:           ~2,800줄 (19 모듈)
총 코드:         ~36,500줄
API:             522+ endpoints
모델:            39개 (의료 11 + 글쓰기 3 + 비전 6 + 상담 2 + STT 1 + 기존 16)
품질 점수:       93/100 (bare except 0, silent pass 0, conn.close 전부 보호)
보안:            @require_auth 전체 적용, SQL 파라미터화 100%
성능:            SOAP 병렬(3x), 의료 캐싱, RAG 캐시, gzip 압축
```

---

## 8. 기술 스택

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12 |
| Web Framework | Flask 3.x + Gunicorn (gthread) |
| LLM Engine | Ollama v0.21.0 |
| Image Gen | ComfyUI (on-demand) |
| Vector DB | ChromaDB 1.5.8 |
| Database | SQLite 3 (WAL mode) |
| Embedding | bge-m3 (1024d), nomic-embed-text (768d) |
| GPU | NVIDIA RTX 5080 16GB (Driver 570) |
| OS | Ubuntu 24.04.3 LTS |
| Reverse Proxy | Nginx + SSH 리버스 터널 |
| Monitoring | Prometheus metrics + health_check.sh (cron 5분) |
| CI/CD | GitHub → 수동 배포 (git pull + gunicorn restart) |
