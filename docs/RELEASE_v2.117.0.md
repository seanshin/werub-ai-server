# RELEASE v2.117.0 — SANA 잔여 폴리시 완결 (양방향 mutex·status·Admin 카드·회귀)

**날짜**: 2026-07-05
**헤드라인**: v2.115.0(생성)·v2.116.0(ControlNet)·v2.116.1(하위호환 게이트)로 완결한 NVIDIA SANA
무스왑 이미지 스택의 **운영 폴리시 3건**을 닫는다 — CN↔ComfyUI 양방향 상호배제, 상태 관측
엔드포인트, Admin 카드, 회귀 테스트. 프로덕션 동작 계약은 불변(sana_enabled OFF=100% 무변경).

---

## 배경

SANA 스택은 기능적으로 완결(검색→PoC→Phase1 생성→Phase2 ControlNet→하위호환 게이트)됐으나
릴리즈 노트가 백로그로 남긴 3건이 있었다:
1. **CN↔ComfyUI 양방향 mutex** — CN co-resident 마진이 극소(~30MB: CN 5.1GB + qwen 10GB = 15.8GB).
   ComfyUI on-demand가 idle-stop(300s) 전이라 동시 상주하면 SANA CN이 OOM. 기존엔 ComfyUI 잡 전
   SANA 언로드(한 방향)만 있었고, **반대 방향(SANA 전 ComfyUI VRAM 반납) 훅이 없었다**.
2. **상태 관측 부재** — 사이드카 loaded/mode/vram을 Admin에서 볼 수 없었다.
3. **회귀 테스트 부재** — `tests/`에 SANA 테스트 파일이 없었다.

## 변경 사항

### ① CN↔ComfyUI 양방향 상호배제
- `routes/image.py` `_free_comfyui_vram_for_sana()` 신설 — ComfyUI `/free`(unload_models)로 모델
  캐시만 비운다(프로세스 유지). **ComfyUI 미실행 시 no-op**(예외 삼킴).
- `POST /api/image/sana/controlnet`: 사이드카 호출 **전** `_free_comfyui_vram_for_sana()` 호출
  (CN 마진 극소 → OOM 방지). 테스트로 free가 controlnet보다 먼저 실행됨을 검증.
- `app.py image_worker_loop`(gen 경로): `_sana_engine` 분기에 `_gpu_manager.ensure_llm_vram()` 추가
  — SANA co-resident 전 ComfyUI가 상주 중이면 VRAM 반납(best-effort). ComfyUI 잡 전
  `_unload_sana_sidecar`(기존)와 짝이 되는 반대 방향.

### ② 상태 관측 — `GET /api/image/sana/status`
- config 토글(`sana_enabled`·`sana_service_url`) + 사이드카 `/health` 프록시(loaded·mode·vram·idle_s).
- **graceful**: 사이드카 미도달 시 `reachable:false`·`sidecar:null`로 200 반환(500 아님).

### ③ Admin "SANA 이미지" 카드
- `templates/admin.html` + `static/js/main.js` — 활성 여부·사이드카 도달·현재 파이프라인(gen/cn/idle)·
  VRAM 여유 표시. `/api/image/sana/status` 5분 폴링(기존 카드 패턴 재사용, 신규 폴링 부담 최소).

### ④ 회귀 테스트 `tests/test_sana.py` (15)
- route 계층: engine 검증(sana 비활성 400·unknown 400·활성 큐잉·comfyui 하위호환·watermark:false 기록),
  ControlNet 게이트(비활성 400·필드누락 400·성공 시 마킹된 PNG·**mutex free 선행**·사이드카 미도달 503),
  settings 토글, status 프록시(도달/미도달 graceful).
- 순수(사이드카): `sana_service._make_control` 엣지맵(1024² RGB·`data:` 접두 제거).

## 검증
- 로컬 py_compile(routes/image.py·app.py·sana_service.py·test)·`node --check main.js` OK.
- GPU `venv/bin/python -m pytest tests/test_sana.py -q` → **15 passed**.
- graceful HUP(NRestarts 0)·health 200·`/api/image/sana/status` 라이브(enabled·reachable·mode·vram).
- 계약 불변: sana_enabled OFF=기존 ComfyUI 경로 신규 호출 0(v2.116.1 게이트 유지).

## 영향 / 하위호환
- 신규 엔드포인트 1건(`/api/image/sana/status`)·Admin 카드 1건·테스트 15. 기존 API 계약 무변경.
- gen 경로 mutex는 SANA 잡에서만 발화(비-SANA=기존 동작). ComfyUI 미실행 시 mutex는 no-op.
