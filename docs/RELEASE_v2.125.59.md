# v2.125.59 — 고급 설정 표 + 접속 정보 동적화 (Phase 3) + 버전 배지 딥링크 수정

기준 v2.125.58 · 기획 `docs/01-plan/features/admin-menu-settings-cleanup.plan.md` §4.3

## 1. 고급 설정 표 (시스템 정보 절 · 읽기 전용)

- 신규 `GET /api/config/overview`(admin): config 전 키 · 현재값(D2와 같은 마스킹) · **코드 기본값** · 바꾸는 곳(설정 절 / API / config.json 직접).
- 기본값은 **지어내지 않고 코드에서 뽑는다**: `DEFAULT_CONFIG` 리터럴 + `app.py`·`routes`·`core`의 `<cfg|config…>.get("키", 리터럴)` AST 스캔
  (파일 ~150개·≈0.7s, 첫 호출 1회 후 캐시). 기본값을 못 찾은 키는 빈칸.
- 같은 키에 기본값이 여러 개면 전부 보이고 "⚠ N곳 상이" 표시. **첫 스캔 결과 101키 중 4키**(라이브 config에 있는 3키):
  `default_model`(`''` 큐 — 없으면 400 / `'qwen2.5:14b'` 파이프라인) · `allowed_origins`(DEFAULT_CONFIG 목록 / `[]` — DEFAULT_CONFIG가 먼저 병합돼 무해) ·
  `heavy_model`(`'default'` / 식) · `bind_port`. 현재는 모두 config에 값이 있어 **무증상** — 키가 빠지는 신규 설치에서 경로마다 다르게 동작할 수 있다(기록만, 처치 보류).
- 편집 UI는 만들지 않는다(기획 §1 비목표). 키 검색 입력 제공.

## 2. 접속 정보 동적화 (연동·연결 절)

- 예전 하드코딩 4줄(`localhost:8585`·`localhost:11434`·`example.org/ollama/`·Swagger) → 이 화면을 연 주소(`location.origin` + BASE) ·
  서버가 받은 바인드(`SERVER_NAME:PORT`) · Ollama 엔진 주소 · `ingress_mode` · iMac 워커 위임 설정(상태는 Dashboard 링크).

## 3. 수정 — 버전 배지 딥링크 (v2.125.58 라이브 확인 중 발견)

- 이미 `#settings/engine`에 있으면 버전 배지가 시스템 정보 절로 가지 않았다 — `openSettings`가 지정한 절보다 현재 hash를 먼저 봤다. 순서 교정.

## 4. 검증

- 신규 회귀: overview 마스킹·전 키·DEFAULT_CONFIG 기본값·편집 위치·연결 필드 / AST 스캔 리터럴 / 비관리 403 / 접속 정보 하드코딩 제거 / 절 우선순위. 전체 **2343 passed**.

## 5. 배포 게이트 — 176/177 차단 (기존 결함 재발)

- 코드는 4단계(HUP)에서 반영돼 라이브 v2.125.59. 게이트는 `vision-general: 기본 모델 응답` 1건으로 차단 —
  `qwen3-vl:8b` 사고 **13,985자**로 4096도 소진(v2.125.55 처치의 반증). 이번 변경(설정 화면)과 무관.
- 사전 등록 (2) temperature 0.2 측정 중 — `docs/01-plan/references/vision-thinking-budget-2026-09-29.md` §6·§7.
- 배포 구간 실사용 5xx·Ollama 60s 초과·499 = 0 · NRestarts 0.
