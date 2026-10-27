# Release v2.124.51 — 회의 접수 상한 호출자별 적용 (멀티테넌트 큐 독점 방지)

날짜: 2026-07-29 | 종류: feat(meeting) | 배포: systemd restart(부팅 실패 복구 포함)

## 배경

edu 확인 요청: "접수 50건이 전체 큐 기준인지 키(테넌트)당인지 — 병원이 늘면 이게 먼저 걸린다".
확인 결과 **전체 큐 기준**이었고 제출자 기록조차 없어, **한 병원의 일괄 업로드가 큐를 채우면
다른 병원이 전부 503**으로 막히는 구조였다.

## 수정

- `meeting_jobs.submitted_by`(API 키 이름) 기록 + 인덱스 + 멱등 마이그레이션.
- `can_accept(submitted_by) → (ok, 사유)`: 전체 `MEETING_MAX_QUEUE`(50) **AND**
  키당 `MEETING_MAX_QUEUE_PER_KEY`(30). 503 메시지에 어느 상한인지 명시.
- 키 없는 호출(로컬 등)은 전체 상한만 적용.
- `/api/meeting/status`에 `max_queue_per_caller` 노출.
- 회귀 5(한 호출자 상한 도달·**다른 호출자는 계속 접수**·전체 상한·완료 잡 미포함·익명).

## 🔴 배포 사고 — 약 1분 서비스 중단(자책 기록)

신규 컬럼 인덱스를 `CREATE TABLE ... ` **executescript 안에** 넣어, 기존 DB(컬럼 없음)에서
`no such column: submitted_by`로 **워커 부팅이 실패**했다. gunicorn 마스터가 종료되며
`/api/health`가 000이 됐고, 코드 수정 후 `systemctl restart`로 복구했다(09:31:28 장애 →
약 1분 내 복구, health 200·전체 회귀 1081 passed).

- **원인**: 마이그레이션 순서 오류(인덱스가 ALTER보다 먼저).
- **왜 못 걸렀나**: 이 프로젝트의 배포 절차에 있는 **`import app` dry-run을 이번에 건너뛰고**
  pytest만 돌린 뒤 HUP했다. dry-run은 실 DB로 `_init_db`를 태우므로 **이 오류를 반드시
  잡았을 것**이다. 단위 테스트는 임시 DB(신규 스키마)를 쓰기 때문에 통과했다.
- **재발 방지**: 스키마를 건드리는 변경은 ①인덱스·제약을 ALTER 이후에 두고 ②배포 전
  **실 DB 대상 import dry-run 필수**(생략 금지).
