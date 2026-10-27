# Release v2.96.0 — 약물 데이터 통합 반영 (통합검토 P1+P2+P3)

**2026-06-12** | 이전: [v2.95.0](RELEASE_v2.95.0.md)

## 배경

v2.91~v2.95에서 추가한 약물 데이터 스택(약가목록 fee_codes·저가약 대체조제·DUR 병용금기·
코드 크로스워크 drug_master)이 **기존 시스템 흐름에 제대로 연결됐는지 통합 검토** → 발견한
갭(신규 데이터가 siloed)을 P1+P2+P3 전체 반영.

## 변경 사항

### P1 — 기능 통합
- **A. drug-analysis DUR 정본 교차검증**(`/api/medical/drug-analysis`): LLM(meditron/Med42/OpenBioLLM)
  분석 전 식약처 DUR 병용금기를 점검해 **컨텍스트에 주입**(권위 데이터로 grounding) + 응답에
  `dur_authoritative` 동봉. LLM 상호작용 환각을 정본으로 보정.
- **B. 크로스워크 폴백**(`core/drug_master.to_product_codes`): `drug_substitution.lookup`·
  `fee_codes.resolve_notice_types`가 입력이 제품코드가 아니어도(표준코드=바코드/품목기준코드/대표코드)
  drug_master로 제품코드 해석 후 재조회. 응답에 `resolved_via` 표기. → HIS 스캔 바코드가 약가·대체조제에서 동작.

### P2 — 정본 일관화
- **C. quality.check_drug_interactions DUR 병합**: 기존 내부 DB(LLM 시드)는 `source:internal_db`,
  식약처 DUR 정본은 `source:식약처DUR`로 구분해 함께 노출. `has_major`에 `contraindicated` 포함.

### P3 — 가시성·문서
- **D. Admin 약물 데이터 카드** + `GET /api/notice/drug-data/status`(약가·대체조제·크로스워크·DUR 집계).
- **E. `docs/MEDICAL_API_GUIDE.md`** 약물 데이터 스택 섹션 신설(엔드포인트·크로스워크·DUR·통합 동작).

## 검증
- iMac: 영향 테스트 71 passed(drug_substitution/drug_master/dur/fee_codes/notice/quality).
- 통합: 바코드 `8806519003301` → `drug_substitution.lookup`/`fee_codes` 크로스워크 폴백으로 해석 확인(resolved_via).
- GPU(배포 후): drug-analysis `dur_authoritative` 동봉, drug-data/status, Admin 카드, 바코드→대체조제 라이브.
