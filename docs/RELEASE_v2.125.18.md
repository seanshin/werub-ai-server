# v2.125.18 — 미검증 보안 7건 재현·판정 후 확진 3건 처치

날짜: 2026-08-25 | 선행 v2.125.17(§4에 7건 미검증으로 명시) | 규율: 남의 결론도 통제군을 거친다(v2.125.5)

## 0. 재현·판정 — 에이전트 결론을 그대로 승격하지 않았다

v2.125.17이 §4에 남긴 7건을 **하나씩 직접 재현**했다. 결과: **확진 4 · 부분정정 2 · 위험낮음 1.**
에이전트 보고 3건(③⑤⑥)이 실제보다 부풀려져 있었다 — 검증이 유효했다.

| # | 항목 | 판정 | 근거(재현) |
|---|---|---|---|
| ① | medical 테넌트 플레이스홀더 | 🔴 확진 | `api_key` 컬럼=`hashed:{id}` 리터럴 · **3/3 테넌트가 `X-Medical-Key: hashed:{id}`로 우회**(실측) · `/api/medical/tenants`가 id를 임의 키로 노출 · PHI 경로 · 로테이션으로 안 닫힘 |
| ② | RAG collection 자유 지정 | 🔴 확진 | `collection:"medical_law_knowledge"`로 rag 검색 성공(테넌트 격리 없음). medical 라우트만 `_tenant_kb_filter`를 걸고 rag는 안 걺 |
| ④ | `/api/delete`·`/api/pull` 무-admin | 🔴 확진 | 두 라우트에 `_is_admin_request()` 없음 · 임의 유효 키로 fleet 파손 가능 |
| ⑦ | CORS loopback 오리진 반사 | 🔴 확진(협소) | `app.py:1477` `is_local_request()`면 오리진 반사 · `ssh -L` 터널 연 개발자 한정 |
| ③ | `/api/config` 시크릿 노출 | 🟡 **부분 정정** | 시크릿 전량 응답은 사실이나 **`_is_admin_request()` 게이트가 있다**(외부 키 403). localhost/위조 localhost로만 가능했고 R1(v2.125.17)이 위조 진입로를 이미 닫음 |
| ⑤ | iMac 워커 임의 파일 쓰기 | 🟡 **부분 정정** | `mlx_stt.transcribe`가 `filename` 무검증은 사실이나 `/worker/stt`는 `@worker_auth`(공유 토큰) 게이트 — "임의 API 키"가 아니라 워커 토큰 보유자 한정 |
| ⑥ | 커밋된 `sk-` 키 | 🟢 **위험 낮음** | 그 prefix가 현재 20개 활성 키 어디에도 없음 = **이미 폐기**. 로테이션 불요 |

## 1. 처치 — 확진 3건(①②④) + ⑥ 문서 정리

전부 **이 레포가 이미 옳게 푼 패턴의 미적용 지점**이었다(v2.125.17의 관찰이 여기서도 그대로).

**① `routes/medical.py` `_resolve_medical_tenant`** — 레거시 평문 조회에서 플레이스홀더 제외:
```python
if not key or key.startswith("hashed:"):
    return None
t = conn.execute("SELECT * FROM medical_tenants "
                 "WHERE api_key=? AND api_key<>'' AND api_key NOT LIKE 'hashed:%' AND is_active=1", ...)
```
🔑 `hr_law.py:696`이 이미 `AND api_key<>''`로 옳게 하고 있었다 — medical만 빠져 있었다.

**④ `routes/ollama.py` `/api/pull`·`/api/delete`** — `_is_admin_request()` 게이트 추가.
형제 `/api/keys`·`/api/config`·`service/restart`는 이미 게이트돼 있었고 이 둘만 빠져 있었다.

**② `routes/rag.py`** — 파트너 범용 스토어라 collection 자유 지정이 설계지만, 의료·공시 전용
컬렉션 4종(`medical_knowledge`·`medical_law_knowledge`·`medical_pub_knowledge`·`notice_knowledge`)을
**차단 목록**으로 막는다(`_collection_blocked` → 5개 진입점: index 단건·배치·search·ask·delete).
파트너 워크플로(Cerno·edu 컬렉션)는 영향 0.

**⑥** `docs/archive/plan_module_split.md`의 폐기된 키 문자열 REDACT(git 이력 잔존은 별건).

## 2. 회귀 6 + 전체 1839 passed

`tests/test_security_unverified7.py`: ① 플레이스홀더 SQL 제외(메모리 DB로 실증)·소스 계약 ·
④ 두 라우트 admin 게이트 소스 확인 · ② 보호 컬렉션 403·파트너 통과·**5개 진입점 전부 배선**.

## 3. 남긴 것

- **⑦ CORS**: 확진이나 조건이 좁다(개발자 터널). `is_local_request()` 반사 절 제거는 별건 —
  `http://localhost:8585`가 이미 `allowed_origins`에 있어 콘솔은 그 절 없이도 동작한다.
- **⑤ iMac**: `mlx_stt`에 `secure_filename` 추가는 iMac 워커 배포가 걸려 별건(워커 토큰이 1차 방어).
- **③**: 정정으로 종결(게이트 있음).
- **git 이력 blob**(v2.125.17 §4 X2·`moderation.db`·`training.db`)은 히스토리 재작성이 필요한 별건.
