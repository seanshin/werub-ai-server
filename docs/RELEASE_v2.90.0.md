# Release v2.90.0 — HIRA 코퍼스 확장 + 근거 권위 위계 + col.delete 가드

**날짜**: 2026-06-11
**이전**: v2.89.0 (환자 아바타 R1/R2)

심평원(HIRA) 공식 발간물을 의료법/사전심사 RAG에 대거 적재하고, 그 과정에서 드러난
ChromaDB 손상을 복구하며 멀티컬렉션 검색·근거 권위 위계·삭제 가드를 도입했다.

---

## 1. HIRA 상대가치점수책 적재 (fee_codes + 코퍼스)

- **「건강보험요양급여비용」 2025판(고시 제2025-186호, 1,351p)** → 한 파싱으로 두 산출물:
  - **fee_codes 7,138건**(전부 `TARIFF` → `[REVIEW_CRITERIA, TARIFF]`) — ERP 청구코드→notice_type 자동해석(방식②). `/api/notice/search`·`/diagnose` 실데이터 동작.
  - **`medical_law_knowledge`에 hira_valuepoint 3,141청크** — 산정지침(주:)·별첨3 신의료기술 안전성·유효성 평가결과.
- 신규: `core/hira_fee_parser.py`(다컬럼 표 파서 — 분류번호|코드|분류|점수 4컬럼, 별첨3 3컬럼 자동검출, 행 클러스터+컬럼분배, 서브청킹), `scripts/ingest_hira_valuepoint.py`(멱등).
- 구 2021판은 교체·제거. ⚠️ fee_codes는 ERP 정본(export.csv 441k)의 부분집합·임시(방식① 우선).

## 2. HIRA/복지부 발간물 12종 → 별도 컬렉션 `medical_pub_knowledge`

- 약제 적용기준·청구 작성요령·치료재료 가이드맵·질병군(DRG)·자동차보험수가·의료급여 실무편람·
  입원 시 상병 코딩지침(POA)·KDRG/KOPG 환자분류체계·민원편람·의약품 청구가이드·국민건강보험법령집
  → **15,275청크**(`scripts/ingest_hira_pubs.py`, origin=hira_pub).
- **별도 컬렉션 분리**: 단일 ChromaDB 컬렉션이 ~25K로 커지면 HNSW `link_lists.bin`이 폭주 손상되어
  디스크 풀·서버 다운을 유발(아래 §6 인시던트). 신규 별도 컬렉션은 15K도 안전 확인.

## 3. 멀티컬렉션 RAG 검색

- `routes/medical_law.py` `_rag_law_search`: 본 코퍼스(`medical_law_knowledge`) + 발간물
  (`MEDLAW_AUX_COLLECTIONS`) 함께 질의 → score 병합 → top_k. 보조 컬렉션 실패는 격리.
- `/corpus-status`에 `aux_collections` 카운트 노출. citations에 `doc_type`.

## 4. 🔑 근거 권위(authority) 위계 — 핵심 호출 규칙

- 발간물(15K)이 법령(1.1K)보다 커서 순수 score 정렬 시 **권위역전**(법적 질의에 citations 전부
  편람, 법령 0건) 발생 → 보정:
  - 위계 **법령 > 고시 > 상대가치고시 > 발간물(참고)**. `_AUTHORITY`(라벨+tier penalty).
  - tier penalty(발간물 −0.06)로 동등 관련도에서 법적 근거 우선 + **관련 법령·고시 상위 2건 floor 보장**
    (단 min_score 넘을 때만 → 순수 실무주제 POA는 발간물만 유지).
  - citations에 `authority` 라벨, LLM 컨텍스트에 위계 지침 주입. `/ask`·`/review` 일괄.
- 검증: "의료급여 본인부담"→법령+고시 우선 / "POA 코딩"→발간물(법령 무관).

## 5. medlaw 스케줄러 col.delete 대량 삭제 가드

- 매일 스케줄러의 `dedup_versions`/`purge_noise`(col.delete)가 HNSW 폭주 트리거(§6) → 가드:
  - `_MAX_SAFE_DELETE_CHUNKS`(기본 500, env `MEDLAW_MAX_SAFE_DELETE`) 초과 시 **skip + CRITICAL 로그**
    (수동 dedup 유도). 일일 증분(소량)은 정상 통과, 대량/누적만 차단. dedup은 2패스(계획→가드→실행).
  - `_link_lists_warn()`: 삭제 후 vector_db 최대 link_lists.bin 점검, 임계 초과 시 CRITICAL(조기감지).

## 6. 운영 — ChromaDB link_lists.bin 폭주 인시던트 복구

- 적재 중 `link_lists.bin`이 137G/1.3TB로 폭주 손상→디스크 100%→서버 다운(2회). 원인 = 대량
  col.delete·on-top 대량 upsert가 ChromaDB(Rust) HNSW compaction을 폭주시킴.
- 복구: 로컬 백업(chroma.sqlite3) → `truncate` 137G 회수 → 재기동 WAL 자동 재구축 → 컬렉션 WAL 손상 시
  `delete_collection` + 로컬백업 추출 재임베딩으로 **9,593 무손실 복구**. 워치독으로 2차 폭주 차단.
- 디스크 정리 ~25G(pip 캐시 + HF 파인튜닝 잔재 19G). **Ollama 35모델 fleet은 전부 라이브 배선
  (코딩/의료/비전/agent 라우팅)이라 보존** — 삭제는 라우팅 리팩터 동반 시에만.

## 7. 문서·테스트

- **`docs/MEDICAL_LAW_RAG_GUIDE.md`** 신설 — 호출자/유지보수자용 규칙 단일 소스(코퍼스 구성·권위 위계·
  fee_codes ERP정본 관계·판본/시점·공통규칙·적재도구+안전규율). CLAUDE.md 포인터.
- 회귀: `test_hira_fee_parser` 13(순수 단위) + `test_medlaw_delete_guard` 5(GPU 29 passed).

---

## 검증
- VERSION 2.90.0, health 200, NRestarts 0, 디스크 162G 여유.
- 코퍼스: `medical_law_knowledge` 9,593(law3dan 1,151+khidi 5,301+hira_valuepoint 3,141) +
  `medical_pub_knowledge` 15,275. fee_codes 7,138.
- 멀티컬렉션 검색·권위 위계 라이브 검증, 재기동 안정(link_lists 0).

## 잔존 백로그
- Ollama fleet 회수는 도메인 라우팅 단순화 리팩터 필요(보존 권장). GPU 디스크 baseline 80%.
- medlaw col.delete는 가드로 1차 완화(systemd 워치독 상시화는 선택). ChromaDB 업그레이드 미실시(우회 충분).
