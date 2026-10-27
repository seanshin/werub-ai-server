# v2.125.69 — 대기열 거절(busy)을 정직한 재시도 신호로: 502 → 429+Retry-After, 공용 브레이커 오염 제거

기준 v2.125.68 · 발견 경위: 2026-10-07 서버 점검(읽기 전용)이 7일 5xx 3건 중 HIS 2건을 판독 · 지시서 `memory/project_busy_rejection_fix_brief.md`

## 결함

2026-09-30 14:38:27 `POST /api/medical/predict/complication` **502 ×2**(UA `node` — HIS). 로그:
`[model_router] generate failed model=thewindmom/llama3-med42-8b: LLM server busy (active=1/3, wait_queue=1/20)` → `[medical] 생성 실패 → 502`.

1. **거절이 고장으로 보고됐다.** `ChatFairQueue(max_global=3, max_per_user=1, wait_timeout=60)`가 같은 키의 동시 요청을
   60s 넘게 기다리게 한 뒤 거절했는데(전역 슬롯은 1/3 — 사용자당 한도), `_GatedOllamaSession`이 그냥 `RuntimeError`를 던지고
   라우터가 문자열로 싣고 medical이 **502**로 냈다. HIS는 '서버 고장'과 '잠시 후 다시'를 못 가른다.
   `/api/chat` 계열은 같은 거절을 이미 **429+retry_after**로 내고 있었다(계약 불일치).
2. 🔴 **거절이 공용 서킷 브레이커 실패로 세졌다.** `ollama_breaker`(연속 3회 → OPEN 30s)는 의료·상담·스토리·소설·글쓰기가 공유한다.
   한 클라이언트의 동시 거절 3건이면 **전 도메인 30초 503**. — 60일 OPEN 이력 2건(09-28)은 둘 다 타임아웃 건이고
   **busy로 열린 실례는 0** ⇒ 이 항목은 **예방**이다.
3. 로그가 원인을 가렸다 — `active=1/3`만 있어 '전역 여유가 있는데 거절'로 읽혔다. 사용자당 한도에 걸렸다는 사실이 없었다.

## 처치

1. `core/llm_resilience.LLMBusyError(RuntimeError)` — 메시지 문자열은 종전과 동일(로그 grep 호환), `RuntimeError` 상속이라
   기존 `except RuntimeError/Exception`은 그대로 잡는다. 헬퍼 `is_busy`·`result_error`·`record_failure_unless_busy`·`busy_payload`.
2. `app._GatedOllamaSession.post` — 거절 시 `LLMBusyError` + **`[fair-queue] busy key=… active=… per_user=…/… wait_queue=… waited=…s`** 경고 한 줄.
3. `core/model_router` — `Result.busy`·`retry_after` 필드(성공 경로 영향 없음). `generate`·`chat`이 예외 종류를 보존한다. 스트림은 원래 예외를 던지므로 자연 보존.
4. 브레이커를 공유하는 **5곳**(medical·counseling 비스트림/스트림·story·novel·writing)이 busy를 `record_failure`하지 않는다. 직접 `record_failure()` 호출은 0곳이 됐다(전부 헬퍼 경유).
5. 응답 계약:
   - **medical** `MedicalGenerationError(status=429, retry_after)` → 핸들러 **429 + `Retry-After`** + `{"error":"server busy","code":"server_busy","retry_after":5,…}` · SSE `code: server_busy`. **dedup 대기자도 같은 429**(실패 표식 튜플 4번째 칸).
   - **counseling** `/api/counseling/chat` — 종전 `RuntimeError → 503 '불안정'`보다 먼저 busy → **429**.
   - **앱 전역** `@app.errorhandler(LLMBusyError)` → 핸들러 밖으로 빠져나온 거절은 500이 아니라 429.

## 범위 밖(기록)

- **story·novel·writing의 응답 코드** — `_generate`가 `"Error: …"` 문자열을 돌려주고 핸들러 ~25곳이 `startswith("Error:")` → **500**으로 낸다
  (circuit open도 500). busy도 여전히 500으로 나간다. 핸들러 25곳 수정은 이 릴리즈 범위 밖 — **브레이커 오염(전 도메인 영향)만 막았다**.
- 사용자당 한도·대기 시간·브레이커 임계값 **불변**(효과 미확인 처치 금지 — 2개월 2건).
- 공용 브레이커를 도메인별로 나누는 것 — 09-28 사례는 **진짜 백엔드 실패도 전 도메인을 같이 막는다**는 실증이지만 별도 판단.
- 회의 파이프라인(`core/meeting_pipeline.py`)은 백그라운드 스레드라 게이트를 거치지 않는다(busy 불가) — 손대지 않음.

## 연동자 영향

- **HIS**: 의료 API 동시 요청 거절이 **502 → 429**(`Retry-After: 5`). 안내문 초안 `docs/01-plan/references/his-busy-429-notice-2026-10-07.md`(발송은 운영자).
- 상담 API 동시 요청 거절이 503 → 429. 그 외 성공 계약 불변.

## 검증

- 신규 `tests/test_busy_rejection.py` **20건**: 게이트 타입+로그 · 라우터 종류 보존(generate/chat) · 브레이커(busy 3연속 CLOSED / 진짜 실패 3연속 OPEN — 음성 통제) ·
  medical 429·dedup 대기자 429·핸들러 형식·SSE · story/novel/writing busy=브레이커 미기록·비busy=기록 · counseling 브레이커·라우트 429 · 앱 전역 핸들러.
- **구 코드 측정**(예외 클래스·헬퍼만 신 파일, 게이트·라우터·도메인은 구 코드): **14 실패 / 6 통과** — 통과 6은 전부 불변식
  (진짜 실패 502·기록, 비busy 기록, 헬퍼 단위). 실행 위치: GPU 서버 `/tmp` 격리 worktree(운영 디렉터리 무변경·종료 후 제거).
- 전체 **2430 passed**(13 skipped). ⚠️ 1차 전체 실행에서 `test_migration_progress_is_tracked` 실패 — 내 주석이 `model_router`라는 단어를 써서
  `core/llm_resilience.py`가 '라우터 이관 호출자'로 잡혔다(실제 호출 없음). 목록에 넣지 않고 주석 표현을 '라우터'로 바꿨다(목록이 운영 사실과 달라지면 가드가 뭘 막는지 증명 못 한다).

## 배포 후 점검 세션이 볼 것

- `journalctl -u ollama-admin | grep -E "\[fair-queue\] busy|Circuit breaker → OPEN|\[breaker\] busy"` — busy가 OPEN으로 이어지지 않는지, per_user가 찍히는지.
- 의료 경로 502 중 `server busy` 사유 0 · 429 전환.
