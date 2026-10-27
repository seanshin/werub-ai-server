# Release v2.92.1 — API 문서 동기화 (ERP/Notice + 청구코드 + 저가약 대체조제)

**2026-06-12** | 이전: [v2.92.0](RELEASE_v2.92.0.md)

## 요약

v2.87~v2.92에서 추가된 **ERP 사전심사/Notice API 군**이 마스터 API 문서에 누락되어 있어
일괄 반영. 코드/엔드포인트 변경 없음(문서 전용 — GPU는 git pull만, 재기동 불요).
OpenAPI 명세(`/openapi.json`)는 flasgger docstring으로 v2.92.0 재기동 시 이미 노출됨(검증 완료).

## 변경 사항

### docs/API_REFERENCE.md
- 신규 섹션 **「19A. ERP/Notice 사전심사 + 청구코드 (15 API)」** 추가(기존 누락분 일괄 문서화):
  - 공시 수명주기 5(등록/일괄/개정/폐지/삭제), 색인 3, 검색·평가 2(`/search`·`/diagnose`),
    청구코드→notice_type 3(fee-codes), **저가약 대체조제 2(`/drug-substitution/<code>`·`/stats`, v2.92.0 신규)**.
  - fee_codes 현황 갱신(TARIFF 7,138 + DRUG 21,898 = 29,036), 대체 후보 임상안전 규칙·curl 예시.
- Medical Law 섹션에 RAG 자문 레이어(`/ask`·`/review`·`/corpus-status`) 포인터 추가.

### docs/MEDICAL_LAW_RAG_GUIDE.md (SoT)
- 코퍼스: `medical_pub_knowledge` 발간물 13종(+저가약 대체조제 청구요령), 15,285청크로 갱신.
- §3 fee_codes: DRUG(약가목록 2026.6.1, 21,898) 반영 → 29,036.
- **§3.5 저가약 대체조제(drug_substitution) 조회 규칙 신설**: eligible 판정·대체 후보 임상안전 규칙.
- §6 적재 도구: `ingest_hira_drug.py`·`ingest_drug_substitution.py`·`ingest_pub_file.py`·구형 HWP olefile 파서 추가.

## 검증
- GPU `/openapi.json` → drug-substitution·fee-codes 5경로 노출 확인.
- 문서 전용 변경 → 재기동 불요.
