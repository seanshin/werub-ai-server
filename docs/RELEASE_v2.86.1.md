# Release v2.86.1 — HIS 채팅 행/지연 수정 (get_db busy_timeout)

> 2026-06-08 | 이전: v2.86.0 | 패치(버그 수정)

## 핵심 수정
**증상**: HIS 채팅이 60s 행 후 무응답(http=000), "database is locked" 동반.

**진단** (py-spy 스택 덤프):
- GPU/Ollama는 정상 — Ollama 직접 0.24s, `/api/generate` 0.28s, GPU util 정상. **GPU/iMac 라우팅 문제 아님.**
- `/api/chat` → `get_or_create_session` → **`get_db()`(core/db.py)의 PRAGMA 단계에서 wedge**.
- `/api/generate`(세션메모리 없음)는 멀쩡 → 세션 DB 커넥션 경로가 병목으로 확정.

**수정** (`core/db.py`):
- `PRAGMA busy_timeout=5000`을 `connect` 직후 **첫 PRAGMA로 이동**(기존엔 journal_mode 뒤) → 이후 모든 DB op이 락 시 5s 내 OperationalError → 세션메모리 try/except가 잡아 chat 진행(무한 행 제거).

**검증**:
- chat 5/5 정상(0.2~3.5s), `/api/queue/status` 복구(빈응답→200, 1.7ms), VRAM 안전, detect/STT 위임 정상.

## 부가 확인
- **flash_attention=1 + kv_cache=q8_0**: GPU Ollama에 **이미 활성**(품질 유지 속도 개선). 14b chat ~3.5s는 모델 본연 속도(15 tok/s).
- 모델 **14b 유지** 결정(품질 우선). 7b 전환(2배 속도)은 보류.
- `sqlite3.connect` 전수 스캔: 메인 경로(get_db) 견고. timeout 인자 없는 7곳(meeting_pipeline/hr_law/novel/story/meeting/app 일부)은 락 시 빠른 에러(행 아님)·저트래픽 → **저우선 백로그**.

## 알려진 한계 / 후속
- get_db는 `connect(timeout=10)`로도 busy timeout이 있어, PRAGMA 재배치는 belt-and-suspenders. 행 해소엔 재시작의 stale 커넥션 정리 효과도 포함. **HIS 고부하 재발 시 thread-local 커넥션/WAL 경합 추가 분석 필요.**
- DB 데이터(56MB, ~18K행)는 비대 아님 — purge 불필요.

## 카운트
- VERSION 2.86.0 → **2.86.1** | 코드 변경: core/db.py 1파일(이미 e003f9c 배포)
