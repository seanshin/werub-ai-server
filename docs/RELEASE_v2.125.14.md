# v2.125.14 — D2: `repeat_penalty 1.1` 어댑터 핀 — 엔진 기본값 의존 탈피

날짜: 2026-08-24 | 기획 `docs/01-plan/features/remaining-work-2026-08-19.plan.md` §3-D2 | 선행 D1 완료(v2.125.13 이관 대상 0) · A/B 판정 v2.125.9 §5-b

## 1. 무엇을·왜

Ollama 0.32.10이 `repeat_penalty` 기본값을 **1.1→1.0**으로 바꿨다. 우리 fleet **33/42가
Modelfile 값 없이 엔진 기본값에 의존** — 엔진 업그레이드가 곧 전 모델 생성 파라미터 변경이
된다. 사전 등록 A/B(N=40)는 **1.1 유지** 판정(반복 점수만 1.0에서 임계 초과 악화·나머지 노이즈 안).
v2.125.9 때는 직접 호출이 81곳이라 "한 곳 명시"가 불가능해 어댑터 핀을 만들었다 되돌렸고
(반쪽 처치 금지), D1이 이관 대상을 0으로 만든 지금이 그 "한 곳"이 생긴 시점이다.

## 2. 처치

- **`core/engine_defaults.py`(신규)** — `pin_options(model, options)`:
  - 호출자가 `repeat_penalty`를 명시하면 그 값이 이긴다(setdefault 의미론 — A/B 하네스 보호).
  - **모델 Modelfile에 자체 값이 있으면 핀하지 않는다** — `/api/show` `parameters` 판별.
    블랭킷 setdefault는 자체 값 모델(9/42: qwen3 계열=1 · dcarrascosa/medgemma=1.25 ·
    qwen3-coder=1.05 · exaone 계열=1)의 의도된 값을 바꾼다(기획서 §6 함정).
  - `/api/show` **실패 시 핀 생략**(fail-safe=엔진 기본값·0.32.5에선 1.1과 동일 동작).
    조회는 모델별 캐시(성공 1h·실패 60s).
  - 항상 **복사본**을 돌려준다 — 라우팅 표 options dict 제자리 오염 방지.
- **배선**: `core/model_router.OllamaAdapter._payload`/`_chat_payload`(스트림/비스트림 공유
  빌더 두 곳 = 라우터 경유 전 경로의 단일 통로). staticmethod → 인스턴스 메서드(`self.base`).
  embed는 핀 대상 아님(생성 전용 파라미터).
- **직접 호출 허용 목록 25곳+잔재 6곳은 의도적으로 핀 대상이 아니다** — 패스스루 프록시는
  클라이언트 페이로드를 바꾸면 안 되고, heavy/openai_compat은 자기 options 규율, 프로브는
  생성 품질 무관. `_router_enabled()` 폴백(잔재 6)은 핀 없이 종전 그대로 = 문서화된 갭.

## 3. 회귀 — 페이로드 동일성 테스트는 "핀 포함"으로 갱신

- 신규 `tests/test_engine_defaults.py` **14건**: 핀 값=1.1 고정 · 호출자 우선 · 자체 값 모델
  비핀 · 실패 시 생략+실패 캐시 · 모델별 캐시 · 비변이 · 라우터 4경로(generate/chat/stream) 배선 · embed 제외.
- `tests/conftest.py` autouse 픽스처가 `/api/show` 조회를 "자체 값 없음"으로 스텁 —
  단위 테스트 네트워크 금지·핀은 결정적으로 발동. **핀을 끄는 env 방식은 쓰지 않았다**
  (프로덕션과 테스트가 갈린다 — 기획서 §3-D2가 기각한 방향). 핀 로직은 테스트에서도 살아 있다.
- 동일성 회귀 **50건 갱신**(11파일): 기대 options에 `"repeat_penalty": 1.1` 합류.
  `medlaw`·`router_stream`의 "routed==legacy" 계약은 "**routed==legacy+핀**"으로 개정
  (`_pop_pin`이 핀을 검증하며 떼고 나머지 동일성 비교 — 폴백 경로 무핀도 함께 고정).
  `test_story_generate_options_none_becomes_empty`→`_becomes_pin_only` 개명.
- 전체 **1777 passed**(+14) · 갱신은 5그룹 분할(병렬 에이전트 3+직접 2) — v2.125.13 병렬 규율 재사용·충돌 0.

## 4. 검증 (기준은 실행 전 §3-D2에 있던 그대로)

| 축 | 결과 |
|---|---|
| 탐지기 통제군(라이브) | qwen2.5:14b/7b=핀 · qwen3:8b·dcarrascosa/medgemma:Q8_0=비핀 — **첫 실행 1건 MISMATCH는 내 라벨 오류**(v2.125.9의 "medgemma=1.25"는 dcarrascosa 태그판이고 `medgemma:4b`는 자체 값 없음 → 핀이 맞다). 전수: 42모델 중 자체 값 9 |
| 태그 없는 모델명 | `/api/show` 404 → 핀 생략(fail-safe) — 프로덕션은 전부 태그명 사용 확인 |
| 라이브 페이로드 | 라우터 경유 실호출 `options={'repeat_penalty': 1.1}` 실림·정상 응답 |
| **프로덕션 diff 0(값 동일)** | seed 고정·temp 0에서 핀 명시 == 엔진 기본값 출력 **완전 일치**(0.32.5 기본=1.1) |

## 5. 이것이 여는 것 / 여전히 안 하는 것

- **D3(Ollama 0.32.15 업그레이드)의 선행 조건이 닫혔다** — 이제 업그레이드해도 33개 모델의
  repeat_penalty가 조용히 1.0으로 바뀌지 않는다. 단 **트리거 없이는 올리지 않는다**(v2.114.0 규율)
  — 0.32.15 존재는 정보이지 트리거가 아니다.
- 허용 목록 직접 호출의 핀은 하지 않는다(위 §2) — 성격상 정당한 제외이며 인벤토리에 기록됨.
