---
name: Performance & SQL Tuning - Plan
description: 위성 SQLite DB(notice/dur/drug/medical/avatar)의 연결 PRAGMA 일관화·인덱스 커버리지·핫패스 캐싱을 정비하는 성능 튜닝 계획. queue.db(core/db.py)는 이미 최적화됨 — 본 계획은 그 표준을 나머지 DB·핫엔드포인트로 확산.
type: plan
feature: performance-sql-tuning
status: ✅ 구현·GPU 라이브검증 완료(P1-2 캐시 ~50×·P0 하드닝·P1-4 ANALYZE, 측정으로 나머지 컷) · PR 머지 대기(§8-3) · ⚠️P0-2는 미구현 잔여(컷 목록 누락 — §8-2 정정 2026-08-26)
date: 2026-06-23
---

# 성능 개선 & SQL 튜닝 계획서

> 중앙 작업큐 `queue.db`(`core/db.py` `get_db()`)는 이미 WAL·busy_timeout·cache·mmap·핵심 인덱스를
> 갖춘 **모범 표준**이다. 본 계획은 그 표준을 **위성 DB 12종**(notice/dur/drug_info/drug_master/
> medical/avatar/clinic_codes/hr_law/meeting/users 등)과 **집계형 핫 엔드포인트**로 확산하여,
> 락 경합·재연결 오버헤드·풀스캔 집계를 제거한다.
>
> ⚠️ 본 계획의 성능 수치 중 "**[측정필요]**" 표기는 `EXPLAIN QUERY PLAN`/부하측정으로 확정해야 하는
> 추정치다(SQLite OR-term 인덱스 유니온 등 옵티마이저 거동은 실측 전 단정 금지). "**[검증됨]**"은
> 코드 직접 확인(file:line)으로 사실 확정한 항목이다.

---

## 0. 현황 (2026-06-23, v2.109.0 HEAD d9f0861)

| 영역 | 현재 상태 | 판정 |
|------|-----------|------|
| `queue.db` 연결(`core/db.py:27`) | WAL·busy_timeout=5000·cache=-8000·mmap=128M·temp=MEMORY·thread-local | ✅ 표준 |
| `queue.db` 인덱스(`core/db.py:118-122`) | status/batch/queue(status,priority,created)/created/completed | ✅ 양호 |
| api_activity 로깅(`app.py:1443-1457`) | 비동기 큐(maxsize 4000)+배경 writer+2000행 상한 purge | ✅ 최적화됨 |
| 위성 DB 연결 표준화 | **모듈별 제각각** — WAL/busy_timeout 누락 다수 | 🔴 대상 |
| 위성 DB 인덱스 커버리지 | 단일컬럼 위주, 복합 WHERE·정렬 미커버 | 🟡 대상 |
| 집계형 핫 엔드포인트 캐싱 | 일부만 TTL 캐시(/dashboard 10s·corpus 60s) | 🟡 대상 |
| gunicorn | gthread workers=1·threads=96·preload_app=False·max_requests=5000 | ✅ 안정 |

**핵심 판단**: 성능 병목은 LLM/임베딩(GPU)과 16GB VRAM 천장이 지배적이며, SQLite는 대부분 정상이다.
다만 **(1) 요청 경로의 비표준 연결**(WAL/busy_timeout 누락 → 동시 ingest 시 락 대기·SQLITE_BUSY 위험),
**(2) 미캐시 집계 엔드포인트**(GROUP BY 풀스캔 반복), **(3) N+1 약물 해석 루프**는 저비용·저위험으로
정리 가능한 명확한 개선 여지다. 본 계획은 이 3축에 집중한다.

---

## 1. 조사 요약 (3축, file:line 근거)

### 1-1. 연결 PRAGMA 일관성 [검증됨]

표준(`core/db.py`)을 따르지 않는 요청-경로 연결:

| 위치 | DB | WAL | busy_timeout | 수명 | 비고 |
|------|----|----|--------------|------|------|
| `core/medical_validator.py:22` | clinic_codes.db | ✗ | ✗(5s 기본) | per-call | 진료응답 검증 경로 |
| `core/quality.py:176, :256` | clinic_codes.db | ✗ | ✗ | per-call | ICD/약물 상호작용 검증 |
| `core/hr_law.py:33` | hr_law.db | ✗ | ✗(10s) | per-call | 법령 LIKE 검색 |
| `app.py:4490` (`_get_users_db`) | users.db | ✗ | ✗ | per-call | **로그인/인증 경로** |
| `worker_app.py:89` | queue.db | ✗ | ✗ | per-call | 베어러 토큰 검증 |
| `core/avatar_mesh.py:75` | avatar.db | ✓ | ✗ | per-call | 잡 상태 polling |
| `core/cardiac_ep.py:57` | avatar.db | ✓ | ✗ | per-call | 잡 상태 polling |
| `routes/meeting.py:53` / `story.py:162` / `novel.py:437` / `counseling.py:102` | 각 db | ✓ | ✗ | per-call | WAL은 있으나 timeout 무방비 |

> 표준 준수(thread-local·WAL·busy_timeout)는 `notice_db.py:77`·`dur.py:111`·`drug_info.py:66`·
> `routes/medical.py:350` 등에서 이미 적용됨 — 이들을 레퍼런스로 통일.

**위험 모델**: 위성 DB는 대부분 **read-mostly**(쓰기는 ingest 스크립트 시점)라 평시 영향은 작다. 그러나
busy_timeout 미설정 시 **동기화/ingest 동시 진행 중** 읽기가 5s 기본 대기 후 `SQLITE_BUSY`로 즉시 실패할
수 있다(WAL 누락이면 reader↔writer 차단까지). 96 스레드 환경에서 락 대기가 스레드를 점유하면
과거 SSE 스레드 고갈 패턴과 유사한 처닝 위험. → **방어적 일관화가 핵심 가치**(성능보다 안정성).

### 1-2. 인덱스 커버리지 [일부 측정필요]

| 후보 인덱스 | 쿼리 위치 | 테이블 규모 | 근거 |
|-------------|-----------|-------------|------|
| `avatar_jobs(status)` | `core/avatar_mesh.py:283` GROUP BY status | 소(잡 수) | [검증됨] 인덱스 無·매 polling 풀스캔(소규모라 절대비용 작음) |
| `cardiac_ep_jobs(status)` | `core/cardiac_ep.py:217` GROUP BY status | 소 | [검증됨] 동일 |
| `drug_substitution(ingr_code, eligible, price)` | `core/drug_substitution.py:131-136` WHERE+ORDER BY | ~11K | [검증됨] `idx_sub_ingr`(ingr_code)만 존재. WHERE에 eligible/rep_code 추가, `ORDER BY (price IS NULL), price` — **표현식 정렬이라 단순 인덱스로 정렬 완전커버 불가**(부분 효과만, 측정 후 판단) |
| `orchestrator_runs(status, created_at)` | `routes/dashboard_api.py:565-568` COUNT WHERE | [측정필요 행수] | [검증됨] 테이블 존재(`orchestrator.py:115`)하나 **인덱스 정의 無** → status·created_at COUNT 2회가 무인덱스. 행수만 측정 후 추가 |
| `drug_master` 5-way OR | `core/drug_master.py:91` `WHERE std_code=? OR product_code=? OR ...` | ~305K | [검증됨→인덱스 양호] `std_code`=PK(자동인덱스)+나머지 4컬럼 `idx_dm_*` 보유 → **5개 OR 분기 전부 인덱스됨** → SQLite OR-union 적용 가능성 높음. **EXPLAIN로 SCAN 출현 확인 전 인덱스 추가 금지**(현재로선 불요 추정) |

### 1-3. LIKE 스캔 & 집계 핫패스 [검증됨 구조 / 측정필요 비용]

- **LIKE 선행 와일드카드**(`%term%` = 풀스캔): `core/drug_info.py:124`(drug_easy_info.name, ~4.8K, exact 실패 시 폴백)·
  `core/hr_law.py:74-84`(law_documents title/content/tags 다중 LIKE)·`core/dur.py:287`(dur_split, `_looks_like_sr` 게이트라 저빈도).
- **미캐시 집계 엔드포인트**: `routes/queue.py:575` `/api/queue/stats`(완료잡 COUNT/SUM/AVG+GROUP BY model, **TTL 캐시 없음**)·
  `routes/notice.py:842` `/api/notice/drug-data/status`(fee/sub/drug_master/dur 4개 stats 순차, **캐시 없음**).
- **N+1**: `core/dur.py:448-472` `check(drugs)` — 약물 N개 각각 `_resolve_one`이 3~5개 SELECT → 10약물=30~50쿼리.
  이어 쌍별 금기검사 O(N²)(각 LIMIT 1). 소규모 N이라 절대지연은 작으나 배칭 여지.
- **이미 최적화 확인**: api_activity(비동기·상한)·chat history(LIMIT 10·인덱스)·corpus-status(60s)·dashboard summary(10s+ETag)·
  orchestrator/nodes(인메모리). → **건드리지 않음**.

---

## 2. 작업 분류 (우선순위)

| P | 작업 | 유형 | 위험 | 효과 | 노력 |
|---|------|------|------|------|------|
| **P0-1** | 인증/요청경로 연결 PRAGMA 표준화(users.db·clinic_codes 등) | 안정성 | 낮음 | 락경합·BUSY 실패 제거 | S |
| **P0-2** | 위성 DB 공용 연결 헬퍼 도입(`core/db.tune_connection` — §5와 이름 통일·2026-08-26) | 리팩터 | 낮음 | 표준 일원화·재발방지 | M |
| **P1-4** 🆕 | ANALYZE/PRAGMA optimize — 옵티마이저 통계(ingest/sync 후·기동 시) | 통계 | 낮음 | **최고 ROI**·인덱스 작업 전제 | S |
| **P1-1** | `/api/queue/stats` TTL 캐시(30s) | 캐싱 | 낮음 | 집계 풀스캔 반복 제거 | S |
| **P1-2** | `/api/notice/drug-data/status` TTL 캐시(60~300s) | 캐싱 | 낮음 | 4-DB 집계 반복 제거 | S |
| **P1-3** | `EXPLAIN QUERY PLAN` 실측 → 인덱스 확정 추가(orchestrator_runs 등, drug_master는 불요추정) | 인덱스 | 낮음 | 핫쿼리 인덱스화 | M |
| **P2-1** | avatar/cardiac 잡 polling: status 인덱스 + 카운트 TTL | 인덱스/캐시 | 낮음 | polling 풀스캔 제거 | S |
| **P2-2** | `dur.check()` N+1 → IN() 배칭 | 쿼리 | 중간 | 30~50쿼리→소수 | M |
| **P2-3** 🆕 | 위성 DB WAL 체크포인트(dur_sync clear+replace 후) | 유지보수 | 낮음 | WAL 비대 방지 | S |
| **P3** | drug_info/hr_law LIKE → FTS5 검토(선택) | 인덱스 | 중간 | 퍼지검색 가속 | M |

> S=반나절, M=1~2일 규모(검증·배포 포함). 위험 "중간"은 동작 동등성 회귀가 필요한 항목.
> ⚠️ **착수 전 §7-5 베이스라인 측정 선행** — ROI 없는 작업(특히 P1-3 인덱스)을 거른다. 실행순서는 §7-7.

---

## 3. 작업 상세

### P0-1. 인증/요청경로 연결 PRAGMA 표준화 [핵심]
**대상**: `app.py:4490`(users.db·로그인)·`worker_app.py:89`(토큰)·`core/medical_validator.py:22`·
`core/quality.py:176,256`·`core/hr_law.py:33`.
**변경**: 각 `sqlite3.connect(...)` 직후 `busy_timeout=5000` + (쓰기 가능 DB는)`journal_mode=WAL` +
`synchronous=NORMAL` 추가. **읽기전용 위성 DB**(clinic_codes 등 평시 read-only)는 최소 busy_timeout만으로도 충분.
**비목표**: 연결 수명(per-call→thread-local) 변경은 P0-2에서 일괄 — 여기선 PRAGMA만.
**검증**: 각 경로 단위 호출 200 OK 회귀 + 로그인/토큰 정상 + ingest 동시 진행 중 읽기 BUSY 미발생.

### P0-2. 공용 연결 헬퍼 (재발 방지)
**신설**: `core/db.py`에 `tune_connection(conn, *, wal=True, write=True)` — 표준 PRAGMA 세트 1곳 정의.
모든 위성 모듈의 connect가 이 헬퍼를 호출하도록 점진 교체(per-call도 헬퍼 경유). thread-local 패턴은
`notice_db`/`dur`/`drug_info`를 레퍼런스로 통일.
**효과**: PRAGMA 누락이 코드리뷰 없이도 구조적으로 차단(신규 DB 모듈도 헬퍼 강제).
**위험**: 광범위 touch → **모듈별 분할 PR**, 각 PR마다 회귀 그린 확인 후 머지.

### P1-1 / P1-2. 집계 엔드포인트 TTL 캐시 [검증됨: 현재 무캐시]
`core/cache.py`의 TTLCache 패턴(이미 LLM/이미지 캐시에 사용)을 재사용.
- `/api/queue/stats`(`routes/queue.py:575`): 30s TTL. 완료잡 누적 통계는 30s 지연 무해.
- `/api/notice/drug-data/status`(`routes/notice.py:842`): 60~300s TTL. 약물 데이터는 ingest 시점만 변동.
**검증**: 첫 호출 후 TTL 내 동일응답·DB 쿼리 0회(로그 카운트), TTL 경과 후 갱신.

### P1-3. EXPLAIN 기반 인덱스 확정 [측정 선행 필수]
**절차**: GPU에서 실 DB 대상 `EXPLAIN QUERY PLAN`으로 아래 확인 후에만 인덱스 추가:
1. `drug_master.resolve` 5-way OR → `SCAN` 출현 여부(OR-union이면 인덱스 불요·단정 금지).
2. `drug_substitution.lookup` → ingr_code 후 잔여필터/정렬이 `USE TEMP B-TREE`인지.
3. `orchestrator_runs` COUNT WHERE → 풀스캔 여부 + 테이블 행수.
**원칙**: 305K 같은 대형 read-mostly 테이블은 인덱스 추가비용 작음. 단 **측정 없는 인덱스 추가 금지**
(쓰기 ingest 비용·디스크 증가 trade-off). 추가 시 `CREATE INDEX IF NOT EXISTS`로 멱등.

### P2-1. 잡 polling 최적화 [검증됨: 인덱스 無·매콜 GROUP BY]
`avatar_mesh.py:283`·`cardiac_ep.py:217`의 `GROUP BY status`. 테이블 소규모라 절대지연은 작으나,
polling 빈도가 높을 수 있어: (a) `idx_*_jobs_status` 추가(멱등 마이그레이션), (b) 카운트 결과 짧은 TTL 캐시.
저위험·저노력. 단독 잡 상태조회(`GET /<job_id>`)는 PK 조회라 무관.

### P2-2. dur.check() N+1 배칭 [동작 동등성 회귀 필수]
`core/dur.py:448-472` `_resolve_one` 루프를 약물명 일괄 `IN()` 1쿼리로 전환(성분/품목/상호작용명 조인).
**위험**: 해석 우선순위(품번→품명→성분명) 동작이 미묘 → **결과 동등성 회귀**(test_dur 18 기준 동일 출력) 후 머지.
약물 N이 보통 작아 효과는 제한적이므로 P2(여유 시).

### P3. LIKE→FTS5 (선택)
`drug_info.name`·`hr_law` LIKE 폴백을 FTS5로. 단 drug_info는 4.8K 소형이고 exact 우선·5건 제한이라
실익 작음. hr_law는 medical_law RAG로 대부분 대체됨. **저실익 → 명시적 요청 시에만**.

---

## 4. 검증 방법론

1. **EXPLAIN QUERY PLAN**: 인덱스 추가 전후 GPU 실 DB로 비교(`SCAN`→`SEARCH ... USING INDEX` 전환 확인).
2. **회귀 테스트**: 기존 647 passed 유지 + 신규 캐시/배칭 동등성 케이스 추가. iMac numpy·순수로직 우선.
3. **부하 점검**: 대상 엔드포인트 연속 호출 시 DB 쿼리 카운트(캐시 적중)·p95 지연 비교. 과도 측정 불요(가벼운 before/after).
4. **라이브 안전**: GPU pull→`kill -HUP`(graceful reload, preload_app=False) 또는 systemd 재기동.
   health 200·NRestarts 0·`ollama ps` 무영향 확인. 동시 ingest 중 BUSY 로그 0건.

---

## 5. 비목표 / 리스크

- **비목표**: queue.db 재튜닝(이미 최적)·api_activity 구조변경·gunicorn worker 증설(workers=1 의도적,
  preload_app fork 교훈)·FastAPI 전환(별도 백로그)·ChromaDB/임베딩(별도 영역)·DB 엔진 교체.
- **리스크 R1**: 광범위 PRAGMA touch → 모듈별 분할·회귀 그린 게이트로 완화.
- **리스크 R2**: 인덱스 과다 추가 → 쓰기/디스크 비용. EXPLAIN 측정 게이트로 차단.
- **리스크 R3**: 캐시 TTL이 실시간성 해침 → 집계/상태 엔드포인트에만 적용(쓰기·의료판정 경로 제외).
- **리스크 R4**: ChromaDB link_lists 인시던트 교훈 — 본 계획은 SQLite만 대상, ChromaDB 미접촉.

---

## 6. 실행 체크리스트

- [ ] **§7-5 베이스라인 측정 선행**(p50/p95·EXPLAIN·행수) — 코드 변경 전
- [ ] P1-4 🆕 ANALYZE/PRAGMA optimize(ingest/sync 후·기동 시) — 최우선 ROI
- [ ] P1-1 `/api/queue/stats` 30s TTL 캐시
- [ ] P1-2 `/api/notice/drug-data/status` 60~300s TTL 캐시
- [ ] P0-1 인증/요청경로 5곳 PRAGMA 하드닝 + 회귀 (성능 아님=신뢰성, §7-4)
- [ ] P0-2 `core/db.tune_connection` 헬퍼 + 위성모듈 점진 교체(분할 PR)
- [ ] P1-3 EXPLAIN 실측 → orchestrator_runs/drug_substitution 인덱스 확정(개선폭 미미시 보류)
- [ ] P2-1 avatar/cardiac jobs status 인덱스 + 카운트 TTL
- [ ] P2-2 dur.check() IN() 배칭(동등성 회귀)
- [ ] P2-3 🆕 위성 DB WAL 체크포인트(dur_sync 후)
- [ ] (P3 선택) drug_info/hr_law FTS5 — 요청 시
- [ ] 릴리즈 문서화(docs/RELEASE_*) + CLAUDE.md 헤더 갱신

---

## 7. 검토 보완 (2026-06-23) — 검증 결과·신규 항목·정직 교정

초안의 **[측정필요]** 항목을 코드 직접 확인으로 검증하고, 누락 영역 2종을 추가하며, P0 가치를
과장 없이 재교정한다.

### 7-1. 검증 결과 (초안 가정 → 확정)
| 항목 | 초안 | 검증 결과 |
|------|------|-----------|
| orchestrator_runs 인덱스 | 측정필요 | **무인덱스 확정**(`orchestrator.py:115` CREATE TABLE만, INDEX 정의 無) → P1-3 인덱스 후보 승격, 행수만 측정 |
| drug_master OR 풀스캔 | 풀스캔 의심 | **인덱스 양호로 정정** — std_code=PK + 4컬럼 idx → OR 분기 전부 인덱스. **인덱스 추가 불요 추정**(EXPLAIN 확인만) |
| avatar/cardiac jobs status 인덱스 | 검증됨 | **재확인 — 무인덱스**(CREATE TABLE만). 단 테이블 소규모라 절대비용 작음 |
| queue_stats 캐시 | 검증됨 | **재확인 — 캐시 데코레이터 無**(`queue.py:575` raw route) |
| 위성 DB 쓰기 윈도우 | 추정 | **확정** — `dur_sync.py:87-94`가 `clear=True` 전량삭제+재삽입(7일 주기 daemon). dur.db/drug_info.db의 유일 쓰기 시점 |
| clinic_codes 쓰기 | 가정 | 요청 경로 read-only(ingest 스크립트로만 적재) → P0 BUSY 위험은 **방어적 수준**(실시간 쓰기 없음) |

### 7-2. 🆕 P1-4. ANALYZE / PRAGMA optimize — 옵티마이저 통계 [큰 누락]
**발견 [검증됨]**: 코드베이스 전체에 `ANALYZE`·`PRAGMA optimize` **호출 0건**(wal_checkpoint는 queue.db만).
SQLite 옵티마이저는 `sqlite_stat1` 통계가 있어야 다중 인덱스·OR-union·복합 WHERE에서 올바른 인덱스를
고른다. 통계 부재 시 휴리스틱에 의존 → 본 계획의 인덱스 효과 자체가 저평가될 수 있다.
**특히 `dur_sync`가 수천 행을 clear+replace한 직후 통계가 완전 노후화** → 인덱스 선택 열화.
**처방**(저비용·고효과):
- 각 ingest/sync 완료 직후 해당 conn에 `ANALYZE`(또는 변경 테이블 한정). `dur_sync._import` clear+insert 후 1줄.
- 장수명 연결(thread-local)에 주기적 `PRAGMA optimize`(SQLite 권장 패턴, 연결 종료/유휴 시).
- 기동 시 위성 DB 1회 `ANALYZE`(stat1 없으면 생성).
**위험**: 거의 없음(통계는 읽기전용 메타). ANALYZE는 대형 테이블서 수백ms 소요 가능 → ingest 후/백그라운드에서만.
**검증**: ANALYZE 전후 동일 쿼리 `EXPLAIN QUERY PLAN` 비교(인덱스 선택 개선 확인).

### 7-3. 🆕 P2-3. 위성 DB WAL 체크포인트 (bulk sync 후)
**발견 [검증됨]**: queue.db만 `_wal_checkpoint`(60s, `app.py:4616`) 보유. WAL 쓰는 위성 DB
(notice/dur/drug_info)는 체크포인트 관리 無. `dur_sync` clear+replace가 WAL을 일시 비대시킬 수 있음.
**처방**: `dur_sync._import` 성공 후 `PRAGMA wal_checkpoint(TRUNCATE)` 1회(ANALYZE와 동반). 저빈도(7일)라
긴급도 낮음. **passive autocheckpoint가 평시 처리하므로 P2**(여유 시).

### 7-4. P0 가치 정직 재교정 (성능 ❌ → 안정성 ✅)
초안 P0의 효과를 "락경합·BUSY 제거"로 적었으나, **냉정히 SQLite connect는 ~0ms**(메모리 기록:
"격리 connect는 0ms")이고 대상 DB는 요청경로 read-only다. 따라서:
- **per-call 재연결 오버헤드는 성능 이슈가 아님** → "재연결 비용 절감"을 P0 근거로 쓰지 말 것.
- P0의 **진짜 가치 = 방어적 일관성**: busy_timeout 누락 연결이 **유일 쓰기윈도우**(dur_sync·수동 ingest)와
  겹칠 때 5s 후 SQLITE_BUSY로 즉시 실패하는 경로를 막는 것. 빈도는 낮지만 의료 검증 경로(quality·
  medical_validator)의 무방비 실패는 silent 오판정 위험이라 **하드닝 가치는 유효**.
- 결론: P0는 "성능 개선"이 아니라 **"드문 동시쓰기 창에서의 신뢰성 하드닝"**으로 명명. 기대 p95 변화 ≈ 0.

### 7-5. 베이스라인 측정 & 성공기준 (초안 누락)
**측정 우선(코드 변경 전)**: 변경 없이 먼저 수집해 ROI 없는 작업을 거른다.
1. 대상 엔드포인트 30회 연속 호출 p50/p95(`/api/queue/stats`·`/api/notice/drug-data/status`).
2. GPU 실 DB에서 §3 핫쿼리 `EXPLAIN QUERY PLAN` + `.timer on` 단건 실측.
3. 위성 DB 행수(`orchestrator_runs`·`drug_substitution` 등)로 풀스캔 실비용 산정.
**성공기준(측정 후 확정)**:
- P1 캐시: 동일 엔드포인트 TTL 내 DB 쿼리 0회 + p95 캐시적중 < 5ms.
- P1-3 인덱스: EXPLAIN `SCAN`→`SEARCH ... USING INDEX` 전환 + 단건 지연 측정 개선. **개선폭 미미하면 추가 보류**(인덱스 부채 회피).
- 전 작업: 회귀 647 passed 유지·health 200·NRestarts 0·동시 sync 중 BUSY 로그 0.

### 7-6. 연결 수 sizing (참고)
gthread threads=96 × thread-local 연결 = DB당 최대 ~96 연결 보유. 위성 DB를 thread-local로 통일하면
DB 종수 × 96 fd. 현 fd 한계 내지만, **P0-2 헬퍼 적용 시 thread-local 일괄 전환은 fd 증가**를 동반하므로
per-call이 더 나은 저빈도 DB(avatar/cardiac 잡)는 thread-local로 굳이 바꾸지 않는다(연결 재사용 이득 < fd 비용).

### 7-7. 보완 후 우선순위 재정렬
1. **측정 먼저**(7-5) — 코드 변경 전 베이스라인·EXPLAIN.
2. **P1-4 ANALYZE**(7-2) — 최저비용·최고 ROI(인덱스 작업의 전제조건이기도).
3. **P1-1/P1-2 캐시** — 즉효·저위험.
4. **P0 하드닝**(7-4 명명대로) — 신뢰성, 분할 PR.
5. **P1-3 인덱스** — EXPLAIN 근거 있을 때만(drug_master는 불요 추정).
6. **P2 배칭·체크포인트·잡 인덱스** — 여유 시.

---

## 8. 측정 결과 & 실행 현황 (2026-06-23, GPU 실 DB)

### 8-1. 베이스라인 측정(§7-5 실행) — 추측 작업 대거 컷
| 측정 대상 | 결과 | 결정 |
|-----------|------|------|
| `sqlite_stat1`(전 위성 DB) | **부재**(ANALYZE 0회) | P1-4 정당화(단 플래너는 통계 없이도 양호) |
| drug_master 5-way OR EXPLAIN | **MULTI-INDEX OR**(5 인덱스 전부, std_code=PK) | 인덱스 불요 확정 — **컷** |
| orchestrator_runs | SCAN이나 **22행** | 인덱스 무의미 — **컷** |
| avatar_jobs / cardiac_ep_jobs | GROUP BY지만 **13 / 6행** | 인덱스 무의미 — **컷** |
| drug_substitution.lookup | `idx_sub_ingr` 사용 + ORDER BY temp b-tree(11K 중 동일성분 소수만) | 복합인덱스 이득 미미 — **컷** |
| `/api/queue/stats` | jobs 2,149행·`idx_jobs_completed`·**8ms** | 핫스팟 아님 + ETA/recent staleness 위험 → **P1-1 컷** |
| `/api/notice/drug-data/status` | 4-DB 집계 **153ms→53ms** | **유일 실제 느린 엔드포인트 → P1-2 채택** |
| queue.db 파일 | 2,149행에 **192MB**(free-page bloat) | VACUUM 후보(별도 백로그, 무관) |

> **측정 선행의 성과**: 초안 P1-3(인덱스 4종)·P2-1(잡 인덱스)·P1-1(stats 캐시)이 **전부 ROI 음수로 확정 컷**.
> 인덱스 부채 없이 가치 있는 3건만 남김 — "측정 없는 인덱스 추가 금지" 원칙이 그대로 작동.

### 8-2. 구현(as-built) — 측정 정당화된 3건만
| 작업 | 변경 | 상태 |
|------|------|------|
| **P1-2** 캐싱 | `routes/notice.py` `_DRUG_DATA_STATUS_CACHE`(TTLCache 120s)로 drug-data/status 래핑 | ✅ 구현·로컬검증 |
| **P0** 하드닝 | `medical_validator.py:22`·`quality.py:176,256`·`hr_law.py:33`·`app.py`(users.db, +WAL)·`worker_app.py` busy_timeout=5000 추가 | ✅ 구현·로컬검증 |
| **P1-4** ANALYZE | `dur_sync.refresh_all` 성공 후 `_analyze_after_sync`(dur.db·drug_info.db ANALYZE) | ✅ 구현·로컬검증 |

**컷(측정 근거)**: P1-1(8ms·staleness)·P1-3(인덱스 전부 미미)·P2-1 인덱스(소규모)·P2-2(N+1, 소규모 N)·P2-3(autocheckpoint 충분)·P3.

⚠️ **정정(2026-08-26·X11)**: **P0-2(`tune_connection` 헬퍼)는 구현도 컷도 아닌 채 이 목록에서 증발**했었다 — status ✅인 문서에서 유일하게 행방 없는 항목. §8-1 측정은 P0-2를 다루지 않았으므로 "측정으로 컷"에 해당하지 않는다. 현재 상태=**미구현 잔여**(PRAGMA는 `get_db()` 인라인이 실질 표준·§7의 "thread-local 일괄 전환은 fd 증가 동반" 지적이 보류 근거로 유효). 착수 시 이 기획서 §4 P0-2 사양으로.
**로컬 회귀**: dur 23 + drug_substitution/drug_info 14 + quality/medical_validator 60 = **97 passed**. py_compile 8파일 OK.

### 8-3. ✅ GPU 라이브 검증 완료 (2026-06-23, 브랜치 `perf/sql-tuning-p0-p1` 배포)
import 드라이런 OK → graceful HUP reload(MainPID 77188, NRestarts 0 유지) → 라이브:
- **P1-2 캐시 입증**: `/api/notice/drug-data/status` #1 **53.7ms(콜드) → #2 1.08ms → #3 0.8ms**(캐시적중 ~50×).
- **P1-4 ANALYZE 입증**: `_analyze_after_sync()` 실행 후 dur.db `sqlite_stat1` **0→15행** 생성.
- **P0**: busy_timeout 코드 배치(users.db WAL 포함). 로그인 미발생이라 wal 파일은 미생성(정상, 접근 시 활성).
- health 200@5ms·NRestarts 0·**전체 회귀 647 passed/7 skipped**(v2.109.0 베이스라인 동일, 부수영향 0).
- **GPU 현 상태**: feature 브랜치 실행 중(검증 코드 라이브). PR 머지 후 main 복귀 → 그때 main에 반영.

---

## 부록 A. 조사 출처 (file:line)

연결: `core/db.py:27`·`medical_validator.py:22`·`quality.py:176,256`·`hr_law.py:33`·`app.py:4490`·
`worker_app.py:89`·`avatar_mesh.py:75`·`cardiac_ep.py:57`.
쿼리: `drug_master.py:91`(OR)·`drug_substitution.py:131-136`·`drug_info.py:124`(LIKE)·`dur.py:287,448-472`·
`avatar_mesh.py:283`·`cardiac_ep.py:217`.
캐시/핫패스: `app.py:1443-1457`(activity)·`routes/queue.py:575`·`routes/notice.py:842`·
`routes/dashboard_api.py:543-642,565-568`·`routes/medical_law.py:637-679`·`core/cache.py`·`gunicorn.conf.py:8-34`.
