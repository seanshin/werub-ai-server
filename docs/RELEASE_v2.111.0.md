# Release v2.111.0 — 성능 모니터링 내부 확장 (신호수집·시계열영속·임계알림)

> 2026-06-30 | 기획 `docs/01-plan/features/performance-monitoring.plan.md` · 가이드 `docs/MONITORING_GUIDE.md`

## 요약

운영 병목(GPU/VRAM 천장·모델 스왑·워커 churn·스레드 고갈·지연/에러)이 그동안 **모니터링이
아니라 수동 라이브점검으로만 발견**되던 관측성 공백을, 기존 인프라 확장으로 봉합했다.
**외부 Prometheus/Grafana 배포 없이**(신규 의존성 0) `stats_history` DB + `/metrics` + Admin으로 완결.

## P0 — 측정 선행 (GPU 라이브 실측)

추측 작업을 컷하고 설계를 확정한 측정:

| 측정 | 값 | 결론 |
|---|---|---|
| `/metrics` 스크랩(6h) | **0건** | 노출 무소비자 → **히스토리(stats_history)가 모니터링 본체** |
| api_activity 회전율 | 2039행 = **18.5분**, ~110 req/min | 리저버 _MAX=2048(~18min) 적정 |
| distinct path | **7개** | 카디널리티 폭주 기우 — 단순 cap만 |
| nvidia-smi spawn | **0.03s** | 5min 샘플링 무부하 |
| stats_history 주기 | 300s · 30일 · 9150행 | 주기·retention 정상 |
| 워커 recycle | autorestart **~37-40min**(max_requests) + 운영자 HUP 배포 | churn 실재 → DB 영속 정당 |
| 트래픽 | `/api/notice/index-status` ~1/s = 50% | churn은 폴링 구동(별건) |

**🔑 설계 전환**: gunicorn `workers=1` + `max_requests=5000` → 워커 ~40분 recycle → 인메모리 상태
전부 리셋. 외부 스크래퍼가 없으므로 Prometheus 카운터는 무용 → **수집기(`_collect_stats` 5min) +
히스토리 테이블을 척추**로, 누적 지표는 DB delta로 산출.

## P1 — 신호 수집

- **`core/metrics_reservoir.py`** (신규, 순수 모듈): 인메모리 지연 리저버 — p50/95/99 · 5xx 에러율 ·
  상위 8 엔드포인트 카디널리티 가드 · `_MAX=2048`. `_activity_log` after_request에서 `record()`.
- **`_collect_stats` 확장**: nvidia-smi(VRAM/util/온도) · 워커 nlwp(`/proc/self/task`)+peak-tracking ·
  SSE 구독자 · `ollama /api/ps` 상주 모델 · 리저버 percentile.

## P2 — 시계열 영속

- **`stats_history` +14컬럼**(마이그레이션): vram_used/total_mb · gpu_util_pct · gpu_temp_c ·
  latency_p50/95/99_ms · error_rate_pct · worker_threads(+peak) · sse_subscribers · model_resident ·
  worker_recycles · worker_uptime_s.
- **`monitoring_meta` 테이블**: 워커 recycle 카운터 · 부팅시각. `_start_background_threads` 부팅마다
  증가 → churn 추세를 DB delta로(인메모리 카운터는 recycle마다 리셋되므로 영속).
- **`/metrics` 신규 게이지**: `ollama_http_latency_ms{quantile}` · `ollama_http_error_rate_pct` ·
  `ollama_http_requests_total{status}` · `ollama_gpu_*` · `ollama_worker_threads{,_peak}` ·
  `ollama_sse_subscribers` · `ollama_worker_recycles_total` · `ollama_worker_uptime_seconds`
  (미래 스크래퍼 대비 — 인증 게이트됨).

## P3 — 임계 자동알림

`/api/health` `_perf_alerts` — 기존 다채널(Slack/Discord/Webhook)·`alerts_history` 재사용,
config `monitoring_thresholds` 무재배포 튜닝:

| 알림 | 기본 임계 |
|---|---|
| `vram_pressure` | VRAM ≥ 96% (스왑 thrash) |
| `gpu_temp_throttle` | ≥ 83°C |
| `latency_p95_spike` | p95 ≥ 5000ms (n≥20) |
| `error_rate_spike` | 5xx ≥ 10% (n≥20) |
| `thread_exhaustion_warning` | nlwp ≥ 80 (SSE 고갈 90+ **전** 조기경보) |
| `worker_churn_anomaly` | ~30min recycle ≥ 4 (**DUR 과다동기화 class 자동 포착**) |

**worker_dead 오탐 수정**: 2회 연속 관측 게이트(`_worker_dead_streak`) — graceful recycle/HUP 배포
중 일시적 `alive=False`로 매번 critical 발화하던 노이즈 봉합(P0서 확인).

## P4 — Admin 대시보드

Dashboard "성능 모니터링" 카드 — 현재값(VRAM/온도/p95/에러율/스레드/SSE/recycle/uptime/모델) +
GPU(VRAM%·온도) 및 지연(p95·에러율) 트렌드 차트. **기존 `loadDashboardCharts` 60s 타이머 ·
`/api/stats/history` 재사용 = 신규 폴링 0**(SSE 스레드 고갈 인시던트 재발 방지).

## 검증 (GPU 라이브, HEAD bd3012f)

- dry-run import OK · GPU venv `test_metrics_reservoir` **9 passed** · graceful HUP **NRestarts=0** · health **200@47ms**.
- `/metrics` 신규 게이지 전부 노출(vram 10529/16303 · temp 39 · threads 42 · p95 578 · recycles 카운터).
- `stats_history` 최신 row 14컬럼 채워짐(model_resident=`qwen2.5:14b`).
- `/api/health` `checks.perf`={vram_pct 64.6 · p95 578 · threads_peak 42 · err 0} · worker ok=True(오탐 0).

## 비범위 / DEFER

- 외부 Prometheus/Grafana/node_exporter 배포(사용자 결정=내부 확장).
- 오케스트레이터 `metrics.snapshot()` → `/metrics` 연결(현재 위임 트래픽 0 = snapshot 빈값).
- 모델 스왑 정밀 카운트(Ollama 내부 로드 훅 불가 → 샘플 시점 게이지로 thrash 관측).
- `max_requests`/recycle 정책 변경(churn은 *관측만* — 정책 변경은 chat-행/DUR 인시던트 회귀 위험, 별건).

## 변경 파일

- 신규: `core/metrics_reservoir.py` · `tests/test_metrics_reservoir.py` · `docs/MONITORING_GUIDE.md` ·
  `docs/01-plan/features/performance-monitoring.plan.md` · `docs/RELEASE_v2.111.0.md`
- 수정: `app.py`(리저버 훅·`_collect_stats` 확장·스키마+monitoring_meta·recycle 카운터) ·
  `routes/activity.py`(/metrics) · `routes/system.py`(/api/health 알림) ·
  `templates/admin.html` · `static/js/main.js`(성능 카드+차트)
