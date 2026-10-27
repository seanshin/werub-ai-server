# Release v2.115.0 — NVIDIA SANA 무스왑 이미지 fast-lane (Phase 1)

## 헤드라인
현 이미지 스택(ComfyUI: SDXL 6.5G/Flux 17G)은 생성 때마다 상주 LLM(qwen2.5:14b)을 언로드(스왑)해야 한다. **NVIDIA SANA-0.6B**를 사이드카로 통합해 **LLM을 밀어내지 않고 co-resident로 1024² 이미지를 warm 1.14s에 생성**하는 무스왑 fast-lane을 프로덕션에 배선했다. 기본 opt-in(`sana_enabled`)이라 기존 동작 100% 불변.

## 배경 (PoC → 통합)
- **검색·PoC**(2026-07-04): NVIDIA 공개 이미지 모듈 조사 → SANA 선정. 생성 SANA-0.6B(1.14s·4.4GB)·확장 ControlNet-HED(1.66s·4.7GB)가 **상주 qwen과 co-resident** 실증(4-bit Gemma TE + VAE 타일 레시피). SANA-1.6B는 6.3GB로 co-reside 불가(LLM 축출)→16GB 부적합 판정. 라이선스=NVIDIA Open Model(상업 허용, Gemma Terms 준수).
- **차별 가치**: 현 무기고에서 **유일하게 이미지 생성이 LLM을 축출하지 않는** 모델. 16GB 천장·모델 스왑이라는 근본 병목을 이미지 축에서 우회.

## Phase 1 구현 (무스왑 생성)
### 1. SANA 사이드카 서비스 (`sana_service.py` + systemd `sana.service`)
- Flask `127.0.0.1:8189`, 프로덕션 venv 재사용(torch/diffusers/bitsandbytes 기존재, 신규 설치 0). 가중치 `~/sana_models/Sana_600M_1024px`.
- **지연로드 + idle-unload**(`sana_idle_timeout` 기본 1800s): 첫 요청 시 로드, 유휴 시 VRAM 자율 반납. `POST /generate`·`GET /health`·`POST /unload`.
- **🔑 warm 1.14s 확보**: Flask `threaded=True`가 요청마다 새 스레드를 만들어 cuDNN 워밍업이 리셋(매 호출 콜드=4.3s)되던 것을, **단일 `ThreadPoolExecutor(max_workers=1)`로 모든 CUDA 작업을 같은 스레드에 고정** → 워밍업 유지(warm 1.14s·cold 4.5s).
- systemd 상시 running·`enabled`(부팅 자동)·`Restart=on-failure`. 내부 전용(127.0.0.1).

### 2. 메인 앱 통합 (`app.py` + `routes/image.py`)
- `_process_image_job` **engine=sana 분기** → `_sana_generate_job`(사이드카 호출 → RAW PNG를 `sana_out/`에 저장).
- `image_worker_loop`: sana 잡은 **LLM 언로드/복원 skip**(co-resident=무스왑 핵심).
- `_auto_s3_upload`/`api_image_view`: sana(subfolder='sana')는 ComfyUI `/view` 대신 **디스크에서 바이트** 취득 → 마킹(AI 기본법 §31)·S3 업로드 로직은 ComfyUI 경로와 동일 재사용.
- `api_image_generate`: `engine` 검증(sana는 `sana_enabled=true`일 때만 허용, 미지정=comfyui 기존동작).
- **VRAM 상호배제**: ComfyUI 대형잡(SDXL/Flux) 전 `_unload_sana_sidecar`(SANA+SDXL+qwen 동시 불가 방지).

### 3. config 토글 (`api_image_settings`, localhost/admin 전용)
- `sana_enabled`(기본 false=기존 동작 불변) · `sana_service_url`(기본 `http://127.0.0.1:8189`). 무재배포 튜닝.

## 검증 (E2E)
- `POST /api/image/generate {engine:"sana"}` → 큐 → 워커 → 사이드카 → 디스크 → **마킹 + S3(Wasabi presigned)** → `completed`.
- **qwen 잡 전후 100% GPU 유지 = 진짜 무스왑 co-resident 확정**. cold 4.5s·warm 1.14s.
- graceful HUP NRestarts0·health200. 미채용 SANA-1.6B(25G) 정리.

## 리스크 / 불변식
- **기본 opt-in**: `sana_enabled=false` 또는 engine 미지정 시 기존 ComfyUI 경로 100% 동일. 사이드카 다운 시 SANA 잡만 실패(기존 경로 무영향).
- 의료 이미지 아님(일반 생성)·§31 마킹 재사용. 라이선스=NVIDIA Open Model 상업 허용(Gemma Terms 준수).

## 잔여 (Phase 2)
- SANA ControlNet 구조제어 엔드포인트(+HED 전처리기) · Admin "SANA 이미지" 카드 · 회귀 테스트.

## 운영 교훈
- 이 GPU는 SSH background-launch(`setsid &`)가 세션을 끊음(255) → 영속 프로세스는 systemd로.
- `scp`로 여러 파일을 디렉터리에 복사하면 subdir이 평탄화됨(`routes/image.py`→루트) → 명시 경로 지정 필수.
- qwen CPU 스필/wedge는 `sudo systemctl restart ollama`(NOPASSWD)로 클리어.
