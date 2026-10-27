# v2.125.17 — 보안: 라이브 재현된 유출 3건 봉합

날짜: 2026-08-25 | 발단: 보안·성능 병렬 검토(에이전트 2) → **내가 직접 재현한 것만** 처치

## 0. 규율 — 남의 결론도 통제군을 거친다

검토 에이전트가 🔴 9건을 보고했으나, v2.125.5에서 **A/B 리뷰 에이전트의 결론을 근거 확인 없이
세 문서에 승격시킨 사고**가 있었다. 그래서 이번엔 상위 지적을 **직접 재현**했고, 재현된 것만
이 릴리즈에 넣는다. 나머지는 미검증으로 분류해 후속 과제로 남긴다(§4).

## 1. 🔴 경로 순회 2건 — 재현됨

```
처치 전(2026-08-25 라이브):
  GET /admin-console/../../config.json         → 200 · config.json 1,783B   ← 무인증
  GET /admin-console/%2e%2e/%2e%2e/config.json → 200                        ← 인코딩 변종
  GET /api/image/view/../.secret_key?subfolder=sana → 200 · 32B (Flask SECRET_KEY)
```

- **`app.py` `/admin-console/<path:subpath>`**: 인증 데코레이터가 **없고** `@limiter.exempt`.
  `config.json`에는 S3 시크릿·워커 토큰·GPU 키가 들어 있다. `%2e%2e` 변종은 nginx 정규화도 통과한다.
- **`routes/image.py` `/api/image/view/<path:filename>`**: `subfolder=sana` 분기가 `open(...,"rb")`로
  임의 파일을 읽는다. `.secret_key` 32바이트 → **세션 위조**(`web_logged_in=True`) →
  `_is_admin_request()` 통과 = **관리자 승격**.

🔑 **새 결함이 아니라 미적용 지점이다.** 형제 4곳(`image.py:753`·`training.py:1902/1920/1938`)은
이미 `os.path.basename`을 쓰고 있었다 — CLAUDE.md 결함 클래스 7("한 곳에서 고친 결함은 같은 클래스
전 경로를 쓸어야 한다")의 반례가 레포 안에 있었던 것이다.

**처치**: image view는 `basename` + subfolder 순회 거부(400) · admin-console은 조립 후
`realpath` 봉쇄(`basename`은 `_next/static` 하위 구조를 깨므로 봉쇄 방식을 택했다).

## 2. 🔴 오케스트레이터 `ai:` allowlist — 위조 localhost 권한

`routes/orchestrator.py`의 `_execute_action`은
`test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"})` + `full_dispatch_request()`로
**localhost를 위조**해 디스패치하는데, 액션 검증은 **접두사(`ai:`)만** 봤다. localhost는 인증
bypass이자 `_is_admin_request()` 통과 조건이므로 워크플로 한 줄이 관리자 API를 실행한다:

| 워크플로 스텝 | 결과 |
|---|---|
| `ai:/api/keys` | 영구 API 키 발급 → 지속 접근 |
| `ai:/api/config` (GET) | 전 시크릿 열람 |
| `ai:/api/config` (POST `auth_enabled:false`) | **서버 전역 인증 킬스위치** |

유효 API 키 하나면 도달 가능했고, **이 한 곳이 코드베이스의 모든 `_is_admin_request()` 게이트를
무효화**할 수 있었다(그래서 다른 게이트를 아무리 고쳐도 이걸 안 닫으면 의미가 없다).
※ 실제 키 발급은 부작용이 있어 재현하지 않고 구조로 확인했다.

**처치**: `_AI_ACTION_ALLOWLIST`(실사용 36개 전수) + `_ai_endpoint_allowed()`를
**등록·디스패치 두 지점**에 배선. 기존 DB 행은 등록 검증을 안 거치므로 디스패치가 최종 방어선이다.
- **deny 목록이 아니라 allowlist인 이유**: 새 관리자 엔드포인트가 생길 때 deny는 **조용히 뚫린다**.
  이 레포는 이미 같은 결론을 냈다 — `core/model_router`의 "미등록 task는 즉시 실패".
- 도입 시점 DB 등록 커스텀 워크플로 **0건** 확인 → 파손 없음.
- 정확 일치(접두 일치·경로 순회·중복 슬래시 우회 차단).

## 3. 회귀 33

- `tests/test_path_traversal_guard.py`(5): 두 라우트 차단 + 정상 자산 서빙 유지 +
  **패턴 검사**(가드 없는 `<path:>` 조립이 레포에 **0곳**임을 고정).
- `tests/test_orchestrator_ai_allowlist.py`(28): 관리자 8종 차단 · 실사용 10종 통과 ·
  우회 5종 차단 · 두 지점 배선 · **레포의 모든 `ai:` 액션이 allowlist 안에 있음**
  (새 워크플로가 엔드포인트를 추가하면 여기서 빨개져 "위조 localhost 권한으로 실행돼도 되는가"를 묻게 된다).

전체 **1823 passed**.

## 4. ⚠️ 미검증으로 남긴 것 (에이전트 보고 · 내가 재현하지 않음)

아래는 **아직 사실로 확정하지 않았다**. 다음 세션에서 하나씩 재현·판정한다.

| 항목 | 요지 |
|---|---|
| medical 테넌트 플레이스홀더 | `api_key` 컬럼의 `hashed:<tenant_id>`가 유도 가능해 PHI 경로 우회 가능성 · **키 로테이션으로 안 닫힌다**는 주장 |
| RAG `collection` 자유 지정 | 테넌트 격리·삭제 보호 우회 가능성 |
| `/api/config` 시크릿 전량 응답 | 형제 코드(`imac_link`·`gateway`)는 이미 가리고 있다 |
| `/api/delete`·`/api/pull` 무-admin | fleet 라이브 배선과 충돌 |
| iMac 워커 임의 파일 쓰기 | `filename` 무검증 |
| 커밋된 `sk-` 형식 문자열 | 로테이션+히스토리 purge 필요 여부 |
| CORS가 loopback에 오리진 반사 | 터널 사용자 브라우저 경유 위험 |

## 5. 관찰 — 이 레포의 강점이 그대로 약점이다

재현된 3건 모두 **레포가 이미 옳게 푼 패턴의 미적용 지점**이었다(`basename` 4곳 적용/2곳 누락,
allowlist 규율은 `model_router`에 있는데 orchestrator엔 없음). 개별 처치보다 **패턴별 인벤토리를
떠서 잔여를 0으로 만드는 방식**이 맞고, D1 router 이관이 이미 그 효과를 입증했다.
그래서 이번 회귀 2건은 개별 라우트가 아니라 **패턴 자체**를 고정한다.
