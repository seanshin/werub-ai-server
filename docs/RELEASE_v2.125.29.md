# v2.125.29 — 전체 API 표면 감사 + 버전 응답 기능

날짜: 2026-08-31 | 범위: 561 경로 / 591 오퍼레이션 전수

## 1. 감사 — 축을 정해 전수 측정

눈으로 볼 수 없는 규모라 품질 축을 정의해 라이브 `openapi.json` + 소스를 대조했다
(`/tmp/api_audit.py` 방식, 읽기 전용).

| 축 | 감사 전 | 감사 후 |
|---|---|---|
| 미선언 태그 사용 | **381 / 591 (64%)** | **0** |
| `@require_auth`인데 Swagger `security` 누락 | **17** | **0** |
| `responses` 없음 | 144 (24%) | 131 (coding 13건 해소) |
| 태그 없음 · summary 없음 | 0 · 0 | 0 · 0 |

## 2. 🔴 `/api/version`이 98KB였다

`{"version", "changelog"}`를 항상 돌려줬고 changelog가 **91건 = 98,348 bytes**였다.
버전만 확인하려는 연동 호출·헬스체크에 매번 그 페이로드가 나갔다.

처치: **기본은 요약**, changelog는 `?changelog=true`일 때만.

```json
{"version": "2.125.29", "api_contract": "1", "commit": "…",
 "service": "weru.b-ai-server", "endpoints": 561,
 "docs": {"swagger_ui": "/docs", "openapi": "/openapi.json",
          "medical_catalog": "/api/medical/catalog"}}
```

⚠️ **소비자를 함께 고쳤다** — 관리 콘솔 두 곳(`static/js/main.js`·`app.py` 인라인)이
`d.changelog`를 렌더하므로 `?changelog=true`로 부르게 했다. 기본만 바꾸고 화면을 안 고쳤으면
**changelog 탭이 빈 화면**이 됐다. `client/his_client.version()`은 `version`만 읽어 무영향.

## 3. 신규 — 모든 응답이 자기 버전을 말한다

연동 시스템이 장애를 신고할 때 `/api/version`을 따로 부르면 그건 *지금* 버전이지
**문제의 응답을 낸 버전이 아니다**(그 사이 배포가 있었을 수 있다). 그래서 헤더로 싣는다:

| 헤더 | 뜻 |
|---|---|
| `X-API-Version` | 서비스 빌드 버전 — 배포마다 오름. 재현·신고용 |
| `X-API-Contract` | **API 계약 버전** — 하위 호환이 깨질 때만 오름. 연동 코드가 분기할 축 |
| `X-API-Commit` | HEAD 커밋 7자리(`.git` 없으면 생략) |

🔑 **두 축을 분리한 이유**: 한 숫자로 섞으면 연동자가 **매 배포마다 호환성을 의심**하게 된다.
엔드포인트 추가·선택 필드 추가는 계약을 깨지 않으므로 `X-API-Contract`는 그대로다.

설계 규율:
- **CORS 노출 선언 동반**(`Access-Control-Expose-Headers`) — 없으면 브라우저 XHR에서
  **조용히 안 보인다**.
- **subprocess 금지** — 커밋 해시는 `.git`을 직접 읽고 캐시한다. 요청 경로에서 프로세스를
  띄우면 gthread 풀을 잡아먹는다(레포 규율). `.git`이 없으면 헤더를 **아예 안 싣는다**(빈 값 금지).
- 헤더 부착 실패가 **응답을 막지 않는다**(try/except + return response).

## 4. Swagger 배치 — 미선언 태그 54개 → 0

Swagger UI는 미선언 태그를 **설명 없이 알파벳순으로 뒤에** 붙인다. 사용 87 / 선언 33이라
**381 오퍼레이션(64%)**이 그 상태였다. `swagger_template["tags"]`에 57 + 3(Discovery·Gateway·MCP)을
도메인별로 선언·정렬·설명했다:

```
Medical - Catalog(진입점) → 의료 데이터 → 의료 추론 → 의료 운영
→ 코어(Ollama·Queue·RAG·Search…) → 오케스트레이션 → 한국어·창작 → 코딩/HR/상담/회의
→ 게임 → 상호운용·저장·운영
```

라우트 docstring은 건드리지 않았다(태그 **이름**은 그대로). 배치만으로 목적이 달성된다.

## 5. 인증 문서 정합 — 17건

`@require_auth`인데 Swagger에 `security`가 없었다(coding.py 16 + `medical.py:/api/medical/batch`).
**문서가 코드보다 관대하면** 연동자가 키 없이 붙였다가 401을 맞는다. 전부 `security: [{Bearer: []}]` 선언.

## 6. `responses` 보강 — 코드에서 확인된 것만

가장 큰 결손 덩어리였던 `coding.py` 13개 라우트에 200/400/401을 추가했다.
**스키마를 지어내지 않았다** — `core.security.require_auth`가 401 `{"error":"unauthorized"}`,
핸들러가 400 `{"error":"code is required"}`를 내는 것을 코드에서 확인하고 그것만 적었다.
스트리밍 엔드포인트는 200 설명을 따로 뒀다.

## 7. 검증

신규 회귀 16건 `tests/test_api_surface_quality.py` — 버전 헤더 형상·두 축 분리·CORS 노출·
응답 차단 금지·subprocess 금지·캐시/fail-open·changelog 게이트·**관리 콘솔 소비자 정합**·
`@require_auth`↔`security` 전수 대조·태그 선언 커버리지·태그 설명 전수·Catalog 최상단·coding 401/400.

⚠️ **테스트가 세 번 결함을 잡았고 그중 둘은 내 테스트의 결함이었다**:
1. 미선언 태그 3개(Discovery·Gateway·MCP) — 진짜 누락. `openapi.json`의 `rule_filter`가
   `/api/` 접두사만 실어 **라이브 스펙에는 안 보이던 것**을 소스 대조가 잡았다.
2. `subprocess` 문자열 검사가 **자기 docstring의 설명 문구**에 걸렸다 → 코드 라인만 검사하도록 수정.
3. 태그 설명 검사 정규식이 한 줄 형식만 봐서 여러 줄 의료 태그를 "설명 없음"으로 오판 → 개행 허용.

## 8. 남긴 것

- `responses` 없음 131건 잔존 — 라우트마다 실제 반환을 읽어야 하므로 **일괄 생성하지 않는다**
  (지어낸 스키마는 문서-코드 드리프트를 만든다). 도메인별로 나눠 후속 처리.
- 🔴 **정정(같은 세션)**: 위 문장의 초안은 "`description` 없음 561건은 flasgger 동작이라
  결함이 아니다"였는데 **틀렸다**. flasgger는 docstring 첫 줄을 `summary`로, **`---` 앞 본문을
  `description`으로 싣는다** — 라이브 스펙에서 `/api/medical/catalog`·`/api/version`이 실제로
  description을 갖고 있는 것으로 확인했다(가진 것은 591 중 31건뿐). 즉 **560건은 진짜 문서 공백**이다.
  기전을 확인하지 않고 도구 탓으로 돌릴 뻔했다.
  → v2.125.30에서 **카탈로그가 가리키는 연동 표면 14건 중 13건**을 먼저 보강했다(나머지 1건은
  이미 본문 보유). 전체 560건 일괄 생성은 하지 않는다 — 지어낸 산문은 문서-코드 드리프트를 만든다.
- 태그 **이름** 통합(`Medical - Clinical`/`Clinical EN`/`Clinical QA` 등)은 하지 않았다 —
  기존 소비자의 태그 필터가 깨진다.
