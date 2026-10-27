# Orchestrator ↔ Smart Gateway 역할 경계 (정합 분석)

> 2026-06-08 | 두 라우팅 레이어(병렬 개발)의 역할 정리 — 중복/혼선/루프 방지

## 1. 결론 먼저

**충돌 없음. 상보적.** `core/platform.py`가 호스트별로 분리 로딩하고, 둘은 서로 다른
워크로드(capability 오프로드 vs chat 진입 라우팅)를 다룬다. 단 **진입점이 둘**이라는 점과
**Phase 3에서 LLM 라우팅 추가 시 루프 위험**을 사전에 못박는다.

## 2. 두 레이어 비교

| | **Node Orchestrator** (내 작업) | **Smart Gateway** (워커측) |
|---|---|---|
| 위치 | **GPU 호스트** (`core/orchestrator/`, GPU에만 로드) | **iMac 호스트** (`routes/gateway.py`, `if is_imac()`만 로드) |
| 진입점 | GPU :8585 (메인 API) | iMac `/api/gateway/chat` |
| 방향 | GPU 앞단 → 특화 capability를 **iMac으로 오프로드** | iMac 앞단 → 복잡 요청을 **GPU로 포워딩** |
| 라우팅 기준 | capability + 노드 부하/발열/지연/breaker | 메시지 **복잡도(intent) 분류**(경량 모델) |
| 대상 워크로드 | detect(현재) / mark·stt·embed(예정) | **LLM chat** |
| 폴백 | 원격 실패 → GPU 로컬 | (게이트웨이 자체 로직) |

→ **호스트도 다르고 워크로드도 다르다.** 같은 머신에서 동시 동작하지 않으며(platform 게이팅),
detect(orchestrator)와 chat(gateway)은 겹치지 않는다.

## 3. 진입점이 둘 — 사용 지침 (혼선 방지)

| 목표 | 호출 | 동작 |
|---|---|---|
| **"GPU만 호출"** (사용자 기본) | GPU :8585 (orchestrator) | detect 등 특화작업 자동 iMac 오프로드 + 폴백 |
| chat 비용/지연 최적화 | iMac `/api/gateway/chat` | 단순=iMac 로컬, 복잡=GPU 포워딩 |

- 기본/canonical 진입점은 **GPU(orchestrator)**. "메인 = GPU"는 유효.
- Gateway는 **chat 전용 대안 진입점**(iMac 근처 LAN 클라이언트·경량 대화 비용 절감). 선택적.
- 두 경로는 다른 워크로드를 다루므로 동시 운용 가능.

## 4. ⚠️ Phase 3 루프 위험 (사전 가드 필수)

현재는 안전(orchestrator=detect, gateway=chat — 무교집합). 그러나 **orchestrator Phase 3에서
`llm_light`(GPU→iMac LLM 위임)를 추가하면** 루프가 가능하다:

```
클라 → iMac gateway (복잡 판정) → GPU 포워딩 → GPU orchestrator(llm_light) → iMac 위임 → ???
```

**가드 (Phase 3 착수 전 필수):**
- gateway가 GPU로 포워딩하는 요청에 마커 헤더 부여 (예: `X-Routed-By: gateway`)
- GPU orchestrator는 해당 마커가 있으면 **LLM capability를 재위임하지 않음**(GPU 로컬 처리 강제)
- 또는 orchestrator `llm_*` capability는 gateway-origin 요청에 대해 `policy=gpu_only` 적용

이로써 "iMac→GPU→iMac" 왕복을 차단한다. detect/mark/stt/embed는 chat이 아니므로 영향 없음.

## 5. 공유 자산 / 중복 점검

| 항목 | 상태 |
|---|---|
| `core/platform.py` | 공유 — 호스트 감지 + feature 게이팅 (둘 다 사용, 충돌 없음) |
| config 변경 API | `/api/gateway/config`(gateway) vs `/api/orchestrator/registry/reload`(orchestrator) — 스코프 다름, OK |
| 복잡도/intent 분류 | gateway만 사용(chat). orchestrator는 capability+부하 기반(메시지 내용 무관) — 중복 아님 |
| 상태 API | `/api/gateway/status` vs `/api/orchestrator/nodes` — 관점 다름(진입 라우팅 vs 노드 헬스) |

## 6. 권고

1. **현행 유지** — 구조적 충돌 없음. 별도 통합 불필요.
2. **문서로 진입점 이원화 명시** (본 문서 + ORCHESTRATOR_GUIDE) — "GPU=기본, iMac gateway=chat 대안".
3. **Phase 3 착수 전 §4 루프 가드 구현** (마커 헤더 + orchestrator gpu_only 강제).
4. (선택) 장기적으로 Admin 콘솔에서 두 경로를 한 화면에 표기(진입 라우팅 + 노드 오프로드).

## 7. 관련
- `docs/ORCHESTRATOR_GUIDE.md` (orchestrator 호출법)
- `routes/gateway.py` / `core/platform.py` (gateway·플랫폼, 워커측)
- `docs/02-design/features/node-orchestrator.design.md` (orchestrator 설계)
