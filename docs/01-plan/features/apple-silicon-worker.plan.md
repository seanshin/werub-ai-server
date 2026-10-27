# Apple Silicon 특화 워커 — iMac M1 워크로드 전담 이관

> **Feature**: apple-silicon-worker
> **Created**: 2026-06-07
> **Version Target**: v2.80.0 ~ v2.83.0 (Phase별 릴리즈)
> **Priority**: P2 (High — GPU 서버 부하 분산 + Apple Silicon 가속 활용)
> **Target HW**: iMac M1 16GB (Unified Memory)

## 1. 목적

iMac M1을 단순 "경량 LLM CPU 오프로드" 노드에서 **Apple Silicon이 구조적으로 유리한
워크로드를 전담하는 특화 워커**로 격상한다. GPU 서버(RTX 5080)는 LLM 추론·이미지 생성에
집중하고, 다음 작업을 iMac으로 이관한다:

1. **AI 생성물 판별 ML + C2PA 마킹** (현재 GPU 서버 CPU 추론 → torch 스레드 캡 이슈 이력)
2. **STT (Meeting Analysis Whisper)** (현재 GPU 서버 VRAM/CUDA 점유 → LLM과 경합)
3. **임베딩/RAG 배치 색인** (qwen3-embedding:8b 주 / bge-m3 폴백, 야간 배치)

**원칙: 기존 GPU 버전은 크게 변경하지 않는다.** GPU 서버 측 변경은 "위임 + 폴백" 라우팅
설정 추가에 한정하며, iMac 미연결 시 기존 동작과 100% 동일하게 폴백한다.

## 2. 현황 분석

### 2-1. 현재 구조

```
GPU Server (RTX 5080, Ubuntu)                iMac M1 16GB (macOS)
├─ Flask 543+ API (메인)                     ├─ Ollama (3b/2b 경량 모델 오프로드)
├─ Ollama 34 models (CUDA)                   │   └─ 터널 헬스체크 30초 캐시 라우팅
├─ ComfyUI 이미지 생성                        ├─ BATCH_MODE Flask (배치 전용, 수동)
├─ faster-whisper STT (CUDA)        SSH 터널  └─ stats agent (모니터링 패널)
├─ AI 판별 ML (CPU, ~0.1s)         <───────>
├─ C2PA 서명 (CPU)
└─ qwen3-embedding:8b 임베딩 (CUDA, bge-m3 폴백)
```

### 2-2. 기존 기록 (git/docs)

| 기록 | 내용 |
|------|------|
| v2.15.0 | iMac offload + GPU scheduler — 3b/2b 자동 라우팅, `/api/imac/status` |
| `371a8e0` | macOS 이미지 생성 비활성화 |
| `c44dc40` | iMac(macOS) 운영 가이드 (README) — systemctl→launchctl 등 수동 변경 항목 |
| `dbb3fb2` | 멀티서버 모니터링 — iMac 패널 + stats agent |
| v2.78.1 | AI 판별 ML torch 스레드 캡 (GPU 서버에서 nlwp 폭증 인시던트) |

### 2-3. 제약 사항 (코드 레벨 확인)

| 모듈 | 현재 | M칩셋 문제 |
|------|------|-----------|
| `core/meeting_stt.py` | faster-whisper, `WHISPER_DEVICE=cuda` | **CTranslate2는 Metal 미지원** → macOS에서 CPU로만 동작 (large-v3 실용 불가) |
| `core/ai_image_detector.py` | HF pipeline, `device` cuda/cpu만 분기 (`dev = 0 if cuda else -1`) | MPS(`device="mps"`) 미지원 |
| `core/content_marking.py` | Pillow 워터마크 + PNG iTXt | 폰트 후보에 mac 경로 이미 포함 — **호환 OK** |
| `core/c2pa_sign.py` | c2pa 서명 (순수 CPU) | 플랫폼 독립 — **호환 OK** |
| Ollama (iMac) | "CPU 오프로드"로 문서화 | 실제 macOS Ollama는 Metal 기본 가속 — **검증/튜닝 기록 없음** |
| 운영 | launchd 수동, watchdog 없음 | sleep/thermal/메모리압박 대응 부재 |

### 2-4. Apple Silicon M1의 구조적 강점 (이관 근거)

- **Unified Memory 16GB**: CPU↔GPU 메모리 복사 없음 — 모델 로드/전환 빠름
- **Metal GPU (7~8코어)**: Ollama 기본 지원 — 실측 3b 22.4 / 7b 11.1 tok/s (§7-0)
- **ANE (Neural Engine, 11 TOPS)**: whisper.cpp CoreML 인코더 ~3배 가속, ViT 분류기 가속
- **MLX**: Apple 공식 ML 프레임워크 — mlx-whisper 등 Apple Silicon 네이티브
- **전력 효율**: 상시 가동 배치 노드로 적합 (GPU 서버 대비 유휴 전력 1/10 수준)

## 3. 목표 아키텍처

```
GPU Server (변경 최소)                        iMac M1 16GB — Apple Silicon Worker
├─ Flask 메인 (위임 라우팅 + 폴백만 추가)      ├─ WORKER_MODE Flask :8587 (신규, 경량)
├─ Ollama (메인 LLM, 변경 없음)               │   ├─ POST /worker/detect/image   (판별 ML, MPS/CoreML)
├─ ComfyUI (변경 없음)                        │   ├─ POST /worker/mark/image     (워터마크+C2PA)
│                                  SSH 터널   │   ├─ POST /worker/stt            (whisper.cpp CoreML)
│  meeting_pipeline ──STT 위임──── <───────>  │   ├─ POST /worker/embed          (qwen3-embedding/bge-m3)
│  detect/image    ──판별 위임────            │   └─ GET  /worker/health         (메모리/thermal 포함)
│  (iMac down → 기존 로컬 처리 폴백)           ├─ Ollama Metal :11434 (경량 LLM 오프로드 — 기존 유지)
│                                             └─ 운영 자동화: launchd + watchdog + caffeinate
```

- 신규 포트 **:8587** — ⚠️ **:8586은 사용 불가**: GPU 서버에서 iMac Ollama 터널 포트로 이미 점유 중 (`core/config.py:10` `IMAC_OLLAMA_BASE = http://localhost:8586`)
- **터널 방향**: iMac이 GPU 서버로 SSH **reverse tunnel** 개시 (기존 iMac Ollama 8586 터널과 동일 방식) — GPU 서버 `localhost:8587` → iMac `127.0.0.1:8587`. 기존 `setup/com.werub.*-tunnel.plist` 패턴 재사용
- **워커 인증**: 워커는 127.0.0.1 바인딩(터널 전용)이지만 심층 방어로 `X-Worker-Token` 공유 시크릿 헤더 검증 추가 (GPU 서버 config + iMac env 양쪽 보관)
- 워커는 **무상태(stateless)**: 큐/DB 없음, 동기 처리 + 짧은 작업만 (STT는 job 콜백)
- GPU 서버는 `config.json`의 `apple_worker_url` 설정 유무로만 동작 분기

### 3-1. 활용 모드 — 기능 소비 경로 2가지를 모두 지원 (사용자 요구사항)

| 모드 | 호출 주체 | 경로 | 인증 |
|------|----------|------|------|
| **A. 위임 모드** | GPU 서버 | SSH 터널 (GPU `localhost:8587` → iMac `127.0.0.1:8587`) | `X-Worker-Token` (공유 시크릿) |
| **B. 독립 서버 모드** | 외부 클라이언트 (메인웹/ERP/내부망 도구) | ① LAN 직접 (iMac IP:8587, 바인딩 확장 시) ② 공인 노출 (prod nginx `example.org/imac-ai/*` → reverse tunnel → iMac:8587 — 기존 :8585 admin 노출과 동일 패턴) | **Bearer API 키** (기존 `core/security` 재사용) |

- 두 모드는 **동시 활성 가능** — 요청 헤더로 구분 (X-Worker-Token이면 내부 위임, Bearer면 외부 직접)
- 독립 모드는 `worker_expose` config로 opt-in (기본 OFF — Phase 1은 위임 모드만, 독립 노출은 Phase 1 검증 후 활성화)
- 기존 iMac BATCH_MODE(:8585, 큐 기반 배치)는 그대로 유지 — 워커(:8587, 동기 특화 기능)와 상호 보완

## 4. 구현 범위

### Phase 0: M1 기반 정비 (v2.80.0) — 선행 필수

> 목표: "CPU 노드"라는 잘못된 전제 해소 + 운영 신뢰성 확보

| # | 항목 | 내용 |
|---|------|------|
| 0-1 | Metal 가속 검증 | ✅ **완료 (2026-06-07)** — 전 모델 100% GPU 확인, 3b 22.4 / 7b 10.9 tok/s, bge-m3 5 docs/s(500자 청크), 동시 요청 직렬화 발견(NUM_PARALLEL 미설정). 상세: `docs/IMAC_M1_BENCHMARK.md` |
| 0-2 | Ollama 튜닝 | ✅ **완료 (2026-06-07)** — `setup/com.werub.ollama.plist` 설치 (brew services 대체), FLASH_ATTENTION=1·NUM_PARALLEL=2·MAX_LOADED_MODELS=2·KEEP_ALIVE=30m. 동시 요청 직렬화 해소 검증 (벤치마크 §6-1) |
| 0-3 | launchd 자동화 | ✅ **완료 (2026-06-07)** — ollama/caffeinate/watchdog/worker plist 4종 + `scripts/deploy_imac.sh --setup` (토큰 생성·치환·일괄 설치). 터널 plist는 GPU 서버 위임 활성화 시점에 추가 |
| 0-4 | sleep/절전 방지 | ✅ **완료 (2026-06-07)** — `setup/com.werub.caffeinate.plist` (`caffeinate -ims`, 디스플레이는 잠자기 허용). 발견: pmset `sleep=1`(유휴 1분 잠자기)이 터널 끊김 근본 원인이었음. KeepAlive 자가복구 검증 완료 |
| 0-5 | watchdog macOS판 | ✅ **완료 (2026-06-07)** — `scripts/watchdog_mac.sh` + `setup/com.werub.watchdog.plist` (launchd StartInterval 300 — macOS 표준, cron 대체). 감시 5항목: ollama/워커(조건부)/터널(조건부)/caffeinate/자원경고. launchctl kickstart 복구 |
| 0-6 | 모니터링 보강 | ✅ **완료 (2026-06-07)** — `scripts/server-stats.py` darwin 분기 추가 (Linux 경로 불변): CPU/RAM/uptime/ps macOS 대응 + `apple_silicon` 블록(chip/memory_pressure/thermal/swap). 이 iMac에서 실측 검증. ⚠️ 검증 중 디스크 94.4% 사용 발견 — 정리 필요 |
| 0-7 | 문서 정정 | ✅ **완료 (2026-06-07)** — ARCHITECTURE.md·README "iMac CPU" → "Metal" + 실측 수치 병기 |
| 0-8 | macOS 의존성 분리 | ✅ **완료 (2026-06-07)** — `requirements-mac.txt` (GPU 핀 동일: torch 2.10.0/transformers 5.5.0/c2pa 0.32.12) + `venv-mac` 구성, MPS True 확인 |
| 0-9 | 배포 절차 | ✅ **완료 (2026-06-07)** — `scripts/deploy_imac.sh` (--setup 최초/기본 git pull+재기동), `pytest.ini` macos 마커 등록, `tests/test_worker.py` 12건 |

### Phase 1: AI 판별 + 마킹 워커 (v2.81.0) — 가장 가벼운 이관부터

> 목표: v2.77~2.79에서 구축한 AI 마킹/판별을 iMac에서 처리 가능하게 (GPU 서버 torch 스레드 이슈 원천 분리)

| # | 항목 | 내용 |
|---|------|------|
| 1-1 | MPS 지원 | `core/ai_image_detector.py` — `device="mps"` 분기 추가 (HF pipeline `device="mps"`), macOS 자동 감지 |
| 1-2 | 워커 모드 신설 | `WORKER_MODE=1` 환경변수 → 워커 API 4종만 노출하는 경량 Flask 프로파일 (`gunicorn_worker.conf.py`, BATCH_MODE 패턴 재사용) |
| 1-3 | `/worker/detect/image` | 기존 `core/ai_image_detector.classify()` 래핑 — 입력: 이미지 bytes, 출력: 기존 detect API와 동일 스키마 |
| 1-4 | `/worker/mark/image` | `content_marking` + `c2pa_sign` 래핑 (인증서는 iMac 로컬 배치, `scripts/gen_c2pa_cert.sh` 재사용) |
| 1-5 | GPU 서버 위임 | `routes/`의 detect 핸들러에 위임 로직: `apple_worker_url` 설정 시 iMac 호출(타임아웃 5s) → 실패 시 기존 로컬 처리 폴백. 기존 3b/2b 오프로드의 헬스체크 30초 캐시 패턴 재사용 |
| 1-6 | config | `config.json`: `apple_worker_enabled`, `apple_worker_url`, `apple_worker_delegate` (`["detect"]` 형태로 기능별 토글) |

### Phase 2: STT 이관 (v2.82.0) — 효과 최대 항목

> 목표: Meeting Analysis STT를 iMac ANE/Metal에서 처리 → GPU 서버 VRAM/CUDA 경합 해소

| # | 항목 | 내용 |
|---|------|------|
| 2-1 | 백엔드 선정 PoC | ✅ **완료 (2026-06-07) — mlx-whisper 선정**. large-v3 동일 조건: 품질 완전 동일(CER 2.8%, 출력 일치), mlx RTF 0.34 vs whisper.cpp CLI 모델 재로드 부담. in-process 상주가 워커 구조와 정합. 상세: `IMAC_M1_BENCHMARK.md` §6-3. 실 회의 녹음 재검증·ffmpeg 요건은 2-2 착수 시 |
| 2-2 | STT 추상화 | `core/meeting_stt.py`에 백엔드 인터페이스 분리: `faster-whisper`(GPU 서버, 기존 그대로) / 선정 백엔드(iMac). 기존 30분 청크 분할 로직 공유 |
| 2-3 | `/worker/stt` | 비동기 작업형: 오디오 수신(또는 S3 URL) → 처리 → callback. 긴 작업이므로 동기 응답 대신 job_id + 콜백 |
| 2-4 | 파이프라인 위임 | `core/meeting_pipeline.py` STT 단계에서 `apple_worker_delegate`에 "stt" 포함 시 iMac 위임, 실패/타임아웃 시 기존 faster-whisper 폴백 |
| 2-5 | 화자 분리 검토 | SpeechBrain ECAPA-TDNN의 MPS 동작 검증 — 안 되면 화자 분리만 GPU 서버 잔류 (STT 텍스트+타임스탬프만 iMac) |

### Phase 3: 임베딩/RAG 배치 이관 (v2.83.0)

> ⚠️ **임베딩 모델 정합 제약**: 현행 RAG 주 임베딩은 **qwen3-embedding:8b (4096d, v2.62 업그레이드)**이고 bge-m3(1024d)는 폴백이다.
> **같은 ChromaDB 컬렉션은 반드시 단일 모델로만 색인**해야 하므로(차원/벡터공간 비호환), iMac 위임 시에도
> GPU와 동일 모델을 사용한다. 컬렉션별 사용 모델을 config에 고정(`collection_embed_model` 맵)하고 워커가 검증한다.

| # | 항목 | 내용 |
|---|------|------|
| 3-1 | `/worker/embed` | iMac Ollama 임베딩 래핑 — 요청에 `model` 명시 필수(qwen3-embedding:8b ~4.7GB 또는 bge-m3 1.1GB), 배치 입력 지원 |
| 3-2 | 야간 색인 위임 | ChromaDB 재색인·대량 임베딩 작업(예: `reindex_bge_m3.py`)을 iMac 임베딩으로 수행하는 옵션 — 컬렉션-모델 맵 검증 후 실행 |
| 3-3 | Drug Night Mode 연계 | 약물 배치 파이프라인의 임베딩 단계 iMac 위임 검토 (효과 측정 후 결정) |
| 3-4 | **ERP 공시 RAG 색인 연계** | `erp-notice-rag.plan.md`의 공시 색인 잡(notice_index_jobs)을 `/worker/embed`로 처리 — 의료법/삭감공지 야간 색인의 주 소비처. 해당 기능은 GPU 폴백으로 선출시 가능하므로 일정 독립 |

## 5. 메모리 전략 (M1 16GB Unified)

상주 조합이 16GB를 넘지 않도록 **워크로드 상호배제** 운영:

| 구성 | 메모리 | 비고 |
|------|--------|------|
| macOS + Flask 워커 기본 | ~4GB | 상시 |
| 경량 LLM 오프로드 (qwen2.5:3b) | ~2GB | 상시 (기존 역할 유지) |
| whisper large-v3 (CoreML/MLX) | ~3.5GB | STT 작업 시 로드, 완료 후 해제 |
| AI 판별 ML (ViT) | ~1GB | 상주 (가벼움, lazy load) |
| qwen3-embedding:8b (Q4) | ~4.7GB | 배치 시 로드 — **STT와 상호배제** |
| **피크 합계 (시나리오별)** | STT+판별 ~10.5GB / 임베딩+판별 ~11.7GB | 여유 ≥ 4GB (swap 회피선) |

- 규칙 1: **STT와 임베딩 배치 동시 실행 금지** — 워커가 작업 상호배제 락 관리
- 규칙 2: **무거운 작업(STT/임베딩) 중에는 7b급 LLM 로드 금지** (3b까지만) — 메모리 가드 체크
- `memory_pressure` 경고 단계 진입 시 신규 작업 거부(503) + 모니터링 알림

## 6. GPU 서버 측 변경 (최소화 원칙)

| 파일 | 변경 | 폴백 |
|------|------|------|
| `config.json` | `apple_worker_*` 키 3개 추가 | 미설정 시 기존 동작 100% 동일 |
| detect 핸들러 | 위임 분기 ~20줄 | 타임아웃/오류 → 로컬 처리 |
| `core/meeting_pipeline.py` | STT 위임 분기 ~30줄 | 타임아웃/오류 → faster-whisper |
| 임베딩 호출부 | 배치 색인 스크립트 옵션 플래그 | 기본값 기존 동작 |

**변경하지 않는 것**: Ollama 메인 LLM 서빙, ComfyUI 이미지 생성, 543+ API 스키마,
인증 체계, 큐 엔진, SSE 처리.

## 7. 검증 계획 (Zero Script QA + 회귀)

### 7-0. Phase 0-1 실측 결과 (2026-06-07, 대상 iMac M1 16GB / Ollama 0.23.4 / env 미튜닝)

| 항목 | 실측 | 판정 |
|------|------|------|
| Metal 가속 | **3모델 동시 적재 전부 100% GPU** (`ollama ps`) — "CPU 노드" 전제는 오류로 확정 | ✅ |
| qwen2.5:3b 생성 | **22.4 tok/s** (웜), prompt eval 1,063 tok/s | ✅ 오프로드 실용 |
| qwen2.5:7b / coder:7b 생성 | **10.9 / 11.1 tok/s** (웜, 100% GPU) | △ M1 메모리 대역폭(68GB/s) 한계 — 7b는 배치 전용 권장 |
| bge-m3 임베딩 | 17.5 docs/s (135자) / **5.0 docs/s (500자 실전 청크)** | ✅ 야간 색인 충분 (공시 1건 ~2초) |
| 동시 요청 | 3b 동시 2건 → **직렬 큐잉** (wall 2배) | ⚠️ NUM_PARALLEL 미설정 — Phase 0-2 튜닝 필수 근거 |
| 동시 적재 | 3b+7b+bge-m3 = 8.3GB 동시 상주 정상 | ✅ §5 메모리 전략 여유 확인 |
| OLLAMA env | FLASH_ATTENTION 등 전부 미설정 | Phase 0-2 튜닝 여지 |

> 초안의 통과 기준(3b ≥ 40, 7b ≥ 20 tok/s)은 M1 Pro/Max급 수치로 과대 설정 — 실측 기반으로 하향 정정.

| 항목 | 방법 | 통과 기준 |
|------|------|----------|
| Metal 가속 | `ollama ps` GPU% + tok/s 벤치 | 3b ≥ 20 tok/s, 7b ≥ 10 tok/s (✅ 실측 통과) |
| 판별 정확도 동일성 | 동일 이미지 100장 GPU CPU vs iMac MPS 결과 비교 | ai_probability 편차 < 1% |
| STT 품질 | 기존 회의 녹음 3건 — faster-whisper(CUDA) vs iMac 백엔드 WER 비교 | 한국어 WER 열화 ≤ 2%p |
| STT 속도 | 60분 오디오 처리 시간 | ≤ 실시간의 1/3 (20분 이내) |
| 폴백 동작 | iMac 터널 차단 상태에서 detect/STT 호출 | 기존 경로로 자동 처리, 오류 0 |
| 메모리 가드 | STT 중 LLM 동시 요청 | swap 미발생, 503 가드 동작 |
| 회귀 | 기존 157건 + 워커 신규 테스트 | 전체 통과 (워커 모듈은 macOS 마커로 분리) |
| 24h 안정성 | watchdog 로그 + stats agent | 무중단, sleep/터널 끊김 0회 |

## 8. 공수 산정

| Phase | 내용 | 공수 |
|-------|------|------|
| Phase 0 | 기반 정비 (검증+launchd+watchdog+모니터링) | 1.5일 |
| Phase 1 | 판별/마킹 워커 + 위임 라우팅 | 2일 |
| Phase 2 | STT PoC + 이관 + 폴백 | 3일 (PoC 1일 포함) |
| Phase 3 | 임베딩 배치 이관 | 1일 |
| 검증/문서 | QA + 릴리즈 노트 + ARCHITECTURE 갱신 | 1일 |
| **합계** | | **8.5일** |

## 9. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| CTranslate2 Metal 미지원 (faster-whisper 재사용 불가) | STT 백엔드 이원화로 유지보수 증가 | 백엔드 인터페이스 추상화(2-2)로 격리, GPU 서버 쪽은 무변경 |
| whisper.cpp/MLX 한국어 정확도 열화 | Meeting 요약 품질 저하 | PoC에서 WER 기준 미달 시 STT 이관 보류(Phase 2 중단 가능 — Phase 1/3과 독립) |
| 16GB 메모리 경합 (STT+LLM 동시) | swap → 전체 성능 붕괴 | 메모리 가드(§5) + memory_pressure 503 |
| SSH 터널 단일 장애점 | 위임 실패 빈발 | 모든 위임에 폴백 필수 + autossh/launchd KeepAlive + watchdog |
| MPS 연산 결과 미세 차이 (판별 확률) | confidence 등급 흔들림 | 동일성 검증(§7) + 등급 경계값 여유 확인 |
| iMac sleep/절전 | 야간 배치 실패 | Phase 0-4 caffeinate/pmset 선행 |
| HF pipeline MPS 미성숙 연산 폴백 | 판별 속도 저하 | `PYTORCH_ENABLE_MPS_FALLBACK=1` + 속도 기준 미달 시 CoreML 변환(후속 과제) |
| 임베딩 모델 불일치 색인 (iMac↔GPU 다른 모델) | **벡터 비호환 → 검색 결과 오염** | 컬렉션-모델 맵 config 고정 + 워커가 요청 모델 검증·거부 (§Phase 3 제약) |
| qwen3-embedding:8b의 M1 처리량 부족 | 야간 배치 시간 초과 | Phase 3 착수 시 벤치마크 선행 — 기준 미달이면 해당 컬렉션만 bge-m3 유지 또는 임베딩 이관 보류 |

## 10. 제외 범위 (이번 기획에서 다루지 않음)

- **macOS 이미지 생성 재활성화** (MLX/mflux) — 보류. 이미지 생성은 GPU 서버 ComfyUI 전담 유지 (`371a8e0` 비활성화 유지)
- GPU 서버 Failover(iMac 단독 전체 API 서빙) — 별도 기획
- iMac 멀티 노드 (현재 1대 기준)
- QLoRA 학습 파이프라인의 MPS 이관 — Unsloth Metal 성숙도 재평가 후

## 11. 의사결정 기록

| 결정 | 선택 | 근거 |
|------|------|------|
| iMac 역할 방향 | 특화 워크로드 전담 | Apple Silicon 강점(ANE/Metal/전력)과 GPU 서버 경합 해소의 교집합 |
| 대상 하드웨어 | M1 16GB | 현재 보유 장비, 메모리 전략 §5로 커버 |
| 이미지 생성 | 제외, 판별/마킹만 | 생성은 ComfyUI 품질/생태계 우위, 판별·마킹은 경량이라 이관 적합 |
| 워커 포트 | :8587 신규 | :8585(Admin)·:8586(iMac Ollama 터널, `core/config.py:10`)이 점유 중 — 검토에서 8586 충돌 발견 후 정정 |
| GPU 서버 변경 | 위임+폴백 한정 | 사용자 요구사항 — "기존 GPU 버전은 크게 변경할 필요 없음" |
| 임베딩 모델 | 컬렉션별 GPU와 동일 모델 강제 | 현행 주 임베딩이 qwen3-embedding:8b(4096d)로 업그레이드된 사실 반영 — 혼합 색인 시 벡터 비호환 |
| 활용 모드 | 위임(GPU 호출) + 독립 서버(직접 응답) 이중 지원 | 사용자 요구사항 (2026-06-07) — 헤더 기반 인증 분리(X-Worker-Token/Bearer), 독립 노출은 opt-in |

## 12. 검토 이력

| 일자 | 검토 결과 |
|------|----------|
| 2026-06-07 | 코드베이스 대조 검토 — ① 워커 포트 8586→8587 정정 (IMAC_OLLAMA_BASE 충돌) ② 임베딩 현행화 (bge-m3→qwen3-embedding:8b 주모델, 컬렉션-모델 정합 제약 신설) ③ X-Worker-Token 인증 추가 ④ 터널 방향(iMac발 reverse) 명시 ⑤ Phase 0에 requirements-mac/배포/테스트 마커 추가 ⑥ 메모리 전략에 STT↔임베딩 상호배제 반영 |
| 2026-06-07 (2차) | **Phase 0-1 실측 완료** (대상 iMac에서 직접 측정) — Metal 100% GPU 확정, 3b 22.4 / 7b 11.1 tok/s, bge-m3 17.5 docs/s, 3모델 동시 적재 8.3GB 정상. 통과 기준을 실측 기반으로 정정(3b ≥20, 7b ≥10). 7b는 실시간보다 배치 전용 권장으로 조정 |
