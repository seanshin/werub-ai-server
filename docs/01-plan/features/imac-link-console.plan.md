# iMac 연계 콘솔 — GPU↔iMac 관계·오케스트레이션 설정 UI

> **Feature**: imac-link-console
> **Created**: 2026-06-07 | **Version Target**: v2.82.0
> **Priority**: P2 (운영성 — 2026-06-08 배포 후 운영 경험 기반으로 착수 권장)
> **Related**: gpu-imac-link (터널 토폴로지), apple-silicon-worker (위임)
> **⚠️ 범위 주의**: 본 기능은 **GPU 서버 Admin UI 변경**을 포함 — 사용자 발의(2026-06-07)로 승인됨.
> 단, 기존 API/동작 변경 없는 **추가형**(신규 Blueprint + 패널)으로 한정

## 1. 목적 / 문제

GPU↔iMac 연계가 가동되면(2026-06-08 이후) 운영자가 제어할 지점이 생기는데, 현재 전부 수동이다:

| 설정/상태 | 현재 위치 | 변경 방법 |
|----------|----------|----------|
| 위임 on/off + 대상 기능 | GPU `config.json` apple_worker_* 4키 | ssh + vi |
| 경량 오프로드 모델 목록 | GPU `app.py:3602` **하드코딩** | 코드 수정 + 재배포 |
| 워커 상태 (memory/thermal/locks) | iMac `:8587/worker/health` | curl (터널 경유) |
| 터널 채널 3개 생존 | GPU `ss`/iMac `pgrep` | CLI |
| 위임 실적 (몇 건이 iMac行?) | GPU 로그 grep | CLI |
| iMac 워커 노출 모드 | iMac plist env | iMac에서 vi + 재기동 |

→ **Admin 대시보드에 "iMac 연계" 패널 1개 + 설정 API**로 통합한다.

## 2. 구현 범위 — 3단계

### Phase A: 상태 가시화 (읽기 전용) — 배포 직후 가장 필요

| # | 항목 | 내용 |
|---|------|------|
| A-1 | `GET /api/imac-link/status` (신규 BP `routes/imac_link.py`) | 통합 상태 JSON: ①터널 채널별(8586 Ollama ping / 8587 worker health / 8590은 워커측 보고) ②worker health 프록시(memory_pressure/thermal/locks/capabilities/expose) ③위임 config 현황 ④L1 오프로드 가용성(기존 `_is_imac_available` 재사용) |
| A-2 | 위임 실적 카운터 | `core/apple_worker.py`에 인메모리 카운터(delegated/fallback/error 건수+최근 10건) — status에 포함. Prometheus `apple_worker_delegated_total` 게이지 추가 |
| A-3 | Admin 대시보드 패널 | `templates/admin.html` + `static/js/main.js`에 "iMac 연계" 카드 — 채널 신호등(🟢🔴), 워커 메모리/thermal, 위임 통계, 30s 폴링 (기존 멀티서버 패널 패턴 재사용) |

### Phase B: 오케스트레이션 설정 (쓰기 — localhost only)

| # | 항목 | 내용 |
|---|------|------|
| B-1 | `LIGHTWEIGHT_MODELS` config화 | 하드코딩 → `config.json["imac_offload_models"]` (기본값 `["llama3.2:3b"]` — 기존 동작 동일). gpu-imac-link §7의 qwen2.5:3b 확장이 **UI 토글**로 가능해짐 |
| B-2 | `POST /api/imac-link/config` | 변경 가능 항목: `apple_worker_enabled`, `apple_worker_delegate`(체크박스: detect/stt/embed), `imac_offload_models`(iMac 보유 모델 목록에서 선택 — 8586으로 `/api/tags` 조회해 검증), 타임아웃 값. **토큰은 UI 노출 금지** (마스킹, 변경은 CLI만) |
| B-3 | UI 설정 폼 | 패널에 접이식 설정 영역 — 저장 시 즉시 반영 (apple_worker는 호출마다 config 로드라 무재시작) |
| B-4 | 가드레일 | localhost only (기존 `/api/config` 패턴) + 변경 이력 로그(activity 기록) + delegate에 미구현 기능(stt/embed) 선택 시 경고 표시 |

### Phase C: 진단 도구 (선택)

| # | 항목 | 내용 |
|---|------|------|
| C-1 | `POST /api/imac-link/test` | 원클릭 진단: 샘플 이미지로 위임 detect 실행 → engine/소요시간 반환, 실패 시 폴백 경로 확인 결과 |
| C-2 | 헬스 캐시 강제 갱신 | `POST /api/imac-link/refresh` — 30s 캐시 무효화 (터널 복구 직후 즉시 반영용) |

## 3. UI 스케치 (Admin 대시보드 카드)

```
┌─ iMac 연계 (Apple Silicon Worker) ─────────────────────┐
│ 터널   8586 Ollama 🟢   8587 Worker 🟢   (세션 1개)    │
│ 워커   memory normal (12.0GB free) · thermal nominal   │
│        locks: - · capabilities: detect, mark           │
│ 위임   ✅ enabled · delegate: [✓detect ☐stt ☐embed]    │
│        실적(기동후): 위임 142 · 폴백 3 · 오류 0        │
│ 오프로드 모델: [✓llama3.2:3b ☐qwen2.5:3b] (iMac 보유 4)│
│ [설정 저장] [진단 실행] [캐시 갱신]          30s 자동 ↻ │
└────────────────────────────────────────────────────────┘
```

## 4. iMac 측 변경 (경미)

- `/worker/health`에 `tunnel_8590_ok`(iMac→GPU 역방향 채널 자가 점검 — `localhost:8590/api/health` 2s ping) 필드 추가 — GPU UI가 3채널 전부 표시 가능
- 워커 노출 모드(`expose`)는 이미 health에 있음 — UI는 읽기 전용 표시 (변경은 iMac plist 작업으로 안내 문구)

## 5. 안 하는 것 (범위 제외)

- iMac 측 설정의 원격 변경 (plist env 원격 수정) — 보안상 iMac 로컬 작업 유지
- 토큰 UI 노출/변경 — CLI 전용 (마스킹 표시만)
- Next.js Admin Console(3콘솔) 통합 — 1차는 Flask admin.html만, 콘솔 통합은 수요 확인 후
- 큐/잡 재라우팅 수동 개입 — 기존 자동 라우팅 유지

## 6. 공수

| Phase | 공수 |
|-------|------|
| A (상태 가시화) | 1일 |
| B (설정 + LIGHTWEIGHT_MODELS config화) | 1일 |
| C (진단) | 0.5일 |
| **합계** | **2.5일** |

## 7. 리스크

| 리스크 | 대응 |
|--------|------|
| GPU Admin UI 변경 (M칩셋 전용 원칙) | 사용자 발의로 승인 — 단 **추가형 한정** (기존 패널/API 무변경), 신규 BP 분리로 격리 |
| B-1 config화가 기존 동작 변경 | 기본값을 현재 하드코딩 값과 동일하게 — config 키 없으면 기존과 동일 (위임 패턴과 같은 휴면 원칙) |
| 설정 오변경 (delegate에 미구현 기능) | 가드레일 B-4 — 미구현 capability는 워커 health의 capabilities와 대조해 경고 |
| UI 폴링이 헬스 캐시 TTL과 충돌 | status API는 캐시된 값 반환 (30s) — 워커에 추가 부하 없음 |

## 8. 착수 시점 권고

**2026-06-08 배포 + 24h 관찰 이후** 권장 — ① 위임 실적 데이터가 쌓여야 패널 설계 검증 가능
② LIGHTWEIGHT_MODELS 확장 판단(§7 of gpu-imac-link)에 UI가 맞물림 ③ 배포일에 UI 작업까지 겹치면 변수 증가.
순서: 배포(6/8) → 24h 관찰 → Phase A → erp-notice-rag Day 2~4와 병행 판단
