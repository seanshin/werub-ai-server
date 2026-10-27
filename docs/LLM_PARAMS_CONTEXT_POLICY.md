# LLM 추론 파라미터·컨텍스트·타임아웃 정책 (R4 — 허가신청서 인용용)

**v1.1** 2026-07-19 | WeRU.B AI 서버(v2.124.10 · Ollama 0.30.11 · RTX 5080 16GB 자체 호스팅)
| 대응: 식약처 「LLM 기반 디지털의료기기 허가·심사 가이드라인」(안내서-1511-01) — 트윈 요청 R4
| 정본: 본 문서(코드·라이브 실측 기준·근거 코드 경로 병기, 변경 시 버전 상향)

변경 이력: v1.0(2026-07-19 초판) → **v1.1**(동일자 — /v1 필드 매핑·공정 큐 수치·응답 캐시
정책·모델 상주 정책·버전 확인 절차 추가)

## 1. 컨텍스트 윈도우 정책

| 항목 | 값 | 근거 |
|---|---|---|
| 전역 기본 컨텍스트 | **8,192 tokens** | Ollama env `OLLAMA_CONTEXT_LENGTH=8192` (systemd drop-in, 전 모델 공통 기본) |
| `/v1/chat/completions` | **요청별 16,384 tokens 주입** | `routes/openai_compat.py` — config `openpencil_num_ctx`(기본 16384) |
| 임베딩(bge-m3) | 2,048 tokens | `client/rag.py embed_text`의 `options.num_ctx=2048` 명시(상주 LLM과 VRAM 공존 설계) |
| 모델 자체 상한 | qwen2.5 계열 32,768 | 서버 설정이 하위 상한. 32K 필요 시 협의 — 14b@32K=KV 캐시가 VRAM 초과로 CPU 스필(비권장), 7b@32K 권장 |

### 1.1 초과 시 동작 — **오류 반환(무성 절단 아님)**

Ollama 0.30.9+는 입력이 컨텍스트 상한을 초과하면 **요청을 오류로 반환**한다(구버전의
무성 앞부분 절단 아님 — 2026-07 v2.114.0 업그레이드 검증에서 실측 확인). 따라서 "환자
요약이 길어져 임상 중요정보가 조용히 잘리는" 시나리오는 **구조적으로 발생하지 않으며**,
초과는 호출자에게 실패로 드러난다(FMEA [표7-2] 심각도 산정 근거). 호출자 대응: 오류
수신 시 입력 분할·사전 요약 후 재시도.

### 1.2 서버측 애플리케이션 캡 (프롬프트 조립층)

컨텍스트 상한과 별개로 서버가 프롬프트를 조립하는 경로에는 결정적 캡이 있다:

| 경로 | 캡 | 목적 |
|---|---|---|
| 의료법 RAG 청크 | 청크당 1,500자(config `medlaw_chunk_chars`, 200~4,000 클램프) | 열거 조문이 캡에 잘려 모델이 기억으로 보완하는 환각 클래스 방지를 위해 600→1500 상향(v2.124.6) + 벌칙 청크는 전문 기준 교차참조 주석으로 캡 밖 목록도 매핑 보존(v2.124.9) |
| medical reference 컨텍스트 | 3,000~5,000자(질문 길이 적응) | `routes/medical.py` 하이브리드 검색 컨텍스트 |
| 채팅 이력 주입 | 최근 10턴+20턴 초과 시 압축 | `core/chat_memory.compress_chat_history` |

## 2. 추론 파라미터 정책

### 2.1 클라이언트 파라미터 수용 여부 (경로별)

| 경로 | temperature/top_p/max_tokens/seed | 필드 위치 |
|---|---|---|
| `/v1/chat/completions` | **전부 적용** · seed 지원 | top-level(OpenAI 규격) |
| `/api/queue/submit`(배치) | 적용 | `options` 하위(Ollama 규격) |
| `/api/chat` · `/api/generate` | **무시**(서버 고정) | — |
| `/api/medical/*` · `/api/medical-law/*` | **무시**(서버 고정, `model`만 오버라이드 가능) | — |

허가 대상 트래픽 권장 경로 = **`/v1/chat/completions`** — 모델·파라미터·seed를 호출자가
완전 통제하므로 [표8] 변경 관리의 비교 기준선(고정 조건 재현)을 어플리케이션측에서
독립적으로 성립시킬 수 있다.

### 2.2 /v1 필드 매핑 (OpenAI → Ollama, `routes/openai_compat.py`)

| OpenAI 필드 | 서버 처리 | 상한/기본 |
|---|---|---|
| `model` | 그대로 전달(자동 pull 없음 — 미설치 모델=오류) | — |
| `max_completion_tokens`(신)/`max_tokens`(구) | `options.num_predict` | **캡 16,384**(`_MAX_NUM_PREDICT` — 초장문 자원 점유 방지) |
| `temperature` | `options.temperature` | 숫자형만 수용 |
| `top_p` | `options.top_p` | 숫자형만 수용 |
| `seed` | `options.seed` | int만 수용 |
| `messages`/`tools` | 패스스루(+`options.num_ctx=16384` 주입) | — |
| 스트리밍 | Ollama NDJSON → **OpenAI SSE**(`data: {...}` + `[DONE]`) 변환 | 파서는 `choices[0].delta`/`message.content` 처리 필요 |

seed 재현성 주의: 동일 환경·동일 판본에선 사실상 재현되나, GPU 부동소수 연산 특성상
비트단위 재현이 항상 보장되지는 않음(신청서 표현: "결정적 조건 고정" 수준으로 기술 권장).

### 2.3 서버 고정 기본값 (의료 경로 — 코드 실측)

의료 경로는 **보수적 저온 고정**(출력 변동성 최소화·근거 우선). 호출자 파라미터를 받지
않는 것 자체가 정책이다(임상 보조 경로에서 고온 창의성 설정을 구조적으로 배제).

| 경로/기능 | temperature | num_predict | 근거 |
|---|---|---|---|
| `/api/medical/summarize`·`analyze` 등 임상 문서 | 0.2 | 2,000(섹션 축약 500) | `routes/medical.py` |
| `/api/medical/reference`·`explain` | 0.3 | 1,500 | 〃 |
| `/api/medical/drug-analysis`(2단 LLM) | 0.2 | 1,500~2,000 | `routes/medical_advanced.py` |
| `/api/medical/predict/*` | 0.2 | 경로별 | 〃 |
| `/api/medical-law/ask` | 0.3 | 1,024 | `routes/medical_law.py _generate` |
| `/api/medical-law` 구조화(json 강제) | 0.2 | 1,024 | 〃 `_generate_json`(`format:"json"` 제약 디코딩) |
| `/api/chat/enhanced` 단계 공통 | 0.4 | 2,048 | `routes/ollama.py` |

## 3. 타임아웃·동시성·재시도 정책

| 항목 | 값 | 근거 |
|---|---|---|
| `/api/chat`(스트림/비스트림) | 서버→Ollama 600s | `routes/ollama.py` |
| `/api/chat/enhanced` | 단계당 120s | 〃 |
| `/api/medical-law/*` | 생성 60s · 구조화 90s | `routes/medical_law.py` |
| **공정 큐(Fair Queue)** | 동시 실행 **전역 3 · 사용자(IP)당 1** · 대기열 20 · 대기 상한 60s | `core/queue_engine.py` |
| 한도 초과 응답 | **429 + `retry_after` + 큐 통계**(무한 대기 아님 — 즉시 실패 명시) | 〃 |
| Ollama 동시 추론 | `OLLAMA_NUM_PARALLEL=2` · 상주 모델 1(`OLLAMA_MAX_LOADED_MODELS=1`) | systemd drop-in |
| 배치 큐 재시도 | 백오프 30/60/120s(`next_retry_at` 게이트 — 즉시 재큐 아님) · 최대 3회 | `app.py`(v2.110.4) |
| LLM 오류 시 | 의료 경로=오류 반환(임의 폴백 생성 없음) · 한국어 품질 미달 시만 저온 재시도 최대 2회 | `routes/ollama.py` |

## 4. 응답 캐시 정책 (재현성·변경 반영 관점)

| 캐시 | 규모/TTL | 키 | 규제 관점 함의 |
|---|---|---|---|
| LLM 응답 캐시(챗) | 500건 / **1시간** | md5(모델+사용자 메시지) | 동일 질의 1시간 내 재요청=**저장 응답 반환(재생성 없음)**. `no_cache:true`로 우회 가능 |
| 의료법 질의 캐시 | 300건 / 1시간 | 질문+카테고리 해시 | 프롬프트·코퍼스 변경 후 최대 1시간 구응답 잔존 가능(워커 재기동 시 소멸 — 배포는 graceful reload라 즉시 무효화) |
| 의료 프롬프트 캐시 | 200건 / 1시간 | 〃 | 〃 |
| Ollama prefix 캐시 | 엔진 내부 | KV prefix | 동일 프리픽스 prefill 재사용(출력 결정성과 무관·성능 전용, 실측 98% prefill 단축) |

**검증·평가 시 주의**: 성능/품질 측정은 캐시 우회 또는 배포 직후 수행(캐시 히트가
지연·출력 측정을 오염). 야간 자동 평가 러너는 매 배치 신규 생성 경로를 사용한다.

## 5. 모델 상주·로딩 정책

| 항목 | 값 | 함의 |
|---|---|---|
| 상주 모델 | qwen2.5:14b(`keep_alive=-1` 상시) | 챗 fast-lane — 로드 지연 0 |
| 기타 모델 | `OLLAMA_KEEP_ALIVE=5m` 후 언로드 | 첫 호출 시 콜드 로드 수초 발생 가능(digest 동일=동일 가중치 보장) |
| 동시 상주 | 최대 1(MAX_LOADED=1) | 타 모델 호출 시 상주 일시 축출 후 자동 복원 |
| 모델 다운로드 | 수동 관리만(자동 pull 없음) | 운영자 개입 없이 모델 집합이 변하지 않음 |

## 6. 서빙 엔진 고정 환경 (재현 조건)

| 항목 | 값 |
|---|---|
| 엔진 | Ollama **0.30.11** — 수동 바이너리 관리(자동 업데이트 없음), 업그레이드=검증 스크립트 통과 후 릴리즈 단위 |
| 어텐션/캐시 | Flash Attention on · KV cache **q8_0** 양자화 |
| 모델 형식 | GGUF(양자화 태그·**digest로 판본 고정**) |
| 모델 교체 | 릴리즈 단위만 · 사전 통보(규제 회신 R2 합의) · 인벤토리 정본 `docs/01-plan/references/mfds-model-inventory.md` |

### 6.1 판본 확인 절차 (감사·기록용)

```bash
# 서버 소프트웨어 버전(릴리즈 단위)
curl https://example.org/ollama/api/version          # → {"version": "2.124.10", ...}
# 모델 판본(digest) — 신청서 기재 3요소: 태그 + digest + 엔진 버전
ollama list                                          # 예: qwen2.5:14b  7cdf5a0187d5
```

## 7. 변경 관리

본 문서 수치는 v2.124.10 시점 코드·env 실측이다. 컨텍스트 상한·기본 파라미터·엔진
버전·캐시/큐 정책 변경은 릴리즈 노트(`docs/RELEASE_v2.*.md`)에 기록되며, 트윈 허가
자료에 인용된 항목의 변경은 **사전 통보 대상**이다. 문서 개정 시 상단 버전을 올리고
변경 이력에 기재한다. 가이드라인 [표8] 대조는 `mfds-llm-compliance-status.md` §5 참조.
