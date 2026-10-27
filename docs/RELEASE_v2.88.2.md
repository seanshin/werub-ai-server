# Release v2.88.2 — 의료 지식 RAG 이중 store 통합

**날짜**: 2026-06-10 | **선행**: v2.88.1 | **범위**: v2.87 백로그 "의료 RAG 이중 store 통합"

## 근원 문제 (3중 분리)

| 경로 | store | 임베딩 | 상태 |
|---|---|---|---|
| 색인 `POST /api/medical/knowledge/index` | `chromadb_data/`(_get_chroma) | qwen3-embedding:8b(→_v2 컬렉션) | 실데이터 157건은 구 bge-m3 컬렉션 |
| 검색 hybrid/vector/keyword | `vector_db/`(client.rag) | bge-m3 | **0건** → 항상 무히트 |
| 원본 | `medical.db medical_knowledge` | — | 156행 (전부 공유) |

→ `/api/medical/reference` 등 RAG 자문이 사실상 SQL LIKE 폴백에 의존. 추가로 EMBED_MODEL 전환(qwen3) 후 색인은 `medical_knowledge_v2`(4096차원)를 보는데 기존 데이터는 bge-m3 컬렉션이라 신구 데이터도 분리. 삭제 API는 SQLite만 지워 고아 벡터 잔존.

## 변경

1. **store 단일화**: 색인·삭제·테넌트 purge 전부 `client.rag`(`vector_db/medical_knowledge` 벡터 + FTS5 키워드)로 통일. `MEDICAL_KB_COLLECTION` 상수화.
   - 색인: `index_document`+`fts_index` (hybrid의 양 축 모두 채움), metadata `{category,title,source,tenant_id|'shared'}` — 기존 `_kb_tenant_ok`/`_tenant_kb_filter` 격리와 호환.
   - 삭제: SQL rowcount 확인 후에만 `delete_document`+`fts_delete` (타 테넌트 문서 보호 + 고아 벡터 수정). `fts_delete` 신설(`client/rag.py`, collection 스코프).
   - 테넌트 삭제: 지식문서 id 수집 → 벡터/FTS 동반 정리.
2. **레거시 제거**: `_get_chroma`/`_get_embedding`/`EMBED_MODEL(qwen3-embedding:8b)`/`_embed_cache` 삭제 — VRAM 부담(8B 임베딩 모델 로드) 제거, 임베딩은 bge-m3 `num_ctx=2048` 단일(v2.86.12 픽스 경로).
3. **폴백 단순화**: reference의 3단 폴백(hybrid→구chroma→SQL) → 2단(hybrid→SQL). SQL 폴백은 tenant WHERE 격리 유지(자문 경로 무중단 불변식).
4. **백필**: `scripts/backfill_medical_kb.py` — medical.db 156행 → vector_db+FTS 멱등(upsert) 재색인. tenant_id NULL→`'shared'`.
5. **구 store 처분**: `chromadb_data/medical_knowledge`(157건)는 코드 미참조 동결 보존(원본은 medical.db라 데이터 유실 없음). `chromadb_data/code_knowledge`(coding.py)는 범위 밖 무변경.

## 검증

- 문법 177 .py 통과, 잔여 참조(_get_chroma/_get_embedding) 0.
- GPU: 백필 실행 → `vector_db/medical_knowledge` 156건 + hybrid 실히트, `/api/medical/reference` 인용 동작, 색인→검색→삭제 라운드트립, test_medical_isolation 등 회귀.

## 영향 범위

- API 응답 키 추가: knowledge DELETE에 `deleted`(rowcount), tenant DELETE에 `kb_docs` — additive.
- 검색 무히트→실히트로 reference 품질 향상(동작 변화는 의도된 개선). RAG 미가용 시 SQL 폴백 불변.
