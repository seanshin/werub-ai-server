# Release v2.124.44 — 회귀 안전망 복구 + core Flask 규칙 정확화 + 키 만료 정책 정리

날짜: 2026-07-29 | 종류: fix(tests/core) + ops | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

07-28에 `/openapi.json`이 하루 동안 500이었는데도 못 잡은 원인이 **회귀 안전망 자체의
고장**이었다. 그 뿌리를 정리했다.

## 1. pytest 수집 전면 중단 해소 (핵심)

`tests/test_queue_backoff_reap.py`와 `tests/api/test_queue_backoff_reap.py`가 **같은
basename**이라 rootdir 기준 모듈명이 충돌 → `pytest` 기본 실행이 **수집 단계에서 중단**됐다.
그 결과 `tests/api`의 openapi 검증이 아예 실행되지 않아 Swagger YAML 파손을 놓쳤다.

- `tests/test_queue_backoff_reap.py` → **`tests/test_auto_purge_null.py`** 이름 변경
  (내용이 v2.124.22 auto_purge NULL 회귀라 새 이름이 실제와도 맞다).
- GPU 레포 루트의 untracked 잔재 `test_dur.py`(07-05·tests/ 정본과 동일) 제거(백업 후).
- **결과: 기본 `pytest` = 1043 passed / 7 skipped / 0 failed** (종전 수집 2건 중단 +
  importlib 우회 시 2건 실패). 우회 옵션 없이 도는 상태로 복구.

## 2. core Flask 규칙을 AST 최상위 검사로 정확화

`test_no_flask_imports`가 **문자열 검사**여서 지연 import까지 실패로 잡았고, 그 회피용
화이트리스트에 'lazy import'라고 적힌 파일 중 실제로는 **최상위 import**인 것이 섞여 있었다
(규칙이 의도를 잃은 상태). 규칙의 실제 의도는 "core가 Flask 없이 import 가능"이다.

- 검사를 **AST 모듈 최상위 import**로 교체 → 함수 내부 지연 import는 정상 통과.
- `core/errors.py`(v2.124.25 도입분)의 `jsonify`를 함수 내부로 이동 → **규칙을 정직하게 통과**.
- 예외 목록은 `websocket_handler.py`(Blueprint 정의라 최상위 의존 불가피) **1개로 축소**.

## 3. edu 키 만료 정책 정리 (ops)

edu 키 id21(`sk-9cfe6a5`·07-28 사용 중)이 **만료 무제한**이라 365일 정책과 어긋났다 →
`expires_at 2027-07-28`로 설정. 안전망은 v2.124.31 D-7 경보(만료+최근 사용=critical).

⚠️ 관찰: 무제한 키가 아직 여럿(his-2026·erp-notice-worker·movie·pacs·devAI·OpenPencil).
일괄 적용은 **미래 단절점을 만드는 일**이라 운영자 판단 사안으로 남긴다(07-19 appServer
단절이 그 클래스였다).

## 검증

기본 `pytest` 1043 passed/7 skipped · `core.errors` import OK · HUP NRestarts 0 ·
health 200 · `/openapi.json` 200 · health perf `keys_expiring` 0건(위양성 없음).
