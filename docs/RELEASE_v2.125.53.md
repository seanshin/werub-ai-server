# v2.125.53 — 한국어 필터 입력 표류 관측 로그 + 신규 설치 DB 스키마 결함 봉합

기준 v2.125.52

## 1. `force_korean` 입력 표류 관측 (측정 도구 · 동작 불변)

v2.125.52가 story·novel에서 30% 중국어 표류를 실측했다. 같은 모델을 쓰는 다른 한국어 경로도
필터가 표류분을 **조용히** 걷어낼 수 있다. 앱을 2중 기동해 in-process로 재면 스케줄러가 함께 떠
라이브와 ChromaDB 동시 쓰기 위험이 있어, 필터 자체에 관측을 달았다:
입력 CJK 비율 > 2%면 `[korean-filter] CJK 표류 입력 share=… len=… caller=모듈:줄` 경고. **반환값 불변**·
관측 실패는 필터를 막지 않는다. 운영에서도 계속 모든 경로의 표류를 보이게 한다.
측정 계획은 사전 등록 `docs/01-plan/references/korean-drift-survey-2026-09-28.md`.

## 2. 🔴 신규 설치 DB 스키마 결함 (측정 준비 중 발견)

- 증상: v2.125.51의 speaker 인증 회귀가 **간헐 실패**(`no such column: expires_at`) — 단독 실행은 통과.
- 원인: `app.py`·`core/db.py`의 `init_db` 둘 다 **컬럼 마이그레이션 루프가 api_keys·stats_history·api_activity
  생성 전에** 돌았다. 신규 DB에선 `ALTER`가 '테이블 없음'으로 **조용히 실패**(except pass)하고 테이블이 그 컬럼 없이
  만들어졌다 ⇒ **`api_keys.expires_at` 부재 → Bearer 검증·키 발급이 `no such column`**, `stats_history` 성능 컬럼 16개 부재.
  라이브 DB는 테이블이 먼저 있어 드러나지 않았다(연동 검토의 '신규 설치 부재'와 같은 클래스).
  간헐성은 선행 테스트가 어떤 스키마로 DB를 먼저 만들었는지에 좌우됐다.
- 처치: 두 `init_db`의 마이그레이션 루프를 **모든 CREATE TABLE 뒤로** 이동(마이그레이션 컬럼 인덱스는 루프 이후 —
  기존 `idx_activity_key`는 CREATE에 컬럼이 있어 안전 확인).
- 🔴 연쇄 발견: `core/db.py` `init_db`의 `from . import logger`가 로거가 아니라 **`core/logger.py` 모듈**을 가져와,
  ALTER가 처음 성공하는 순간 `AttributeError`. 이 함수는 호출처가 없는 중복본(정본=app.py)이라 운영 영향 0 — 쓰일 때 틀리지 않게 교정.

## 3. 검증

- 신규 `tests/test_init_db_fresh_schema.py` 5건 — **구 코드 5/5 실패 · 신 코드 5/5 통과**(탐지기부터).
- 관측 회귀 4건(호출 위치 로그·반환값 불변·정상 입력 무로그·관측 예외 무전파).
- 전체 **2311 passed × 2회 연속**(간헐 실패 소멸).
- ⚠️ 운영 기록: 첫 시도에서 GPU 임시 worktree 생성이 stale 등록으로 실패했는데 후속 명령이 `;`로 이어져
  **라이브 레포 디렉터리에서 구 코드 회귀가 돌았다** — 변경 0·stash 0·상태 파일 무변경·서비스 무영향 확인 후
  `set -e`로 재실행. 이후 worktree 검증은 `set -e` 필수.
