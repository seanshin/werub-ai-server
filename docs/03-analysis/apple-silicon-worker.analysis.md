# apple-silicon-worker — Phase 0~1 갭 분석 (PDCA Check)

> **분석일**: 2026-06-07 | **분석자**: gap-detector 에이전트
> **범위**: Phase 0 (기반 정비 9항목) + Phase 1 (워커 + 위임 클라이언트) — Phase 2/3은 미래 작업으로 제외
> **결과**: **Match Rate 96%** (기준 90% 충족 ✅)

## 종합

| 분류 | 건수 | 내용 |
|------|------|------|
| ✅ 완전 일치 | 26/32 | 워커 API 3종, 가드, 위임 클라이언트, 폴백 계약, MPS 분기, launchd 4종, 테스트 |
| 🟡 의도적 지연/추가 | 3 | tunnel plist(위임 활성화 시점 추가로 계획됨), health 추가 필드, gunicorn workers=1 |
| 🔵 경미 변경 (동등/개선) | 5 | AppleWorker 함수형(클래스 대신), caffeinate 별도 plist 분리, 위임 분기 10줄(<20줄), 동일성 테스트 1장(100장 기준 미달), stats 직접 수집 |
| 🔴 기능 결함 | 1 → **0 (수정 완료)** | 모드 B Bearer가 부재 함수(core.security.verify_api_key) 호출 — `_verify_bearer_key`(queue.db 직접 조회)로 당일 수정 + 테스트 추가 (20/20 통과) |

## 조치 내역 (분석 직후)

1. ✅ **Bearer 검증 결함 수정** — worker_app.py `_verify_bearer_key()`: SHA-256 key_hash + active + expires_at 직접 조회 (app.py 9.7K줄 import 회피). 유효/무효/만료 테스트 3케이스 추가
2. ✅ **설계 문서 동기화 4건** — §3 함수형 모듈 확정, §2-1 health 실응답 반영, §7 caffeinate 분리·stats 직접 수집 정정, §8 Bearer 검증 방식 확정

## 잔여 (비차단, 운영 검증 항목)

| 항목 | 시점 |
|------|------|
| detect cpu↔mps 동일성 100장 확대 측정 | GPU 위임 활성화 직전 |
| 24h 안정성 (watchdog 무중단 로그) | 상시 — watchdog 가동 중 |
| worker-tunnel plist 추가 | GPU 서버 위임 활성화 시점 (Plan 0-3 명시 절차) |
