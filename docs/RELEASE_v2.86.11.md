# Release v2.86.11 — 인증 보안 하드닝 (비밀번호 per-user salt 시리즈 완결 + 문서/API명세 동기화)

> 2026-06-09 | 이전: v2.86.7 | 보안 (v2.86.8 → v2.86.9 → v2.86.10 → v2.86.11 통합 노트)

비밀번호 해싱의 **static salt** 약점을 per-user 랜덤 salt로 전환하고, code-analyzer 독립
재검증으로 발견한 동시성·열거·정책 이슈를 한 사이클 안에서 모두 종결했다. KDF(pbkdf2-hmac-sha256
200,000회)는 기존과 동일하며, **기존 비밀번호는 무중단으로 유지**(강제 리셋·잠금 0)된다.

## 배경
이전에는 `app.py`/`routes/training.py` 5곳이 각각
`hashlib.pbkdf2_hmac("sha256", pw, b"werub-ollama-salt", 200000)` 로 **고정 salt를 하드코딩**했다.
KDF는 강하지만 static salt는 ①동일 비번→동일 해시 ②단일 known-salt 사전계산(rainbow) 약점이 있다.

## 변경 요약 (3개 패치)

### v2.86.8 — per-user salt 전환 + 무중단 마이그레이션
- **`core/password.py` 신규**: 비밀번호 해싱 단일 진실 공급원
  - `hash_password(pw)` → per-user 랜덤 16B salt, 자기기술 포맷 `pbkdf2_sha256$200000$<salt_hex>$<hash_hex>`
  - `verify_password(pw, stored)` → 신규 포맷은 per-user 검증, 레거시 bare 64-hex는 static salt 폴백 검증 (둘 다 `hmac.compare_digest` 상수시간)
  - `needs_rehash(stored)` → 레거시/iteration 미달이면 True
- **static salt 하드코딩 5곳 제거** → core 위임: `_hash_password`, `_verify_web_login`, 비번변경(app.py) + `admin_users_create`, `admin_users_update`(training.py)
- **무중단 lazy 마이그레이션**: 레거시 해시는 verify 자동 폴백, 로그인 성공 시 `needs_rehash`면 새 salt로 재해싱 UPDATE. 라이브 사용자는 다음 로그인 1회에 자동 전환.

### v2.86.9 — 동시 로그인 false-negative 제거 (M2/M3)
- `_verify_web_login`에서 비번 검증 성공 판정과 `last_login`/lazy 재해싱 UPDATE가 **같은 `except: return False` 블록**이라, 동시 로그인 시 SQLite BUSY로 UPDATE가 실패하면 **비번이 맞아도 인증 실패**로 오염되던 문제.
- → verify 성공과 post-auth UPDATE를 **분리**. UPDATE 실패는 warning 로그만 남기고 인증 True 유지(비번 불변·데이터 손상 0).

### v2.86.10 — 잔여 하드닝 4건 (L1/L2/L3/M1)
| ID | 내용 |
|----|------|
| **L2** | user 미존재/비활성 시 `dummy_verify`로 동일 비용 KDF 1회 소비 → 응답시간 차 기반 **username 열거 방지** |
| **L1** | 비번변경 시 config `admin_password_hash` 기록 제거 (write-only 잔재 — 인증은 users.db만 read, grep 전수 확인) |
| **L3** | 비번 최소 길이 **8자로 통일** (create/update/change-password 전부) |
| **M1** | `pbkdf2 encode("utf-8")` 명시. lone surrogate 등 인코딩 불가 입력은 verify에서 무예외 False |

### v2.86.11 — 문서/API명세 동기화 + L3 마무리
- **L3 완성**: `admin_users_update`(PUT)의 비밀번호 변경 경로에 길이 검증이 **누락**돼 있던 것을 8자 검증 추가(create/change-password와 완전 통일).
- **API 명세(flasgger docstring) 갱신**: `admin_users_create`/`admin_users_update` 스키마에 `password.minLength=8`, `role` enum, `required` 명시 → `/openapi.json` 자동 반영.
- **문서 동기화**: CLAUDE.md/README 버전 헤더 + 인증 보안 항목, 본 RELEASE 노트.

## API 명세 변경 (OpenAPI 자동 반영)
- `POST /api/admin/users` (사용자 생성): `password` **minLength 8** + `required:[username,password]` + `role` enum 명시
- `PUT /api/admin/users/{uid}` (사용자 수정): `password` 변경 시 **minLength 8** + 스키마 속성 명시
- `POST /api/admin/change-password`: 기존대로 minLength 8 (변경 없음)
- 인증 내부 동작(salt) 변경은 **API 표면 무변경** — 기존 클라이언트 영향 없음

## 검증
- `core/password.py` 회귀 `tests/test_password.py` **8건**: 신규 라운드트립·per-user salt·레거시 호환·needs_rehash·손상입력·마이그레이션 플로우·dummy_verify 무예외·surrogate 처리
- check_syntax 160 .py 통과
- **GPU 라이브 배포·검증**(HEAD c944c3f): health 200@4.7ms, VERSION 2.86.10, GPU pytest 8 passed, 로그인 오답/미존재 user 둘 다 200(무예외 거부), 워커 nlwp 39(정상)
- code-analyzer 독립 재검증 88/100, Critical/High 0 → 잔여 Medium/Low 전부 종결

## 불변식
- 기존 비밀번호 그대로 동작(레거시 폴백) · 강제 재설정 0 · 잠금 0
- KDF는 pbkdf2-hmac-sha256 200k 유지 · 외부 의존 0

## 잔여(저우선 백로그)
- sqlite `connect` timeout 명시 일관성(기본 5s라 무한대기 아님 — 미관/일관성)
- graceful_shutdown 시 구워커 `threading._shutdown` 노이즈 정리(무해)
