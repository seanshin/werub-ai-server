# v2.125.46 — 무인증 공개 `GET /api/tags` (PACS 가용성 감시·HIS 모델 레지스트리)

기준 v2.125.45 · 출처 = 연동 검토 회신

## 0. 한 줄

PACS 가용성 감시가 30분마다 인증 없이 부르던 `GET /api/tags`가 **405**라 PACS가 AI 서버를
**상시 down**으로 판정하던 것을, 공개 경로 신설로 풀었다(운영자 결정: 이 경로만 무인증 공개).

## 1. 라이브 실측 (추측 아님)

- journald 7일: `GET /api/tags` **335건 전부 405** · `python-httpx/0.28.1` · **정확히 30분 주기**
  (06:22:45 → 06:52:45 → …) = PACS `ai_health_monitor`(`PROBE_PATH='/api/tags'`·200 외 down).
- PACS 컨테이너 3종 `AI_SERVER_URL=https://example.org/ollama` → Cloudflare→Caddy(XFF 부착)
  경유 = 우리 쪽에서 **외부 요청**. 그래서 라우트만 만들어서는 401이 된다.
- 외부 무인증 실측: `/api/health`·`/api/version`·`/api/status`·`/api/models`·`/v1/models` **전부 401**,
  `/api/tags` 405 ⇒ 무인증으로 200을 줄 경로가 **하나도 없었다**.
- 405인 이유: 라우트가 없어 `app.py`의 OPTIONS 전용 catch-all(`/<path:path>`)에 걸림(404 아님).

## 2. 처치

`@app.route("/api/tags")`(app 직속·blueprint 인증 훅 밖 — `/openapi.json`과 같은 층):

| 항목 | 값 |
|---|---|
| 인증 | 없음 (이 경로만) |
| 형식 | Ollama 표준 `{"models":[…]}` |
| 공개 필드 | `name`·`model`·`digest`·`details.family` **뿐** (크기·양자화·파라미터 수·수정 시각 제외) |
| 엔진 다운 | **503** `{"models":[],"error":"engine_unavailable"}` + warning 로그 — 200+빈 목록이면 감시가 속는다(반복 클래스 #1) |
| 보호 | per-클라이언트(CF-Connecting-IP 기준) **30/분** + **10s 캐시**(외부 반복이 Ollama로 새지 않게) |

Swagger `Discovery` 태그에 등재. 다른 표면의 인증은 불변.

## 3. 검증

신규 `tests/test_public_tags.py` 8건: 엣지 모양(루프백+XFF)·무인증 → 200 Ollama 형식 ·
**공개 필드 화이트리스트 고정** · 엔진 다운 3변형(연결 거부·HTTP 500·타임아웃) → 503+로그 ·
10s 캐시(3회 호출에 엔진 1회) · **음성 통제**(`/api/models`·`/api/status`·`/api/keys`는 여전히 401) ·
리밋(31번째 429). 전체 회귀 **2231 passed**·9 skipped·1 xpassed(GPU 임시 worktree).
⚠️ 신규 테스트를 구 코드에 걸면 픽스처가 캐시 변수 부재로 **에러**가 난다(깨끗한 실패 시연이 아님) —
구 동작(405)은 라이브 로그가 증명한다.

## 4. 연동자 안내

- PACS: 코드 변경 불요 — 다음 30분 주기부터 200(엔진 정상 시)/503(엔진 다운 시).
- HIS 모델 레지스트리: 같은 경로로 모델 이름·digest를 받는다. 공개 필드 외 정보가 필요하면
  인증 경로(`/api/models`·Bearer)를 쓴다.
