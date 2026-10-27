# Release v2.110.0 — OpenAI 호환 `/v1/chat/completions` (OpenPencil 연동)

**날짜**: 2026-06-23
**브랜치**: `feature/openai-compat-v1` → `main`
**헤드라인**: 외부 OpenAI Chat Completions 클라이언트(OpenPencil ai-sdk 등)가 표준 OpenAI 포맷으로
우리 AI 서버를 호출할 수 있는 `/v1/chat/completions` + `/v1/models` 엔드포인트 신설.

---

## 배경

OpenPencil(디자인 도구)은 `ToolLoopAgent`로 캔버스를 조작하는 100% 도구 기반 에이전트로,
표준 OpenAI Chat Completions 포맷(`POST /v1/chat/completions`, SSE 스트리밍, `tools`/`tool_calls`)을
사용한다. 매 요청에 **대형 시스템 프롬프트(~27KB) + 90+개 디자인 도구 스키마**를 함께 전송하고,
최대 50 스텝까지 도구 호출 루프를 돈다.

우리 서버는 자체 API(`/api/chat` NDJSON 등)만 노출하고 OpenAI 호환 `/v1`을 노출하지 않아
OpenPencil이 직접 붙을 수 없었다.

## 설계 결정 — 순수 패스스루가 아닌 `/api/chat` 변환 프록시

Ollama 0.21은 `/v1/chat/completions`를 네이티브로 제공한다. 그러나 **순수 패스스루는 채택하지 않았다**.

- 이유: Ollama `/v1`은 모델 로드 시점의 컨텍스트(전역 `OLLAMA_CONTEXT_LENGTH`, 현재 **8192**)에 묶인다.
  OpenPencil의 27KB 프롬프트 + 90개 도구 스키마는 8192 토큰을 초과 → **조용히 절단(silent truncation)**되어
  모델이 엉뚱하게 동작한다. OpenAI 포맷에는 컨텍스트 길이를 지정할 파라미터가 없어 패스스루로는 제어 불가.
- 채택: OpenAI 요청을 Ollama **`/api/chat`** 포맷으로 변환하면서 **요청별 `options.num_ctx`를 주입**한다.
  전역 env(= 전 모델의 VRAM)를 건드리지 않고 OpenPencil 트래픽에만 필요한 컨텍스트를 부여한다.

### GPU 실측 — 컨텍스트별 VRAM/적재 (RTX 5080 16GB)

| 구성 | VRAM | 적재 | 속도 |
|---|---|---|---|
| qwen2.5:14b @ 16K | ~13–14GB | GPU 완전 | ~15 tok/s |
| qwen2.5:14b @ 32K | 15.48GB + CPU 스필 | **부분 CPU 오프로드** | 급격히 느림 |
| qwen2.5:7b @ 16K | 10.16GB | GPU 완전 | ~30 tok/s |
| qwen2.5:7b @ 32K | ~15GB | GPU 완전 | ~30 tok/s |

**모델 위계**: 컨텍스트 16K로 충분하면 **14b**(품질↑·GPU 완전 적재). 32K가 필요하면 **7b**
(14b@32K는 CPU 스필로 50스텝 에이전트 루프가 느려짐). 기본 `num_ctx=16384`는 14b/7b 양쪽 16GB에 적합.

## 구현

### `routes/openai_compat.py` (신규)

순수 변환 함수(Flask/네트워크 비의존 — 단위테스트 대상)로 분리:

- `build_ollama_payload(body, num_ctx)`: OpenAI 요청 → Ollama `/api/chat` 페이로드.
  `messages`/`tools` 패스스루(OpenAI function 스키마 = Ollama 스키마), `options`에
  `num_ctx`(주입)·`num_predict`(`max_completion_tokens`/`max_tokens`)·`temperature`·`top_p`·`stop`·`seed` 매핑.
- `_openai_tool_calls(...)`: Ollama tool_calls(`arguments`=객체) → OpenAI(`arguments`=**JSON 문자열**). OpenAI 규격 준수.
- `ollama_chunk_to_openai(...)`: 스트림 청크 → OpenAI `chat.completion.chunk`(첫 델타만 `role`, content/tool_calls 델타).
- `build_finish_chunk(...)`: `finish_reason`(tool_calls/stop) + 선택 usage.
- `ollama_final_to_openai(...)`: 비스트리밍 단건 → OpenAI `chat.completion`(message + usage).
- `models_to_openai(tags)`: Ollama `/api/tags` → OpenAI `/v1/models` 리스트.

엔드포인트:
- `POST /v1/chat/completions`: 스트리밍 시 Ollama NDJSON → **OpenAI SSE**(`data: {...}\n\n` + `data: [DONE]`),
  `text/event-stream` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`(nginx 버퍼링 비활성). 비스트리밍 시 단건 JSON.
  업스트림 오류/연결 실패는 OpenAI 에러 포맷으로 변환.
- `GET /v1/models`.

### `app.py`

- `openai_bp` 등록. `before_request`에서 **`OPTIONS`는 인증 면제**(브라우저 CORS preflight) 후 `_blueprint_auth()`.
- `init_openai_compat(ollama_base=OLLAMA_BASE, load_config=load_config)`.

### config

- `openpencil_num_ctx`(기본 **16384**, 상한 131072). config.json에서 무재배포 튜닝.

## 검증 (GPU 라이브)

- 전체 회귀 **667 passed / 7 skipped**(베이스라인 647 + 신규 `tests/test_openai_compat.py` 20, 회귀 0).
- graceful HUP 리로드: NRestarts 0·health 200.
- 로컬 E2E: 비스트리밍(content/usage)·tool calling(finish=tool_calls·arguments JSON 문자열 파싱)·스트리밍 SSE(`[DONE]`)·`/v1/models`(37).
- 인증: 프록시(XFF)/외부 무키 → **401**, OPTIONS preflight → 면제(2xx).
- **외부 E2E**: `https://example.org/ollama/v1/chat/completions` + Bearer API 키 → tool calling(`createFrame` args JSON 문자열)·스트리밍 전부 통과.

## OpenPencil 설정

```
API Type        : OpenAI-compatible → completions
Base URL        : https://example.org/ollama/v1
API Key         : (콘솔 발급 sk-...)
Custom Model ID : qwen2.5:7b   (또는 qwen2.5:14b)
```

- 컨텍스트는 서버가 `options.num_ctx`를 주입한다(OpenPencil이 설정 불가). 응답이 절단/엉키면
  config `openpencil_num_ctx`를 32768로 올리고 모델을 7b로(14b@32K는 CPU 스필).
- 인증: 외부=Bearer API 키 필수, localhost=bypass.

## 파일

- 신규: `routes/openai_compat.py`, `tests/test_openai_compat.py`, `docs/RELEASE_v2.110.0.md`
- 수정: `app.py`(블루프린트 등록+init), `CLAUDE.md`/`README.md`(헤더+API 레퍼런스)

## v2.110.0 패치 — 브라우저 CORS (allow-origin)

OpenPencil이 브라우저 앱이라 `Failed to fetch`(CORS) 발생. 진단: 응답에 `Access-Control-Allow-Headers`/
`-Methods`/`-Max-Age`는 있으나 **`Access-Control-Allow-Origin`이 빠짐** — CORS는 Flask `add_cors_headers`
(`app.py`)가 처리하는데(nginx 아님), allow-origin이 `allowed_origins` 화이트리스트에만 조건부로 설정되어
OpenPencil 오리진(외부)에 미설정됨. curl/E2E는 CORS 비강제라 통과했던 것.

수정: **`/v1/*` 경로는 요청 Origin을 반사**(`Vary: Origin` 동반). `/v1`은 서드파티 OpenAI 클라이언트
전용 표면이고 Bearer API 키로 보호되므로 임의 오리진 반사가 적절. 전역 와일드카드는 미복원(그 외 경로는
화이트리스트 유지=v2.86.x 하드닝 보존). nginx add_header는 Flask 헤더와 이중→"multiple values" 에러
위험이라 미채택(한 레이어에서 처리). 라이브 검증: 임의 Origin POST/OPTIONS 응답에 `access-control-allow-origin`
반사 확인.

## v2.110.0 패치 2 — 멀티턴 tool 라운드트립 + num_ctx + tool_choice (OpenPencil 에이전트 루프)

OpenPencil 실측 리포트 반영. 에이전트 루프(도구 호출→결과 되먹임→다음 스텝) 블로커 해소.

- **A.1 (최우선 블로커)**: 메시지 히스토리에 `assistant{tool_calls}`+`role:"tool"`를 넣어 보내면
  `ollama error 400: "Value looks like object, can't find closing '}'"`로 1스텝 뒤 무조건 멈춤.
  **근본 원인**: OpenAI는 assistant tool_calls의 `arguments`를 **JSON 문자열**로 보내지만 Ollama
  `/api/chat`는 **객체**를 기대(GPU 재현: 문자열→400, 객체→200). 응답 방향(객체→문자열)만 변환했고
  **입력 방향(문자열→객체) 역변환이 누락**돼 있었다. → `normalize_inbound_messages`로 인바운드
  assistant.tool_calls.arguments를 객체로 역변환(빈/비JSON 문자열→`{}`). id/type/tool_call_id 등
  OpenAI 잉여 필드는 Ollama가 관용하므로 arguments만 손댐.
- **B.2 num_ctx**: 기본 `openpencil_num_ctx` **16384→32768**. num_ctx는 input+output 총량인데
  OpenPencil 입력(27KB 시스템 ~7K토큰 + 90+도구 ~11K토큰 ≈ 17K)이 16384를 초과 → 32768 필요(아니면
  입력 절단). **🔴 VRAM 제약(16GB)**: Ollama는 KV를 `num_ctx × OLLAMA_NUM_PARALLEL`로 잡는다(현재
  parallel=4). GPU 실측 7b@16K=10.16GB GPU완전 / **7b@32K=17.81GB CPU 부분 스필**(32K×4 KV). 즉
  parallel=4에선 32K가 동작하나 느림(50스텝 에이전트 부적합). **GPU 완전+빠르게 = `OLLAMA_NUM_PARALLEL=2`
  권장**(7b@32K≈11GB). parallel=4·16GB에선 "빠르면서 17K 입력 수용" 설정이 없음(16GB 천장).
- **B.4 tool_choice**: Ollama 0.21 `/api/chat`는 tool_choice 미강제(실측). `"none"`→도구 미전달(비활성),
  `"required"`/named→시스템 지시로 best-effort 유도(`_tool_choice_nudge`).

회귀 test_openai_compat 20→**30**. GPU 라이브 E2E: 멀티턴 tool 히스토리 → HTTP 200 + 모델 이어받음(비스트림/스트림).

## 잔여/후속

- 모델명 fuzzy 매칭 미적용(OpenPencil은 정확한 Ollama 모델 ID를 보냄 — 불요).
- `tool_choice`/`n`/`presence_penalty` 등 일부 OpenAI 파라미터는 Ollama `/api/chat` 미지원이라 무시.
- 다중 `n`(choices) 미지원(단일 choice).
