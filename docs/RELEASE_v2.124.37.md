# v2.124.37 — edu 연동: RAG collection 파라미터 봉합 + 접속정보 회신·키 발급

2026-07-27 | 계기: edu 팀 "생성 엔드포인트 정상화 + 접속정보 요청"({ok:false} 비활성)

## 진단 — 서버는 살아 있었고, 원인은 2건

- **P0 `/v1/chat/completions`**: 라이브 실측 **정상 가동 중**(표준 OpenAI 스키마
  `choices[].message.content`). 외부 무키 호출 = `401 unauthorized`(fail-closed) —
  edu의 `{ok:false}`는 이 401 래핑으로 추정. "edu" 키는 07-08 발급 이력이 있으나
  발급 당일 1회 사용 후 0(분실/미설정 추정).
- **P1 RAG**: `/api/rag/ask`가 **`collection`을 무시**하고 항상 기본 컬렉션
  (werub_docs)을 검색 — search만 collection을 받던 비대칭. `/api/rag/index`도
  비청크 단건·배치 경로에서 collection 무시(청크 경로만 반영), delete·stats도 기본
  고정. 호출자가 컬렉션을 지정해도 **조용히 기본 컬렉션으로 색인/검색**되는 무성 결함.

## 변경 (`routes/rag.py`)

- `rag_ask`: `collection`(기본 werub_docs) 수용 → `rag_search(collection_name=)` 전달.
  edu 계약 `{question, collection}` → `{answer, sources}` 성립.
- `rag_index`: 단건 `index_document`·배치 `index_batch`에 `collection` 전달
  (청크 경로와 대칭화).
- `rag_delete`·`rag_stats`: `?collection=` 쿼리 수용.
- 회귀 `tests/test_rag_collection.py` **6**(ask 전달/기본값·index 단건/배치·delete·stats).
  기존 test_rag_filter 7 무회귀.

## 운영 조치

- **edu 전용 API 키 신규 발급**(prefix `sk-e37ab96…`) + 만료 **365일**(2027-07-27)
  연장 — 07-22 appServer 90일 만료 단절 교훈 + D-7 경보(v2.124.31) 안전망 전제.
  키 전문은 별도 채널 전달(레포 미기재). 구 edu 키(id16 `sk-f3539c1…`·휴면)는
  edu 전환 확인 후 폐기 권고(운영자).
- **회신 문서** `docs/01-plan/references/edu-ai-integration-reply.md` — baseUrl·인증·
  모델(qwen2.5:14b 권장/7b)·요청/응답 스키마(OpenAI 호환 확정)·레이트리밋(120/min)·
  타임아웃(생성 120s 권장)·엔드포인트 경로·컬렉션 위생(edu_docs+owner/ns filter) 확정.

## 검증

- **외부 경유(example.org) E2E, 신규 키**: ①/v1 문항 생성(4지선다 JSON) 정상
  ②rag/index→`edu_docs` 색인 ok ③rag/ask `{question,collection}` → 정답+sources
  인용(score 0.662) ④테스트 문서 삭제(`?collection=`) 정리.
- 무키 외부 호출 401 유지(보안 불변)·GPU 회귀 rag 계열 13 passed·graceful HUP
  NRestarts 0·health 200.

## 주의

- collection 미지정 호출은 기존과 동일(werub_docs) = 하위호환 100%.
- rag/ask 기본 모델은 qwen2.5:7b — 품질 우선 경로(튜터·법정 Q&A)는 호출측에서
  `"model":"qwen2.5:14b"` 지정 권장.
