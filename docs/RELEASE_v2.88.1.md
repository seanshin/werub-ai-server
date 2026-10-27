# Release v2.88.1 — 의료법 RAG 백로그: /ask/stream + 노이즈필터 강화

**날짜**: 2026-06-10 | **선행**: v2.88.0 (의료법 RAG 자문 AI) | **범위**: medical-law-rag 비차단 백로그 2건

## 1. POST /api/medical-law/ask/stream — NDJSON 스트리밍

`/ask`와 동일한 RAG(시드 조문 + 법령 3단비교 + KHIDI 고시 벡터검색, 근거인용)를 토큰 단위로 스트리밍.

- 이벤트 시퀀스: `{event:"meta", legal_basis, citations, rag_used, model}` → `{event:"token", token}` × N → `{event:"done", answer, tokens, duration_ms, disclaimer}`
- `answer`(done)는 force_korean 적용 최종본. 이력 저장(law_queries)은 /ask와 동일.
- RAG 조립은 스트림 시작 전에 수행 — 조립 실패는 HTTP 에러로 정상 반환.
- 에러 시 `{event:"error", message}` 라인 후 종료. mimetype `application/x-ndjson`, `X-Accel-Buffering: no`.
- 구현: `routes/medical_law.py` — `/ask`·`/ask/stream` 공용 조립 헬퍼 `_ask_assemble()` 분리(리팩터, /ask 동작 무변경).

```bash
curl -N -X POST http://localhost:8585/api/medical-law/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "진료기록 사본 발급 거부가 가능한가요?"}'
```

## 2. 노이즈필터 강화 — 첨부파일명 양식류 차단 + 기색인 정리

**문제**: `is_relevant_title`은 게시물 제목만 검사 → 제목이 정상 고시인 게시물의 **검토의견서/서식 첨부**가 색인에 유입(라이브 9파일 관찰, 예: `붙임3_검토의견서_양식.pdf`).

- `is_relevant_attachment(filename)` 신설(`core/medlaw_processor.py`): **검토의견/검토서 포함** 또는 **(붙임N_)서식/양식으로 시작**만 노이즈 판정. `명세서서식` 등 본문 고시 제목 중간의 '서식'은 통과(오탐 방지) — 관찰 노이즈 9건 전부 매치, 정상 본문 오탐 0.
- `collect_and_index_new`: 첨부 후보를 필터 통과분으로 한정(양식이 첫 첨부여도 본문 PDF 선택), **제외 건수 로그+결과 카운트**(`noise_skipped_titles`/`noise_skipped_atts`, 기획 §9-1 silent drop 금지).
- `purge_noise(collection)`: 기색인 양식류 청크 제거. 1회 수동 정리(배포 시) + 증분 색인 후 자동 호출(자가유지).

## 검증

- 회귀 `tests/test_medlaw.py` 20→**24건**(첨부 노이즈/정상 판정·prefix 제거·purge 키 경로), 문법 176 .py 통과.
- GPU 배포 후: purge_noise 1회 실행(9파일/9청크 제거 예상) + `/ask/stream` E2E.

## 영향 범위

- `/ask` 응답 무변경(조립 헬퍼 리팩터만). 스케줄러/색인 파이프라인은 필터 추가 외 동일. 폴백 매트릭스 불변(자문 경로 무중단).
