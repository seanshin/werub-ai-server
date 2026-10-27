# Release v2.124.39 — RAG 색인/삭제 FTS·청크 대칭 완결 (v2.124.38 보강)

날짜: 2026-07-27 | 종류: fix(rag) | 배포: GPU graceful HUP(무재시작)

## 배경

v2.124.38(delete FTS 동반 정리) 검토서 확인된 **잔여 비대칭 2건** 보강.
edu 계약 자체는 불변(edu 측 변경 0) — 서버 내부 정합성 완결.

## 수정 2건

1. **단건·청크 색인 FTS 미기록**: `POST /api/rag/index`가 배치(documents) 경로만
   `fts_index_batch`를 호출 — 단건/청크 색인 문서는 키워드(FTS) 검색 사각.
   edu는 현재 단건 POST라 edu_docs 전체가 hybrid 검색 FTS 경로에서 안 보이던
   상태. → 단건/청크 경로에도 FTS best-effort 기록(청크는 **부모 doc_id**로 원문
   기록=delete 동반 정리와 대칭·비차단).
2. **청크 색인 문서 delete 무효**: `chunk_strategy` 색인은 청크 id가
   `{doc_id}__chunk_N`이라 부모 id `DELETE`가 벡터를 못 지움(영구 잔존).
   → `client.delete_document`가 `source_doc_id` 메타 매칭 동반 삭제(비청크
   문서=매칭 0 no-op·문서당 청크 수십 개 규모라 대량삭제 폭주 클래스 무관·
   실패 시 warning 로그=무성 실패 금지).

## 부수

- Swagger 실계약 보강(openapi.json=외부 연동 셀프서비스 정본 규율): index
  documents(≤500·413·upsert 멱등)·delete(멱등·동반 정리·collection 필수 권고)·
  stats(응답 필드 name/count/metadata·메타 필터별 카운트 미지원 명시).

## 검증

- 회귀: `tests/test_rag_collection.py` 8→11(단건 FTS 기록·청크 FTS 기록·청크
  동반 삭제) GPU passed.
- 라이브 E2E: 단건 색인→FTS 행 생성→삭제→벡터·FTS 0 / 청크 색인
  (chunk_strategy)→부모 id 삭제→청크 벡터·FTS 전부 0.
