# v2.124.23 — 성능·SQL 검토 라운드2 (위성 DB·pragma) + WAL 위생

2026-07-21 | 계기: 운영자 "성능 검토. SQL 튜닝도 검토" (v2.124.22 이후 재요청) | 관련: [[project_performance_monitoring_plan]]

## 요약

v2.124.22가 큰 테이블(fee_codes·drug_master) 중심이었던 것과 다른 각도 —
**위성 DB 인덱스·SQLite pragma·ANALYZE 통계 최신성·WAL 상태**를 실측했습니다.
**병목은 여전히 0**이고, 디스크 위생 항목 1건(대량 sync 후 WAL 고수위)만 조치했습니다.

## 측정 결과 — 전부 정상

### queue.db 핫쿼리 (ANALYZE 통계 0인데도 문제없음)

254.9MB 최대 DB인데 `sqlite_stat1`이 비어 있어 옵티마이저 판단을 확인했으나, 모든
핫쿼리가 **명시적 단일 인덱스 경로**라 통계가 불필요했습니다:

| 쿼리 | 지연 | 계획 |
|---|---|---|
| worker 디큐 | 0.01ms | idx_jobs_queue (status=?) |
| 배치 조회 | 0.04ms | idx_jobs_batch |
| 세션 메시지 | 0.01ms | idx_chat_messages_session_created |
| 세션 목록 | 0.02ms | idx_chat_sessions_last_active_at |

→ **ANALYZE 불요**(추가하면 인덱스 부채만 늘고 계획 불변).

### 위성 DB — 인덱스 완비

- clinic_codes(15.6MB)·drug_info(15MB)·dur(3.1MB): 조회 대상 테이블 전부 인덱스 보유.
- 약 정보 조회 0.03ms(item_seq PK), fee_codes 코드 조회 0.01ms(v2.124.22 재확인).
- notice.db는 dur_sync 후 ANALYZE로 stat1 20행 유지(정상).

### eval.db `journal=delete` — 무해

다른 DB는 WAL인데 eval.db만 rollback 저널입니다. 야간 배치 1회 쓰기라 WAL 이점이
없고, 조회는 0.1~0.2ms로 빠릅니다 → 조치 불요.

## 조치 — 대량 sync 후 WAL 고수위 축소 (성능 아닌 디스크 위생)

**notice.db-wal이 20.6MB**였습니다(queue.db-wal은 0.9MB). 원인 규명:

- 체크포인트 결과가 `(busy=0, log_pages=0, ckpt=0)` — **데이터는 이미 병합됨**.
  즉 자동 체크포인트는 정상 동작했고, WAL 파일만 오늘 치료재료 91K 적재의 고수위에서
  축소되지 않은 채 남아 있었습니다.
- 조회 지연도 WAL 크기와 무관(fee_codes 집계 WAL 20MB→0 전후 동일) → **성능 문제 아님**.

근본 원인은 **비대칭**입니다 — 주기 `_wal_checkpoint`(60s)는 queue.db만 대상이라,
위성 DB는 대량 트랜잭션 후 WAL이 고수위로 남습니다. 게다가 sync 경로들이 ANALYZE는
하면서 체크포인트만 빠져 있었습니다.

fix: `core.notice_db.checkpoint_truncate` 헬퍼 + 대량 clear+replace sync 3경로
(`hira_drug_sync`·`hira_mcat_sync`·`dur_sync`)의 ANALYZE 옆에 적용. 비치명(실패 무시).
현재 상태는 라이브에서 TRUNCATE로 이미 정리(20.6MB→0, 무손상).

**정직한 위치**: 이건 성능 최적화가 아니라 위생·비대칭 해소입니다. 이득은 디스크
(대량 sync당 수십 MB)와 크래시 복구 시간뿐이고, 조회 성능은 그대로입니다. queue.db와
동일한 WAL 위생을 위성 DB에도 맞춘다는 관점에서 추가했습니다.

## 회귀

`tests/test_notice_wal_checkpoint.py` 2 — WAL 축소·데이터 보존, 이상 커넥션 무동작.

## 종합

v2.124.22와 동일한 결론 — **소프트웨어 튜닝 대상 없음, 근본 레버는 16GB VRAM 천장**.
측정 선행이 이번에도 추측을 잘라냈습니다(ANALYZE 0을 "튜닝 필요"로 오판할 뻔했으나
명시 인덱스라 불요, WAL 20MB를 "성능 저하"로 오판할 뻔했으나 log_pages=0이라 위생 항목).
