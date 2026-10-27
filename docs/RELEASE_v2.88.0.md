# Release v2.88.0 — 의료법 RAG 자문 AI (신규)

> 2026-06-10 | 의료기관 의료법 자문 + 행위 이슈 체크 + 법령·KHIDI 고시 RAG 지속 보강

기존 `routes/medical_law.py`(정적 22조문 SQLite)에 **법령·고시 RAG 자동 학습 보강 레이어**를 얹어, 의료기관 자문 AI로 고도화. 기획·상세 = `docs/01-plan/features/medical-law-rag-augmentation.plan.md`(as-built 정본).

## 신규 기능

### P0 — 법령 RAG 자문 (`/api/medical-law/ask`)
- `core/law_pdf_parser.py`: 국가법령정보센터 **3단비교표 PDF**(법률/시행령/시행규칙) 컬럼 인식 파서.
- 법령 5종(의료법·약사법·의료급여법·국민건강보험법·의료기기유통규칙) = **1,151 조문** 색인.
- `/ask` RAG 업그레이드: SQLite LIKE → `medical_law_knowledge` 벡터검색 + 시드 병합 + **근거인용(citations)**. RAG 미가용/0건 시 기존 SQLite 자문 폴백(회귀 0).

### P0.5 — 행위 이슈 체크 (`/api/medical-law/review`, 신규)
- 의료기관 행위(비식별 서술) → RAG → **위험밴드(HIGH≥0.70/MEDIUM/LOW) + 근거인용**.
- **환각 방지**: 근거 없으면 위험 상향 금지·보류. 모든 응답 disclaimer.
- QA 검증: 정상 외래 진찰→LOW(오탐0) / 전공의 단독 마취→HIGH(제35조) / 간호사 무처방 투약→HIGH(제27조 무면허) / 동의없는 기록 제3자제공→HIGH(제21조).

### P1 — KHIDI 고시 직접 수집 + 증분 스케줄러
- `core/khidi_collector.py`(게시판 수집, robots 준수·정중 1.5s)·`doc_parser.py`(PDF=pdfplumber, **HWPX=순수 Python**, 구형 .hwp=rhwp 어댑터 휴면)·`medlaw_processor.py`(증분 스케줄러).
- KHIDI 고시 **173건 색인 → 버전 dedup → 5,268 청크**(같은 규정 다버전 중 전문 보존).
- **증분 스케줄러 LIVE**(daily 05 UTC): 신규 고시 자동 수집·색인 + **버전 dedup 내장(자가유지, 재블로트 방지)**.
- 구형 .hwp 실고시는 `_needs_manual.tsv` manifest 산출 → 운영자 수작업 PDF 전환.

### O5 — 고시 category 분류·필터
- `classify_notice_category`: 키워드 분류(신의료기술/선별급여/의료기기/급여기준/심사기준/infection/record/광고/기타).
- 5,268 청크 백필 + 신규 색인 자동 부여 + `/ask`·`/review` `notice_category` 필터 + citations 노출.

### 운영/관측
- `GET /api/medical-law/corpus-status`(origin/category 분포·watermark, 60s TTL 캐시) + **Admin 콘솔 카드**.
- 스크립트: `ingest_law_3dan.py`·`scrape_khidi.py`·`ingest_notice_docs.py`·`dedup_medlaw.py`·`backfill_notice_category.py`.

## 코퍼스
`medical_law_knowledge` ChromaDB = **6,419 청크** (법령 1,151 + 고시 5,268). category 분포: 의료기기 1527·신의료기술 1570·선별급여 882·급여기준 583·심사기준 28·기타 678.

## 버그 fix / 하드닝 (개발 중 발견)
- **파일명 충돌 80% 유실**: 긴 첨부명 절단 시 다른 고시 덮어씀 → `titleId-fileId` 고유 접두.
- **no1 phantom 행번호 불안정**: pageNum 반복(보드 실제 ~180 고유 고시, "1,574"는 오독) → 증분 dedup을 source_file 기준으로.
- 보안(code-analyzer): HWPX zip bomb 상한(100MB)·다운로드 상한(80MB)·파일핸들 누수·state 원자적 쓰기.

## 품질
- 회귀 `tests/test_medlaw.py` 20건 + 기존 의료 30건.
- 게이트: gap-detector·design-validator·code-analyzer·실사용 QA 통과.

## 의존성
- `pdfplumber==0.11.9` 추가.

## 외부(트윈/HIS) 대응
- RAG filter(owner/ns) 동작 재검증·401 키 로테이션 진단·predict 레이트리밋(②캐싱 권장 + cap 20,000 상향). `docs/01-plan/references/rag-filter-reply-to-twin.md`.

## 백로그(비차단)
- 노이즈 필터 강화 · /ask/stream · .hwp 실고시 3건 수작업 변환.
