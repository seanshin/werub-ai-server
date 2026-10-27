# Release v2.116.0 — SANA ControlNet 구조 제어 (Phase 2)

## 헤드라인
v2.115.0의 SANA 무스왑 생성 fast-lane에 이어, **NVIDIA SANA ControlNet 구조 제어**를 추가했다. 엣지/윤곽(제어 이미지)대로 프롬프트를 렌더링 — "이 구도/스케치대로 그려줘". 생성과 마찬가지로 **상주 LLM(qwen2.5:14b)을 밀지 않고 co-resident**로 warm 1.66s에 동작.

## 구현
### 사이드카 CN 모드 (`sana_service.py`)
- `_load(mode)` — `gen`(SanaPipeline) ↔ `cn`(SanaControlNetPipeline) 모드 스왑. VRAM 예산상 한 번에 한 파이프라인만 로드(모드 전환 시 기존 반납).
- CN 가중치=diffusers 변환본 `ishan24/Sana_600M_1024px_ControlNetPlus_diffusers`(4-bit Gemma TE + VAE 타일). NVIDIA 공식 `.pth`는 NVLabs 네이티브 포맷이라 diffusers 비호환.
- `POST /controlnet {prompt, control_image(base64), preprocess, seed, controlnet_conditioning_scale, negative}`. `GET /health`에 현재 `mode` 노출.
- **HED 전처리기(controlnet_aux) 미설치 → PIL `FIND_EDGES` 대체**(`_make_control`) — 임의 소스 이미지를 엣지맵으로 변환(진짜 HED보다 거칠지만 구조 제어엔 유효).
- **🐞 모드 스왑 OOM fix**: gen→cn 스왑 시 gen 파이프라인 미완전해제 상태에서 cn 로드가 겹쳐 순간 peak 초과(4-bit bitsandbytes 잔재로 `empty_cache`만으론 부족) → **`gc.collect()` + `empty_cache()` + `synchronize()`** 로 완전 해제 후 로드.

### 앱 엔드포인트 (`routes/image.py`)
- `POST /api/image/sana/controlnet` — 동기. `sana_enabled` 게이트 → 사이드카 프록시 → **마킹(AI 기본법 §31, `_apply_marking`)** → PNG 직접 반환. `watermark:false`로 가시 워터마크 토글(메타 유지).

## 검증 (E2E)
- 가을 풍경 소스 → PIL 엣지맵 → "여름"/"겨울" 프롬프트로 **동일 구도(산·나무·호수 배치 보존)를 다른 계절로 렌더링** + "AI 생성·AI-generated" 워터마크 확인.
- warm CN **1.66s** · 모드 스왑 후 gen 1.18s. **qwen 잡 전후 100% GPU 유지 = 무스왑**.

## 한계 / 백로그
- **CN co-resident 마진 극소(~30MB)**: CN(5.1GB)+qwen(10GB)=15.8GB. **ComfyUI on-demand가 동시 실행 중이면 CN OOM**(정상상태는 ComfyUI idle-stop 300s이라 무관). 견고성엔 CN 경로에서 ComfyUI stop 훅이 이상적(미구현).
- Admin "SANA 이미지" 카드 · 회귀 pytest · CN↔ComfyUI 양방향 VRAM 상호배제 = 후속.
