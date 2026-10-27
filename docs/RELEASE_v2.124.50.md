# Release v2.124.50 — 비전 입력 경로 3종 봉합 (그룹웨어 제보)

날짜: 2026-07-29 | 종류: fix(vision) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

그룹웨어가 명함 OCR E2E 실패를 추적하다 **같은 이미지·프롬프트로 경로만 바꾼 실측표**를
제출했다: `/api/chat+images` ✅ / `/api/generate+images` ❌ **조용히 무시** /
`/v1+image_url` ❌ **502**. 셋 다 우리 쪽 결함이었고, 조사 중 **네 번째 경로에서 추가 결함**을
찾았다.

## 수정 3건

1. **`/api/generate` 이미지 무시**: 핸들러가 페이로드에 `images`를 넣지 않아 오류 없이
   버려졌다(모델이 "이미지가 포함되어 있지 않습니다"라고 답한 이유). `_normalize_images()`로
   전달 + `data:` URI 접두 제거(Ollama는 raw base64만 수용).
2. **`/v1/chat/completions` 502**: OpenAI 멀티모달 `content` 배열을 변환 없이 넘겨 업스트림이
   거절했다 → `_convert_multimodal_content()`(content 문자열 + images[]).
   원격 http(s) URL은 **서버가 받아오지 않는다**(요청 경로 SSRF 금지 규율) → 명시적 **400**.
3. 🔴 **`/api/vision/describe` 빈 응답**(추가 발견): `language=ko`에서 영문 우세 출력이
   `force_korean` 필터를 타면 **영문이 전부 제거돼 `description: ""`이 200으로** 나갔다.
   빈 응답 502 가드는 필터 **이전**이라 못 잡았고 호출자에겐 "인식 실패"로 보였다.
   → `_keep_content()`: 필터 결과가 비거나 **기호만 남으면 원문 유지**(한국어화보다 내용 보존).
   + `model` 폼 파라미터 허용(한글 OCR은 qwen3-vl:8b가 정확·기본 minicpm-v는 오독).

## 라이브 검증

| 경로 | 결과 |
|------|------|
| `/api/generate + images` | **200** · `INVOICE NO 77281 / TOTAL 45,900 KRW` 정확 |
| `/v1 + image_url`(data URI) | **200** · 동일 판독 |
| `/v1 + https URL` | **400** + 사유(SSRF 금지) |
| `/api/chat + images`(대조군) | 200 불변 |
| `/api/vision/describe` ko | 수정 전 `''` → 수정 후 내용 유지 |
| `model=qwen3-vl:8b` | `LEE SOO KYUNG DAESUNG 010-1234-5678` 정확 |

## 부수 관찰

- **`feedback_qwen3vl_vision_broken` 메모리는 갱신 필요** — qwen3-vl:8b vision은 `/api/chat`·
  `/api/generate`·`/v1` 모두 **정상 동작**함을 실측(과거 `/api/vision/describe` 빈 응답은
  이번에 밝혀진 **후처리 필터**가 원인일 가능성이 크다).
- 기본 비전 모델(`VISION_MODEL=minicpm-v`) 교체는 다른 호출자 영향이 있어 **별도 판단**.

## 검증

회귀 `tests/test_vision_paths.py` 7 + `tests/test_vision_korean.py` +4 = 계열 **15 passed**.

## 회신

`docs/01-plan/references/vision-paths-reply-to-groupware.md`
