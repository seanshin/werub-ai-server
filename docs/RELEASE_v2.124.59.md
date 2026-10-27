# Release v2.124.59 — `/api/chat` 요청 필드 유실 4종 봉합 (그룹웨어 V0 P0 외)

날짜: 2026-07-30 | 종류: fix(chat/cache) | 배포: GPU graceful HUP(NRestarts 0)

## 배경

그룹웨어 재상신 `ai-server-open-items-2026-07-30.md`. **V0(P0)가 v2.124.50 회신에서 누락**돼
있었고, 재현 3회로 다시 올라왔다. 조사 결과 V0는 실재했고 **같은 클래스 결함 3건이 더** 있었다.

공통 클래스 = **호출자가 준 값이 조용히 버려진다**. 오류가 아니라 200 + 그럴듯한 답이라
호출자 로그에 흔적이 없다.

## 1. 🔴 V0 — 캐시 키가 이미지·파라미터를 무시 (P0)

`/api/chat` 캐시 키가 `md5(model + 사용자 메시지 텍스트)`뿐이었다. **이미지가 달라도 같은 키**라
직전 응답을 200으로 반환한다 — 그룹웨어 표현대로 *A 고객 명함을 올렸는데 B 고객 정보가 저장*된다.

- fix: `core/cache.chat_cache_key()` — 모델·**전 역할 메시지**(system 포함)·**이미지 sha256
  지문**·think·options·format·tools를 정렬 직렬화해 sha256. `data:` 접두는 동일 취급.
- 큐 경로(`/api/queue/submit` ↔ 워커)도 같은 클래스(options 무시)라 `generate_cache_key()`로 통일.
- 라이브 실증(스트리밍=캐시 대상 경로): 빨강→"빨간색"·파랑→"파란색"·노랑→"노란색" 전부
  `cached=false` 정상 실행 / **동일 요청 반복은 1ms `cached=true`**(캐시 이득 보존) /
  think만 바꾸면 `cached=false`로 재실행.

## 2. V5 — `think`가 애초에 전달되지 않고 있었다 + 엔진 한계

그룹웨어는 "억제가 불완전"으로 봤으나 실제로는 **`/api/chat`이 `think`를 업스트림에 보내지
않았다**(페이로드가 `{model, messages, stream}`뿐).

- fix: `chat_payload()`로 `think`·`options`·`format`·`tools` 전달.
- ⚠️ **전달해도 억제되지 않는다** — Ollama 0.32.5에 직접 물어도 `qwen3-vl:8b`는
  `think:false`에서 thinking 3,025자(미지정 2,264 / `true` 7,034). **엔진·모델 특성**이며
  서버가 개입할 지점이 없다. 문서에 "완전 억제 아님"으로 명시.
- 🔑 **전달 시작이 만든 새 위험 차단**: 비-thinking 모델은 `think`를 받으면 요청 전체를
  거절한다(`"qwen2.5:14b" does not support thinking`). `/api/show` capabilities 확인
  (`model_supports_thinking`, 10분 TTL 캐시·조회 실패 시 전달)을 앞에 두어 기존 호출자를
  보호했다. 라이브: qwen2.5:14b + `think:true` → 정상 200(종전 로직이면 업스트림 에러).

## 3. 추가 발견 — `/api/chat`만 남아 있던 비대칭 2건

| 결함 | 영향 | 비고 |
|------|------|------|
| `options` 미전달 | temperature·num_ctx 지정이 **무시** | `/api/generate`는 v2.124.32에서 해소된 것 |
| messages의 `data:` 접두 미제거 | Ollama가 **이미지를 무시** | `/api/generate`는 v2.124.50에서 해소된 것 |

같은 결함을 경로별로 따로 고쳐 온 탓에 남은 잔재다. 이제 `chat_payload()` 단일 소스.

## 4. 회귀

`tests/test_chat_cache_key.py` 13 + `tests/test_chat_payload.py` 11 = **24 신규**.
V0 프로브(빨강/파랑)를 테스트로 고정했고, 캐시 이득 보존·기존 호출 페이로드 불변·
호출자 객체 미변형도 함께 고정.

## 5. 측정으로 답한 잔여 항목(회신서에 수록)

- **V4 규격**: 게이트웨이 본문 **10MB**(11MB→413 nginx HTML) · 앱 20MB · PNG/JPEG/GIF 인식 ·
  **WEBP 미인식** · 3000×2000 정상(픽셀 상한·자동 리사이즈 **없음**) · 다중 3장은 형식상
  동작하나 **정확도 붕괴**(n=1) → 장당 1요청 권장.
- **V6 사용량**: `api_activity`에 이미지 장수·픽셀 **미기록**, `request_size`(본문 바이트)만
  있고 **2000행 롤링(≈18분)**이라 예산 집계 부적합 → 호출 측 집계 권장.

회신 정본 `docs/01-plan/references/vision-paths-reply2-to-groupware.md`.
