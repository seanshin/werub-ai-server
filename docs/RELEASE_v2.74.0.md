# Release v2.74.0 — 보안 하드닝(SSRF) + 인시던트 진단 인프라

> 릴리즈: 2026-06-06 | 이전: v2.73.0

## 요약

code-analyzer 전수 스캔(2개 에이전트 교차검증)으로 발견한 **Meeting 파이프라인 SSRF/로컬파일
read(Critical)** 를 차단하고, webhook SSRF 가드를 IP 검증 기반으로 강화했다. 함께 SSE 워커
스레드 고갈 인시던트의 **근본원인 확정용 진단 인프라(py-spy + watchdog 자동 포렌식)** 를
구축하고, `requirements.txt` 드리프트(런타임 의존성 13종 누락)를 바로잡았다.

---

## 🔐 보안 — Meeting SSRF + 로컬파일 read 차단 (Critical)

`/api/meeting/analyze`(nginx 외부 노출)의 `file_url`/`callback_url`이 검증 없이 다운로드
파이프라인에 전달되어, `file://`·절대경로로 **임의 로컬파일 read**(`shutil.copy2`), 내부망·
클라우드 메타데이터(169.254.169.254)로 **SSRF**가 가능했다.

- `routes/meeting.py`: `file_url`은 `validate_webhook_url`(공인 http(s)만), `callback_url`은
  `validate_callback_url`(스킴만 검증 — 내부 `hospital-jitsi` 콜백 연동 보존).
- `core/meeting_pipeline.py`: 로컬파일 분기를 `MEETING_ALLOW_LOCAL_FILE=1` 환경변수로 게이트
  (내부 테스트 전용) + 다운로드 직전 `resolve_host_is_public()`로 도메인→사설IP 2차 차단.

## 🔐 보안 — webhook SSRF 가드 강화 + 중복 제거 (High)

- `core/security.py`: 정규식 prefix 매칭 → **IP 리터럴 정적 검사**로 전환. 점표기·IPv6(`::1`)·
  정수/16진/8진 인코딩(`2130706433`, `0x7f000001`)까지 `ipaddress`로 직접 차단(이전 정규식이
  놓치던 메타데이터·인코딩 우회벡터). 도메인은 빠른 통과(요청 핸들러 블로킹 DNS 금지 —
  gthread 점유로 인한 스레드 고갈 방지). 도메인→사설IP 리졸브 SSRF는 백그라운드 워커에서 차단.
- `app.py`: 중복 구현 `_validate_webhook_url`/`_PRIVATE_NETS` 제거 → `core.security`로 위임
  (queue/image webhook 동시 강화).

> ⚠️ 개발 과정 회귀 1건 자가 수정: 초기 강화안이 요청 경로에서 `getaddrinfo`(DNS)를 호출해
> 미해석 호스트에서 20초 블로킹 + 공인 S3 URL 오차단 → DNS 미사용 정적 검사로 재설계.

## 🐛 안정성 — medical_validator DB 커넥션 누수 (High)

`check_drug_names`/`check_drug_interaction_warnings`/`check_icd_consistency`가 `conn.execute`
예외 시 `conn.close()`를 건너뛰어 누수(의료 핫패스 × gthread 96 누적 위험) → `try/finally`로 보장.

## 🧰 신규 — SSE 스레드 고갈 인시던트 진단 인프라

`[[project_incident_worker_deadlock]]`의 근본원인(SSE `Queue.get` vs DB락) 확정용.

- `scripts/diag_threads.sh`: gunicorn 워커 PID 자동탐지 + `nlwp`/`/proc wchan` 집계(sudo 불필요)
  + py-spy 파이썬 스택 덤프(sudo). `logs/diag/`에 저장. 읽기 전용·비파괴.
- `scripts/watchdog.sh`: 000/502/503 **재시작 직전** `capture_diag()` 자동 포렌식(인시던트당 1회,
  부팅중/쿨다운 skip) — 재시작이 증거를 지우기 전 포착.
- `docs/INCIDENT_DIAG.md` 런북 + GPU py-spy 0.4.2 + sudoers NOPASSWD 설치·검증 완료.
- 베이스라인 측정으로 판독 기준 정정: 건강 워커도 `futex_do_wait` 다수가 정상(유휴 풀) →
  **1차 판별은 스레드 수(nlwp), 2차는 py-spy 스택**.

## 📦 수정 — requirements.txt 드리프트

프로덕션 코드가 import하나 미기재된 13종을 GPU venv 실버전으로 핀(이전 fresh install 시
S3/Meeting/Korean 기능이 ImportError): `boto3` `botocore` `s3transfer` `jmespath` `pillow`
`kss` `faster-whisper` `speechbrain` `soundfile` `resampy` `scipy` `scikit-learn` `pytest`.

## ⚡ 성능 — 대시보드 폴링 부하 완화 (이전 세션 포함)

`routes/system.py` 원격 프록시 6개에 TTL 4s 캐시 + ETag/304(`_cached_proxy`), `dashboard.html`
`fetchCached()` If-None-Match — 다중 브라우저 폴링 시 원격 호출을 TTL당 1회로 제한.

---

## 커밋 (v2.73.0 → v2.74.0)

```
3c2e4c4 fix(security): webhook 검증 블로킹 DNS 제거 — 요청 gthread 20s 점유 회귀 수정
74fd4a8 security+fix: Meeting SSRF/로컬파일 read 차단, webhook IP검증 강화, DB누수, requirements
f8d8d49 fix(diag): py-spy 경로 자동탐지 (venv 우선)
86470fa fix(diag): 판독 기준 수정 — nlwp 수가 1차 신호
b87a0db feat: SSE 워커 고갈 인시던트 진단 인프라 — py-spy 덤프 + watchdog 자동 포렌식
0004ca0 perf: 대시보드 폴링 부하 완화 — 원격 프록시 TTL 캐시+ETag/304
d2fdae0 fix: watchdog 기동 유예 가드 — deploy 레이스 방지
```

## 운영 메모

- 배포: GPU `git pull` + `sudo -n systemctl restart ollama-admin`. **활성 대시보드 연결 중
  재시작 금지**(SSE 고갈 촉발) — 저트래픽 창에서. watchdog `STARTUP_GRACE=90` 가드로 부팅 레이스 완화.
- 인시던트 재발 시: watchdog가 `logs/diag/`에 자동 캡처. 수동은 `./scripts/diag_threads.sh`
  (복구 재시작 '전'에 실행 — 재시작은 증거를 지움). 판독: `docs/INCIDENT_DIAG.md`.
- SSRF 검증 원칙: 요청 핸들러는 IP 리터럴 정적 검사만(DNS 금지), 도메인 리졸브는 백그라운드 워커.
