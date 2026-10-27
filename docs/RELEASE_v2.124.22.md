# v2.124.22 — 성능·SQL 검토 (측정 선행) + auto_purge NULL 잔존 수정

2026-07-21 | 계기: 운영자 "성능 검토하고, SQL 튜닝도 검토" | 관련: [[project_performance_monitoring_plan]]

## 요약

라이브 실측으로 성능·SQL을 전면 검토했습니다. **병목 0 · 추가 튜닝 불요**(v2.109.1·
6/27 결론의 3번째 독립 재확인). 오늘 fee_codes가 4배(29K→120K)로 커진 만큼 그쪽을
중점으로 봤으나 영향 없음. 검토 중 발견한 실동작 결함 1건(auto_purge)만 수정했습니다.

## 측정 결과 — 전부 정상

| 항목 | 실측 | 판단 |
|---|---|---|
| 엔드포인트 p50 | status 3.0 · queue/status 1.1 · routing 0.6 · hr-law/stats 1.1ms | 빠름 |
| `/api/health` | 44.8ms | nvidia-smi 33ms + ollama/ps 65ms가 지배(5분 폴링, 무해) |
| **fee_codes 코드 조회** | **0.01ms** (PK 인덱스) | 120,308행에도 무영향 |
| fee_codes 카테고리 집계 | 4.4ms (COVERING INDEX) | 정상 |
| drug_master 조회 | 0.01ms (idx_dm_product) | 305,522행에도 무영향 |
| freelist | notice 0.0% · queue 0.8% · eval 0.0% | **VACUUM 무의미** |
| perf 알림 | **alerts 없음** · p95 599ms · error 0% · VRAM 77.6% · 41°C | 임계 미달 |

## 세운 가설 2건, 측정으로 기각

측정 선행이 추측 작업을 잘라낸 사례입니다.

1. **보안 헤더의 `load_config()` 자기 회귀 의심**: v2.124.19 보안 헤더가 응답마다
   `load_config()`(캐시 없이 매번 파일 파싱)를 호출한다. 실측 **12.9µs/call**, 요청당
   3~4회여도 40~50µs로 p50(0.6~3ms) 대비 무시 가능. → **캐싱 안 함**(이득이 노이즈
   수준인데 "설정 변경 즉시 반영" 성질을 잃음).
2. **RAG 오버페치(v2.124.17, n_results 2배) 비용 의심**: 첫 측정 495ms는 **콜드 호출
   혼입**이었고, 워밍업 후 공정 비교 시 n=5 3.69ms vs n=10 3.78ms = **+0.09ms(2%)**.
   HNSW는 결과 개수가 아니라 그래프 탐색이 지배. `/ask` 검색 파이프라인 전체도 p50
   10ms(LLM 제외). → 회귀 없음 확정.

`fee_codes` 접두 LIKE는 10ms 풀스캔이나 **코드상 그런 쿼리를 쓰는 곳이 없어**(전부
`WHERE code=?` 정확 조회) 인덱스를 추가하지 않음(쓰지 않는 경로에 인덱스 부채 금지).

## 발견·수정 — auto_purge가 completed_at NULL 잡을 영구 잔존시킴

retention 점검 중 `jobs`에 **22일·17일 된 failed 행 2건**이 잔존(7일 정책 위반)함을
발견. 원인:

```sql
DELETE ... WHERE status IN ('completed','failed') AND completed_at < ?
```

일부 실패 경로가 `completed_at`을 남기지 않아 **NULL**이 되고, SQL 비교는 NULL을
절대 매칭하지 않아 그 잡이 **영구 잔존**한다. 규모는 작으나 무한 누적 성질.

fix: `COALESCE(completed_at, created_at) < ?` — 나이 기준을 항상 성립시킴. 다음
`_auto_purge` 주기(24h)에 기존 2행이 정리된다. 회귀
`test_auto_purge_matches_null_completed_at`(NULL 오래됨=삭제·NULL 최근=보존).

## 나머지 retention 정상

- `chat_sessions` 7,800행 최고령 67일 — 90일 정책이라 **8월 중순부터** 실제 삭제 시작.
- `stats_history` 정확히 30일 · `api_activity` 2,000행 캡 유지.
- queue.db 254.9MB = chat_messages 15,610행 누적(정당 데이터·인덱스 완비·조회 무이슈).

## 종합

근본 성능 레버는 여전히 **16GB VRAM 천장**(하드웨어)뿐. 소프트웨어 튜닝 대상 없음 —
무리한 조치를 피하는 프로젝트 ethos와 일관. `recycles_30min=5`는 당일 배포 중 graceful
HUP 다회로 생긴 값(이상 아님).
