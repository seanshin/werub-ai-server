# Release v2.72.0 — 신뢰성 자동복구 + 숨은 결함 발굴 + 모델 config화

> 릴리즈: 2026-06-06 | 이전: v2.71.0

## 요약

v2.71.0에서 완화한 SSE 인시던트의 **무재발을 확정**하고, 운영 신뢰성을 점검하던 중
**조용히 묻혀 있던 결함 3건**(모니터링 오탐·로케일 버그·미커밋 프로덕션 코드)을 발굴·수정했다.
또한 깨져 있던 **watchdog 자동복구를 복원**하고, 오케스트레이터 모델을 **코드 수정 없이
교체 가능하도록 config화**했다.

---

## 🚨 핵심 수정 — "Ollama Engine Down"은 실제 장애가 아닌 오탐이었음

### 증상
- `logs/queue.log`에 "Ollama Engine Down" critical 알림 누적 **31건** (야간·주간 산발 클러스터)

### 원인 (실제 다운 아님)
- ollama 프로세스는 **NRestarts=0, 2일 무중단**, journal상 사건 직전 `GET /` **8µs** 정상 응답
- `app.py get_ollama_status()`가 `GET :11434/`를 **timeout=3s 단발** 호출 후 **1회 실패만으로
  '다운' 확정** → SSE 고갈과 같은 부하구간의 일시적 스레드포화/스테일 keep-alive 블립에 걸려
  **대부분 가짜 경보**

### 수정
| 항목 | 변경 |
|------|------|
| `app.py` `get_ollama_status` | **2회 연속 실패해야 다운 확정**(단발 블립은 직전 상태 유지), timeout 3→4s |
| `scripts/watchdog.sh` | ollama 재시작을 **`systemctl is-active` 기준으로 보수화** — HTTP 무응답만으로 멀쩡한 엔진(대형모델 로드중)을 죽이는 위험 제거 |

---

## 🔧 신규/복원 — watchdog 자동복구 (P1-A)

기존 `watchdog.sh`는 **macOS launchctl/plist** 기반으로 작성돼 GPU(Ubuntu/systemd)에서
무용지물 + cron 미등록 → **자동복구 수단이 전무**한 상태였다. 전면 재작성:

- systemd 기반 복구: `sudo -n systemctl restart ollama-admin`(HTTP 000/502/503 = SSE 고갈)
- 단발 블립 방지(재확인) + 서비스별 **600s 쿨다운**(기동·34모델 로드 안정화)
- **하트비트** `logs/.watchdog/last_run` — 로그 스팸 없이 가동 여부 관찰
- GPU `*/5` cron 등록 + **실제 자동실행 검증**(하트비트 틱 갱신 확인)

---

## 🐛 숨은 결함 발굴

| 발견 | 내용 |
|------|------|
| **mem/swap 점검 미작동** | `health_check.sh`가 한국어 로케일에서 `free`/`df` 헤더 현지화(메모리:/스왑:)로 awk 미매칭 → 빈값 → `[: -ge` 에러로 **메모리·스왑 점검이 그동안 조용히 미작동**(디스크만 동작). `LC_ALL=C`+빈값 가드로 수정 |
| **미커밋 프로덕션 코드** | `routes/chat.py`가 import하는 `core/hr_law.py`(365줄)가 **VCS에 없어** fresh clone/재배포 시 ImportError로 앱 기동 실패 상태였음. GPU 프로덕션에서 캡처·커밋(전수검증으로 다른 깨진 import 없음 확인) |
| **데드코드 4파일** | 어디서도 import 0건인 `core/medical.py`·`core/game_memory.py`·`core/image.py`·루트 `hr_law.py`(구버전) 백업 후 삭제 |

---

## ✨ 신규 — 오케스트레이터 모델 config화

- **일반챗 폴백 모델**: `config/tools.json` `general_chat` 블록(model/temperature/num_predict/timeout/system).
  타입·범위 가드 포함(timeout=null/bool/초과 차단 → 무한대기 방지)
- **워크플로 액션 기본모델**: `workflow_default_model`로 하드코딩 3곳 제거
- 변경 후 `POST /api/orchestrator/tools/reload`로 **코드 수정·재배포 없이** 모델 교체
  (기본=qwen2.5:14b 유지, 회귀 없음. qwen3:14b=품질↑ 단 thinking 5배 느림)

---

## 🧹 운영 위생

- **DB pull 마찰 제거**: 추적 DB 7개에 GPU `git update-index --skip-worktree` 적용 —
  런타임 수정을 git이 무시(시드데이터 repo 보존, 삭제·untrack 없음, 완전 가역)
- `health_check.sh` 하트비트(`logs/.health_check_last`) — "로그 침묵=정상 OR cron사망" 모호성 제거
- `.gitignore` 백업 패턴 보강(`*.bak`/`*.backup`/`app_new.py` 등)

---

## 커밋 (v2.71.0 → v2.72.0)

```
c0d154c feat: 오케스트레이터 일반챗 폴백 모델 config화
98389fa fix: general_chat config 타입·범위 가드 (무한대기 방지)
c4a4fe0 fix: watchdog.sh GPU/systemd 재작성 — 자동복구 부재 해소 (P1-A)
e45bf13 feat: watchdog 하트비트 추가
5bd845e fix: Ollama Engine Down 오탐 제거 (P1-B)
2e18d34 chore: P2/P3 위생 — health_check 하트비트 + 워크플로 모델 config화 + gitignore
29c34d9 fix: health_check.sh 메모리/스왑 점검 로케일 버그
24cc076 fix: 미커밋 프로덕션 의존 core/hr_law.py 추가 (repo 복구)
```

## 운영 메모

- GPU watchdog `*/5` cron 가동 중. 점검: `cat logs/.watchdog/last_run`(600s+ 미갱신 시 cron 의심)
- sudo NOPASSWD 허용: `systemctl restart ollama-admin/ollama`, `daemon-reload`, comfyui 제어
- 시드 DB를 커밋으로 업데이트하려면 해당 DB만 `git update-index --no-skip-worktree` 후 pull
