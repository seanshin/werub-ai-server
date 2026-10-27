# Release v2.75.0 — SSE graceful drain (워커 스레드 고갈 인시던트 근본수정)

> 릴리즈: 2026-06-07 | 이전: v2.74.0

## 요약

장기 미해결 인시던트(`[[project_incident_worker_deadlock]]`)의 **잔여 근본원인**을 제거했다.
지금까지의 완화책(threads 96 + SSE 구독자 상한 24 + IP당 1개 축출)은 정상 운영은 막았으나,
**활성 대시보드/SSE 연결 중 재시작**이라는 확정 트리거에서는 워커 스레드가 `graceful_timeout`(30s)
까지 점유되고 클라이언트가 일제히 재연결하여 스레드 풀(97)을 고갈시켰다. 이번 릴리즈는
재시작 시 ①서버가 모든 SSE 구독자를 **즉시 종료시켜 스레드를 회수**하고, ②클라이언트에
**shutdown을 통지해 분산 재연결**시키며, ③기동 직후 일정 시간 신규 SSE를 **거부(503)** 하여
초기화 중 워커를 보호한다. 함께 드리프트된 `app.py` VERSION(2.73.0 표기)을 바로잡았다.

---

## 🧵 SSE graceful drain (인시던트 근본수정)

### 문제

- 두 SSE 제너레이터(`/api/monitor/stream` app.py, 대시보드 SSE dashboard_api.py)가 연결 수명
  내내 `q.get(timeout)` 루프로 gthread 스레드 1개를 점유.
- 재시작(SIGTERM) 시 서버가 구독자에게 종료를 알리는 경로가 없어 스레드가 timeout까지 잔존.
- 클라이언트는 끊김을 감지하면 곧바로(3s) 재연결 → 초기화 중인 새 워커에 폭주 → 스레드 고갈.

### 변경

- **`app.py`**
  - `_SHUTDOWN`(Event), `_BOOT_TS`(단조 시계), `_MONITOR_STARTUP_GRACE=15s` 추가.
  - `signal_shutdown()`: 전 구독자 큐에 `{"type":"shutdown"}` 통지 + `None` sentinel push +
    `close.set()` → 대기 중인 `q.get`을 즉시 깨워 break, 스레드 회수.
  - `_monitor_subscribe()`: 종료 중이거나 기동 후 15s 이내면 신규 구독 거부(503) →
    재연결 폭주가 초기화 중 워커에 쌓이지 않게.
  - `monitor_stream` 제너레이터에 `_SHUTDOWN` 백스톱(큐 push 실패 시에도 다음 루프에서 break).
- **`gunicorn.conf.py`**
  - `post_worker_init`에서 워커의 SIGTERM(=`systemctl restart` 경로)을 가로채 `signal_shutdown()`
    실행 후 **gunicorn 원래 핸들러로 체이닝**(graceful stop 동작 보존).
  - `worker_int`(SIGINT/SIGQUIT 경로)에서도 동일 드레인. `preload_app=True`라 워커의 `import app`
    은 fork된 동일 메모리를 공유.
- **`templates/monitor.html`**
  - `type:shutdown` 수신 시 18~30s **지터 후 재연결**(`_draining` 플래그로 `onerror` 중복 스케줄
    방지). 기존 지수 백오프(3s→30s)·탭 hidden 시 연결 해제는 유지.
  - `dashboard.html`은 폴링(`fetchCached` + 5s interval) 방식이라 클라이언트 변경 불필요.
  - `dashboard_api.py` SSE도 공유 `None` sentinel + `close`로 자동 드레인(코드 변경 없음).

### 효과

- 재시작 시 SSE 스레드를 30s 대기 없이 즉시 회수 → outage 창 제거.
- 클라이언트 분산 재연결 + 기동 유예로 restart-storm 차단(상한 24만으로 못 막던 트리거).

## 🔧 수정 — VERSION 드리프트

`app.py` `VERSION`이 `2.73.0`에 머물러 v2.74.0 릴리즈와 불일치 → `2.75.0`으로 정정
(CLAUDE.md/README 동기화).

---

## 커밋 (v2.74.0 → v2.75.0)

```
(이 릴리즈 커밋 — SSE graceful drain + VERSION 동기화)
```

## 운영 메모

- 배포: GPU `git pull` + `sudo -n systemctl restart ollama-admin`. 재시작은 여전히 **저트래픽
  창** 권장이나, 이번 변경으로 활성 SSE 연결이 있어도 스레드 즉시 회수 + 분산 재연결로
  고갈 트리거가 제거됨.
- 배포 직후 검증: `./scripts/diag_threads.sh`로 워커 `nlwp` 평탄(베이스라인 ~9) 확인 +
  `/api/queue/status` health 200. 재시작 시 monitor 페이지가 "Restarting…" 후 18~30s 뒤 재연결되면 정상.
- 튜닝: `_MONITOR_STARTUP_GRACE`(기동 유예, app.py)와 monitor.html 재연결 지터(18~30s)는
  기동 시간에 맞춰 조정 가능.
