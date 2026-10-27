# Release v2.73.0 — 회귀 테스트 인프라 + 품질 하드닝

> 릴리즈: 2026-06-06 | 이전: v2.72.0

## 요약

v2.72.0의 신뢰성 수정(Engine Down 오탐, watchdog, config화)을 **회귀 테스트로 고정**하고,
순수 core 모듈 전반에 단위 테스트를 신규 도입했다. 테스트 0개 수준이던 핵심 로직에
**157개 테스트(8파일)** 를 추가해 향후 회귀를 자동 차단한다. 함께 모니터링 관측성과
테스트 용이성을 위한 소규모 리팩터를 수행했다.

---

## ✨ 신규 — 회귀 테스트 157건 (8파일)

| 파일 | 건수 | 대상 |
|------|------|------|
| `test_orchestrator_config.py` | 17 | general_chat/workflow 모델 config 타입·범위 가드 |
| `test_ollama_health.py` | 8 | Engine Down 연속실패 판정(블립 흡수) |
| `test_tool_registry.py` | 19 | Tool Calling 레지스트리(load/defaults/enabled/reload/에러내성) |
| `test_priority_queue.py` | 20 | 도메인 우선순위 매핑·응급/긴급 키워드 분류 |
| `test_hr_law.py` | 13 | 질문 난이도 분류·모델 선택·법령 인용 포맷 |
| `test_medical_validator.py` | 20 | **의료 안전** — 비정상 고용량 감지·ICD·검증 오케스트레이션 |
| `test_quality.py` | 37 | force_json 계열·코드 보안 스캔(eval/shell-injection)·Python 문법 |
| `test_validator.py` | 23 | 입력 스키마 검증(required/type/length/enum/range/default) |

- 순수 모듈 대상이라 **iMac(의존성 없는 환경)에서도 실행** 가능(의존 필요한 것은 `importorskip` 자동 skip)
- 실행: GPU `venv/bin/python -m pytest`(pytest 9.0.3). 전체 **157 PASS (0.2s)**

---

## 🔧 리팩터 — get_ollama_status 로직 분리

`get_ollama_status`의 연속실패 판정(v2.72.0 P1-B 오탐 수정)을 순수 모듈
`core/ollama_health.evaluate_status`로 추출. app.py는 HTTP 호출만 담당하고
판정 로직은 의존성 없는 순수 함수가 되어 단위 테스트 가능해졌다. (동작 동일, 무회귀)

---

## 🐛 관측성 — dashboard 모니터링 침묵 제거

`routes/dashboard_api.py`에서 GPU/터널/서버 상태 수집 실패가 `except Exception: pass`로
조용히 묻혀 '지표 빈값'을 정상으로 오인하던 4곳(v2.72.0의 mem/swap 로케일 버그와 동일 계열)을
`logger.debug`로 전환. 나머지 침묵 swallow 23건은 정당한 best-effort로 의도적 유지.

---

## 커밋 (v2.72.0 → v2.73.0)

```
77dba4f chore: dashboard 모니터링 지표 침묵 swallow 4건 → debug 로깅 (P3-G)
8dfa8cc refactor: get_ollama_status 연속실패 판정 로직 추출 + 테스트
af1d0da test: orchestrator config 로더 회귀 테스트 (17)
34528dc test: core.tool_registry 단위 테스트 (19)
06c1e33 test: priority_queue + hr_law 순수함수 테스트 (33)
d553169 test: medical_validator 단위 테스트 (20, 의료 안전)
93db762 test: quality + validator 순수함수 테스트 (60)
```

## 운영 메모

- 테스트 실행: GPU `cd ~/WeRUBLLMManager && venv/bin/python -m pytest tests/ -q`
  (시스템 `/usr/bin/python3`엔 pytest 없음 — 반드시 venv 사용)
- 미테스트 순수 core 잔여(점진 확대 후보): web_search/drug_*/llm_gateway/reasoning_chain 등
  (대부분 외부 HTTP/Ollama 의존이라 단위테스트 가치 낮음)
