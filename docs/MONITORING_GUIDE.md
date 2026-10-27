# 성능 모니터링 가이드 (Performance Monitoring)

> v2.111.0 신규 | 기획·측정: `docs/01-plan/features/performance-monitoring.plan.md`
> 설계 기조: 내부 확장만(외부 데몬 0) · 측정 선행 · 단일 GPU 박스 부하 최소
> 관련 인시던트 판독: `docs/INCIDENT_DIAG.md`

운영 병목(GPU/VRAM 천장·모델 스왑·워커 churn·스레드 고갈·지연/에러)을 **시계열로 가시화**하고
임계 초과 시 **자동 알림**한다. 외부 Prometheus/Grafana 없이 내부(`stats_history` DB + `/metrics` + Admin)로 완결.

## 아키텍처 — 수집기가 척추

```
요청 → after_request(_activity_log) → metrics_reservoir.record()   # 인메모리 지연/상태 (live)
worker_loop → _collect_stats() [5분]                                # 척추: 샘플→DB 박제
   ├ nvidia-smi (VRAM/util/온도, ~0.03s)
   ├ /proc/self/task (워커 nlwp + peak)
   ├ len(_monitor_subscribers) (SSE)
   ├ ollama /api/ps (상주 모델)
   └ metrics_reservoir.quantiles()/error_rate()  → stats_history(+14컬럼, 30일 보관)
```

**왜 수집기 중심인가**: gunicorn `max_requests=5000`로 워커가 ~37-40min마다 recycle → 인메모리
상태(리저버·카운터)가 리셋된다. 외부 스크래퍼가 없으므로(내부 확장 결정) **`stats_history`(DB)가
장기 추세의 본체**다. `/metrics`는 시점값 + 미래 스크래퍼 대비. 누적 지표(워커 recycle 등)는
인메모리 카운터가 아니라 **DB delta**로 산출한다(`monitoring_meta`에 영속).

## 데이터 소스

| 소스 | 내용 | 갱신 |
|---|---|---|
| `GET /metrics` | Prometheus 텍스트(큐 + GPU/지연/스레드/SSE/recycle 게이지) | 요청 시(인메모리+최신 스냅샷) |
| `GET /api/stats/history?hours=24` | 시계열 points[](신규 14컬럼 포함, `SELECT *`) | 5분 |
| `GET /api/health` `checks.perf` | 현재 perf 요약 + 임계 평가 | 요청 시 |
| Admin → Dashboard "성능 모니터링" 카드 | 현재값 + GPU/지연 트렌드 차트 | 60초(기존 차트 타이머 재사용) |

### `/metrics` 신규 게이지
```
ollama_http_latency_ms{quantile="0.5|0.95|0.99"}   # 롤링 리저버
ollama_http_error_rate_pct                          # 5xx 비율(4xx=인증 등 제외)
ollama_http_requests_total{status="2xx|3xx|..."}
ollama_gpu_vram_used_mb / ollama_gpu_vram_total_mb / ollama_gpu_util_pct / ollama_gpu_temp_c
ollama_worker_threads / ollama_worker_threads_peak  # nlwp(+스냅샷간 peak)
ollama_sse_subscribers                              # SSE 모니터 구독자
ollama_worker_recycles_total                        # 누적 워커 부팅(churn)
ollama_worker_uptime_seconds                        # 현 워커 가동시간
```

## 알림 (P3)

`/api/health`가 평가, 기존 다채널(`alert_slack_url`/`alert_discord_url`/`alert_webhook_url`)로 발송,
`alerts_history`에 기록. 임계는 **config `monitoring_thresholds` 무재배포 튜닝**(미설정 시 기본값).

| 알림 키 | 기본 임계 | 의미 |
|---|---|---|
| `vram_pressure` | VRAM ≥ 96% | 모델 스왑 thrash 위험 (16GB 천장) |
| `gpu_temp_throttle` | ≥ 83°C | GPU throttle 임박 |
| `latency_p95_spike` | p95 ≥ 5000ms (n≥20) | 응답 지연 급증 |
| `error_rate_spike` | 5xx ≥ 10% (n≥20) | 서버 오류 급증 |
| `thread_exhaustion_warning` | nlwp ≥ 80 | **SSE 스레드 고갈(90+) 전 조기경보** |
| `worker_churn_anomaly` | ~30min recycle ≥ 4 | **폴링 폭주/과다동기화 자동 포착**(DUR 버그 class) |
| `quality_index_low` (v2.124.3) | 지수 < 80 (B 미만, 쿨다운 6h) | **야간 LLM 품질 평가 회귀** — /eval-monitor 이슈 리스트 확인 (`docs/EVAL_GUIDE.md`) |
| `gpu_cpu_fallback` (v2.124.33) | `model_vram_ratio` ≈ 0 (부분 오프로드 < 0.5) | **health는 healthy인데 추론만 CPU**로 떨어지는 사각(07-25 드라이버 인시던트 class) |
| `key_expiry_soon` / `key_expired_in_use` (v2.124.31) | 만료 D-7 / 만료+최근 사용 | **조용한 통합 단절 방지**(07-19 appServer 3일 401 폭주). 휴면 키 만료는 무경보 |
| `meeting_backlog` / `meeting_queue_full` (v2.124.54) | 대기열 ≥ 임계(기본 상한/3) / 상한 도달 | **일괄 업로드 몰림 자동 감지** — 사전 통지에 의존하지 않는다. 상한 도달은 신규 접수 503 |

```jsonc
// config.json 예시 (선택)
"monitoring_thresholds": {
  "vram_pressure_pct": 96, "gpu_temp_c": 83, "latency_p95_ms": 5000,
  "error_rate_pct": 10, "worker_threads_warn": 80, "worker_recycles_per_30min": 4,
  "quality_index_min": 80, "gpu_fallback_min_vram_ratio": 0.5,
  "key_expiry_warn_days": 7, "meeting_backlog_warn": 16
}
```

**worker_dead 오탐 수정**: graceful recycle/HUP 배포 중 일시적 `alive=False`는 1회 관측으로
알림하지 않고 **2회 연속 관측** 시에만 발송(`_worker_dead_streak`). 평시 recycle(~40min)마다
critical이 발화하던 노이즈 제거.

## 판독 가이드

- **워커 churn**: `ollama_worker_recycles_total` delta가 ~30min 4회↑ → 폴링 과다(예: `/api/notice/index-status`
  ~1/s가 traffic 50%) 또는 max_requests 조기 도달. recycle은 DUR-sync 등 startup 작업을 재발화시킨다.
- **스레드**: 평시 nlwp ~42(AI 판별 ML 사용 워커는 ~50). 80 경보·90+ 고갈(전 엔드포인트 HTTP 000).
  py-spy 스택이 SSE `generate()`/`q.get` 다수면 고갈 확정 → `docs/INCIDENT_DIAG.md`.
- **VRAM**: 단일 LLM 상주 시 60~89%가 정상. 96%+ 지속 = 스왑 압박(MAX_LOADED=1, 16GB 천장).
  근본해결은 32GB 박스 — 튜닝 아님.
- **지연 p50≈0**: index-status/health 등 캐시 응답 지배. p95가 실 서빙(`/api/chat`) 지표.

## 한계 / 비범위

- 모델 스왑은 5분 샘플 시점의 `model_resident` 게이지 — Ollama 내부 로드를 훅하지 않으므로 정밀
  스왑 카운트는 아님(thrash 패턴 관측용).
- 인메모리 리저버는 워커 recycle(~40min)마다 리셋 — *최근* 트래픽 percentile이 목적(추세는 DB).
- 외부 Prometheus/Grafana 배포는 비범위(`/metrics`는 향후 스크래퍼 연동 시 그대로 사용 가능, 인증 게이트됨).
