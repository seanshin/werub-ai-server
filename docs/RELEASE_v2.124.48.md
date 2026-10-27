# Release v2.124.48 — 의료 테넌트 API 키 해싱 (보안 P2 M3 해소)

날짜: 2026-07-29 | 종류: security | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

6/23 감사 M3 — `medical_tenants.api_key`가 **평문 저장 + 평문 비교**였다. HIS가 라이브로
`X-Medical-Key`를 쓰고 있어 "협의 대기"로 6주 묶여 있었으나, **첫 인증 시 자동 전환**
(지연 마이그레이션)이면 **호출자 작업이 0이고 체감도 없다**는 판단으로 협의 없이 처리했다.
HR Law 테넌트 키(v2.124.21)·본 서버 API 키(v2.124.46)와 같은 규율로 통일된다.

## 구현

- `hash_medical_key()`(sha256) + `api_key_hash`·`api_key_prefix` 컬럼(멱등 ALTER + 인덱스).
- `_resolve_medical_tenant`: **해시 조회 → 미스 시 레거시 평문 조회 + 즉시 전환**.
  전환 실패는 경고만 남기고 인증을 막지 않는다(다음 요청에 재시도).
- 신규 발급·키 회전: **해시·prefix만 저장**(평문 미저장). 목록은 저장된 prefix 사용.
- ⚠️ `api_key`는 레거시 `NOT NULL UNIQUE`라 NULL·빈 문자열을 못 넣는다(빈 문자열은 테넌트가
  둘 이상이면 UNIQUE 충돌 — HR Law에서 겪은 클래스) → **테넌트별 고유 플레이스홀더**
  `hashed:<tenant_id>`. 실제 키는 `med-` 접두라 절대 일치하지 않는다.

## 검증 (라이브)

| 단계 | 결과 |
|------|------|
| 레거시 평문 키로 1차 호출 | **200** + 해당 행 전환(key=`hashed:<id>`·hash·prefix 기록) |
| 같은 키로 2차 호출(해시 경로) | **200** |
| 남은 2행 직접 전환 후 **HIS 키로 인증** | **200** (단절 없음 확인) |
| 평문 잔여 | **0행** |

회귀 `tests/test_medical_tenant_key_hash.py` **7**(해시 안정성·전환 후 평문 제거·
플레이스홀더 테넌트별 유일성=UNIQUE 충돌 방지·해시 조회 왕복).

## 잔여

- **M4 medical_bp fail-closed**: 공개 라우트 열거를 틀리면 HIS가 막히므로 별도 진행
  (전수 열거 + 무키/Bearer/X-Medical-Key 3종 인증 매트릭스 라이브 검증 전제).
- **M5 잡 IDOR**: owner 스코프 마이그레이션 + 트윈 submit↔poll 키 정합 확인 필요 = **협의 대기**.
