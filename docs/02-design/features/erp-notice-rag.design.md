# ERP 공시 연동 + 공시 RAG — Design

> **Feature**: erp-notice-rag | **Version Target**: v2.84.0 | **Created**: 2026-06-07
> **Plan**: `docs/01-plan/features/erp-notice-rag.plan.md` (ERP 회신 반영 완료 — 폴링 단독 모드 확정)

## 1. 컴포넌트 구조

```
routes/notice.py           ← Blueprint 12 API (신규)
core/notice_indexer.py     ← 정규화/해시/청크/색인 잡 처리기 (신규)
core/notice_db.py          ← notice.db 연결/DDL (신규, core/db.py 패턴)
notice.db                  ← 공시 + 색인 잡 (신규, WAL)
config.json                ← notice_* 설정 키 추가
core/security.py           ← (기존) require_auth + ERP 키 rate limit 예외
client/rag.py              ← (기존) ChromaDB 래퍼 재사용 (notice_knowledge collection)
core/drug_batch_scheduler.py ← (참조) 야간 사이클 패턴 — notice nightly 잡 소비 등록
tests/test_notice.py       ← 회귀 테스트 (신규)
```

## 2. 데이터 흐름

### 2-1. 공시 등록 → 색인 (immediate)
```
ERP → POST /api/notice (Bearer sk-erp-..., body ≤1MB)
  → 검증: 필수필드(erp_ref_id, revision_no, notice_type, title, body, effective_from)
  → 정규화 7항 적용 → content_hash 계산 → ERP 송신 해시와 대조 (불일치 시 422)
  → notices INSERT (UNIQUE(erp_ref_id, revision_no) — 중복 시 409 + 기존 ntc-id 반환[멱등])
  → notice_index_jobs INSERT (mode=immediate)
  → [immediate 전용 워커 스레드 즉시 깨움] — SLA 5분
      → 청크 분할 → qwen3-embedding:8b (iMac /worker/embed 위임, 실패 시 GPU 폴백)
      → ChromaDB notice_knowledge upsert + FTS 색인
      → job.status = done
  → 응답 202 {"id": "ntc-...", "index_job": {...}}
ERP → GET /api/notice/{id}/index-status?client_request_id=... (30s 폴링)
```

### 2-2. 개정 / 폐지
```
개정: PUT /api/notice/{ntc-id}  (새 revision_no 포함)
  → 새 notices 행 INSERT (같은 erp_ref_id, revision_no+N)
  → 구 행 status=superseded
  → 잡 2건: ① 구 revision 청크 remove ② 신 revision index
폐지: POST /api/notice/{ntc-id}/revoke
  → status=revoked → remove 잡 (청크 삭제 — 검색에서 즉시 제외는 메타필터가 보장하므로 잡은 nightly 허용)
```

### 2-3. 질의
```
POST /api/notice/query {"question", "notice_type"?, "as_of"?, "include_internal"?}
  → hybrid_search(notice_knowledge, where={status:"active", notice_type≠INTERNAL(기본)})
  → 후처리 필터: effective_from ≤ as_of(기본 오늘) AND (effective_to IS NULL OR ≥ as_of)
     ※ 미시행(effective_from > as_of) 공시는 결과에 포함하되 caveat "시행 예정" 표시
  → 컨텍스트 조립(build_context_with_citations) → qwen2.5:14b 답변 생성 → force_korean
  → citations: [{notice_id, doc_no, title, effective_from, snippet, score}]
```

## 3. DB 설계 (notice.db)

Plan §3-1 DDL 확정본 사용. 추가 설계 사항:

- **멱등성**: 같은 (erp_ref_id, revision_no) 재등록 시 content_hash 동일하면 200 + 기존 잡 상태 반환,
  해시 다르면 409 (ERP는 개정으로 보내야 함) — ERP 재시도(지수 백오프) 안전
- **연결**: `core/notice_db.py` — `get_notice_db()` (thread-local, WAL, busy_timeout=5000ms, conn.close 보호)
- **clinic_codes.db 삭감 데이터와의 관계**: 기존 삭감 140건은 구조화 룰(청구 검증용), 공시는 원문 RAG —
  분리 유지. REVIEW_CRITERIA 공시가 색인될 때 향후 룰 추출 파이프라인 후보(스코프 외)

## 4. API 명세 (핵심 4종 — 전체 12종은 Plan §4)

### POST /api/notice
```json
Request: {
  "erp_ref_id": "ERP-2026-0612", "revision_no": 1,
  "notice_type": "REVIEW_CRITERIA",          // 8종 코드 외 422
  "title": "...", "body": "...",             // body ≤ 1MB(정규화 후 바이트)
  "doc_no": "심평원공고 제2026-141호", "source": "건강보험심사평가원",
  "source_url": "https://...",               // optional
  "effective_from": "2026-07-01", "effective_to": null,
  "content_hash": "sha256:ab12...",          // ERP 계산값 — 당측 재계산 대조
  "mode": "immediate",                        // immediate | nightly(기본)
  "client_request_id": "erp-req-78f3"
}
Response 202: {"id": "ntc-a1b2", "index_job": {"id": 17, "mode": "immediate", "status": "pending"}}
오류: 400(필수 누락) 409(중복/해시 상이) 413(1MB 초과) 422(해시 불일치·코드 무효) 429(한도)
```

### GET /api/notice/{id}/index-status  (또는 ?client_request_id=)
```json
Response: {"job_id": 17, "notice_id": "ntc-a1b2", "client_request_id": "erp-req-78f3",
           "action": "index", "status": "done", "engine": "gpu", "chunks": 6,
           "created_at": "...", "finished_at": "..."}
```

### POST /api/notice/sync/verify  (ERP→AI push, 주 1회 일요일 02–06시)
```json
Request: {"snapshot": [{"erp_ref_id": "ERP-2026-0612", "revision_no": 2, "content_hash": "sha256:..."}]}
Response: {"total": 412, "matched": 410,
           "mismatched": [{"erp_ref_id": "...", "revision_no": 2, "reason": "hash_diff|missing|extra"}]}
```
- `missing`: ERP에 있는데 AI에 없음 → ERP가 재발신 / `extra`: AI에만 있음 → AI가 revoke 후보로 관리자 알림

### POST /api/notice/query
Plan §4-3 스키마. `include_internal=false` 기본. 검색 필터는 **서버 측 강제** — 요청으로 해제 불가.

## 5. core/notice_indexer.py 설계

```python
normalize_body(text: str) -> bytes      # 정규화 7항: BOM제거→NFC→LF통일→행끝공백제거→끝개행1→바이트열
content_hash(text: str) -> str          # "sha256:" + hexdigest(normalize_body)
chunk_notice(body: str) -> list[Chunk]  # ① 조문 패턴(제N조/N./별표N/[붙임]) 분할 시도
                                        # ② 실패·과대(>1500자) 시 512자 슬라이딩(오버랩 64)
process_job(job_id: int) -> None        # 잡 실행: 임베딩(위임/폴백) → upsert/remove → 상태 갱신
run_immediate_worker() -> None          # 데몬 스레드: immediate 잡 즉시 소비 (Event 깨움, SLA 5분)
run_nightly_cycle() -> dict             # 야간 배치: pending nightly 일괄 (drug Night Mode 패턴 등록)
```

- **임베딩 엔진 선택**: `config["apple_worker_url"]` + delegate에 "embed" 포함 + 헬스 OK → iMac, 아니면 GPU.
  모델은 `config["notice_embed_model"]`(기본 `qwen3-embedding:8b`) — 컬렉션-모델 맵 검증 후 호출
- **테스트 벡터**: `tests/data/notice_hash_vectors.json` — 샘플 3건(일반/표서식/CRLF+BOM) + 기대 해시.
  ERP와 교환하는 파일과 동일본 (회신 ⑤)

## 6. 설정 (config.json 추가 키)

```json
{
  "notice_enabled": true,
  "notice_embed_model": "qwen3-embedding:8b",
  "notice_erp_key_name": "erp-notice",        // 이 이름의 API 키에 rate limit 예외
  "notice_rate_limit_per_min": 600,            // ERP 키 전용 한도 (기본 IP 120/min 대체)
  "notice_immediate_sla_sec": 300,
  "notice_nightly_hour": 3                      // 03:00 (drug night와 시간대 분리)
}
```

- **rate limit 예외 구현**: 기존 IP 기반 검사 전에 API 키 이름이 `notice_erp_key_name`이면
  키 기반 카운터(분당 `notice_rate_limit_per_min`) 적용 — `core/security.py` 데코레이터 분기 ~15줄

## 7. 오류 처리 / 엣지 케이스

| 케이스 | 처리 |
|--------|------|
| ERP 해시 ≠ 당측 재계산 해시 | 422 + 양측 정규화 단계별 중간값 로깅 (분쟁 디버깅) |
| immediate 잡이 SLA 5분 초과 | 잡 계속 진행 + 경고 로그 + Prometheus `notice_sla_breach_total` 증가 |
| 임베딩 양쪽(iMac/GPU) 실패 | job failed + 다음 nightly 자동 재시도 (최대 3회 후 관리자 알림) |
| revoke 대상 ntc-id 미존재 | 404 — ERP는 sync/verify 대사로 정합 회복 |
| batch 100건 중 일부 실패 | 부분 성공 응답: `{"accepted": [...], "rejected": [{"erp_ref_id", "reason"}]}` |
| 미시행 공시 질의 노출 | 결과 포함 + `caveats: ["2026-07-01 시행 예정 — 현행 기준 아님"]` |

## 8. 테스트 계획 (tests/test_notice.py)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | 정규화 7항 벡터 3건 | 기대 해시 일치 (ERP 교환본과 동일) |
| 2 | 수명주기: 등록→개정→폐지 | 각 단계 query 결과에서 구버전/폐지본 미인용 |
| 3 | 멱등 재등록 | 동일 해시 200, 상이 해시 409 |
| 4 | UNIQUE(erp_ref_id, revision_no) | 중복 INSERT 차단 |
| 5 | 조문 청크 분할 | "제1조...제2조" 입력 → 조 단위 분할, 비정형 → 512자 폴백 |
| 6 | sync/verify | hash_diff/missing/extra 3종 검출 |
| 7 | 시행일 필터 | as_of 경계값 (시행 전날/당일/종료 익일) |
| 8 | INTERNAL 기본 제외 | include_internal 토글 동작 |
| 9 | 1MB 한도 | 413 응답 |
- LLM/ChromaDB 의존 구간은 mock — 기존 157건 테스트 컨벤션(순수 core 단위) 준수

## 9. 구현 순서 (4일)

| Day | 작업 |
|-----|------|
| 1 | notice_db.py + notice_indexer.py(정규화/해시/청크) + 테스트 벡터 |
| 2 | routes/notice.py 수명주기 5종 + 멱등/검증 + rate limit 예외 |
| 3 | 색인 잡 워커(immediate 스레드 + nightly 등록) + 상태/changes/sync API |
| 4 | query + 인용 + 필터 강제 → 회귀 테스트 → Swagger/API_REFERENCE/CLAUDE.md 갱신 |
