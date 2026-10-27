# v2.124.26 — 보안 L4: 세션 요청 Origin 검증 (CSRF 방어심층)

2026-07-21 | 계기: 운영자 "보안 L4 진행" | 계획: `security-review-remediation.plan.md` §5 L4

## 측정 선행 — L4 실질 위험은 낮았다

L4(CSRF 빈 바디 통과)를 측정한 결과, **다층 방어가 이미 있었습니다**:

- `SESSION_COOKIE_SAMESITE=Lax` → cross-site POST에 **세션 쿠키 미전송**(세션 CSRF 주 방어).
- CORS 화이트리스트(`allowed_origins`) → cross-site JSON은 preflight에서 차단.
- Content-Type 검증(JSON/multipart만) → cross-site 폼 submit 차단.

즉 세션 기반 CSRF는 SameSite=Lax가 이미 막습니다(계획서가 L4를 LOW로 본 이유).
"토큰 강제"는 프론트 전면 수정이 필요한데 이득이 낮아 채택하지 않았습니다.

## 조치 — Origin 검증 이중화 (프론트·API 클라 영향 0)

세션(`web_logged_in`) mutating 요청에만 **Origin/Referer 화이트리스트 검증**을 추가해,
구형 브라우저·SameSite 미적용 창을 대비한 이중 방어를 넣었습니다.

- `_csrf_origin_ok()`: Origin(없으면 Referer)이 `allowed_origins`에 있으면 통과, 아니면
  403. **Origin 부재는 통과**(same-origin 일부·구형 — SameSite가 주 방어). localhost bypass.
- **세션 분기에만 적용** — API 클라(Bearer/X-Medical-Key)는 이 분기에 도달하지 않아
  **영향 0**(라이브 확인: Bearer→401 인증실패이지 CSRF 아님, X-Medical-Key→200).
- **브라우저가 Origin을 자동 전송**하므로 프론트(main.js) 수정 불요.

## 검증

- 회귀 `tests/test_csrf_origin.py` 5 — Origin 허용/cross-origin 거부/부재 통과/localhost
  bypass/Referer 폴백.
- 라이브: health 200 · Bearer API 401(정상) · X-Medical-Key 200(세션 분기 미도달·무영향).

## 보안 L 시리즈 현황

- L1(에러바디)·L2(키캐시)·L3(LIKE)·L5(헤더)·L6(프롬프트 캡)·**L4(CSRF Origin)** = 완료.
- 잔여: **P2 신중**(M3 테넌트키 해싱·M4 medical_bp fail-closed·M5 잡 IDOR) = HIS·트윈
  협의 필요. HR Law 테넌트키는 v2.124.21에서 이미 해싱 처리(M3의 HR Law 부분 선행 완료).
