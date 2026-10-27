# Release v2.101.0 — 의료 오케스트레이션 합류 보완 (결정적 라우터)

**2026-06-12** | 이전: [v2.100.0](RELEASE_v2.100.0.md)

## 배경 — 라우팅 점검 결과

이번 세션 추가 약물/의료 기능의 챗 오케스트레이션 합류를 점검(8개 대표 질의)한 결과:

| 질의 | 결과 | 문제 |
|------|------|------|
| DUR 병용/임부 · e약은요 | ✅ tool_calling | 정상 |
| 대체조제 · 코드해석 | ❌ type=chat | 도구 있으나 qwen3:8b가 미호출(일반챗 폴백) |
| 의료법 · 삭감 · 약가코드 | ❌ type=workflow | 거친 인텐트 패턴('진료'/'청구'/'삭감')이 일반 워크플로우로 가로챔 |

두 근본 원인: ①`CHAT_INTENTS` 패턴이 의료 질의를 일반 워크플로우로 가로챔 ②LLM tool-calling 불확실.

## 변경 — `core/medical_router.py` 결정적 우선 라우터

`orch_chat` **최상단**(인텐트 탐지 전)에 결정적 라우터를 배치해 LLM 불확실성·워크플로우
가로채기를 우회. **명확한 의도+엔티티일 때만** 라우팅(보수적), 미매칭은 기존 흐름 유지.

- 제품코드 + 대체 의도 → `drug_substitution`
- 바코드(표준코드)/제품코드 + 해석·분류 의도 → `drug_resolve`
- 의료법/약사법/진료기록 보존/신고의무/동의서/급여기준/대체조제 청구 → `medical_law_ask`
- 삭감/심사기준 → `notice_search`

DUR·e약은요는 기존 tool-calling이 정상이라 유지. 순수 로직(re만 의존)으로 분리 = 테스트 가능.

## 검증
- iMac: `pytest tests/test_medical_direct_route.py` → 10 passed.
- GPU(배포 후): 대체조제/코드해석/의료법/삭감 질의가 정본 도구로 직결(type=tool_calling intent=medical_direct).
