# Release v2.110.3 — 비활성 챗 세션 retention purge

**날짜**: 2026-06-30
**유형**: 거버넌스 / 데이터 수명주기 (인터페이스 하위호환)
**대상**: `core/chat_memory.py`, `app.py`, `routes/ollama.py`
**근거**: PHI 최소화 원칙 — 챗 콘텐츠 무한보존 갭 봉합

## 배경

직전 성능·SQL 튜닝 검토(v2.109.1)에서 측정 결과, 저장소의 다른 누적 데이터는 전부
retention이 걸려 있는데(`api_activity` 2000캡·`stats_history` 30일·`chat_messages`
세션별 트림) **`chat_sessions`만 나이기반 retention이 없어** 5/14부터 무한 누적
(~3MB/일·~1GB/년)되고 있었다. 디스크 여유(147G)상 저장압은 무시 가능하나, 챗 콘텐츠
무한보존은 PHI 최소화 원칙과 상충 → 거버넌스 차원에서 봉합.

## 변경

### 1. retention purge 코어 (`core/chat_memory.purge_old_sessions`)
- `last_active_at`(없으면 `created_at`) 기준 `now - retention_days` 이전 비활성 세션 대상.
- 메시지를 먼저 삭제한 뒤 세션을 삭제하는 **2단계**(FK CASCADE pragma 의존 회피 + 삭제
  건수 정확 보고). 시각은 UTC isoformat 동일 오프셋이라 사전식 비교 = 시간순 비교.
- `retention_days <= 0` 이면 **무동작**(정책 비활성), 실패 시 롤백 후 재발생.

### 2. 자동 정리 루프 합류 (`app._auto_purge`)
- 기존 24h 1회 정리 루프(jobs·images)에 **단계 3**으로 합류.
- `cfg.get("chat_retention_days", 90)` — 기본 90일, `config.json`으로 무재배포 튜닝
  (0 이하 = 비활성). 실패는 경고 로그만(다른 정리 단계·워커 루프 비차단).

### 3. 수동 트리거 (`POST /api/chat/purge`)
```bash
# 기본(config 또는 90일)
curl -X POST .../api/chat/purge -H "Content-Type: application/json" -d '{}'
# 일수 오버라이드 + 디스크 공간 OS 회수(VACUUM)
curl -X POST .../api/chat/purge -d '{"retention_days": 60, "vacuum": true}'
```
- `retention_days`(선택, 기본 config), `vacuum`(선택, 기본 false).
- **VACUUM은 opt-in**: 삭제만으론 freelist 재사용(OS 미반환). 회수 원할 때만 명시 —
  라이브 DB 락 경합 가능성이 있어 실패는 가드 처리(`vacuum: "failed: ..."` 보고).

## 설계 노트

- **기본 90일 = 전향적 정책**: 배포 시점 최고령 세션이 ~47일이라 **첫 실행은 삭제 0건**.
  이후 90일 경과분부터 자동 정리 → 즉시 파괴 없이 정책만 활성화.
- 공간 회수는 비필수(저장압 무시 가능, v2.109.1 측정). 필요 시 한산할 때 `vacuum:true` 수동.

## 검증

- 회귀 `tests/test_chat_retention.py` **4건**(나이 필터·비활성 무동작·`created_at` 폴백·
  전부 신선 시 noop) + `test_db_concurrency` 8건 통과.
- GPU graceful HUP(MainPID 유지·NRestarts 0)·health 200.
- 라이브 `/api/chat/purge`: `retention_days=99999`→cutoff 1752·0건, 기본 90일→cutoff
  2026-04-01·0건(전향적 정상, 데이터 무손상 확인).
