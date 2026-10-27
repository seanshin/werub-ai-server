# 세션 보고서 2026-06-10c — 의료 RAG 백로그 종결 + store 통합 + FTS 격리 + MedGemma 1.5

**범위**: v2.88.0 → **v2.88.3** (릴리즈 3건 + 모델 도입 1건, 커밋 6건 c33214a~b349112, 전부 GPU 배포·E2E 검증)
**선행**: `SESSION_REPORT_20260610b.md`(v2.88.0 의료법 RAG 신규 구축)

## 1. v2.88.1 — 의료법 RAG 백로그 종결 (c33214a)

- **`POST /api/medical-law/ask/stream`**: /ask 동일 RAG를 NDJSON(meta→token→done) 스트리밍. 공용 조립 `_ask_assemble()` 분리(/ask 무변경). E2E: 제13조의3 근거 meta + 172토큰 + done(제21조 정확).
- **노이즈필터 강화**: `is_relevant_attachment`(검토의견/검토서·(붙임N_)서식/양식 시작 — `명세서서식` 오탐 0) + 증분수집 첨부 필터·제외건수 로그(§9-1) + `purge_noise()` 자가유지. **라이브 purge: 양식류 8파일/8청크 제거**(6,419→6,411).
- 회귀 test_medlaw 20→24.

## 2. v2.88.2 — 의료 지식 RAG 이중 store 통합 (7f8d466 + 4834b81)

- **근원(3중 분리)**: 색인=`_get_chroma`(chromadb_data·qwen3-embedding→_v2) vs 검색=client.rag(vector_db·bge-m3 **0건**) vs 실데이터 157건(구 bge-m3 컬렉션) → hybrid 항상 무히트·SQL 폴백 의존, 삭제는 SQLite만(고아 벡터).
- **통합**: 색인/삭제/테넌트purge 전부 client.rag(vector_db+FTS5) 단일화, `fts_delete` 신설, rowcount 가드 동반 삭제. reference 폴백 hybrid→SQL 2단. qwen3-embedding:8b 경로 제거(VRAM↓). 구 store 동결 보존(원본=medical.db).
- **백필**: `scripts/backfill_medical_kb.py` 156행 → 벡터+FTS. 도중 **embed_batch num_ctx 누락 발견·수정**(4834b81, v2.86.12 픽스의 빠진 경로 — 배치 임베딩 VRAM OOM 500).
- E2E: hybrid 실히트(I10)·라운드트립·purge·**reference 최초 실인용**(sources 5, [1] 정확).

## 3. 품질 검토 (code-analyzer, 변경분 대상)

- 종합 78/100. **H1**(FTS keyword 경로 테넌트 격리 부재 — 현행 데이터 전부 shared라 실노출 0) / M1(스트림 close) / M2·M3·L1~L4.
- GPU 전체 회귀 **491 passed / 7 skipped** 동시 확인.

## 4. MedGemma 1.5 4B 도입 (99aabb8, 34→35 Models)

- 신모델 웹검색(2026-06): 추천 = MedGemma1.5(의료)·Qwen3.6-35B-A3B(코딩, 후보 보류). 16GB 초과 제외(Kimi K2.6/GLM-5.1/Llama4/EXAONE Deep 등).
- `MedAIBase/MedGemma1.5:4b`(F16 7.8GB, google/medgemma-1.5-4b-it 2026-01) pull. **커뮤니티 빌드 Text-only 확인**(`ollama show` capabilities) → 비전 경로(영상판독)는 medgemma:4b 유지, **텍스트 의료 추가 등록**(clinical_qa/clinical_reasoning 2순위 + medical_records).
- 벤치(eval_model.py 의료 10케이스): **vs medgemma:4b = 89.7 : 80.0**(신모델 우위) / **vs med42 = 82.9 : 98.7**(med42 압승 → **1순위 med42 유지 확정**, 라우팅 무변경).
- 교훈: 커뮤니티 Ollama 변환은 capabilities 확인 필수(멀티모달 원본도 Text-only 변환 사례).

## 5. v2.88.3 — FTS 테넌트 격리(H1) + 커넥션 회수(M1) (b349112)

- **H1**: `docs_fts`에 **tenant 컬럼**+무중단 자동 마이그레이션(기존 189행='shared' 보존, 라이브 로그 확인). `fts_search/hybrid_search tenant=` SQL 단계 격리(미지정=무필터, 기존 호출 100% 호환), FTS-only 항목 metadata.tenant_id 동봉. medical.py 색인·검색 3곳 전달.
- **M1**: ask/stream `try/finally r.close()`. **L3**: /ask 캐시키 notice_category.
- **라이브 2테넌트 교차 E2E**: t2 keyword/hybrid 누출 **False**·소유자 hit True·테넌트 삭제 시 FTS 동반 제거. 회귀 test_fts_tenant 5(격리·레거시 마이그레이션·배치·hybrid 메타), GPU 16 passed.
- → HIS RAG 격리 확인서가 keyword 경로까지 완전 뒷받침.

## 운영 검증 (전 구간)

- 매 배포: push→GPU pull→import 드라이런→재시작→health 200·NRestarts=0. 라이브 점검: 큐 failed 0, GPU 43°C, 재시작 후 에러·경고 0, 스케줄러 3종 재기동.
- 인시던트: 벤치 SSH 끊김→이중 실행 경합 발견·정리(이후 nohup 분리 실행 표준화).

## 잔여 / 다음

- **다음 후보**: Qwen3.6-35B-A3B 도입(~20GB+코딩 벤치) / 스케줄러 실가동 관측(명일 12시·14시 KST — notice nightly·medlaw 증분+노이즈필터 첫 자동동작).
- **백로그**: code-analyzer M2(삭제 부분실패 재시도)·M3(정리기 풀스캔, 수만 청크 시)·L1·L4 / .hwp 수작업 3건 / 의료 일일한도 500 검토.
- **외부 대기**: ERP 실데이터·실삭감 라벨(캘리브레이션), 트윈측 신키 .env+predict 캐싱.
