# Release v2.86.2 — 채팅 행 진짜 근원 수정 (queue.db WAL 체크포인트)

> 2026-06-08 | 이전: v2.86.1 | 패치 (v2.86.1 후 재발 → 근본 수정)

## 핵심: v2.86.1은 부분완화, 진짜 근원은 WAL 무한증가

**증상 재발**: v2.86.1(get_db busy_timeout 재배치) 배포 후에도 재시작하면 chat이 다시 60s 행/000.

**진짜 근원** (py-spy 재진단):
- `queue.db-wal`이 **59MB까지 비대** — gthread 96개가 각자 queue.db **영속 thread-local 커넥션**을 들고 있어(core/db.py get_db, 안 닫음) **passive autocheckpoint가 굶음** → WAL 무한 증가.
- 비대한 WAL 때문에 새 `get_db` `sqlite3.connect`/PRAGMA가 wedge → `/api/chat` 세션메모리 행.
- 재시작 시 WAL 정리(축소)되어 잠깐 빨라지나, 부하로 WAL 재증가 → **재발**(busy_timeout만으론 못 막음).

**수정** (`app.py`):
- `worker_loop`에 주기 `PRAGMA wal_checkpoint(TRUNCATE)` (60s, `_wal_checkpoint`) 추가 → WAL을 지속적으로 축소·바운드.

**검증**:
- 배포 후 chat 5/5 정상(0.21~0.26s), **WAL 59MB → 0.5MB**, VERSION 2.86.2 라이브.

## 진단 요약 (이 incident 전체)
- GPU/Ollama는 **시종 정상**(0.24s). iMac 라우팅 아님. 순수 DB 레이어 문제였음.
- `/api/generate`(세션메모리 없음)는 영향 없었음 → 세션 DB 경로 격리.
- v2.86.1 busy_timeout 재배치 = 부분완화(belt-and-suspenders), v2.86.2 WAL 체크포인트 = 근본.

## 백로그 (근본 구조 개선 — 후속)
- **영속 thread-local 커넥션 ×96 구조 재검토**: 커넥션 풀 상한 / 유휴 정리 (WAL starvation 원천 제거)
- `get_db` recursive retry 횟수 제한 (현재 무한 재귀 가능 — DB 영구 오류 시 wedge)
- WAL 크기 모니터링/알림 (재발 조기 감지)

## 카운트
- VERSION 2.86.1 → **2.86.2** | 코드: app.py(_wal_checkpoint) + core/db.py(busy_timeout, 2.86.1)
