# 의료법 자문 RAG — 코퍼스 구성 및 호출 규칙

> 대상: `/api/medical-law/ask`·`/ask/stream`·`/review` 호출자(내부 서비스·ERP·HIS·운영자) 및 유지보수자.
> 코드: `routes/medical_law.py` · 코어 코퍼스: ChromaDB `medical_law_knowledge`(+보조 `medical_pub_knowledge`).
> 청구코드·저가약 대체조제 구조적 조회는 `routes/notice.py`(ERP 사전심사 도메인, notice.db).
> 최종 갱신: 2026-06-12.

의료법 자문 AI는 **법령·고시·발간물을 RAG로 검색해 근거를 인용**하고, LLM(qwen2.5:14b)이 답한다.
근거가 없으면 위험을 상향하지 않고 보류한다(환각 방지). 아래는 호출·해석 시 반드시 지켜야 할 규칙이다.

---

## 1. 코퍼스 구성 (2개 컬렉션)

| 컬렉션 | 내용 | origin | 규모 |
|--------|------|--------|------|
| `medical_law_knowledge` (본 코퍼스) | 법령 3단비교(법률·시행령·시행규칙) | `law3dan` | 1,151 |
| | KHIDI 고시(심사기준·신의료기술·선별급여 등) | `khidi_notice` | 5,301 |
| | 건강보험요양급여비용 상대가치점수(고시 제2025-186호) | `hira_valuepoint` | 3,141 |
| `medical_pub_knowledge` (보조) | HIRA/복지부 발간물 13종(편람·가이드맵·분류체계·사례집·**저가약 대체조제 청구요령** 등) | `hira_pub` | 15,285 |

- **별도 컬렉션 분리 이유**: 단일 ChromaDB(Rust) 컬렉션이 ~25K로 커지면 HNSW `link_lists.bin`이
  폭주 손상되어 디스크 풀·서버 다운이 발생한다(2026-06-11 인시던트). 발간물은 별도 컬렉션에 두어
  본 코퍼스 크기를 억제한다. **운영 규율: 큰 컬렉션에 대량 `col.delete`/대량 on-top `upsert` 금지**,
  불가피하면 link_lists/디스크 워치독 동반.
- `GET /api/medical-law/corpus-status` 가 `by_origin` + `aux_collections` 카운트를 보고한다(60s 캐시).

---

## 2. 🔑 근거 권위(authority) 위계 — 가장 중요한 호출 규칙

법적 효력 순서로 인용·해석한다. **발간물은 실무 참고자료이며 법적 구속력은 법령·고시가 우선**한다.

| 순위 | authority 라벨 | origin | 성격 |
|------|----------------|--------|------|
| 1 | `법령` | law3dan | 법률·시행령·시행규칙(최우선 근거) |
| 2 | `고시` | khidi_notice | 보건복지부·심평원 고시(심사기준 등) |
| 3 | `고시(상대가치)` | hira_valuepoint | 상대가치점수 고시(부속) |
| 4 | `발간물(참고)` | hira_pub | 편람·가이드맵·사례집·분류체계 = **실무 참고** |

- **병합 규칙(`_rag_law_search`)**: 본 코퍼스와 발간물을 각각 질의 → tier penalty(발간물 −0.06,
  상대가치 −0.02)로 동등 관련도에서 법적 근거 우선 → **관련 법령·고시 상위 2건은 인용 보장(floor)**.
  단 floor는 `min_score` 넘는 법적 근거가 있을 때만(예: POA 코딩처럼 법령 무관 실무주제는 발간물만 인용).
- **⚠️ 거리 공간 전제(v2.124.8)**: 위 게이트·penalty·floor는 전부 **절대 점수 = cosine 유사도** 전제다.
  본 컬렉션이 2026-07-16까지 L2 공간(score=2cos−1 압축)으로 생성돼 있어 법령이 구조적으로 발간물에
  밀리던 것을 cosine 재구축으로 정정(`scripts/migrate_medlaw_cosine.py`). `corpus-status`의
  `distance_space`가 cosine이 아니면 즉시 재구축하라(CRITICAL 로그+`space_warning` 발화).
  **새 컬렉션 생성 시 `hnsw:space: cosine` 지정 필수** — `client.rag.get_collection` 경유가 안전
  (get_or_create는 기존 컬렉션의 공간을 바꾸지 못하므로 생성 시점 실수가 영구화된다).
- **벌칙 역참조(v2.124.8, `_penalty_backref_chunks`)**: 벌칙·과태료·자격정지 조문은 조번호
  교차참조 목록이라 자연어 질의와 임베딩 유사도가 구조적으로 낮다(top-K 진입 실패 → 조문·형량 창작
  위험, eval id199 실증). 처벌 의도 질의(처벌/벌칙/벌금/징역/과태료/자격정지/행정처분/형량)면 검색된
  법령 조문의 조번호를 역참조하는 같은 법령의 벌칙류 조문을 근거 테일에 결정적 합류(최대 2건·floor
  불변). config `medlaw_penalty_backref`(기본 true). **검색 로직 변경 시 eval
  `get_evidence_chunks`에도 동시 적용**(생성/판정 근거 비대칭 규율).
- **행위 조문 보강(v2.124.9, `_act_context_chunks`)**: 벌칙 역참조의 **대칭 방향**. 처벌 의도
  질의는 "벌칙" 어휘 때문에 top-K가 벌칙류 청크로 채워져 **행위 조문 본문**(금지·의무 조항)이
  탈락한다 — 그러면 벌칙 조문의 위반 조항 목록(제N조제M항)을 항 단위로 대조할 재료가 없어 모델이
  파라메트릭 추측으로 오귀속(eval id240 실증: 무면허 벌칙을 제90조 500만원으로 — 정답 제87조의2
  5년/5천만). 벌칙 어휘를 제거한 질의로 본 코퍼스 보조 검색 → 벌칙류 아닌 상위 조문을 테일 합류
  (최대 2건·기존 결과 불변 append). config `medlaw_act_context`(기본 true).
- **벌칙 교차참조 주석(v2.124.9, `_penalty_xref_note`)**: /ask 컨텍스트에서 벌칙류 청크
  (`_is_penalty_chunk`: 표제 벌칙/과태료/자격정지 또는 본문 선두 패턴) 뒤에 그 청크 **전문**에서
  결정적 추출(regex·LLM 불요)한 위반 조항 토큰 목록을 주석으로 합류 — 캡에 잘린 목록도 매핑
  보존. **표제 병기는 조-only 토큰만**: 조 단위 표제를 항 붙은 토큰에 달면 항 구분이 뭉개져
  잘못된 매핑을 정당화한다(실측: "제27조제2항(무면허…)"이 오귀속 유도). 항 변별은 행위 조문
  본문(위 보강)이 담당. 시스템 규칙 8(벌칙 조·항·호 정확 일치)·9(단서의 제목 승격 금지)와 한 벌.
- **발간물 라벨 규칙(v2.124.8)**: 발간물(특히 법령집)의 `law_name` 메타는 **문서 제목**이지 법령명이
  아니다(예: 「국민건강보험법령집」에 마약류관리법 조문 수록). /ask 컨텍스트는 hira_pub 청크를
  `[발간물(종류) 「제목」 — 문서 제목이며 법령명 아님]`으로 라벨링하고, 시스템 규칙 7이 법령명
  추정·축약 인용을 금지한다(eval id200 오귀속 봉합).
- **응답 해석 규칙(호출자)**: citations의 `authority` 필드로 근거의 법적 위계를 판단하라.
  발간물만 인용된 경우 = 해당 주제에 직접 법령·고시 근거가 약함 → **실무 참고로만 활용**.
  벌칙 역참조·행위 조문 보강으로 합류한 인용은 `score`가 null이거나 확장 질의 기준 점수다
  (원 질의 임베딩 점수와 직접 비교 금지).
- ⚠️ **구현 주의(v2.124.9 실측 함정)**: `_rag_search`는 `_rag_law_search` **내부의 지역 import
  별칭**이다 — 모듈 전역이 아니므로 새 함수에서 그대로 참조하면 NameError가 나고, fail-safe
  try/except가 이를 삼켜 **무성 무효화**된다(기능이 조용히 죽음). 새 검색 보조 함수는
  `from client.rag import search`를 함수 안에서 직접 import하라.
- LLM 컨텍스트에 위계 지침이 주입되어 답변도 법령·고시를 우선 반영한다.

---

## 3. fee_codes(청구코드 → notice_type) 호출 규칙

> 코드: `core/fee_codes.py`, `POST /api/notice/fee-codes/import`·`GET /fee-codes/<code>`·`/stats`. 저장: `notice.db`.

- 현재 적재분 **29,036**: `TARIFF` 7,138(상대가치점수책 2025) + `DRUG` 21,898(**약가목록 「약제급여목록 및 급여상한금액표」 2026.6.1, 제품코드**).
  HIRA 자료 기반 부분집합으로, 약가/치료재료 추가 파일이 오면 같은 경로로 `category=DRUG`/`MATERIAL` 확장 적재한다.
- **정본은 ERP 코드마스터**(`GET /regulatory/fees/export.csv`, 약 441,511건). ERP는 **방식①(notice_type 직접 전달)**
  을 채택했고 weve fee_codes(방식②)는 보완·휴면. ERP가 `fee-codes/import`로 실적재하면 `INSERT OR REPLACE`로 갱신된다.
- category 매핑은 결정적: `TARIFF`→[`REVIEW_CRITERIA`,`TARIFF`], `DRUG`/`MATERIAL`→[`DRUG_MATERIAL`,`REVIEW_CRITERIA`].

---

## 3.5 저가약 대체조제 장려금 — drug_substitution 조회 규칙

> 코드: `core/drug_substitution.py`, `GET /api/notice/drug-substitution/<code>`·`/stats`. 저장: `notice.db`.

- 「저가약 대체조제 장려금 지급대상 의약품 목록」(2026.5.1, 10,979품목)을 **fee_codes와 별도** 전용 테이블에 적재.
  목록은 "장려금 대상 여부 + 대체 가능 동일성분 의약품"을 묻는 **membership 성격**이라 code→notice_type(fee_codes)과 분리한다.
- **eligible(장려금 대상)** = 목록 xlsx의 '단일품목 제외' 시트(대체 후보가 존재하는 품목) 멤버십 ∩ 급여정지 아님.
- **대체 후보(임상안전 규칙)**: `lookup(code)`은 **정확한 주성분코드 동일**(성분+함량+제형 동일) AND **대표코드 상이**
  AND eligible 인 의약품만 상한금액 오름차순으로 제시한다. 주성분코드 앞4+뒤3만 일치하는 광의 그룹(3mg/6mg 등 함량
  혼입)은 **쓰지 않는다** — 잘못된 대체 제안 방지. **최종 대체조제 판단은 ERP/약사**(본 API는 후보 제시·보조).
- 청구 방법(약가차액 30% 등)은 발간물 「저가약 대체조제 약제비 청구요령」으로 `medical_pub_knowledge`에 색인 → `/ask`로 자문.

---

## 4. 판본·시점(effective) 규칙

- 상대가치점수 = **고시 제2025-186호(2025년판)**. 구 2021판은 교체·제거됨.
- 발간물 = 2025/2026년판(약제기준 2025.7, 치료재료 2025.12, 의료급여편람 2026 등).
- 자문·삭감위험 평가는 **진료일 시행 여부**가 중요하다. 고시는 `effective_date` 메타를 보유하며
  `/api/notice/diagnose`는 `effective_on`으로 시행 중 기준만 적용한다. 발간물은 판본 기준 시점으로 해석하라.

---

## 5. 공통 규칙 (기존, 유지)

- **근거인용 강제 = 환각 방지**: 근거(citation)가 없으면 위험을 상향하지 않고 보류(`has_evidence=False`).
- **disclaimer 강제**: 모든 응답에 "법률 정보 제공이며 법적 조언이 아님" 고지. 구체 사안은 전문가 검토.
- **비식별**: 코퍼스는 공개 고시·발간물(환자정보 없음). 환자식별자 미전송(HIS/ERP 연동 규칙과 정합).
- **RAG 미가용 시 폴백**: ChromaDB/Ollama 장애 시 SQLite 시드 조문으로 폴백(회귀 0), 보조 컬렉션 실패는 격리.

---

## 6. 적재·유지보수 도구

| 도구 | 용도 |
|------|------|
| `scripts/ingest_hira_valuepoint.py` | 상대가치점수책 → fee_codes(TARIFF) + `medical_law_knowledge`(hira_valuepoint) |
| `scripts/ingest_hira_drug.py` | 약가목록 xlsx → fee_codes(DRUG). 순수 stdlib 파서(openpyxl 불요), RAG 미색인 |
| `scripts/ingest_drug_substitution.py` | 저가약 대체조제 목록 xlsx → notice.db `drug_substitution`(lookup) |
| `scripts/ingest_hira_pubs.py` | 발간물(폴더+MANIFEST) → 별도 컬렉션 `medical_pub_knowledge`(hira_pub) |
| `scripts/ingest_pub_file.py` | 단일 발간물 파일(PDF/HWPX/**구형 HWP**/TXT) → `medical_pub_knowledge`. 구형 .hwp는 olefile 순수 Python 추출 |
| `scripts/ingest_notice_docs.py` | KHIDI 수동 다운로드 고시 → `medical_law_knowledge`(khidi_notice) |
| `scripts/ingest_law_3dan.py` | 법령 3단비교 PDF → `medical_law_knowledge`(law3dan) |
| `core/medlaw_processor.py` | 증분 스케줄러(매일 05 UTC) — 신규 고시 수집·색인·dedup |

> **문서 파서**(`core/doc_parser.py`): PDF(pdfplumber)·HWPX(순수 XML)·**구형 바이너리 HWP 5.x(olefile, rhwp 불요)**·TXT.
> xlsx 코드표는 `core/hira_drug_parser.iter_xlsx_records`(헤더행 스캔+다중시트).

**⚠️ 안전 규율**(인시던트 재발 방지):
- 대량 색인은 **사전 임베딩(JSONL) → 배치 upsert + link_lists/디스크 워치독** 패턴으로(부하·손상 차단).
- 기존 큰 컬렉션에 대량 `col.delete`(dedup/purge 포함) 시 link_lists 폭주 주의 — **워치독 동반 권장**.
- 증분 스케줄러의 daily dedup/purge(`col.delete`)는 잠재 트리거 → link_lists 크기 모니터링 권장.
