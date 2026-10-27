# v2.125.44 — 의료 생성 실패가 200으로 나가던 것 봉합 (연동 검토 회신)

기준 v2.125.43 · 출처 = 연동 검토 회신

## 0. 한 줄

`_medical_generate`가 모든 실패를 `("Error: …", 0, ms)` **문자열**로 돌려주고 핸들러가
그걸 **200**으로 내보내던 것을, 전용 예외 `MedicalGenerationError` + 전역 에러 핸들러
한 곳(**502/503**) + SSE `error` 이벤트로 바꿨다. 반복 결함 클래스 #1(200인데 빈 결과).

## 1. 무엇이 틀려 있었나

- 발동 조건 = 모델 부재 · 타임아웃 · Ollama HTTP 오류 · **차단기 열림**.
- 호출 **35곳/4파일**(`routes/medical.py`·`medical_advanced.py`·`orchestrator.py`·
  `core/reasoning_chain.py`). medical 핸들러는 `startswith("Error:")`를 보지 않는다
  (story·novel·writing·medical_law는 본다).
- 결과: 응답 200 + 본문 `"answer": "Error: …"` → **HIS는 성공(201)으로 넘김** ·
  `_save_consultation`이 **오류 문구를 상담 이력에 저장** · `_increment_usage`가 과금 ·
  JSON 계약 경로는 `force_json`이 `{"raw": "Error: …"}`로 감싸 구조만 비어 나감.
- 🔑 **부분 실패를 허용하도록 짜인 호출자가 이미 있었다** — 모델 비교·벤치마크·앙상블
  3번째 모델은 `except Exception`으로 실패를 받게 돼 있었는데 예외가 오지 않아, 비교는
  **`status: ok`**, 벤치마크는 정상 케이스, 앙상블은 **정상 단계**로 셌다(`step_skipped`
  미발동). 설계 의도와 반환 계약이 어긋나 있던 것.
- `generate_with_validation` 경로는 `"Error: …"`가 검증기에서 떨어져 **최대 2회 더
  재시도**했다(타임아웃이면 요청 하나가 타임아웃×3을 쓴다). 예외는 루프를 바로 끊는다.
- v2.125.15는 **브레이커만** 정직하게 고쳤고(HTTP 오류를 success로 세던 것) 응답은 뒀다.

## 2. 처치

| 층 | 처치 |
|---|---|
| 생성 | `_medical_generate`가 실패 시 `MedicalGenerationError(reason, status, duration_ms)` 발생. circuit open=**503**, 그 외=**502** |
| dedup | `_do_generate`는 예외 대신 **표식 튜플**(`_GEN_FAILED`)을 돌려주고 밖에서 올린다 — dedup 대기자도 같은 예외를 받는다(dedup 안에서 올리면 대기자는 `None`을 받는다) |
| HTTP | `@medical_bp.app_errorhandler` **한 곳** → `{"error","code":"generation_failed","detail"}` + 502/503. **app 전역**이라 medical_advanced·orchestrator 폴백·fhir 경로도 같은 변환 |
| SSE | 헤더가 나간 뒤라 핸들러가 못 닿는다 → `_sse_generation_guard`가 `event: error` 한 줄로 끝냄(추론 체인·앙상블·자동 의무기록 스트림 3곳) |
| 배치 | `/api/medical/batch`는 **건별** `status: failed`(전체 502 아님) |
| 오케스트레이터 | `_execute_action`의 기존 `except`가 받아 워크플로 단계 `{"error"}`로 — 추가 수정 없음 |

**성공 계약은 불변** — `(response, tokens, ms)` 그대로, 캐시·`accept`·브레이커 동작 동일.
모델이 *정상 응답으로* `"Error…"`를 낸 경우는 종전대로 반환하되 캐시하지 않는다.

## 3. 검증

- 신규 `tests/test_medical_generation_error.py` 10건: HTTP 오류·전송 오류=502 · circuit=503
  (모델 미호출) · 성공 계약 불변 · **실패 비캐시**(다음 호출이 재생성) · 에러 핸들러
  502/503 JSON · SSE 가드 이벤트 · 소스 스캔 3(문자열 반환 재유입·SSE 3곳 가드·배치 건별).
- 기존 `test_medical_generate_error_contracts`를 **새 계약으로 개정**(옛 문자열 계약을
  고정하던 테스트).
- 전체 회귀 **2218 passed**·9 skipped·1 xpassed(GPU 임시 worktree — 라이브 레포 무접촉).

## 4. ⚠️ 연동자 영향 (의도된 계약 변경)

실패하던 요청이 **200 → 502/503**으로 바뀐다. 성공 경로는 무변화. HIS가 이 응답을
"성공 201"로 넘기던 동작이 멈추는 것이 목적이다. 503은 재시도 가능 신호(차단기 열림).

## 5. 부수 발견 (미처치)

- 🟡 `core/llm_resilience.dedup_call`이 **`_inflight_lock`을 쥔 채** `future.result(timeout=180)`로
  대기한다 → 중복 요청 하나가 대기하는 동안 **키가 다른 모든 medical 생성**이 락에서 멈춘다.
  원 요청이 끝나면 풀리므로 교착은 아니나 직렬화다. 이번 범위 밖 — 별건.
- 성공(`res.ok`)인데 **빈 텍스트**인 경우는 이번 처치 대상이 아니다(근거 미관측).
