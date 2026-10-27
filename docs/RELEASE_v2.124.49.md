# Release v2.124.49 — 의료 Blueprint fail-closed (보안 P2 M4 해소)

날짜: 2026-07-29 | 종류: security | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

6/23 감사 M4 — `_medical_auth` 가드가 **`/api/medical/tenant*`의 POST/PUT/DELETE만** 인증을
강제했다. 나머지 임상 라우트(summarize·analyze·knowledge·reference 등)는 **무인증으로 외부에
열려 있었다**. 이 계열은 Bearer가 아니라 **X-Medical-Key**(테넌트 키)로 인증하므로
`_blueprint_auth()`를 단순 강제하면 **HIS가 통째로 차단**된다 — 그래서 6주간 보류돼 있었다.

## 수정

**"로컬·Bearer/세션·X-Medical-Key 중 아무것도 없을 때만 401"** 로 판정한다.

- `X-Medical-Key`(또는 `?medical_key=`)가 있으면 통과 → **테넌트 검증은 라우트가 수행**
  (기존 `_require_tenant` 경로 불변).
- 그 외에는 `_blueprint_auth()`(로컬 bypass·웹 세션·Bearer)로 판정.
- **OPTIONS 면제**(CORS preflight).
- 차단 시 **경고 로그**(메서드·경로·IP·UA) — 정상 호출자가 걸렸는지 즉시 식별(무성 실패 금지).
- **킬스위치 `MEDICAL_AUTH_ENFORCE=0`** — 재배포 없이 종전 동작으로 즉시 복귀.

## 라이브 인증 매트릭스 (example.org 경유)

| 호출 형태 | 결과 |
|-----------|------|
| 외부 무키 `/api/medical/models` | **401** (봉합) |
| 외부 Bearer | 200 |
| 외부 **X-Medical-Key** | 200 (HIS 형태 유지) |
| 로컬 무키(local bypass) | 200 |
| 외부 OPTIONS(preflight) | 200 |
| 외부 X-Medical-Key `summarize` | **200** (임상 실사용 경로 정상) |
| 외부 무키 `summarize` | **401** |
| 외부 무키 `tenants`(admin) | 401 |

배포 후 차단 로그 3건은 **전부 이번 검증 요청**(curl/8.5.0)이며, 정상 호출자 차단은 없었다.

## 범위 밖(확인 결과)

- 트윈이 쓰는 `/api/models/routing`은 **ollama BP**라 무관.
- `/api/medical/triage/*`는 **별도 triage BP**라 이번 가드에 포함되지 않는다 →
  같은 클래스인지 별도 판단 필요(호출자 확인 선행).

## 검증

회귀 `tests/test_medical_auth_failclosed.py` **6**(가드 형태를 소스 계약으로 고정:
tenant 경로 한정 금지·X-Medical-Key 수용·OPTIONS 면제·킬스위치·차단 로그).

## 잔여 P2

**M5 잡 IDOR**만 남는다 — owner 스코프 스키마 마이그레이션 + **트윈 submit↔poll 키 정합**
확인이 필요해 협의 대기(uuid라 열거 난이도가 높아 위험 완화 상태).
