# Release v2.109.1 — 측정 선행 성능/SQL 튜닝

**날짜**: 2026-06-23
**유형**: 성능·신뢰성 패치 (신규 API 0, 인터페이스 무변경)
**계획서**: `docs/01-plan/features/performance-sql-tuning.plan.md`

## 헤드라인

위성 SQLite DB(notice/dur/drug/clinic_codes/users 등)의 연결·캐싱·통계를 정비.
**핵심은 "측정 선행"** — GPU 실 DB에서 `EXPLAIN QUERY PLAN`·행수·엔드포인트 지연을 먼저
측정해 ROI 없는 인덱스 작업을 전부 컷하고, 정당화된 3건만 구현(인덱스 부채 0).

## 측정으로 컷한 추측 작업

| 측정 | 결과 | 결정 |
|------|------|------|
| `drug_master` 5-way OR | **MULTI-INDEX OR**(5 인덱스 전부, std_code=PK) | 풀스캔 아님 → 인덱스 불요 |
| orchestrator_runs / avatar_jobs / cardiac_ep_jobs | 22 / 13 / 6행 | 인덱스 무의미 |
| drug_substitution.lookup | `idx_sub_ingr` 사용 + 소수 정렬 | 복합인덱스 이득 미미 |
| `/api/queue/stats` | jobs 2,149행·`idx_jobs_completed`·8ms | 핫스팟 아님 + ETA/recent staleness 위험 |

> 초안의 인덱스 4종·잡 인덱스·queue/stats 캐시가 전부 ROI 음수로 확정 컷.

## 변경 사항 (3건)

### P1-2 — `/api/notice/drug-data/status` TTL 캐시 (120s)
4-DB 집계(drug_master 305K 등)로 유일하게 느렸던 엔드포인트. `routes/notice.py`
`_DRUG_DATA_STATUS_CACHE`(TTLCache). 데이터는 ingest/sync 시점만 변동.
**라이브 입증**: 53.7ms(콜드) → 1.08ms → 0.8ms(캐시적중, ~50×).

### P0 — busy_timeout 하드닝 (신뢰성)
무방비 connect 5곳에 `PRAGMA busy_timeout=5000` 추가(users.db는 WAL 동반):
`core/medical_validator.py`·`core/quality.py`(2)·`core/hr_law.py`·`app.py`(users.db)·`worker_app.py`.
동시쓰기 창(dur_sync clear+replace·수동 ingest)에서 `SQLITE_BUSY` 즉시실패를 방어.
성능 변화 ≈ 0(메모리상 connect ~0ms) — **드문 동시쓰기 창의 신뢰성 하드닝**.

### P1-4 — 동기화 후 ANALYZE (옵티마이저 통계)
`core/dur_sync.refresh_all` 성공 후 dur.db·drug_info.db `ANALYZE`(`_analyze_after_sync`).
전 위성 DB에 `sqlite_stat1` 부재(ANALYZE 0회) 확인 → bulk clear+replace로 노후화되는
통계를 갱신. **라이브 입증**: dur.db `sqlite_stat1` 0→15행 생성.

## 검증

- 로컬 회귀 97 passed(dur 23 + drug_substitution/drug_info 14 + quality/medical_validator 60).
- GPU: import 드라이런 OK → graceful HUP reload(NRestarts 0) → 라이브 캐시·ANALYZE 입증 →
  health 200@5ms → **전체 회귀 647 passed / 7 skipped**(v2.109.0 베이스라인 동일, 부수영향 0).

## 잔여 (저우선, 측정상 불요)

- queue.db 192MB / 2,149행 free-page bloat → VACUUM 후보(무관, 별도 백로그).
- P2(dur.check N+1 배칭·위성 DB WAL 체크포인트·LIKE→FTS5)=측정상 실익 작아 보류.
