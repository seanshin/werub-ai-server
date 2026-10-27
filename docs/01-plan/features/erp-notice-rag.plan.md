# ERP 공시 연동 API + 공시 RAG 파이프라인

> **Feature**: erp-notice-rag
> **Created**: 2026-06-07
> **Version Target**: v2.84.0 (apple-silicon-worker와 병행 개발 가능 — 색인 위임부만 의존)
> **Priority**: P2 (High — 의료법/삭감공지 RAG의 데이터 공급 채널)
> **Related**: `apple-silicon-worker.plan.md` Phase 3 (iMac 임베딩 배치)

## 1. 목적

ERP에서 관리하는 **주요 공시**(의료법 개정, 심평원 삭감공지, 행정고시/수가 변경 등)를
AI 서버와 소통시키는 표준 API를 구축한다. ERP가 공시를 등록/개정/폐지하면 AI 서버가
자동으로 RAG 색인에 반영하고, 색인 결과를 ERP에 콜백으로 통지한다. 사용자는 공시 기반
RAG 질의로 "이 청구가 최신 삭감 기준에 걸리는지" 같은 질문에 **근거 공시 인용과 함께**
답변받는다.

```
[ERP] ──공시 등록/개정/폐지──> [AI 서버 공시 API] ──색인 잡──> [iMac qwen3-embedding 임베딩]
  ^                                  │                              │
  └────색인 완료/실패 callback────────┘         [ChromaDB notice_knowledge] <─┘
                                                       │
[사용자/ERP] ──질의──> [공시 RAG 질의 API] ──검색+14b 답변+공시 인용──> 응답
```

## 2. 현황 분석

### 2-1. 재사용 가능한 기존 자산

| 자산 | 위치 | 재사용 방식 |
|------|------|------------|
| RAG 색인/하이브리드 검색/질의 | `routes/rag.py` (5 API) + FTS | 공시 전용 collection으로 래핑 |
| staging → 검증 → 승인 워크플로우 | `routes/drug_batch.py` (staging_drug) | 공시 색인 잡 모델의 패턴 차용 |
| callback_url + SSRF 검증 | `routes/meeting.py` + v2.74 webhook IP검증 | 색인 완료 통지에 그대로 재사용 |
| 테넌트 API 키 | `routes/hr_law.py` (X-Tenant-Key) | ERP 전용 키 발급 패턴 |
| 데이터 출처 추적 | `routes/provenance.py` (6 API) | 공시 등록/개정 이력 연계 |
| 야간 배치 스케줄러 | `core/drug_batch_scheduler.py` (Night Mode) | 야간 색인 모드 패턴 차용 |

### 2-2. 현재 부재한 것

- ERP가 공시를 **프로그래밍 방식으로 밀어넣을 표준 입구 없음** (현재 RAG 색인은 범용 `/api/rag/index`뿐 — 공시 도메인 모델 없음)
- 공시의 **생애주기 관리 없음**: 개정(supersede)·폐지(revoke) 시 낡은 공시가 검색에 계속 잡힘 → **잘못된 삭감 기준으로 답변할 위험**
- 색인 완료/실패를 ERP가 알 방법 없음 (단방향)
- 시행일 기반 유효성 필터 없음 (미시행 공시와 현행 공시 구분 불가)

## 3. 데이터 모델

### 3-1. notice.db (신규)

```sql
-- 공시 본체 (개정 시 새 행 생성 — 같은 erp_ref_id에 revision_no 증가)
-- 동기화 키: erp_ref_id + revision_no (ERP 회신 A-2 확약 — 불변 ID + 버전 행 체계 수용)
CREATE TABLE notices (
  id            TEXT PRIMARY KEY,         -- ntc-{uuid} (버전마다 새 id)
  erp_ref_id    TEXT NOT NULL,            -- ERP측 불변 공시 ID (개정·폐지에도 불변)
  revision_no   INTEGER DEFAULT 1,        -- ERP revision_no 그대로 수용 (1,2,3…)
  notice_type   TEXT NOT NULL,            -- ERP 코드표 8종 (§3-3)
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,            -- 본문 (정규화 UTF-8 텍스트, ERP가 등록 시 강제)
  doc_no        TEXT,                     -- 공시/고시 번호 (예: 보건복지부고시 제2026-xx호)
  source        TEXT,                     -- 발행 기관 (복지부/심평원/식약처/...)
  source_url    TEXT,
  effective_from TEXT NOT NULL,           -- 시행일 (ERP 필수 입력 확약)
  effective_to  TEXT,                     -- 종료일 (nullable)
  status        TEXT DEFAULT 'active',    -- active | superseded | revoked (AI 내부 상태)
  supersedes_id TEXT,                     -- 개정 시 이전 공시 id
  content_hash  TEXT,                     -- 본문 SHA-256 (정규화 7항 §5-2 적용)
  created_at    TEXT, updated_at TEXT,
  UNIQUE(erp_ref_id, revision_no)
);
CREATE INDEX idx_notices_active ON notices(status, notice_type, effective_from);
CREATE INDEX idx_notices_erp ON notices(erp_ref_id);
```

**ERP status ↔ AI status 매핑** (ERP 회신 A-3):

| ERP | AI 내부 | 비고 |
|-----|---------|------|
| SCHEDULED / ACTIVE | `active` | 시행 전/중 구분은 저장하지 않고 **질의 시점에 effective_from으로 계산** — SCHEDULED→ACTIVE 전이 이벤트 수신 불필요 (AI 회신 ②) |
| REPEALED | `revoked` | revoke API로 전파 |
| (ERP 개념 없음) | `superseded` | 개정 시 구 revision에 AI가 자동 부여 |

### 3-2. notice_type 코드표 (ERP 회신 A-4 초안 채택)

`TARIFF`(수가) · `DRUG_MATERIAL`(약제·치료재료) · `REVIEW_CRITERIA`(심사기준/삭감) ·
`MEDICAL_LAW`(의료법령) · `ADMIN`(행정고시) · `NARCOTIC`(마약류) · `LABOR`(노동·인사) ·
`INTERNAL`(원내 규정 — **색인 포함하되 질의 시 기본 제외, `include_internal=true`로 opt-in** 제안)

### 3-3. 색인 잡

```sql
-- 색인 잡 (staging 패턴) — callback 제거, 폴링 단독 모드 (ERP 회신 B-2)
CREATE TABLE notice_index_jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  notice_id   TEXT NOT NULL,
  client_request_id TEXT,                 -- ERP 발신 식별자 (ERP가 폴링 조회 시 사용)
  action      TEXT NOT NULL,              -- index | reindex | remove
  mode        TEXT DEFAULT 'nightly',     -- immediate | nightly
  status      TEXT DEFAULT 'pending',     -- pending | running | done | failed
  engine      TEXT,                       -- imac | gpu (실제 처리한 쪽)
  chunks      INTEGER, error TEXT,
  created_at  TEXT, started_at TEXT, finished_at TEXT
);
CREATE INDEX idx_jobs_client ON notice_index_jobs(client_request_id);
```

### 3-4. ChromaDB

- Collection: `notice_knowledge` — 기존 medical_knowledge와 분리
- **임베딩 모델: qwen3-embedding:8b (4096d)** — 현행 RAG 주 임베딩(v2.62 업그레이드, bge-m3는 폴백)과 통일.
  컬렉션-모델 매핑을 config에 고정하고, **GPU·iMac 어느 쪽이 색인하든 동일 모델 강제** (벡터 비호환 방지 —
  `apple-silicon-worker.plan.md` Phase 3 제약과 동일). 모델 교체 시 전체 재색인 필수임을 운영 문서에 명기
- 청크 메타데이터: `notice_id`, `erp_ref_id`, `revision_no`, `notice_type`, `doc_no`, `effective_from`, `effective_to`, `status`
- **검색 시 `status=active` + 시행일 필터를 기본 적용** — 폐지/개정 전 공시가 답변 근거로 쓰이는 사고 차단. `INTERNAL` 유형은 기본 제외(opt-in)

## 4. API 설계 (routes/notice.py — 12 API)

### 4-1. ERP → AI (공시 수명주기)

| # | Method | Endpoint | 설명 |
|---|--------|----------|------|
| 1 | POST | `/api/notice` | 공시 등록 (단건). `mode=immediate`면 즉시 색인. 본문 ≤ 1MB |
| 2 | POST | `/api/notice/batch` | 일괄 등록 (초기 적재/마이그레이션용, 최대 100건/요청) |
| 3 | PUT | `/api/notice/{id}` | **개정** — 같은 `erp_ref_id`로 ERP `revision_no`의 새 행 생성(새 ntc-id 반환), 이전 revision `superseded` + 색인 교체 |
| 4 | POST | `/api/notice/{id}/revoke` | **폐지** — status 변경 + 색인 제거 잡 생성 |
| 5 | DELETE | `/api/notice/{id}` | 삭제 (오등록 정정용, 색인 제거 포함) |

```bash
# 공시 등록 예시 (ERP에서 호출)
curl -X POST https://example.org/ollama/api/notice \
  -H "Authorization: Bearer sk-erp-..." -H "Content-Type: application/json" \
  -d '{
    "erp_ref_id": "ERP-2026-0612",
    "revision_no": 1,
    "notice_type": "REVIEW_CRITERIA",
    "title": "초음파 검사 삭감 기준 변경 안내",
    "body": "2026-07-01부터 ... 산정 기준이 다음과 같이 변경됨 ...",
    "doc_no": "심평원공고 제2026-141호",
    "source": "건강보험심사평가원",
    "effective_from": "2026-07-01",
    "mode": "immediate",
    "client_request_id": "erp-req-78f3"
  }'
# → {"id": "ntc-a1b2...", "index_job": {"id": 17, "mode": "immediate", "status": "pending"}}
```

### 4-2. 색인 상태/제어

| # | Method | Endpoint | 설명 |
|---|--------|----------|------|
| 6 | GET | `/api/notice/{id}/index-status` | 색인 상태 (pending/running/done/failed + engine). **`?client_request_id=`로도 조회 가능** — ERP 폴링의 1차 채널 (회신 B-2 수용) |
| 7 | POST | `/api/notice/{id}/reindex` | 재색인 (청크 정책 변경/오류 복구) |
| 8 | GET | `/api/notice/changes?since={ts}` | 변경분 조회 — ERP 동기화 검증·폴링 보조 채널 |
| 9 | POST | `/api/notice/sync/verify` | 정합성 검증 — ERP가 `[{erp_ref_id, revision_no, content_hash}]` 스냅샷 push → 불일치 목록 반환. ⚠️ **방향 주의**: ERP는 인바운드 차단망이라 AI가 ERP 스냅샷 API를 호출할 수 없음 → ERP→AI push로 확정 (AI 회신 ⑥) |

### 4-3. 조회/질의 (사용자·ERP 공용)

| # | Method | Endpoint | 설명 |
|---|--------|----------|------|
| 10 | GET | `/api/notice/list` | 목록 (type/status/시행일 범위 필터 + 페이지네이션) |
| 11 | GET | `/api/notice/{id}` | 상세 (개정 체인 포함) |
| 12 | POST | `/api/notice/query` | **공시 RAG 질의** — 하이브리드 검색 + 14b 답변 + 근거 공시 인용 |

```bash
# RAG 질의 예시
curl -X POST .../api/notice/query -d '{
  "question": "복부 초음파와 흉부 CT를 같은 날 시행하면 삭감되나요?",
  "notice_type": "reduction",
  "as_of": "2026-07-15"
}'
```
```json
{
  "answer": "2026-07-01 시행된 기준에 따르면 ... 동시 산정 시 ...",
  "citations": [
    {"notice_id": "ntc-a1b2", "doc_no": "심평원공고 제2026-141호",
     "title": "초음파 검사 삭감 기준 변경 안내", "effective_date": "2026-07-01",
     "snippet": "...", "score": 0.91}
  ],
  "caveats": ["시행일 2026-07-01 이후 청구분에만 적용"],
  "model": "qwen2.5:14b", "duration_ms": 4200
}
```

### 4-4. 색인 결과 전달 — **폴링 단독 모드** (ERP 회신 B-2 합의로 callback 폐기)

> ERP는 병원 격리망(HIS 동거 서버) 내부에 있어 인터넷→ERP 인바운드가 차단됨.
> 우리 측 SSRF 검증도 사설 IP callback을 차단하므로 **callback은 설계에서 제거**하고 폴링 단독으로 확정.

- **1차 채널**: ERP가 발신 시 `client_request_id` 부여 → `GET /api/notice/{id}/index-status?client_request_id=` 폴링 (immediate 건은 30s 간격 권장, batch 건은 다음 영업일 일괄 확인)
- **보조 채널**: `GET /api/notice/changes?since={ts}` — ERP 측 누락 복구·정기 대사용
- **ERP 측 이중 안전망** (회신 B-1): ERP 발신 자체가 비동기 워커 + 지수 백오프 + DLQ — 양측 모두 재시도 체계 보유

### 4-5. 인증

- ERP 연동(§4-1, 4-2): **ERP 전용 Bearer API 키** (기존 키 관리 체계 — 이름 `erp-notice`, 만료 관리 v2.76 기능 활용)
- 질의(§4-3): 기존 Bearer 인증 (외부) / localhost bypass (내부)

## 5. 색인 파이프라인 (iMac 연계)

```
공시 등록 → notice_index_jobs(pending)
  ├─ mode=immediate → 즉시 처리 (긴급 삭감공지 등)
  └─ mode=nightly   → 야간 배치 (기본, drug Night Mode 패턴)
      │
      ▼ 처리 시 엔진 선택 (apple-silicon-worker 위임+폴백 원칙, 모델은 양쪽 동일)
  iMac /worker/embed 가용? ──Yes──> iMac qwen3-embedding:8b (Metal) ──┐
      └──No(터널 다운/미구축)──> GPU 서버 qwen3-embedding:8b 폴백 ────┤
                                                                      ▼
  청크 분할(법령 조문 단위 우선, 기본 512자) → ChromaDB upsert
  → 개정이면 이전 revision 청크 삭제 → FTS 색인 → 잡 상태 done (ERP가 폴링으로 확인)
```

**색인 SLA** (ERP 회신 C-4 합의):

| 모드 | SLA | 트리거 (ERP 회신 C-1 기준 4종) |
|------|-----|------|
| immediate | **5분 이내 색인 반영** | ① 심사기준·수가 변경 중 시행 7일 이내 ② 마약류(NARCOTIC) 전체 ③ 공포 즉일 시행 ④ ERP 관리자 수동 긴급 플래그 |
| nightly (batch) | **24시간 이내** | 그 외 전체. ERP 측 벌크 발신도 진료시간(08–18시) 외로 제한됨 (회신 B-1) |

**content_hash 정규화 규칙** (ERP 회신 B-4 합의 — 연동 스펙으로 확정):

```
1. 인코딩: UTF-8 (BOM 제거)          5. 문서 끝: 마지막 개행 1개로 정규화
2. 유니코드 정규화: NFC               6. 그 외 공백·연속 공백 보존 (표 서식 보호)
3. 개행: CRLF/CR → LF 통일           7. 해시 대상: 정규화된 body_text 바이트열 전체
4. 행 끝 trailing whitespace 제거        (SHA-256)
```
- 구현: `core/notice_indexer.py`에 정규화 함수 + **양측 테스트 벡터 3건 상호 검증** 후 운영 진입 (설계 단계 산출물)

- **apple-silicon-worker Phase 3 미완성이어도 동작**: 폴백 경로(GPU qwen3-embedding:8b)가 기본이므로 본 기능을 먼저 출시 가능. iMac 워커가 올라오면 설정만으로 이관
- 청크 전략: 공시 특성상 "조문/항목 단위" 분할 우선 (제N조, 별표, 항목 번호 패턴), 실패 시 512자 슬라이딩

## 6. 구현 범위 요약

| # | 항목 | 내용 |
|---|------|------|
| 6-1 | `routes/notice.py` 신규 | 12 API (위 설계) + Swagger 스펙 |
| 6-2 | `core/notice_indexer.py` 신규 | 청크 분할(조문 인식) + 색인 잡 처리기 + 엔진 선택/폴백 |
| 6-3 | `notice.db` 신규 | §3-1 스키마, WAL, busy_timeout 5000ms (기존 컨벤션) |
| 6-4 | 야간 배치 등록 | drug_batch_scheduler 패턴으로 nightly 색인 사이클 추가 |
| 6-5 | callback 발송기 | meeting callback + v2.74 SSRF 검증 재사용 |
| 6-6 | 질의 핸들러 | rag.py hybrid_search 재사용 + status/시행일 필터 + 인용 포맷 |
| 6-7 | 회귀 테스트 | tests/test_notice.py — 수명주기(등록→개정→폐지) + 필터 + 정합성 검증 |
| 6-8 | Admin 콘솔 패널 | 공시 목록/색인 상태 (선택 — 1차는 API만) |

## 7. 검증 계획

| 항목 | 방법 | 통과 기준 |
|------|------|----------|
| 수명주기 | 등록→개정→폐지 후 각 단계에서 query 호출 | 개정 후 구버전 미인용, 폐지 후 해당 공시 미인용 |
| 시행일 필터 | `as_of` 변경하며 질의 | 미시행 공시는 caveat 표시, 만료 공시 제외 |
| 정합성 | sync/verify에 고의 불일치 해시 | 불일치 정확 검출 + 정규화 7항 테스트 벡터 3건 양측 일치 |
| 폴링 | client_request_id로 등록→완료 추적 | immediate 건 5분 내 done 조회 가능 (SLA 검증) |
| 색인 폴백 | iMac 터널 차단 후 immediate 색인 | GPU 폴백으로 완료, engine=gpu 기록 |
| 인용 정확성 | 삭감 질의 20건 샘플 | 인용 공시가 답변 근거와 일치 (수동 검수) |

## 8. 공수 산정

| 항목 | 공수 |
|------|------|
| DB + Blueprint 12 API | 1.5일 |
| 색인 파이프라인 (청크/잡/야간/폴백) | 1일 |
| 질의 + 인용 + 필터 | 0.5일 |
| callback + 정합성 검증 | 0.5일 |
| 테스트 + 문서 (API_REFERENCE/CLAUDE.md) | 0.5일 |
| **합계** | **4일** |

## 9. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 폐지/개정 공시가 답변 근거로 사용 | **오답 → 청구 손실** (최대 리스크) | status+시행일 기본 필터를 검색 레이어에 강제 (호출자가 못 끔), 수명주기 회귀 테스트 |
| ERP↔AI 데이터 불일치 (누락/중복) | 색인 공백 | erp_ref_id UNIQUE + content_hash + sync/verify API + changes 폴링 백업 |
| 공시 원문이 PDF/HWP 첨부 | 본문 텍스트 확보 곤란 | **1차 범위는 텍스트 본문 필수** (ERP가 텍스트 추출 책임). 첨부 파싱은 후속 과제로 분리 |
| 조문 단위 청크 분할 오류 | 검색 품질 저하 | 패턴 실패 시 512자 폴백, 인용 검수(§7)로 측정 |
| ERP 폴링 누락 (재시도 소진 후 미확인) | 색인 상태 불일치 | changes API 정기 대사(주 1회 일요일 02–06시, 회신 C-3) + ERP DLQ 관리자 알림 |
| 프록시 단일 IP로 rate limit 집중 | ERP batch 발신 차단 | IP 기반 분당 120회 제한에 ERP API 키 예외(키 기반 한도) 적용 — 설계 단계 반영 |
| 임베딩 모델 교체 (qwen3-embedding 버전업 등) | 기존 벡터 전량 무효 | 컬렉션-모델 맵 config 고정 + 교체 시 reindex 잡 일괄 생성 절차 문서화 |
| qwen3-embedding:8b 색인 처리량 (M1) | 야간 배치 지연 | iMac 벤치마크 미달 시 GPU 폴백이 기본이므로 기능 영향 없음 (위임만 보류) |

## 10. 확인 필요 사항 — **2026-06-07 ERP 회신 수신** (`/Users/user/ERP/docs/01-plan/references/weve-ai-integration-reply.md`)

| 항목 | 결과 |
|------|------|
| 1. 본문 포맷 | ✅ 신규분 텍스트 확약 (등록 시 강제) / 과거분 [병원 확인 대기] — +2일 공수는 과거분 batch에만 해당 |
| 2. 분류 체계 | ✅ ERP 코드표 8종 채택 (§3-2) |
| 3. 콜백 | ⚠️ 격리망 인바운드 불가 → **폴링 단독 모드로 합의** (§4-4 재설계 완료) |
| 4. 초기 규모 | ⏳ [병원 확인] 1주 내 — 평시 월 수~수십 건, 피크는 연초 수가 개정기 수백 건 추정 |

**잔여 대기 항목**: 과거 공시 보유 형태/건수(병원), 화이트리스트 등록 완료(병원 정보보안팀), DMZ 프록시 가능 여부(참고용 — 폴링 단독이 기본이므로 비차단)

## 11. 의사결정 기록

| 결정 | 선택 | 근거 |
|------|------|------|
| 연동 방향 | ERP push + callback (+ changes 폴링 백업) | 공시 발생 시점을 ERP가 알므로 push가 자연스럽고, 기존 meeting callback 패턴 재사용 |
| 색인 기본 모드 | nightly (긴급만 immediate) | 공시는 정기 공지형 데이터, GPU/iMac 자원 효율 |
| DB 분리 | notice.db 신규 | 도메인 분리 컨벤션 (hr_law.db/medical.db와 동일 원칙) |
| iMac 의존성 | 선택적 (폴백 기본) | apple-silicon-worker와 일정 독립 — 먼저 출시 가능 |
| 임베딩 모델 | qwen3-embedding:8b (4096d) 고정 | 현행 RAG 주 모델과 통일, 검색 품질 우선. bge-m3 1024d 초안은 검토에서 구버전 기준으로 판명되어 정정 |
| 개정 모델링 | 새 행 + UNIQUE(erp_ref_id, revision_no) | ERP 회신 A-2: 불변 ID + revision_no 체계 그대로 수용 (초안 version → revision_no 통일) |
| 결과 전달 | **폴링 단독** (callback 폐기) | ERP 격리망 인바운드 차단 + 우리 SSRF 정책 — 양측 제약이 일치, callback 실익 없음 (회신 B-2) |
| 분류 체계 | ERP 코드표 8종 수용 | ERP가 SoR이므로 원천 분류를 그대로 사용, 매핑 레이어 제거 |
| 해시 정규화 | ERP 제안 7항 수용 | 분쟁 방지 — 테스트 벡터 상호 검증 후 운영 진입 |

## 12. 부록 — ERP 측 요청 사항 (협조 공문/전달용)

> §10 확인 사항을 실행 가능한 요청 목록으로 구체화. ★ = 착수 전 필수 회신.

### A. 데이터 제공 (기획 확정에 필요)

| # | 요청 | 이유 | 필수 |
|---|------|------|------|
| A-1 | 공시 **본문을 텍스트로** 제공 가능한지 회신 (PDF/HWP 원본만 보유 시 그 사실) | 텍스트 불가 시 첨부 파싱 Phase 추가 — 공수 +2일, 일정 재산정 | ★ |
| A-2 | ERP 공시 **분류 체계 목록** (카테고리 코드표) | `notice_type`(medical_law/reduction/tariff/admin_notice) 매핑표 확정 | ★ |
| A-3 | 공시 **고유 ID 체계** 설명 — 고유성 보장 여부, **개정 시 ID가 유지되는지** | `erp_ref_id` 동기화 키 설계의 전제. 개정 시 ID가 바뀌면 버전 체인 설계 변경 필요 | ★ |
| A-4 | 제공 가능한 메타데이터 필드 확인: 공시번호(doc_no), 발행기관, **시행일**, 만료일 | 시행일은 유효성 필터의 핵심 — 누락 시 답변 정확성 직결 | ★ |
| A-5 | **초기 적재 규모**: 기존 공시 건수, 평균/최대 본문 크기 (1MB 초과 존재 여부), 유형별 분포 | batch 마이그레이션 계획 + 본문 한도 검증 | ★ |
| A-6 | 테스트용 **샘플 공시 유형별 5~10건** (실제 개정 이력이 있는 공시 1건 이상 포함) | 청크 분할(조문 인식)·수명주기 테스트 데이터 | |

### B. 연동 개발 (ERP 측 구현 필요)

| # | 요청 | 내용 |
|---|------|------|
| B-1 | 공시 **등록/개정/폐지 시점에 AI 서버 API 호출** 구현 | POST `/api/notice`, PUT `/api/notice/{id}`, POST `/api/notice/{id}/revoke` — 스펙은 본 기획서 §4 |
| B-2 | ERP 내부에서 공시 **수명주기 이벤트 훅** 존재 여부 확인 | 등록/개정/폐지를 코드에서 가로챌 수 있는지 — 없으면 ERP 측 개발 범위 증가 |
| B-3 | **callback 수신 엔드포인트** 구현 + URL 통보 | `notice.indexed` 이벤트 수신 (POST JSON, §4-4 스키마). 실패해도 `changes` API 폴링으로 복구 가능하므로 단순 수신+로깅이면 충분 |
| B-4 | **네트워크 확인**: ERP → `https://example.org/ollama/*` 아웃바운드 허용 여부, callback URL이 AI 서버에서 도달 가능한 공인 주소인지 | AI 서버 webhook SSRF 검증(내부 IP 차단)을 통과하려면 callback URL이 사설 IP/메타데이터 주소면 안 됨 |
| B-5 | **API 키 수령 담당자** 지정 | ERP 전용 Bearer 키 발급·전달 (만료 정책 포함, v2.76 키 만료 관리 적용) |
| B-6 | `content_hash` 계산 방식 합의 | SHA-256 over UTF-8 본문(개행 정규화 LF) 제안 — 정합성 검증(sync/verify)의 기준 |

### C. 운영 정책 (합의 필요)

| # | 요청 | 내용 |
|---|------|------|
| C-1 | **긴급 공시 기준** 합의 | 어떤 공시를 `mode=immediate`(즉시 색인)로 보낼지 — 예: 시행일 7일 이내 삭감 기준 변경 |
| C-2 | 공시 발생 **빈도/볼륨** 추정치 | rate limit 및 야간 배치 용량 설계 근거 |
| C-3 | 정합성 검증 **주기** 합의 | `sync/verify` 호출 주기 제안: 주 1회 (ERP 측 스케줄러) |
| C-4 | 데이터 책임 경계 확인 | ERP = 원천(SoR), AI 서버 = 색인 사본 — ERP 측 공시 삭제/보존 정책과 AI 색인 동기화 원칙 합의 |
| C-5 | 스테이징/테스트 환경 및 **연동 테스트 일정** | 수명주기 시나리오(등록→개정→폐지) 공동 테스트 |

### D. 회신 양식 (요약)

```
A-1 본문 텍스트 제공: 가능 / 불가(PDF·HWP만)
A-3 공시 ID: 체계 = ____ , 개정 시 ID 유지 = 예/아니오
A-4 시행일 필드: 있음 / 없음
A-5 기존 공시: 약 ____건, 최대 본문 ____KB
B-3 callback URL: https://____ (구현 예정일: ____)
B-4 아웃바운드 허용: 예 / 방화벽 신청 필요
C-1 긴급 공시 기준: ____
```

## 13. 검토 이력

| 일자 | 검토 결과 |
|------|----------|
| 2026-06-07 | 코드베이스 대조 검토 — ① 스키마 결함 정정: erp_ref_id 단독 UNIQUE → UNIQUE(erp_ref_id, version) (개정 시 INSERT 충돌) + 조회 인덱스 2종 추가 ② 임베딩 현행화: bge-m3 1024d → qwen3-embedding:8b 4096d (v2.62 업그레이드 반영, GPU·iMac 동일 모델 강제) ③ callback 재시도 정책(3회 백오프) + 백그라운드 발송(v2.74 gthread 점유 회귀 방지) 명시 ④ 본문 1MB·batch 100건 한도 명시 ⑤ 청크 메타에 erp_ref_id/version 추가 |
| 2026-06-07 (2차) | **ERP 회신 반영** — ① callback 전면 폐기 → 폴링 단독 모드 (client_request_id 조회 채널 신설) ② 동기화 키 erp_ref_id+revision_no 수용, ERP status 매핑표(SCHEDULED/ACTIVE/REPEALED) 추가 ③ notice_type을 ERP 코드표 8종으로 교체 (INTERNAL은 질의 기본 제외 opt-in) ④ content_hash 정규화 7항 합의 반영 ⑤ 긴급 기준 4종 + SLA(immediate 5분/batch 24h) 확정 ⑥ sync/verify 방향을 ERP→AI push로 확정 (AI→ERP 호출 불가) ⑦ rate limit 키 기반 예외 리스크 추가. AI 측 회신: `docs/01-plan/references/notice-integration-reply-to-erp.md` |
