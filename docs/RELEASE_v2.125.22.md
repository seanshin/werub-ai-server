# v2.125.22 — ⑦ CORS 로컬 오리진 반사 절 제거 (v2.125.18 이월분)

날짜: 2026-08-26 | 선행: v2.125.18 §0 ⑦ 확진(협소)·별건 이월

## 1. 결함

`app.py add_cors_headers`가 `is_local_request()`면 **임의 Origin을 반사**했다.
ssh -L 터널을 연 개발자의 브라우저에서 악성 사이트가 `localhost:8585`로 요청하면
반사된 `Access-Control-Allow-Origin` + **로컬 인증 bypass**의 결합으로 관리자 표면의
교차 출처 읽기가 가능했다(v2.125.18에서 라이브 확진·위험은 터널 연 개발자 한정으로 협소).

## 2. 처치

반사 조건에서 `is_local_request()` 절 제거 — 남는 조건은 **화이트리스트**
(`allowed_origins` — `http://localhost:8585`가 이미 등재돼 정당한 로컬 사용 보존·
무재배포 config 반영)와 **/v1 표면**(서드파티 OpenAI 클라이언트·키 인증 보호)뿐.
관리자 UI·eval-monitor·Swagger는 전부 8585 동일 출처라 CORS 자체가 불요.

## 3. 검증

- 회귀 2건(`tests/test_cors_no_local_reflection.py`) — app.py는 테스트 import 불가(전체
  기동)라 **소스 스캔**(v2.125.16 규율): 반사 조건에 `is_local_request()` 재유입 차단 +
  화이트리스트·/v1 반사 유지 고정. (함정: 처치 주석이 그 문자열을 언급 → 주석 제거 후 검사.)
- 배포 후 라이브 매트릭스: 임의 Origin→ACAO 없음 · 화이트리스트 Origin→반사 ·
  `/v1/*`+임의 Origin→반사 (결과는 §4).

## 4. 라이브 검증 결과 (2026-08-26 · 배포 HEAD `9e05265`)

- 배포: 회귀 통과 · 게이트 **177/177·베이스라인 0**(첫 실행) · 라이브 `2.125.22`.
- CORS 매트릭스(localhost 발신 = 종전 반사되던 바로 그 조건):
  ① `Origin: https://evil.example` + `/api/health` → **ACAO 없음(반사 차단)** ✓
  ② `Origin: https://example.org`(화이트리스트) → `ACAO: https://example.org` 반사 유지 ✓
  ③ `Origin: https://evil.example` + `/v1/models` → 반사 유지(설계대로·키 인증 표면) ✓
