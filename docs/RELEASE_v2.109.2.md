# Release v2.109.2 — 보안 보완 P0/P1

**날짜**: 2026-06-23
**유형**: 보안 패치 (인터페이스 무변경, 신규 API 0)
**계획서**: `docs/01-plan/features/security-review-remediation.plan.md`

## 헤드라인

전면 보안 감사(4축: 인증/인가·주입/SSRF·시크릿/PHI·의존성/DoS) 결과 **P0(노출 제거)+P1(인증/SSRF)** 보완.

## P0 — 노출 제거 (이미 발생한 노출)

- **C1/C2**: `medical.db`(상담 PHI)·`users.db`(비번 해시)·moderation/training.db가 git 원격에 커밋돼 있던 것을 제거. `.gitignore` 강화(민감/런타임 DB 명시 + 시드 DB clinic_codes/hr_law/game 유지) + `git rm --cached` + **git-filter-repo로 전 이력 purge** + force-push.
- 검증: 원격·iMac·GPU 로컬 전 ref **잔존 blob 0**, 라이브 데이터·서비스 무손상. 저장소 PRIVATE 확인(노출 범위 한정), GPU 라이브 PHI=0(데모 시드만 노출됐던 것).
- 🔴 **정정(2026-08-25)**: "잔존 blob 0"은 **medical.db·users.db에만 참**이다(`git rev-list HEAD` 도달 0 재확인). **moderation.db·training.db는 filter-repo purge에서 빠졌다** — 이 커밋(`0bcdb5c`)이 `git rm --cached`로 추적만 해제했고 이력 재작성은 안 됐다. blob은 `9e5f3cd`(2026-05-06)에서 지금도 도달 가능(moderation 45KB·training 20KB). 내용 실사=모더레이션 **테스트 샘플**(욕설 문구·해시 ID)과 훈련 런 메타 1행 — **PHI·자격증명 없음**·위험 낮음. 이력 재작성(force-push·전 클론 재동기 필요)은 별건으로 운영자와 조율.
- 비번 로테이션은 운영자 UI(`/api/admin/change-password`)로 수행 → 레거시 static-salt 해시가 per-user salt pbkdf2로 교체.

## P1 — 인증 fail-closed + SSRF 차단

### H1 — `is_local_request()` fail-closed
`CF-Connecting-IP` 단일 의존 제거 → **X-Forwarded-For / X-Real-IP 존재 시 외부로 간주**(`app.py`). nginx 직프록시·SSH 터널 등 CF 헤더 없는 경로로 8585 도달 시 로컬 오인=전면 인증우회를 차단. `_is_monitor_authorized`와 로직 통일. 내부 발신 XFF 부재 확인(회귀 없음).
- **라이브 입증**: localhost 무헤더→200(로컬 바이패스 유지)·localhost+XFF→**401(이전 200, 신규 차단)**·+X-Real-IP→401·+CF→401·외부 무인증→401(불변).

### M1 — meeting 다운로드 SSRF (`core/meeting_pipeline.py`)
`allow_redirects=False`+3xx 거부(302→사설/메타데이터 IP 우회 차단) + `DOWNLOAD_MAX_MB=4096` 용량 캡(악성 무한 스트림 디스크 필 방지). avatar_mesh._download 하드닝 패턴 준용.

### M2 — image detect-by-URL SSRF (`routes/image.py`)
`POST /api/detect/image` URL 페치에 `allow_redirects=False`+3xx 거부. validate_webhook_url(IP리터럴만 차단)을 리다이렉트 차단으로 보강.

## 검증
- 로컬 py_compile OK. GPU: import 드라이런→graceful HUP(NRestarts 0)→H1 인증 매트릭스 4종 통과→**전체 회귀 647 passed/7 skipped**(베이스라인 동일, 부수영향 0)·외부 무인증 401.

## 잔여 (계획서 P2/P3, 후속)
- **P2**: M3 테넌트키 해싱·M4 medical_bp fail-closed·M5 잡 소유자 스코프·M6/M7 레이트리밋·M8 S3 prefix.
- **P3**: L1~L6 방어심층(에러바디·키캐시 폐기·LIKE ESCAPE·CSRF·CSP/HSTS·프롬프트 캡).
