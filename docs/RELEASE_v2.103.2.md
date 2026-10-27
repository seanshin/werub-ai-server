# Release v2.103.2 — 의료 챗 스트림 결정적 라우터 (스트림 경로 모델 무관)

**2026-06-17** | 이전: [v2.103.1](RELEASE_v2.103.1.md)

## 요약

비스트림 `orch_chat`(`/api/orchestrator/chat`)은 v2.101/v2.102에서 결정적 의료 라우터
(`core/medical_router.py _medical_direct_route`)를 step 0에 적용해 의료 경로를 모델 무관화했으나,
**스트림 `/api/orchestrator/chat/stream`에는 미적용**이라 LLM tool-calling 확률에 의존하던 갭이 있었다.
이 릴리즈로 스트림 경로에도 동일 라우터를 적용해 **의료 라우팅이 스트림/비스트림 양쪽 모두 모델 무관**으로 일관된다.

## 변경 (`routes/orchestrator.py` — `orch_chat_stream`)

- `generate()` 최상단에서 `_medical_direct_route(message)` 매칭 시 NDJSON 이벤트로 정본 도구 실행 후 즉시 `return`:
  - `intent`(intent=medical_direct, tool) → `step`(running) → `step`(completed, duration_ms, preview) → `summary`(type=tool_calling, tool_calls, tool_result, message)
- 비스트림과 동일하게 `_execute_action`·`_summarize_medical_route`·`force_korean`·`_save_session_turn` 재사용(동작 정합).
- 미매칭 시 기존 흐름(인텐트 감지 → 워크플로우 → tool-calling 폴백) 100% 보존.

## 검증

- 로컬: `pytest tests/test_medical_direct_route.py` → **20 passed**(라우터 순수 로직 불변).
- 푸시 문법훅 212 통과 → GPU pull → import OK → 재기동(health 200@1.4ms, NRestarts 0).
- **라이브 E2E 4/4**:
  - "진료기록 보존기간?" → `medical_direct` / `medical_law_ask`(5년·10년 의료법 시행규칙 §15)
  - "와파린이랑 아스피린 같이 먹어도 돼?" → `medical_direct` / `dur_check`
  - "타이레놀정 어떻게 먹어요?" → `medical_direct` / `drug_info`(e약은요)
  - "오늘 날씨 어때?" → `general`(미매칭 시 기존 흐름 보존)

## 비고

- 코드 변경은 `routes/orchestrator.py` 1파일(+35줄). 라우터 순수 로직(`core/medical_router.py`)은 불변.
- 잔여(저우선): `split_cautions` 성분명 LIKE 과매칭(무해), `/medical-law/ask` 소형 의안 RAG 랭킹(글로벌 튜닝 백로그).
