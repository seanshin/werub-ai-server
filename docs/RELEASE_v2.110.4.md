# Release v2.110.4 — 운영 하드닝: DUR 과다동기화 + 배치 파이프라인/워커

**날짜**: 2026-06-30
**유형**: 버그 수정 / 운영 안정성 (인터페이스 하위호환)
**대상**: `core/dur_sync.py`, `app.py`, `routes/queue.py`
**근거**: 라이브 점검 + 배치작업 전체 검토에서 발견한 운영 결함 봉합

## 1. DUR 외부 API 과다 동기화 봉합 (`core/dur_sync.py`)

라이브 점검서 DUR 동기화가 7일 주기가 아니라 **~22분마다 반복 실행**(식약처
data.go.kr API를 의도 대비 ~72배/일 호출)되는 것을 발견. 근본 원인 = `gunicorn
max_requests=5000` + 대시보드 폴링 트래픽으로 워커가 ~22분마다 재생성되고,
`preload_app=False`라 모듈이 fresh import되며 dur-sync 스케줄러가 재기동 → startup
600s 후 `_loop`이 staleness 무관하게 무조건 `refresh_all()` 발화.

- **fix**: `_sync_due(interval)` 게이트 — `dur_sync_meta`의 최근 성공(ok) 동기화가
  interval 이내면 skip. due 판정이 워커 churn을 가로질러 7일 cadence 보장. 파싱 불가
  등 불확실 시 보수적 skip(기존 정본 보존).
- **위험 차단**: 외부 API 자원남용 · 서비스키 스로틀(특히 e약은요 '신청' 상태 키).
  데이터 손상 위험은 없었음(안전 갱신=fetch 성공+건수 sane 후 교체).

## 2. 배치작업 전체 검토 — 3건 봉합 (`app.py`, `routes/queue.py`)

### 2-1. 파이프라인 단계 실패 시 하위 `waiting` 잡 고아화
`_advance_pipeline`은 성공 시에만 다음 단계를 pending으로 올려, 중간 단계 최종 실패
(max retry)면 downstream waiting이 never run + `_auto_purge`(completed/failed만 삭제)도
못 지워 누적. **라이브 queue.db에 2026-06-04부터 26일째 stuck된 waiting 10건** 확인.
- `_fail_pipeline_downstream`: 최종 실패 경로서 같은 pipeline_id의 모든 waiting을
  cascade-fail(근본). `_auto_purge` 고아 waiting 리퍼(>auto_purge_days) defense-in-depth.
- HUP 재시작 시 리퍼가 10건 자동 정리(failed 전이) → **waiting=0** 확인.

### 2-2. 재시도 백오프 미적용
`RETRY_DELAYS=[30,60,120]`을 계산·로깅했으나 `time.sleep` 미호출로 실패 잡이 즉시
재큐돼 백오프가 cosmetic. → jobs에 `next_retry_at` 컬럼(마이그레이션) 추가,
`_handle_llm_error`가 now+delay 설정, worker_loop 디큐 3쿼리에 게이트
(`next_retry_at IS NULL OR <= now`)로 미도래 잡 제외. 수동 retry는 `next_retry_at=NULL`.

### 2-3. 워커 재생성 중첩창 in-flight 잡 이중처리 race
워커 재생성(~22분) 중첩창에 살아있는 구 워커가 처리 중인 'running' 잡을 신 워커
startup 리셋이 pending으로 되돌려 이중처리 가능(LLM 멱등이라 무해하나 컴퓨트 낭비).
→ startup 리셋 2곳을 staleness 게이트(`_STALE_RUNNING_S=360`=Ollama timeout 300+graceful
30, 이 이상이면 어떤 워커도 처리 불가=확실한 orphan) + worker_loop 주기 리퍼
`_reap_stale_running`(60s) 안전망(재시작 의존 제거).

### 측정으로 종결(조치 불요)
- queue.db 243MB = chat_messages 15,540행×평균 9.7KB의 정당한 데이터. freelist 10 pages
  (회수 0.0%)→VACHUM 무의미. perf 무이슈(인덱스 완비·쿼리 0.04ms). 거버넌스는 v2.110.3
  chat retention(90일)이 처리(최고령 세션 47일→~8월 중순부터 트림).
- 동적 배치 크기(VRAM-aware 14b=2·경량=8)·webhook SSRF 검증·DB locked 재시도 = 건강.

## 검증

- 회귀 신규 18: `test_dur_sync` 5→9 · `test_pipeline_cascade` 4 · `test_queue_backoff_reap` 5.
- GPU graceful HUP(MainPID 유지·NRestarts 0)·health 200 · `next_retry_at` 마이그레이션 확인
  · DUR 다음 워커 recycle 시 `[dur-sync] skip` 관측 · 라이브 waiting=0.
