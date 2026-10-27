# Release v2.87.0 — ERP 사전심사 통합 + HIS PHI 비저장/키 로테이션/RAG 격리

> Date: 2026-06-10 | 이전: v2.86.13
> 신규 엔드포인트 7 + 하드닝 3. 세션 보고서: `docs/SESSION_REPORT_20260610.md`

---

## 1. ERP 사전심사 통합 (검색 P0 + 평가 P0.5)

ERP가 weve에 요청한 **역방향 질의 채널**(공시 검색 + 삭감위험 평가) 구현.
회신서: `docs/01-plan/references/ai-prescreening-reply-to-erp.md`

### 1-1. 색인 처리기 (erp-notice-rag Day 3)
`core/notice_processor.py` — Day 1-2(수명주기 API)가 잡 생성만 하고 소비기가 없던 갭 해소.
- `index_notice`: 공시 본문 `chunk_notice`(조문 경계) → bge-m3 임베딩 → `notice_knowledge` 컬렉션 upsert(메타 notice_id/erp_ref_id/notice_type/status/effective_from·to)
- `remove_notice`: notice_id 청크 일괄 삭제 (개정 supersede / 폐지)
- **immediate**: 제출 직후 백그라운드 처리(SLA 5분) — `mode:"immediate"`
- **nightly**: 스케줄러 `notice_nightly_hour`(기본 3시 UTC) 일괄 + immediate 5분 안전망 (프로덕션 가동 확증)
- 임베딩 = bge-m3(1024d, 14b와 공존 검증) — 설계의 qwen3-embedding:8b는 MAX_LOADED_MODELS=1 축출 우려로 보류

### 1-2. 신규 엔드포인트

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/notice/search` | 공시 검색(P0) — `active_only`·`notice_type[]`·`effective_on`(진료일 시행여부)·`code`(자동 type 해석) 필터 |
| POST | `/api/notice/diagnose` | 삭감위험 평가(P0.5) — 청구라인(비식별)→위험도·근거인용·권고 |
| POST | `/api/notice/process-jobs` | pending 색인 잡 수동 처리(데모·복구) |
| GET | `/api/notice/<id>/index-status` | 색인 상태 폴링(`?client_request_id`) |
| POST | `/api/notice/fee-codes/import` | 청구 코드마스터 적재(rows[] 또는 csv+가변컬럼) |
| GET | `/api/notice/fee-codes/stats` | 코드마스터 현황 |
| GET | `/api/notice/fee-codes/<code>` | code→notice_type 해석 |

**검색** `POST /api/notice/search`:
```json
{"query":"MRI 급여","notice_type":["REVIEW_CRITERIA"],"effective_on":"2026-06-25","active_only":true}
```

**평가** `POST /api/notice/diagnose`:
```json
{"claim_line":{"code":"AA157","description":"외래 재진 진찰료","service_date":"2026-03-10","context":"동일일 재진 2회 산정"}}
→ {"risk_level":"HIGH","risk_score":0.9,"has_evidence":true,
   "evidence_citations":[{"title":"외래 재진 진찰료 산정기준",...}],
   "recommendation":"재검토 필요","applied_notice_type":["REVIEW_CRITERIA","TARIFF"],"auto_resolved_type":true}
```
- 위험밴드: **HIGH≥0.70 / MEDIUM 0.40~0.69 / LOW<0.40** (ERP 합의)
- **근거 인용 강제**: 모델이 제공 고시를 인용해야 `has_evidence=true`. 미인용 HIGH(=근거 없는 위험)는 `has_evidence=false`→ERP가 'AI 판단 보류'(환각 방지)
- 진료일(`service_date`) 시행 중 심사기준만 근거. 비식별(환자 식별자 미수신).

### 1-3. 청구코드→notice_type 자동해석 (방식 ②)
`core/fee_codes.py` — ERP 코드마스터(441k) 적재 → 청구 `code`→`category`→`notice_type`.
- 매핑(ERP 합의): 수가/TARIFF→`[REVIEW_CRITERIA,TARIFF]`, 약가/DRUG·치료재료/MATERIAL→`[DRUG_MATERIAL,REVIEW_CRITERIA]`
- 적재: `{"rows":[{code,category,name}]}` 또는 `{"csv":"...","columns":{"code":"코드",...}}` (가변 헤더, ≤10만/req)
- search/diagnose는 `notice_type` 미지정 + `code` 시 자동 해석. **방식①(ERP가 type 직접 전달)과 호환**(fee_codes 비면 스킵).
- 정규화 정합: weve는 **NFC**(NFKC 아님) → 전각 보존, ERP와 일치.

---

## 2. HIS 연동 — PHI 비저장 + 키 로테이션 + RAG 격리

확인서: `docs/01-plan/references/his-phi-nonstorage-confirmation.md` / 회신: `his-integration-reply.md`

- **키 로테이션(무중단)**: 신 의료키·Bearer 병행 발급 → HIS 전환 → 구키 폐기(라이브 401). 구키 git 노출 대응.
- **PHI 비저장**: `medical_phi_store` 토글(기본 False) → `_save_consultation` 미실행(진료 input/output 미저장), `_audit_log` 요약 미보존. `/api/chat` 호출별 opt-out(`store:false`/`X-No-Store`)+전역 `chat_persist_messages`. 라이브 E2E 검증. 기존 PHI(98행/103건) 파기.
- **RAG 지식베이스 테넌트 격리**: `medical_knowledge` 검색 5경로(reference hybrid+chroma 폴백, knowledge/search hybrid/vector/keyword)에 `_kb_tenant_ok` post-filter — 타 테넌트 비공개 제외, 공유·레거시 보존(restrictive where 회피로 레거시 오제외 방지).
- **의료 일일한도**: HIS 요청으로 500→5,000/일(월 150,000).

---

## 3. 범용 RAG 메타데이터 필터

- `rag/search`·`rag/ask` **`filter` 파라미터 실동작** — `_build_where`로 ChromaDB where 변환. `{owner,ns}` 동등/다중($and)/$in/raw$or. 색인 `metadata:{owner,ns}` → **의료진 개인별 지식 격리**(조직→개인단위).

---

## 4. 부수 — medical.db 무결성 복구

- PHI purge 중 `medical_consultations` 테이블 손상(malformed) 발견(타 테이블 정상) → 비파괴 재구축·교체. 원본 백업 2종 보존. 무손실(tenant3·knowledge156·audit103).

## 백로그
- 의료 RAG 이중 store(색인 `_get_chroma`/chromadb_data vs 검색 client.rag/CHROMA_PATH) 통합
- ERP: 실 고시 전량 적재, 실삭감 라벨(G1 후)→위험밴드 캘리브레이션→P1 노출, export.csv(441k) 적재
- 데모 공시 9건 정리(ERP 확인 후)
