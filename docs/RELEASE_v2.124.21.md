# v2.124.21 — HR Law 보안 감사·조치 (인가 누락 3건 + 키 해싱)

2026-07-21 | 계기: 운영자 "h.law 부분 살아있나?" → 상태 점검 → 보안 검토 지시

## 요약

HR Law(취업규칙 SaaS, 23 엔드포인트)를 4축(인증·인가·키 취급·주입)으로 감사해
**실증된 인가 결함 3건**과 키 취급 약점 2건을 조치했습니다. 라이브 프로브로 취약점을
재현하고, 수정 후 다시 프로브해 차단을 확인했습니다(프로브가 만든 데이터는 전부 정리).

## 🔴 발견 1 — admin 가드 6곳이 애초에 동작한 적이 없음

`routes/hr_law.py`가 `is_admin_request`를 **정의도 임포트도 하지 않은 채** 6곳에서
호출하고 있었습니다. 즉 테넌트 목록·수정·삭제·키 재발급·통계가 전부
`NameError → 500`이었습니다. 가드가 작동한 게 아니라 **엔드포인트 자체가 불능**이었고,
그래서 아무도 눈치채지 못한 채로 남아 있었습니다.

```
NameError: name 'is_admin_request' is not defined     ← 라이브 로그
GET /api/hr-law/tenants → 500
```

fix: 타 Blueprint와 동일한 패턴으로 `init_hr_law(is_admin_request=...)` 주입 +
**미주입 시 fail-closed**(`False` 반환 + 경고 로그). 주입 실패가 조용한 허용으로
바뀌지 않게 기본값을 막아 둡니다.

## 🔴 발견 2 — 테넌트 자가 등록 (인가 누락, 실증)

`POST /api/hr-law/tenant/register`에 `@require_auth`만 있고 admin 가드가 없었습니다.
같은 파일의 다른 테넌트 관리 엔드포인트는 전부 admin 전용이라 **등록만 빠진 누락**입니다.

임시 API 키를 발급해 외부 요청(XFF 부여)으로 프로브한 결과:

```
POST /tenant/register  {"name":"SEC-PROBE","max_queries_day":999999}
→ 200  tenant_id=dff59d06-ea7  max_queries_day=999999  api_key=CHANGE_ME
```

**아무 용도의 API 키(OpenPencil·HIS·트윈용 등) 하나만 있으면** 테넌트를 자가 발급하고
쿼터를 임의 지정할 수 있었습니다(`max_queries_day`가 요청 값 그대로 — plan 한도
100/500/2000이 무의미). 영향은 쿼터 우회를 통한 LLM 자원 남용과 테넌트 테이블 오염이며,
PHI 유출 경로는 아닙니다.

fix: `is_admin_request()` 가드 + `max_queries_day`를 plan 상한으로 클램프.
**수정 후 재프로브 → 403 `admin only`.** 프로브 테넌트는 삭제, 임시 키는 폐기했습니다.

## 🔴 발견 3 — `tenant/usage` fail-open (인증 실패가 더 큰 권한)

`GET /api/hr-law/tenant/usage`는 테넌트 해석이 실패하면 **무조건** "관리자: 전체 사용량"
분기로 떨어졌습니다. 즉 **잘못된 키를 보낸 호출자가 오히려 전 테넌트의 사용량**
(테넌트명·질의 수·한도)을 받았습니다.

```
X-Tenant-Key: hr-wrongkey → 200 + 전 테넌트 usage      (수정 전)
X-Tenant-Key: hr-wrongkey (외부) → 401                  (수정 후)
```

fix: 집계 분기에 `is_admin_request()` 요구, 아니면 401. 사규 엔드포인트 7곳은 전수
확인 결과 이미 401로 정상이었습니다.

## 🟡 M-1 — 테넌트 키 평문 저장·열람

`tenants.api_key`가 평문 저장·동등 비교 조회였고, `/tenants`가 `SELECT t.*`라 **관리자가
전 테넌트 키를 평문 열람**할 수 있었습니다(DB 유출 시 전 키 노출). 기존 보안 계획서의
**M3(의료 X-Medical-Key 해싱)와 같은 클래스**입니다.

fix: **sha256 해시 저장**(본 서버 API 키와 동일 방식 — 키가 `secrets.token_hex(16)`
=128bit 고엔트로피라 비밀번호용 KDF 불필요) + **지연 마이그레이션**(레거시 평문 키는
첫 사용 시 해시로 전환하고 평문 제거) + 응답에서 `api_key`·`key_hash` 제거(발급 시
1회만 노출, 이후 재발급만).

🐞 **라이브가 잡은 것**: 첫 구현은 평문 칼럼을 `NULL`로 비웠는데 레거시 스키마에
`NOT NULL` 제약이 있어 `IntegrityError → 500`이었습니다(지연 마이그레이션·신규 등록
양쪽 실패). 빈 문자열로 비우고 레거시 조회에 `api_key<>''`를 더해 방어했습니다.

**라이브 검증**: 레거시 키 호출 → 200 · 평문 제거·해시 생성 확인 · 같은 키 재호출 →
200(해시 경로). 나머지 4개 테넌트는 각자 첫 사용 시 자동 전환됩니다.

## 🟡 M-2 — 쿼리스트링 키 수용 제거

`_resolve_tenant`가 `?tenant_key=`도 받고 있었습니다. URL에 실린 키는 액세스 로그·
리퍼러·브라우저 히스토리에 잔류합니다 → **`X-Tenant-Key` 헤더 전용**으로 좁혔습니다.

## ✅ 문제없음으로 확인된 것

- **IDOR 없음** — 사규 삭제가 `WHERE id=? AND tenant_id=?`로 소유권 검사.
- **주입면 없음** — LIKE는 이미 `_escape_like`+`ESCAPE` 적용(의료법보다 앞서 있었음),
  f-string SQL은 상수 조합에 값은 전부 바인딩.
- 키 생성은 `secrets.token_hex(16)`(128비트)로 예측 불가.
- Blueprint 인증은 fail-closed(외부 무키 → 401).

## 회귀

`tests/test_hr_law_security.py` **7** — admin 판정 fail-closed/주입 동작, 키 해시
비가역성, 쿼리스트링 키 거부(DB 조회 자체가 없어야 함), 헤더 없음 처리,
usage fail-open 차단 + admin 허용 경로 유지.

## 참고 — 데이터 정합성 (보안 아님)

테넌트 삭제가 `DELETE FROM hr_queries WHERE user LIKE '%tenant_id%'`로 **부분 문자열
매칭 삭제**를 합니다. tenant_id가 12자 UUID라 충돌 가능성은 낮지만 정확 매칭이 맞습니다.
별도 백로그로 둡니다.

## 운영 메모

HR Law는 **기능은 정상이나 사실상 휴면**입니다 — 마지막 질의가 2026-05-06,
`company_rules` 0행, 법령 데이터 84건도 5월 기준입니다. 재가동 시 **근로기준법 개정분
반영이 선행**돼야 하며, 의료법 쪽 자동 수집·버전 dedup 파이프라인을 같은 방식으로
붙일 수 있습니다.
