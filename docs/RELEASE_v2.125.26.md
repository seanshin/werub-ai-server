# v2.125.26 — 원격 노드 프록시 502가 이 서버의 건전성 지표를 오염시키던 것 봉합

날짜: 2026-08-31 | 발견: 라이브 로그 판독(세션 시작 상태 점검) — 백로그에 없던 결함

## 1. 결함 (라이브 실측으로 확정)

iMac이 **2026-08-29 03:09 ~ 08-31 09:36 KST(54시간)** 미도달인 동안,
`/api/mac/stats`·`/api/mac/health`가 **502를 31,117건** 반환했다(journald 접근로그 실측).

`routes/system.py:_cached_proxy`가 원격 노드 미도달을 502로 돌려주는데, 그 502가
`app._activity_log → metrics_reservoir.record()`를 거쳐 **이 서버 자신의 5xx**로 기록됐다.
결과:

| 축 | 실측 |
|---|---|
| `error_rate()` | **33%** (`stats_history.error_rate_pct` 33.02~33.52, 워커 재활용마다 0→33 재상승) |
| `Error Rate Spike` 경보 | **534건 / 43시간 연속**(`_perf_alerts`, 임계 10%) |
| 그 기간 이 서버 자신의 5xx | 0건 (journald 5xx는 전량 `/api/mac/*`) |

🔑 **진짜 비용은 경보 소음이 아니다** — 그 43시간 동안 이 서버의 진짜 5xx가 났다면
33% 바닥에 묻혀 안 보였다. 오케스트레이터 불변식("노드 미도달 = 기존 동작 100%")과도
정면 충돌한다: 관찰만 하는 노드가 죽은 것을 자기 고장으로 센다.

**같은 클래스 전 경로**: `_cached_proxy`를 쓰는 원격 프록시는 gpu·prod·his·mac
**4계열 10엔드포인트** 전부다. mac만 고치면 반쪽 처치다.
⚠️ 부수 확인: GPU 서버에서 `/api/gpu/{stats,health}`는 **상시 502**다 —
`GPU_STATS_URL=127.0.0.1:9102`가 iMac에만 있는 터널이라 GPU 자신에겐 없는 주소다.
(같은 코드베이스가 양쪽에서 도므로 구조상 그렇다. 이 릴리즈의 라이브 통제군으로 썼다.)

## 2. 위양성이 아닌 것 — 쿨다운은 정상 작동했다

착수 초기에 "쿨다운 300s == 수집 주기 300s라 억제가 무효"라고 판단했으나 **틀렸다**.
`_perf_alerts`는 `/api/health` 핸들러에서 평가되고 대시보드가 **5초마다** 폴링한다 —
300s 쿨다운은 5초를 5분으로 억제하고 있었다(534건 ÷ 43h = 4.8분/건 = 설계대로).
**결함은 억제가 아니라 조건 자체가 위양성인 것**이라, 쿨다운은 건드리지 않는다.

## 3. 처치

**계약은 그대로 두고 지표만 고친다.** 502는 게이트웨이 의미상 맞고, 화면 3개
(`dashboard.html`·`dashboard_all.html`·`monitor.html`)는 `r.ok`를 보지 않고 `d.checks`만
읽으므로 상태 코드를 바꿀 근거가 없다(효과 미확인 처치 금지).

- `core/metrics_reservoir.record(path, status, ms, upstream=False)` — `upstream=True`의
  5xx는 **`5xx_upstream` 별도 계급**으로 센다. `error_rate()`(=서버 건전성 신호)에서 빠지되
  **버리지는 않는다**(무성 소실 금지). 분모(`_total`)·지연 리저버에는 그대로 남는다 —
  프록시 요청도 스레드를 똑같이 점유했기 때문.
- `core/metrics_reservoir.upstream_error_rate()` 신설 — 원격 노드 상태를 자기 건전성과
  **다른 이름으로** 노출.
- `routes/system.py:_cached_proxy` — 표시를 **공유 헬퍼 한 곳**에 둔다(4계열 10엔드포인트가
  전부 이 헬퍼를 쓰므로 구조적으로 전 경로가 덮인다). 예외 분기가 아니라 **최종 status에서
  판정**한다 — TTL(4s) 캐시 히트분도 502를 돌려주므로 예외 분기에만 두면 놓친다.
- `app.py:_activity_log` — 표시를 `record(upstream=...)`로 전달.
  (표시만 하고 안 넘기면 효과 0 — "릴리즈 노트가 코드보다 앞선다" 클래스라 회귀로 고정했다.)
- 노출: `/metrics`에 `ollama_http_upstream_error_rate_pct`, `_perf_alerts`의 `perf` dict에
  `upstream_error_rate_pct`. `ollama_http_error_rate_pct`의 HELP 문구도 정정 —
  종전 "(rolling)"은 창 기반이라는 뜻으로 읽히나 실제로는 **워커 기동 이후 누적**
  (`max_requests=5000` 재활용마다 리셋)이다.

## 4. 검증

- 신규 회귀 14건 `tests/test_upstream_proxy_error_isolation.py`:
  - **실측 형상 재현** — 2000×200 + 1000×502(upstream) = 종전 33.33%였던 그 비율에서
    `error_rate()==0.0`·`upstream_error_rate()==33.33`·임계 10 미만.
  - **음성 통제** — 이 서버 자신의 5xx는 여전히 잡힌다(`/api/rag/query` 500 → 10.0%).
    탐지기를 무디게 만든 게 아니라는 대조군(v2.125.15 규율 #2).
  - 기본값 안전(`upstream` 미전달 = 종전 동작) · 2xx/4xx로 분류가 번지지 않음 ·
    분모/지연에서 빼지 않음 · 표시가 공유 헬퍼에 있고 개별 라우트에 흩어져 있지 않음 ·
    캐시 해석 **이후**에 판정 · 502 계약 불변 · `_activity_log`가 실제로 플래그 전달 ·
    `/metrics`·perf 노출.
- `tests/test_metrics_reservoir.py::test_snapshot_shape` 갱신(스냅샷 키 집합 계약에 신규 필드).
- 전체 회귀 **1976 passed · 9 skipped · 1 xpassed · 0 failed**(종전 1962 + 신규 14).

## 4-b. 배포·라이브 검증 (2026-08-31)

HEAD `455a6bc` · 라이브 `2.125.26` · 실 DB import dry-run OK · graceful 재기동 health 200·NRestarts=0.
배포 로그 회귀 `1972 passed · 13 skipped · 1 xpassed`(게이트가 GPU를 점유하는 동안 환경 조건부
skip이 4건 늘어난 값 — **정본은 클린 실행 1976 passed · 9 skipped**) · 게이트 스모크 **177/177** · ❌ 0건.

**라이브 통제군**: GPU 서버에서 `/api/gpu/health`는 상시 502다(`GPU_STATS_URL=127.0.0.1:9102`
터널이 iMac에만 있음) — 실제 502를 만들 수 있는 자연 통제군.

| | 20× `/api/gpu/health` 호출 전 | 호출 후 |
|---|---|---|
| `ollama_http_error_rate_pct` | 0.0 | **0.0** (불변 — 자기 건전성 청결) |
| `ollama_http_upstream_error_rate_pct` | 0.0 | **3.22** |
| `ollama_http_requests_total{status="5xx"}` | (없음) | **(없음)** |
| `ollama_http_requests_total{status="5xx_upstream"}` | (없음) | **20** |

⇒ 502 20건이 **자기 5xx에서는 빠지고 upstream 계급으로는 보인다**. 종전이면 20/621 = 3.22%가
`error_rate`에 실렸고, 인시던트 구간에서는 그 값이 33%였다.

⚠️ **양성 통제(자기 5xx가 여전히 잡히는지)는 라이브가 아니라 회귀에서만 확인했다**
(`test_own_5xx_still_counted`·`test_default_is_own_error`) — 프로덕션에 고장을 주입하지 않았다.

## 5. 남긴 것

- **경보 임계 자체는 안 건드렸다** — 이번 오염이 걷히면 `error_rate_pct=10`이 실제
  자기 5xx에 대해 적절한지는 **다음 관측 구간의 데이터로** 판정할 일이다(측정 전 기준 고정 규율).
- `/api/gpu/*`가 GPU 서버에서 상시 502인 것은 **구조상 그렇다**(터널이 iMac에만 있음).
  화면에서 GPU 서버 자신을 볼 때 그 카드가 Offline으로 뜨는 것이 옳은지는 별건
  (지표 오염은 이 릴리즈로 해소됐으므로 긴급하지 않다).
- 같은 세션 상태 점검에서 함께 드러난 별건 2건(이 릴리즈 범위 밖):
  - 🔴 **medlaw dedup 가드가 80일째 차단 중** — 2026-06-12부터 17회 SKIP, 삭제 대상
    2790→**3017청크**(마지막 성공 dedup 06-11). 구버전 고시가 `medical_law_knowledge`에
    남아 현행판과 검색 경합. 로그가 "수동 dedup 요망"이라 적었으나 **어느 백로그에도 없었다**.
    ⚠️ ChromaDB 디스크풀 인시던트 이력 때문에 워치독 동반 필수 + **검색 영향 측정 선행**.
  - 🟡 **API 키** — `main-web`·`imac-gateway`가 **2026-09-05 만료**(만료 시 웹·iMac
    게이트웨이 인증 단절). 이미 만료됐는데 active인 키 2건(`imageDev` 08-24·`hwpX` 07-19)
    = 백로그 A3 완료 정의 "만료 active 0" 위반.
