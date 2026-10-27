# Release v2.97.1 — DUR 7유형 문서·API 명세 동기화

**2026-06-12** | 이전: [v2.97.0](RELEASE_v2.97.0.md)

## 요약

v2.97.0 DUR 7유형 확장(병용금기 외 임부·연령·노인·용량·투여기간·효능군중복 + 환자 컨텍스트)을
문서·OpenAPI 명세에 반영. 기능 변경 없음(docstring/문서 — GPU 재기동으로 `/openapi.json` 갱신).

## 변경 사항

- **`/api/medical/dur/check` docstring(OpenAPI 소스)**: 병용금기 단일 설명 → **7유형 + `age`/`pregnant`
  파라미터 스키마 + 응답 필드**(pregnancy/age/elderly_warnings·duplicate_therapy·advisories) 명시.
  `/openapi.json`에 age/pregnant 파라미터 노출.
- **docs/API_REFERENCE.md 19B**: "DUR 병용금기" → "DUR 7유형 금기/주의" — 7유형·환자 컨텍스트·응답
  필드·식약처 오픈 API 출처·환자 컨텍스트 curl 예시.
- **docs/MEDICAL_API_GUIDE.md**: DUR 항목 7유형화 + age/pregnant 예시 + 출처(식약처 오픈 API, 서비스키 시크릿).

## 검증
- iMac: 문법 OK. GPU(배포 후): `/openapi.json`의 dur/check에 age/pregnant 파라미터 노출 확인.
