# Release v2.84.0 — Node Orchestrator Phase 1 + 교차 아키텍처 통합

> 2026-06-08 | 이전: v2.82.0 (v2.83.0 결번 — 본 릴리스로 통합)

## 하이라이트

### 1. Node Orchestrator Phase 1 (신규) — GPU↔iMac 통합 동적 오케스트레이션
호출자는 GPU API만 부르면, **중앙 스케줄러가 작업 capability와 노드 실시간 상태로 최적 노드에 자동 배정**한다. 실패 시 항상 로컬 폴백.

- **신규 패키지** `core/orchestrator/`:
  - `registry.py` — capability 선언(`config/orchestrator.json`) + 무재배포 reload
  - `nodes.py` — Node Health Plane (EWMA·circuit breaker·`_LOCK` 동시성, `/worker/health` 30s 캐시)
  - `scheduler.py` — `pick()`: 하드게이트(미도달/manifest/breaker/mem critical/thermal throttled/heavy lock) + 소프트가중(inflight/지연) + hysteresis
  - `client.py` — payload_kind 인코딩, ok=HTTP200∧available
  - `dispatch.py` — `run(cap, payload, local_fn=)` 단일 진입점, **폴백 예외 전파 금지**(원격·로컬 모두)
  - `metrics.py` — 노드×capability×decision 관측성
- **detect 이전**: `routes/image.py`가 bespoke 위임 → `dispatch.run("detect", …)`
- **신규 API 2종**: `GET /api/orchestrator/nodes`, `POST /api/orchestrator/registry/reload`
- **활성화 도구**: `scripts/activate_imac_delegation.sh` (config 안전 편집 + 검증 + 롤백)
- **불변식**: 노드 0개 = 기존 동작 100% (현재 **휴면** — `config.json apple_worker_*` 미설정) / Darwin 가드 / 무상태 워커
- **검증**: 35 단위테스트 통과, gap-detector Phase 1 **Match Rate 96%**
- 설계/분석/보고: `docs/0{1,2,3}-*/node-orchestrator.*`

### 2. 교차 아키텍처(device) 통합
단일 레포가 Ubuntu(CUDA)+iMac(MPS) 양쪽 실행 → device 선택 단일화.
- `core/device.py` — `resolve(auto/cuda/mps/cpu)` 미가용 시 cpu 폴백, `pipeline_arg`
- `core/meeting_diarize.py` — **CUDA 하드코딩 제거**(iMac 크래시 지뢰 해소)
- `core/meeting_stt.py` — faster-whisper(CTranslate2 MPS 미지원) cuda/cpu 매핑 + CPU int8 폴백
- `core/ai_image_detector.py` — 중복 device 분기 제거(공용 위임)
- 가이드 `docs/CROSS_ARCH.md` (관리 정책 + config 키 표 + 체크리스트)

### 3. 한국어 문장분리 버그 수정 (운영)
- numpy 2.3.5에서 pecab 1.0.8 정수 오버플로 → 문장 오절단 → `_split_sentences` 쓰는 **24개 한국어 API 분리품질 저하**
- KSS `backend=pecab → fast`(규칙 기반·numpy 비의존)로 교체. 한국어 테스트 44건 통과(회귀 0)

## 운영 변경 / 주의
- **GPU 코드 반영 = 전체 재기동**: `gunicorn.conf.py preload_app=True`라 **SIGHUP은 코드 reload 안 함**. 마스터 graceful TERM → systemd(`Restart=always`) 재기동, 또는 `sudo systemctl restart`. 재기동 전 import dry-run 권장.
- 오케스트레이터는 휴면 상태로 배포됨 — iMac 워커+터널 가동 후 `activate_imac_delegation.sh`로 활성화.

## 카운트
- API 549 → **551+** (orchestrator 2종) | Blueprint 33 | Core 32 → **33** (device + orchestrator pkg)

## 호환성 / 기술부채
- `core/apple_worker.py`는 그대로 존속(흡수 아님) — 오케스트레이터가 `config.json apple_worker_*` 키 재사용. 두 경로 공존은 **Phase 2에서 수렴** 예정.

## 다음 (로드맵)
- Phase 2 (v2.85.0): 워커 `/worker/run/<cap>` generic + mark/stt/embed 편입
- Phase 3 (v2.86.0): LLM/큐 일원화 + 메모리 상호배제
- Phase 4 (v2.87.0): 제어판 + 동적 정책 튜닝
