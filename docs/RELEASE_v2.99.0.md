# Release v2.99.0 — DUR 서방정분할주의 (8유형 완성)

**2026-06-12** | 이전: [v2.98.0](RELEASE_v2.98.0.md)

## 요약

식약처 DUR **품목정보** API(`DURPrdlstInfoService03`)의 **서방정분할주의**(2,099품목)를
추가해 DUR을 **8유형으로 완성**(병용금기·임부금기·연령금기·노인주의·용량주의·투여기간주의·
효능군중복·**서방정분할주의**). "이 서방정 쪼개도 되나요?" 류에 정본 답변.

## 변경 사항

- `core/dur.py`: `dur_split` 테이블(item_seq PK — 서방정분할주의는 품목 단위) + `import_split`.
  `check()`에 **`split_cautions`**(원문 제품명/품목일련번호 매칭 — exact→LIKE) 추가, `has_warning`·
  `dur_types_checked`(8유형)·`stats` 반영.
- `scripts/ingest_dur_api.py`: 품목정보 서비스(`DURPrdlstInfoService03`)의 서방정분할주의 적재 추가
  (성분정보 7유형은 기존 `DURIrdntInfoService03`). 서비스키 시크릿(env, 미커밋).
- `dur_check` 도구·`/api/medical/dur/check` docstring 8유형으로 갱신(성분명/제품명 입력).

## 미적재(중복) 판단
- DUR 품목정보의 나머지 7유형(병용/노인/연령/용량/기간/효능군중복/임부)은 성분정보 정본과 중복 → 미적재.
- DUR 품목 마스터(`getDurPrdlstInfoList03`, ITEM_SEQ↔EDI_CODE↔BAR_CODE)는 HIRA `drug_master`와 중복 →
  보류(향후 e약은요 itemSeq↔보험코드 연계가 필요하면 이 마스터로 보강 가능).

## 검증
- iMac: `pytest tests/test_dur.py` → 15 passed(서방정분할 제품명/품목코드 매칭 포함).
- GPU(배포 후): 서방정분할 2,099 적재, dur/check `split_cautions` 라이브.
