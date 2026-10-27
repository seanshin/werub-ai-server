# v2.125.61 — 탭별 권한 서버 강제 (웹 세션 비관리 역할)

기준 v2.125.60 · 기획 `docs/01-plan/features/admin-menu-settings-cleanup.plan.md` D6 잔여

## 결함

- v2.125.56은 **관리자급** API만 역할 admin으로 좁혔다. 탭 고유 API(queue·chat·generate·knowledge·image·activity·logs·training)는
  여전히 로그인만 하면 누구나 쓸 수 있었다 — 역할표(`ROLE_DEFAULTS`)는 화면 숨김에만 쓰였다.
- 대시보드에 붙은 서버 제어(`/api/service/*` Ollama 시작·정지, `/api/models/unload|warm`)도 비관리 역할이 쓸 수 있었다.

## 설계 — UI 호출 지도(95개 method+path) 기준

- **공통 읽기는 막지 않는다**: 헤더·대시보드는 탭과 무관하게 10초~5분마다 `/api/status`·`/api/queue/status`·카드 상태 GET을 부른다.
  막으면 허용된 탭에서도 화면이 깨진다. 규칙에 없는 경로는 통과(기존 동작).
- 여러 탭이 공유하는 경로는 허용 탭 중 하나라도 있으면 통과(`/api/queue/job/*` = queue·image, `/api/image/view/*` = image·queue).
- `ADMIN_ONLY`: `/api/service/*`·`/api/models/unload`·`/api/models/warm` — 비관리 역할은 항상 거부.
- 대상: **웹 세션 + 비관리 역할**만. 로컬(bypass)·Bearer API 키·admin은 영향 없음. 권한은 로그인 때 세션에 실린 값(변경은 재로그인 후 반영).
- 거부는 `403 {"error":"forbidden","reason":"tab:queue"}` + `[tab-perm]` 로그. 규칙표 `core/tab_permissions.py`(먼저 맞는 규칙이 이긴다).

## 검증

- 신규 `tests/api/test_tab_permissions.py` 22건(규칙표 18 · 역할 판정 · operator 차단/허용·공통 읽기 · admin·로컬·Bearer 무영향). 전체 **2367 passed**.
- 라이브 영향: 비관리 계정은 operator 1개(마지막 로그인 2026-04-21) — 현재 운영 사용자 영향 없음.
