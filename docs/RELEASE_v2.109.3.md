# Release v2.109.3 — 보안 보완 P2 (안전 항목)

**날짜**: 2026-06-23
**유형**: 보안 패치 (인터페이스 무변경)
**계획서**: `docs/01-plan/features/security-review-remediation.plan.md`

## 변경 (P2 안전 항목)

### M7 — 레이트리밋 키 실 클라이언트 IP (`app.py`)
`_rate_limit_key` 비-Bearer 경로를 `get_remote_address()`(프록시 뒤 127.0.0.1로 붕괴 → 전 익명 트래픽이
단일 버킷 공유=상호 락아웃·브루트포스 per-server) → `get_client_ip()`(CF-Connecting-IP/XFF) 기반으로 변경.
로그인·익명 한도가 **per-실클라이언트** 적용. (M6 전역 120/min도 이로써 per-client 실효화.)

### M8 — S3 key prefix 검증 (`core/s3_storage.py`, `routes/image.py`)
`key_allowed(key)`(구성 prefix 하위만 허용) 추가 + `/api/image/s3/url/<key>`·`/delete/<key>`에 적용(미허용 403).
인증 호출자가 버킷 임의 객체를 presign/삭제하던 갭 봉합. 내부 upload/list는 항상 prefix 하위 key라 무영향.
- **라이브 입증**: out-of-prefix key → 403.

## 검증
- 로컬 py_compile + key_allowed 단위 동작. GPU: import 드라이런→graceful HUP(NRestarts 0)→S3 403→
  **전체 회귀 647 passed/7 skipped**(베이스라인 동일).

## P2 잔여 (신중처리 — 라이브 연동/마이그레이션 영향)

> 아래는 **테스트 없이 배포 시 프로덕션 의료(HIS)/트윈 연동 파손 위험**이 있어 별도 신중 처리 대상:
- **M3 테넌트 키 해싱**: `medical_tenants.api_key` 평문→해시. 라이브 HIS X-Medical-Key 인증 마이그레이션(lazy) 필요.
- **M4 medical_bp fail-closed**: 클리니컬 라우트는 Bearer 아닌 X-Medical-Key 사용 → `_blueprint_auth` 단순 강제 시 HIS 차단. (로컬/Bearer/X-Medical-Key 중 무엇도 없을 때만 401) 설계 + OPTIONS 예외 + 공개 라우트 enum 필요.
- **M5 잡 IDOR**: avatar/cardiac/meeting/queue 잡에 owner 스코프(스키마 마이그레이션). 트윈 submit↔poll 키 일관성 확인 필요(uuid라 열거 난이=완화).
- **M6 per-엔드포인트 강화**: M7로 전역 per-client화 완료. 추가 강화는 튜닝(저우선).

## P3 (방어심층, 후속)
L1 str(e) 제네릭화·L2 키캐시 즉시폐기·L3 LIKE ESCAPE·L4 CSRF 빈바디·L5 HSTS/CSP·L6 프롬프트 캡.
