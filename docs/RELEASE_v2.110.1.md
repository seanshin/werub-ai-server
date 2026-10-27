# Release v2.110.1 — 보안성 검토 후 `/v1` 표면 하드닝

**날짜**: 2026-06-29
**유형**: 보안 패치 (인터페이스 무변경)
**대상**: `routes/openai_compat.py` (v2.110.0 신규 외부 노출 `/v1` OpenAI 호환 엔드포인트)
**검토 기록**: 메모리 `project_security_review_20260629`

## 배경

[2026-06-23 전면 4축 보안 감사](RELEASE_v2.109.2.md) 이후 새로 추가된 공격 표면은 사실상
v2.110.0의 **외부 노출 `/v1/chat/completions`+`/v1/models`**(OpenPencil 연동)뿐. 이 표면을 중점
재검토 → **인증 fail-closed·CORS 안전·SSRF 불가**를 라이브 4종으로 확인(CRITICAL/HIGH 0건),
경미 하드닝 2건만 패치.

## 변경 (경미 하드닝 2건)

### 1. `num_predict` 상한 캡 (`build_ollama_payload`)
OpenAI `max_tokens`/`max_completion_tokens`(→ Ollama `num_predict`)가 요청 비제한이던 것을
`_MAX_NUM_PREDICT = 16384`로 캡. 인증된 클라이언트가 초장문 생성을 요청해 단일 Ollama 워커
(`OLLAMA_MAX_LOADED_MODELS=1`)를 타임아웃까지 점유하는 자원 남용 방지. 정상 출력엔 충분.

### 2. 업스트림 에러 메시지 일반화 (`chat_completions`)
Ollama 내부 에러 본문(`r.text[:300]`)·내부 커넥션 에러(`{e}`, 내부 URL 포함)를 외부 클라이언트에
그대로 노출하던 것을 일반화:
- `"upstream model error (<status>)"` / `"upstream connection failed"`
- 원본 detail은 `logger.warning`으로 **server-side만** 기록.
- 스트림·비스트림 경로 + 커넥션 실패 4곳 전부.

## 안전 확인 (변경 없음, 검증만)
- **인증**: `_openai_compat_auth`(OPTIONS만 면제)→`_blueprint_auth()` fail-closed Bearer. 외부 무키 401.
- **CORS**: `/v1/*`만 Origin 반사, `Access-Control-Allow-Credentials` 미설정 + Bearer 인증 → 반사가 우회 아님. 비-`/v1` 화이트리스트 유지.
- **SSRF**: `_OLLAMA_BASE` init-time 고정(사용자 비제어), `model`은 `/api/chat`로만(자동 pull 없음).
- **num_ctx**: 서버 config 기반(요청 비제어), 상한 131072.

## 검증
- 로컬 py_compile. GPU venv `pytest tests/test_openai_compat.py` **31 passed**(30→31, `test_payload_num_predict_capped` 추가).
- graceful HUP(MainPID 유지·**NRestarts 0**)·health 200.
- **라이브**: 잘못된 모델 → `"upstream model error (404)"`(내부 텍스트 미노출)·정상 7b 호출 OK(`max_tokens=10` 준수)·외부 무키 `/v1` → **401** 유지.

## 잔여 (기존 6/23 감사분, 변동 없음)
- P2 신중처리 M3(테넌트키 평문)·M4(medical_bp fail-open)·M5(잡 IDOR) = HIS(X-Medical-Key)/트윈 협의+스테이징 필요.
- admin/user 비번 로테이션 = 저장소 PRIVATE라 저긴급.
