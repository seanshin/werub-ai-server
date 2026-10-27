# Release v2.71.0 — SSE 스레드 고갈 수정 + 모델 평가 도구

> 릴리즈: 2026-06-05 | 이전: v2.70.0

## 요약

운영 중 발견된 **워커 무응답(HTTP 000) 장애의 근본 원인을 규명·수정**하고, 모델 선택을
데이터로 뒷받침할 **범용 평가 도구**와 **로컬 CI(pre-push 문법 검사)**를 도입했다.

## 🚨 핵심 수정 — SSE 스레드 풀 고갈

### 증상
- gunicorn 워커가 멈춰 모든 HTTP 요청이 **HTTP 000(30s 타임아웃, 0바이트)** 반환
- `systemctl is-active`는 active인데 헬스만 죽음. 2026-06-04·06-05 재발.

### 원인 (락 데드락 아님)
- `workers=1, threads=32` 환경에서 **SSE `generate()`가 연결 수명 내내 스레드 1개 점유**
  (`/api/monitor/stream`, `/api/dashboard/stream`)
- 브라우저가 끊기면 **3초마다 자동 재연결** → 스레드 점유 누적 → 32스레드 고갈
  → 일반 요청이 스레드를 못 받아 무응답

### 수정
| 항목 | 변경 |
|------|------|
| `gunicorn.conf.py` | threads **32 → 96** (SSE 스트림 점유 대비 헤드룸) |
| `app.py` | SSE 구독자 헬퍼 `_MonitorSub`/`_monitor_subscribe`/`_monitor_unsubscribe` |
| — | 전역 상한 **24** 초과 시 **503** 즉시 거부 (재연결 폭주가 스레드 미점유) |
| — | 실 IP는 **IP당 1개**로 기존 연결 축출 + 종료신호로 스레드 즉시 회수 (localhost 제외) |
| `routes/dashboard_api.py` | 동일 헬퍼 주입·적용 |
| `templates/monitor.html` | 재연결 **지수 백오프(3s→30s)** + **탭 hidden 시 SSE 해제** |

배포 후 무재발 모니터링(30분 주기) 정상.

## ✨ 신규 — 모델 평가 도구 `scripts/eval_model.py`

- 범용·경량·외부의존 0건. 카테고리 4종(korean/medical/coding/reasoning) × **10케이스 = 40**
- 선언형 케이스 + `weights`(합 100) 단일 채점 엔진 (keywords/answer/korean/code/length)
- CLI: `python3 scripts/eval_model.py [모델...] --cat korean,coding --out path`
- Ollama 직접 호출(프록시/인증 우회), 결과 JSON + 콘솔 요약 테이블

### Tool Calling 후보 비교 결과 (40케이스/카테고리)
| 모델 | korean | medical | coding | reasoning | 평균 | tok/s |
|------|--------|---------|--------|-----------|------|-------|
| qwen3:14b | 98.2 | 99.3 | 100 | 100 | 99.4 | 86 |
| **qwen3:8b** (현 tool model) | 95.0 | 99.8 | 100 | 100 | 98.7 | **142** |
| qwen3-coder:30b | 100 | 99.7 | 100 | 90 | 97.4 | 58 |

→ Tool Calling은 최대 5회 반복이라 속도가 결정적. qwen3:8b가 가장 빠르고 medical·reasoning
최상위 → **현 설정(`config/tools.json` `defaults.model=qwen3:8b`) 유지가 최적임을 검증.**

## ✨ 신규 — 로컬 CI (pre-push 문법 검사)

- `.githooks/pre-push` + `scripts/check_syntax.py` — push 직전 전체 `.py` `ast.parse`, 오류 시 push 차단
- GitHub Actions 미도입(PRIVATE repo 무료분 회피) → **클라우드 비용 $0**
- 활성화: `git config core.hooksPath .githooks` (머신별 1회)

## 기타
- `routes/medical_training.py` JS 정규식 `\/` SyntaxWarning 제거 (런타임 출력 동일)
- `.gitignore`: `story.db`, `docs/eval_*.json` 추가

## 배포 메모
- 배포는 직접(수동)만: SSH → `git pull origin main` → (변경 시) `pip install -r requirements.txt`
  → `sudo systemctl restart ollama-admin` → `/api/queue/status` 헬스 체크
- 자동 배포(GitHub Actions) 미도입.
