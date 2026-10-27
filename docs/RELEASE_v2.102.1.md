# Release v2.102.1 — API 명세 상세화 (약물/DUR/e약은요 요청·응답 스키마)

**2026-06-12** | 이전: [v2.102.0](RELEASE_v2.102.0.md)

## 요약

이번 세션 추가 약물/의료 엔드포인트의 OpenAPI(flasgger) docstring에 **요청·응답 스키마를
상세 추가** — 호출자가 `/openapi.json`·Swagger UI에서 파라미터·바디·응답 필드 구조와 타입·
예시를 정확히 알 수 있도록.

## 보강 엔드포인트 (parameters + responses schema)

- `routes/notice.py`: fee-codes/<code>, drug-substitution(GET/<code>·POST·stats),
  drug-master(resolve POST·GET/<code>·stats), drug-data/status.
- `routes/medical_advanced.py`: dur/check(응답 8유형 필드 전체), dur/drug, dur/stats,
  drug-info(GET/POST·응답 효능·부작용 등 필드), drug-info/stats, dur/sync-status, dur/sync.

각 엔드포인트에 path/body 파라미터(타입·필수·설명·예시)와 200 응답 스키마(properties 전체)를 명시.
DUR check는 contraditions·pregnancy/elderly/age_warnings·duplicate_therapy·split_cautions 등
응답 필드 전체를 문서화.

## 검증
- GPU(배포 후): `/openapi.json` 재생성, 약물/DUR 엔드포인트의 parameters·responses 스키마 노출 확인.
