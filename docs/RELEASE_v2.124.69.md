# v2.124.69 — 서로게이트 500 봉합 · RAG 경로 thinking 모델 지원

> 2026-08-03 · v2.124.68 잔여 과제 2건 처리 · 회귀 8건 · graceful HUP 무중단

## 1. 🔴 `/api/chat` 서로게이트 500 봉합 (실측 1,726건)

```
core/cache.py:70  response_cache_key
  ← chat_cache_key ← routes/ollama.py:3191 api_chat
UnicodeEncodeError: 'utf-8' codec can't encode character '\ud83c': surrogates not allowed
```

**원인**: JS 클라이언트가 이모지 중간에서 문자열을 자르면(`slice`/`substring`) 짝 잃은 서로게이트가 넘어온다. `json.dumps(..., ensure_ascii=False)` 결과를 UTF-8로 인코딩할 때 터진다.
**v2.124.59에서 캐시 충돌**(A 명함 → B 고객 응답)**을 고치며 도입한 `chat_cache_key`가 새 500 경로를 만든 것** — 캐시 키 함수는 어떤 입력에도 죽으면 안 된다.

**fix**: `_encode()` 신설 — `text.encode("utf-8", "surrogatepass")`.
`errors="replace"`는 **서로 다른 입력이 같은 키가 될 수 있어 부적합**하다(그 자체가 v2.124.59가 고친 결함 클래스). `surrogatepass`는 서로게이트를 보존해 인코딩하므로 **키 구별성이 유지**된다. `_image_digest`도 동일 적용.

**라이브 검증**: 서로게이트 포함 요청 → **HTTP 500 → 200**.

## 2. RAG 경로 thinking 모델 지원 (`_think_off`)

**증상**: `/api/medical-law/ask`에 `model=qwen3.6:35b-a3b`를 주면 **빈 응답(길이 0)**. `_generate`가 `think`를 전달하지 않아 thinking 모델이 추론 토큰만 내고 `response`가 빈다.

**fix**: `routes/medical_law._think_off(model)` — `routes.ollama.model_supports_thinking`(v2.124.59의 `/api/show` capabilities 10분 캐시) 재사용해 **지원 모델에만** `think: False` 주입. `_generate`·`_generate_json` 양쪽 적용.

| 상황 | 동작 | 이유 |
|------|------|------|
| thinking 모델 | `think: False` | 빈 응답 방지 |
| 비-thinking 모델 | **주입 안 함** | 실으면 Ollama가 **요청 전체를 거절**(v2.124.59 실측) |
| capabilities 조회 실패 | **주입 안 함** | 기존 동작 보존(fail-safe) |

**라이브 검증**: 답변 길이 **0 → 439**, 시행규칙 제15조제1항·각 호·단서(1회 한정 연장)까지 정확 인용.

> 🔑 이것으로 v2.124.68에서 켠 heavy tier(`qwen3.6:35b-a3b`, grounded 충실도 0.946)를 **의료법 RAG 자문에서 실제로 쓸 수 있게 됐다.** 배포 직후 `/api/chat/heavy`가 RAG 없이 "제27조·3년"으로 틀렸던 것과 대비 — **근거를 주면 값어치가 나온다**는 A/B 결과와 일치.

## 3. 회귀 8건

- `tests/test_cache_surrogate.py` 5 — 예외 없음 · **키 구별성 유지**(replace였다면 충돌) · 정상 텍스트 무영향 · 이미지 지문
- `tests/test_medlaw_think.py` 3 — thinking 주입 · 비-thinking 미주입 · 조회 실패 fail-safe

## 4. 검증

graceful HUP · **NRestarts 0** · health true · `/api/chat` 200 · `/api/medical-law/ask` 정상.
