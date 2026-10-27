# 세션 작업 보고서 — 2026-06-09

> WeRU.B AI Server | v2.86.7 → **v2.86.13** | 커밋 6건 | GPU HEAD `6f9fb95` (배포·검증 완료)

## 1. 요약 (Executive Summary)

라이브 점검에서 시작해 **인증 보안 시리즈 완결 → RAG 치명 버그 수정 → DB 하드닝**까지
6개 패치를 배포·검증했다. 각 변경은 GPU 서버에 즉시 배포하고 라이브로 검증했으며,
회귀 없음·무중단을 확인했다. 핵심 성과는 두 가지:
- **code-analyzer 독립 재검증으로 갓 배포한 인증 코드의 실제 동시성 버그(M2/M3)를 발견·수정**
- **사용자가 신고한 RAG 500의 진짜 근원(임베딩 모델 VRAM OOM)을 추적해 코드 한 줄로 해결**

## 2. 작업 내역 (PDCA)

### Plan
- 세션 시작: 기록 점검 → 라이브 상태 점검 → 백로그 중 **static salt(유일 잔여 Medium)** 선택
- 이후 사용자 지시로 RAG 500 진단, 잔여 정리로 확장

### Do — 배포된 6개 패치

| 버전 | 분류 | 내용 |
|------|------|------|
| **v2.86.8** | security | 비밀번호 static salt 제거 → per-user salt(`core/password.py`) + 무중단 lazy 마이그레이션 |
| **v2.86.9** | fix | M2/M3 — 로그인 post-auth UPDATE 분리(동시 로그인 false-negative 제거) |
| **v2.86.10** | security | 잔여 하드닝 4건: L2 username 열거 방지·L1 config 잔재 제거·L3 비번 8자 통일·M1 인코딩 |
| **v2.86.11** | docs | 문서/API명세 동기화 + admin_users_update 길이검증(L3 마무리) |
| **v2.86.12** | fix | **RAG 임베딩 500 수정** — bge-m3 VRAM OOM(글로벌 ctx=8192 KV 폭주) |
| **v2.86.13** | hardening | sqlite connect timeout 10곳 일관화 + 서버 정리 |

### Check — 검증
- **테스트**: `tests/test_password.py` 6→8건, check_syntax 160 .py, 회귀 419건 유지
- **라이브 검증**: 매 배포마다 health 200·VERSION 확인·기능 라이브 테스트(로그인 오답거부, RAG 200, chat/meeting DB)
- **독립 재검증**: code-analyzer가 `core/password.py` 88/100, Critical/High 0, M2/M3 발견

### Act — 후속
- M2/M3 즉시 수정(v2.86.9), 잔여 L1~M1 종결(v2.86.10), 문서 동기화(v2.86.11)

## 3. 주요 발견 및 근본 원인 분석

### 3.1 인증 — static salt → per-user salt
- **문제**: `pbkdf2(pw, b"werub-ollama-salt", 200k)` 고정 salt 5곳 하드코딩 → 동일 비번 동일 해시·사전계산 약점
- **해결**: `core/password.py` 단일 소스, 자기기술 포맷 `pbkdf2_sha256$200000$salt$hash`, 레거시 자동 폴백 + 로그인 시 lazy 재해싱(강제리셋 0)
- **재검증 발견(M2/M3)**: verify 성공과 post-auth UPDATE가 같은 `except: return False` 블록 → 동시 로그인 SQLite BUSY 시 **비번 맞아도 인증 실패**. UPDATE를 best-effort로 분리

### 3.2 RAG 500 — 임베딩 모델 VRAM OOM ⭐
- **증상**: `/api/rag/ask`·`/api/rag/search` 서버 500("여전히"), `/api/medical/reference`는 200
- **추적**: py-spy/journalctl → `embed_text` → Ollama `/api/embed` 500 → `cudaMalloc failed: out of memory (allocating 4512 MiB, free 3621 MiB)`
- **근본 원인**: 글로벌 `OLLAMA_CONTEXT_LENGTH=8192`(v2.86.7 튜닝)가 **임베딩 모델(bge-m3 567M)에도 적용**돼 KV를 ~4.5GB로 부풀림 → qwen2.5:14b(12.2GB) 상주 중 free 3.6GB에 못 들어가 OOM
- **해결**: `embed_text`가 `/api/embed`에 `options.num_ctx=2048` 명시 → bge-m3 **4.5GB→1.16GB**, LLM과 공존. 라이브 검증 200·1024차원
- **삽질 교훈**: 처음 시도한 `NUM_PARALLEL 4→2`는 무효(풋프린트가 parallel 아닌 context에 지배) → parallel 4 원복. **진짜 레버는 임베딩 호출의 per-request num_ctx**
- **대조군 설명**: medical/reference는 임베딩 비사용(직접 생성)이라 영향 없었음

## 4. 최종 라이브 상태

| 지표 | 값 |
|------|-----|
| 서비스 | active/running, NRestarts=0 |
| Health | 200 @ 4.6ms |
| VERSION / HEAD | 2.86.13 / `6f9fb95` (로컬·원격·GPU 동기) |
| 워커 nlwp | 39 (정상, py-spy로 SSE 고갈 아님 확인) |
| 오늘 CRIT/000 | 0건 |
| 인증 / RAG | 정상 (로그인 동작, RAG 파이프라인 200) |

## 5. 부수 확인 사항
- **iMac 위임 폴백 검증**: imac-worker 미도달 시 GPU 로컬 폴백 동작(dispatch.run 3단 방어, 예외 무전파) — 라이브에서 down→up 자동 전환 관찰
- **워커 nlwp=92 오탐 클리어**: py-spy로 ThreadPoolExecutor 유휴 풀 파킹 확인(SSE Queue.get 0건) → 고갈 아님

## 6. 남은 항목 (백로그)

| 우선 | 항목 | 비고 |
|------|------|------|
| 데이터 | **ChromaDB 지식 색인** | RAG 파이프라인 정상이나 색인할 실제 문서 없음 → 소스 제공 필요(코드 아님) |
| 조율 | orchestrator Phase 2/3 (embed 위임·Phase3 LLM) | operator(seanshin) 병렬작업과 충돌 주의 |
| 대기 | HIS 스트리밍 적용(체감 80배) | 외부 팀 전달 대기 |
| 장기 | 소설 EPUB / CI-CD / FastAPI 전환 | 보류 |

won't-fix: graceful_shutdown `threading._shutdown` 노이즈(무해, sys.exit 데몬 teardown 본질)

## 7. 변경 통계
- 6 커밋, 14 파일, +376/-46
- 신규: `core/password.py`, `tests/test_password.py`, `docs/RELEASE_v2.86.11.md`
- 서버 config(git 밖): OLLAMA env 임시 parallel=2 → **4 원복**, 백업파일 정리

---
*생성: 2026-06-09 | 작성 보조: Claude Opus 4.8 (1M context)*
