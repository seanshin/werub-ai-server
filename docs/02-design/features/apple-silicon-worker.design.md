# Apple Silicon 특화 워커 (iMac M1) — Design

> **Feature**: apple-silicon-worker | **Version Target**: v2.80.0~v2.83.0 | **Created**: 2026-06-07
> **Plan**: `docs/01-plan/features/apple-silicon-worker.plan.md` (Phase 0-1 실측 완료 — Metal 100% GPU, 3b 22.4 / 7b 11.1 tok/s, bge-m3 17.5 docs/s)

## 1. 컴포넌트 구조

```
[iMac 신규]
  worker_app.py                  ← WORKER_MODE 전용 Flask 엔트리 (app.py와 분리된 경량 앱)
  gunicorn_worker.conf.py        ← 워커 gunicorn 설정 (gthread 8, :8587 바인딩 127.0.0.1)
  core/worker_guard.py           ← 메모리 가드 + 작업 상호배제 락 + thermal 체크
  core/stt_backend.py            ← STT 백엔드 추상화 (faster_whisper | whispercpp_coreml | mlx_whisper)
  requirements-mac.txt           ← macOS 전용 의존성
  setup/imac_setup.sh            ← launchd plist 3종 설치 (worker/tunnel/ollama)
  setup/com.werub.worker.plist   ← 워커 launchd (caffeinate -dims 래핑)
  setup/com.werub.worker-tunnel.plist ← reverse tunnel: GPU:8587 → iMac:8587
  scripts/watchdog_mac.sh        ← macOS watchdog (launchctl 재기동 + 터널 생존)
  scripts/deploy_imac.sh         ← git pull + 재기동

[GPU 서버 변경 — 위임+폴백 한정]
  core/config.py                 ← APPLE_WORKER_* 키 로드
  core/apple_worker.py           ← 위임 클라이언트 (헬스 캐시 30s + 폴백, 신규 ~80줄)
  routes/image.py(detect 핸들러) ← 판별 위임 분기 ~20줄
  core/meeting_pipeline.py       ← STT 위임 분기 ~30줄
```

## 2. 활용 모드 (소비 경로 — 2모드 동시 지원)

```
[모드 A — 위임]                              [모드 B — 독립 서버]
GPU 서버 Flask                                메인웹 / ERP / 내부망 도구
  └─ core/apple_worker.py                       │ Bearer sk-... (기존 API 키 체계)
      │ X-Worker-Token                          ├─ ① LAN: http://<iMac-IP>:8587
      ▼ SSH 터널                                ├─ ② 공인: https://example.org/imac-ai/*
GPU localhost:8587 ──────> iMac :8587 <─────────┘    (prod nginx → reverse tunnel,
                           worker_app.py              기존 :8585 admin 노출 패턴 재사용)
```

| 항목 | 모드 A (위임) | 모드 B (독립) |
|------|--------------|--------------|
| 인증 | `X-Worker-Token` 공유 시크릿 | `Authorization: Bearer` — `core/security.require_auth` 재사용 (키 발급/만료/토글 기존 체계) |
| 바인딩 | 127.0.0.1 (터널 전용) | `worker_expose=lan`이면 0.0.0.0, `tunnel`이면 127.0.0.1 유지 + prod nginx 라우트 |
| rate limit | 없음 (내부) | 기존 IP 기반 분당 120회 적용 |
| 활성화 | 기본 ON | **기본 OFF** — config `worker_expose: "off"|"lan"|"tunnel"` opt-in (Phase 1 검증 후) |
| 응답 스키마 | 동일 (모드 무관) | 동일 |

- 인증 분기: 요청에 `X-Worker-Token` 있으면 내부 위임으로 검증, 없으면 Bearer 검증 — `worker_app.py`의 `@worker_auth` 데코레이터 하나로 처리
- 독립 모드에서도 worker_guard(메모리/락/thermal 503)는 동일 적용 — 외부 호출이 위임 호출을 굶기지 않도록 heavy 작업은 공평 큐 없이 단순 선착순(락) 유지, 503 시 클라이언트 재시도 책임
- 기존 BATCH_MODE(:8585)는 큐 기반 비동기 배치용으로 그대로 유지 — 워커(:8587)는 동기 특화 기능 전용, 역할 중복 없음

## 2-1. 워커 API 명세 (:8587, `@worker_auth` — 모드 A: X-Worker-Token / 모드 B: Bearer)

### GET /worker/health
```json
Response: {"ok": true, "version": "2.81.0-phase1", "platform": "arm64",
  "memory": {"free_gb": 12.0, "free_pct": 75.0, "pressure": "normal"},
  "thermal": "nominal",                                    // pmset -g therm
  "locks": {"heavy_task": null},                           // 현재 무거운 작업 (stt|embed|null)
  "capabilities": ["detect", "mark"],                      // Phase별 활성 기능
  "detector": {"device": "mps", "available": true},
  "c2pa": false, "expose": "off"}
// models_loaded(Ollama ps 연동)는 Phase 3 embed 추가 시 도입
```

### POST /worker/detect/image  (Phase 1)
```json
Request:  multipart image | {"image_b64": "..."}  (≤10MB)
Response: {"ai_probability": 0.97, "label": "artificial", "model": "Organika/sdxl-detector",
           "device": "mps", "duration_ms": 95}
```
- GPU 서버 `/api/detect/image`의 ML 단계와 **동일 스키마** — 호출측 코드 변경 없이 위임 가능

### POST /worker/mark/image  (Phase 1)
```json
Request:  {"image_b64": "...", "c2pa": true}
Response: {"image_b64": "...", "marked": true, "c2pa_signed": true, "duration_ms": 180}
```
- 워터마크(`content_marking`) + C2PA(`c2pa_sign`) — 인증서는 iMac 로컬 `config/c2pa/` (gen_c2pa_cert.sh)
- ⚠️ **알려진 한계 (2026-06-07 확인)**: macOS에서 c2pa 실서명이 조용히 실패(passthrough — LibreSSL/플랫폼, v2.79 "실서명은 GPU" 전제와 일치). 워커는 `c2pa_signed:false`로 정직 보고 — **iMac mark는 워터마크+iTXt까지, C2PA 서명은 GPU 전담 유지**. 해소 시도(brew openssl 인증서 생성 등)는 후속 과제

### POST /worker/stt  (Phase 2 — 비동기)
```json
Request:  {"audio_url": "https://s3.wasabisys.com/...", "language": "ko",
           "callback_url": "http://localhost:8585/api/meeting/internal/stt-callback"}
Response 202: {"stt_job_id": "stt-3fa2", "estimated_minutes": 18}
```
- iMac이 S3에서 직접 다운로드(터널 경유 대용량 전송 회피), 완료 시 GPU 서버로 콜백
  (GPU 서버는 인바운드 가능 — ERP와 달리 터널 내부 통신이므로 SSRF 면제 경로 아님: localhost 콜백만 허용)
- 메모리 가드: heavy_task 락 획득 실패 시 503 {"retry_after": 600} → GPU 폴백 트리거
- **모드 B(독립) 호출 시**: callback_url 생략 가능 → `GET /worker/stt/{stt_job_id}` 폴링으로 결과 수신
  (인메모리 잡 테이블, 완료 후 1h 보관 — 워커 무상태 원칙의 유일한 예외, 재시작 시 소실 허용)

### POST /worker/embed  (Phase 3)
```json
Request:  {"model": "qwen3-embedding:8b", "input": ["...", "..."], "collection": "notice_knowledge"}
Response: {"embeddings": [[...]], "model": "qwen3-embedding:8b", "duration_ms": 850}
```
- `collection` 지정 시 컬렉션-모델 맵(`config`) 대조 — 불일치 시 409 거부 (벡터 오염 차단)
- 내부적으로 iMac Ollama(:11434) `/api/embed` 프록시 + 가드 적용

## 3. GPU 서버 위임 클라이언트 (core/apple_worker.py)

```python
# 함수형 모듈 (구현 확정형 — 클래스 불필요, config는 호출 시점에 load_config로 동적 로드)
core/apple_worker.py:
    available(capability: str) -> bool      # 4중 활성조건 + 헬스 캐시 30s
    detect(image_bytes: bytes) -> dict|None # None = 호출측 로컬 폴백, engine="imac" 부착
    # Phase 2/3에서 추가: stt(audio_url, callback_url), embed(model, texts, collection)
```

- **폴백 계약**: 타임아웃(detect 5s / embed 30s / stt 접수 10s)·5xx·토큰 오류 → `None` 반환,
  호출측은 `None`이면 기존 로컬 경로 실행. **예외를 위로 던지지 않음** (기존 동작 보존 원칙)
- **Darwin 가드**: `platform.system()=="Darwin"`이면 config가 있어도 위임 불가 — iMac 자기 위임 원천 차단

### 3-1. GPU 서버 활성화 절차 (구현 완료 — 배포 시 수행)

> 코드는 양쪽에 배포돼도 **config 키를 넣기 전까지 완전 휴면** (test_config_absent_returns_none로 보증)

1. iMac에서 reverse tunnel 가동: `ssh -N -R 8587:127.0.0.1:8587 deploy@GPU서버 -p 819` (launchd plist화는 운영 확정 시)
2. GPU 서버 `config.json`에 추가:
```json
"apple_worker_enabled": true,
"apple_worker_url": "http://localhost:8587",
"apple_worker_token": "<iMac ~/.werub_worker_token 값>",
"apple_worker_delegate": ["detect"]
```
3. 확인: `/api/detect/image` 응답의 `ml.engine == "imac"` → 위임 동작. 터널 차단 후 재호출 → engine 없음(로컬) = 폴백 정상
4. 끄기: `apple_worker_enabled: false` 또는 키 삭제 (재배포 불필요, config만)
- 호출측 분기 패턴 (detect 예):
```python
result = apple_worker.detect(img) if apple_worker.available("detect") else None
if result is None:
    result = ai_image_detector.classify(img, device=DETECTOR_DEVICE)  # 기존 경로
```

## 4. MPS 지원 (core/ai_image_detector.py 수정)

```python
def _resolve_device(device: str) -> str | int:
    if device == "mps" and torch.backends.mps.is_available(): return "mps"
    if device == "cuda": return 0
    return -1   # cpu (기존 동작)
```
- `_load()`의 `dev = 0 if device == "cuda" else -1` 분기를 위 함수로 교체 — **GPU 서버 기본값(cpu) 불변**
- iMac 워커 env: `DETECTOR_DEVICE=mps`, `PYTORCH_ENABLE_MPS_FALLBACK=1`
- 동일성 검증: 샘플 100장 cpu vs mps `ai_probability` 편차 < 1% (회귀 테스트, macos 마커)

## 5. STT 백엔드 추상화 (core/stt_backend.py — Phase 2)

```python
class STTBackend(Protocol):
    def transcribe(self, wav_path: str, language: str) -> list[Segment]  # 기존 청크 분할 로직 공유

get_backend() -> STTBackend
  # env WHISPER_BACKEND: faster_whisper(기본, GPU 서버 기존 그대로)
  #                    | whispercpp_coreml | mlx_whisper  ← PoC 후 1종 선정
```
- `core/meeting_stt.py`는 `get_backend()` 호출로만 변경 — faster-whisper 경로는 바이트 단위 동일 유지
- PoC 판정 기준(Plan 2-1): 한국어 WER 열화 ≤ 2%p AND 60분 오디오 ≤ 20분 처리

## 6. 워커 가드 (core/worker_guard.py)

```python
heavy_lock = threading.Lock()                  # stt ↔ embed 상호배제
def acquire_heavy(task: str) -> bool           # 락 + free_mem ≥ 6GB 확인
def memory_state() -> dict                     # memory_pressure 명령 파싱 (normal/warn/critical)
def thermal_state() -> str                     # pmset -g therm
@guard("heavy")                                 # 데코레이터: 실패 시 503 retry_after
```
- warn 이상이면 신규 heavy 작업 503, critical이면 detect/mark도 503 (폴백이 받아줌)

## 7. 운영 (launchd / 터널 / 감시)

| 구성 | 내용 |
|------|------|
| com.werub.worker.plist | gunicorn 직접 실행, KeepAlive, env(WORKER_TOKEN/EXPOSE/DETECTOR_DEVICE). **caffeinate는 별도 plist(com.werub.caffeinate, `-ims`)로 분리** — 워커 재기동과 절전방지 독립 (구현 시 개선) |
| com.werub.worker-tunnel.plist | `ssh -N -R 8587:127.0.0.1:8587 deploy@GPU -p 819` + ServerAliveInterval 30, KeepAlive |
| ollama env (plist) | `OLLAMA_FLASH_ATTENTION=1, NUM_PARALLEL=2, MAX_LOADED_MODELS=2, KEEP_ALIVE=30m` — 실측 기준 3모델 동시 8.3GB 확인했으나 STT 공존 위해 2로 제한 |
| watchdog_mac.sh | cron 5분: 워커 헬스 → launchctl kickstart, 터널 프로세스 → 재기동, 로그 `logs/watchdog_mac.log` |
| 모니터링 | stats agent가 `apple_silicon` 블록(chip/pressure/thermal/swap)을 **직접 수집** (구현 확정 — 네트워크 의존 제거). `/worker/health` HTTP 폴링 통합(locks/capabilities 노출)은 선택 과제 |
| 공인 노출 (모드 B-②, opt-in) | prod nginx에 `location /imac-ai/ → proxy_pass 터널포트` 라우트 추가 + reverse tunnel 8587 — 기존 `/ollama/`(:8585) 노출과 동일 패턴, GPU 서버 코드 변경 아님 (인프라 설정) |

## 8. 보안

- 기본 바인딩 **127.0.0.1** (모드 A 전용) — `worker_expose=lan` 설정 시에만 0.0.0.0 확장
- 모드 A: `X-Worker-Token` 64자 랜덤, GPU `config.json["apple_worker_token"]` ↔ iMac env `WORKER_TOKEN`. 불일치 401
- 모드 B: Bearer 검증은 워커가 **iMac 로컬 queue.db api_keys를 직접 조회** (`_verify_bearer_key` — SHA-256 key_hash + active + expires_at, app.py와 동일 스키마). app.py(9.7K줄) import 없이 경량 유지. 키 발급/만료 관리는 기존 Admin(:8585, localhost only)에서 — 갭 분석에서 발견된 결함(core.security에 verify_api_key 부재) 수정 완료 (2026-06-07)
- 워커는 색인/큐 로직 없음 — 공격 표면 최소 (이미지 바이트·텍스트·S3 URL만 수신)
- STT callback_url은 `localhost:8585` 프리픽스만 허용 (외부 URL 거부) — 모드 B에서는 STT 비동기 대신 동기 폴링(`GET /worker/stt/{job_id}`) 제공

## 9. 테스트 계획

| # | 테스트 | 위치/마커 |
|---|--------|----------|
| 1 | _resolve_device cpu/cuda/mps 분기 | tests/test_ai_image_detector.py 확장 (전 플랫폼) |
| 2 | AppleWorker 폴백 계약 (타임아웃/5xx/토큰오류 → None) | tests/test_apple_worker.py (mock, 전 플랫폼) |
| 3 | detect cpu vs mps 동일성 100장 | @pytest.mark.macos |
| 4 | worker_guard 상호배제/메모리 503 | @pytest.mark.macos |
| 5 | 해시·청크 등 기존 157건 회귀 | GPU venv 기존 그대로 (변경 없음 확인) |
| 6 | 24h 안정성 (watchdog 로그 무중단) | 운영 검증 (Phase 0 완료 기준) |

## 10. 구현 순서

| 단계 | 작업 | 공수 |
|------|------|------|
| Phase 0 잔여 | launchd 3종 + caffeinate + watchdog_mac + requirements-mac + 모니터링 (0-1 실측은 완료) | 1일 |
| Phase 1 | worker_app + guard + detect/mark API + MPS 분기 + AppleWorker + detect 위임 | 2일 |
| Phase 2 | STT PoC(whisper.cpp vs mlx) → stt_backend + /worker/stt + meeting_pipeline 위임 | 3일 |
| Phase 3 | /worker/embed + 컬렉션-모델 맵 + reindex 옵션 + erp-notice-rag 색인 연계 | 1일 |
