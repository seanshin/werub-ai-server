# Release v2.87.1 — ERP 사전심사 후속(연동이슈 fix·임계 config화·회귀테스트)

> Date: 2026-06-10 | 이전: v2.87.0
> v2.87.0(ERP 사전심사 통합)의 같은-날 후속 패치. 실데이터 유입 중 발견된 연동이슈 수정 + 캘리브레이션 인프라 + 회귀 안전망.

---

## 1. ERP 연동이슈 fix (실데이터 유입 중 발견)

ERP가 실 고시·실데이터를 적재하며 보고한 2건 수정:

- **REVOKE/DELETE 404** — weve가 내부 `ntc-*` id만 인식 → ERP가 자기 `erp_ref_id`로 폐지/삭제 시 404.
  `_resolve_notice_id(ident)` 추가(id 우선, 없으면 `erp_ref_id` 최신 revision 폴백) → revoke/delete/index-status에 적용.
- **index-status 405** — ERP가 경로 id 없이 폴링(`GET /api/notice/index-status`) → 405.
  **컬렉션 라우트 신설** `GET /api/notice/index-status?client_request_id=|erp_ref_id=|notice_id=`. 경로형 `/{nid}/index-status`도 `erp_ref_id` 허용.

## 2. diagnose 위험밴드 임계 config화 (무재배포 캘리브레이션)

ERP P0.5 섀도 목표 **precision ≥ 0.8** 튜닝을 코드 변경 없이:
- `config.json` `notice_diagnose_thresholds: {"high": 0.70, "medium": 0.40}` (기본값) — `_risk_level`이 config 참조
- 유효성(0≤medium<high≤1) 위반·파싱오류 시 기본값 폴백
- diagnose 응답에 `risk_thresholds` 노출(ERP가 적용 밴드 확인)
- 캘리브레이션 흐름: ERP 섀도(diagnose 호출→실삭감 G1 대조→precision/recall 산출) → 목표 미달 시 weve `config.json` 임계 조정 → 즉시 반영

## 3. content_hash 정합 종결 (c)

ERP 제공 3벡터(`ERP/docs/01-plan/references/hash-vectors/`, PR #98)를 weve `content_hash`(정규화 7항)로 대조:
- vector1 일반(행내 연속공백 보존) `0e15…3943` ✅
- vector2 표/탭 `469c…7f9c` ✅
- vector3 **CRLF+BOM+전각`１`** `27207…0936` ✅ (전각 보존=NFC, ERP와 바이트 일치)
- raw_sha256 수신 무결성 3건 OK → **정규화 7항 양측 정합 확정, 운영 진입 가능**

## 4. 데모 정리 + 실데이터 E2E

- 데모 공시 9건 purge(erp_ref_id DELETE) → 실고시 2건만 잔존(제2026-125호 TARIFF·118호 DRUG_MATERIAL)
- 실데이터 E2E: 검색(118호 0.643)·평가(치료재료 상한초과 HIGH 1.0 has_evidence 근거인용) 정상. nightly 스케줄러 프로덕션 가동 확증.

## 5. 회귀 테스트 41건

| 파일 | 대상 | 건수 |
|------|------|------|
| `test_fee_codes.py` | 코드→type 매핑·import·resolve·upsert·stats (순수 sqlite, iMac 실행가능) | 8 |
| `test_notice_v287.py` | `_effective_on_ok`·`_risk_level`·**임계 config** + revoke/delete `erp_ref_id` 리졸버 + index-status 컬렉션/경로 + fee-codes 엔드포인트 + 입력검증 | 21 |
| `test_rag_filter.py` | `_build_where`(owner/ns·$and·$in·raw$·검증) | 7 |
| `test_medical_isolation.py` | `_kb_tenant_ok`·`_tenant_kb_filter`(공유/레거시/타테넌트) | 4 |

전체 스위트 **460+ passed**(GPU). 외부 의존 0(또는 importorskip), 백그라운드 색인 no-op 격리.

## ERP 운영 합의 (P0.5→P1)
- 섀도 대조 주체 = **ERP**(diagnose 호출→저장→실삭감 G1 도착 시 매칭·precision/recall 산출)
- P1 임계 = **precision ≥ 0.8**(과탐 억제 우선)부터 협의
- 전 구간 ERP 실데이터 게이트(실 통보서 샘플 미확보 → G1 → 라벨) — weve는 대기

## 백로그
- 의료 RAG 이중 store 통합 / ERP 실 고시 전량 적재 / export.csv(441k) 방식② on-demand
