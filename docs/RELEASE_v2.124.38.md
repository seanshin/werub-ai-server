# Release v2.124.38 — RAG delete FTS 동반 정리 + edu 후속 스키마 확정 회신

날짜: 2026-07-27 | 종류: fix(rag) + docs(edu) | 배포: GPU graceful HUP(무재시작)

## 배경

edu 후속 요청 2건(①delete/stats 스키마 확정 — 반려·삭제 콘텐츠 색인 정리 훅용
②batch index 스키마 — 대량 재색인 최적화) 회신 준비 중 계약 검토서 **실결함 1건** 발견.

## 결함·수정 — delete가 FTS 잔존물 미정리

- **결함**: 배치 색인(`POST /api/rag/index` documents)은 벡터(ChromaDB)+FTS5
  (`fts_index_batch`) 양쪽에 쓰는데, `DELETE /api/rag/document/{id}`는
  `delete_document`(벡터)만 호출 — **FTS 행이 영구 잔존**. hybrid 검색
  (`search hybrid=true`)의 FTS 경로로 삭제 문서가 재부상 가능.
  `client/rag.py fts_delete`는 "벡터 delete_document와 병행" 용도로 이미
  존재했으나 라우트가 호출하지 않던 배선 누락(v2.88.2 계열 비대칭).
- **수정**: `routes/rag.py rag_delete`에 `fts_delete(doc_id, collection)`
  best-effort 호출(실패 시 debug 로그·벡터 삭제 결과 비차단). edu 측 변경 0.
- **영향 범위**: edu는 현재 단건 색인(FTS 미기록)이라 즉시 영향 없으나, 배치
  전환+삭제 훅 배선 시 발현될 예정이던 갭의 선제 봉합.

## edu 회신 — 스키마 확정 3건 (§9, 실측 포함)

정본: `docs/01-plan/references/edu-ai-integration-reply.md` §9

1. **delete**: `DELETE /api/rag/document/{id}?collection=edu_docs` — id 단건·멱등
   (없는 id도 ok:true). filter/courseId 일괄 삭제 미지원 → id 목록 반복 권장
   (고빈도화 시 안전 상한 가드 포함 서버측 추가 협의).
2. **stats**: `GET /api/rag/stats?collection=edu_docs` → `{name,count,metadata}`.
   과정별 카운트 미지원 → edu 콘텐츠 원장 집계 권장.
3. **batch index**: `{collection, documents[≤500]}`(단건 스키마 그대로 배열).
   **GPU 실측: 단건 1.47s/건 ↔ 배치 100건 1.37s·500건 6.15s(~12ms/건, ~100배)**.
   120/min은 요청 수 기준=배치 1회 1요청. 권장 200~500건/요청·순차·타임아웃 120s.
   upsert라 재색인 멱등.

## 검증

- 회귀: `tests/test_rag_collection.py` 6→8 (FTS 동반 정리·비차단 실패) GPU 8 passed.
- 라이브: graceful HUP 무재시작(`Active: since 07-25` 유지)·`/api/version` 2.124.38·
  스크래치 컬렉션 601건 색인→삭제 왕복(신규 delete 경로 실사용)·최종 벡터 count 0·
  **FTS 잔존 0 확인**.
- 벤치 부산물(edu_bench_tmp) 전량 정리. 부수 실측: **localhost도 120/min 레이트리밋
  대상**(601건 연속 삭제서 429 발생·16건 3회 재시도 소진→백오프 재실행으로 완료)
  → §9-1에 대량 삭제 페이스·백오프 가이드 반영.
