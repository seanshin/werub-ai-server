# v2.125.56 — 역할 권한 서버 강제 + 메뉴 숨김 복구 (Phase 0c · D6)

기준 v2.125.55 · 기획 `docs/01-plan/features/admin-menu-settings-cleanup.plan.md` §2.4 D6

## 1. 결함

- 관리 API 판정 `is_admin_request()`가 **로그인 여부만** 봤다. 역할(`web_role`)은 로그인 때 세션에 실리지만 서버가 확인하지 않았다.
  역할표 `ROLE_DEFAULTS`와 `_check_user_permission`은 정의만 있고 **호출 0**.
  ⇒ `operator`·`viewer` 계정이 `/api/config`(시크릿·인증 설정)·`/api/keys`(영구 키 발급)·`/api/ollama/*`(재시작)·pull/delete·
  상주·heavy·이미지 설정·HR Law 관리·`/docs`를 쓸 수 있었다. 라이브에 operator 1계정(`user`·활성·마지막 로그인 2026-04-21).
- 화면의 역할별 탭 숨김(`app.py` index)은 인라인 폴백 HTML만 고치고 바로 뒤 `render_template` 결과로 **덮어써** 한 번도 적용되지 않았다.

## 2. 처치

- `is_admin_request()` = 로컬 **또는** (웹 로그인 **그리고** `web_role == "admin"`). 세션에 역할이 없으면 거부(fail-closed).
  로컬(서버 자신·스크립트)은 기존대로. 관리 판정을 쓰는 29곳 전부 관리자급 변경·시크릿이라 operator 허용 화면(dashboard·training) 불변 —
  대시보드 상주 조회(GET)는 판정 밖.
- 메뉴: `index()`가 `allowed_tabs`를 템플릿에 넘기고, 템플릿이 허용 탭만 그린다(항목이 없는 그룹은 통째로 생략).
  `switchTab`·hash는 **메뉴에 있는 탭만** 연다(권한 밖 탭을 `#settings`로 여는 것 차단).
- 역할 변경은 **재로그인 후** 반영된다(세션 기준) — 강등 즉시 반영이 필요하면 후속.

## 3. 잔여(범위 밖)

- 관리자급이 아닌 탭 API(queue·chat·models 조회 등)의 **탭별 권한**은 여전히 서버가 강제하지 않는다(화면 숨김만). 필요 시 후속.

## 4. 검증

- 신규 6건(비관리 역할 3종 × 관리 API 403 · admin 200 · 로컬 유지 · operator 메뉴=dashboard+training · 권한 밖 탭 차단) —
  **구 코드 5건 실패** · 신 코드 통과. 전체 **2336 passed**.
