# Release v2.88.3 — FTS 테넌트 격리(H1) + 스트림 커넥션 회수(M1)

**날짜**: 2026-06-10 | **선행**: v2.88.2 | **계기**: v2.88.1~2 변경분 code-analyzer 검토(78/100)의 High/Medium 후속

## H1 — FTS(keyword) 검색 테넌트 격리 (보안, 선제 봉합)

**결함**: `docs_fts`(FTS5)에 tenant 정보가 없고 `fts_search` 결과엔 `metadata`가 없어 후처리 격리 `_tenant_kb_filter`가 전부 통과 → `mode=keyword`(및 hybrid의 FTS-only 매칭)로 타 테넌트 비공개 의료 지식이 노출될 수 있는 구조. v2.88.2 store 통합으로 FTS에 실제 적재가 시작되며 표면화.
**현행 영향 0 확인**: 도입 시점 FTS 행은 전부 공유(`shared`) — 비공개 테넌트 문서 0건. 실노출 사고는 없었음(HIS RAG 격리 확인서 유효).

**수정**:
- `docs_fts`에 **tenant 컬럼 신설** + `_get_fts_db()` 무중단 자동 마이그레이션(구 스키마 감지→재구축, 기존 행=`'shared'`)
- `fts_index(tenant=)`/`fts_index_batch`(metadata.tenant_id) 적재, `fts_search(tenant=)` = `tenant IN ('shared','',?)` SQL 단계 격리. **tenant 미지정=무필터**(werub_docs 등 테넌트 무관 컬렉션·기존 호출 100% 호환)
- `hybrid_search(tenant=)` 전달 + FTS-only 항목 metadata에 `tenant_id` 동봉(후처리 필터 일관)
- medical.py: 색인 tenant 전달, keyword/hybrid(지식검색·reference) 호출 3곳 `tenant=tenant["id"]`

## M1 — `/api/medical-law/ask/stream` 업스트림 커넥션 회수

Ollama `stream=True` 응답을 `try/finally r.close()`로 감싸 클라이언트 조기 종료(GeneratorExit) 시에도 세션 커넥션 풀 회수 보장(풀 고갈 방지).

## L3 — `/ask` 캐시 키 보정

캐시 키에 `notice_category` 포함 — 같은 질문을 다른 고시 필터로 호출 시 구 캐시 반환되던 정확성 결함 수정.

## 검증

- 회귀 `tests/test_fts_tenant.py` **5건**: 격리(t1은 shared+자기만)·무필터 호환·**레거시 자동 마이그레이션**·배치 tenant_id·hybrid FTS-only 메타
- GPU: 마이그레이션 로그 확인 + 2테넌트 교차 E2E(t1 비공개 색인→t2 keyword/hybrid 미노출·t1 노출)

## 영향 범위

- 기존 호출 시그니처 전부 하위호환(tenant 기본 None/'shared'). rag.py 일반 색인(`fts_index_batch` metadata에 tenant_id 없음)은 'shared'로 기존 동작 동일.
- code-analyzer 잔여: M2(삭제 부분실패 재시도 큐)·M3(정리기 풀스캔 비용)은 백로그(현 규모 무해), L1·L4 모니터링.
