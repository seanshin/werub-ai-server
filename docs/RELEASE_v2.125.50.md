# v2.125.50 — ERP 공시 대사(`/api/notice/sync/verify`) 양쪽 스키마 수용

기준 v2.125.49 · 출처 = 연동 검토 회신 · 운영자 결정(2026-09-28: "양쪽 다 받게")

## 1. 불일치

| | 요청 키 | 응답 키 |
|---|---|---|
| 우리(설계서 `erp-notice-rag.design.md` §sync/verify · v2.125.19) | `snapshot` | `mismatched` |
| ERP 구현(`hospital-erp/services/core/src/erp/integrations/ai_publisher.py:94`) | `items` | `mismatches` |

ERP는 `post(…/sync/verify, json={"items": snapshot})` 후 `resp.json().get("mismatches", [])`로 읽는다
⇒ 그대로 붙으면 **400**(snapshot 없음)이고, 400이 안 나도 `mismatches`가 없어 **빈 목록**으로 보였을 것이다
(반복 클래스 #1 모양). ERP 쪽 주간 잡(`job_sync_verify`)은 아직 TODO라 실피해 0.
항목 스키마는 양쪽 동일(`erp_ref_id`·`revision_no`·`content_hash` — ERP TODO 주석 확인).

## 2. 처치

- 요청: `snapshot` **또는** `items`(항목 스키마 동일). **둘 다 보내면 400**(모호성 차단). 오류 문구는 받은 키 이름으로(`items[0]: …`).
- 응답: `mismatched`와 **`mismatches`에 같은 목록**. 기존 필드·의미 불변.
- Swagger 스키마에 두 키 모두 명시.

## 3. 검증

회귀 신규 4(ERP 호출 모양 그대로 — `items` 요청 → `mismatches` 판독 · snapshot 경로도 alias 보유 ·
둘 다 보내면 400 · 오류 문구 키 이름) · 기존 11 무수정 통과 · 전체 **2260 passed**.
