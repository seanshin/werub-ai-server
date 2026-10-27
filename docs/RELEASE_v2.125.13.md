# v2.125.13 — D1 router 이관 슬라이스 3: 44곳 병렬 이관 → 이관 대상 잔여 0

날짜: 2026-08-19 밤 | 기획 §3-D · 인벤토리 `docs/01-plan/references/direct-ollama-calls-2026-08-19.md`

5그룹을 **병렬 에이전트**로 이관했다(파일 집합이 서로 겹치지 않게 나누고, 라우팅 표·MIGRATED 목록은 중앙에서 병합).
각 그룹은 종전 페이로드를 **그대로 적은** 동일성 회귀를 냈다.

| 그룹 | 지점 | task | 회귀 |
|---|---|---|---|
| `routes/game.py` | 12 | `game.*` 10종 | 11 |
| `routes/vision.py` | 10 (describe·medical analyze/stream/report·dual read·classify) | `vision.*` 7종 | 17 |
| `routes/chat.py`(레거시)·`story`·`triage` | 3+2+2 | `chat.checked`·`chat.generate`·`chat.general`(재사용)·`story.generate`·`triage.*` | 17 (7 skip=chat.py import 깨짐·app 미등록 레거시) |
| `routes/hr_law`·`medical`·`medical_cli` | 3+1+2 | `hr_law.*`·`medical.generate`·`medical.cli_*` | 15 |
| `core/tool_executor`·`websocket_handler`·`chat_memory`·`meeting_pipeline`·`meeting_vision`·`app.py` | 3+2+1+1+1+1 | `orchestrator.tool_calling`(재사용)·`ws.*`·`chat.memory_summary`·`meeting.*`·`queue.llm_job` | 15 |

- 라우팅 표 **25 → 59 task**. 표 값은 종전 하드코딩/기본값(광고용)·동적 선택은 호출자 `model=`에 그대로.
- 전체 회귀 **1771 passed**(GPU worktree) · MIGRATED +13 파일.
- 재계수 잔여 직접 호출 **32** = 허용 25(패스스루 프록시 `routes/ollama.py` 10·`system` 프로브 4·`gpu_scheduler`·`heavy`·`openai_compat`·`llm_gateway`·`gateway`·`medical_training` 프로브·`app.py` 예열/축출·`medical.py:1251` NDJSON 바이트 패스스루) + 잔재 6(`coding`·`medical_law`의 `_router_enabled()` 폴백) ⇒ **이관 대상 0**. → D2(`repeat_penalty` 명시)는 어댑터 한 곳이면 된다(허용 목록은 의도적으로 클라이언트 옵션 그대로).

## 동작 차이 (전부 회귀에 고정·의도적)
- 어댑터는 `options`를 항상 싣는다 → 종전에 키가 없던 본문에 `"options": {}`가 붙는다(Ollama 의미 동일): chat_memory·meeting_pipeline·queue 잡·hr_law 스트림·chat.py 3.
- 스트림 경로의 HTTP≥400: 종전 **무언 종료**(200인데 빈 결과 클래스) → `error` 이벤트(hr_law ask/stream·websocket·vision medical stream). 종전 무성 실패 3곳이 드러났다.
- 스트림 토큰 이벤트를 어댑터 조각으로 재구성(chat.py·vision medical) — `created_at` 등 원 라인 부가 필드 소실·done은 raw 그대로.
- 오류 문구 형식: `HTTP <n>: <본문 120자>`(종전 requests 문구·200자). queue 잡 `job.error`에 `HTTP n: ` 접두.
- 보존한 무성 실패(변경 안 함·주석): game 12곳·hr_law rules_draft/review·`_medical_generate`·cli·tool_executor·dual-read — HTTP 오류 JSON→빈 결과+200. **별건 릴리즈 후보**(같은 클래스 전 경로 일괄).
- 🔴 부수 발견: `routes/chat.py`는 `from core.korean import validate_korean_quality`가 **존재하지 않는 이름**이라 import 자체가 깨져 있고 app.py에 미등록 — 레거시(컨시어지 `/api/chat/enhanced`는 `routes/ollama.py`). 삭제/복구는 별건.

## 배포
회귀 **1781 passed** · 첫 게이트 176/177(`adr: 구조화 필드 존재` 1건 — 온도 0.2~0.3의 저율 비-JSON, 라이브 재호출 1+`--only` 3회 정상·게이트에 실패 시 본문 남기게 보강) → **재실행 177/177 · 베이스라인 0 · 신규 실패 0**. 라이브 2.125.13. 라이브 경로 중 game·vision·story·triage·hr_law·medical·tool_calling·websocket·meeting은 스모크/골든셋(177)이 회귀망.
