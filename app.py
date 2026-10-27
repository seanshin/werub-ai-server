__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

#!/usr/bin/env python3
"""Ollama Web Admin - lightweight management UI + batch queue for Ollama."""

VERSION = "2.125.73"

# ---------------------------------------------------------------------------
# Batch Mode (v2.70.0)
# ---------------------------------------------------------------------------
# 환경변수 BATCH_MODE=1 또는 config.json {"batch_mode": true} 로 활성화.
# iMac 등 CPU 서버에서 배치 전용으로 운영 시 사용.
# - 실시간 API (의료/상담/오케스트레이터 등) 비활성화
# - 큐/배치/모델관리/한국어/글쓰기 API만 활성화
# - 워커 스레드 최소화, 타임아웃 연장
import os as _os
BATCH_MODE = _os.environ.get("BATCH_MODE", "").strip() in ("1", "true", "yes")


CHANGELOG = [
    {
        "version": "2.124.14",
        "date": "2026-07-19",
        "title": "v2.91.0~2.124.14 상세는 docs/RELEASE_v2.*.md 정본 참조 (app.py CHANGELOG는 v2.90.0 이후 포인터 유지)",
        "changes": [
            "app.py VERSION 상수는 현재 버전으로 정합(과거 v2.90.0 동결 → /api/version 미스리딩 제거). v2.91+ 릴리즈 상세는 docs/RELEASE_v2.*.md + CLAUDE.md 헤더가 정본",
            "최근: v2.124.9 의료법 생성 정밀도 규칙 8·9(벌칙 항 정합·제목-본문 정합)+judge 위양성 규율 · v2.124.8 거리 공간 정정(L2→cosine)+벌칙 역참조 · v2.124.7 결정적 질의 확장 · v2.124.6 청크 캡 1500 · v2.124.5 서식 오염 필터 · v2.124.0~3 검색 품질 축·지수·모니터",
        ],
    },
    {
        "version": "2.90.0",
        "date": "2026-06-11",
        "title": "HIRA 코퍼스 확장(상대가치 2025+발간물 별도컬렉션) + 근거 권위 위계 + col.delete 가드",
        "changes": [
            "HIRA 「건강보험요양급여비용」 상대가치점수책 2025판(고시 제2025-186호) 적재: fee_codes 7,138건(TARIFF→REVIEW_CRITERIA,TARIFF, ERP code→notice_type 방식②) + medical_law_knowledge에 hira_valuepoint 3,141청크(산정지침·별첨3 신의료기술). core/hira_fee_parser.py(다컬럼 표 파서)+scripts/ingest_hira_valuepoint.py",
            "HIRA/복지부 발간물 12종(약제기준·청구작성요령·치료재료·질병군·자보·의료급여편람·코딩지침·KDRG/KOPG분류체계·민원편람·의약품청구·법령집) → 별도 컬렉션 medical_pub_knowledge 15,275청크(scripts/ingest_hira_pubs.py, origin=hira_pub)",
            "발간물 별도 컬렉션 분리 이유 = 단일 ChromaDB 컬렉션 ~25K 시 HNSW link_lists.bin 폭주 손상(디스크풀·서버다운) 회피. 신규 컬렉션 15K는 안전 확인",
            "멀티컬렉션 RAG 검색(routes/medical_law.py _rag_law_search): 본 코퍼스+발간물 함께 질의→score 병합, 보조 컬렉션 실패 격리. corpus-status에 aux_collections",
            "🔑 근거 권위 위계: 법령>고시>상대가치>발간물(_AUTHORITY tier penalty + 관련 법령·고시 상위 2건 floor 보장) — 발간물(15K)이 법령(1.1K)을 밀어내던 권위역전 방지. citations에 authority 라벨, LLM 컨텍스트에 위계 지침",
            "medlaw 스케줄러 col.delete 대량 삭제 가드(_MAX_SAFE_DELETE_CHUNKS=500): dedup_versions/purge_noise가 임계 초과 시 skip+CRITICAL(자율 폭주 방지), _link_lists_warn() 폭주 조기감지",
            "호출 규칙 정식 문서 docs/MEDICAL_LAW_RAG_GUIDE.md(권위 위계·fee_codes ERP정본 관계·판본/시점·안전규율) + CLAUDE.md 포인터",
            "운영: ChromaDB link_lists.bin 폭주 인시던트 복구(truncate+재기동 WAL 재구축, delete_collection+로컬백업 추출 재임베딩으로 9,593 무손실 복구). 디스크 정리 ~25G(pip+HF 파인튜닝 잔재)",
            "회귀 test_hira_fee_parser 13 + test_medlaw_delete_guard 5",
        ],
    },
    {
        "version": "2.89.0",
        "date": "2026-06-11",
        "title": "환자 아바타 R1 — werub-physio-v1 0D 다계통 생리 시뮬 + 계통간 커플링 + L4",
        "changes": [
            "R1 생리모델: POST /api/medical/physiology(+/stream NDJSON) — core/physiology.py 순수 numpy·CPU(GPU/LLM 무경합)·비식별·미저장·결정적. 트윈 0D(Windkessel/renal_pk) 격상",
            "계통간 커플링 2개(트윈 0D엔 없는 차별점): ①egfr_pk_cardio 신배설 심혈관약→eGFR 축적비→MAP/HR(단조구배) ②glucose 경구혈당강하제→onset 1차동역학",
            "series 8키(hr/map/sbp/dbp/rr/spo2/glucose_mgdl/cardiac_output_lmin)+drug_response[]+coupling[]. 체중·키 HIS미노출→성/연령 표준추정 폴백. 비식별 강제(name/mrn 입력 400)",
            "약물명 정규화 _normalize_drug — 한글 일반명 17+브랜드 11→INN, 용량/제형/괄호 토큰 제거(트윈 한국 HIS 실데이터 플래그 봉합), matched_from 원문보존",
            "R2 영상→메시 PoC(AI측 실증): TotalSegmentator VRAM 정책(fast 안전·full OOM임박→온디맨드 언로드) + 마스크→marching cubes→glTF .glb 생성 E2E(격리 venv·운영 무영향). 트윈 공개 CT 대기",
            "L4: medical-law top_k 입력 안전 클램프 — 비정수/음수 입력 500→무예외 default(_safe_top_k [1,10], /ask·/stream·/review 3경로)",
            "회귀 test_physiology 10 + test_medlaw +1",
        ],
    },
    {
        "version": "2.88.3",
        "date": "2026-06-10",
        "title": "FTS 키워드 검색 테넌트 격리(H1) + ask/stream 커넥션 회수(M1) — code-analyzer 후속",
        "changes": [
            "H1: docs_fts에 tenant 컬럼 신설(무중단 자동 마이그레이션, 기존 행='shared') — fts_search/hybrid_search tenant 필터로 mode=keyword·hybrid FTS-only 경로의 타 테넌트 비공개 노출 차단(현행 데이터는 전부 shared라 실노출 0이었음, 선제 봉합)",
            "hybrid FTS-only 항목 metadata에 tenant_id 동봉 — _tenant_kb_filter 후처리 일관 판정",
            "M1: /api/medical-law/ask/stream Ollama 스트림 try/finally r.close() — 클라 조기종료 시 커넥션 풀 회수 보장",
            "L3: /ask 캐시 키에 notice_category 포함(필터 변경 시 구 캐시 반환 방지)",
            "회귀 tests/test_fts_tenant.py 5건(격리·레거시 마이그레이션·배치·hybrid 메타)",
        ],
    },
    {
        "version": "2.88.2",
        "date": "2026-06-10",
        "title": "의료 지식 RAG 이중 store 통합 — 색인·검색 client.rag 단일화 + 백필",
        "changes": [
            "근원: 색인=_get_chroma(chromadb_data, qwen3-embedding) vs 검색=client.rag(vector_db, bge-m3) 분리 → 검색측 0건이라 hybrid 무히트·SQL 폴백 의존이던 구조 해소",
            "색인/삭제/테넌트purge 전부 client.rag(vector_db 벡터+FTS5)로 단일화, 삭제 시 벡터·FTS 동반 정리(고아 벡터 수정), fts_delete 신설(client/rag.py)",
            "reference 폴백 체인 hybrid→SQL 단순화, 구 qwen3-embedding:8b 경로 제거(VRAM 부담 제거, bge-m3 num_ctx=2048 단일)",
            "백필 scripts/backfill_medical_kb.py: medical.db 156행→vector_db+FTS 멱등 재색인. chromadb_data 구 store는 동결 보존",
        ],
    },
    {
        "version": "2.88.1",
        "date": "2026-06-10",
        "title": "의료법 RAG 백로그 — /ask/stream NDJSON 스트리밍 + 노이즈필터 강화(첨부 양식류)",
        "changes": [
            "POST /api/medical-law/ask/stream: /ask와 동일 RAG(법령+고시 근거인용)를 NDJSON 스트리밍(meta→token→done)으로 제공, 이력 저장 동일",
            "노이즈필터 강화: 첨부파일명 단위 양식류 판정 is_relevant_attachment(검토의견서/검토서·(붙임N_)서식/양식 시작) — 게시물 제목 정상+첨부 양식 유입(라이브 9건) 차단",
            "purge_noise(): 기색인 양식류 청크 제거(1회 정리+증분 자가유지), 제외 건수 로그(silent drop 금지 §9-1)",
            "회귀 test_medlaw 20→24건",
        ],
    },
    {
        "version": "2.88.0",
        "date": "2026-06-10",
        "title": "의료법 RAG 자문 — 법령·KHIDI 고시 색인 + /ask RAG + /review 행위체크 + 증분 스케줄러",
        "changes": [
            "P0 법령 RAG 자문: core/law_pdf_parser.py(국가법령정보센터 3단비교 PDF 컬럼파서) + 법령 5종 1,151조문 색인 → /api/medical-law/ask RAG 업그레이드(SQLite LIKE→medical_law_knowledge 벡터검색+시드병합+근거인용, RAG 미가용 폴백)",
            "P0.5 행위 이슈 체크: /api/medical-law/review — 의료기관 행위→RAG→위험밴드(HIGH≥0.70/MED/LOW)+근거인용 강제(근거없으면 위험상향금지=환각방지). QA: 정상→LOW·위반→HIGH(제27/35/21조 정확)",
            "P1 KHIDI 고시 직접수집: core/{khidi_collector,doc_parser,medlaw_processor}.py — 게시판 수집(정중 1.5s)+PDF(pdfplumber)/HWPX(순수Python, rhwp불요)+증분 스케줄러(daily 5 UTC, source_file dedup, 버전dedup 내장=자가유지). 고시 173건→5,268청크",
            "O5 고시 category 분류·필터: classify_notice_category(신의료기술/선별급여/의료기기/급여기준/심사기준 등) + /ask·/review notice_category 필터 + citations 노출",
            "운영: /api/medical-law/corpus-status(origin/category 분포·60s캐시) + Admin 콘솔 카드. 구형 .hwp는 _needs_manual.tsv manifest→수작업 PDF 전환 유도",
            "버그 fix: 파일명 충돌 80% 유실(titleId-fileId 고유접두)·no1 phantom 행번호 불안정(source_file dedup). 보안 하드닝(HWPX zip bomb·다운로드 상한·원자적 state). 회귀 test_medlaw 20건",
            "코퍼스 medical_law_knowledge 6,419(법령1,151+고시5,268, 버전dedup). 트윈 RAG filter/401키/predict 레이트리밋 대응",
        ],
    },
    {
        "version": "2.87.1",
        "date": "2026-06-10",
        "title": "ERP 사전심사 후속 — 연동이슈 fix·위험밴드 임계 config화·회귀테스트 41건",
        "changes": [
            "ERP 연동이슈 fix: notice revoke/delete/index-status가 erp_ref_id 인식(_resolve_notice_id 최신 revision 폴백, 과거 404), GET /api/notice/index-status 컬렉션 라우트 신설(?client_request_id/?erp_ref_id, 과거 405). 경로형도 erp_ref_id 허용",
            "diagnose 위험밴드 임계 config화: config notice_diagnose_thresholds{high,medium}(기본 0.70/0.40)로 무재배포 캘리브레이션(ERP P0.5 목표 precision≥0.8). 유효성(0≤medium<high≤1) 폴백, 응답 risk_thresholds 노출",
            "(c) content_hash 정합 종결: ERP 3벡터(일반·표·전각 CRLF+BOM)를 weve content_hash로 대조 → 전부 MATCH(전각 보존=NFC, 바이트 일치). 정규화 7항 양측 정합 확정",
            "데모 공시 9건 purge(실고시 2건만 잔존: 제2026-125·118호), 실데이터 검색·평가 E2E 정상(치료재료 상한초과 HIGH 1.0 근거인용)",
            "회귀테스트 41건 추가: test_fee_codes(8)·test_notice_v287(21: 헬퍼+임계config+리졸버+index-status+fee-codes)·test_rag_filter(7)·test_medical_isolation(4). 전체 460+ passed",
        ],
    },
    {
        "version": "2.87.0",
        "date": "2026-06-10",
        "title": "ERP 사전심사(공시 검색/삭감위험 평가) + HIS PHI 비저장/RAG 격리 + 키 로테이션",
        "changes": [
            "ERP 사전심사 색인 처리기(erp-notice-rag Day3): core/notice_processor.py — pending notice_index_jobs를 chunk→bge-m3 임베딩→notice_knowledge 색인. immediate 백그라운드 + nightly 스케줄러(notice_nightly_hour 기본3시 UTC) + 수동 POST /api/notice/process-jobs + GET /api/notice/<id>/index-status",
            "P0 검색: POST /api/notice/search — active_only·notice_type·effective_on(진료일 시행여부) 필터. P0.5 평가: POST /api/notice/diagnose — 시행 중 심사기준 RAG→qwen2.5:14b 구조화 평가, 위험밴드 HIGH≥0.70/MED0.40~0.69/LOW<0.40, 근거인용 강제(has_evidence)",
            "청구코드→notice_type 자동해석(방식②): core/fee_codes.py + POST /api/notice/fee-codes/import(rows/csv)·stats·<code>. 수가→REVIEW_CRITERIA/TARIFF, 약가·치료재료→DRUG_MATERIAL/REVIEW_CRITERIA. search/diagnose code 자동해석",
            "HIS PHI 비저장 모드: medical_phi_store 토글(기본 False)→_save_consultation 미실행·감사요약 미보존, /api/chat store opt-out(store=false/X-No-Store). RAG 지식베이스 테넌트 격리(_kb_tenant_ok post-filter 5경로). 확인서 docs/01-plan/references/his-phi-nonstorage-confirmation.md",
            "rag/search·ask filter 파라미터 실동작: _build_where로 ChromaDB where 변환(owner/ns 메타 격리, 조직→개인단위)",
            "HIS 키 로테이션: 신키 병행 발급→구키 폐기(무중단). medical.db medical_consultations 손상 비파괴 재구축·복구(백업 보존)",
            "문서: docs/01-plan/references/{ai-prescreening-reply-to-erp, his-integration-reply, his-phi-nonstorage-confirmation}.md, docs/SESSION_REPORT_20260610.md",
        ],
    },
    {
        "version": "2.86.13",
        "date": "2026-06-09",
        "title": "sqlite connect timeout 일관성 (10곳) + 서버 백업파일 정리",
        "changes": [
            "DB 하드닝: timeout 미명시 sqlite3.connect 10곳에 timeout=10 추가(app.py 2 + story/novel/meeting/training3/meeting_pipeline/hr_law). 기본 5s→10s 명시·일관화(fork+shm 픽스의 belt-suspenders 방어)",
            "운영 정리: 서버 ollama env 백업파일(.bak-20260609) 삭제, OLLAMA_NUM_PARALLEL=4 정합 확인",
            "graceful_shutdown의 threading._shutdown 노이즈는 sys.exit(0) 시 데몬 스레드 teardown 본질이라 won't-fix(무해, SSE drain 로직 위험 회피)",
        ],
    },
    {
        "version": "2.86.12",
        "date": "2026-06-09",
        "title": "RAG 임베딩 500 수정 — bge-m3 VRAM OOM (글로벌 ctx=8192가 임베딩 KV 폭주)",
        "changes": [
            "근원: /api/rag/ask·search → embed_text → Ollama /api/embed가 글로벌 OLLAMA_CONTEXT_LENGTH=8192×NUM_PARALLEL로 bge-m3 KV를 ~4.5GB 잡음 → 14b(12.2GB) 상주 중 free 3.6GB에 못 들어가 cudaMalloc OOM → 500(여전히 발생, v2.86.7 튜닝 부작용)",
            "fix(client/rag.py): embed_text가 options.num_ctx=2048(RAG_EMBED_NUM_CTX) 명시 → bge-m3 ~1.2GB로 축소, 14b와 공존. 라이브 검증: embed 200·1024차원, rag/ask 정상",
            "방어(routes/rag.py): rag_ask/rag_search_api의 rag_search 호출을 try/except로 — 임베딩 실패 시 raw 500 대신 503+명확한 메시지",
            "medical/reference는 임베딩 비사용(직접 생성)이라 영향 없었음. 서버 OLLAMA_NUM_PARALLEL은 4 유지(임베딩 KV가 진짜 원인이라 LLM 튜닝 무관)",
        ],
    },
    {
        "version": "2.86.11",
        "date": "2026-06-09",
        "title": "문서/API명세 동기화 + L3 마무리 (admin_users_update 비번 길이검증)",
        "changes": [
            "L3 완성: admin_users_update(PUT) 비번 변경 경로에 누락됐던 8자 검증 추가(create/change-password와 통일)",
            "API 명세(flasgger): admin_users_create/update 스키마에 password.minLength=8 + role enum + required 명시 → /openapi.json 자동 반영",
            "문서: CLAUDE.md/README 버전·인증보안 항목 갱신, docs/RELEASE_v2.86.11.md(v2.86.8~11 통합 노트)",
        ],
    },
    {
        "version": "2.86.10",
        "date": "2026-06-09",
        "title": "보안 잔여 정리 — 인증 하드닝 4건(L1/L2/L3/M1)",
        "changes": [
            "L2: _verify_web_login에서 user 미존재/비활성 시 dummy_verify로 동일 비용 KDF 1회 소비 → 응답시간 차 기반 username 열거 방지",
            "L1: 비번변경 시 config admin_password_hash 기록 제거(write-only 잔재 — 인증은 users.db만 read). 불필요한 해시 노출면 제거",
            "L3: 사용자 생성 비번 최소길이 4→8자(비번변경과 통일)",
            "M1: core/password.py pbkdf2 encode('utf-8') 명시. surrogate 등 인코딩 불가 입력은 verify에서 무예외 False(테스트 보강)",
            "회귀 tests/test_password.py 6→8건(dummy_verify 무예외·surrogate 처리). code-analyzer 재검증 잔여 종결",
        ],
    },
    {
        "version": "2.86.9",
        "date": "2026-06-09",
        "title": "보안 후속 — 로그인 post-auth UPDATE를 best-effort로 분리 (동시 로그인 false-negative 제거)",
        "changes": [
            "fix(M2/M3): _verify_web_login에서 비번 검증 성공 판정과 last_login/lazy 재해싱 UPDATE를 분리된 try/except로. 기존엔 같은 except: return False 블록이라 동시 로그인 SQLite BUSY 시 UPDATE 실패가 인증 실패(false-negative)로 오염됨 — code-analyzer 재검증 지적",
            "이제 verify 통과 후 UPDATE 실패는 warning 로그만 남기고 인증 True 유지(비번 불변·데이터 손상 0). v2.86.8 인증 변경의 동시성 견고화",
            "잔여(저우선 백로그): L2 user 미존재 타이밍 균등화(열거 방지), L3 비번 최소길이 4↔8 통일, L1 config admin_password_hash write-only 잔재 정리, M1 hash_password surrogate 입력 방어",
        ],
    },
    {
        "version": "2.86.8",
        "date": "2026-06-09",
        "title": "보안 — 비밀번호 per-user salt 전환 (static salt 제거, 무중단 마이그레이션)",
        "changes": [
            "fix: 비밀번호 해싱 static salt(b'werub-ollama-salt') 하드코딩 5곳 제거 → core/password.py 단일 소스(per-user 랜덤 16B salt, pbkdf2_sha256$200000$salt$hash 자기기술 포맷)",
            "무중단 마이그레이션: 레거시 bare-hex(static salt) 해시는 verify가 자동 폴백 검증, 로그인 성공 시 needs_rehash면 새 salt로 재해싱 UPDATE(강제 리셋·잠금 0)",
            "배선: app.py(_hash_password/_verify_web_login/비번변경)+routes/training.py(admin_users_create/update). 회귀 tests/test_password.py 6건",
            "코드품질: code-analyzer 잔여 Medium(static salt) 종결. KDF는 기존대로 pbkdf2-hmac-sha256 200k 유지",
        ],
    },
    {
        "version": "2.86.7",
        "date": "2026-06-08",
        "title": "GPU Ollama 성능 튜닝 — parallel=4 + ctx=8192 + 스트리밍 (실측 기반)",
        "changes": [
            "처리량: OLLAMA_NUM_PARALLEL 2→4 (동시 4명 무대기). drop-in env(/etc/systemd/system/ollama.service.d/environment.conf — /etc/default/ollama를 shadow하므로 여기 수정+daemon-reload)",
            "품질: OLLAMA_CONTEXT_LENGTH 8192 (긴 의료맥락). flash_attention+kv q8 유지",
            "실측: 14b 89 tok/s(문서 '15'는 오기), GPU 메모리대역폭 바운드, parallel4+ctx8192 VRAM 12.2GB/16GB(OOM 없음), chat 동시 6/6",
            "⭐ 체감: 스트리밍 TTFT 0.14s vs 비스트림 11s(80배) → 호출측 stream:true 권장(가이드 docs/RELEASE_v2.86.7)",
            "튜닝값 기록: docs/RELEASE_v2.86.7.md (server config는 git 밖이라 문서로 보존)",
        ],
    },
    {
        "version": "2.86.6",
        "date": "2026-06-08",
        "title": "채팅 행 진짜 근원 — preload_app=False (fork+SQLite -shm 상속)",
        "changes": [
            "fix: gunicorn preload_app=True + fork + SQLite -shm(MAP_SHARED) 상속으로 워커 connect 데드락(/api/chat 60s행). preload_app=False(gunicorn.conf.py) — workers=1이라 이득 없음",
            "진단 결정타: lsof로 마스터+워커 동시 queue.db-shm 점유 확인(격리 connect는 0ms·코드 롤백 무효·race라 간헐). v2.86.1~5(busy_timeout/WAL/max-age/connect락)는 헛짚음, busy_timeout·wal_checkpoint는 방어로 유지",
            "회귀 테스트: tests/test_db_concurrency.py (preload_app=False 가드 + get_db 동시성). 전체 419 passed",
        ],
    },
    {
        "version": "2.86.4",
        "date": "2026-06-08",
        "title": "채팅 행 진짜 근원 — sqlite3.connect 직렬화 (WAL -shm 경합)",
        "changes": [
            "fix: py-spy로 _new_conn의 sqlite3.connect(queue.db) 자체가 wedge 확인 — 재시작 직후 gthread 96개 동시 connect가 WAL -shm(wal-index) 락에 경합. core/db.py connect 직렬화 락(_conn_lock) → 폭주 제거",
            "revert: v2.86.3 커넥션 max-age 재생성 제거(connect 빈도↑로 경합 악화). 영속 thread-local 유지(스레드당 1회 connect)",
            "유지: get_db 재시도 횟수 제한(무한재귀 방지), v2.86.2 주기 wal_checkpoint, WAL>10MB 경보",
            "배포 시 stale queue.db-wal/-shm 제거(WAL=0 확인 후) 후 기동",
        ],
    },
    {
        "version": "2.86.3",
        "date": "2026-06-08",
        "title": "DB 레이어 하드닝 — get_db 재시도 제한 + 커넥션 max-age 재생성 + WAL 모니터",
        "changes": [
            "get_db: 무한 recursive retry → 횟수 제한(MAX_RETRIES=3) fail-fast (DB 영구오류 시 wedge 방지, py-spy서 재귀 확인)",
            "get_db: 영속 thread-local 커넥션 max-age 재생성(300s) — WAL read-mark 누적 해제로 체크포인트 starvation 근본 완화(같은 스레드 close, 안전)",
            "_wal_checkpoint: queue.db-wal 10MB 초과 시 경고 로그 — WAL 비대 재발 조기경보",
            "v2.86.2(주기 wal_checkpoint)와 합쳐 채팅 행(WAL starvation) 재발방지 완결. 전면 커넥션 풀 재작성은 별도 백로그",
        ],
    },
    {
        "version": "2.86.2",
        "date": "2026-06-08",
        "title": "채팅 행 진짜 근원 수정 — queue.db WAL 무한증가 체크포인트",
        "changes": [
            "fix: /api/chat 행 재발의 진짜 근원 = queue.db-wal 59MB 비대(체크포인트 starvation — gthread 96 영속 커넥션이 passive autocheckpoint를 굶김) → get_db connect wedge. worker_loop에 주기 PRAGMA wal_checkpoint(TRUNCATE) 60s 추가(_wal_checkpoint)",
            "note: v2.86.1 busy_timeout 재배치는 부분 완화였고(재시작 후 재발), WAL 증가가 본 근원. py-spy로 get_db 재귀/connect wedge + WAL 59MB 확인",
            "후속 백로그: get_db 영속 thread-local 커넥션(×96) 구조 재검토(커넥션 수 제한/주기 정리), get_db recursive retry 횟수 제한",
        ],
    },
    {
        "version": "2.86.1",
        "date": "2026-06-08",
        "title": "HIS 채팅 행/지연 수정 — get_db busy_timeout 순서",
        "changes": [
            "fix: /api/chat 60s 행/000 근원 수정 — core/db.py get_db의 busy_timeout=5000을 journal_mode보다 먼저 설정(첫 PRAGMA 락 무방비 wedge 제거). py-spy로 get_db wedge 확인",
            "진단: GPU/Ollama는 정상(0.24s)이었고 세션메모리 DB 커넥션 단계 문제. /api/generate(세션 없음)는 영향 없었음",
            "검증: chat 5/5 정상(0.2~3.5s), /api/queue/status 복구(빈응답→200), VRAM 안전",
            "확인: flash_attention+kv q8는 GPU Ollama에 이미 활성(품질 유지 속도). sqlite connect 전수 스캔 — 메인 경로 견고, 잔여 7곳 저우선 백로그",
        ],
    },
    {
        "version": "2.86.0",
        "date": "2026-06-08",
        "title": "STT 오프로드 LIVE(detect/mark/stt 3종) + 보안·신뢰성 하드닝",
        "changes": [
            "🟢 STT 자동 오프로드 LIVE — 회의 분석 STT가 iMac으로(engine=imac-worker). GPU만 호출하면 detect/mark/stt 3종 자동 배정",
            "stt 멀티파트 필드 file→audio(워커 계약 일치), capability timeout_s=1800(장시간 전사)",
            "워커측(operator): MLX Metal이 gunicorn fork 후 접근불가 → 요청마다 subprocess spawn으로 해결(99e5bc4)",
            "보안 C1: imac_link/orchestrator 엔드포인트 인증 훅 추가 — /api/orchestrator/registry/reload 등 외부 무인증 노출 차단(외부 401)",
            "신뢰성: client 파일핸들 누수(H1)·dispatch never-raise(M1)·inflight 누수가드(M3)·헬스폴링 비차단 백그라운드(M5, SSE 인시던트 패턴 회피)·config 수치 방어(M4)",
            "detect/mark 콜드스타트 타임아웃 상향(관측 9s>5s 폴백 제거), detector.available 게이팅",
            "docs: ORCHESTRATOR_GUIDE/GATEWAY_BOUNDARY/MERGE_OWNERSHIP/IMAC_WORKER_TODO + Phase2-stt/embed·Phase3 기획",
        ],
    },
    {
        "version": "2.85.0",
        "date": "2026-06-08",
        "title": "오케스트레이션 운영 가동(detect 자동 위임) + 관리 콘솔 + iMac 플랫폼 통합",
        "changes": [
            "🟢 detect 자동 위임 운영 가동 — GPU만 호출하면 오케스트레이터가 iMac으로 배정(engine=imac-worker), 실패 시 GPU 로컬 폴백. 단일 진입점 유지(호출 코드 무변경)",
            "정책 detect/mark=prefer_imac (config/orchestrator.json) — offload 의도 정합",
            "영구 브릿지 ollama-worker-bridge.service (GPU localhost:8587 → prod:8587 → iMac worker, 재부팅 생존)",
            "하드닝: 워커 detector.available로 detect capability 게이팅 (ML 다운 시 breaker 트립 churn 제거)",
            "Admin 오케스트레이터 콘솔 카드 — 노드×capability×라우팅 실적/지연/breaker 가시화 (일체형 관리)",
            "운영: GPU 코드 반영은 preload_app=True라 SIGHUP 무효 → 마스터 TERM→systemd 재기동",
            "iMac 워커 안정화(협업): OBJC_DISABLE_INITIALIZE_FORK_SAFETY, DETECTOR_DEVICE=cpu(jetsam 회피), 역터널 11435, Smart Gateway/platform 통합(routes/gateway.py·system.py·core/platform.py)",
            "docs: ORCHESTRATOR_GUIDE.md(호출법/토폴로지/운영) + RELEASE_v2.85.0.md",
        ],
    },
    {
        "version": "2.84.0",
        "date": "2026-06-08",
        "title": "Node Orchestrator Phase 1 + 교차 아키텍처 device 통합 + 한국어 분리 fix",
        "changes": [
            "core/orchestrator/ — GPU↔iMac 중앙 동적 스케줄러(registry/nodes/scheduler/client/dispatch/metrics): capability 기반 자동 배정, 하드게이트[발열/메모리/breaker/heavy]+소프트가중[부하/지연]+hysteresis",
            "dispatch.run(cap,payload,local_fn=) 단일 진입점 — 폴백 예외 전파 금지. 노드0=기존동작100%(현재 휴면)",
            "routes/image.py — detect 위임을 orchestrator.run으로 이전. engine ∈ {imac-worker, gpu-local}",
            "GET /api/orchestrator/nodes + POST /api/orchestrator/registry/reload (imac_link bp)",
            "scripts/activate_imac_delegation.sh — iMac 가동 시 1커맨드 위임 활성화/검증/롤백",
            "core/device.py — 교차 아키텍처 device 단일화(cuda/mps/cpu 폴백). meeting_diarize CUDA 하드코딩 제거, meeting_stt MPS 회피(int8 폴백)",
            "fix(korean): numpy 2.x pecab 오버플로 오절단 → KSS backend=fast (24 API 분리품질 복구)",
            "ops: preload_app=True라 SIGHUP은 코드 reload 불가 — 마스터 TERM→systemd 재기동 필요(docs/CROSS_ARCH, RELEASE_v2.84.0)",
            "test: orchestrator 35건 + device 24건, gap-detector Phase 1 96%",
        ],
    },
    {
        "version": "2.82.0",
        "date": "2026-06-07",
        "title": "iMac 연계 콘솔 Phase A + ERP 공시 수명주기 API",
        "changes": [
            "routes/imac_link.py — GET /api/imac-link/status (터널 3채널+워커 health+위임 config/실적+오프로드 모델 교집합)",
            "Admin 대시보드 'iMac 연계' 카드 — 신호등/레이턴시/실적, 30s 폴링",
            "core/apple_worker.py 위임 실적 카운터 (delegated/fallback + 최근 10건)",
            "routes/notice.py — ERP 공시 수명주기 5 API (등록 멱등/일괄 207/개정 supersedes/폐지/삭제) + 해시 검증 422 + 600/min 키 버킷",
            "워커 health에 역방향 터널(8590) 자가 점검 필드",
            "fix: core/worker_guard Flask 의존 제거 (컨벤션 — check_light/heavy 순수 함수화)",
            "fix(tests): 폐기된 CheckResult 테스트 제거 — 단위 회귀 301 passed/0 failed",
            "known: macOS c2pa 실서명 미지원 확정 — iMac mark는 워터마크+iTXt 전담 (C2PA는 GPU)",
        ],
    },
    {
        "version": "2.81.0",
        "date": "2026-06-07",
        "title": "Apple Silicon Worker — iMac M1 특화 워커 (Phase 0+1)",
        "changes": [
            "worker_app.py (:8587) — AI 판별(MPS 58~92ms)/마킹 워커, 이중 인증(위임 X-Worker-Token + 독립 Bearer opt-in)",
            "core/apple_worker.py — GPU→iMac 위임 클라이언트 (config 4키 휴면, Darwin 가드, 폴백 계약 None)",
            "core/worker_guard.py — memory_pressure/thermal 가드 + heavy 작업 상호배제 락",
            "core/ai_image_detector.py — _resolve_device() MPS 분기 (cuda/cpu 기존 경로 불변)",
            "iMac 운영 자동화: launchd 4종(ollama 튜닝/caffeinate/watchdog/worker) + deploy_imac.sh + requirements-mac.txt",
            "Ollama 튜닝: NUM_PARALLEL=2로 동시 요청 직렬화 해소, Metal 100% GPU 실측 확정 (3b 22.4/7b 10.9 tok/s)",
            "stats agent darwin 분기 + apple_silicon 메트릭 (pressure/thermal/swap)",
            "erp-notice-rag 기반: 공시 정규화 7항/해시/조문 청크 + notice.db + ERP 교환 테스트 벡터",
            "테스트 41건 (worker 13 + apple_worker 7 + notice 14 + 기타) + 갭 분석 96%",
        ],
    },
    {
        "version": "2.70.0",
        "date": "2026-06-04",
        "title": "배치 전용 모드 — BATCH_MODE 환경변수로 iMac CPU 서버 배치 운영",
        "changes": [
            "BATCH_MODE=1 환경변수로 배치 전용 모드 활성화",
            "배치 모드: 실시간 Blueprint 20개 비활성화, 배치 핵심 9개만 등록",
            "배치 모드 활성 API: queue, ollama, system, korean, writing, coding, search, dashboard, activity",
            "gunicorn_batch.conf.py — CPU 서버 최적화 (threads 4, timeout 600초)",
            "/api/status에 batch_mode + version 표시",
            "비활성 API 호출 시 503 + 안내 메시지 반환",
            "큐 파이프라인 API — POST /api/queue/pipeline (다단계 순차 처리, {{prev.response}} 체인)",
            "파이프라인 상태 조회 — GET /api/queue/pipeline/{id}",
            "Dashboard Monitor — 큐 상세/적재 모델/파이프라인 현황 패널 추가",
            "qwen3-coder:30b 설치 — 코딩 도메인 MoE 업그레이드 (활성 3.3B)",
            "S3 이미지 스토리지 — Wasabi S3 업로드/Presigned URL/목록/삭제 (6 API)",
            "core/s3_storage.py — S3 업로드/다운로드/Presigned URL/관리 모듈",
        ],
    },
    {
        "version": "2.69.0",
        "date": "2026-06-04",
        "title": "모델 확장 — gpt-oss:20b + gemma4:26b + qwen3:14b 추가, 4종 정리",
        "changes": [
            "신규 모델 3종 설치: gpt-oss:20b (o3-mini급 추론), gemma4:26b (MoE TC+Vision), qwen3:14b (추론 강화)",
            "불필요 모델 4종 삭제: samantha-mistral, gemma3:12b, llava-med, biomistral (~25GB 확보)",
            "의료 라우팅 업데이트 — biomistral→gpt-oss, llava-med→medgemma 통합",
            "Vision dual-read — LLaVA-Med→MedGemma:4b 전환",
            "reasoning/agent 도메인 추가 (gpt-oss, gemma4:26b 활용)",
            "GPU 스케줄러 + 적응형 타임아웃 신규 모델 반영",
        ],
    },
    {
        "version": "2.68.0",
        "date": "2026-06-02",
        "title": "성능 강화 — 병렬화 + 캐시 전면 확대 + Triage 확장",
        "changes": [
            "Korean pipeline 병렬화: analyze+spellcheck, readability+cohesion 동시 실행 (ThreadPoolExecutor)",
            "Tool Calling 병렬 실행: LLM 다중 tool_call 동시 처리 (순서 보존)",
            "TTLCache 스레드 안전: threading.Lock 추가 (32 gthread 동시 접근 경쟁 해소)",
            "캐시 일괄 추가: Search/Coding/hr_law/medical_law/Korean (TTL 60~3600s)",
            "임베딩 캐시: medical.py + coding.py _get_embedding() TTL 2시간 (결정론적)",
            "SearXNG HTTP 세션 재사용: requests.Session() connection pool (web_search.py)",
            "Orchestrator intent 캐시: _detect_chat_intent() TTL 60s",
            "Novel/Story GET 캐시: 목록·상세 조회 TTL 120s (maxsize 300)",
            "Gunicorn threads 24→32, preload_app=True (메모리 공유 + 재시작 속도)",
            "SQLite busy_timeout 강화: meeting.db, counseling.db (check_same_thread=False)",
            "Triage 확장: 증상 100+개, 진료과 21개, 응급 패턴 10개, 소아·노인 특화 규칙",
        ],
    },
    {
        "version": "2.67.0",
        "date": "2026-06-02",
        "title": "MCP 서버 노출 (Model Context Protocol, 31번째 Blueprint)",
        "changes": [
            "routes/mcp.py — MCP JSON-RPC 2.0 엔드포인트 (/mcp)",
            "config/tools.json 20도구 MCP 표준 노출 (initialize/tools/list/tools/call/ping)",
            "외부 AI 에이전트(Claude Desktop/Code, GPT, Cursor) 직접 연동 지원",
            "Streamable HTTP 프로토콜 (MCP 2025-03-26 표준)",
        ],
    },
    {
        "version": "2.66.0",
        "date": "2026-06-02",
        "title": "Meeting Analysis Phase 2 — 화자 분리 + Vision 슬라이드 분석",
        "changes": [
            "core/meeting_diarize.py — SpeechBrain ECAPA-TDNN 화자 분리",
            "core/meeting_vision.py — 키프레임 추출 + qwen3-vl:8b 슬라이드 분석",
            "routes/search.py — Search Blueprint 독립 분리 (5 API: /api/search, news, science, it, status)",
            "API 531+개, 30 Blueprint 달성",
        ],
    },
    {
        "version": "2.65.0",
        "date": "2026-06-01",
        "title": "SearXNG 실시간 웹 검색 연동 (Tool Calling 21번째 도구)",
        "changes": [
            "core/web_search.py — SearXNG Docker 래퍼 (general/news/science/it 카테고리)",
            "Tool Calling search:// 프로토콜 추가 (config/tools.json web_search 도구)",
            "API 526+개 달성",
        ],
    },
    {
        "version": "2.64.0",
        "date": "2026-06-01",
        "title": "Tool Calling 네이티브 지원 (Orchestrator 통합)",
        "changes": [
            "core/tool_registry.py — config/tools.json 기반 도구 레지스트리",
            "core/tool_executor.py — LLM↔도구 반복 루프 (최대 5회, qwen3:8b)",
            "3단계 폴백: 워크플로우 매칭 → Tool Calling → General Chat",
            "도구 21개 (의료8+한국어5+글쓰기4+유틸3+검색1), ai:// + llm:// + search:// 프로토콜",
            "force_korean_json: JSON 필드별 선별 한국어화",
        ],
    },
    {
        "version": "2.63.0",
        "date": "2026-06-02",
        "title": "Meeting Analysis API — 화상회의 녹화본 AI 분석 (hospital-jitsi 연동)",
        "changes": [
            "Meeting Analysis Blueprint 6 API — STT + LLM 요약 + 액션아이템 + 콜백",
            "core/meeting_stt.py — faster-whisper 래퍼 (large-v3, GPU, 30분 청크 분할)",
            "core/meeting_pipeline.py — 순차 파이프라인 오케스트레이터 (download→STT→LLM→callback)",
            "hospital-jitsi 연동: S3 Presigned URL 다운로드 + callback POST",
            "동시 작업 2건 제한 (세마포어), 콜백 3회 지수 백오프 재시도",
            "한국어 후처리: force_korean 요약/액션아이템/하이라이트 적용",
        ],
    },
    {
        "version": "2.62.0",
        "date": "2026-06-02",
        "title": "Counseling API + 신규 모델 3종 + 한국어 후처리 전면 보강",
        "changes": [
            "Counseling Blueprint 6 API — mindwell+qwen3 오케스트레이션, 위기 감지, 세션 관리",
            "신규 모델 3종: qwen3:8b(추론), phi4-reasoning(의료), qwen3-embedding:8b(RAG 4096차원)",
            "의료 교차검증 모델 업그레이드: qwen2.5:14b → phi4-reasoning",
            "RAG 임베딩 업그레이드: bge-m3 → qwen3-embedding:8b (폴백 유지)",
            "한국어 후처리 전면 보강: postprocess_korean() 통합 함수, 10+ 엔드포인트 force_korean 적용",
            "캐시/세션 오염 수정: 스트리밍 응답 force_korean 후 저장",
            "validate≠filter 혼동 수정: force_korean → quality_check 순서 통일",
            "llm_resilience: 6개 신규 모델 타임아웃 등록",
        ],
    },
    {
        "version": "2.56.0",
        "date": "2026-05-26",
        "title": "글쓰기 AI 보강 + LibriScribe 연동 확장 7건 + 테스트 63건",
        "changes": [
            "Writing AI 3건: POST /api/writing/craft/stream (3모델 SSE), POST /api/writing/translate (7언어), GET /api/writing/languages",
            "SEO 폴백 스키마 강화 (readability 하위 필드 보장)",
            "Story 분기 제안 한국어 필터 적용",
            "LibriScribe E1: chapter/write external_context — 외부 컨텍스트 주입 (_build_novel_context 스킵)",
            "LibriScribe E2: GET /api/novel/project/:id/snapshot — 프로젝트 전체 상태 1회 조회 (5~10회→1회)",
            "LibriScribe E3: score include_web_novel_metrics — 웹소설 4축 (hook/cliffhanger/pacing/growth)",
            "LibriScribe E4: revise/consistency focus_chapter + context_summary — 단일 회차 빠른 검증",
            "LibriScribe E5: POST /api/novel/extract/metadata — 텍스트 엔티티 자동 추출 (캐릭터/떡밥/관계/성장)",
            "LibriScribe E6: chapter/write episode_structure — 회차 구조 지시 (hook/도입/전개/절정/클리프행어)",
            "LibriScribe E7: _orchestrated_write return_detail — 단계별 타이밍/토큰 상세",
            "pytest 테스트 63건 (core 16 + API 28 + novel 확장 19) — GPU 서버 전체 통과",
            "requirements.txt 신규 생성, 문서 v2.55.0 갱신 (API_REFERENCE + ARCHITECTURE)",
        ]
    },
    {
        "version": "2.55.0",
        "date": "2026-05-26",
        "title": "Admin Console 확장 — Model Manager + Orchestration Editor 독립 페이지",
        "changes": [
            "Model Manager 전용 페이지 (/model-manager) — 모델 목록/Pull/삭제/GPU 상태 (4탭)",
            "Orchestration Editor 전용 페이지 (/orchestration-editor) — 워크플로우 CRUD/실행/이력 (4탭)",
            "admin.html 탭 바에 Model Manager ↗, Orchestration ↗ 링크 추가",
            "core/llm_resilience.py — gemma4/exaone-deep/dna 모델 타임아웃 추가",
        ]
    },
    {
        "version": "2.53.0",
        "date": "2026-05-22",
        "title": "의료법 Phase 2 — 자동 검사 + 추론체인 연동 + 인폼드컨센트/광고/보관/과태료",
        "changes": [
            "의료법 Phase 2 — routes/medical_law.py 450줄 → 1074줄 (+624줄, 8 API 추가)",
            "POST /api/medical-law/check/consent — 동의서 요건 확인 (시술별 서면동의 판단)",
            "POST /api/medical-law/check/privacy — 개인정보 처리 적법성 검사 (민감정보/제3자 제공)",
            "POST /api/medical-law/check/emergency — 응급의료 의무 준수 확인",
            "POST /api/medical-law/guide/informed-consent — 인폼드 컨센트 가이드 (LLM, 시술별 위험/대안)",
            "POST /api/medical-law/guide/retention — 기록 보관 기간 안내 (10종 기록 유형별)",
            "POST /api/medical-law/guide/advertisement — 의료 광고 적법성 검토 (LLM + 금지키워드 자동검사)",
            "POST /api/medical-law/calculate/penalty — 위반 시 벌칙/과태료 조회",
            "POST /api/medical-law/auto-check — 추론체인/자동의무기록 통합 법적 검사 (기록+감염병+동의서)",
            "GET /api/medical-law/history — 상담 이력 조회",
        ]
    },
    {
        "version": "2.52.0",
        "date": "2026-05-22",
        "title": "스토리 엔진 Phase 1 — 생성 + 컨텍스트 + 이어쓰기 + 분기 + 세계관/인물",
        "changes": [
            "Story Engine Blueprint (routes/story.py) — 20 API 엔드포인트",
            "POST /api/story/generate — 스토리 생성 (9 style × 9 tone = 81 조합)",
            "POST /api/story/generate/stream — SSE 스트리밍 생성",
            "POST /api/story/continue — 이어쓰기 (parent_id 체인 + 자동 시리즈 생성)",
            "POST /api/story/branch — 선택지 분기 생성 (dramatic/subtle/humorous/moral_dilemma)",
            "POST /api/story/rewrite — 문체/톤 변환 (확장 or 톤만 변환)",
            "POST /api/story/dialogue/scene — 대화 장면 생성 (지문 + 대사)",
            "세계관 CRUD — POST/GET/PUT /api/story/world/*",
            "인물 CRUD — POST/GET/PUT /api/story/character/* + 상태 이력 추적",
            "소설 아웃라인 — POST /api/story/novel/outline (3막/히어로저니/기승전결)",
            "소설 챕터 생성 — POST /api/story/novel/chapter (5000자 챕터)",
            "세계관 확장 — POST /api/story/world/expand (규칙 준수 검증)",
            "5계층 컨텍스트 엔진 — 세계관→인물→요약→직전장면→지시",
            "점진적 요약 시스템 — 장편 이어쓰기용 자동 요약 캐시",
            "스토리 체인(시리즈) 자동 관리 — 에피소드 묶음 + 순서 추적",
            "소설 작법 규칙 시스템 — Show Don't Tell, 감각 묘사, 문장 리듬",
            "story.db — stories, story_chains, story_summaries, story_worlds, story_characters, story_relationships, story_foreshadowing 7 테이블",
            "한국어 전/후처리 전체 적용 (force_korean + force_korean_token)",
            "Novel Writer Blueprint (routes/novel.py) — 16 API 엔드포인트",
            "소설 프로젝트 CRUD — POST/GET/PUT/DELETE /api/novel/project/*",
            "시놉시스 자동 생성 — POST /api/novel/synopsis/generate (brief/standard/detailed)",
            "플롯 구조 설계 — POST /api/novel/plot/generate (3막/히어로저니/기승전결)",
            "챕터 아웃라인 — POST /api/novel/outline/generate (자동 챕터 레코드 생성)",
            "챕터 집필 — POST /api/novel/chapter/write (quick 단일모델 / orchestrated 3모델)",
            "3단계 오케스트레이션 집필 — qwen2.5:14b(초안) → exaone-deep(구조) → dna(다듬기)",
            "퇴고 시스템 — consistency(일관성) + polish(문장력) + foreshadowing(복선 회수)",
            "소설 내보내기 — POST /api/novel/export (text/markdown)",
            "컨텍스트 엔진 강화 — 관계도 CRUD + 자동 갱신 + 복선 CRUD + 자동 감지",
            "일관성 검증 — POST /api/story/validate (세계관/인물/관계/시간 모순 검사)",
            "컨텍스트 미리보기 — GET /api/story/chain/<id>/context",
            "novel_projects, novel_chapters, novel_revisions 3테이블 (story.db 공유)",
            "오케스트레이션 SSE 스트리밍 — mode=stream (단계별 실시간 진행 이벤트)",
            "오케스트레이션 비동기 실행 — mode=async (즉시 run_id 반환 + 폴링)",
            "GET /api/orchestration/run/<id>/status — 비동기 실행 상태 폴링",
            "의료 앙상블 — POST /api/medical/reasoning/ensemble (multi/consensus 2모드)",
            "Multi-Model Reasoning Chain — werub(KO)→Med42(EN)→werub(KO) 단계별 전문 모델",
            "Consensus 모드 — 3모델 순차 감별진단 투표 (합집합 + 출현빈도 가중치)",
            "DiagnosisNormalizer — 모델별 JSON 스키마 표준화 + 투표 병합",
            "Vision 기본 모델 교체 — moondream:1.8b → qwen3-vl:8b (폴백 체인 유지)",
        ]
    },
    {
        "version": "2.50.0",
        "date": "2026-05-22",
        "title": "오케스트레이션 플랫폼 + 강화 API + 34 Models",
        "changes": [
            "커스텀 오케스트레이션 플랫폼 13 API — CRUD + 실행 + 테스트 + 이력 + 비교 + 버전 + 복제",
            "POST /api/orchestration/test — validate(모델+VRAM) / dry_run / full_test 3모드",
            "GET /api/orchestration/runs — 실행 이력 DB 자동 기록 + 조회",
            "POST /api/orchestration/compare — 파이프라인 A/B 비교",
            "PUT /pipeline + GET /versions + POST /rollback — 버전 관리 + 자동 백업",
            "POST /pipeline/clone — 파이프라인 복제 (builtin 포함)",
            "on_fail(abort/continue/skip) + retry + fallback_model 에러 정책",
            "debug=true — 단계별 중간 output + quality 메트릭",
            "POST /api/chat/enhanced — 도메인별 다중 모델 오케스트레이션 (medical/coding/writing/legal)",
            "POST /api/writing/craft — 3모델 고품질 글쓰기 (qwen→exaone-deep→dna)",
            "채팅 6도메인 자동 감지 + 12+ 모델 라우팅 + 멀티모달",
            "글쓰기/비전 모델 6종 설치 (exaone-deep, dna, gemma4, qwen3-vl, minicpm-v, deepseek-ocr)",
            "총 34 모델, 350+ API",
        ]
    },
    {
        "version": "2.49.0",
        "date": "2026-05-22",
        "title": "채팅 도메인 라우팅 + 멀티모달 + 글쓰기/비전 모델 6종",
        "changes": [
            "채팅 도메인 자동 감지 — medical/legal/coding/writing/vision/general 6도메인",
            "도메인별 전문 모델 자동 라우팅 — 12+ 모델 활용 (기존 2개 → 12개)",
            "도메인별 시스템 프롬프트 자동 주입 (의료 면책, 법률 고지, 코딩 보안 등)",
            "멀티모달 채팅 — images 필드 감지 시 비전 모델 자동 전환",
            "한국어 품질 검증 도메인별 스킵 — 영어 모델/코딩 도메인",
            "컨텍스트 윈도우 자동 관리 — 모델별 한계에 맞춰 히스토리 축소",
            "응답에 domain_detected, domain_confidence, routing_reason 필드 추가",
            "글쓰기 모델 3종 설치 — exaone-deep:7.8b, dnotitia/dna:8b, gemma4",
            "비전 모델 3종 설치 — qwen3-vl:8b, minicpm-v:8b, deepseek-ocr:3b",
            "POST /api/chat/enhanced — 강화 채팅 (도메인별 다중 모델 오케스트레이션)",
            "POST /api/writing/craft — 3모델 오케스트레이션 고품질 글쓰기",
            "Writing API 모델 목록 — GET /api/writing/models",
            "Vision API 모델 목록 확장 3→6종",
            "GPU 스케줄러 MODEL_VRAM_ESTIMATE 6종 추가 (총 34모델)",
        ]
    },
    {
        "version": "2.48.0",
        "date": "2026-05-22",
        "title": "의료 모델 11종 + 도메인 라우팅 + 신규 API 8개 + 벤치마크 실측",
        "changes": [
            "의료 모델 6종 신규 설치 — Med42 8B, OpenBioLLM, BioMistral, LLaVA-Med, MediChat, MedGemma 27B",
            "MEDICAL_MODELS 레지스트리 11개 + DOMAIN_MODEL_ROUTING 20도메인",
            "한국어/영어 입력 자동 감지 → 최적 모델 분기 (_is_korean_input)",
            "모델 폴백 체인 — _resolve_model_safe() + 설치 확인 캐시",
            "POST /api/medical/clinical-qa — Med42 기반 의료인 전문 QA",
            "POST /api/medical/drug-analysis — 약물 심층 분석 (3 depth: quick/standard/comprehensive)",
            "POST /api/vision/medical/dual-read — MedGemma+LLaVA-Med 영상 이중 판독",
            "POST /api/medical/model/compare — 다중 모델 비교 (최대 3개)",
            "POST /api/medical/model/benchmark — 10 표준 증례 ICD 정확도 벤치마크",
            "POST /api/medical/research — 바이오의학 연구 질의 (OpenBioLLM)",
            "POST /api/medical/patient-education — 환자 교육 설명 (MediChat)",
            "GET /api/medical/models + routing + recommend — 모델 레지스트리 조회",
            "GPU 스케줄러 MODEL_VRAM_ESTIMATE 11개 모델 추가",
            "벤치마크 실측: Med42 80%, werub 60%, 합집합 90% (10 표준 증례)",
        ]
    },
    {
        "version": "2.47.0",
        "date": "2026-05-22",
        "title": "Admin Console 고도화 + 3콘솔 분리 + Image Fuzzy Match",
        "changes": [
            "Admin Console 3개 독립 콘솔 분리 — Medical AI / HR Law / Game NPC",
            "Medical Dashboard 고도화 — GPU/VRAM, Queue, Drug Pipeline 실시간 위젯 (10초 폴링)",
            "Reasoning Chain — 모델 선택 (6종), 세션 히스토리, A/B 모델 비교 뷰",
            "Predict Dashboard — SVG 원형 게이지, Risk Factor 바 차트, 예측 히스토리",
            "이미지 모델명 fuzzy 매칭 — 3단계 (exact id → exact ckpt → fuzzy prefix)",
            "Admin Console 정적 파일 rate limit 제외 (@limiter.exempt)",
            "메인 대시보드에 Medical AI / HR Law / Game NPC 개별 링크",
        ]
    },
    {
        "version": "2.44.0",
        "date": "2026-05-20",
        "title": "P2-3: RAG Hybrid Search — 벡터 + 키워드 + Reranking + 소스 인용",
        "changes": [
            "P2-3 Hybrid Search 엔진 — 벡터(ChromaDB) + 키워드(SQLite FTS5) + RRF 병합",
            "LLM Reranking — 후보 문서 관련성 0-10 점수 재정렬 (선택적)",
            "적응적 컨텍스트 — 질문 복잡도에 따라 3000→5000자 확장",
            "소스 인용 — 응답에 [1], [2] 참조 번호 + citations 메타데이터",
            "Medical knowledge/search 4모드 — hybrid/vector/keyword/sql",
            "Medical reference API — Hybrid Search 기본 적용 + 인용 지시",
            "RAG search API — mode=hybrid 옵션 추가",
            "FTS5 동시 색인 — 벡터 색인 시 FTS 자동 병행",
            "build_context_with_citations — 적응적 컨텍스트 + 인용 빌더",
        ]
    },
    {
        "version": "2.43.0",
        "date": "2026-05-20",
        "title": "FHIR-Lite + HIS SDK — 병원정보시스템 연동 표준화",
        "changes": [
            "FHIR R4 래퍼 Blueprint — 기존 API를 FHIR 표준으로 변환",
            "FHIR DiagnosticReport — 추론 체인 → DiagnosticReport + Observation Bundle",
            "FHIR DocumentReference — 자동 의무기록 → DocumentReference + MedicationRequest",
            "FHIR RiskAssessment — 재입원/합병증/ADR 예측 → RiskAssessment Bundle",
            "FHIR Observation — 약물 상호작용 → Observation Bundle",
            "FHIR Condition — KCD 코드 검색 → Condition searchset Bundle",
            "FHIR CapabilityStatement — 서버 지원 리소스 메타데이터",
            "HIS 연동 Python SDK (client/his_client.py) — 일반 API + FHIR API 통합 클라이언트",
            "파인튜닝 모델 Ollama 등록: werub-medical:7b (QLoRA + Q4_K_M 4.7GB)",
        ]
    },
    {
        "version": "2.42.0",
        "date": "2026-05-20",
        "title": "Phase 3: Performance Architecture — GPU Scheduling + Priority Queue + Streaming",
        "changes": [
            "P3-1 스마트 GPU 스케줄링 — 시간대별 모델 전략 (의료/비의료) + VRAM 모니터링",
            "P3-1 자동 모델 적재/해제 — VRAM 부족 시 우선순위 기반 모델 교체",
            "P3-1 백그라운드 스케줄러 — 5분마다 프로파일 체크 + 자동 전환",
            "P3-2 도메인 우선순위 큐 — 응급(0)→의료긴급(1)→의료일반(2)→일반(3)→배경(4)",
            "P3-2 응급 키워드 감지 — 심정지/아나필락시스 등 자동 priority=0",
            "P3-2 API 경로 기반 자동 우선순위 — 22개 도메인 매핑",
            "P3-3 SSE 토큰 스트리밍 — /api/ws/chat, /api/ws/generate (first-token latency 측정)",
            "P3-3 실시간 상태 스트리밍 — /api/ws/status (큐+GPU+모델 3초 갱신)",
            "P3-4 동적 배치 크기 — 경량 모델 8, 14b 2, 기본 4 (VRAM 기반)",
            "GPU API 6종 — scheduler/apply/load/unload/priority/classify",
        ]
    },
    {
        "version": "2.41.0",
        "date": "2026-05-20",
        "title": "Phase 4: Advanced Medical AI — Reasoning Chain + Auto Records + Predictive",
        "changes": [
            "P4-1 의료 추론 체인 — 5단계 감별진단 파이프라인 (정규화→감별→순위화→검사→평가)",
            "P4-1 추론 체인 SSE 스트리밍 — 단계별 실시간 결과 전송",
            "P4-3 자동 의무기록 — 진료 대화 NER → SOAP + ICD-10 + 처방 자동 생성",
            "P4-3 자동 의무기록 SSE 스트리밍 — NER/SOAP 단계별 스트리밍",
            "P4-2 구조화된 영상 판독 — BI-RADS/Lung-RADS/TI-RADS/LI-RADS 등급",
            "P4-4 재입원 위험도 예측 — HOSPITAL Score 기반 30일 재입원 예측",
            "P4-4 합병증 위험도 예측 — 진단별 합병증 목록 + 모니터링 계획",
            "P4-4 약물 부작용(ADR) 예측 — 상호작용 + 중복약효 + DB 검증 병행",
            "core/reasoning_chain.py — 추론 체인 엔진 (Step 4/5 병렬 실행)",
            "routes/medical_advanced.py — 8 API 엔드포인트 (Blueprint 19번째)",
        ]
    },
    {
        "version": "2.40.0",
        "date": "2026-05-20",
        "title": "Writing AI + Medical Precheck + Performance + Fine-tuning",
        "changes": [
            "Medical Image Precheck API — 의료 영상 사전 검증 (품질/분류/모달리티)",
            "SOAP 병렬 생성 — ThreadPoolExecutor 4+1 (12초→4초)",
            "의료 프롬프트 캐싱 — TTLCache(200, 1h) 반복 질의 95% 절감",
            "경량 모델 라우팅 — explain/reference/translate → qwen2.5:7b",
            "RAG 임베딩 캐시 — 동일 쿼리 500ms 절감",
            "의료 배치 API — POST /api/medical/batch (최대 20건 동시 처리)",
            "영상 판독 스트리밍 — SSE 토큰 단위 (체감 1초)",
            "Fine-tuning 관리 UI — /medical-training (데이터 준비/학습/배포)",
            "Fine-tuning 파이프라인 — QLoRA + GGUF + Ollama deploy",
            "Vision 에러 코드 10종 (PRECHECK_FAILED, MODEL_NOT_FOUND 등)",
            "Writing AI Blueprint 6 API — 톤앤매너 6종, SEO, 코칭, 스트리밍",
            "약물 상호작용 DB 61→97건, 삭감사유 34→75건 보충",
            "Triage Structured Output — force_json_array 적용",
            "gzip 응답 압축 (70-80% 감소)",
            "Gunicorn threads 16→24",
            "PACS 연동 가이드 + 의료 성능 계획안"
        ]
    },
    {
        "version": "2.39.1",
        "date": "2026-05-20",
        "title": "Security Hardening + Quality 52→93",
        "changes": [
            "보안 16건 — @require_auth 전체 적용 (medical/hr_law/coding/rag)",
            "bare except 46→0, silent pass 34→0, conn.close try/finally ~90건",
            "datetime.utcnow() 29→0 (timezone.utc 교체)",
            "requests alias 수정, 하드코딩 URL → config 상수",
            "P2-3 HR Law AI Quality PDCA (90% Match Rate)",
            "HIS/PACS 서버 모니터링 프록시 + 대시보드"
        ]
    },
    {
        "version": "2.39.0",
        "date": "2026-05-18",
        "title": "P5~P8 — 17 Features + Quality",
        "changes": [
            "P5-1 Structured Output (generate_with_validation 4개 적용)",
            "P5-2 RAG Smart Chunking (5전략 + 병합검색)",
            "P5-3 Orchestrator 직접 호출 (32ms→<1ms)",
            "P6-1 Triage 다증상 (순서 무관 + 15개 룰)",
            "P6-2 ComfyUI 프리로드 (3모드)",
            "P7-1 ICD-10 검증 (KCD 469건)",
            "P7-2 약물 상호작용 (61건 DB + API)",
            "P7-3 직접 호출 확장 (summarize/analyze)",
            "P7-4 코드 구문 검증 (ast.parse)",
            "P7-5 Chat Session API (list/messages/delete)",
            "P8-2 히스토리 주입 (LLM 대화 기억)",
            "P8-3 SOAP 분리 생성 (S/O/A/P 별도)",
            "P8-4 코드 보안 스캔 (13 OWASP 패턴)",
            "P8-5 스트리밍 세션 저장",
            "P8-6 다국어 면책 (ko/en/zh/vi/ja)"
        ]
    },
    {
        "version": "2.36.0",
        "date": "2026-05-18",
        "title": "P4-1 Chat Quality + P4-2 Chat Server Memory",
        "changes": [
            "Chat 한국어 품질 강화 (quality wrapper 적용)",
            "Chat 서버 메모리 — 대화 이력 DB 저장/복원",
            "Swagger/API Reference/Integration Guide 문서 동기화"
        ]
    },
    {
        "version": "2.35.0",
        "date": "2026-05-12",
        "title": "MedGemma Medical Imaging AI",
        "changes": [
            "MedGemma 4B (Google DeepMind) 의료 영상 분석 모델 탑재",
            "Vision API 3개 추가: /api/vision/medical/analyze, /medical/models, /medical/report",
            "의료 영상 판독: 소견(Findings), 판독소견(Impression), 권고사항(Recommendations) 구조화",
            "비교 판독: 현재 vs 이전 영상 변화 분석",
            "지원 모달리티: X-ray, CT, MRI, 초음파, 병리, 안저, 피부경",
            "docs/MEDICAL_IMAGING_MODELS.md 의료 영상 모델 검토 보고서"
        ]
    },
    {
        "version": "2.34.0",
        "date": "2026-05-12",
        "title": "Quality Phase 1 + Architecture Docs",
        "items": [
            "core/quality.py: force_json, force_json_array, response validation, auto-retry",
            "JSON parsing robustness: medical analyze/summarize/explain, coding review",
            "Orchestrator: HTTP self-call → Flask direct dispatch (zero overhead)",
            "docs/ARCHITECTURE.md: system architecture + feature spec + roadmap",
            "docs/API_REFERENCE.md: unified ~255 API reference",
            "docs/CODING_API_GUIDE.md: 16 coding API reference",
            "docs/plan_quality_improvement.md: 3-phase quality plan"
        ]
    },
    {
        "version": "2.33.0",
        "date": "2026-05-12",
        "title": "Code RAG — Codebase Indexing + Retrieval-Augmented Code Generation",
        "items": [
            "Code RAG: 6 APIs (index, batch, search, ask, delete, stats)",
            "ChromaDB code_knowledge collection with nomic-embed-text",
            "RAG-augmented code generation: search existing codebase + LLM answer",
            "Vector search with language/category/project filters",
            "Coding AI total: 16 APIs (10 core + 6 RAG)"
        ]
    },
    {
        "version": "2.32.0",
        "date": "2026-05-12",
        "title": "Coding AI — 10 APIs with qwen2.5-coder:14b",
        "items": [
            "Coding AI: 10 APIs (generate, review, refactor, debug, test, explain, convert, architect, chat)",
            "Models: qwen2.5-coder:14b (HumanEval 88%, default), deepseek-coder-v2:16b (122tok/s, fast)",
            "Code review with severity levels (critical/warning/suggestion)",
            "Language conversion (Python/TS/Java/Go/Rust)",
            "Architecture design in Korean",
            "Streaming code generation"
        ]
    },
    {
        "version": "2.31.0",
        "date": "2026-05-09",
        "title": "AI Orchestrator — Workflow Engine + Smart Chat",
        "items": [
            "Orchestrator: 7 APIs (run, stream, chat, workflows, status, history)",
            "5 pre-defined workflows (quick_reception, full_consultation, nurse_assessment, bill_calculate, claim_review)",
            "Context pipeline: previous step results auto-injected into next step",
            "Smart chat: natural language → auto workflow selection + execution",
            "SSE streaming for real-time step progress",
            "orchestrator.db for run history and step logs"
        ]
    },
    {
        "version": "2.30.0",
        "date": "2026-05-09",
        "title": "Medical CLI Handlers + Data Completion + Symptom Triage + Orchestrator Plan",
        "items": [
            "Symptom Triage: 4 APIs (symptom to department, emergency detection, follow-up)",
            "Medical CLI: dedicated handlers for billing/nurse/doctor/claims (DB-driven, instant)",
            "Data completion: KCD 469, EDI 256, drugs 159, interactions 61, mappings 100, rules 39, denials 34",
            "clinic_codes.db: 1,118 total data points",
            "Drug interaction lookup with Korean name conversion",
            "Auto bill calculation: KCD to EDI mapping to copay",
            "Claim validation against rules DB (bundle/limit/excluded)",
            "Orchestrator v3 plan: 25 workflows across 6 domains (HIS+Blog+Cafe+Workspace)"
        ]
    },
    {
        "version": "2.29.0",
        "date": "2026-05-07",
        "title": "Medical AI API + ChromaDB Vector Search",
        "items": [
            "Medical AI: 22 API endpoints (multi-tenant, SOAP summary, differential diagnosis, RAG reference)",
            "Clinical English: medllama2 analysis + meditron drug info + KO translation pipeline",
            "ChromaDB: bge-m3 1024d vector search for medical knowledge (156 docs)",
            "Knowledge base: ICD-10 50, drugs 50, guidelines 20, diseases 16, lab refs 20",
            "Multi-tenant dashboard plan (docs/plan_tenant_dashboard.md)",
            "OLLAMA_MAX_LOADED_MODELS=2 (dual model loading verified)"
        ]
    },
    {
        "version": "2.28.0",
        "date": "2026-05-06",
        "title": "Module Split Complete - 10 Blueprints + Dynamic Server Title",
        "items": [
            "app.py 18,380 -> 9,000 lines (-51%)",
            "10 Blueprints: vision, rag, training, queue, image, hr_law, game, ollama, system, activity",
            "6 core modules: config, cache, korean, security, queue_engine, logger",
            "Korean helpers: 3x duplicate -> core/korean.py single source",
            "Dynamic server title: WeRU.B AI Server Admin - GPU/iMac/CPU auto-detect",
            "Integration test: 44/44 endpoints passed (GET 39 + POST 5)",
            "13+ bug fixes: login, health check, chat, HR Law streaming, etc."
        ]
    },
    {
        "version": "2.27.0",
        "date": "2026-05-06",
        "title": "Vision API + Knowledge UI + RAG/글쓰기 Phase 3",
        "items": [
            "Vision API: /api/vision/describe (moondream:1.8b 이미지 설명 생성)",
            "Knowledge Base 관리 UI (Admin Console — RAG 색인/검색/질의/삭제)",
            "글쓰기 AI Phase 3: RAG 기반 글쓰기 + 인라인 자동완성 (Tab 수락)",
            "다국어 초안: 한국어+영어+일본어 동시 생성",
            "디스크 정리: 패치 스크립트/백업/훈련 출력물 5GB+ 삭제",
        ],
    },
    {
        "version": "2.26.0",
        "date": "2026-05-05",
        "title": "한국어 품질 검증 + 중국어 필터 강화",
        "items": [
            "_llm_generate_korean: 응답 한글 비율 검증 + 불완전 시 최대 2회 자동 재시도",
            "_korean_quality_check: 한글 30% 미만 / 빈 응답 / 5자 미만 감지",
            "중국어 필터 강화: 한자 인라인 전면 제거 + CJK 구두점 제거",
            "NPC 대화 / 히로인 대화 / HR Law 질의 3곳 적용",
        ],
    },
    {
        "version": "2.25.0",
        "date": "2026-05-05",
        "title": "GPUResourceManager — ComfyUI on-demand + 양방향 GPU 스케줄링",
        "items": [
            "ComfyUI 기본 OFF → LLM이 VRAM 전부 활용 (크래시 방지)",
            "이미지 요청 시 ComfyUI 자동 시작 (콜드스타트 ~5초)",
            "이미지 완료 후 ComfyUI VRAM 해제 → LLM 즉시 복원",
            "5분 idle 후 ComfyUI 자동 종료 (RAM도 회수)",
            "LLM 호출 시 ComfyUI VRAM 자동 해제 (양방향 스케줄링)",
            "/api/gpu/status 엔드포인트 추가",
        ],
    },
    {
        "version": "2.24.1",
        "date": "2026-05-04",
        "title": "Chat Fair Queue — 전체 LLM 호출 공정 큐 게이트",
        "items": [
            "글로벌 동시 채팅 3건 제한 (서버 보호)",
            "유저별 동시 채팅 1건 제한 (공정 배분)",
            "초과 시 FIFO 대기열 + 라운드로빈 스케줄링 (최대 20건)",
            "대기 타임아웃 60초 → 429 응답 (retry_after 안내)",
            "LLM 캐시 히트 시 슬롯 미사용 즉시 반환",
            "스트리밍 응답 자동 캐시 저장",
            "/api/chat/queue/stats 엔드포인트 추가",
            "GatedOllamaSession: 모든 API의 LLM 호출에 자동 Fair Queue 적용",
            "HR Law/Game NPC/히로인 등 28개 엔드포인트 보호",
            "큐 워커/웹훅은 자동 우회 (Flask context 기반 판별)",
        ],
    },
    {
        "version": "2.22.0",
        "date": "2026-04-29",
        "title": "Heroine NPC — 연애 시뮬레이션 AI",
        "items": [
            "히로인 프로필: 성격/외모/취향/이상형/루트 설정",
            "호감도 시스템: stranger→acquaintance→friend→crush→lover→soulmate",
            "멀티턴 대화: 이전 대화 맥락 유지 (최대 20턴)",
            "데이트: 장소/이벤트별 호감도 변화",
            "선물: 취향 매칭 → 호감도 보너스",
            "고백: 호감도 기반 성공/거절/보류",
            "질투/신뢰: 다른 히로인과의 상호작용 영향",
            "CG 일러스트: 이벤트별 자동 생성",
        ],
    },
    {
        "version": "2.21.0",
        "date": "2026-04-29",
        "title": "Shared NPC — 사용자 간 영향 전파 + 추억 시스템",
        "items": [
            "공유 NPC: is_shared 플래그 — 모든 플레이어가 영향을 주는 주요 NPC",
            "추억 시스템: 플레이어별 좋은/나쁜 기억 축적 (감정 강도 포함)",
            "평판 시스템: 플레이어별 NPC 호감도 (-100~100) + 칭호",
            "영향 전파: 한 플레이어 행동이 NPC 글로벌 상태에 반영",
            "NPC 무드: 전체 플레이어 영향의 합산 → 다른 플레이어 대화에 반영",
            "/api/game/shared/* 엔드포인트 7개 추가",
        ],
    },
    {
        "version": "2.20.0",
        "date": "2026-04-29",
        "title": "Game NPC Phase 2~4 — 다자대화·시나리오·일러스트",
        "items": [
            "다자 대화 (2~6명 파티): /api/game/dialogue/group",
            "스킬 자동 생성: /api/game/growth/learn-skill",
            "레벨업 처리: /api/game/growth/level-up (스탯/스킬 자동)",
            "시나리오 생성: /api/game/scenario/generate (퀘스트/이벤트/분기)",
            "캐릭터 일러스트: /api/game/illustrate/character (ComfyUI+IP-Adapter)",
            "장면 일러스트: /api/game/illustrate/scene",
            "세계관 RAG: /api/game/world/setting, /world/search",
        ],
    },
    {
        "version": "2.19.0",
        "date": "2026-04-29",
        "title": "Game NPC AI — 대화·성장·시나리오·일러스트",
        "items": [
            "게임사 멀티테넌트: X-Game-Key + player_id 3단계 격리",
            "NPC 템플릿/인스턴스: 게임사 공통 원본 + 플레이어별 독립 성장",
            "NPC 대화: 1:1, 다자(파티), 플레이어↔NPC — 성격/기억/관계 기반",
            "성장 시스템: 대화 품질 → EXP → 레벨업 → 스킬/스탯 자동 생성",
            "시나리오 생성: 퀘스트/이벤트/분기 자동 생성",
            "일러스트: 캐릭터(IP-Adapter)/장면/컷신 — ComfyUI 연동",
            "세계관 RAG: 설정/역사/지역 색인 + NPC 기억",
        ],
    },
    {
        "version": "2.18.0",
        "date": "2026-04-29",
        "title": "취업규칙 법위반 검토 + 다국어 지원",
        "items": [
            "/api/hr-law/rules/review: 취업규칙 법 위반 검토 (LLM 분석)",
            "/api/hr-law/ask: language 파라미터 추가 (ko/en/vi/zh/ja)",
            "다국어 시스템 프롬프트 자동 전환",
            "법위반 검토 결과: 위반/주의/적합 판정 + 수정 제안",
        ],
    },
    {
        "version": "2.17.0",
        "date": "2026-04-29",
        "title": "HR Law 멀티테넌트 — 회사별 취업규칙 SaaS 구조",
        "items": [
            "멀티테넌트: 회사별 API 키 + 취업규칙 분리",
            "테넌트 CRUD: /api/hr-law/tenant/* (등록/목록/수정/삭제)",
            "회사별 취업규칙: /api/hr-law/rules/* (등록/목록/검색)",
            "질의응답 테넌트 분리: 공통 법률 + 회사 취업규칙 통합 검색",
            "사용량 추적: 회사별 일일 쿼리 제한",
        ],
    },
    {
        "version": "2.16.0",
        "date": "2026-04-28",
        "title": "근태/인사 법률 AI 대응 시스템",
        "items": [
            "HR Law DB: 근로기준법 등 핵심 법률 21개 조문 색인",
            "/api/hr-law/ask: 법적 근거 포함 AI 질의응답",
            "/api/hr-law/search: 법률 키워드 검색",
            "/api/hr-law/sources: 수집 법률 목록",
            "/api/hr-law/stats: 색인 통계",
            "/api/hr-law/calculate: 연차/초과근무수당/퇴직금 계산기",
            "Admin UI: HR Law 탭 추가",
        ],
    },
    {
        "version": "2.15.0",
        "date": "2026-04-28",
        "title": "성능 P3 — iMac 오프로드 + GPU 스케줄러",
        "items": [
            "iMac M1 경량 모델 오프로드: 3b/2b 모델 자동 라우팅 (터널 헬스체크 30초 캐시)",
            "GPU 스케줄러: 이미지 생성 시 LLM VRAM 자동 해제 → 완료 후 기본 모델 복원",
            "이미지 생성 속도 향상 (full VRAM 활용, lowvram 오버헤드 제거)",
            "/api/imac/status 엔드포인트: iMac 연결 상태 확인",
        ],
    },
    {
        "version": "2.14.0",
        "date": "2026-04-27",
        "title": "성능 P2 — 배치 추론 + 이미지 파이프라인",
        "items": [
            "LLM 배치 추론: 동일 모델 최대 4건 ThreadPoolExecutor 동시 처리",
            "_process_single_llm_job() + _handle_llm_error() 분리",
            "모바일 자동 해상도 (768x768) — User-Agent 감지 또는 auto_resize 파라미터",
            "Swagger: auto_resize, no_cache 파라미터 추가",
        ],
    },
    {
        "version": "2.13.0",
        "date": "2026-04-27",
        "title": "성능 P1 — 응답 캐싱 + LLM/Image 워커 분리",
        "items": [
            "TTLCache: LLM 응답 캐시 (500건/1h) + 이미지 모델 목록 캐시 (5m)",
            "queue submit 캐시 히트 시 즉시 반환 (no_cache 파라미터로 우회)",
            "/api/cache/stats, /api/cache/clear 엔드포인트",
            "LLM/Image 듀얼 워커: llm-worker + image-worker 병렬 실행",
            "이미지 생성 중 LLM 추론 동시 처리 가능",
        ],
    },
    {
        "version": "2.12.0",
        "date": "2026-04-27",
        "title": "성능 개선 P0 — Gunicorn + VRAM 최적화",
        "items": [
            "Gunicorn gthread 도입 (2워커 × 8스레드, 동시 요청 16배↑)",
            "백그라운드 스레드 구조 개선 (_start_background_threads)",
            "ComfyUI --lowvram 제거 (이미지 30~50%↑)",
            "Ollama KV Cache q8_0→q4_0 (VRAM 50%↓)",
            "gunicorn.conf.py 추가",
        ],
    },
    {
        "version": "2.11.0",
        "date": "2026-04-27",
        "title": "LCM 초고속 생성 + Detail LoRA + ControlNet OpenPose",
        "items": [
            "LCM-LoRA: lcm-fast 스타일 — SDXL 4-8 step 초고속 생성 (sampler/cfg 자동 오버라이드)",
            "Detail Tweaker XL LoRA: high-detail 스타일 — 디테일 강화",
            "ControlNet OpenPose XL 설치 (포즈 기반 캐릭터 생성 준비)",
            "_build_sdxl_lora_workflow() LCM 모드 지원 (sampler_override, scheduler_override)",
        ],
    },
    {
        "version": "2.10.2",
        "date": "2026-04-27",
        "title": "이미지 생성 안정성 + 릴리즈 정리",
        "items": [
            "ComfyUI 타임아웃 900s→120s (배치 워커 블록 최소화)",
            "/api/image/status — job_id DB 조회 우선 (prompt_id 폴백)",
            "삭제된 이미지 Queue에서 삭제됨 표시 (onerror 처리)",
            "VERSION/CHANGELOG 누락분 일괄 반영",
        ],
    },
    {
        "version": "2.10.0",
        "date": "2026-04-24",
        "title": "픽셀아트 LoRA + 캐릭터 일관성(IP-Adapter)",
        "items": [
            "픽셀아트 스타일 4종: pixel-art, pixel-art-character, pixel-art-sprite, pixel-art-scene",
            "pixel-art-xl LoRA 자동 적용 + _build_sdxl_lora_workflow()",
            "캐릭터 일관성: IP-Adapter + CLIP Vision 기반 참조 이미지 시스템",
            "캐릭터 CRUD API 5개: create/list/detail/delete/view + 생성 이력 조회",
            "_build_sdxl_ipadapter_workflow(): IPAdapterUnifiedLoader 활용",
            "generate API 확장: character_id, ip_adapter_strength, ip_adapter_mode",
            "스타일 프리셋에 lora, default_size, negative 필드 추가",
        ],
    },
    {
        "version": "2.9.0",
        "date": "2026-04-22",
        "title": "인증 구조 단일화 (users.db 전용)",
        "items": [
            "DEFAULT_CONFIG admin_password_hash 하드코딩 제거",
            "init_db() admin 자동 생성 블록 제거",
            "_verify_web_login() config.json 폴백 제거 → DB 전용 인증",
            "서비스 재시작 시 비밀번호 초기화 버그 근본 해결",
            "cs-mod 평가 프롬프트 Alpaca 포맷 개선",
        ],
    },
    {
        "version": "2.8.0",
        "date": "2026-04-22",
        "title": "시스템 환경 자동 감지 + 권장 설정",
        "items": [
            "시스템 환경 자동 감지: OS/CPU/RAM/GPU(CUDA/Metal) 판별",
            "환경 기반 권장 설정: 모델/병렬 수/학습 가능 여부 자동 계산",
            "/api/system/info + /api/system/apply-recommended API",
            "Dashboard 시스템 정보 배지 (GPU/RAM/CPU/권장 모델)",
            "iMac(macOS) 운영 가이드 README 추가",
        ],
    },
    {
        "version": "2.7.0",
        "date": "2026-04-22",
        "title": "파일 분리 + 이미지 갤러리 강화 + IME 수정",
        "items": [
            "HTML/JS/CSS 파일 분리: templates/ + static/js/main.js + static/css/admin.css",
            "이미지 갤러리: 체크박스 일괄 선택/삭제 + 프로그레스바 + 페이드아웃",
            "디스크 용량 프로그레스바 (사용량/전체 + 색상)",
            "/api/image/gallery API (파일 목록 포함)",
            "한글 IME 이중 입력 수정 (Chat + CLI)",
            "비밀번호 변경: BASE 경로 + users.db 동기화",
            "JS SyntaxError 근본 해결 (인라인 → 외부 파일)",
            "Ollama 14b 최적화: 병렬 4 + 캐싱 5000건 + 영구 상주",
        ],
    },
    {
        "version": "2.6.0",
        "date": "2026-04-22",
        "title": "Moderation API + 수집/정제 + 리포트",
        "items": [
            "Moderation API: /api/mod/judge (실시간 판정), /api/mod/collect (보정 수집)",
            "/api/mod/stats (통계), /api/mod/refine (정제 파이프라인)",
            "학습 리포트: /runs/:id/report (종합 + 자동 진단 + 오분류)",
            "수집 현황 UI: 수집/정제/대기/보정 4칸 + 정제 실행 버튼",
            "자동 정제 스케줄러 (1시간 주기, pending ≥ 100건)",
            "CLI: 수집 현황, 정제 실행, 데이터 건강도",
            "moderation.db: raw_collections + refined_samples",
        ],
    },
    {
        "version": "2.5.0",
        "date": "2026-04-21",
        "title": "Training Center + 사용자 관리 + 학습 방향성 제어",
        "items": [
            "Training 탭 4단계 위저드 UI (재료→학습→결과→적용)",
            "학습 자료실: 파일 업로드/다운로드/미리보기/삭제",
            "학습 방향 7프리셋 + 카테고리 가중치 슬라이더 + 시스템 프롬프트 편집",
            "학습 대상 모델 선택 (Ollama 설치 모델 실시간 목록)",
            "사용자 관리: admin/operator/viewer 3역할 + 메뉴별 권한 체크박스",
            "SSR 권한 기반 탭 숨김 (서버 사이드 HTML 치환)",
            "상단 헤더: 사용자명(역할) + 로그아웃 버튼",
            "학습 가이드 사이드바 위저드 (🧙 버튼)",
            "실제 QLoRA Fine-tuning 성공 (16초, loss 3.08→0.24)",
            "한글 CLI 어시스턴트 + LLM 2단계 의도 분기 + 분석 엔진",
        ],
    },
    {
        "version": "2.4.0",
        "date": "2026-04-20",
        "title": "Content Safety Training API",
        "items": [
            "Training API 13 엔드포인트 (start/pause/resume/stop/status/runs/deploy/rollback/config/gpu)",
            "GPU 우선순위 P0>P1>P2 — 유휴 검사 + 허용 시간대 + 자동 일시정지",
            "training.db SQLite — 학습 실행 이력 + 설정 저장",
            "Swagger 문서 (Training 태그)",
        ],
    },
    {
        "version": "2.3.1",
        "date": "2026-04-20",
        "title": "All Servers 듀얼 대시보드",
        "items": [
            "All Servers 페이지 (/dashboard/all) — GPU + Production 나란히 표시",
            "서버별 CPU/RAM 차트, GPU/Disk 미터, 서비스 상태, 프로세스, 스파이크 이력",
            "상태 dot 인디케이터 (녹색/빨간색) 즉시 확인",
            "기존 대시보드에 All Servers 링크 추가",
        ],
    },
    {
        "version": "2.3.0",
        "date": "2026-04-20",
        "title": "서버 대시보드 고도화 + 보안 강화 + 프로덕션 모니터링",
        "items": [
            "보안: /api/server/stats @require_auth + XSS esc() 이스케이프",
            "보안: health_check DB 커넥션 try/finally (leak 방지)",
            "CPU: /proc/stat 델타 기반 실제 사용률 (load average 대체)",
            "CPU: 24코어 개별 사용률 차트 (CPU CORES 패널, 코어별 색상)",
            "차트: 프로세스 합산 CPU 빨간 점선 오버레이 (스파이크 포착)",
            "차트: idle % 보라색 실선 + 그라데이션 오버레이",
            "차트: peak/top 수치 표시 (CPU/RAM)",
            "SPIKE ALERT: 프로세스 CPU≥50% / MEM≥30% 감지 이력 패널",
            "프로세스: ps aux 자체 제외, COMMAND 컬럼 스크롤 개선",
            "성능: 폴링 1초→5초, document.hidden 탭 비활성 최적화",
            "프로덕션 서버 모니터링: GPU↔Production 서버 전환 버튼",
            "프로덕션 stats API (server-stats.py) + SSH 터널 연동",
        ],
    },
    {
        "version": "1.5.0",
        "date": "2026-04-06",
        "title": "Activity 서버별 통계 / API 키 기준 구분",
        "items": [
            "Activity 탭 기본 뷰를 서버별 통계 카드로 개편 (기간 선택, 자동 갱신)",
            "SSH 터널 환경에서도 Bearer 토큰으로 호출 서버 구분",
            "api_activity 테이블에 key_name 컬럼 추가 (local / web:admin / 키 이름)",
            "워커 루프 DB 커넥션 누수 수정 — try/finally로 명시적 close",
            "Ollama 추론 중(최대 300s) DB 커넥션 비점유 구조로 개선",
            "app.run(threaded=True) 명시적 설정",
        ],
    },
    {
        "version": "1.4.0",
        "date": "2026-04-04",
        "title": "API Activity 로그 탭",
        "items": [
            "모든 /api/ 호출을 api_activity 테이블에 자동 기록",
            "경로·메서드·상태코드·응답시간·모델 필터링",
            "최근 1시간 요약 통계 및 Top 경로 태그",
            "자동 갱신 및 2000건 자동 순환",
        ],
    },
    {
        "version": "1.3.0",
        "date": "2026-04-03",
        "title": "모델 초기화 명령어 세트",
        "items": [
            "현재 시각(KST) · 국가 · 지역 · 언어 자동 주입 시스템 프롬프트",
            "커스텀 초기 명령어 설정 (init_config.json)",
            "Models 탭 — 초기화 버튼 및 초기화 설정 편집 UI",
            "GET/POST /api/models/init-config, POST /api/models/init 엔드포인트 추가",
        ],
    },
    {
        "version": "1.2.0",
        "date": "2026-04-02",
        "title": "웹 UI 로그인 인증",
        "items": [
            "관리자 ID/PW 로그인 (PBKDF2-HMAC-SHA256, 200,000회)",
            "Flask 세션 기반 8h 자동 만료",
            "API Key 관리 페이지를 세션 인증으로 접근 가능",
            "커스텀 confirm 모달 (브라우저 팝업 차단 대응)",
            "비밀번호 변경 API 추가",
        ],
    },
    {
        "version": "1.1.0",
        "date": "2026-04-01",
        "title": "보안 강화",
        "items": [
            "6개 엔드포인트 @require_auth 누락 수정 (generate, chat, pull, delete, service, logs)",
            "Webhook SSRF 차단 (core.security IP 해석 기반 사설/메타데이터 대역 차단)",
            "int() 변환 미검증 4곳 → 400 에러 처리",
            "verify_api_key 캐시 변수명 버그 수정 (now → now_ts)",
            "Flask secret_key 파일 영속화 (.secret_key)",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-03-31",
        "title": "초기 릴리즈",
        "items": [
            "Flask REST API + SQLite WAL 배치 큐",
            "Ollama 모델 관리 (pull, delete, generate, chat)",
            "우선순위 큐 + Fast Lane (경량 모델 자동 부스트)",
            "API Key 인증, Rate Limit, CORS",
            "Prometheus 메트릭, Swagger UI",
            "ChromaDB RAG (nomic-embed-text 768-dim)",
            "SSH 리버스 터널 연동 (Ubuntu → iMac M1)",
        ],
    },
]

import hashlib
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import sys
import time
import threading
import uuid
import subprocess
import shutil
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template_string, request, jsonify, Response, session, redirect, url_for
import requests
from flasgger import Swagger

# --- core/ 모듈 (프레임워크 무관, Step 1 분리) ---
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.config import load_config as _core_load_config, OLLAMA_BASE as _CORE_OLLAMA_BASE
from core.cache import TTLCache as _CoreTTLCache, llm_response_cache as _core_llm_cache
from core.korean import force_korean as _core_force_korean, korean_quality_check as _core_quality_check
from core.security import validate_webhook_url as _core_validate_webhook, fire_webhook as _core_fire_webhook
from core.queue_engine import ChatFairQueue as _CoreChatFairQueue
from core.logger import setup_logger as _core_setup_logger
from core.ollama_health import evaluate_status as _evaluate_ollama_status
from core import metrics_reservoir
from core import api_version as _api_version_mod
from core.password import hash_password as _core_hash_password, verify_password as _core_verify_password, needs_rehash as _core_needs_rehash, dummy_verify as _core_dummy_verify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
    static_url_path='/static')

# Reverse proxy prefix support (X-Forwarded-Prefix / X-Script-Name)
class PrefixMiddleware:
    def __init__(self, app):
        self.app = app
    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "") or environ.get("HTTP_X_SCRIPT_NAME", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix.rstrip("/")
            path_info = environ.get("PATH_INFO", "")
            if path_info.startswith(prefix):
                environ["PATH_INFO"] = path_info[len(prefix.rstrip("/")):]
        return self.app(environ, start_response)

app.wsgi_app = PrefixMiddleware(app.wsgi_app)

# Set session cookie name to avoid conflicts with other apps on same domain
app.config["SESSION_COOKIE_NAME"] = "ollama_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True  # nginx HTTPS 프록시 뒤
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB 전역 제한


@app.errorhandler(413)
def _payload_too_large(e):
    """본문 초과는 JSON으로 답한다 — 기본 HTML은 API 호출자가 파싱할 수 없다.

    ⚠️ 외부(example.org) 경유는 nginx `client_max_body_size`(10MB)가 **먼저** 걸러
    nginx HTML 413이 나간다. 그 응답은 이 핸들러에 도달하지 않으므로, 호출자는
    바디가 아니라 **상태 코드로 판별**해야 한다(2026-07-30 그룹웨어 회신 §4).
    """
    return jsonify({
        "error": "payload too large",
        "limit_bytes": app.config["MAX_CONTENT_LENGTH"],
        "hint": "이미지는 base64가 원본의 4/3로 커집니다. 외부 경유 실효 상한은 10MB입니다.",
    }), 413


from core.llm_resilience import LLMBusyError as _LLMBusyError  # noqa: E402


@app.errorhandler(_LLMBusyError)
def _llm_busy(e):
    """대기열 거절이 핸들러 밖으로 빠져나오면 429 + Retry-After(v2.125.68).

    잡히지 않은 채 올라오면 종전엔 500(RuntimeError)이었다. 거절은 고장이 아니라 '잠시 후 다시'라서
    재시도 가능 신호를 정확히 준다. 도메인 핸들러가 직접 잡는 곳(medical·counseling)은 거기서 같은 형식으로 낸다.
    ⚠️ 블루프린트 `app_errorhandler(MedicalGenerationError)`는 별개 타입이라 겹치지 않는다.
    """
    from core.llm_resilience import busy_payload
    body, headers = busy_payload(e)
    return jsonify(body), 429, headers

# Session secret key — 파일에 영속 저장 (재시작 시 세션 유지)
_SECRET_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
if os.path.exists(_SECRET_KEY_PATH):
    with open(_SECRET_KEY_PATH, "rb") as _f:
        app.secret_key = _f.read()
else:
    app.secret_key = secrets.token_bytes(32)
    with open(_SECRET_KEY_PATH, "wb") as _f:
        _f.write(app.secret_key)

def _rate_limit_key():
    """Rate limit key: API key hash or 실 클라이언트 IP.

    비-Bearer는 get_remote_address()(프록시 뒤 127.0.0.1로 붕괴=전 익명 단일버킷)가 아니라
    get_client_ip()(CF-Connecting-IP/XFF)로 키 → 로그인 브루트포스·익명 한도가 per-실클라이언트
    적용(보안검토 M7). get_client_ip는 호출시점 평가라 정의 위치 무관.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return hashlib.sha256(auth[7:].encode()).hexdigest()[:16]
    return get_client_ip()

limiter = Limiter(app=app, key_func=_rate_limit_key, default_limits=["120/minute"],
                  storage_uri="memory://", strategy="fixed-window")

# M6(v2.125.38): 고비용 엔드포인트 개별 리밋 — routes 모듈이 import 시점에 쓰도록
# **blueprint import보다 먼저** 주입한다(core/rate_limit.heavy_limit 참조).
from core import rate_limit as _rate_limit_holder
_rate_limit_holder.limiter = limiter

# ---------------------------------------------------------------------------
# Response Compression (gzip)
# ---------------------------------------------------------------------------
import gzip as _gzip

# 보안 헤더(방어심층 L5) — 엣지(nginx/CF)와 중복돼도 무해하고, 엣지를 우회하는
# 경로(SSH 터널·직접 접근)에서도 보장된다. HTML 응답에만 적용해 API JSON은 불변.
# ⚠️ 콘솔이 인라인 핸들러(onclick 111곳)·인라인 스타일(567곳)·CDN Chart.js를 쓰므로
# script/style은 'unsafe-inline'을 허용해야 한다. 그래도 frame-ancestors(클릭재킹)·
# object-src·base-uri는 실효가 있어 남긴다. 완전 강화는 인라인 제거가 선행 과제.
_CSP = ("default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")


@app.after_request
def _api_version_headers(response):
    """모든 응답이 **어느 서버가 답했는지** 스스로 말하게 한다.

    연동 시스템이 장애를 신고할 때 `/api/version`을 따로 부르면 그건 *지금* 버전이지
    문제의 응답을 낸 버전이 아니다(그 사이 배포가 있었을 수 있다).
      X-API-Version  = 서비스 빌드 버전(배포마다 오름) — 재현·신고용
      X-API-Contract = API 계약 버전(하위 호환 깨질 때만) — 연동 코드가 분기할 축
      X-API-Commit   = HEAD 커밋(있을 때만)
    브라우저 XHR이 읽으려면 CORS로 **노출 선언**이 있어야 한다(없으면 조용히 안 보인다).
    """
    try:
        for k, v in _api_version_mod.headers(VERSION).items():
            response.headers.setdefault(k, v)
        prev = response.headers.get("Access-Control-Expose-Headers", "")
        want = "X-API-Version, X-API-Contract, X-API-Commit"
        response.headers["Access-Control-Expose-Headers"] = (
            f"{prev}, {want}" if prev else want)
    except Exception as e:  # noqa: BLE001 — 헤더 부착 실패가 응답을 막지 않게
        logger.debug("api version headers skipped: %s", e)
    return response


@app.after_request
def _security_headers(response):
    try:
        if not load_config().get("security_headers_enabled", True):
            return response
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # HSTS는 HTTPS로 들어온 요청에만(평문 localhost에 걸면 개발 접근이 깨진다)
        if (request.is_secure
                or request.headers.get("X-Forwarded-Proto", "").lower() == "https"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if (response.content_type or "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", _CSP)
            response.headers.setdefault("X-Frame-Options", "DENY")
    except Exception as e:  # noqa: BLE001 — 헤더 부착 실패가 응답을 막지 않게
        logger.debug("security headers skipped: %s", e)
    return response


@app.after_request
def _compress_response(response):
    """Gzip responses >500 bytes when client supports it (skip streaming)"""
    if (response.status_code < 200 or response.status_code >= 300
            or response.direct_passthrough
            or "Content-Encoding" in response.headers
            or response.content_type and ("stream" in response.content_type
                                          or "ndjson" in response.content_type
                                          or "event-stream" in response.content_type)):
        return response
    accept_encoding = request.headers.get("Accept-Encoding", "")
    if "gzip" not in accept_encoding:
        return response
    data = response.get_data()
    if len(data) < 500:
        return response
    compressed = _gzip.compress(data, compresslevel=6)
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = len(compressed)
    response.headers["Vary"] = "Accept-Encoding"
    return response

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "queue.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("ollama-admin")

# ---------------------------------------------------------------------------
# CORS — 외부 CLI/도구에서 Swagger 스펙 및 API 호출 허용
# ---------------------------------------------------------------------------
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    cfg = load_config()
    allowed = cfg.get("allowed_origins", [])
    # /v1/* = 서드파티 OpenAI 클라이언트(OpenPencil 등) 전용 표면. 임의 오리진의 브라우저 앱이
    # API 키(Bearer)로 호출하므로 Origin을 반사한다(엔드포인트가 인증으로 보호됨). 그 외 경로는
    # 화이트리스트 유지(전역 와일드카드 미복원). credentials 미사용이라 특정 오리진 반사로 충분.
    is_v1_surface = request.path.startswith("/v1/")
    # v2.125.22(⑦): is_local_request() 반사 절 제거 — ssh -L 터널을 연 개발자의 브라우저에서
    # 임의 사이트가 localhost:8585로 요청하면 오리진이 반사되고, 로컬=인증 bypass와 결합해
    # 교차 출처 읽기가 가능했다(v2.125.18 확진·협소). 로컬 개발 UI가 교차 출처로 필요하면
    # config allowed_origins에 명시할 것(무재배포 반영).
    if origin and (origin in allowed or "*" in allowed or is_v1_surface):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    # 와일드카드 제거 — Origin 없는 요청에는 CORS 헤더 미설정
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Medical-Key, X-Tenant-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Max-Age"] = "86400"
    # 보안 헤더
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
@limiter.exempt
def handle_options(path):
    return "", 204

# ---------------------------------------------------------------------------
# Activity logging middleware — 비동기 큐 기반 (응답 경로에서 DB write 제거)
# ---------------------------------------------------------------------------
_ACTIVITY_SKIP = {"/api/health", "/metrics", "/api/activity", "/api/stats/history", "/api/server/stats",
                  # ERP 폴러가 완료 잡을 ~1/s 상시 폴링(전체 트래픽 ~48%) → api_activity 2000행
                  # 윈도우를 18분으로 압축해 관측성 훼손. 헬스체크류와 동일하게 기록 제외
                  # (포렌식은 journald 접근로그로 가능). ERP측 종료조건 수정 통지와 별개 방어.
                  "/api/notice/index-status"}
_ACTIVITY_SKIP_PREFIX = ("/swagger-static/", "/openapi.json", "/.well-known/",
                         "/docs", "/apispec", "/flasgger")

import queue as _queue_mod
_act_queue = _queue_mod.Queue(maxsize=4000)   # 넘치면 drop (서비스 무중단 우선)
_act_insert_count = 0                          # purge 주기 제어용

def _activity_writer():
    """백그라운드 스레드 — 큐에서 꺼내 배치 INSERT (요청 경로와 분리)"""
    global _act_insert_count
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-4000")
    while True:
        try:
            row = _act_queue.get(timeout=1)
        except _queue_mod.Empty:
            continue
        if row is None:
            break
        try:
            conn.execute(
                "INSERT INTO api_activity (ts,method,path,status_code,duration_ms,ip,model,request_size,response_size,user,key_name)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            _act_insert_count += 1
            # 100건마다 1회 purge — 매 요청 full scan 제거
            if _act_insert_count % 100 == 0:
                conn.execute("DELETE FROM api_activity WHERE id NOT IN "
                             "(SELECT id FROM api_activity ORDER BY ts DESC LIMIT 2000)")
            conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass

# ---------------------------------------------------------------------------
# CSRF 보호 — localhost bypass POST 요청에 Content-Type/Authorization 검증
# ---------------------------------------------------------------------------
_CSRF_SAFE_CONTENT_TYPES = {"application/json", "multipart/form-data"}
_CSRF_EXEMPT_PATHS = {"/api/service/start", "/api/service/stop", "/api/service/restart", "/login", "/logout"}

def _csrf_origin_ok():
    """세션 쿠키 mutating 요청의 Origin/Referer가 화이트리스트인지 검증(CSRF 방어심층·L4).

    SameSite=Lax가 주 방어(cross-site POST에 세션 쿠키 미전송)이나, 구형 브라우저·
    SameSite 미적용 창을 대비한 이중 방어. **세션(web_logged_in) 요청에만 적용** —
    API 클라(Bearer/X-Medical-Key)는 세션 분기에 도달하지 않아 영향 0. 브라우저가 Origin을
    자동 전송하므로 프론트 수정 불요. Origin/Referer 부재 시엔 통과(same-origin 일부·구형)."""
    if is_local_request():
        return True
    origin = request.headers.get("Origin") or request.headers.get("Referer", "")
    if not origin:
        return True
    allowed = load_config().get("allowed_origins", [])
    if "*" in allowed:
        return True
    from urllib.parse import urlparse
    o = urlparse(origin)
    o_base = f"{o.scheme}://{o.netloc}" if o.scheme and o.netloc else ""
    return any(o_base == a or origin.startswith(a + "/") or origin == a for a in allowed)


@app.before_request
def _csrf_check():
    """Mutating 요청에 CSRF 보호: JSON Content-Type 또는 Bearer 토큰 필수"""
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    # Bearer 토큰 있으면 CSRF-safe (API 클라 — 브라우저 자동 전송 아님)
    if request.headers.get("Authorization", "").startswith("Bearer "):
        return
    # 서비스 제어 등 단순 POST 예외
    if request.path in _CSRF_EXEMPT_PATHS:
        return
    # 웹 대시보드 세션 로그인 상태 — Origin 화이트리스트 검증(L4 방어심층)
    if session.get("web_logged_in"):
        if not _csrf_origin_ok():
            return jsonify({"error": "cross-origin request rejected"}), 403
        return
    # Content-Type 검증 (JSON/multipart만 허용 — form submit CSRF 방지)
    ct = (request.content_type or "").split(";")[0].strip().lower()
    if ct in _CSRF_SAFE_CONTENT_TYPES:
        return
    # Content-Type 미설정이고 body 없는 간단 POST (curl 등)는 허용
    if not request.content_length or request.content_length == 0:
        return
    return jsonify({"error": "Content-Type must be application/json or multipart/form-data"}), 415


@app.before_request
def _tab_permission_check():
    """웹 세션 비관리 역할의 탭별 권한 — 규칙표는 core/tab_permissions.py.

    예전엔 역할표가 화면 숨김에만 쓰였다(operator도 queue·chat·training API를 그대로 썼다).
    로컬(bypass)·Bearer API 키·admin 역할은 대상이 아니다. 규칙에 없는 경로(공통 읽기 등)는 통과.
    """
    # 순서: 이 훅은 전역 before_request라 블루프린트 인증(_blueprint_auth)보다 **먼저** 돈다. 그래서 여기서는
    # "로그인한 비관리 웹 세션"인지만 보고, 인증 자체(키·세션 유효성)는 뒤의 훅에 맡긴다 — 미로그인 요청은 그냥 통과시켜
    # 원래의 401 경로를 타게 한다. OPTIONS는 CORS preflight라 권한 판단 대상이 아니다.
    if not request.path.startswith("/api/") or request.method == "OPTIONS":
        return
    if not session.get("web_logged_in") or session.get("web_role") == "admin":
        return
    if request.headers.get("Authorization", "").startswith("Bearer "):
        return
    try:
        if load_config().get("local_bypass", False) and is_local_request():
            return
    except Exception:  # noqa: BLE001
        pass
    from core.tab_permissions import denied_reason
    # 권한은 로그인 때 users.db에서 세션에 실린 값이다 — 관리자가 권한을 바꿔도 **재로그인 전까지** 옛 값으로 판정한다.
    # 즉시 반영이 필요해지면 여기서 DB를 조회하도록 바꾼다(요청마다 SQLite 1회). 깨진 JSON은 빈 권한 = 전부 거부.
    try:
        perms = json.loads(session.get("web_permissions") or "{}")
    except Exception:  # noqa: BLE001
        perms = {}
    why = denied_reason(request.method, request.path, session.get("web_role"), perms)
    if why:
        logger.info("[tab-perm] 거부 %s %s user=%s role=%s (%s)", request.method, request.path,
                    session.get("web_user"), session.get("web_role"), why)
        return jsonify({"error": "forbidden", "reason": why}), 403


@app.before_request
def _inflight_start():
    """동시 처리 요청 계측 (v2.124.94). _activity_start와 달리 **전 경로**를 센다 —
    _ACTIVITY_SKIP 대상(ERP 폴링 등)도 스레드는 똑같이 점유하기 때문."""
    from flask import g
    try:
        metrics_reservoir.enter()
        g.inflight_counted = True
    except Exception:
        pass


@app.teardown_request
def _inflight_end(exc=None):
    """예외로 after_request가 건너뛰어도 반드시 반납되는 자리. enter한 요청만 반납."""
    from flask import g
    if getattr(g, "inflight_counted", False):
        try:
            metrics_reservoir.leave()
        except Exception:
            pass


@app.before_request
def _activity_start():
    from flask import g
    if request.method == "OPTIONS":
        return
    path = request.path
    if path in _ACTIVITY_SKIP:
        return
    if any(path.startswith(p) for p in _ACTIVITY_SKIP_PREFIX):
        return
    g.act_start = time.time()
    g.act_req_size = request.content_length or 0

@app.after_request
def _activity_log(response):
    from flask import g
    start = getattr(g, "act_start", None)
    if start is None:
        return response
    duration_ms = int((time.time() - start) * 1000)
    # 성능 모니터링 (P1): 인메모리 지연/상태 리저버 기록 (DB 무관·경량)
    # upstream=원격 노드 프록시 실패(_cached_proxy가 표시) → 이 서버의 5xx로 세지 않는다.
    try:
        metrics_reservoir.record(request.path, response.status_code, duration_ms,
                                 upstream=bool(getattr(g, "upstream_proxy_error", False)))
    except Exception:
        pass
    model = None
    try:
        if request.is_json and request.content_length and request.content_length < 32768:
            body = request.get_json(silent=True) or {}
            model = body.get("model") or body.get("model_name")
    except Exception as e:
        logger.debug("request body parsing failed: %s", e)
    key_name = getattr(g, "act_key_name", None)
    if key_name is None and session.get("web_logged_in"):
        key_name = "web:" + (session.get("web_user") or "admin")
    try:
        _act_queue.put_nowait((
            datetime.now(timezone.utc).isoformat() + "Z",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            get_client_ip(),
            model,
            g.act_req_size,
            response.content_length or 0,
            session.get("web_user") if request.method != "OPTIONS" else None,
            key_name,
        ))
    except _queue_mod.Full:
        pass   # 큐 포화 시 drop — 서비스 지연보다 drop 선택

    # SSE 실시간 모니터 구독자에게 전송
    _broadcast_monitor_event({
        "ts": datetime.now(timezone.utc).isoformat() + "Z",
        "method": request.method,
        "path": request.path,
        "status": response.status_code,
        "duration_ms": duration_ms,
        "ip": get_client_ip(),
        "model": model,
        "key": key_name,
        "req_size": g.act_req_size,
        "res_size": response.content_length or 0,
    })
    return response

# ---------------------------------------------------------------------------
# Real-time Monitor (SSE)
# ---------------------------------------------------------------------------
_monitor_subscribers = []          # list[_MonitorSub]
_monitor_lock = threading.Lock()
_MONITOR_MAX = 24                   # SSE 동시 구독자 상한 (스레드 풀 고갈 방지, v2.71)

# graceful drain (v2.75): 재시작 시 SSE 스레드 즉시 회수 + 재연결 폭주 차단
_SHUTDOWN = threading.Event()      # worker_int(gunicorn) → signal_shutdown()에서 set
_BOOT_TS = time.monotonic()        # 기동 시각 (단조 시계)
_MONITOR_STARTUP_GRACE = 15.0      # 기동 후 N초간 신규 SSE 거부(503) — 재연결 폭주가 초기화 중 워커에 안 쌓이게


def signal_shutdown():
    """SIGTERM/재시작 시 호출. 전 SSE 구독자에게 shutdown 통지 + 즉시 종료시켜
    gthread 스레드를 graceful_timeout(30s) 대기 없이 회수한다.
    (gunicorn.conf.py worker_int 훅에서 호출)"""
    _SHUTDOWN.set()
    msg = 'data: {"type":"shutdown"}\n\n'   # 클라는 이 신호 수신 시 긴 백오프 (즉시 재연결 폭주 금지)
    with _monitor_lock:
        for sub in list(_monitor_subscribers):
            try:
                sub.q.put_nowait(msg)
            except Exception:
                pass
            try:
                sub.q.put_nowait(None)      # generate()의 q.get 즉시 깨워 break
            except Exception:
                pass
            sub.close.set()                 # 백스톱: 큐 push 실패해도 다음 루프에서 break (dashboard SSE 포함)


class _MonitorSub:
    """SSE 구독자 1개 — 큐 + 클라이언트 IP + 종료 신호"""
    __slots__ = ("q", "ip", "close")

    def __init__(self, q, ip):
        self.q = q
        self.ip = ip
        self.close = threading.Event()


def _monitor_subscribe(q):
    """SSE 구독자 등록. 용량 초과 시 None 반환(호출측 503 처리).

    실 IP가 구분되는 경우 같은 IP의 기존 연결을 축출(IP당 1개)하여
    브라우저 자동재연결 누적으로 인한 gthread 스레드 풀 고갈을 방지한다.
    """
    if _SHUTDOWN.is_set():
        return None                       # 종료 중 — 신규 구독 거부
    if (time.monotonic() - _BOOT_TS) < _MONITOR_STARTUP_GRACE:
        return None                       # 기동 유예 — 재연결 폭주 흡수 방지(503)
    ip = get_client_ip()
    sub = _MonitorSub(q, ip)
    with _monitor_lock:
        if ip not in ("127.0.0.1", "::1", "unknown", ""):
            for old in [s for s in _monitor_subscribers if s.ip == ip]:
                old.close.set()
                try:
                    old.q.put_nowait(None)   # 대기 중인 generate() 깨워 즉시 종료
                except Exception:
                    pass
                _monitor_subscribers.remove(old)
        if len(_monitor_subscribers) >= _MONITOR_MAX:
            return None
        _monitor_subscribers.append(sub)
    return sub


def _monitor_unsubscribe(sub):
    """SSE 구독자 해제"""
    if sub is None:
        return
    with _monitor_lock:
        try:
            _monitor_subscribers.remove(sub)
        except ValueError:
            pass


def _broadcast_monitor_event(data):
    """SSE 구독자 전체에 이벤트 전송"""
    msg = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _monitor_lock:
        dead = []
        for sub in _monitor_subscribers:
            try:
                sub.q.put_nowait(msg)
            except _queue_mod.Full:
                dead.append(sub)
        for sub in dead:
            _monitor_subscribers.remove(sub)

# 큐 작업 완료/실패 시에도 이벤트 전송
def _broadcast_job_event(job, status, response_text=None, error=None, duration_ms=0):
    _broadcast_monitor_event({
        "ts": datetime.now(timezone.utc).isoformat() + "Z",
        "type": "job",
        "job_id": job.get("id", "")[:8],
        "job_type": job.get("job_type", "llm"),
        "model": job.get("model", ""),
        "status": status,
        "prompt": (job.get("prompt", "") or "")[:100],
        "response": (response_text or "")[:200],
        "error": error,
        "duration_ms": duration_ms,
    })

@app.route("/dashboard")
def dashboard_page():
    """프로덕션 서버 대시보드"""
    if not _is_monitor_authorized():
        return redirect(url_for("login", next="/dashboard"))
    try:
        return render_template_string(open(os.path.join(app.template_folder, 'dashboard.html')).read())
    except Exception:
        return render_template_string(DASHBOARD_TEMPLATE)

DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WeRU.B Server Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0a0b0f; --surface: #12141c; --card: #161822; --border: #1e2130;
  --text: #e4e4e7; --muted: #71717a; --accent: #6366f1;
  --green: #22c55e; --red: #ef4444; --yellow: #eab308; --blue: #3b82f6; --cyan: #06b6d4;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
.header { padding: 16px 24px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 18px; font-weight: 700; }
.header .meta { font-size: 12px; color: var(--muted); }
.content { padding: 20px 24px; max-width: 1200px; margin: 0 auto; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }
.card h3 { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
.chart-wrap { position: relative; height: 160px; background: var(--surface); border-radius: 8px; overflow: hidden; margin-bottom: 4px; }
.chart-wrap canvas { width: 100%; height: 160px; }
.chart-pct { position: absolute; top: 8px; right: 12px; font-size: 28px; font-weight: 800; }
.chart-label { position: absolute; top: 10px; left: 12px; font-size: 11px; color: var(--muted); }
.chart-detail { font-size: 11px; color: var(--muted); text-align: right; }
.svc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 8px; margin-bottom: 14px; }
.svc-item { text-align: center; padding: 10px 6px; background: var(--surface); border-radius: 8px; }
.svc-item .name { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.svc-item .val { font-size: 15px; font-weight: 700; }
.svc-item .val.on { color: var(--green); }
.svc-item .val.off { color: var(--red); }
.svc-item .val.warn { color: var(--yellow); }
.svc-item .val.info { color: var(--cyan); }
.svc-item .sub { font-size: 9px; color: var(--muted); margin-top: 2px; }
.proc-table { width: 100%; min-width: 700px; border-collapse: collapse; font-size: 11px; font-family: 'JetBrains Mono', monospace; }
.proc-table th { text-align: left; padding: 6px 8px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); font-size: 10px; text-transform: uppercase; }
.proc-table td { padding: 5px 8px; border-bottom: 1px solid rgba(30,33,48,0.3); }
.proc-table tr:hover { background: rgba(99,102,241,0.05); }
.proc-cpu { color: var(--cyan); font-weight: 600; }
.proc-mem { color: var(--accent); font-weight: 600; }
.proc-cmd { color: var(--muted); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meter-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; font-size: 12px; }
.meter-label { color: var(--muted); min-width: 50px; }
.meter-bar { flex: 1; height: 8px; background: var(--surface); border-radius: 4px; overflow: hidden; }
.meter-fill { height: 100%; border-radius: 4px; transition: width 0.5s; }
.meter-val { min-width: 100px; text-align: right; font-weight: 600; font-size: 11px; }
.links { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.links a { padding: 5px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; color: var(--accent); font-size: 11px; text-decoration: none; }
.links a:hover { border-color: var(--accent); }
.spike-log { max-height: 150px; overflow-y: auto; font-size: 11px; font-family: 'JetBrains Mono', monospace; }
.spike-entry { display: flex; gap: 10px; padding: 4px 0; border-bottom: 1px solid rgba(30,33,48,0.3); align-items: center; }
.spike-entry .ts { color: var(--muted); min-width: 70px; }
.spike-entry .badge { padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; }
.spike-entry .badge.crit { background: rgba(239,68,68,0.15); color: var(--red); }
.spike-entry .badge.warn { background: rgba(234,179,8,0.15); color: var(--yellow); }
.spike-entry .detail { color: var(--text); }
.spike-count { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; margin-left: 8px; }
.spike-count.has { background: rgba(239,68,68,0.15); color: var(--red); }
.spike-count.none { background: rgba(34,197,94,0.1); color: var(--green); }
@media (max-width: 768px) { .row { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>WeRU.B Server Dashboard</h1>
    <div style="display:flex;gap:6px;margin-top:4px;">
      <button id="btnGpu" onclick="switchServer('gpu')" style="padding:2px 10px;border:1px solid var(--border);background:var(--accent);color:#fff;border-radius:4px;font-size:10px;cursor:pointer;">GPU</button>
      <button id="btnMac" onclick="switchServer('mac')" style="padding:2px 10px;border:1px solid var(--border);background:var(--bg);color:var(--muted);border-radius:4px;font-size:10px;cursor:pointer;">iMac</button>
      <button id="btnProd" onclick="switchServer('prod')" style="padding:2px 10px;border:1px solid var(--border);background:var(--bg);color:var(--muted);border-radius:4px;font-size:10px;cursor:pointer;">Production</button>
      <button id="btnHis" onclick="switchServer('his')" style="padding:2px 10px;border:1px solid var(--border);background:var(--bg);color:var(--muted);border-radius:4px;font-size:10px;cursor:pointer;">HIS/PACS</button>
    </div>
    <div class="meta" id="serverInfo">Loading...</div>
  </div>
  <div style="text-align:right;">
    <div id="lastUpdate" class="meta">Loading...</div>
    <div class="meta" style="margin-top:2px;">Auto-refresh: 5s</div>
  </div>
</div>
<div class="content">
  <!-- CPU / RAM Charts -->
  <div class="row">
    <div class="card">
      <div class="chart-wrap">
        <span class="chart-label">CPU</span>
        <span class="chart-pct" id="cpuPct" style="color:var(--green);">--%</span>
        <span id="cpuPeak" style="position:absolute;top:40px;right:12px;font-size:11px;color:var(--muted);"></span>
        <canvas id="cpuCanvas"></canvas>
      </div>
      <div class="chart-detail" id="cpuDetail">--</div>
    </div>
    <div class="card">
      <div class="chart-wrap">
        <span class="chart-label">RAM</span>
        <span class="chart-pct" id="ramPct" style="color:var(--green);">--%</span>
        <span id="ramPeak" style="position:absolute;top:40px;right:12px;font-size:11px;color:var(--muted);"></span>
        <canvas id="ramCanvas"></canvas>
      </div>
      <div class="chart-detail" id="ramDetail">--</div>
    </div>
  </div>
  <!-- CPU Cores -->
  <div class="card" style="margin-bottom:16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <h3>CPU CORES <span id="coreCount" style="font-size:11px;color:var(--muted);font-weight:400;"></span></h3>
      <span id="coreHottest" style="font-size:11px;color:var(--muted);"></span>
    </div>
    <div style="position:relative;height:140px;background:var(--surface);border-radius:8px;overflow:hidden;">
      <canvas id="coresCanvas" style="width:100%;height:140px;"></canvas>
    </div>
  </div>
  <!-- GPU / Disk / Services -->
  <div class="row">
    <div class="card">
      <h3>GPU / Storage</h3>
      <div id="gpuMeters">Loading...</div>
    </div>
    <div class="card">
      <h3>Services</h3>
      <div id="svcGrid" class="svc-grid">Loading...</div>
    </div>
  </div>
  <!-- Processes -->
  <div class="card">
    <h3>Active Processes</h3>
    <div style="overflow-x:auto;">
      <table class="proc-table">
        <thead><tr><th>User</th><th>PID</th><th>CPU%</th><th>MEM%</th><th>RSS</th><th>Command</th></tr></thead>
        <tbody id="procBody"><tr><td colspan="6" style="color:var(--muted);">Loading...</td></tr></tbody>
      </table>
    </div>
  </div>
  <!-- Spike History -->
  <div class="card" style="margin-top:16px;">
    <h3>SPIKE ALERT <span id="spikeCount" class="spike-count none">0</span></h3>
    <p style="font-size:10px;color:var(--muted);margin-bottom:8px;">프로세스 CPU ≥ 50% 또는 MEM ≥ 30% 감지 시 기록 (최근 50건)</p>
    <div id="spikeLog" class="spike-log">
      <div style="color:var(--muted);font-size:11px;">감시 중...</div>
    </div>
  </div>
  <div class="links" style="justify-content:center;padding:8px 0;">
    <a href="dashboard/all">All Servers</a>
    <a href="monitor">Live Monitor</a>
    <a href="./">Admin UI</a>
    <a href="docs">Swagger API</a>
  </div>
</div>

<script>
const BASE = (() => { const p = window.location.pathname.replace(/\/dashboard\/?$/, ''); return p || ''; })();

let currentServer = 'gpu'; // 'gpu' or 'prod'
function switchServer(s) {
  currentServer = s;
  // Reset histories
  cpuHist.length = 0; ramHist.length = 0; timeLabels.length = 0;
  cpuProcHist.length = 0; ramProcHist.length = 0;
  cpuIdleHist.length = 0; ramIdleHist.length = 0;
  coreHistories.length = 0;
  cpuPeakVal = 0; ramPeakVal = 0;
  spikeHistory.length = 0; renderSpikes();
  // Button style
  ['Gpu','Mac','Prod','His'].forEach(k => {
    const key = k.toLowerCase();
    document.getElementById('btn'+k).style.background = s === key ? 'var(--accent)' : 'var(--bg)';
    document.getElementById('btn'+k).style.color = s === key ? '#fff' : 'var(--muted)';
  });
  refresh();
}
const SERVER_URLS = {
  gpu:  { stats: BASE+'/api/gpu/stats',    health: BASE+'/api/gpu/health' },
  mac:  { stats: BASE+'/api/server/stats', health: BASE+'/api/health' },
  prod: { stats: BASE+'/api/prod/stats',   health: BASE+'/api/prod/health' },
  his:  { stats: BASE+'/api/his/stats',    health: BASE+'/api/his/health' },
};
function statsUrl() { return SERVER_URLS[currentServer].stats; }
function healthUrl() { return SERVER_URLS[currentServer].health; }

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function round1(v) { return Math.round(v * 10) / 10; }

const cpuHist = [], ramHist = [], timeLabels = [];
const cpuProcHist = [], ramProcHist = [];
const cpuIdleHist = [], ramIdleHist = [];
const coreHistories = []; // Array of arrays, one per core
const CORE_COLORS = [
  '#ef4444','#f97316','#eab308','#84cc16','#22c55e','#14b8a6',
  '#06b6d4','#3b82f6','#6366f1','#8b5cf6','#a855f7','#d946ef',
  '#ec4899','#f43f5e','#fb923c','#fbbf24','#a3e635','#34d399',
  '#22d3ee','#60a5fa','#818cf8','#a78bfa','#c084fc','#e879f9',
];

function drawCoresChart() {
  const c = document.getElementById('coresCanvas');
  if (!c || coreHistories.length === 0) return;
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = c.getBoundingClientRect();
  c.width = rect.width * dpr; c.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const padL = 35, padR = 10, padT = 8, padB = 18;
  const w = rect.width - padL - padR;
  const h = rect.height - padT - padB;
  const step = w / (MAX_PTS - 1);

  ctx.clearRect(0, 0, rect.width, rect.height);

  // Y grid
  ctx.font = '9px -apple-system, sans-serif';
  ctx.textAlign = 'right';
  for (let p of [0, 50, 100]) {
    const y = padT + h - (p / 100) * h;
    ctx.strokeStyle = 'rgba(113,113,122,' + (p === 0 || p === 100 ? '0.15' : '0.06') + ')';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + w, y); ctx.stroke();
    ctx.fillStyle = 'rgba(113,113,122,0.4)';
    ctx.fillText(p + '%', padL - 4, y + 3);
  }

  // X labels
  if (timeLabels.length > 1) {
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(113,113,122,0.4)';
    const interval = Math.max(1, 12);
    for (let i = 0; i < timeLabels.length; i += interval) {
      ctx.fillText(timeLabels[i], padL + i * step, rect.height - 4);
    }
  }

  // Draw each core line
  for (let ci = 0; ci < coreHistories.length; ci++) {
    const data = coreHistories[ci];
    if (data.length < 2) continue;
    ctx.beginPath();
    for (let i = 0; i < data.length; i++) {
      const x = padL + i * step;
      const y = padT + h - (Math.min(data[i], 100) / 100) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = CORE_COLORS[ci % CORE_COLORS.length] + '90';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  // Hottest core label
  let maxCore = -1, maxVal = 0;
  for (let ci = 0; ci < coreHistories.length; ci++) {
    const last = coreHistories[ci][coreHistories[ci].length - 1] || 0;
    if (last > maxVal) { maxVal = last; maxCore = ci; }
  }
  if (maxCore >= 0 && maxVal > 5) {
    const data = coreHistories[maxCore];
    const lx = padL + (data.length - 1) * step;
    const ly = padT + h - (Math.min(maxVal, 100) / 100) * h;
    ctx.beginPath(); ctx.arc(lx, ly, 3, 0, Math.PI*2);
    ctx.fillStyle = CORE_COLORS[maxCore % CORE_COLORS.length]; ctx.fill();
    ctx.font = 'bold 9px -apple-system, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('core' + maxCore + ' ' + maxVal + '%', lx - 6, ly - 5);
  }
  document.getElementById('coreHottest').textContent = maxCore >= 0 ? 'hottest: core' + maxCore + ' ' + maxVal + '%' : '';
}
let cpuPeakVal = 0, ramPeakVal = 0;
const spikeHistory = [];
const CPU_SPIKE_THRESHOLD = 50;  // 단일 프로세스 CPU%
const MEM_SPIKE_THRESHOLD = 30;  // 단일 프로세스 MEM%
const MAX_SPIKES = 50;

function addSpike(type, level, proc) {
  const now = new Date();
  const ts = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0') + ':' + now.getSeconds().toString().padStart(2,'0');
  spikeHistory.unshift({ ts, type, level, proc });
  if (spikeHistory.length > MAX_SPIKES) spikeHistory.pop();
  renderSpikes();
}

function renderSpikes() {
  const el = document.getElementById('spikeLog');
  const cnt = document.getElementById('spikeCount');
  cnt.textContent = spikeHistory.length;
  cnt.className = 'spike-count ' + (spikeHistory.length > 0 ? 'has' : 'none');
  if (spikeHistory.length === 0) {
    el.innerHTML = '<div style="color:var(--muted);font-size:11px;">스파이크 없음</div>';
    return;
  }
  el.innerHTML = spikeHistory.map(s =>
    '<div class="spike-entry">' +
      '<span class="ts">' + s.ts + '</span>' +
      '<span class="badge ' + s.level + '">' + s.type + '</span>' +
      '<span class="detail">' + esc(s.proc) + '</span>' +
    '</div>'
  ).join('');
}
const MAX_PTS = 300; // 300 × 1s = 5분

function pctColor(p) { return p > 80 ? '#ef4444' : p > 50 ? '#eab308' : '#22c55e'; }

function timeStr(d) {
  return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0') + ':' + d.getSeconds().toString().padStart(2,'0');
}

function drawChart(id, data, times, color, overlay, idle) {
  const c = document.getElementById(id);
  if (!c) return;
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = c.getBoundingClientRect();
  c.width = rect.width * dpr; c.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const padL = 35, padR = 10, padT = 10, padB = 22;
  const w = rect.width - padL - padR;
  const h = rect.height - padT - padB;

  ctx.clearRect(0, 0, rect.width, rect.height);

  // Y-axis grid + labels (0%, 25%, 50%, 75%, 100%)
  ctx.font = '10px -apple-system, sans-serif';
  ctx.textAlign = 'right';
  for (let p of [0, 25, 50, 75, 100]) {
    const y = padT + h - (p / 100) * h;
    ctx.strokeStyle = 'rgba(113,113,122,' + (p === 0 || p === 100 ? '0.2' : '0.08') + ')';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + w, y); ctx.stroke();
    ctx.fillStyle = 'rgba(113,113,122,0.5)';
    ctx.fillText(p + '%', padL - 4, y + 3);
  }

  // X-axis time labels (every ~1분)
  if (times.length > 1) {
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(113,113,122,0.5)';
    const step = w / (MAX_PTS - 1);
    const interval = Math.max(1, Math.floor(12)); // every ~60s (12pts × 5s)
    for (let i = 0; i < times.length; i += interval) {
      const x = padL + i * step;
      ctx.fillText(times[i], x, rect.height - 4);
    }
    // always show last
    if (times.length > 1) {
      const lx = padL + (times.length - 1) * step;
      ctx.fillText(times[times.length - 1], lx, rect.height - 4);
    }
  }

  if (data.length < 2) return;
  const step = w / (MAX_PTS - 1);

  // Fill gradient
  const grad = ctx.createLinearGradient(0, padT, 0, padT + h);
  grad.addColorStop(0, color + '30'); grad.addColorStop(1, color + '03');
  ctx.beginPath();
  ctx.moveTo(padL, padT + h);
  for (let i = 0; i < data.length; i++) {
    ctx.lineTo(padL + i * step, padT + h - (data[i] / 100) * h);
  }
  ctx.lineTo(padL + (data.length - 1) * step, padT + h);
  ctx.closePath(); ctx.fillStyle = grad; ctx.fill();

  // Line
  ctx.beginPath();
  for (let i = 0; i < data.length; i++) {
    const x = padL + i * step, y = padT + h - (data[i] / 100) * h;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();

  // Current value dot + glow
  const lx = padL + (data.length-1) * step;
  const ly = padT + h - (data[data.length-1] / 100) * h;
  ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI*2);
  ctx.fillStyle = color + '40'; ctx.fill();
  ctx.beginPath(); ctx.arc(lx, ly, 3, 0, Math.PI*2);
  ctx.fillStyle = color; ctx.fill();

  // Idle line (유휴 %)
  if (idle && idle.length >= 2) {
    // Fill
    const iGrad = ctx.createLinearGradient(0, padT, 0, padT + h);
    iGrad.addColorStop(0, 'rgba(99,102,241,0.08)'); iGrad.addColorStop(1, 'rgba(99,102,241,0.01)');
    ctx.beginPath();
    ctx.moveTo(padL, padT + h);
    for (let i = 0; i < idle.length; i++) {
      ctx.lineTo(padL + i * step, padT + h - (idle[i] / 100) * h);
    }
    ctx.lineTo(padL + (idle.length - 1) * step, padT + h);
    ctx.closePath(); ctx.fillStyle = iGrad; ctx.fill();
    // Line
    ctx.beginPath();
    for (let i = 0; i < idle.length; i++) {
      const x = padL + i * step, y = padT + h - (idle[i] / 100) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = 'rgba(99,102,241,0.5)'; ctx.lineWidth = 1.5; ctx.stroke();
    // Dot + label
    const iLast = idle[idle.length-1];
    const iLx = padL + (idle.length-1) * step;
    const iLy = padT + h - (iLast / 100) * h;
    ctx.beginPath(); ctx.arc(iLx, iLy, 3, 0, Math.PI*2);
    ctx.fillStyle = 'rgba(99,102,241,0.7)'; ctx.fill();
    ctx.fillStyle = 'rgba(99,102,241,0.7)';
    ctx.font = '9px -apple-system, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('idle ' + iLast.toFixed(1) + '%', padL + 4, iLy - 4);
  }

  // Overlay line (프로세스 기반 실제 사용률)
  if (overlay && overlay.length >= 2) {
    // Fill
    const oGrad = ctx.createLinearGradient(0, padT, 0, padT + h);
    oGrad.addColorStop(0, 'rgba(239,68,68,0.15)'); oGrad.addColorStop(1, 'rgba(239,68,68,0.01)');
    ctx.beginPath();
    ctx.moveTo(padL, padT + h);
    for (let i = 0; i < overlay.length; i++) {
      ctx.lineTo(padL + i * step, padT + h - (Math.min(overlay[i], 100) / 100) * h);
    }
    ctx.lineTo(padL + (overlay.length - 1) * step, padT + h);
    ctx.closePath(); ctx.fillStyle = oGrad; ctx.fill();
    // Line
    ctx.beginPath();
    for (let i = 0; i < overlay.length; i++) {
      const x = padL + i * step, y = padT + h - (Math.min(overlay[i], 100) / 100) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = '#ef4444'; ctx.lineWidth = 1.5; ctx.setLineDash([4,3]); ctx.stroke(); ctx.setLineDash([]);
    // Peak dot
    const oLx = padL + (overlay.length-1) * step;
    const oLy = padT + h - (Math.min(overlay[overlay.length-1], 100) / 100) * h;
    ctx.beginPath(); ctx.arc(oLx, oLy, 3, 0, Math.PI*2);
    ctx.fillStyle = '#ef4444'; ctx.fill();
    // 100% 초과 시 화살표 + 실제 값 표시
    const lastOv = overlay[overlay.length-1];
    if (lastOv > 100) {
      ctx.fillStyle = '#ef4444';
      ctx.font = 'bold 10px -apple-system, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(lastOv.toFixed(0) + '%', oLx - 6, padT + 12);
    }
  }

  // Spike markers — 값이 급등한 구간에 삼각형 마커
  const chartData = overlay || data;
  for (let i = 1; i < chartData.length; i++) {
    if (chartData[i] > 15 && chartData[i] > chartData[i-1] * 2) {
      const sx = padL + i * step;
      const sy = padT + h - (Math.min(chartData[i], 100) / 100) * h;
      ctx.beginPath();
      ctx.moveTo(sx, sy - 8); ctx.lineTo(sx - 4, sy); ctx.lineTo(sx + 4, sy);
      ctx.closePath();
      ctx.fillStyle = chartData[i] > 50 ? '#ef4444' : '#eab308';
      ctx.fill();
    }
  }
}

function meterHtml(label, pct, detail) {
  const c = pctColor(pct);
  return `<div class="meter-row">
    <span class="meter-label">${label}</span>
    <div class="meter-bar"><div class="meter-fill" style="width:${Math.min(pct,100)}%;background:${c};"></div></div>
    <span class="meter-val">${detail}</span>
  </div>`;
}

async function refresh() {
  try {
    const [stats, health] = await Promise.all([
      fetch(statsUrl()).then(r=>r.json()),
      fetch(healthUrl()).then(r=>r.json()),
    ]);
    const cpu = stats.cpu||{}, ram = stats.ram||{}, gpu = stats.gpu, disk = stats.disk||{}, up = stats.uptime||{};
    const c = health.checks||{};

    // Server info
    document.getElementById('serverInfo').textContent =
      (gpu ? gpu.name + ' · ' : '') + `${cpu.cores||0} cores · ${Math.round((ram.total_mb||0)/1024)}GB RAM · Uptime ${up.display||'--'}`;

    // Time label
    const now = new Date();
    const tl = timeStr(now);
    timeLabels.push(tl); if (timeLabels.length > MAX_PTS) timeLabels.shift();

    // CPU
    const cpuP = cpu.usage_pct||0;
    const cpuProc = cpu.proc_sum_pct ? round1(cpu.proc_sum_pct / (cpu.cores||1)) : cpuP;
    const cpuIdle = round1(100 - Math.max(cpuP, cpuProc));
    cpuHist.push(cpuP); if (cpuHist.length > MAX_PTS) cpuHist.shift();
    cpuProcHist.push(cpuProc); if (cpuProcHist.length > MAX_PTS) cpuProcHist.shift();
    cpuIdleHist.push(cpuIdle); if (cpuIdleHist.length > MAX_PTS) cpuIdleHist.shift();
    const cpuMax = Math.max(cpuP, cpuProc);
    if (cpuMax > cpuPeakVal) cpuPeakVal = round1(cpuMax);
    document.getElementById('cpuPct').textContent = cpuProc.toFixed(1) + '%';
    document.getElementById('cpuPct').style.color = pctColor(cpuProc);
    document.getElementById('cpuPeak').textContent = `peak ${cpuPeakVal}%` + (cpu.peak_proc_pct ? ` · top ${cpu.peak_proc_pct}%` : '');
    document.getElementById('cpuDetail').textContent = `idle ${cpuIdle}% · load: ${cpu.load_1m||0} / ${cpu.load_5m||0} / ${cpu.load_15m||0} · ${cpu.cores||0} cores`;
    drawChart('cpuCanvas', cpuHist, timeLabels, pctColor(cpuP), cpuProcHist, cpuIdleHist);

    // Per-core history
    const perCore = cpu.per_core || [];
    document.getElementById('coreCount').textContent = perCore.length ? perCore.length + ' cores' : '';
    for (let ci = 0; ci < perCore.length; ci++) {
      if (!coreHistories[ci]) coreHistories[ci] = [];
      coreHistories[ci].push(perCore[ci]);
      if (coreHistories[ci].length > MAX_PTS) coreHistories[ci].shift();
    }
    drawCoresChart();

    // RAM
    const ramP = ram.usage_pct||0;
    const ramProc = ram.proc_sum_pct || ramP;
    const ramIdle = round1(100 - ramP);
    ramHist.push(ramP); if (ramHist.length > MAX_PTS) ramHist.shift();
    ramProcHist.push(ramProc); if (ramProcHist.length > MAX_PTS) ramProcHist.shift();
    ramIdleHist.push(ramIdle); if (ramIdleHist.length > MAX_PTS) ramIdleHist.shift();
    if (ramP > ramPeakVal) ramPeakVal = round1(ramP);
    document.getElementById('ramPct').textContent = ramP + '%';
    document.getElementById('ramPct').style.color = pctColor(ramP);
    document.getElementById('ramPeak').textContent = `peak ${ramPeakVal}%` + (ram.peak_proc_pct ? ` · top ${ram.peak_proc_pct}%` : '');
    document.getElementById('ramDetail').textContent = `idle ${ramIdle}% · ${Math.round((ram.used_mb||0)/1024)}G used / ${Math.round((ram.total_mb||0)/1024)}G total · ${Math.round((ram.avail_mb||0)/1024)}G available`;
    drawChart('ramCanvas', ramHist, timeLabels, pctColor(ramP), ramProcHist, ramIdleHist);

    // GPU / Disk meters
    let meters = '';
    if (gpu) {
      meters += meterHtml('GPU', gpu.utilization_pct||0, `${gpu.utilization_pct}% · ${gpu.temperature_c}°C`);
      meters += meterHtml('VRAM', gpu.memory_usage_pct||0, `${Math.round(gpu.memory_used_mb/1024*10)/10}G / ${Math.round(gpu.memory_total_mb/1024)}G`);
    }
    meters += meterHtml('Disk', disk.usage_pct||0, `${Math.round(disk.free_gb||0)}G free / ${Math.round(disk.total_gb||0)}G`);
    document.getElementById('gpuMeters').innerHTML = meters;

    // Services
    const sv = (k, ok, on, off, sub) =>
      `<div class="svc-item"><div class="name">${k}</div><span class="val ${ok?'on':'off'}">${ok?(on||'ON'):(off||'OFF')}</span>${sub?'<div class="sub">'+sub+'</div>':''}</div>`;
    document.getElementById('svcGrid').innerHTML =
      sv('Ollama', c.ollama?.ok, 'ON', 'OFF') +
      sv('Worker', c.worker?.ok, c.worker?.paused?'PAUSED':'ON') +
      sv('Tunnel', c.tunnel?.ok, 'UP', 'DOWN') +
      sv('Queue', true, (c.queue?.pending||0)+(c.queue?.running||0)||'Idle', '', `P:${c.queue?.pending||0} R:${c.queue?.running||0}`) +
      sv('DB', c.database?.ok, c.database?.size_mb+'MB') +
      sv('Uptime', true, up.display||'--');

    // Spike detection — 프로세스 기반
    const procs = stats.processes || [];
    for (const p of procs) {
      if (p.cpu >= CPU_SPIKE_THRESHOLD) {
        addSpike('CPU ' + p.cpu.toFixed(1) + '%', p.cpu >= 80 ? 'crit' : 'warn', p.user + ' · ' + p.command);
      }
      if (p.mem >= MEM_SPIKE_THRESHOLD) {
        addSpike('MEM ' + p.mem.toFixed(1) + '%', p.mem >= 50 ? 'crit' : 'warn', p.user + ' · ' + p.command);
      }
    }

    // Processes
    const tbody = document.getElementById('procBody');
    if (procs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted);">No active processes</td></tr>';
    } else {
      tbody.innerHTML = procs.map(p => `<tr>
        <td>${esc(p.user)}</td><td>${p.pid}</td>
        <td class="proc-cpu">${p.cpu.toFixed(1)}</td>
        <td class="proc-mem">${p.mem.toFixed(1)}</td>
        <td>${p.rss_mb}M</td>
        <td class="proc-cmd" title="${esc(p.command)}">${esc(p.command)}</td>
      </tr>`).join('');
    }

    document.getElementById('lastUpdate').textContent = 'Updated: ' + new Date().toLocaleTimeString('ko-KR', {hour12:false});
  } catch(e) {
    document.getElementById('serverInfo').textContent = 'Connection error';
  }
}

let refreshTimer;
function startRefresh() { refresh(); refreshTimer = setInterval(() => { if (!document.hidden) refresh(); }, 5000); }
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearInterval(refreshTimer); }
  else { startRefresh(); }
});
startRefresh();

window.addEventListener('resize', () => {
  drawChart('cpuCanvas', cpuHist, timeLabels, pctColor(cpuHist[cpuHist.length-1]||0), cpuProcHist, cpuIdleHist);
  drawChart('ramCanvas', ramHist, timeLabels, pctColor(ramHist[ramHist.length-1]||0), ramProcHist, ramIdleHist);
  drawCoresChart();
});
</script>
</body>
</html>"""

def _is_monitor_authorized():
    """모니터 접근 권한 확인 (세션 로그인 필수, 프록시 경유도 체크)"""
    if session.get("web_logged_in"):
        return True
    # X-Real-IP가 있으면 프록시 경유 = 외부 접근 → 인증 필요
    real_ip = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For")
    if real_ip:
        return False
    # 직접 로컬 접속만 허용 — 공개판: 로컬 우회를 켰을 때만
    if not load_config().get("local_bypass", False):
        return False
    ip = request.remote_addr or ""
    return ip in ("127.0.0.1", "::1")


@app.route("/dashboard/all")
def dashboard_all_page():
    """GPU + Production 듀얼 서버 대시보드"""
    if not _is_monitor_authorized():
        return redirect(url_for("login", next="/dashboard/all"))
    try:
        return render_template_string(open(os.path.join(app.template_folder, 'dashboard_all.html')).read())
    except Exception:
        return render_template_string(DASHBOARD_ALL_TEMPLATE)

DASHBOARD_ALL_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WeRU.B All Servers</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0a0b0f; --surface: #12141c; --card: #161822; --border: #1e2130;
  --text: #e4e4e7; --muted: #71717a; --accent: #6366f1;
  --green: #22c55e; --red: #ef4444; --yellow: #eab308; --blue: #3b82f6; --cyan: #06b6d4;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
.header { padding: 12px 20px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 16px; font-weight: 700; }
.header .meta { font-size: 11px; color: var(--muted); }
.content { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 20px; max-width: 1800px; margin: 0 auto; }
@media (max-width: 900px) { .content { grid-template-columns: 1fr; } }
.server-panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.server-title { font-size: 14px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.server-title .dot { width: 8px; height: 8px; border-radius: 50%; }
.server-title .dot.ok { background: var(--green); }
.server-title .dot.err { background: var(--red); }
.chart-wrap { position: relative; height: 120px; background: var(--surface); border-radius: 8px; overflow: hidden; margin-bottom: 8px; }
.chart-wrap canvas { width: 100%; height: 120px; }
.chart-pct { position: absolute; top: 6px; right: 10px; font-size: 22px; font-weight: 800; }
.chart-label { position: absolute; top: 6px; left: 10px; font-size: 10px; color: var(--muted); }
.chart-peak { position: absolute; top: 30px; right: 10px; font-size: 10px; color: var(--muted); }
.detail { font-size: 10px; color: var(--muted); text-align: right; margin-bottom: 10px; }
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 10px; }
.metric { text-align: center; padding: 8px 4px; background: var(--surface); border-radius: 6px; }
.metric .val { font-size: 14px; font-weight: 700; }
.metric .val.on { color: var(--green); }
.metric .val.off { color: var(--red); }
.metric .val.info { color: var(--cyan); }
.metric .lbl { font-size: 9px; color: var(--muted); margin-top: 2px; }
.proc-table { width: 100%; border-collapse: collapse; font-size: 10px; font-family: 'JetBrains Mono', monospace; }
.proc-table th { text-align: left; padding: 4px 6px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); font-size: 9px; text-transform: uppercase; }
.proc-table td { padding: 3px 6px; border-bottom: 1px solid rgba(30,33,48,0.2); }
.proc-cpu { color: var(--cyan); font-weight: 600; }
.proc-mem { color: var(--accent); font-weight: 600; }
.proc-cmd { color: var(--muted); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meter-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 11px; }
.meter-label { color: var(--muted); min-width: 40px; }
.meter-bar { flex: 1; height: 6px; background: var(--surface); border-radius: 3px; overflow: hidden; }
.meter-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
.meter-val { min-width: 90px; text-align: right; font-weight: 600; font-size: 10px; }
.links { display: flex; gap: 8px; justify-content: center; padding: 8px 0; }
.links a { padding: 4px 10px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; color: var(--accent); font-size: 10px; text-decoration: none; }
.links a:hover { border-color: var(--accent); }
.spike-entry { font-size: 10px; padding: 2px 0; border-bottom: 1px solid rgba(30,33,48,0.2); display: flex; gap: 6px; }
.spike-entry .ts { color: var(--muted); min-width: 55px; }
.spike-entry .badge { padding: 0 4px; border-radius: 3px; font-size: 9px; font-weight: 700; }
.badge.crit { background: rgba(239,68,68,0.15); color: var(--red); }
.badge.warn { background: rgba(234,179,8,0.15); color: var(--yellow); }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>WeRU.B All Servers</h1>
    <div class="meta" id="updateTime">Loading...</div>
  </div>
  <div style="text-align:right;">
    <div class="meta">Auto-refresh: 5s</div>
  </div>
</div>

<div class="content">
  <!-- GPU Server -->
  <div class="server-panel" id="panelGpu">
    <div class="server-title"><span class="dot" id="gpuDot"></span>iMac <span style="font-size:11px;color:var(--muted);font-weight:400;" id="gpuInfo"></span></div>
    <div class="chart-wrap">
      <span class="chart-label">CPU</span>
      <span class="chart-pct" id="gpuCpuPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="gpuCpuPeak"></span>
      <canvas id="gpuCpuCanvas"></canvas>
    </div>
    <div class="detail" id="gpuCpuDetail">--</div>
    <div class="chart-wrap">
      <span class="chart-label">RAM</span>
      <span class="chart-pct" id="gpuRamPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="gpuRamPeak"></span>
      <canvas id="gpuRamCanvas"></canvas>
    </div>
    <div class="detail" id="gpuRamDetail">--</div>
    <div id="gpuMeters"></div>
    <div class="metrics" id="gpuSvc"></div>
    <div style="margin-top:8px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">Top Processes</div>
      <table class="proc-table"><tbody id="gpuProcs"><tr><td colspan="5" style="color:var(--muted);">Loading...</td></tr></tbody></table>
    </div>
    <div id="gpuSpikes" style="margin-top:8px;max-height:80px;overflow-y:auto;"></div>
  </div>

  <!-- iMac Server -->
  <div class="server-panel" id="panelMac">
    <div class="server-title"><span class="dot" id="macDot"></span>iMac M1 <span style="font-size:11px;color:var(--muted);font-weight:400;" id="macInfo"></span></div>
    <div class="chart-wrap">
      <span class="chart-label">CPU</span>
      <span class="chart-pct" id="macCpuPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="macCpuPeak"></span>
      <canvas id="macCpuCanvas"></canvas>
    </div>
    <div class="detail" id="macCpuDetail">--</div>
    <div class="chart-wrap">
      <span class="chart-label">RAM</span>
      <span class="chart-pct" id="macRamPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="macRamPeak"></span>
      <canvas id="macRamCanvas"></canvas>
    </div>
    <div class="detail" id="macRamDetail">--</div>
    <div id="macMeters"></div>
    <div class="metrics" id="macSvc"></div>
    <div style="margin-top:8px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">Top Processes</div>
      <table class="proc-table"><tbody id="macProcs"><tr><td colspan="5" style="color:var(--muted);">Loading...</td></tr></tbody></table>
    </div>
    <div id="macSpikes" style="margin-top:8px;max-height:80px;overflow-y:auto;"></div>
  </div>

  <!-- Production Server -->
  <div class="server-panel" id="panelProd">
    <div class="server-title"><span class="dot" id="prodDot"></span>Production <span style="font-size:11px;color:var(--muted);font-weight:400;" id="prodInfo"></span></div>
    <div class="chart-wrap">
      <span class="chart-label">CPU</span>
      <span class="chart-pct" id="prodCpuPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="prodCpuPeak"></span>
      <canvas id="prodCpuCanvas"></canvas>
    </div>
    <div class="detail" id="prodCpuDetail">--</div>
    <div class="chart-wrap">
      <span class="chart-label">RAM</span>
      <span class="chart-pct" id="prodRamPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="prodRamPeak"></span>
      <canvas id="prodRamCanvas"></canvas>
    </div>
    <div class="detail" id="prodRamDetail">--</div>
    <div id="prodMeters"></div>
    <div class="metrics" id="prodSvc"></div>
    <div style="margin-top:8px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">Top Processes</div>
      <table class="proc-table"><tbody id="prodProcs"><tr><td colspan="5" style="color:var(--muted);">Loading...</td></tr></tbody></table>
    </div>
    <div id="prodSpikes" style="margin-top:8px;max-height:80px;overflow-y:auto;"></div>
  </div>

  <!-- HIS/PACS Server -->
  <div class="server-panel" id="panelHis">
    <div class="server-title"><span class="dot" id="hisDot"></span>HIS/PACS <span style="font-size:11px;color:var(--muted);font-weight:400;" id="hisInfo"></span></div>
    <div class="chart-wrap">
      <span class="chart-label">CPU</span>
      <span class="chart-pct" id="hisCpuPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="hisCpuPeak"></span>
      <canvas id="hisCpuCanvas"></canvas>
    </div>
    <div class="detail" id="hisCpuDetail">--</div>
    <div class="chart-wrap">
      <span class="chart-label">RAM</span>
      <span class="chart-pct" id="hisRamPct" style="color:var(--green);">--%</span>
      <span class="chart-peak" id="hisRamPeak"></span>
      <canvas id="hisRamCanvas"></canvas>
    </div>
    <div class="detail" id="hisRamDetail">--</div>
    <div id="hisMeters"></div>
    <div class="metrics" id="hisSvc"></div>
    <div style="margin-top:8px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">Top Processes</div>
      <table class="proc-table"><tbody id="hisProcs"><tr><td colspan="5" style="color:var(--muted);">Loading...</td></tr></tbody></table>
    </div>
    <div id="hisSpikes" style="margin-top:8px;max-height:80px;overflow-y:auto;"></div>
  </div>
</div>

<div class="links">
  <a href="dashboard">Single View</a>
  <a href="monitor">Live Monitor</a>
  <a href="./">Admin UI</a>
</div>

<script>
const BASE = (() => { const p = window.location.pathname.replace(/\/dashboard\/all\/?$/, ''); return p || ''; })();
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function round1(v) { return Math.round(v * 10) / 10; }
function pctColor(p) { return p > 80 ? '#ef4444' : p > 50 ? '#eab308' : '#22c55e'; }
function timeStr(d) { return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0')+':'+d.getSeconds().toString().padStart(2,'0'); }

const MAX_PTS = 300;
const servers = {
  gpu:  { cpuHist:[], ramHist:[], cpuProcHist:[], ramProcHist:[], times:[], cpuPeak:0, ramPeak:0, spikes:[], url: BASE+'/api/gpu/stats', healthUrl: BASE+'/api/gpu/health' },
  mac:  { cpuHist:[], ramHist:[], cpuProcHist:[], ramProcHist:[], times:[], cpuPeak:0, ramPeak:0, spikes:[], url: BASE+'/api/server/stats', healthUrl: BASE+'/api/health' },
  prod: { cpuHist:[], ramHist:[], cpuProcHist:[], ramProcHist:[], times:[], cpuPeak:0, ramPeak:0, spikes:[], url: BASE+'/api/prod/stats', healthUrl: BASE+'/api/prod/health' },
  his:  { cpuHist:[], ramHist:[], cpuProcHist:[], ramProcHist:[], times:[], cpuPeak:0, ramPeak:0, spikes:[], url: BASE+'/api/his/stats', healthUrl: BASE+'/api/his/health' },
};

function drawMiniChart(id, data, color, overlay) {
  const c = document.getElementById(id);
  if (!c) return;
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = c.getBoundingClientRect();
  c.width = rect.width * dpr; c.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const padL = 28, padR = 6, padT = 6, padB = 4;
  const w = rect.width - padL - padR, h = rect.height - padT - padB;
  ctx.clearRect(0, 0, rect.width, rect.height);

  // Y grid
  ctx.font = '9px -apple-system, sans-serif';
  ctx.textAlign = 'right';
  for (let p of [0, 50, 100]) {
    const y = padT + h - (p/100)*h;
    ctx.strokeStyle = 'rgba(113,113,122,0.08)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL+w, y); ctx.stroke();
    ctx.fillStyle = 'rgba(113,113,122,0.4)';
    ctx.fillText(p+'%', padL-3, y+3);
  }

  if (data.length < 2) return;
  const step = w / (MAX_PTS - 1);

  // Main fill + line
  const grad = ctx.createLinearGradient(0, padT, 0, padT+h);
  grad.addColorStop(0, color+'30'); grad.addColorStop(1, color+'03');
  ctx.beginPath(); ctx.moveTo(padL, padT+h);
  for (let i=0; i<data.length; i++) ctx.lineTo(padL+i*step, padT+h-(data[i]/100)*h);
  ctx.lineTo(padL+(data.length-1)*step, padT+h);
  ctx.closePath(); ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath();
  for (let i=0; i<data.length; i++) { const x=padL+i*step, y=padT+h-(data[i]/100)*h; i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); }
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();

  // Overlay (proc)
  if (overlay && overlay.length >= 2) {
    ctx.beginPath();
    for (let i=0; i<overlay.length; i++) { const x=padL+i*step, y=padT+h-(Math.min(overlay[i],100)/100)*h; i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); }
    ctx.strokeStyle = '#ef4444'; ctx.lineWidth = 1; ctx.setLineDash([3,2]); ctx.stroke(); ctx.setLineDash([]);
  }

  // Current dot
  const lx = padL+(data.length-1)*step, ly = padT+h-(data[data.length-1]/100)*h;
  ctx.beginPath(); ctx.arc(lx, ly, 3, 0, Math.PI*2); ctx.fillStyle = color; ctx.fill();
}

function meterHtml(label, pct, detail) {
  const c = pctColor(pct);
  return `<div class="meter-row"><span class="meter-label">${label}</span><div class="meter-bar"><div class="meter-fill" style="width:${Math.min(pct,100)}%;background:${c};"></div></div><span class="meter-val">${detail}</span></div>`;
}

function svcHtml(name, ok, val, sub) {
  return `<div class="metric"><div class="val ${ok?'on':'off'}">${val||'OFF'}</div><div class="lbl">${name}${sub?' · '+sub:''}</div></div>`;
}

async function refreshServer(key, prefix) {
  const s = servers[key];
  try {
    const [stats, health] = await Promise.all([
      fetch(s.url).then(r=>r.json()),
      fetch(s.healthUrl).then(r=>r.json()),
    ]);
    const cpu = stats.cpu||{}, ram = stats.ram||{}, gpu = stats.gpu, disk = stats.disk||{}, up = stats.uptime||{};
    const c = health.checks||{};

    // Status dot
    document.getElementById(prefix+'Dot').className = 'dot ' + (health.healthy !== false ? 'ok' : 'err');
    document.getElementById(prefix+'Info').textContent = (gpu ? gpu.name+' · ' : '') + (cpu.cores||0) + ' cores · ' + Math.round((ram.total_mb||0)/1024) + 'GB';

    // CPU
    const cpuP = cpu.usage_pct||0;
    const cpuProc = cpu.proc_sum_pct ? round1(cpu.proc_sum_pct / (cpu.cores||1)) : cpuP;
    s.cpuHist.push(cpuP); if (s.cpuHist.length > MAX_PTS) s.cpuHist.shift();
    s.cpuProcHist.push(cpuProc); if (s.cpuProcHist.length > MAX_PTS) s.cpuProcHist.shift();
    const cpuMax = Math.max(cpuP, cpuProc);
    if (cpuMax > s.cpuPeak) s.cpuPeak = round1(cpuMax);
    document.getElementById(prefix+'CpuPct').textContent = cpuProc.toFixed(1)+'%';
    document.getElementById(prefix+'CpuPct').style.color = pctColor(cpuProc);
    document.getElementById(prefix+'CpuPeak').textContent = 'peak '+s.cpuPeak+'%'+(cpu.peak_proc_pct?' · top '+cpu.peak_proc_pct+'%':'');
    document.getElementById(prefix+'CpuDetail').textContent = 'idle '+round1(100-cpuMax)+'% · load: '+cpu.load_1m+' / '+cpu.load_5m+' / '+cpu.load_15m+' · '+cpu.cores+' cores';
    drawMiniChart(prefix+'CpuCanvas', s.cpuHist, pctColor(cpuP), s.cpuProcHist);

    // RAM
    const ramP = ram.usage_pct||0;
    s.ramHist.push(ramP); if (s.ramHist.length > MAX_PTS) s.ramHist.shift();
    if (ramP > s.ramPeak) s.ramPeak = round1(ramP);
    document.getElementById(prefix+'RamPct').textContent = ramP+'%';
    document.getElementById(prefix+'RamPct').style.color = pctColor(ramP);
    document.getElementById(prefix+'RamPeak').textContent = 'peak '+s.ramPeak+'%'+(ram.peak_proc_pct?' · top '+ram.peak_proc_pct+'%':'');
    document.getElementById(prefix+'RamDetail').textContent = 'idle '+round1(100-ramP)+'% · '+Math.round((ram.used_mb||0)/1024)+'G / '+Math.round((ram.total_mb||0)/1024)+'G';
    drawMiniChart(prefix+'RamCanvas', s.ramHist, pctColor(ramP));

    // GPU / Disk meters
    let meters = '';
    if (gpu) {
      meters += meterHtml('GPU', gpu.utilization_pct||0, gpu.utilization_pct+'% · '+gpu.temperature_c+'°C');
      meters += meterHtml('VRAM', gpu.memory_usage_pct||0, Math.round(gpu.memory_used_mb/1024*10)/10+'G / '+Math.round(gpu.memory_total_mb/1024)+'G');
    }
    meters += meterHtml('Disk', disk.usage_pct||0, Math.round(disk.free_gb||0)+'G free / '+Math.round(disk.total_gb||0)+'G');
    document.getElementById(prefix+'Meters').innerHTML = meters;

    // Services
    let svc = '';
    if (c.ollama) svc += svcHtml('Ollama', c.ollama.ok, c.ollama.ok?'ON':'OFF');
    if (c.nextjs) svc += svcHtml('Next.js', c.nextjs.ok, c.nextjs.ok?'ON':'OFF');
    if (c.nginx) svc += svcHtml('Nginx', c.nginx.ok, c.nginx.ok?'ON':'OFF');
    if (c.redis) svc += svcHtml('Redis', c.redis.ok, c.redis.ok?'ON':'OFF');
    if (c.worker) svc += svcHtml('Worker', c.worker.ok, c.worker.ok?'ON':'OFF');
    if (c.tunnel) svc += svcHtml('Tunnel', c.tunnel.ok, c.tunnel.ok?'UP':'DOWN');
    if (c.database) svc += svcHtml('DB', c.database.ok, (c.database.size_mb||'?')+'MB');
    svc += svcHtml('Uptime', true, up.display||'--');
    document.getElementById(prefix+'Svc').innerHTML = svc;

    // Processes (top 8)
    const procs = (stats.processes||[]).slice(0, 8);
    const tbody = document.getElementById(prefix+'Procs');
    if (procs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted);">No processes</td></tr>';
    } else {
      tbody.innerHTML = procs.map(p => `<tr><td>${esc(p.user)}</td><td class="proc-cpu">${p.cpu.toFixed(1)}</td><td class="proc-mem">${p.mem.toFixed(1)}</td><td>${p.rss_mb}M</td><td class="proc-cmd" title="${esc(p.command)}">${esc(p.command)}</td></tr>`).join('');
    }

    // Spikes
    for (const p of procs) {
      if (p.cpu >= 50) s.spikes.unshift({ ts: timeStr(new Date()), txt: 'CPU '+p.cpu.toFixed(1)+'% · '+p.user+' · '+p.command, level: p.cpu>=80?'crit':'warn' });
      if (p.mem >= 30) s.spikes.unshift({ ts: timeStr(new Date()), txt: 'MEM '+p.mem.toFixed(1)+'% · '+p.user+' · '+p.command, level: p.mem>=50?'crit':'warn' });
    }
    if (s.spikes.length > 20) s.spikes.length = 20;
    document.getElementById(prefix+'Spikes').innerHTML = s.spikes.map(sp => '<div class="spike-entry"><span class="ts">'+sp.ts+'</span><span class="badge '+sp.level+'">'+esc(sp.txt)+'</span></div>').join('');

  } catch(e) {
    document.getElementById(prefix+'Dot').className = 'dot err';
    document.getElementById(prefix+'Info').textContent = 'Connection error';
  }
}

async function refresh() {
  await Promise.all([refreshServer('gpu','gpu'), refreshServer('mac','mac'), refreshServer('prod','prod'), refreshServer('his','his')]);
  document.getElementById('updateTime').textContent = 'Updated: ' + new Date().toLocaleTimeString('ko-KR',{hour12:false});
}

let timer;
function start() { refresh(); timer = setInterval(() => { if (!document.hidden) refresh(); }, 5000); }
document.addEventListener('visibilitychange', () => { if (document.hidden) clearInterval(timer); else start(); });
start();

window.addEventListener('resize', () => {
  for (const [key, prefix] of [['gpu','gpu'],['mac','mac'],['prod','prod'],['his','his']]) {
    const s = servers[key];
    drawMiniChart(prefix+'CpuCanvas', s.cpuHist, pctColor(s.cpuHist[s.cpuHist.length-1]||0), s.cpuProcHist);
    drawMiniChart(prefix+'RamCanvas', s.ramHist, pctColor(s.ramHist[s.ramHist.length-1]||0));
  }
});
</script>
</body>
</html>"""

@app.route("/monitor")
def monitor_page():
    """실시간 API 모니터링 페이지 (로그인 필요)"""
    if not _is_monitor_authorized():
        return redirect(url_for("login", next="/monitor"))
    try:
        return render_template_string(open(os.path.join(app.template_folder, 'monitor.html')).read())
    except Exception:
        return render_template_string(MONITOR_TEMPLATE)


@app.route("/eval-monitor")
def eval_monitor_page():
    """LLM 품질 평가 전용 모니터 (새창) — 세밀 지표·시계열·질문별 진단 자동 갱신 (로그인 필요)"""
    if not _is_monitor_authorized():
        return redirect(url_for("login", next="/eval-monitor"))
    from flask import send_file
    return send_file(os.path.join(app.template_folder, 'eval_monitor.html'))

@app.route("/api/monitor/stream")
def monitor_stream():
    """SSE stream for real-time monitoring (로그인 필요)"""
    if not _is_monitor_authorized():
        return jsonify({"error": "unauthorized"}), 401
    q = _queue_mod.Queue(maxsize=200)
    sub = _monitor_subscribe(q)
    if sub is None:
        return jsonify({"error": "monitor capacity reached"}), 503

    def generate():
        try:
            yield "data: {\"type\":\"connected\"}\n\n"
            while not sub.close.is_set():
                if _SHUTDOWN.is_set():     # 재시작 드레인 — 스레드 즉시 회수
                    break
                try:
                    msg = q.get(timeout=30)
                    if msg is None:        # 종료 신호 (IP 축출 / shutdown sentinel)
                        break
                    yield msg
                except _queue_mod.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            logger.debug("SSE monitor client disconnected")
        finally:
            _monitor_unsubscribe(sub)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

MONITOR_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WeRU.B Ollama Monitor</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0a0b0f; --surface: #12141c; --border: #1e2130;
  --text: #e4e4e7; --muted: #71717a; --accent: #6366f1;
  --green: #22c55e; --red: #ef4444; --yellow: #eab308; --blue: #3b82f6;
}
body { font-family: 'JetBrains Mono', 'Fira Code', monospace; background: var(--bg); color: var(--text); min-height: 100vh; }
.header { padding: 12px 20px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 16px; font-weight: 600; }
.header .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; }
.badge-online { background: rgba(34,197,94,0.15); color: var(--green); }
.badge-offline { background: rgba(239,68,68,0.15); color: var(--red); }
.stats { display: flex; gap: 20px; padding: 10px 20px; background: var(--surface); border-bottom: 1px solid var(--border); font-size: 12px; color: var(--muted); }
.stats span { color: var(--text); font-weight: 600; }
.controls { padding: 8px 20px; display: flex; gap: 8px; align-items: center; background: var(--surface); border-bottom: 1px solid var(--border); }
.controls button { padding: 4px 12px; border: 1px solid var(--border); background: var(--bg); color: var(--text); border-radius: 4px; cursor: pointer; font-size: 11px; font-family: inherit; }
.controls button:hover { border-color: var(--accent); }
.controls button.active { background: var(--accent); border-color: var(--accent); }
.controls input { padding: 4px 8px; border: 1px solid var(--border); background: var(--bg); color: var(--text); border-radius: 4px; font-size: 11px; font-family: inherit; width: 200px; }
.log-container { padding: 0; overflow-y: auto; height: calc(100vh - 290px); }
.log-entry { padding: 6px 20px; border-bottom: 1px solid var(--border); font-size: 12px; line-height: 1.6; display: flex; gap: 12px; align-items: flex-start; transition: background 0.3s; }
.log-entry:hover { background: rgba(99,102,241,0.05); }
.log-entry.new { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { background: rgba(99,102,241,0.15); } to { background: transparent; } }
.log-entry .ts { color: var(--muted); min-width: 85px; flex-shrink: 0; }
.log-entry .method { min-width: 50px; font-weight: 700; flex-shrink: 0; }
.log-entry .method.GET { color: var(--green); }
.log-entry .method.POST { color: var(--blue); }
.log-entry .method.DELETE { color: var(--red); }
.log-entry .path { color: var(--text); min-width: 250px; }
.log-entry .status { min-width: 35px; font-weight: 700; flex-shrink: 0; }
.log-entry .status.s2 { color: var(--green); }
.log-entry .status.s3 { color: var(--yellow); }
.log-entry .status.s4 { color: var(--red); }
.log-entry .status.s5 { color: var(--red); }
.log-entry .dur { color: var(--muted); min-width: 60px; text-align: right; flex-shrink: 0; }
.log-entry .model { color: var(--accent); min-width: 120px; flex-shrink: 0; }
.log-entry .meta { color: var(--muted); font-size: 11px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.log-entry.job { background: rgba(99,102,241,0.03); }
.log-entry.job .ts { color: var(--accent); }
.job-status { padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 700; }
.job-status.completed { background: rgba(34,197,94,0.15); color: var(--green); }
.job-status.failed { background: rgba(239,68,68,0.15); color: var(--red); }
.job-status.started { background: rgba(59,130,246,0.15); color: var(--blue); }
.job-prompt { color: var(--yellow); opacity: 0.7; }
.job-response { color: var(--green); opacity: 0.8; }
.empty { padding: 40px; text-align: center; color: var(--muted); font-size: 14px; }
.status-bar { display: flex; gap: 0; padding: 0; background: var(--surface); border-bottom: 1px solid var(--border); overflow-x: auto; }
.status-card { padding: 10px 16px; border-right: 1px solid var(--border); min-width: 120px; }
.status-card .label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }
.status-card .value { font-size: 15px; font-weight: 700; }
.status-card .value.on { color: var(--green); }
.status-card .value.off { color: var(--red); }
.status-card .value.warn { color: var(--yellow); }
.status-card .sub { font-size: 10px; color: var(--muted); margin-top: 1px; }
</style>
</head>
<body>
<div class="header">
  <h1>🔴 WeRU.B Ollama Monitor</h1>
  <div>
    <span id="connBadge" class="badge badge-offline">Connecting...</span>
    <span style="font-size:11px;color:var(--muted);margin-left:8px;" id="entryCount">0 entries</span>
  </div>
</div>
<div style="font-size:11px;color:var(--muted);padding:6px 16px 2px;background:var(--surface);border-bottom:1px solid var(--border);font-weight:700;letter-spacing:1px;">
  🖥 GPU SERVER <span style="font-weight:400;color:var(--accent);">RTX 5080</span>
</div>
<div class="status-bar" id="statusBar">
  <div class="status-card">
    <div class="label">Ollama</div>
    <div class="value off" id="sOllama">--</div>
    <div class="sub" id="sOllamaModels"></div>
  </div>
  <div class="status-card">
    <div class="label">ComfyUI</div>
    <div class="value off" id="sComfyUI">--</div>
    <div class="sub" id="sComfyModels"></div>
  </div>
  <div class="status-card">
    <div class="label">Queue</div>
    <div class="value" id="sQueue">--</div>
    <div class="sub" id="sQueueDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">GPU</div>
    <div class="value" id="sGPU">--</div>
    <div class="sub" id="sGPUDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Worker</div>
    <div class="value" id="sWorker">--</div>
    <div class="sub" id="sWorkerDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Tunnel</div>
    <div class="value" id="sTunnel">--</div>
  </div>
  <div class="status-card">
    <div class="label">Disk</div>
    <div class="value" id="sDisk">--</div>
    <div class="sub" id="sDiskDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">DB</div>
    <div class="value" id="sDB">--</div>
  </div>
</div>
<div class="status-bar" id="resourceBar" style="border-bottom:none;">
  <div class="status-card" style="min-width:200px;">
    <div class="label">CPU <span id="rCPUPct" style="float:right;color:var(--text);font-weight:700;">--%</span></div>
    <div style="position:relative;height:24px;background:var(--bg);border-radius:4px;overflow:hidden;margin:4px 0;">
      <canvas id="cpuChart" style="width:100%;height:24px;"></canvas>
    </div>
    <div class="sub" id="rCPUDetail"></div>
  </div>
  <div class="status-card" style="min-width:200px;">
    <div class="label">RAM <span id="rRAMPct" style="float:right;color:var(--text);font-weight:700;">--%</span></div>
    <div style="position:relative;height:24px;background:var(--bg);border-radius:4px;overflow:hidden;margin:4px 0;">
      <canvas id="ramChart" style="width:100%;height:24px;"></canvas>
    </div>
    <div class="sub" id="rRAMDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">GPU Util</div>
    <div class="value" id="rGPUUtil">--</div>
    <div class="sub" id="rGPUTemp"></div>
  </div>
  <div class="status-card">
    <div class="label">VRAM</div>
    <div class="value" id="rVRAM">--</div>
    <div class="sub" id="rVRAMDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Disk</div>
    <div class="value" id="rDisk">--</div>
    <div class="sub" id="rDiskDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Uptime</div>
    <div class="value" id="rUptime">--</div>
  </div>
</div>
<div style="font-size:11px;color:var(--muted);padding:6px 16px 2px;background:var(--surface);border-bottom:1px solid var(--border);font-weight:700;letter-spacing:1px;">
  🍎 iMAC SERVER <span style="font-weight:400;color:var(--green);">M1 CPU</span>
</div>
<div class="status-bar" id="statusBarMac">
  <div class="status-card">
    <div class="label">Ollama</div>
    <div class="value off" id="mOllama">--</div>
    <div class="sub" id="mOllamaModels"></div>
  </div>
  <div class="status-card">
    <div class="label">ComfyUI</div>
    <div class="value off" id="mComfyUI">--</div>
    <div class="sub" id="mComfyModels"></div>
  </div>
  <div class="status-card">
    <div class="label">Queue</div>
    <div class="value" id="mQueue">--</div>
    <div class="sub" id="mQueueDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Worker</div>
    <div class="value" id="mWorker">--</div>
    <div class="sub" id="mWorkerDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Tunnel</div>
    <div class="value" id="mTunnel">--</div>
  </div>
  <div class="status-card">
    <div class="label">DB</div>
    <div class="value" id="mDB">--</div>
  </div>
</div>
<div style="font-size:11px;color:var(--muted);padding:6px 16px 2px;background:var(--surface);border-bottom:1px solid var(--border);font-weight:700;letter-spacing:1px;">
  🏥 HIS/PACS SERVER <span style="font-weight:400;color:var(--blue);">198.51.100.73</span>
</div>
<div class="status-bar" id="statusBarHis">
  <div class="status-card">
    <div class="label">Status</div>
    <div class="value off" id="hStatus">--</div>
    <div class="sub" id="hStatusDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">CPU</div>
    <div class="value" id="hCPU">--</div>
    <div class="sub" id="hCPUDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">RAM</div>
    <div class="value" id="hRAM">--</div>
    <div class="sub" id="hRAMDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">GPU</div>
    <div class="value" id="hGPU">--</div>
    <div class="sub" id="hGPUDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Disk</div>
    <div class="value" id="hDisk">--</div>
    <div class="sub" id="hDiskDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Uptime</div>
    <div class="value" id="hUptime">--</div>
  </div>
</div>
<div style="font-size:11px;color:var(--muted);padding:6px 16px 2px;background:var(--surface);border-bottom:1px solid var(--border);font-weight:700;letter-spacing:1px;">
  🌐 PRODUCTION SERVER <span style="font-weight:400;color:var(--yellow);">192.0.2.108</span>
</div>
<div class="status-bar" id="statusBarProd">
  <div class="status-card">
    <div class="label">Status</div>
    <div class="value off" id="pStatus">--</div>
    <div class="sub" id="pStatusDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">CPU</div>
    <div class="value" id="pCPU">--</div>
    <div class="sub" id="pCPUDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">RAM</div>
    <div class="value" id="pRAM">--</div>
    <div class="sub" id="pRAMDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">GPU</div>
    <div class="value" id="pGPU">--</div>
    <div class="sub" id="pGPUDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Disk</div>
    <div class="value" id="pDisk">--</div>
    <div class="sub" id="pDiskDetail"></div>
  </div>
  <div class="status-card">
    <div class="label">Uptime</div>
    <div class="value" id="pUptime">--</div>
  </div>
</div>
<div class="controls">
  <button onclick="toggleFilter('all')" class="active" id="fAll">All</button>
  <button onclick="toggleFilter('chat')" id="fChat">Chat</button>
  <button onclick="toggleFilter('api')" id="fApi">API</button>
  <button onclick="toggleFilter('job')" id="fJob">Jobs</button>
  <button onclick="toggleFilter('error')" id="fError">Errors</button>
  <input type="text" id="searchInput" placeholder="Filter: path, model, status..." oninput="applyFilter()">
  <span style="flex:1;"></span>
  <button onclick="togglePause()" id="pauseBtn">⏸ Pause</button>
  <button onclick="clearLog()">🗑 Clear</button>
  <button onclick="toggleAutoScroll()" id="scrollBtn" class="active">↓ Auto-scroll</button>
</div>
<div class="log-container" id="logContainer">
  <div class="empty" id="emptyMsg">Waiting for events...</div>
</div>

<script>
const BASE = (() => { const p = window.location.pathname.replace(/\/monitor\/?$/, ''); return p || ''; })();
let entries = [];
let paused = false;
let autoScroll = true;
let currentFilter = 'all';
let totalReqs = 0, totalJobs = 0, totalErrors = 0;
const maxEntries = 1000;

function connect() {
  const badge = document.getElementById('connBadge');
  const es = new EventSource(BASE + '/api/monitor/stream');

  es.onopen = () => {
    badge.textContent = 'Live';
    badge.className = 'badge badge-online';
  };

  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.type === 'connected') return;
      addEntry(d);
    } catch(err) {}
  };

  es.onerror = () => {
    badge.textContent = 'Disconnected';
    badge.className = 'badge badge-offline';
    es.close();
    setTimeout(connect, 3000);
  };
}

function addEntry(d) {
  if (paused) return;
  entries.push(d);
  if (entries.length > maxEntries) entries.shift();

  if (d.type === 'job') totalJobs++;
  else totalReqs++;
  if ((d.status >= 400) || d.status === 'failed') totalErrors++;

  document.getElementById('entryCount').textContent =
    `${entries.length} entries · ${totalReqs} reqs · ${totalJobs} jobs · ${totalErrors} errors`;

  const container = document.getElementById('logContainer');
  const empty = document.getElementById('emptyMsg');
  if (empty) empty.remove();

  const el = createEntryEl(d);
  if (!matchFilter(d)) el.style.display = 'none';
  container.insertBefore(el, container.firstChild);

  // 최대 DOM 노드 제한
  while (container.children.length > maxEntries) container.removeChild(container.lastChild);

  if (autoScroll) container.scrollTop = 0;
}

function createEntryEl(d) {
  const div = document.createElement('div');
  div.className = 'log-entry new' + (d.type === 'job' ? ' job' : '');
  div.dataset.raw = JSON.stringify(d);

  if (d.type === 'job') {
    const statusCls = d.status === 'completed' ? 'completed' : d.status === 'failed' ? 'failed' : 'started';
    div.innerHTML = `
      <span class="ts">${formatTime(d.ts)}</span>
      <span class="job-status ${statusCls}">${d.status}</span>
      <span class="model">${d.model || ''}</span>
      <span class="method POST" style="min-width:auto;">${d.job_type || 'llm'}</span>
      <span class="dur">${d.duration_ms ? d.duration_ms + 'ms' : ''}</span>
      <span class="meta">
        ${d.prompt ? '<span class="job-prompt">Q: ' + esc(d.prompt) + '</span>' : ''}
        ${d.response ? ' <span class="job-response">→ ' + esc(d.response) + '</span>' : ''}
        ${d.error ? ' <span style="color:var(--red);">ERR: ' + esc(d.error) + '</span>' : ''}
      </span>`;
  } else {
    const sc = String(d.status)[0];
    div.innerHTML = `
      <span class="ts">${formatTime(d.ts)}</span>
      <span class="method ${d.method}">${d.method}</span>
      <span class="status s${sc}">${d.status}</span>
      <span class="path">${esc(d.path)}</span>
      <span class="dur">${d.duration_ms}ms</span>
      <span class="model">${d.model || ''}</span>
      <span class="meta">${d.ip || ''} · ${d.key || ''} · ${formatBytes(d.req_size)}→${formatBytes(d.res_size)}</span>`;
  }
  return div;
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('ko-KR', {hour12:false}) + '.' + String(d.getMilliseconds()).padStart(3,'0');
  } catch(e) { return ts.substr(11,12); }
}

function formatBytes(b) {
  if (!b || b === 0) return '0';
  if (b < 1024) return b + 'B';
  if (b < 1048576) return (b/1024).toFixed(1) + 'K';
  return (b/1048576).toFixed(1) + 'M';
}

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function matchFilter(d) {
  const search = document.getElementById('searchInput').value.toLowerCase();
  let show = true;
  if (currentFilter === 'chat') show = (d.path && (d.path.includes('/chat') || d.path.includes('/generate') || d.path.includes('/queue/submit'))) || d.type === 'job';
  else if (currentFilter === 'api' && d.type === 'job') show = false;
  else if (currentFilter === 'job' && d.type !== 'job') show = false;
  else if (currentFilter === 'error' && (d.status < 400 && d.status !== 'failed')) show = false;
  if (show && search) {
    const raw = JSON.stringify(d).toLowerCase();
    show = raw.includes(search);
  }
  return show;
}

function toggleFilter(f) {
  currentFilter = f;
  document.querySelectorAll('.controls button').forEach(b => b.classList.remove('active'));
  document.getElementById('f' + f.charAt(0).toUpperCase() + f.slice(1)).classList.add('active');
  applyFilter();
}

function applyFilter() {
  const search = document.getElementById('searchInput').value.toLowerCase();
  document.querySelectorAll('.log-entry').forEach(el => {
    const d = JSON.parse(el.dataset.raw || '{}');
    let show = true;
    if (currentFilter === 'chat') show = (d.path && (d.path.includes('/chat') || d.path.includes('/generate') || d.path.includes('/queue/submit'))) || d.type === 'job';
    else if (currentFilter === 'api' && d.type === 'job') show = false;
    else if (currentFilter === 'job' && d.type !== 'job') show = false;
    else if (currentFilter === 'error' && (d.status < 400 && d.status !== 'failed')) show = false;
    if (show && search) {
      const raw = JSON.stringify(d).toLowerCase();
      show = raw.includes(search);
    }
    el.style.display = show ? '' : 'none';
  });
}

function togglePause() {
  paused = !paused;
  const btn = document.getElementById('pauseBtn');
  btn.textContent = paused ? '▶ Resume' : '⏸ Pause';
  btn.classList.toggle('active', paused);
}

function toggleAutoScroll() {
  autoScroll = !autoScroll;
  const btn = document.getElementById('scrollBtn');
  btn.classList.toggle('active', autoScroll);
}

function clearLog() {
  entries = [];
  totalReqs = 0; totalJobs = 0; totalErrors = 0;
  document.getElementById('logContainer').innerHTML = '<div class="empty" id="emptyMsg">Waiting for events...</div>';
  document.getElementById('entryCount').textContent = '0 entries';
}

// Server status polling
async function refreshServerStatus() {
  try {
    const r = await fetch(BASE + '/api/health');
    const d = await r.json();
    const c = d.checks || {};

    // Ollama
    const ollama = c.ollama || {};
    const oEl = document.getElementById('sOllama');
    oEl.textContent = ollama.ok ? 'Online' : 'Offline';
    oEl.className = 'value ' + (ollama.ok ? 'on' : 'off');

    // Queue
    const q = c.queue || {};
    document.getElementById('sQueue').textContent = (q.pending || 0) + (q.running || 0) > 0
      ? (q.pending + q.running) : 'Idle';
    document.getElementById('sQueue').className = 'value ' + ((q.pending || 0) + (q.running || 0) > 0 ? 'warn' : 'on');
    document.getElementById('sQueueDetail').textContent = `P:${q.pending||0} R:${q.running||0}`;

    // Worker
    const w = c.worker || {};
    const wEl = document.getElementById('sWorker');
    wEl.textContent = w.ok ? (w.paused ? 'Paused' : 'Active') : 'Down';
    wEl.className = 'value ' + (w.ok && !w.paused ? 'on' : w.paused ? 'warn' : 'off');

    // Tunnel
    const t = c.tunnel || {};
    const tEl = document.getElementById('sTunnel');
    tEl.textContent = t.ok ? 'Up' : 'Down';
    tEl.className = 'value ' + (t.ok ? 'on' : 'off');

    // Disk
    const disk = c.disk || {};
    document.getElementById('sDisk').textContent = disk.ok ? `${Math.round(disk.free_gb)}GB` : 'Low';
    document.getElementById('sDisk').className = 'value ' + (disk.free_gb > 50 ? 'on' : disk.free_gb > 10 ? 'warn' : 'off');
    document.getElementById('sDiskDetail').textContent = `${disk.used_pct||0}% used`;

    // DB
    const db = c.database || {};
    document.getElementById('sDB').textContent = db.ok ? `${db.size_mb}MB` : 'Error';
    document.getElementById('sDB').className = 'value ' + (db.ok ? 'on' : 'off');

    // Fail rate
    const fr = c.fail_rate_1h || {};
    document.getElementById('sWorkerDetail').textContent = fr.total > 0 ? `${fr.total} jobs/h · ${fr.rate_pct}% fail` : 'No jobs/h';

  } catch(e) {}

  // Ollama models
  try {
    const r2 = await fetch(BASE + '/api/status');
    const d2 = await r2.json();
    const models = d2.models || [];
    const running = d2.running_models || [];
    document.getElementById('sOllamaModels').textContent = `${models.length} models · ${running.length} loaded`;
  } catch(e) {}

  // ComfyUI
  try {
    const r3 = await fetch(BASE + '/api/image/models');
    const d3 = await r3.json();
    const cEl = document.getElementById('sComfyUI');
    if (d3.online) {
      cEl.textContent = 'Online';
      cEl.className = 'value on';
      document.getElementById('sComfyModels').textContent = `${(d3.models||[]).length} models`;
    } else {
      cEl.textContent = 'Offline';
      cEl.className = 'value off';
    }
  } catch(e) {
    document.getElementById('sComfyUI').textContent = 'Offline';
    document.getElementById('sComfyUI').className = 'value off';
  }

  // GPU (from status)
  try {
    const r4 = await fetch(BASE + '/api/status');
    const d4 = await r4.json();
    const rm = d4.running_models || [];
    const gEl = document.getElementById('sGPU');
    if (rm.length > 0) {
      const vram = rm.reduce((s,m) => s + (m.size_vram||0), 0);
      gEl.textContent = formatBytes(vram);
      gEl.className = 'value warn';
      document.getElementById('sGPUDetail').textContent = rm.map(m => m.name).join(', ');
    } else {
      gEl.textContent = 'Idle';
      gEl.className = 'value on';
      document.getElementById('sGPUDetail').textContent = 'No models loaded';
    }
  } catch(e) {}
}

// iMac server status polling
const MAC_BASE = (() => {
  // /ollama/monitor → /ollama-mac 로 변환
  const p = window.location.pathname.replace(/\/ollama\/monitor\/?$/, '/ollama-mac');
  return p || '/ollama-mac';
})();

async function refreshMacStatus() {
  try {
    const r = await fetch(MAC_BASE + '/api/health');
    const d = await r.json();
    const c = d.checks || {};

    const ollama = c.ollama || {};
    const oEl = document.getElementById('mOllama');
    oEl.textContent = ollama.ok ? 'Online' : 'Offline';
    oEl.className = 'value ' + (ollama.ok ? 'on' : 'off');

    const q = c.queue || {};
    document.getElementById('mQueue').textContent = (q.pending||0) + (q.running||0) > 0
      ? (q.pending + q.running) : 'Idle';
    document.getElementById('mQueue').className = 'value ' + ((q.pending||0) + (q.running||0) > 0 ? 'warn' : 'on');
    document.getElementById('mQueueDetail').textContent = `P:${q.pending||0} R:${q.running||0}`;

    const w = c.worker || {};
    const wEl = document.getElementById('mWorker');
    wEl.textContent = w.ok ? (w.paused ? 'Paused' : 'Active') : 'Down';
    wEl.className = 'value ' + (w.ok && !w.paused ? 'on' : w.paused ? 'warn' : 'off');

    const fr = c.fail_rate_1h || {};
    document.getElementById('mWorkerDetail').textContent = fr.total > 0 ? `${fr.total} jobs/h` : '';

    const t = c.tunnel || {};
    const tEl = document.getElementById('mTunnel');
    tEl.textContent = t.ok ? 'Up' : 'Down';
    tEl.className = 'value ' + (t.ok ? 'on' : 'off');

    const db = c.database || {};
    document.getElementById('mDB').textContent = db.ok ? `${db.size_mb}MB` : 'Error';
    document.getElementById('mDB').className = 'value ' + (db.ok ? 'on' : 'off');
  } catch(e) {
    document.getElementById('mOllama').textContent = 'Unreachable';
    document.getElementById('mOllama').className = 'value off';
  }

  // iMac models
  try {
    const r2 = await fetch(MAC_BASE + '/api/status');
    const d2 = await r2.json();
    document.getElementById('mOllamaModels').textContent = `${(d2.models||[]).length} models · ${(d2.running_models||[]).length} loaded`;
  } catch(e) {}

  // iMac ComfyUI
  try {
    const r3 = await fetch(MAC_BASE + '/api/image/models');
    const d3 = await r3.json();
    const cEl = document.getElementById('mComfyUI');
    if (d3.online) {
      cEl.textContent = 'Online';
      cEl.className = 'value on';
      document.getElementById('mComfyModels').textContent = `${(d3.models||[]).length} models`;
    } else {
      cEl.textContent = 'Offline';
      cEl.className = 'value off';
    }
  } catch(e) {
    document.getElementById('mComfyUI').textContent = 'Offline';
    document.getElementById('mComfyUI').className = 'value off';
  }
}

// Mini sparkline chart
const cpuHistory = [];
const ramHistory = [];
const CHART_MAX_POINTS = 60; // 60 points × 5s = 5분

function drawSparkline(canvasId, data, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height;

  ctx.clearRect(0, 0, w, h);
  if (data.length < 2) return;

  const len = data.length;
  const step = w / (CHART_MAX_POINTS - 1);

  // Fill gradient
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + '40');
  grad.addColorStop(1, color + '05');

  ctx.beginPath();
  ctx.moveTo((len - 1) * step, h);
  for (let i = 0; i < len; i++) {
    const x = i * step;
    const y = h - (data[i] / 100) * h;
    if (i === 0) ctx.lineTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.lineTo((len - 1) * step, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  for (let i = 0; i < len; i++) {
    const x = i * step;
    const y = h - (data[i] / 100) * h;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Current value dot
  if (len > 0) {
    const lastX = (len - 1) * step;
    const lastY = h - (data[len - 1] / 100) * h;
    ctx.beginPath();
    ctx.arc(lastX, lastY, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }
}

function pctColor(pct) {
  if (pct > 80) return '#ef4444';
  if (pct > 50) return '#eab308';
  return '#22c55e';
}

async function refreshResources() {
  try {
    const r = await fetch(BASE + '/api/server/stats');
    const d = await r.json();

    // CPU
    const cpu = d.cpu || {};
    const cpuPct = cpu.usage_pct || 0;
    cpuHistory.push(cpuPct);
    if (cpuHistory.length > CHART_MAX_POINTS) cpuHistory.shift();
    document.getElementById('rCPUPct').textContent = cpuPct + '%';
    document.getElementById('rCPUPct').style.color = pctColor(cpuPct);
    document.getElementById('rCPUDetail').textContent = `load ${cpu.load_1m || 0} · ${cpu.cores || 0} cores`;
    drawSparkline('cpuChart', cpuHistory, pctColor(cpuPct));

    // RAM
    const ram = d.ram || {};
    const ramPct = ram.usage_pct || 0;
    ramHistory.push(ramPct);
    if (ramHistory.length > CHART_MAX_POINTS) ramHistory.shift();
    document.getElementById('rRAMPct').textContent = ramPct + '%';
    document.getElementById('rRAMPct').style.color = pctColor(ramPct);
    document.getElementById('rRAMDetail').textContent = `${Math.round((ram.used_mb||0)/1024)}G / ${Math.round((ram.total_mb||0)/1024)}G`;
    drawSparkline('ramChart', ramHistory, pctColor(ramPct));

    // GPU
    if (d.gpu) {
      const gpuEl = document.getElementById('rGPUUtil');
      gpuEl.textContent = d.gpu.utilization_pct + '%';
      gpuEl.className = 'value ' + (d.gpu.utilization_pct > 80 ? 'warn' : 'on');
      document.getElementById('rGPUTemp').textContent = d.gpu.temperature_c + '°C · ' + d.gpu.name.replace('NVIDIA ','');

      const vramEl = document.getElementById('rVRAM');
      vramEl.textContent = Math.round(d.gpu.memory_used_mb/1024*10)/10 + 'G';
      vramEl.className = 'value ' + (d.gpu.memory_usage_pct > 90 ? 'off' : d.gpu.memory_usage_pct > 70 ? 'warn' : 'on');
      document.getElementById('rVRAMDetail').textContent = `${Math.round(d.gpu.memory_used_mb)}/${d.gpu.memory_total_mb}MB (${d.gpu.memory_usage_pct}%)`;
    } else {
      document.getElementById('rGPUUtil').textContent = 'N/A';
      document.getElementById('rVRAM').textContent = 'N/A';
    }

    // Disk
    const disk = d.disk || {};
    const diskEl = document.getElementById('rDisk');
    diskEl.textContent = Math.round(disk.free_gb || 0) + 'G free';
    diskEl.className = 'value ' + (disk.free_gb < 50 ? 'off' : disk.free_gb < 100 ? 'warn' : 'on');
    document.getElementById('rDiskDetail').textContent = `${disk.usage_pct||0}% · ${Math.round(disk.used_gb||0)}/${Math.round(disk.total_gb||0)}G`;

    // Uptime
    document.getElementById('rUptime').textContent = (d.uptime || {}).display || '--';
  } catch(e) {}
}

// HIS/PACS server status polling
async function refreshHisStatus() {
  try {
    const r = await fetch(BASE + '/api/his/stats');
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    const hEl = document.getElementById('hStatus');
    hEl.textContent = 'Online';
    hEl.className = 'value on';
    document.getElementById('hStatusDetail').textContent = (d.gpu ? d.gpu.name : '') + ' · ' + (d.cpu?.cores||0) + ' cores';

    const cpu = d.cpu || {};
    document.getElementById('hCPU').textContent = (cpu.usage_pct||0) + '%';
    document.getElementById('hCPU').className = 'value ' + ((cpu.usage_pct||0) > 80 ? 'off' : (cpu.usage_pct||0) > 50 ? 'warn' : 'on');
    document.getElementById('hCPUDetail').textContent = 'load ' + (cpu.load_1m||0);

    const ram = d.ram || {};
    document.getElementById('hRAM').textContent = (ram.usage_pct||0) + '%';
    document.getElementById('hRAM').className = 'value ' + ((ram.usage_pct||0) > 80 ? 'off' : (ram.usage_pct||0) > 50 ? 'warn' : 'on');
    document.getElementById('hRAMDetail').textContent = Math.round((ram.used_mb||0)/1024) + 'G / ' + Math.round((ram.total_mb||0)/1024) + 'G';

    if (d.gpu) {
      document.getElementById('hGPU').textContent = d.gpu.utilization_pct + '%';
      document.getElementById('hGPU').className = 'value ' + (d.gpu.utilization_pct > 80 ? 'warn' : 'on');
      document.getElementById('hGPUDetail').textContent = d.gpu.temperature_c + '°C · ' + Math.round(d.gpu.memory_used_mb/1024*10)/10 + 'G VRAM';
    } else {
      document.getElementById('hGPU').textContent = 'N/A';
    }

    const disk = d.disk || {};
    document.getElementById('hDisk').textContent = Math.round(disk.free_gb||0) + 'G free';
    document.getElementById('hDisk').className = 'value ' + ((disk.free_gb||0) < 50 ? 'off' : (disk.free_gb||0) < 100 ? 'warn' : 'on');
    document.getElementById('hDiskDetail').textContent = (disk.usage_pct||0) + '% used';

    document.getElementById('hUptime').textContent = (d.uptime||{}).display || '--';
  } catch(e) {
    document.getElementById('hStatus').textContent = 'Offline';
    document.getElementById('hStatus').className = 'value off';
    document.getElementById('hStatusDetail').textContent = '';
  }
}

// Production server status polling
async function refreshProdStatus() {
  try {
    const r = await fetch(BASE + '/api/prod/stats');
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    const el = document.getElementById('pStatus');
    el.textContent = 'Online';
    el.className = 'value on';
    document.getElementById('pStatusDetail').textContent = (d.gpu ? d.gpu.name : '') + ' · ' + (d.cpu?.cores||0) + ' cores';

    const cpu = d.cpu || {};
    document.getElementById('pCPU').textContent = (cpu.usage_pct||0) + '%';
    document.getElementById('pCPU').className = 'value ' + ((cpu.usage_pct||0) > 80 ? 'off' : (cpu.usage_pct||0) > 50 ? 'warn' : 'on');
    document.getElementById('pCPUDetail').textContent = 'load ' + (cpu.load_1m||0);

    const ram = d.ram || {};
    document.getElementById('pRAM').textContent = (ram.usage_pct||0) + '%';
    document.getElementById('pRAM').className = 'value ' + ((ram.usage_pct||0) > 80 ? 'off' : (ram.usage_pct||0) > 50 ? 'warn' : 'on');
    document.getElementById('pRAMDetail').textContent = Math.round((ram.used_mb||0)/1024) + 'G / ' + Math.round((ram.total_mb||0)/1024) + 'G';

    if (d.gpu) {
      document.getElementById('pGPU').textContent = d.gpu.utilization_pct + '%';
      document.getElementById('pGPU').className = 'value ' + (d.gpu.utilization_pct > 80 ? 'warn' : 'on');
      document.getElementById('pGPUDetail').textContent = d.gpu.temperature_c + '°C · ' + Math.round(d.gpu.memory_used_mb/1024*10)/10 + 'G VRAM';
    } else {
      document.getElementById('pGPU').textContent = 'N/A';
    }

    const disk = d.disk || {};
    document.getElementById('pDisk').textContent = Math.round(disk.free_gb||0) + 'G free';
    document.getElementById('pDisk').className = 'value ' + ((disk.free_gb||0) < 50 ? 'off' : (disk.free_gb||0) < 100 ? 'warn' : 'on');
    document.getElementById('pDiskDetail').textContent = (disk.usage_pct||0) + '% used';

    document.getElementById('pUptime').textContent = (d.uptime||{}).display || '--';
  } catch(e) {
    document.getElementById('pStatus').textContent = 'Offline';
    document.getElementById('pStatus').className = 'value off';
    document.getElementById('pStatusDetail').textContent = '';
  }
}

refreshServerStatus();
refreshMacStatus();
refreshHisStatus();
refreshProdStatus();
refreshResources();
setInterval(refreshServerStatus, 10000);
setInterval(refreshMacStatus, 10000);
setInterval(refreshHisStatus, 10000);
setInterval(refreshProdStatus, 10000);
setInterval(refreshResources, 5000);

connect();
</script>
</body>
</html>"""

swagger_config = {
    "headers": [],
    "specs": [{
        "endpoint": "apispec",
        "route": "/apispec.json",
        "rule_filter": lambda rule: rule.rule.startswith("/api/"),
        "model_filter": lambda tag: True,
    }],
    "static_url_path": "/flasgger_static",
    "swagger_ui": False,
    "specs_route": "/docs-internal",
}

# Swagger UI static 파일 직접 서빙
import flasgger as _flasgger_mod
_SWAGGER_STATIC = os.path.join(os.path.dirname(_flasgger_mod.__file__), "ui3", "static")

@app.route("/swagger-static/<path:filename>")
@limiter.exempt
def swagger_static(filename):
    from flask import send_from_directory
    return send_from_directory(_SWAGGER_STATIC, filename)

@app.route("/docs")
@limiter.exempt
def swagger_ui():
    """리버스 프록시 호환 Swagger UI"""
    return render_template_string(r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>WeRU.B Ollama Admin — API Docs</title>
<link rel="stylesheet" href="swagger-static/swagger-ui.css">
<style>body{margin:0;padding:0;}</style>
</head><body>
<div id="swagger-ui"></div>
<script>
function initSwagger() {
  var basePath = window.location.pathname.replace(/\/docs\/?$/, '');
  SwaggerUIBundle({
    url: basePath + '/openapi.json',
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: 'BaseLayout',
    deepLinking: true,
    validatorUrl: null,
  });
}
</script>
<script src="swagger-static/swagger-ui-bundle.js" onload="initSwagger()"></script>
</body></html>""")

swagger_template = {
    "info": {
        "title": "WeRu.B AI Server API",
        "description": (
            "LLM · 이미지 · 의료 AI · HR Law · Game NPC · Story/Novel 통합 API.\n\n"
            "**의료 데이터를 연동하려면 `GET /api/medical/catalog` 부터** — 보유 데이터셋, "
            "건수, 최신성, 그리고 각 데이터셋을 실제로 서빙하는 엔드포인트를 기계가 읽는 "
            "형태로 한 번에 낸다. 아래 `Medical - *` 태그는 그 카탈로그가 가리키는 API들이다.\n\n"
            "인증: 외부는 `Authorization: Bearer <API_KEY>`, localhost는 bypass."
        ),
        "version": "1.0.0",
    },
    "host": "",  # 동적으로 설정됨
    "schemes": ["https", "http"],
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Bearer <API_KEY>",
        }
    },
    # Swagger UI는 **여기 선언한 순서대로** 태그를 먼저 보이고, 미선언 태그는 뒤에
    # 알파벳순·설명 없이 붙인다. 의료 엔드포인트가 26개 태그로 흩어져 있어 연동자가
    # 문서를 통독해야 했던 문제(2026-08-31 실측)를 여기서 배치로 푼다 —
    # 라우트 docstring 100여 개를 건드리지 않고 한 곳에서.
    "tags": [
        # ── 의료: 데이터 (내부 시스템 연동의 주 표면) ──
        {"name": "Medical - Catalog",
         "description": "🔎 **여기서 시작** — 보유 의료 데이터셋·건수·최신성·서빙 엔드포인트 카탈로그"},
        {"name": "Notice",
         "description": "고시·공고 정본 + 코드 마스터(수가·치료재료·약가 fee_codes)·약가 마스터·저가약 대체조제. HIRA/data.go.kr 자동 동기화"},
        {"name": "Medical - DUR",
         "description": "식약처 DUR 정본 — 병용금기·단일성분 금기/주의 6종·서방정 분할주의. 처방 검증용"},
        {"name": "Medical - DrugInfo",
         "description": "의약품 개요정보(e약은요) — 환자용 복약 설명 정본"},
        {"name": "Medical - Code",
         "description": "KCD 상병코드 · EDI 청구코드 · 진단-처방 매핑"},
        {"name": "Medical Law",
         "description": "의료법·고시 RAG 자문(ask/review). 근거 조문 라벨 동반 — 자문 보조이며 법률 자문이 아니다"},
        {"name": "Medical - Knowledge", "description": "의료 지식 RAG 검색"},
        # ── 의료: 추론 ──
        {"name": "Medical - Clinical", "description": "임상 보조 — SOAP 요약·감별진단·환자 설명"},
        {"name": "Medical - Clinical EN", "description": "임상 보조(영문)"},
        {"name": "Medical - Clinical QA", "description": "임상 질의응답"},
        {"name": "Medical - Triage", "description": "증상 분류·진료과 추천"},
        {"name": "Medical - Drug Analysis", "description": "약물 상호작용·이상반응 분석(DUR 정본 병합)"},
        {"name": "Medical - Advanced AI", "description": "추론 체인 + 멀티모델 앙상블"},
        {"name": "Medical - Deep Analysis", "description": "심층 분석 파이프라인"},
        {"name": "Medical - Predictive", "description": "재입원·합병증 예측"},
        {"name": "Medical - Patient", "description": "환자 단위 조회"},
        {"name": "Medical - Physiology", "description": "생리 시뮬레이션(아바타 R1)"},
        {"name": "Medical - Avatar", "description": "영상→장기 메시(glTF) 비동기 생성(아바타 R2)"},
        {"name": "Medical Imaging", "description": "의료 영상 판독 보조(MedGemma vision+text)"},
        {"name": "Medical - CLI", "description": "임상 명령 실행·의도 추천"},
        # ── 의료: 운영 ──
        {"name": "Medical - Models", "description": "의료 모델 위계·라우팅"},
        {"name": "Medical - Stats", "description": "의료 사용량 통계"},
        {"name": "Medical - Admin", "description": "테넌트·키 관리(관리자)"},
        {"name": "Medical - Validation", "description": "응답 검증"},
        {"name": "Medical - Research", "description": "연구용"},
        {"name": "Medical Training", "description": "의료 모델 학습"},
        # ── 일반 AI (의료 AI와 분리된 표면 — v2.125.34) ──
        {"name": "General AI",
         "description": "일반 AI 표면 — 의료 AI와 별도 영역. 모델 allowlist로 의료 특화 모델 호출 불가(분리 계약은 /api/general/status). 커뮤니티 프로필은 HyperCLOVA X SEED(1.5B)"},
        # ── 공통 ──
        {"name": "Queue", "description": "Batch queue job management"},
        {"name": "Ollama", "description": "Ollama model & inference"},
        {"name": "Vision", "description": "범용 비전(qwen3-vl:8b 기본·폴백 minicpm-v)"},
        {"name": "Image", "description": "Image generation via ComfyUI (FLUX.1-schnell)"},
        {"name": "Heavy", "description": "Heavy tier — 하이브리드(GPU+RAM 오프로드) 대형 모델 on-demand 구동 (상주 fast-lane과 분리)"},
        {"name": "Keys", "description": "API 키 발급·폐기·만료 관리 (localhost 전용)"},
        {"name": "Config", "description": "서버 설정 (관리자)"},
        # ── 코어: 추론·큐·모델 ──
        {"name": "Ollama", "description": "Ollama 모델 관리·추론(생성/채팅 스트리밍)"},
        {"name": "Chat", "description": "범용 채팅 프록시 — 클라이언트에 raw 통과(번역 등 비한국어 사용례 보존)"},
        {"name": "Queue", "description": "배치 작업 큐 — 제출·조회·배치·파이프라인"},
        {"name": "Queue / Priority", "description": "큐 우선순위·fast-lane 제어"},
        {"name": "Heavy", "description": "Heavy tier — 하이브리드(GPU+RAM 오프로드) 대형 모델 on-demand (상주 fast-lane과 분리)"},
        {"name": "Providers", "description": "모델 공급자·라우팅 표"},
        {"name": "RAG", "description": "범용 RAG 색인·검색(ChromaDB)"},
        {"name": "Search", "description": "실시간 웹 검색(SearXNG) — 일반·뉴스·학술·IT"},
        {"name": "Detect", "description": "AI 생성물 판별 — iMac 자동 위임 대상"},
        {"name": "Moderation", "description": "콘텐츠 검열"},
        # ── 오케스트레이션·노드 ──
        {"name": "Orchestration", "description": "단일 진입점 자동 분산 — capability×노드 상태 기반 배정"},
        {"name": "Orchestrator", "description": "오케스트레이터 대화·워크플로우(3단계 폴백)"},
        {"name": "Orchestrator - Tools", "description": "Tool Calling 도구 레지스트리(config/tools.json)"},
        {"name": "GPU / Scheduler", "description": "GPU 자원 스케줄러·모델 적재 제어"},
        {"name": "iMac Link", "description": "iMac 워커 연동 상태"},
        {"name": "WebSocket / Streaming", "description": "SSE/WebSocket 스트리밍 표면"},
        # ── 한국어·글쓰기·창작 ──
        {"name": "Korean Language", "description": "한국어 분석·교정·변환 24종 — AI 패턴 탐지·가독성·응집성·경어·맞춤법·문체 변환"},
        {"name": "Writing AI", "description": "글쓰기 보조 — 맞춤법+자연화 파이프라인"},
        {"name": "AI Writing", "description": "AI 글쓰기(레거시 표면)"},
        {"name": "Story Engine", "description": "스토리 엔진 — 플롯·전개"},
        {"name": "Story Engine - Context", "description": "스토리 컨텍스트 관리"},
        {"name": "Story Engine - Character", "description": "스토리 캐릭터"},
        {"name": "Story Engine - World", "description": "스토리 세계관"},
        {"name": "Story Engine - Novel", "description": "스토리→소설 변환"},
        {"name": "Novel Writer", "description": "소설 집필 — 장편 파이프라인"},
        {"name": "Novel Writer - Pipeline", "description": "소설 집필 파이프라인 제어"},
        {"name": "Novel Writer - Assistant", "description": "집필 보조"},
        {"name": "Novel Writer - Analysis", "description": "원고 분석"},
        {"name": "Novel Writer - Revision", "description": "퇴고·개고"},
        {"name": "Novel Writer - Series", "description": "시리즈 관리"},
        {"name": "Novel Writer - Scene", "description": "장면 단위 작업"},
        {"name": "Novel Writer - Chapter", "description": "챕터 단위 작업"},
        {"name": "Novel Writer - Character", "description": "소설 캐릭터·경험 추적"},
        {"name": "Novel Decomposer", "description": "원고 구조 분해"},
        {"name": "Novel Decomposer - Components", "description": "구조 컴포넌트"},
        {"name": "Character", "description": "캐릭터 공통"},
        # ── 코딩·HR·상담·회의 ──
        {"name": "Coding AI", "description": "코드 생성·리뷰·리팩터·디버그·변환"},
        {"name": "Coding AI - RAG", "description": "코드베이스 RAG 색인·질의"},
        {"name": "HR Law", "description": "노동법 자문 — 자문 보조이며 법률 자문이 아니다"},
        {"name": "HR Law Tenant", "description": "노동법 테넌트 관리(격리·키)"},
        {"name": "Counseling", "description": "상담 오케스트레이션 — 위기 감지 시 1393 자동 안내"},
        {"name": "Meeting Analysis", "description": "회의 녹화 분석 — STT+화자분리+Vision+요약"},
        {"name": "Speaker", "description": "성문 등록·대조·파기 — HIS VoiceEMR 화자 게이트(B2). 오디오 비저장·ECAPA 임베딩만·score=코사인(인증 아님)"},
        # ── 게임 ──
        {"name": "Game", "description": "게임 공통"},
        {"name": "Game NPC", "description": "NPC 대화·행동"},
        {"name": "Game Shared NPC", "description": "공유 NPC"},
        {"name": "Game Heroine", "description": "히로인 캐릭터"},
        {"name": "Game Dialogue", "description": "대사 생성"},
        {"name": "Game Growth", "description": "성장·육성"},
        {"name": "Game Illustrate", "description": "일러스트 생성"},
        {"name": "Game Scenario", "description": "시나리오"},
        {"name": "Game World", "description": "세계관"},
        # ── 상호운용·저장·운영 ──
        {"name": "FHIR - Interoperability", "description": "FHIR R4 래퍼 — HIS/EMR이 표준 형식으로 AI 결과를 소비"},
        {"name": "S3 Storage", "description": "Wasabi S3 이미지 저장·Presigned URL"},
        {"name": "Training", "description": "모델 파인튜닝·데이터셋"},
        {"name": "System", "description": "서버 상태·성능·경보·로그"},
        {"name": "Dashboard", "description": "통합 대시보드 API(외부 시스템용)"},
        {"name": "Admin", "description": "관리자 기능"},
        {"name": "Discovery", "description": "API 디스커버리 — OpenAPI 스펙·AI Plugin manifest(외부 도구 자동 인식)·공개 모델 목록 `/api/tags`(가용성 감시용·인증 불요)"},
        {"name": "Gateway", "description": "게이트웨이 표면 — 라우팅·프록시"},
        {"name": "MCP", "description": "Model Context Protocol 서버(JSON-RPC 2.0) — 외부 AI 에이전트가 도구를 표준으로 호출"},
    ],
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# --- Blueprint 공통 인증 (C1/C3 수정: key_hash 기반, 통일) ---
def _blueprint_auth():
    """모든 Blueprint 공통 인증 — verify_api_key (key_hash) 사용"""
    from flask import g
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    cfg = load_config()

    # 인증 비활성화 시
    if not cfg.get("auth_enabled", True):
        g.act_key_name = get_key_name(token) if token else "noauth"
        return None

    # 로컬 bypass
    if cfg.get("local_bypass", False) and is_local_request():
        if token and verify_api_key(token):
            g.act_key_name = get_key_name(token)
        else:
            g.act_key_name = "local"
        return None

    # 웹 로그인 세션
    if session.get("web_logged_in"):
        g.act_key_name = "web:" + (session.get("web_user") or "admin")
        return None

    # Bearer 토큰 검증 (key_hash 기반)
    if not verify_api_key(token):
        return jsonify({"error": "unauthorized"}), 401
    g.act_key_name = get_key_name(token)
    return None


# --- Blueprint 등록 (Step 2) ---
# BATCH_MODE: 배치 전용 모드에서는 실시간 API를 비활성화하고
# 큐/배치/모델관리/한국어/글쓰기 등 배치에 필요한 Blueprint만 등록.
# 비활성 Blueprint의 URL 호출 시 503 + 안내 메시지 반환.

# 배치 모드에서 비활성화할 Blueprint 경로 접두사 (BATCH_MODE 시 등록 스킵)
_BATCH_SKIP_PREFIXES = {
    "/api/vision", "/api/rag", "/api/medical", "/api/triage",
    "/api/orchestrator", "/api/counseling", "/api/meeting",
    "/api/fhir", "/api/drug-batch", "/api/provenance",
    "/api/game", "/api/hr-law", "/api/story", "/api/novel",
    "/mcp",
}

if BATCH_MODE:
    @app.errorhandler(404)
    def _batch_mode_404(e):
        path = request.path
        for prefix in _BATCH_SKIP_PREFIXES:
            if path.startswith(prefix):
                return jsonify({
                    "error": "batch_mode",
                    "message": f"이 서버는 배치 전용 모드입니다. {prefix} API는 비활성화되어 있습니다.",
                    "available_apis": ["/api/queue/*", "/api/models", "/api/generate", "/api/chat",
                                       "/api/korean/*", "/api/writing/*", "/api/search/*",
                                       "/api/keys", "/api/config", "/api/status"],
                }), 503
        return jsonify({"error": "not found"}), 404
    logger.info("BATCH_MODE 활성화 — 실시간 API 비활성화, 배치 전용 운영")

# ---------- 배치 모드에서도 항상 등록하는 Blueprint ----------

# Queue Blueprint (배치 핵심)
from routes.queue import queue_bp, init_queue, get_queue_paused, get_worker_alive, set_worker_alive
@queue_bp.before_request
def _queue_auth():
    return _blueprint_auth()
app.register_blueprint(queue_bp)

if not BATCH_MODE:
    # Vision Blueprint (실시간 전용)
    from routes.vision import vision_bp
    for _bp_rule in vision_bp.deferred_functions:
        pass
    @vision_bp.before_request
    def _vision_auth():
        return _blueprint_auth()
    app.register_blueprint(vision_bp)

    # RAG Blueprint (실시간 전용)
    from routes.rag import rag_bp, init_rag
    @rag_bp.before_request
    def _rag_auth():
        return _blueprint_auth()
    app.register_blueprint(rag_bp)

    # Training Blueprint (실시간 전용)
    from routes.training import training_bp, init_training
    @training_bp.before_request
    def _training_auth():
        return _blueprint_auth()
    app.register_blueprint(training_bp)

# ---------- 배치/풀 모두 등록하는 Blueprint ----------

# Image Blueprint
from routes.image import image_bp, init_image
@image_bp.before_request
def _image_auth():
    result = _blueprint_auth()
    if result:
        return result
    env = _detect_server_env()
    if env == "imac":
        return jsonify({"error": "Image generation is not available on this server (iMac). Use the Ubuntu RTX server."}), 503
app.register_blueprint(image_bp)

# Ollama Blueprint (모델 관리 — 배치에서도 필요)
from routes.ollama import ollama_bp, init_ollama, _detect_server_env
@ollama_bp.before_request
def _ollama_auth():
    return _blueprint_auth()
app.register_blueprint(ollama_bp)

# Heavy tier Blueprint (하이브리드 대형 모델 on-demand — 상주 fast-lane과 분리)
from routes.heavy import heavy_bp, init_heavy
@heavy_bp.before_request
def _heavy_auth():
    return _blueprint_auth()
app.register_blueprint(heavy_bp)

# OpenAI 호환 Blueprint (/v1/chat/completions — OpenPencil 등 외부 OpenAI 클라이언트)
from routes.openai_compat import openai_bp, init_openai_compat
@openai_bp.before_request
def _openai_compat_auth():
    if request.method == "OPTIONS":  # 브라우저 CORS preflight는 인증 면제
        return None
    return _blueprint_auth()
app.register_blueprint(openai_bp)

# System Blueprint (서버 상태 — 배치에서도 필요)
from routes.system import system_bp, init_system, build_init_system_prompt
@system_bp.before_request
def _system_auth():
    return _blueprint_auth()
app.register_blueprint(system_bp)

# Activity Blueprint (로그 — 배치에서도 필요)
from routes.activity import activity_bp, init_activity, _SYSTEM_INFO
@activity_bp.before_request
def _activity_auth():
    return _blueprint_auth()
app.register_blueprint(activity_bp)

# iMac Link Blueprint (GPU↔iMac 연계 상태 — imac-link-console Phase A, v2.82.0)
from routes.imac_link import imac_link_bp, init_imac_link
@imac_link_bp.before_request
def _imac_link_auth():
    # localhost bypass + 외부는 Bearer 필요 (registry/reload 등 상태변경 보호) — C1 fix
    return _blueprint_auth()
app.register_blueprint(imac_link_bp)

# Notice Blueprint (ERP 공시 수명주기 — erp-notice-rag Day 2, v2.84.0 목표)
from routes.notice import notice_bp
@notice_bp.before_request
def _notice_auth():
    return _blueprint_auth()
limiter.limit("600/minute")(notice_bp)  # ERP batch 여유 (키 기반 버킷 — _rate_limit_key)
app.register_blueprint(notice_bp)

# Korean Blueprint (텍스트 처리 — 배치 핵심)
from routes.korean import korean_bp
@korean_bp.before_request
def _korean_auth():
    return _blueprint_auth()
app.register_blueprint(korean_bp)

# Writing AI Blueprint (글쓰기 배치 — 배치 핵심)
from routes.writing import writing_bp, init_writing
@writing_bp.before_request
def _writing_auth():
    return _blueprint_auth()
init_writing(ollama_session=None, ollama_base=None)
app.register_blueprint(writing_bp)

# Coding AI Blueprint (배치에서도 유용)
from routes.coding import coding_bp, init_coding
app.register_blueprint(coding_bp)

# Search Blueprint (배치에서도 유용)
from routes.search import search_bp
@search_bp.before_request
def _search_auth():
    return _blueprint_auth()
app.register_blueprint(search_bp)

# Dashboard API Blueprint (상태 모니터링 — 배치에서도 필요)
from routes.dashboard_api import dashboard_api_bp, init_dashboard_api
@dashboard_api_bp.before_request
def _dashboard_api_auth():
    return _blueprint_auth()
app.register_blueprint(dashboard_api_bp)

# ---------- 실시간 전용 Blueprint (BATCH_MODE 시 스킵) ----------
if not BATCH_MODE:
    # HR Law Blueprint
    from routes.hr_law import hr_law_bp, init_hr_law
    @hr_law_bp.before_request
    def _hr_law_auth():
        return _blueprint_auth()
    app.register_blueprint(hr_law_bp)

    # Game Blueprint
    from routes.game import game_bp, init_game
    @game_bp.before_request
    def _game_auth():
        return _blueprint_auth()
    app.register_blueprint(game_bp)

    # Medical Blueprint
    from routes.medical import medical_bp, init_medical

    @medical_bp.before_request
    def _medical_auth():
        """의료 Blueprint fail-closed (보안 P2 M4).

        종전에는 `/api/medical/tenant*`의 변경 요청만 인증을 강제해, 임상 라우트 다수가
        **무인증으로 외부에 열려 있었다**. 다만 이 계열은 Bearer가 아니라 **X-Medical-Key**
        (테넌트 키)로 인증하므로 `_blueprint_auth()`를 그대로 강제하면 HIS가 차단된다.
        → **셋 중 아무것도 없을 때만 401**로 막고, 키가 있으면 라우트 내부 검증에 맡긴다.

        되돌림: `MEDICAL_AUTH_ENFORCE=0` (재배포 없이 종전 동작). 차단분은 경고 로그에
        남겨 정상 호출자가 걸렸는지 즉시 확인할 수 있게 한다.
        """
        if request.method == "OPTIONS":
            return None                                    # CORS preflight
        if os.environ.get("MEDICAL_AUTH_ENFORCE", "1") != "1":
            return None                                    # 킬스위치(관측 후 판단용)
        if request.headers.get("X-Medical-Key") or request.args.get("medical_key"):
            return None                                    # 테넌트 인증 → 라우트가 검증
        res = _blueprint_auth()                            # 로컬 bypass·세션·Bearer
        if res is not None:
            logger.warning("[medical] 인증 없는 요청 차단: %s %s ip=%s ua=%s",
                           request.method, request.path, get_client_ip(),
                           (request.headers.get("User-Agent") or "")[:60])
        return res

    app.register_blueprint(medical_bp)

    # Medical Law Blueprint (v2.51.0)
    from routes.medical_law import medical_law_bp, init_medical_law
    @medical_law_bp.before_request
    def _medical_law_auth():
        return _blueprint_auth()
    app.register_blueprint(medical_law_bp)

    # Medical Training Management Blueprint
    from routes.medical_training import medical_training_bp, init_medical_training
    @medical_training_bp.before_request
    def _medical_training_auth():
        return _blueprint_auth()
    init_medical_training(base_dir=os.path.dirname(os.path.abspath(__file__)),
                          ollama_base=_CORE_OLLAMA_BASE)
    app.register_blueprint(medical_training_bp)

    # Medical CLI Blueprint
    from routes.medical_cli import medical_cli_bp, init_medical_cli
    @medical_cli_bp.before_request
    def _medical_cli_auth():
        return _blueprint_auth()
    app.register_blueprint(medical_cli_bp)

    # Triage Blueprint
    from routes.triage import triage_bp, init_triage
    @triage_bp.before_request
    def _triage_auth():
        return _blueprint_auth()
    app.register_blueprint(triage_bp)

    # Orchestrator Blueprint
    from routes.orchestrator import orchestrator_bp, init_orchestrator
    @orchestrator_bp.before_request
    def _orchestrator_auth():
        return _blueprint_auth()
    app.register_blueprint(orchestrator_bp)

    # Medical Advanced AI Blueprint (Phase 4)
    from routes.medical_advanced import medical_advanced_bp, init_medical_advanced
    @medical_advanced_bp.before_request
    def _medical_advanced_auth():
        return _blueprint_auth()
    app.register_blueprint(medical_advanced_bp)

    # Story Engine Blueprint (v2.52.0)
    from routes.story import story_bp, init_story
    @story_bp.before_request
    def _story_auth():
        return _blueprint_auth()
    app.register_blueprint(story_bp)

    # Novel Writer Blueprint (v2.52.0)
    from routes.novel import novel_bp, init_novel
    @novel_bp.before_request
    def _novel_auth():
        return _blueprint_auth()
    app.register_blueprint(novel_bp)

    # FHIR-Lite Interoperability Blueprint
    from routes.fhir import fhir_bp
    @fhir_bp.before_request
    def _fhir_auth():
        return _blueprint_auth()
    app.register_blueprint(fhir_bp)

    # Drug Batch Pipeline + Provenance Blueprint
    from routes.drug_batch import drug_batch_bp
    @drug_batch_bp.before_request
    def _drug_batch_auth():
        return _blueprint_auth()
    app.register_blueprint(drug_batch_bp)

    from routes.provenance import provenance_bp
    @provenance_bp.before_request
    def _provenance_auth():
        return _blueprint_auth()
    app.register_blueprint(provenance_bp)

    # Counseling Orchestration Blueprint (v2.62.0)
    from routes.counseling import counseling_bp, init_counseling
    @counseling_bp.before_request
    def _counseling_auth():
        return _blueprint_auth()
    app.register_blueprint(counseling_bp)

    # General AI Blueprint (v2.125.34) — 일반 AI 표면, 의료 표면과 분리
    from routes.general_ai import general_ai_bp
    @general_ai_bp.before_request
    def _general_ai_auth():
        return _blueprint_auth()
    app.register_blueprint(general_ai_bp)

    # Speaker Blueprint (v2.125.51) — 성문 등록·대조·파기 (HIS VoiceEMR B2 계약)
    from routes.speaker import speaker_bp
    @speaker_bp.before_request
    def _speaker_auth():
        return _blueprint_auth()
    app.register_blueprint(speaker_bp)

    # Meeting Analysis Blueprint (v2.63.0)
    from routes.meeting import meeting_bp, init_meeting
    @meeting_bp.before_request
    def _meeting_auth():
        return _blueprint_auth()
    app.register_blueprint(meeting_bp)

    # MCP Blueprint (v2.67.0)
    from routes.mcp import mcp_bp, init_mcp
    @mcp_bp.before_request
    def _mcp_auth():
        return _blueprint_auth()
    app.register_blueprint(mcp_bp)

    # WebSocket / Streaming Blueprint (P3-3)
    from core.websocket_handler import ws_bp, init_websocket
    @ws_bp.before_request
    def _ws_auth():
        return _blueprint_auth()
    app.register_blueprint(ws_bp)

else:
    logger.info("BATCH_MODE: 실시간 Blueprint 20개 스킵 (의료/상담/오케스트레이터/스토리/MCP 등)")


# ---------------------------------------------------------------------------
# 플랫폼별 Blueprint 등록
# ---------------------------------------------------------------------------
from core.platform import detect as _detect_platform, is_imac as _is_imac, feature as _feature

_platform = _detect_platform()
logger.info("[platform] %s — 플랫폼 전용 Blueprint 로딩 중", _platform)

if _is_imac() and not BATCH_MODE:
    # Smart Gateway Blueprint (iMac 전용: GPU 서버 포워딩)
    from routes.gateway import gateway_bp, init_gateway as _init_gateway
    @gateway_bp.before_request
    def _gateway_auth():
        return _blueprint_auth()
    app.register_blueprint(gateway_bp)
    logger.info("[platform:imac] Smart Gateway Blueprint 등록")




OLLAMA_BASE = "http://localhost:11434"

# ---------------------------------------------------------------------------
# iMac M1 경량 모델 오프로드
# ---------------------------------------------------------------------------
IMAC_OLLAMA_BASE = "http://localhost:8586"  # SSH 리버스 터널 (iMac → 웹서버 → AI서버)
LIGHTWEIGHT_MODELS = {"llama3.2:3b"}  # iMac으로 라우팅할 모델

_imac_available = None   # True/False/None (30초 캐시)
_imac_check_time = 0

def _is_imac_available():
    """iMac Ollama 연결 확인 (30초 캐시)"""
    global _imac_available, _imac_check_time
    import time as _t
    if _t.time() - _imac_check_time < 30:
        return _imac_available
    try:
        r = requests.get(f"{IMAC_OLLAMA_BASE}/", timeout=2)
        _imac_available = r.status_code == 200
    except Exception:
        _imac_available = False
    _imac_check_time = _t.time()
    if _imac_available:
        logger.info("iMac Ollama: connected")
    return _imac_available
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "queue.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "auth_enabled": True,
    # 공개판: 기본 끔 — 프록시 뒤에서 모든 요청이 로컬로 보일 수 있다. 켜려면 config.json 또는 AI_LOCAL_BYPASS=1
    "local_bypass": os.environ.get("AI_LOCAL_BYPASS", "") == "1",
    "allowed_origins": ["https://example.org", "https://ollama.example.org", "http://localhost:8585"],
    "admin_user": "admin",
}

_config_cache = {"data": None, "mtime": 0}

def load_config():
    try:
        mt = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return dict(DEFAULT_CONFIG)
    if _config_cache["data"] and _config_cache["mtime"] == mt:
        return _config_cache["data"]
    with open(CONFIG_PATH) as f:
        cfg = {**DEFAULT_CONFIG, **json.load(f)}
    _config_cache["data"] = cfg
    _config_cache["mtime"] = mt
    return cfg

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    _config_cache["data"] = None  # 캐시 무효화

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_db_local = threading.local()

def get_db():
    """DB connection — 스레드 로컬 영속 커넥션 (PRAGMA 오버헤드 제거)"""
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA cache_size=-8000")    # 8MB 페이지 캐시
        conn.execute("PRAGMA temp_store=MEMORY")   # 임시 테이블 메모리
        conn.execute("PRAGMA mmap_size=134217728") # 128MB mmap 읽기
        _db_local.conn = conn
    try:
        conn.execute("SELECT 1")   # 연결 유효성 확인
    except sqlite3.DatabaseError:
        try: conn.close()
        except Exception: pass
        _db_local.conn = None
        return get_db()
    return conn


# ---------------------------------------------------------------------------
# HR Law — 근태/인사 법률 DB
# ---------------------------------------------------------------------------
# 섹션별 법적 필수 여부 요약

MAX_RETRIES = 3
RETRY_DELAYS = [30, 60, 120]  # seconds

# stale 'running' 복구 임계 — Ollama timeout(300s)+graceful(30s) 초과면 어떤 워커도
# 처리 중일 수 없으므로(=확실한 orphan) 안전 복구. 미만이면 워커 재생성(max_requests)
# 중첩창에 살아있는 구 워커가 처리 중일 수 있어 리셋 보류(이중처리 race 방지).
_STALE_RUNNING_S = 360


def _stale_running_cutoff():
    return (datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=_STALE_RUNNING_S)).isoformat() + "Z"

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            batch_id TEXT,
            status TEXT DEFAULT 'pending',
            model TEXT NOT NULL,
            system_prompt TEXT,
            prompt TEXT NOT NULL,
            response TEXT,
            error TEXT,
            tokens INTEGER,
            duration_ms INTEGER,
            priority INTEGER DEFAULT 5,
            retry_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, priority, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_completed ON jobs(status, completed_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            pending INTEGER DEFAULT 0,
            running INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            tokens INTEGER DEFAULT 0,
            avg_speed REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            channel TEXT,
            success INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status_code INTEGER,
            duration_ms INTEGER,
            ip TEXT,
            model TEXT,
            request_size INTEGER DEFAULT 0,
            response_size INTEGER DEFAULT 0,
            user TEXT,
            key_name TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_ts ON api_activity(ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_path ON api_activity(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_key ON api_activity(key_name)")
    # 성능 모니터링 메타 (P2) — 워커 recycle 카운터·부팅시각 (인메모리는 recycle마다 리셋되므로 영속)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Characters table — 캐릭터 일관성 시스템
    conn.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            reference_image TEXT NOT NULL,
            thumbnail TEXT,
            tags TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            metadata TEXT
        )
    """)
    # Orchestration runs (v2.50.0)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orchestration_runs (
            id TEXT PRIMARY KEY,
            pipeline_name TEXT NOT NULL,
            pipeline_type TEXT NOT NULL,
            input_text TEXT NOT NULL,
            result_text TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            stages_json TEXT,
            variables_json TEXT,
            total_tokens INTEGER DEFAULT 0,
            total_duration_ms INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orch_runs_pipeline ON orchestration_runs(pipeline_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orch_runs_created ON orchestration_runs(created_at DESC)")
    # 컬럼 마이그레이션 (기존 DB 호환)
    # ⚠️ v2.125.53: 이 루프는 **모든 CREATE TABLE 뒤**에 있어야 한다. 종전엔 api_keys·stats_history·
    # api_activity 생성 **전**에 돌아, 신규 설치 DB에선 ALTER가 '테이블 없음'으로 조용히 실패하고
    # 테이블이 컬럼 없이 만들어졌다(api_keys.expires_at 부재 → Bearer 검증·키 발급이 no such column).
    # 라이브 DB는 테이블이 이미 있어 드러나지 않았다. 회귀: tests/test_init_db_fresh_schema.py
    for tbl, col, typedef in [
        ("jobs", "retry_count", "INTEGER DEFAULT 0"),
        ("tenants", "key_regenerated_at", "TEXT"),  # HR Law 키 재발급 일시
        ("jobs", "options", "TEXT"),
        ("jobs", "webhook_url", "TEXT"),
        ("jobs", "job_type", "TEXT DEFAULT 'llm'"),
        ("jobs", "next_retry_at", "TEXT"),  # 재시도 백오프 — 이 시각 전엔 디큐 제외
        ("api_keys", "expires_at", "TEXT"),
        # ("api_keys", "key_value", "TEXT") — 평문 저장 폐지(v2.124.46). 기존 컬럼은 비워둔다.
        ("api_activity", "key_name", "TEXT"),
        # 성능 모니터링 (P1/P2) — stats_history 확장 컬럼
        ("stats_history", "vram_used_mb", "INTEGER"),
        ("stats_history", "vram_total_mb", "INTEGER"),
        ("stats_history", "gpu_util_pct", "INTEGER"),
        ("stats_history", "gpu_temp_c", "INTEGER"),
        ("stats_history", "latency_p50_ms", "INTEGER"),
        ("stats_history", "latency_p95_ms", "INTEGER"),
        ("stats_history", "latency_p99_ms", "INTEGER"),
        ("stats_history", "error_rate_pct", "REAL"),
        ("stats_history", "worker_threads", "INTEGER"),
        ("stats_history", "worker_threads_peak", "INTEGER"),  # 스냅샷 간 최대(peak-tracking)
        ("stats_history", "sse_subscribers", "INTEGER"),
        ("stats_history", "model_resident", "TEXT"),
        ("stats_history", "worker_recycles", "INTEGER"),      # 누적 워커 부팅수(churn delta용)
        ("stats_history", "worker_uptime_s", "INTEGER"),
        ("stats_history", "model_vram_ratio", "REAL"),        # 상주 모델 최소 VRAM 비율(CPU 폴백 감지, v2.124.33)
        ("stats_history", "inflight_peak", "INTEGER"),        # 스냅샷 간 동시 요청 최대(풀 포화 신호, v2.124.94)
    ]:
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typedef}")
            conn.commit()
            logger.info(f"Added {col} column to jobs table")
        except sqlite3.OperationalError:
            pass
    # Reset stuck running jobs from previous crash — staleness 게이트로 워커 재생성
    # 중첩창 in-flight 잡 이중처리 race 방지(>_STALE_RUNNING_S만 확실한 orphan).
    conn.execute(
        "UPDATE jobs SET status='pending', started_at=NULL "
        "WHERE status='running' AND (started_at IS NULL OR started_at < ?)",
        (_stale_running_cutoff(),))
    conn.commit()

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()

# API key verification cache — DB 조회 최소화
_key_cache = {}  # hash -> (valid, last_checked)
_KEY_CACHE_TTL = 60  # seconds

# ---------------------------------------------------------------------------
# Response Cache — TTL 기반 인메모리 캐시
# ---------------------------------------------------------------------------
import hashlib as _hashlib
from collections import OrderedDict

class _TTLCache:
    """간단한 TTL 캐시 (maxsize + TTL 초과 시 자동 만료)"""
    def __init__(self, maxsize=256, ttl=300):
        self._cache = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key):
        import time
        item = self._cache.get(key)
        if item is None:
            return None
        if time.time() - item[1] > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return item[0]

    def set(self, key, value):
        import time
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = (value, time.time())

    def clear(self):
        self._cache.clear()

    def __len__(self):
        return len(self._cache)

# LLM 응답 캐시 (동일 prompt+model+system → 즉시 반환)
_llm_response_cache = _TTLCache(maxsize=500, ttl=3600)   # 1시간

# 이미지 모델 목록 캐시 (ComfyUI 호출 절감)
_image_models_cache = _TTLCache(maxsize=1, ttl=300)       # 5분



# ---------------------------------------------------------------------------
# Chat Fair Queue — 공정 큐 게이트 (v2.24.0)
# ---------------------------------------------------------------------------
class ChatFairQueue:
    """글로벌 + 유저별 동시 제한 + FIFO 대기열 (라운드로빈)"""

    def __init__(self, max_global=3, max_per_user=1, max_wait_queue=20, wait_timeout=60):
        self._lock = threading.Lock()
        self._max_global = max_global
        self._max_per_user = max_per_user
        self._max_wait_queue = max_wait_queue
        self._wait_timeout = wait_timeout
        self._active = {}          # key → count (현재 진행 중)
        self._active_total = 0
        self._waiters = []         # [(key, event, timestamp), ...]

    @property
    def active_total(self):
        return self._active_total

    @property
    def queue_length(self):
        return len(self._waiters)

    def try_acquire(self, key):
        """즉시 슬롯 획득 시도. 성공하면 True, 실패하면 False."""
        with self._lock:
            if self._active_total >= self._max_global:
                return False
            if self._active.get(key, 0) >= self._max_per_user:
                return False
            self._active[key] = self._active.get(key, 0) + 1
            self._active_total += 1
            return True

    def acquire(self, key):
        """슬롯 획득. 즉시 못 얻으면 대기열 진입.
        Returns: (acquired: bool, waited: bool, position: int, est_seconds: int)
        """
        # 즉시 획득 시도
        if self.try_acquire(key):
            return True, False, 0, 0

        # 대기열 진입
        with self._lock:
            if len(self._waiters) >= self._max_wait_queue:
                return False, False, len(self._waiters), -1

            evt = threading.Event()
            entry = (key, evt, time.time())
            self._waiters.append(entry)
            position = len(self._waiters)

        # 대기
        acquired = evt.wait(timeout=self._wait_timeout)
        if acquired:
            # 진입 시 순번을 그대로 돌려준다 — 종전엔 0으로 버려서 성공 경로의
            # X-Queue-Position 헤더가 항상 비었다(대기했다는 사실만 알고 몇 번째였는지
            # 모르면 호출자가 혼잡도를 못 읽는다).
            return True, True, position, 0

        # 타임아웃 — 대기열에서 제거
        with self._lock:
            self._waiters = [(k, e, t) for k, e, t in self._waiters if e is not evt]
        return False, True, position, -1

    def release(self, key):
        """슬롯 해제 + 대기열에서 다음 유저 깨우기 (라운드로빈)"""
        with self._lock:
            self._active[key] = max(0, self._active.get(key, 0) - 1)
            if self._active[key] == 0:
                del self._active[key]
            self._active_total = max(0, self._active_total - 1)

            if not self._waiters:
                return

            # 라운드로빈: 현재 active에 없는 유저 우선
            next_idx = None
            for i, (wk, we, wt) in enumerate(self._waiters):
                if wk not in self._active:
                    next_idx = i
                    break
            if next_idx is None:
                # 모두 active면 FIFO
                next_idx = 0

            wk, we, wt = self._waiters.pop(next_idx)

            # 슬롯 할당
            self._active[wk] = self._active.get(wk, 0) + 1
            self._active_total += 1
            we.set()

    def stats(self):
        with self._lock:
            return {
                "active_total": self._active_total,
                "max_global": self._max_global,
                "per_user": dict(self._active),
                "max_per_user": self._max_per_user,
                "wait_queue": len(self._waiters),
                "max_wait_queue": self._max_wait_queue,
            }


_chat_queue = ChatFairQueue(max_global=3, max_per_user=1, max_wait_queue=20, wait_timeout=60)

# ---------------------------------------------------------------------------
# Gated Ollama Session — 모든 LLM 호출에 Fair Queue 자동 적용 (v2.24.1)
# ---------------------------------------------------------------------------
class _GatedOllamaSession:
    """_ollama_session 래퍼 — HTTP 엔드포인트에서 LLM 호출 시 자동으로 Fair Queue 적용.

    - Flask request context 있고 OLLAMA_BASE/api/(generate|chat) 호출 → 큐 슬롯 획득
    - 큐 워커 스레드 (request context 없음) → 바이패스
    - Webhook (slack/discord) → OLLAMA_BASE 아니므로 바이패스
    """

    def __init__(self, session, fair_queue, ollama_base):
        self._session = session
        self._queue = fair_queue
        self._ollama_base = ollama_base

    def _should_gate(self, url):
        """LLM 추론 호출인지 판별"""
        if self._ollama_base not in url:
            return False
        return "/api/generate" in url or "/api/chat" in url

    def post(self, url, **kwargs):
        from flask import has_request_context
        if has_request_context() and self._should_gate(url):
            key = _rate_limit_key()
            _t0 = time.time()
            acquired, waited, pos, est = self._queue.acquire(key)
            if not acquired:
                # v2.125.68: 타입이 있는 예외로 — 종전 RuntimeError는 model_router에서 문자열이 되어
                # medical이 502로 내고 **공용 브레이커 실패로 세졌다**(core/llm_resilience.LLMBusyError 주석).
                # 메시지 문자열은 종전과 같게 둔다(로그 grep 호환).
                from core.llm_resilience import LLMBusyError
                st = self._queue.stats()
                per_user = st.get("per_user", {}).get(key, 0)
                waited_s = round(time.time() - _t0, 1)
                # 🔑 종전 로그엔 `active=1/3`만 있어 '전역 여유가 있는데 거절'로 읽혔다 — 사용자당 한도에
                #    걸린 것인지 보이게 per_user를 함께 남긴다(키는 API 키 해시 앞 16자 또는 클라이언트 IP).
                logger.warning("[fair-queue] busy key=%s active=%s/%s per_user=%s/%s wait_queue=%s/%s waited=%ss",
                               key, st["active_total"], st["max_global"], per_user, st["max_per_user"],
                               st["wait_queue"], st["max_wait_queue"], waited_s)
                raise LLMBusyError(
                    f"LLM server busy (active={self._queue.active_total}/{self._queue._max_global}, "
                    f"wait_queue={self._queue.queue_length}/{self._queue._max_wait_queue})",
                    active=st["active_total"], max_global=st["max_global"],
                    wait_queue=st["wait_queue"], max_wait_queue=st["max_wait_queue"],
                    per_user=per_user, waited_s=waited_s,
                )
            try:
                # ComfyUI VRAM 해제 (필요 시)
                try:
                    _gpu_manager.ensure_llm_vram()
                except Exception as e:
                    logger.debug("VRAM release for LLM failed: %s", e)
                return self._session.post(url, **kwargs)
            finally:
                self._queue.release(key)
        return self._session.post(url, **kwargs)

    def get(self, url, **kwargs):
        return self._session.get(url, **kwargs)

    def delete(self, url, **kwargs):
        return self._session.delete(url, **kwargs)

    @property
    def headers(self):
        return self._session.headers




def generate_api_key(name, expires_days=90):
    raw_key = "sk-" + secrets.token_hex(24)
    conn = get_db()
    now = datetime.now(timezone.utc)
    expires = (now + __import__("datetime").timedelta(days=expires_days)).isoformat().replace("+00:00", "Z") if expires_days > 0 else None
    # 평문(key_value)은 저장하지 않는다 — 발급 응답이 원본을 보는 유일한 기회다.
    # DB·백업이 유출돼도 해시만 남아 키를 재사용할 수 없게 한다(HR Law 테넌트 키 sha256과 동일 규율).
    conn.execute(
        "INSERT INTO api_keys (key_hash, key_prefix, name, created_at, expires_at) VALUES (?,?,?,?,?)",
        (hash_key(raw_key), raw_key[:10], name, now.isoformat().replace("+00:00", "Z"), expires),
    )
    conn.commit()
    _key_cache.clear()
    return raw_key

def verify_api_key(key):
    if not key:
        return False
    h = hash_key(key)
    now_ts = time.time()
    cached = _key_cache.get(h)
    if cached and (now_ts - cached[1]) < _KEY_CACHE_TTL:
        return cached[0]
    conn = get_db()
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    row = conn.execute(
        "SELECT id, name FROM api_keys WHERE key_hash=? AND active=1 AND (expires_at IS NULL OR expires_at > ?)",
        (h, now_iso),
    ).fetchone()
    valid = row is not None
    key_name = row["name"] if row else None
    _key_cache[h] = (valid, now_ts, key_name)
    if row:
        conn.execute(
            "UPDATE api_keys SET last_used_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat() + "Z", row["id"]),
        )
        conn.commit()
    return valid


def get_key_name(key):
    """캐시에서 API 키 이름 반환 (verify_api_key 호출 후 사용)"""
    if not key:
        return None
    h = hash_key(key)
    cached = _key_cache.get(h)
    if cached and len(cached) >= 3:
        return cached[2]
    return None

def get_client_ip():
    """실제 클라이언트 IP 반환 — Cloudflare Tunnel 경유 시 CF-Connecting-IP 우선"""
    return (request.headers.get("CF-Connecting-IP") or
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or
            request.remote_addr or "unknown")

def is_local_request():
    # 엣지(Cloudflare/nginx 등)가 외부 요청에 주입하는 포워딩 헤더가 하나라도 있으면 외부로 간주
    # (fail-closed). CF-Connecting-IP 단일 의존 제거(보안검토 H1) — nginx 직프록시/SSH 터널 등
    # CF 헤더 없는 경로로 8585 도달 시 로컬 오인=전면 인증우회를 차단. _is_monitor_authorized와 통일.
    # 정상 로컬 호출(curl/스크립트/내부 워커)은 이 헤더들을 설정하지 않음(내부 발신 XFF 부재 확인).
    if (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For")
            or request.headers.get("X-Real-IP")):
        return False
    remote = request.remote_addr
    return remote in ("127.0.0.1", "::1", "localhost")

def is_admin_request():
    """로컬 요청 OR (웹 로그인 AND 역할 admin).

    예전엔 로그인 여부만 봤다 — operator·viewer 계정도 config·API 키 발급·Ollama 재시작을 쓸 수 있었다
    (역할표 ROLE_DEFAULTS·_check_user_permission은 호출 0이었다 · admin-menu-settings-cleanup D6).
    역할은 로그인 때 users.db에서 세션에 싣는다. 세션에 역할이 없으면 거부(fail-closed).
    """
    if load_config().get("local_bypass", False) and is_local_request():  # 공개판: 로컬 = 관리자는 로컬 우회를 켰을 때만
        return True
    return bool(session.get("web_logged_in")) and session.get("web_role") == "admin"

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import g
        cfg = load_config()
        auth = request.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""

        if not cfg.get("auth_enabled", True):
            # 인증 비활성 — Bearer 토큰이 있으면 키 이름 기록
            if token:
                verify_api_key(token)
                g.act_key_name = get_key_name(token) or "noauth"
            else:
                g.act_key_name = "noauth"
            return f(*args, **kwargs)

        if cfg.get("local_bypass", False) and is_local_request():
            # 로컬 bypass — Bearer 토큰이 있으면 키 이름 우선 기록 (발급 서버 구분용)
            if token and verify_api_key(token):
                g.act_key_name = get_key_name(token)
            else:
                g.act_key_name = "local"
            return f(*args, **kwargs)

        # 웹 로그인 세션 허용 (외부에서 웹 UI로 로그인한 경우)
        if session.get("web_logged_in"):
            if token and verify_api_key(token):
                g.act_key_name = get_key_name(token)
            else:
                g.act_key_name = "web:" + (session.get("web_user") or "admin")
            return f(*args, **kwargs)

        if not verify_api_key(token):
            return jsonify({"error": "unauthorized"}), 401
        g.act_key_name = get_key_name(token)
        return f(*args, **kwargs)
    return decorated

_WEB_SESSION_TIMEOUT = 8 * 3600  # 8시간

def _hash_password(password):
    # per-user 랜덤 salt + 자기기술 포맷 (core.password 단일 소스)
    return _core_hash_password(password)


USERS_DB_PATH = os.path.join(BASE_DIR, "users.db")

def _get_users_db():
    import sqlite3
    conn = sqlite3.connect(USERS_DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")  # 인증 경로 — 동시쓰기 창서 BUSY 즉시실패 방어
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        role TEXT NOT NULL DEFAULT 'viewer',
        permissions TEXT DEFAULT '{}',
        is_active INTEGER DEFAULT 1,
        last_login TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    return conn

def _verify_web_login(username, password):
    """users DB로 인증 (per-user salt + 레거시 static-salt 폴백, 성공 시 lazy 재해싱)"""
    try:
        conn = _get_users_db()
        user = conn.execute("SELECT id, password_hash, is_active FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if not user or not user["is_active"]:
            _core_dummy_verify(password)  # username 열거 방지 — 동일 비용 KDF 소비
            return False
        if not _core_verify_password(password, user["password_hash"]):
            return False
    except Exception:
        return False
    # 비밀번호는 이미 검증됨. 이하 last_login 갱신/lazy 재해싱은 best-effort —
    # DB 락(SQLITE_BUSY) 등으로 실패해도 인증 결과(True)를 절대 오염시키지 않는다.
    try:
        conn2 = _get_users_db()
        if _core_needs_rehash(user["password_hash"]):
            # 레거시 해시 → 새 per-user salt로 무중단 재해싱
            conn2.execute("UPDATE users SET last_login=datetime('now'), password_hash=?, updated_at=datetime('now') WHERE id=?",
                          (_core_hash_password(password), user["id"]))
        else:
            conn2.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
        conn2.commit()
        conn2.close()
    except Exception:
        logger.warning("web login post-auth update(last_login/rehash) failed for user=%s", username)
    return True

def require_web_login(f):
    """웹 UI 접근 시 세션 로그인 확인"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("web_logged_in"):
            return redirect(url_for("login", next=request.path))
        # 세션 타임아웃 체크
        last = session.get("web_last_active", 0)
        if time.time() - last > _WEB_SESSION_TIMEOUT:
            session.clear()
            return redirect(url_for("login", next=request.path))
        session["web_last_active"] = time.time()
        return f(*args, **kwargs)
    return decorated

def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for field in ("metadata", "options"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

worker_event = threading.Event()
image_worker_event = threading.Event()      # 이미지 워커 전용 이벤트

# Ollama HTTP session — keep-alive 재사용
_ollama_session_raw = requests.Session()
_ollama_session_raw.headers.update({"Connection": "keep-alive"})
# GatedOllamaSession 래핑 — 모든 API LLM 호출에 Fair Queue 자동 적용
_ollama_session = _GatedOllamaSession(_ollama_session_raw, _chat_queue, OLLAMA_BASE)
init_rag(_ollama_session, ollama_base=OLLAMA_BASE)  # RAG Blueprint
init_training(_ollama_session, get_db, ollama_base=OLLAMA_BASE, system_info=_SYSTEM_INFO)  # Training Blueprint에 ollama session 주입

_current_model = {"name": None}

# Fast lane — 경량 모델은 priority 자동 부스트
FAST_MODELS = {"qwen2.5:1.5b", "qwen2.5:3b", "tinyllama:1.1b",
               "stablelm2:1.6b", "smollm2:1.7b", "phi3:3.8b", "llama3.2:3b",
               "starcoder2:3b", "qwen2.5-coder:3b", "hyperclova-seed:1.5b",
               "nomic-embed-text", "mxbai-embed-large", "snowflake-arctic-embed:33m"}

def auto_priority(model, user_priority):
    """P3-2: 도메인 우선순위 + 경량 모델 가속

    1. 경량 모델 → 우선순위 2단계 상승 (기존)
    2. 도메인별 자동 우선순위 (P3-2 priority_queue 연동)
    """
    from core.priority_queue import get_domain_priority
    # 도메인 기반 자동 우선순위 (request context에서 path 추출)
    domain_prio = None
    try:
        from flask import has_request_context
        if has_request_context():
            domain_prio = get_domain_priority(path=request.path, user_priority=user_priority)
    except Exception:
        pass

    if domain_prio is not None:
        effective = domain_prio
    else:
        effective = user_priority

    # Fast Lane: 경량 모델 가속
    if model in FAST_MODELS and effective > 1:
        effective = max(1, effective - 2)
    return effective

_last_auto_purge = 0
_last_stats_snapshot = 0
_last_wal_checkpoint = 0


def _wal_checkpoint():
    """주기적 WAL 체크포인트(60s) — 영속 thread-local 커넥션 다수로 passive
    autocheckpoint가 굶어 queue.db-wal이 무한 증가(59MB)→get_db connect wedge→
    /api/chat 행을 유발하던 문제 방지. TRUNCATE로 WAL 축소(busy면 passive flush)."""
    global _last_wal_checkpoint
    now = time.time()
    if now - _last_wal_checkpoint < 60:
        return
    _last_wal_checkpoint = now
    try:
        conn = get_db()
        r = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # r=(busy, log_pages, checkpointed) — busy=1이면 다른 커넥션 점유로 truncate 보류
        if r and r[0]:
            logger.debug("wal_checkpoint busy (log=%s ckpt=%s)", r[1], r[2])
        # WAL 크기 모니터링 — 체크포인트 후에도 비대면 starvation 재발 조기경보(임계 10MB)
        try:
            import os as _os
            wal = DB_PATH + "-wal"
            sz = _os.path.getsize(wal) if _os.path.exists(wal) else 0
            if sz > 10 * 1024 * 1024:
                logger.warning("queue.db-wal %.1fMB 비대 — 체크포인트 starvation 재발 의심"
                               "(영속 커넥션/누수 트랜잭션 점검 필요)", sz / 1024 / 1024)
        except Exception:
            pass
    except Exception as e:
        logger.debug("wal_checkpoint failed: %s", e)

_last_stale_reap = 0


def _reap_stale_running():
    """주기적(60s) stale 'running' 잡 → pending 복구 안전망.

    startup 리셋을 staleness-gate했으므로(이중처리 race 방지), 재시작 없이도 orphan을
    회수. 디큐는 pending만 보므로 stuck running이 큐를 막진 않으나 영구 미완료 방지.
    현재 워커가 처리 중인 잡은 worker_loop가 배치 처리에 블로킹돼 이 함수에 도달하지
    않으므로 자기 잡을 리셋하지 않음(>_STALE_RUNNING_S 게이트로 이중 안전)."""
    global _last_stale_reap
    now = time.time()
    if now - _last_stale_reap < 60:
        return
    _last_stale_reap = now
    conn = None
    try:
        conn = get_db()
        cur = conn.execute(
            "UPDATE jobs SET status='pending', started_at=NULL "
            "WHERE status='running' AND (started_at IS NULL OR started_at < ?)",
            (_stale_running_cutoff(),))
        if cur.rowcount:
            conn.commit()
            logger.warning("Reaped %d stale running job(s) (>%ds) to pending", cur.rowcount, _STALE_RUNNING_S)
    except Exception as e:  # noqa: BLE001
        logger.debug("stale running reap failed: %s", e)
    finally:
        if conn:
            conn.close()


def _worker_thread_count():
    """현 워커 프로세스 스레드수(nlwp). Linux=/proc/self/task, 그 외 폴백."""
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return threading.active_count()

_threads_peak = 0   # 스냅샷 간 최대 스레드수 (peak-tracking, P0 저노력 GO)

def _sample_gpu():
    """nvidia-smi 1회(P0 측정 ~0.03s). 부재/실패 시 빈 dict."""
    try:
        if not shutil.which("nvidia-smi"):
            return {}
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], timeout=5, text=True).strip().splitlines()[0]
        u, t, util, temp = [p.strip() for p in out.split(",")]
        return {"vram_used_mb": int(u), "vram_total_mb": int(t),
                "gpu_util_pct": int(util), "gpu_temp_c": int(temp)}
    except Exception:
        return {}

def _sample_model_resident():
    """현재 Ollama에 상주(로드)된 모델명. best-effort(스왑 thrash 추적용)."""
    try:
        r = _ollama_session.get(f"{OLLAMA_BASE}/api/ps", timeout=4)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return ",".join(n for n in names if n)
    except Exception:
        return None


def _sample_ps_models():
    """Ollama 상주 모델 원시 목록(size/size_vram 포함). best-effort → 실패 시 []."""
    try:
        r = _ollama_session.get(f"{OLLAMA_BASE}/api/ps", timeout=4)
        return r.json().get("models", []) or []
    except Exception:
        return []


def _min_vram_ratio(models):
    """상주 모델 중 최소 size_vram/size 비율 — CPU 폴백 감지용. 순수 함수.

    완전 GPU=1.0·부분 오프로드=(0,1)·CPU 폴백=0.0·상주 없음/불명=None.
    (2026-07-25 인시던트: 드라이버 불일치로 로드 모델이 vram=0 CPU 폴백된 걸
    health가 healthy로 오인 → 이 비율을 stats_history에 박제해 _perf_alerts가 경보)."""
    ratios = []
    for m in models or []:
        size = m.get("size") or 0
        if size > 0:
            ratios.append((m.get("size_vram") or 0) / size)
    return round(min(ratios), 3) if ratios else None

def _get_meta_int(conn, key, default=0):
    try:
        row = conn.execute("SELECT value FROM monitoring_meta WHERE key=?", (key,)).fetchone()
        return int(row["value"]) if row and row["value"] is not None else default
    except (sqlite3.Error, ValueError, TypeError):
        return default

def _collect_stats():
    """5분마다 큐 + 성능 스냅샷 저장 (30일 보관, P1/P2 성능 모니터링 확장)."""
    global _last_stats_snapshot, _threads_peak
    # peak-tracking: throttle 무관 매 호출 시 스레드수 갱신(worker_loop 비블로킹 구간마다)
    cur_threads = _worker_thread_count()
    if cur_threads > _threads_peak:
        _threads_peak = cur_threads
    now = time.time()
    if now - _last_stats_snapshot < 300:
        return
    _last_stats_snapshot = now
    conn = None
    try:
        conn = get_db()
        counts = {}
        for s in ("pending", "running", "completed", "failed"):
            counts[s] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (s,)).fetchone()[0]
        row = conn.execute("SELECT COALESCE(SUM(tokens),0) as t, COALESCE(SUM(duration_ms),0) as d FROM jobs WHERE status='completed' AND tokens>0").fetchone()
        avg_speed = round(row["t"] / max(row["d"] / 1000, 0.01), 1)
        ts = datetime.now(timezone.utc).isoformat() + "Z"

        # ── 성능 메트릭 샘플 (P1/P2) ──
        gpu = _sample_gpu()
        lat = metrics_reservoir.quantiles()
        err = metrics_reservoir.error_rate()
        try:
            sse_subs = len(_monitor_subscribers)
        except Exception:
            sse_subs = None
        recycles = _get_meta_int(conn, "worker_recycle_total", 0)
        boot_ts = _get_meta_int(conn, "worker_boot_epoch", 0)
        uptime_s = int(now - boot_ts) if boot_ts else None
        threads_peak = _threads_peak
        _threads_peak = cur_threads  # 스냅샷 후 peak 리셋
        # 동시 요청 peak — 수집기가 유일한 리셋 주체(경보는 읽기만 함)
        inflight_peak = metrics_reservoir.inflight(reset_peak=True)["peak"]

        # 상주 모델: /api/ps 1회로 이름 + 최소 VRAM 비율(CPU 폴백 감지) 동시 산출
        _ps_models = _sample_ps_models()
        resident = ",".join(m.get("name", "") for m in _ps_models if m.get("name")) or None
        vram_ratio = _min_vram_ratio(_ps_models)

        conn.execute(
            "INSERT INTO stats_history "
            "(ts,pending,running,completed,failed,tokens,avg_speed,"
            " vram_used_mb,vram_total_mb,gpu_util_pct,gpu_temp_c,"
            " latency_p50_ms,latency_p95_ms,latency_p99_ms,error_rate_pct,"
            " worker_threads,worker_threads_peak,sse_subscribers,model_resident,"
            " worker_recycles,worker_uptime_s,model_vram_ratio,inflight_peak) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, counts["pending"], counts["running"], counts["completed"], counts["failed"],
             row["t"], avg_speed,
             gpu.get("vram_used_mb"), gpu.get("vram_total_mb"), gpu.get("gpu_util_pct"), gpu.get("gpu_temp_c"),
             int(lat["p50"]), int(lat["p95"]), int(lat["p99"]), err,
             cur_threads, threads_peak, sse_subs, resident,
             recycles, uptime_s, vram_ratio, inflight_peak))
        # 30일 이상 삭제
        cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=30)).isoformat() + "Z"
        conn.execute("DELETE FROM stats_history WHERE ts < ?", (cutoff,))
        conn.commit()
    except Exception as e:
        logger.debug("stats collection failed: %s", e)
    finally:
        if conn:
            conn.close()

def _auto_purge():
    """완료/실패 작업 및 이미지 파일 자동 정리 (24시간마다 1회)"""
    global _last_auto_purge
    now = time.time()
    if now - _last_auto_purge < 86400:
        return
    _last_auto_purge = now
    cfg = load_config()

    # 1) DB 작업 정리
    days = cfg.get("auto_purge_days", 7)
    if days > 0:
        cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).isoformat() + "Z"
        conn = get_db()
        try:
            # completed_at 대신 COALESCE — 일부 실패 경로가 completed_at을 남기지 않아
            # NULL이 되고, `completed_at < cutoff`는 NULL을 절대 매칭하지 않아 그 잡이
            # **영구 잔존**했다(2026-07-21 측정: 22일·17일 된 failed 2행). created_at으로
            # 폴백해 나이 기준을 항상 성립시킨다.
            cur = conn.execute(
                "DELETE FROM jobs WHERE status IN ('completed','failed') "
                "AND COALESCE(completed_at, created_at) < ?", (cutoff,))
            if cur.rowcount > 0:
                conn.commit()
                logger.info(f"Auto-purge: deleted {cur.rowcount} jobs older than {days} days")
            # 고아 'waiting' 잡 리퍼 — 파이프라인 단계 실패/워커 크래시로 다음 단계가
            # 활성화되지 못한 잔존분 방어. LLM 단계는 수초 내 완료라 days일 넘게 waiting이면
            # 확실히 고아 → failed로 전이(다음 주기 _auto_purge가 삭제). cascade-fail이
            # 근본해결이나 pre-fix 잔존·예외 경로 대비 defense-in-depth.
            stuck = conn.execute(
                "UPDATE jobs SET status='failed', error='orphaned waiting job (pipeline aborted/no advance)', "
                "completed_at=? WHERE status='waiting' AND created_at < ?",
                (datetime.now(timezone.utc).isoformat() + "Z", cutoff),
            )
            if stuck.rowcount > 0:
                conn.commit()
                logger.info(f"Auto-purge: failed {stuck.rowcount} orphaned waiting job(s) older than {days} days")
        finally:
            conn.close()

    # 2) 이미지 파일 정리
    _cleanup_images(cfg)

    # 3) 비활성 챗 세션 정리 (PHI 최소화/거버넌스 — last_active 기준 retention)
    #    chat_sessions만 나이기반 retention이 없어 무한보존이던 갭 봉합. 기본 90일,
    #    config chat_retention_days로 튜닝(0 이하=비활성). 삭제만 수행(공간은 freelist
    #    재사용, OS 회수는 수동 POST /api/chat/purge {vacuum:true} — 라이브 DB VACUUM
    #    락 위험 회피). 실패는 경고만(다른 정리 단계·워커 루프 비차단).
    chat_days = cfg.get("chat_retention_days", 90)
    if chat_days and chat_days > 0:
        try:
            from core.chat_memory import purge_old_sessions
            res = purge_old_sessions(chat_days)
            if res.get("sessions_deleted"):
                logger.info(
                    "Auto-purge chat: deleted %d sessions / %d messages older than %d days",
                    res["sessions_deleted"], res["messages_deleted"], chat_days,
                )
        except Exception as e:
            logger.warning("chat session purge failed: %s", e)

def _get_image_dir():
    """ComfyUI output 디렉토리 경로"""
    # ComfyUI output 디렉토리 탐색
    candidates = [
        os.path.expanduser("~/ComfyUI/output"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ComfyUI", "output"),
        "/home/deploy/ComfyUI/output",
    ]
    for d in candidates:
        if os.path.isdir(d):
            return os.path.abspath(d)
    return None

def _get_image_stats():
    """이미지 저장소 통계"""
    img_dir = _get_image_dir()
    if not img_dir or not os.path.isdir(img_dir):
        return {"dir": None, "count": 0, "size_mb": 0, "oldest": None, "newest": None, "files": []}

    files = []
    for f in os.listdir(img_dir):
        fp = os.path.join(img_dir, f)
        if os.path.isfile(fp) and f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            stat = os.stat(fp)
            files.append({
                "name": f,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "mtime_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

    files.sort(key=lambda x: x["mtime"])
    total_size = sum(f["size"] for f in files)

    return {
        "dir": img_dir,
        "count": len(files),
        "size_mb": round(total_size / (1024 * 1024), 1),
        "oldest": files[0]["mtime_iso"] if files else None,
        "newest": files[-1]["mtime_iso"] if files else None,
        "files": files,
    }

def _cleanup_images(cfg=None, force_days=None, force_max_count=None, force_max_size_mb=None):
    """이미지 파일 정리 — 보관 기간, 최대 개수, 최대 용량 기준"""
    if cfg is None:
        cfg = load_config()

    retention_days = force_days if force_days is not None else cfg.get("image_retention_days", 7)
    max_count = force_max_count if force_max_count is not None else cfg.get("image_max_count", 500)
    max_size_mb = force_max_size_mb if force_max_size_mb is not None else cfg.get("image_max_size_mb", 2000)

    img_dir = _get_image_dir()
    if not img_dir:
        return {"deleted": 0, "freed_mb": 0}

    stats = _get_image_stats()
    files = stats["files"]
    if not files:
        return {"deleted": 0, "freed_mb": 0}

    to_delete = set()
    now = time.time()

    # 1) 보관 기간 초과 파일
    if retention_days > 0:
        cutoff = now - (retention_days * 86400)
        for f in files:
            if f["mtime"] < cutoff:
                to_delete.add(f["name"])

    # 2) 최대 개수 초과 (오래된 것부터 삭제)
    if max_count > 0 and len(files) > max_count:
        excess = len(files) - max_count
        for f in files[:excess]:
            to_delete.add(f["name"])

    # 3) 최대 용량 초과 (오래된 것부터 삭제)
    if max_size_mb > 0:
        total = sum(f["size"] for f in files) / (1024 * 1024)
        if total > max_size_mb:
            for f in files:
                if total <= max_size_mb:
                    break
                to_delete.add(f["name"])
                total -= f["size"] / (1024 * 1024)

    # 삭제 실행
    deleted = 0
    freed = 0
    for f in files:
        if f["name"] in to_delete:
            fp = os.path.join(img_dir, f["name"])
            try:
                os.remove(fp)
                deleted += 1
                freed += f["size"]
            except Exception as e:
                logger.warning(f"Image cleanup failed: {f['name']}: {e}")

    if deleted > 0:
        logger.info(f"Image cleanup: deleted {deleted} files, freed {round(freed/1024/1024,1)}MB")

    return {"deleted": deleted, "freed_mb": round(freed / (1024 * 1024), 1)}

# ---------------------------------------------------------------------------
# Default Model Preload
# ---------------------------------------------------------------------------
_default_model_loaded = False
_resident_restore_state: dict = {}   # restore_due 판정 상태(워커 스레드 전용)

def _ensure_default_model():
    """기본 모델을 GPU에 예열·유지 — `llm_resident=false`면 아무것도 하지 않는다.

    비상주 모드에서는 요청이 올 때 Ollama가 적재하고 `OLLAMA_KEEP_ALIVE` 후 반납한다
    (파인튜닝 등 VRAM 큰 작업과 공존하기 위한 모드). → core/llm_residency.py
    """
    global _default_model_loaded
    cfg = load_config()
    from core.llm_residency import is_resident, resident_model
    if not is_resident(cfg):
        return
    default_model = resident_model(cfg)      # 채팅 기본 모델과 다르게 지정 가능
    if not default_model:
        return

    try:
        # 현재 로드된 모델 확인
        r = _ollama_session.get(f"{OLLAMA_BASE}/api/ps", timeout=5)
        running = r.json().get("models", [])

        # v2.125.48: 다른 모델이 쓰이는 동안은 끼어들지 않는다(적재 스래싱 — core/llm_residency)
        from core.llm_residency import restore_due, restore_grace_s
        if restore_due(running, default_model, _resident_restore_state, time.time(),
                       restore_grace_s(cfg)):
            # 기본 모델을 keep_alive 무한대로 로드
            logger.info(f"Preloading default model: {default_model}")
            _ollama_session.post(f"{OLLAMA_BASE}/api/generate", json={
                "model": default_model,
                "prompt": "",
                "keep_alive": -1,  # 무한 유지
            }, timeout=120)
            _default_model_loaded = True
            logger.info(f"Default model loaded: {default_model}")
    except Exception as e:
        logger.warning(f"Default model preload failed: {e}")

_LLM_BATCH_SIZE = 4  # OLLAMA_NUM_PARALLEL과 동일
_LLM_BATCH_SIZE_MAX = 8  # P3-4: 동적 배치 최대 크기

def _get_dynamic_batch_size(model):
    """P3-4: 모델 크기에 따른 동적 배치 크기 조정
    경량 모델은 병렬 수 확대, 대형 모델은 축소
    """
    if any(k in model for k in ("2b", "1.8b", "3b", "embed", "bge")):
        return min(_LLM_BATCH_SIZE_MAX, 8)  # 경량: 최대 8
    elif "14b" in model:
        return 2  # 14b: VRAM 부족으로 2
    return _LLM_BATCH_SIZE  # 기본 4

def _process_single_llm_job(job, ollama_base=None):
    """단일 LLM 잡 처리 (배치 내 병렬 실행용, ollama_base로 iMac 라우팅 가능)"""
    start_ts = time.time()
    payload = {"model": job["model"], "prompt": job["prompt"], "stream": False}
    if job["system_prompt"]:
        payload["system"] = job["system_prompt"]
    else:
        cfg = load_config()
        if cfg.get("default_system_prompt_enabled", True):
            payload["system"] = build_init_system_prompt()
    if job.get("options"):
        try:
            payload["options"] = json.loads(job["options"])
        except (json.JSONDecodeError, TypeError):
            pass

    _base = ollama_base or OLLAMA_BASE
    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
    # 모델은 잡(job["model"])이 정한다. 종전 계약: 전송 예외·`error` 응답 모두 예외 → _handle_llm_error(재시도).
    from core import model_router as _model_router
    from core.model_router import OllamaAdapter as _OllamaAdapter
    res = _model_router.generate(
        "queue.llm_job", payload["prompt"], model=payload["model"],
        system=payload.get("system"), options=payload.get("options"), timeout=300,
        adapter=_OllamaAdapter(base=_base, session=_ollama_session))
    if not res.ok:
        raise Exception(res.error)
    result = res.raw
    if "error" in result:
        raise Exception(result["error"])

    response_text = result.get("response", "")
    tokens = result.get("eval_count", 0)
    total_ns = result.get("total_duration", 0)
    duration_ms = int(total_ns / 1_000_000) if total_ns else 0

    # LLM 응답 캐시 저장 (제출 경로 routes/queue.py와 같은 키 규칙을 써야 히트한다)
    try:
        from core.cache import generate_cache_key
        _ck = generate_cache_key(job["model"], job["prompt"],
                                 job.get("system_prompt") or "", job.get("options"))
        _llm_response_cache.set(_ck, response_text)
        logger.info(f"Cache SET: key={_ck[:8]} size={len(_llm_response_cache)}")
    except Exception as e:
        logger.debug("LLM response cache save failed: %s", e)

    return {"response": response_text, "tokens": tokens, "duration_ms": duration_ms}


def worker_loop():
    # worker_alive managed by queue blueprint
    logger.info("Worker started")

    # 시작 시 기본 모델 로드
    try:
        _ensure_default_model()
    except Exception as e:
        logger.debug("default model preload failed: %s", e)

    while get_worker_alive():
        _auto_purge()
        _collect_stats()
        _wal_checkpoint()   # WAL 무한증가→get_db wedge 방지 (chat 행 근원)
        _reap_stale_running()  # orphan running 잡 안전망(재시작 의존 제거)
        if get_queue_paused():
            worker_event.wait(timeout=2)
            worker_event.clear()
            continue

        # P3-4: 동일 모델 배치 조회 — 동적 배치 크기
        conn = get_db()
        try:
            target_model = _current_model["name"] or None
            batch_limit = _get_dynamic_batch_size(target_model) if target_model else _LLM_BATCH_SIZE
            # 재시도 백오프: next_retry_at 미도래 잡은 디큐 제외(NULL=백오프 없음, 정상)
            now_iso = datetime.now(timezone.utc).isoformat() + "Z"
            _bk = "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
            if target_model:
                rows = conn.execute(
                    "SELECT id, model, system_prompt, prompt, retry_count, options, webhook_url, job_type "
                    "FROM jobs WHERE status='pending' AND job_type='llm' AND model=? " + _bk +
                    "ORDER BY priority ASC, created_at ASC LIMIT ?",
                    (target_model, now_iso, batch_limit),
                ).fetchall()
                if not rows:
                    rows = conn.execute(
                        "SELECT id, model, system_prompt, prompt, retry_count, options, webhook_url, job_type "
                        "FROM jobs WHERE status='pending' AND job_type='llm' " + _bk +
                        "ORDER BY priority ASC, created_at ASC LIMIT ?",
                        (now_iso, batch_limit),
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, model, system_prompt, prompt, retry_count, options, webhook_url, job_type "
                    "FROM jobs WHERE status='pending' AND job_type='llm' " + _bk +
                    "ORDER BY priority ASC, created_at ASC LIMIT ?",
                    (now_iso, batch_limit),
                ).fetchall()

            if not rows:
                conn.close()
                try:
                    _ensure_default_model()
                except Exception as e:
                    logger.debug("idle model preload failed: %s", e)
                worker_event.wait(timeout=2)
                worker_event.clear()
                continue

            # 같은 모델 잡만 배치로 묶기 (첫 번째 잡의 모델 기준)
            batch_model = dict(rows[0])["model"]
            jobs = []
            for row in rows:
                j = dict(row)
                if j["model"] != batch_model:
                    break
                jobs.append(j)

            now = datetime.now(timezone.utc).isoformat() + "Z"
            for j in jobs:
                conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (now, j["id"]))
            conn.commit()
        finally:
            conn.close()

        _current_model["name"] = batch_model
        batch_size = len(jobs)

        if batch_size == 1:
            # 단일 잡 — 기존과 동일
            job = jobs[0]
            retry = job.get("retry_count", 0) or 0
            # iMac 오프로드: 경량 모델이면 iMac으로 라우팅
            _route_base = None
            if job["model"] in LIGHTWEIGHT_MODELS and _is_imac_available():
                _route_base = IMAC_OLLAMA_BASE
                logger.info(f"Job {job['id'][:8]} → iMac offload — model={job['model']}")
            else:
                logger.info(f"Job {job['id'][:8]} started — model={job['model']} retry={retry}")
            try:
                result = _process_single_llm_job(job, ollama_base=_route_base)
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE jobs SET status='completed', response=?, tokens=?, duration_ms=?, completed_at=? WHERE id=?",
                        (result["response"], result["tokens"], result["duration_ms"], datetime.now(timezone.utc).isoformat() + "Z", job["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                logger.info(f"Job {job['id'][:8]} completed — tokens={result['tokens']} duration={result['duration_ms']}ms")
                _fire_webhook(job, "completed", result["response"], result["tokens"], result["duration_ms"])
                _broadcast_job_event(job, "completed", response_text=result["response"], duration_ms=result["duration_ms"])
                # v2.70.0: 파이프라인 체인 — 다음 단계 활성화
                _advance_pipeline(job, result["response"])
            except Exception as e:
                _handle_llm_error(job, retry, str(e))
        else:
            # 배치 — ThreadPoolExecutor로 동시 처리
            from concurrent.futures import ThreadPoolExecutor, as_completed
            logger.info(f"Batch started — {batch_size} jobs, model={batch_model}")

            _route_base = None
            if batch_model in LIGHTWEIGHT_MODELS and _is_imac_available():
                _route_base = IMAC_OLLAMA_BASE
                logger.info(f"Batch → iMac offload — model={batch_model}, {batch_size} jobs")
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_map = {executor.submit(_process_single_llm_job, j, ollama_base=_route_base): j for j in jobs}
                for future in as_completed(future_map):
                    job = future_map[future]
                    retry = job.get("retry_count", 0) or 0
                    try:
                        result = future.result()
                        conn = get_db()
                        try:
                            conn.execute(
                                "UPDATE jobs SET status='completed', response=?, tokens=?, duration_ms=?, completed_at=? WHERE id=?",
                                (result["response"], result["tokens"], result["duration_ms"], datetime.now(timezone.utc).isoformat() + "Z", job["id"]),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        logger.info(f"Job {job['id'][:8]} completed (batch) — tokens={result['tokens']} duration={result['duration_ms']}ms")
                        _fire_webhook(job, "completed", result["response"], result["tokens"], result["duration_ms"])
                        _broadcast_job_event(job, "completed", response_text=result["response"], duration_ms=result["duration_ms"])
                        _advance_pipeline(job, result["response"])
                    except Exception as e:
                        _handle_llm_error(job, retry, str(e))

            logger.info(f"Batch completed — {batch_size} jobs")

    logger.info("Worker stopped")


def _advance_pipeline(job, response_text):
    """파이프라인 체인: 현재 단계 완료 → 다음 단계 활성화 + {{prev.response}} 치환"""
    try:
        meta = json.loads(job.get("metadata") or "{}")
        if not meta.get("pipeline_id"):
            return  # 파이프라인 아님

        step_idx = meta.get("step_index", 0)
        total = meta.get("total_steps", 0)
        if step_idx >= total - 1:
            return  # 마지막 단계

        # 다음 단계 job 찾기 (같은 pipeline_id, step_index = current + 1)
        next_step = step_idx + 1
        pipeline_id = meta["pipeline_id"]
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT id, prompt, metadata FROM jobs WHERE status='waiting' AND metadata LIKE ?",
                (f'%"pipeline_id": "{pipeline_id}"%',),
            ).fetchall()
            for row in rows:
                row_meta = json.loads(row["metadata"] or "{}")
                if row_meta.get("step_index") == next_step:
                    # {{prev.response}} 치환
                    new_prompt = row["prompt"].replace("{{prev.response}}", response_text or "")
                    conn.execute(
                        "UPDATE jobs SET status='pending', prompt=? WHERE id=?",
                        (new_prompt, row["id"]),
                    )
                    conn.commit()
                    logger.info(f"Pipeline {pipeline_id} step {next_step} activated — job {row['id'][:8]}")
                    worker_event.set()
                    break
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Pipeline advance error: %s", e)


def _fail_pipeline_downstream(job, error_msg):
    """파이프라인 단계 최종 실패 시 하위 waiting 단계를 cascade-fail.

    _advance_pipeline은 성공 시에만 다음 단계를 pending으로 올리므로, 중간 단계가
    실패(max retry 소진)하면 downstream waiting 잡이 영구 고아(never run·_auto_purge가
    completed/failed만 지워 never purge)가 되던 문제 방지. 같은 pipeline_id의 모든
    waiting 잡을 failed로 전이(이후 _auto_purge 대상)."""
    try:
        meta = json.loads(job.get("metadata") or "{}")
        pid = meta.get("pipeline_id")
        if not pid:
            return
        conn = get_db()
        try:
            cur = conn.execute(
                "UPDATE jobs SET status='failed', error=?, completed_at=? "
                "WHERE status='waiting' AND metadata LIKE ?",
                (f"pipeline aborted — upstream step {meta.get('step_index')} failed",
                 datetime.now(timezone.utc).isoformat() + "Z",
                 f'%"pipeline_id": "{pid}"%'),
            )
            if cur.rowcount:
                conn.commit()
                logger.warning("Pipeline %s aborted — %d downstream waiting step(s) failed", pid, cur.rowcount)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Pipeline downstream fail error: %s", e)


def _handle_llm_error(job, retry, error_msg):
    """LLM 잡 에러 처리 (단일/배치 공용)"""
    logger.warning(f"Job {job['id'][:8]} error (retry {retry}/{MAX_RETRIES}): {error_msg[:200]}")
    _broadcast_job_event(job, "failed", error=error_msg[:200])
    conn = get_db()
    try:
        if retry < MAX_RETRIES:
            delay = RETRY_DELAYS[min(retry, len(RETRY_DELAYS) - 1)]
            # 백오프: next_retry_at 전까지 디큐에서 제외(이전엔 즉시 재큐돼 백오프가 cosmetic).
            next_retry_at = (datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=delay)).isoformat() + "Z"
            conn.execute(
                "UPDATE jobs SET status='pending', retry_count=?, error=?, started_at=NULL, next_retry_at=? WHERE id=?",
                (retry + 1, f"retry {retry+1}/{MAX_RETRIES}: {error_msg[:500]}", next_retry_at, job["id"]),
            )
            conn.commit()
            logger.info(f"Job {job['id'][:8]} scheduled retry {retry+1} in {delay}s (next_retry_at={next_retry_at})")
        else:
            conn.execute(
                "UPDATE jobs SET status='failed', error=?, completed_at=?, retry_count=? WHERE id=?",
                (f"max retries exceeded: {error_msg[:500]}", datetime.now(timezone.utc).isoformat() + "Z", retry, job["id"]),
            )
            conn.commit()
            logger.error(f"Job {job['id'][:8]} FAILED after {MAX_RETRIES} retries: {error_msg[:200]}")
            _fire_webhook(job, "failed", error_msg)
            _terminal_fail = True
    finally:
        conn.close()
    # 파이프라인 단계가 최종 실패하면 하위 waiting 단계 cascade-fail (커넥션 분리 후)
    if locals().get("_terminal_fail"):
        _fail_pipeline_downstream(job, error_msg)


def _validate_webhook_url(url):
    """SSRF 방지 — core.security로 위임(http/https + IP 해석 기반 사설/메타데이터 차단).
    중복 정규식 구현 제거(이전 _PRIVATE_NETS는 169.254/IPv6/8·16진 우회 미차단)."""
    return _core_validate_webhook(url)

def _fire_webhook(job, status, response=None, tokens=None, duration_ms=None):
    """작업 완료/실패 시 webhook POST"""
    url = job.get("webhook_url")
    if not url:
        return
    if not _validate_webhook_url(url):
        logger.warning(f"Webhook blocked (invalid/private URL): {url}")
        return
    payload = {
        "job_id": job["id"],
        "model": job["model"],
        "status": status,
        "response": (response or "")[:2000],
        "tokens": tokens,
        "duration_ms": duration_ms,
    }
    for attempt in range(3):
        try:
            r = _ollama_session.post(url, json=payload, timeout=10)
            if r.status_code < 400:
                logger.info(f"Webhook sent for {job['id'][:8]} → {url}")
                return
        except Exception as e:
            logger.warning(f"Webhook attempt {attempt+1} failed for {job['id'][:8]}: {e}")
        time.sleep(2)
    logger.error(f"Webhook failed after 3 attempts for {job['id'][:8]} → {url}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ollama_get(path):
    try:
        r = _ollama_session.get(f"{OLLAMA_BASE}{path}", timeout=10)
        return r.json() if r.ok else {"error": r.text}
    except requests.ConnectionError:
        return {"error": "Ollama is not running"}

def ollama_post(path, data, timeout=300):
    try:
        r = _ollama_session.post(f"{OLLAMA_BASE}{path}", json=data, timeout=timeout)
        return r.json() if r.ok else {"error": r.text}
    except requests.ConnectionError:
        return {"error": "Ollama is not running"}

def ollama_delete(path, data):
    try:
        r = _ollama_session.delete(f"{OLLAMA_BASE}{path}", json=data, timeout=30)
        return r.json() if r.ok else {"error": r.text}
    except requests.ConnectionError:
        return {"error": "Ollama is not running"}

def format_size(size_bytes):
    if size_bytes is None:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"

_status_cache = {"value": False, "ts": 0, "fails": 0}

def get_ollama_status():
    """Ollama 엔진 가용성 (3초 캐시).

    오탐 방지: 엔진은 대부분 살아있으나(GET / 보통 µs) 앱측 일시적 스레드 포화/
    스테일 keep-alive로 단발 실패가 발생 → 1회 실패로 '다운' 확정하면 가짜 경보(Engine Down).
    따라서 2회 연속 실패해야 다운으로 확정(직전 상태 낙관 유지). timeout 3→4s.
    실제 다운은 매 호출 일관 실패 → 곧 다운 확정됨.
    """
    now = time.time()
    if now - _status_cache["ts"] < 3:  # 3초 캐시
        return _status_cache["value"]
    try:
        r = _ollama_session.get(f"{OLLAMA_BASE}/", timeout=4)
        ok = (r.status_code == 200)
    except Exception:
        ok = False
    # 연속 실패 판정은 순수 모듈 core.ollama_health.evaluate_status (테스트 가능)
    result = _evaluate_ollama_status(ok, _status_cache)
    _status_cache["ts"] = now
    return result

# ---------------------------------------------------------------------------
# Queue API endpoints
# ---------------------------------------------------------------------------

# OpenAPI Discovery — 표준 경로로 스펙 노출
# ---------------------------------------------------------------------------

@app.route("/openapi.json")
@app.route("/.well-known/openapi.json")
def openapi_spec():
    """OpenAPI spec — 외부 CLI/도구 자동 인식용
    ---
    tags: [Discovery]
    summary: OpenAPI 2.0 specification
    description: >
      Returns the OpenAPI 2.0 spec with dynamic host/basePath/schemes fields
      resolved from the incoming request (supports reverse proxy via
      X-Forwarded-Prefix / X-Script-Name headers).
    produces: [application/json]
    responses:
      200:
        description: OpenAPI 2.0 spec object
        schema:
          type: object
    """
    with app.test_client() as c:
        resp = c.get("/apispec.json")
        spec = json.loads(resp.data)
    # 요청 Host 기반으로 host 필드 동적 설정
    spec["host"] = request.host
    # 리버스 프록시 basePath 감지: X-Forwarded-Prefix > X-Script-Name > 기본값
    prefix = (request.headers.get("X-Forwarded-Prefix") or
              request.headers.get("X-Script-Name") or "").rstrip("/")
    if prefix:
        spec["basePath"] = prefix
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        spec["schemes"] = ["https"]
    else:
        spec["schemes"] = ["https", "http"]
    return jsonify(spec)

@app.route("/.well-known/ai-plugin.json")
def ai_plugin_manifest():
    """AI Plugin manifest — ChatGPT/Claude 등 AI 도구 디스커버리용
    ---
    tags: [Discovery]
    summary: AI Plugin manifest
    description: >
      Returns an AI plugin manifest (OpenAI plugin spec v1) that allows
      ChatGPT, Claude, and other AI tools to auto-discover this API.
    produces: [application/json]
    responses:
      200:
        description: AI plugin manifest object
        schema:
          type: object
          properties:
            schema_version: {type: string, example: "v1"}
            name_for_human: {type: string}
            name_for_model: {type: string}
            description_for_human: {type: string}
            description_for_model: {type: string}
            auth: {type: object}
            api: {type: object}
    """
    host = request.host
    scheme = "https" if request.is_secure else "http"
    return jsonify({
        "schema_version": "v1",
        "name_for_human": "WeRu.B Ollama Admin",
        "name_for_model": "ollama_admin",
        "description_for_human": "Ollama LLM batch queue system on iMac M1",
        "description_for_model": "API for submitting LLM inference jobs to an Ollama batch queue. Supports single jobs, batch jobs, status polling, and model management. Models available: qwen2.5:7b (~30 tok/s), qwen2.5:14b (~15 tok/s).",
        "auth": {"type": "service_http", "authorization_type": "bearer"},
        "api": {
            "type": "openapi",
            "url": f"{scheme}://{host}/openapi.json",
        },
        "logo_url": f"{scheme}://{host}/flasgger_static/favicon-32x32.png",
        "contact_email": "admin@example.org",
        "legal_info_url": f"{scheme}://{host}/docs",
    })


# v2.125.46: 무인증 공개 모델 목록 — PACS 가용성 감시·HIS 모델 레지스트리가 인증 없이
# `GET /api/tags`(Ollama 표준 경로)를 부르는데 라우트가 없어 OPTIONS catch-all에 걸려
# **405**였다(라이브 30분 주기·7일 335건 → PACS가 상시 down 판정). 외부에선 /api/health까지
# 전부 401이라 무인증으로 200을 줄 경로가 없었다(운영자 결정으로 이 경로만 공개).
# 공개 범위 = 이름·digest·family만(크기·경로·양자화 등 제외) · 엔진 다운이면 503(감시가 실제 상태를 본다)
# · per-클라이언트 리밋 + 10s 캐시(외부 반복 호출이 Ollama로 새지 않게).
_PUBLIC_TAGS_TTL = 10.0
_public_tags_cache = {"at": 0.0, "body": None, "status": 0}
_public_tags_lock = threading.Lock()


def _public_tags_payload(raw):
    models = []
    for m in (raw.get("models") or []):
        name = m.get("name") or m.get("model")
        if not name:
            continue
        details = m.get("details") or {}
        models.append({
            "name": name,
            "model": m.get("model") or name,
            "digest": m.get("digest"),
            "details": {"family": details.get("family")},
        })
    return {"models": models}


@app.route("/api/tags")
@limiter.limit("30/minute")
def public_tags():
    """Public model list (Ollama-compatible, no auth)
    ---
    tags: [Discovery]
    summary: 공개 모델 목록 — Ollama `/api/tags` 호환 (인증 불요)
    description: >
      가용성 감시·모델 레지스트리용 공개 경로. **인증 헤더 없이** 호출한다.
      Ollama 표준 형식(`models[]`)이되 공개 필드는 name·model·digest·details.family 뿐이다.
      엔진이 응답하지 않으면 **503**(`error: engine_unavailable`) — 200은 엔진이 살아 있다는 뜻.
      응답은 최대 10초 캐시되며 클라이언트별 분당 30회로 제한된다.
    produces: [application/json]
    responses:
      200:
        description: 엔진 정상 — 모델 목록
        schema:
          type: object
          properties:
            models:
              type: array
              items:
                type: object
                properties:
                  name: {type: string, example: "qwen2.5:14b"}
                  model: {type: string, example: "qwen2.5:14b"}
                  digest: {type: string}
                  details:
                    type: object
                    properties:
                      family: {type: string, example: "qwen2"}
      429:
        description: 리밋 초과(분당 30회)
      503:
        description: 엔진 미응답
        schema:
          type: object
          properties:
            models: {type: array, items: {type: object}, example: []}
            error: {type: string, example: "engine_unavailable"}
    """
    now = time.time()
    with _public_tags_lock:
        c = _public_tags_cache
        if c["body"] is not None and now - c["at"] < _PUBLIC_TAGS_TTL:
            return jsonify(c["body"]), c["status"]
    try:
        r = _ollama_session.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}")
        body, status = _public_tags_payload(r.json()), 200
    except Exception as e:
        # 무성 실패 금지 — 감시가 down을 보게 503, 원인은 로그로
        logger.warning("[api/tags] 엔진 조회 실패: %s: %s", type(e).__name__, e)
        body, status = {"models": [], "error": "engine_unavailable"}, 503
    with _public_tags_lock:
        _public_tags_cache.update(at=now, body=body, status=status)
    return jsonify(body), status


# Image Generation (ComfyUI proxy)
# ---------------------------------------------------------------------------

COMFYUI_BASE = "http://127.0.0.1:8188"
FLUX_CHECKPOINT = "flux1-schnell-fp8.safetensors"


# ---------------------------------------------------------------------------
# Korean helpers — delegate to core/korean.py
from core.korean import force_korean as _force_korean
from core.korean import force_korean_token as _force_korean_token
from core.korean import korean_quality_check as _korean_quality_check
from core.korean import generate_korean as _core_generate_korean
from core.korean import KOREAN_RETRY_SUFFIX


def _llm_generate_korean(model, prompt, system, options=None, timeout=120, max_retries=2):
    """LLM 생성 + 한국어 품질 검증 — core/korean.py에 위임"""
    return _core_generate_korean(
        _ollama_session.post, model, prompt, system,
        options=options, timeout=timeout, max_retries=max_retries,
        ollama_base=OLLAMA_BASE,
    )


# Initialize HR Law Blueprint
init_hr_law(
    load_config=load_config, ollama_session=_ollama_session,
    llm_generate_korean=_llm_generate_korean,
    force_korean_token=_force_korean_token,
    ollama_base=OLLAMA_BASE, base_dir=BASE_DIR,
    is_admin_request=is_admin_request,
)


# Initialize Game Blueprint
init_game(
    load_config=load_config, ollama_session=_ollama_session,
    llm_generate_korean=_llm_generate_korean,
    force_korean_token=_force_korean_token,
    ollama_base=OLLAMA_BASE, base_dir=BASE_DIR,
)

# Initialize System Blueprint
init_system(
    get_db=get_db, load_config=load_config, save_config=save_config,
    ollama_session=_ollama_session, is_admin_request=is_admin_request,
    get_worker_alive=get_worker_alive, get_queue_paused=get_queue_paused,
    ollama_base=OLLAMA_BASE, base_dir=BASE_DIR, get_ollama_status=get_ollama_status,
)

# iMac 전용: Smart Gateway 초기화
if _is_imac() and not BATCH_MODE:
    _gw_cfg = load_config()
    _init_gateway(
        ollama_session=_ollama_session,
        ollama_base=OLLAMA_BASE,
        gpu_base=_gw_cfg.get("gpu_base", ""),
        gpu_api_key=_gw_cfg.get("gpu_api_key", ""),
    )

# Initialize Activity Blueprint
init_activity(
    get_db=get_db, load_config=load_config, is_admin_request=is_admin_request,
    get_worker_alive=get_worker_alive, get_queue_paused=get_queue_paused,
    ollama_base=OLLAMA_BASE, base_dir=BASE_DIR,
    llm_response_cache=_llm_response_cache, image_models_cache=_image_models_cache, get_ollama_status=get_ollama_status, is_imac_available=_is_imac_available, imac_ollama_base=IMAC_OLLAMA_BASE, lightweight_models=LIGHTWEIGHT_MODELS,
)

# Initialize iMac Link Blueprint (Phase A 상태 가시화)
init_imac_link(imac_ollama_base=IMAC_OLLAMA_BASE, lightweight_models=LIGHTWEIGHT_MODELS)

# Initialize Queue Blueprint dependencies
init_queue(
    get_db=get_db, load_config=load_config,
    worker_event=worker_event, image_worker_event=image_worker_event,
    auto_priority=auto_priority, validate_webhook_url=_validate_webhook_url,
    llm_response_cache=_llm_response_cache, row_to_dict=row_to_dict,
    flux_checkpoint=FLUX_CHECKPOINT, fast_models=FAST_MODELS,
)

# Initialize Medical Blueprint
init_medical(
    load_config=load_config, ollama_session=_ollama_session,
    llm_generate_korean=_llm_generate_korean,
    ollama_base=OLLAMA_BASE, base_dir=BASE_DIR,
)
# Medical Law 초기화 (v2.51.0)
init_medical_law(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE, base_dir=BASE_DIR)
# P2-1: 의료 환각 감지 초기화
from core.medical_validator import init_medical_validator
init_medical_validator(base_dir=BASE_DIR)

# Initialize Medical CLI Blueprint
from routes.medical import _resolve_medical_tenant as _resolve_med_tenant
init_medical_cli(
    ollama_session=_ollama_session,
    ollama_base=OLLAMA_BASE, base_dir=BASE_DIR,
    resolve_tenant=_resolve_med_tenant,
)

# Initialize Triage Blueprint
init_triage(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE)

# Initialize Orchestrator
from routes.medical import _resolve_medical_tenant as _resolve_orch_tenant
init_orchestrator(flask_app=app,
    ollama_session=_ollama_session, ollama_base=OLLAMA_BASE,
    base_dir=BASE_DIR, resolve_tenant=_resolve_orch_tenant,
)

# Initialize MCP Server (v2.67.0)
from routes.orchestrator import _tool_registry as _orch_tool_registry, _execute_action as _orch_execute_action
init_mcp(tool_registry=_orch_tool_registry, execute_action_fn=_orch_execute_action, flask_app=app)

# Initialize Medical Advanced AI (Phase 4)
from routes.medical import _medical_generate as _med_gen_fn
init_medical_advanced(
    ollama_session=_ollama_session, ollama_base=OLLAMA_BASE,
    medical_generate_fn=_med_gen_fn, base_dir=BASE_DIR,
)

# Initialize Coding AI
init_coding(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE, base_dir=BASE_DIR)

# Initialize Story Engine (v2.52.0)
init_story(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE, base_dir=BASE_DIR)

# Initialize Novel Writer (v2.52.0)
init_novel(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE, base_dir=BASE_DIR)

# Initialize Counseling Orchestration (v2.62.0)
init_counseling(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE, base_dir=BASE_DIR)

# Initialize Meeting Analysis (v2.63.0)
init_meeting(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE, base_dir=BASE_DIR)

# Initialize Web Search / SearXNG (v2.65.0)
from core.web_search import init_web_search
_cfg = load_config()
init_web_search(
    searxng_url=_cfg.get("searxng_url", "http://localhost:8888"),
    timeout=_cfg.get("searxng_timeout", 10),
    max_results=_cfg.get("searxng_max_results", 5),
)

# Initialize WebSocket / Streaming (P3-3)
init_websocket(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE)

# Initialize GPU Scheduler (P3-1)
from core.gpu_scheduler import init_gpu_scheduler, start_scheduler_loop
init_gpu_scheduler(ollama_session=_ollama_session, ollama_base=OLLAMA_BASE)
# v2.125.49: 시간대 프로파일 자동 적용 기본 off — 상주 정책(14b)과 충돌해 헛적재만 했다
start_scheduler_loop(enabled=bool(load_config().get("gpu_schedule_profiles_enabled", False)))

# Initialize Drug Batch Scheduler
from core.drug_batch_scheduler import start_scheduler_loop as start_drug_scheduler
# v2.125.47: 야간 자동 수집 기본 off(LLM_SEED 비수렴 루프·임상 표 오염) — config로만 켠다
start_drug_scheduler(night_enabled=load_config().get("drug_batch_night_enabled", False))

# Initialize Notice Index Scheduler (erp-notice-rag Day 3 — immediate 안전망 + nightly 배치)
try:
    from core.notice_processor import start_notice_scheduler
    start_notice_scheduler(nightly_hour=load_config().get("notice_nightly_hour", 3))
except Exception as _e:
    logger.warning(f"notice scheduler init skipped: {_e}")

# Initialize Medical-Law Incremental Scheduler (medical-law-rag P1 — KHIDI 신규 고시 주기 수집)
# config medlaw_scheduler_enabled=true 일 때만 기동(백필 완료 후 활성화). 기본 off.
try:
    if load_config().get("medlaw_scheduler_enabled", False):
        from core.medlaw_processor import start_medlaw_scheduler
        start_medlaw_scheduler(daily_hour=load_config().get("medlaw_scheduler_hour", 5))
except Exception as _e:
    logger.warning(f"medlaw scheduler init skipped: {_e}")

# Initialize DUR/e약은요 외부 서비스 자동 갱신 스케줄러 (식약처 data.go.kr 정본 주기 동기화)
# 서비스키(config datago_api_key / env DUR_API_KEY) 있고 datago_sync_enabled!=false 일 때 기동.
try:
    if load_config().get("datago_sync_enabled", True):
        from core.dur_sync import start_dur_sync_scheduler
        start_dur_sync_scheduler(interval_days=load_config().get("datago_sync_interval_days", 7))
except Exception as _e:
    logger.warning(f"dur-sync scheduler init skipped: {_e}")

# Initialize HIRA 약제급여목록(약가) 자동 동기화 스케줄러 — hira.or.kr 게시판 최신판
# 주기 확인·fee_codes(DRUG) 안전 교체. _sync_due 게이트로 워커 churn 가로질러 cadence 보장.
try:
    if load_config().get("hira_drug_sync_enabled", True):
        from core.hira_drug_sync import start_hira_drug_sync_scheduler
        start_hira_drug_sync_scheduler(
            interval_days=load_config().get("hira_drug_sync_interval_days", 7))
except Exception as _e:
    logger.warning(f"hira-drug-sync scheduler init skipped: {_e}")

# ---------------------------------------------------------------------------
# Image Model Registry — 설치된 모델 메타데이터
# ---------------------------------------------------------------------------
IMAGE_MODELS = {
    "flux1-schnell-fp8.safetensors": {
        "id": "flux-schnell",
        "name": "FLUX.1 Schnell",
        "family": "flux",
        "description": "초고속 이미지 생성 (4 steps). 빠른 프로토타입에 적합",
        "default_steps": 4,
        "max_steps": 8,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "fast",
        "quality": "standard",
        "capabilities": ["text-to-image"],
        "sampler": "euler",
        "scheduler": "simple",
    },
    "flux1-dev-fp8.safetensors": {
        "id": "flux-dev",
        "name": "FLUX.1 Dev",
        "family": "flux",
        "description": "고품질 이미지 생성 (20-30 steps). 디테일과 텍스트 렌더링 우수",
        "default_steps": 20,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "high",
        "capabilities": ["text-to-image", "high-detail"],
        "sampler": "euler",
        "scheduler": "simple",
    },
    "sd_xl_base_1.0.safetensors": {
        "id": "sdxl",
        "name": "Stable Diffusion XL",
        "family": "sdxl",
        "description": "범용 SDXL 기본 모델. 다양한 스타일 LoRA 호환",
        "default_steps": 25,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "high",
        "capabilities": ["text-to-image", "illustration", "photorealistic", "anime"],
        "sampler": "euler_ancestral",
        "scheduler": "normal",
    },
    "juggernaut-xl-v9.safetensors": {
        "id": "juggernaut",
        "name": "Juggernaut XL v9",
        "family": "sdxl",
        "description": "실사 모델. 인물, 풍경, 제품 사진급 퀄리티",
        "default_steps": 30,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "ultra",
        "capabilities": ["text-to-image", "photorealistic", "portrait", "landscape", "product"],
        "sampler": "euler_ancestral",
        "scheduler": "normal",
    },
    "juggernaut-xl-ragnarok.safetensors": {
        "id": "juggernaut-ragnarok",
        "name": "Juggernaut XL Ragnarok",
        "family": "sdxl",
        "description": "Juggernaut 최신판. 피부 텍스처/조명/해부학 대폭 개선. 실사 최강",
        "default_steps": 30,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "ultra",
        "capabilities": ["text-to-image", "photorealistic", "portrait", "landscape", "product", "cinematic"],
        "sampler": "euler_ancestral",
        "scheduler": "normal",
    },
    "dreamshaper-xl-v2-turbo.safetensors": {
        "id": "dreamshaper",
        "name": "DreamShaper XL Turbo",
        "family": "sdxl",
        "description": "일러스트+실사 균형. 판타지, 컨셉아트에 강함. Turbo로 빠른 생성",
        "default_steps": 8,
        "max_steps": 30,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "fast",
        "quality": "high",
        "capabilities": ["text-to-image", "illustration", "concept-art", "fantasy", "photorealistic"],
        "sampler": "dpmpp_sde",
        "scheduler": "karras",
    },
    "animagine-xl-3.1.safetensors": {
        "id": "animagine",
        "name": "Animagine XL 3.1",
        "family": "sdxl",
        "description": "애니메이션/만화 스타일 특화. 캐릭터 일러스트에 최적",
        "default_steps": 25,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "high",
        "capabilities": ["text-to-image", "anime", "illustration", "character"],
        "sampler": "euler_ancestral",
        "scheduler": "normal",
    },
    "realvis-xl-v5.safetensors": {
        "id": "realvis",
        "name": "RealVisXL v5",
        "family": "sdxl",
        "description": "실사 사진 특화. 인물 촬영, 제품 사진, 건축 렌더링에 강함",
        "default_steps": 25,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "ultra",
        "capabilities": ["text-to-image", "photorealistic", "portrait", "product", "architecture"],
        "sampler": "euler_ancestral",
        "scheduler": "normal",
    },
    "cyberrealistic-xl-v6.safetensors": {
        "id": "cyberrealistic",
        "name": "CyberRealistic XL v6",
        "family": "sdxl",
        "description": "자연스러운 피부/조명, 영화적 분위기. 인물/패션/시네마틱 실사에 강함",
        "default_steps": 25,
        "max_steps": 50,
        "default_size": [1024, 1024],
        "max_size": 2048,
        "speed": "normal",
        "quality": "ultra",
        "capabilities": ["text-to-image", "photorealistic", "portrait", "cinematic", "fashion"],
        "sampler": "euler_ancestral",
        "scheduler": "normal",
    },
}

# 스타일 프리셋 — 프롬프트 앞/뒤에 자동 추가
IMAGE_STYLE_PRESETS = {
    "none": {"prefix": "", "suffix": "", "description": "스타일 없음 (원본 프롬프트 그대로)"},
    "photorealistic": {
        "prefix": "(photorealistic:1.4), (RAW photo:1.2), 8k uhd, dslr, high quality, ",
        "suffix": ", sharp focus, depth of field, film grain, natural lighting",
        "description": "사실적 사진 스타일",
    },
    "illustration": {
        "prefix": "(best quality:1.4), (masterpiece:1.3), digital illustration, ",
        "suffix": ", vibrant colors, detailed linework, professional illustration",
        "description": "디지털 일러스트레이션",
    },
    "anime": {
        "prefix": "(anime style:1.4), (best quality:1.3), masterpiece, ",
        "suffix": ", anime key visual, cel shading, vivid colors",
        "description": "애니메이션 스타일",
    },
    "oil-painting": {
        "prefix": "(oil painting:1.4), (masterpiece:1.3), classical art, ",
        "suffix": ", brush strokes, canvas texture, rich colors, dramatic lighting",
        "description": "유화 스타일",
    },
    "concept-art": {
        "prefix": "(concept art:1.4), (trending on artstation:1.2), digital painting, ",
        "suffix": ", highly detailed, cinematic lighting, volumetric, epic composition",
        "description": "컨셉 아트 스타일",
    },
    "watercolor": {
        "prefix": "(watercolor painting:1.4), soft edges, ",
        "suffix": ", paper texture, fluid colors, gentle blending, artistic",
        "description": "수채화 스타일",
    },
    "pixel-art": {
        "prefix": "(pixel art:1.5), retro game style, crisp hard edges, ",
        "suffix": ", 16-bit, limited color palette, no anti-aliasing, sharp pixels, white background",
        "description": "픽셀 아트 스타일",
        "lora": "pixel-art-xl.safetensors",
        "lora_strength": 0.85,
        "default_size": [512, 512],
    },
    "pixel-art-character": {
        "prefix": "(pixel art:1.6), (game character sprite:1.3), front-facing, centered, single character, clean outline, ",
        "suffix": ", 32-bit pixel art, RPG character sprite, limited color palette, crisp hard edges, no anti-aliasing, solid white background",
        "negative": "(blurry:1.3), smooth gradients, photorealistic, 3d render, anti-aliasing, multiple characters, complex background",
        "description": "MMORPG 캐릭터 스프라이트 (픽셀 아트)",
        "lora": "pixel-art-xl.safetensors",
        "lora_strength": 0.9,
        "default_size": [512, 512],
    },
    "pixel-art-sprite": {
        "prefix": "(pixel art:1.6), (sprite sheet:1.3), character turnaround, front side back views, game asset, ",
        "suffix": ", 16-bit pixel art, retro RPG style, limited palette, crisp pixels, white background, multiple poses",
        "negative": "(blurry:1.3), smooth gradients, photorealistic, 3d render, anti-aliasing",
        "description": "스프라이트 시트 (다방향 캐릭터)",
        "lora": "pixel-art-xl.safetensors",
        "lora_strength": 0.9,
        "default_size": [1024, 512],
    },
    "pixel-art-scene": {
        "prefix": "(pixel art:1.5), (game background:1.3), tileable, game environment, ",
        "suffix": ", 16-bit pixel art, retro game scene, limited color palette, crisp pixels, side-scrolling game style",
        "negative": "(blurry:1.3), smooth gradients, photorealistic, 3d render",
        "description": "게임 배경/맵 타일 (픽셀 아트)",
        "lora": "pixel-art-xl.safetensors",
        "lora_strength": 0.8,
        "default_size": [1024, 512],
    },
    "logo": {
        "prefix": "(logo design:1.4), minimalist, vector style, ",
        "suffix": ", clean lines, simple background, professional branding",
        "description": "로고/아이콘 디자인",
    },
    "high-detail": {
        "prefix": "",
        "suffix": "",
        "description": "고디테일 강화 (Detail Tweaker LoRA)",
        "lora": "detail-tweaker-xl.safetensors",
        "lora_strength": 0.7,
    },
    "lcm-fast": {
        "prefix": "",
        "suffix": "",
        "description": "LCM 초고속 생성 (4-8 step, SDXL 전용)",
        "lora": "lcm-lora-sdxl.safetensors",
        "lora_strength": 1.0,
        "lcm_mode": True,
    },
    "flux-realism": {
        "prefix": "(photorealistic:1.2), RAW photo, ",
        "suffix": ", natural skin texture, sharp focus, natural lighting, film grain",
        "description": "FLUX 실사 강화 (Realism LoRA, FLUX 모델 전용)",
        "lora": "flux-realism-lora.safetensors",
        "lora_strength": 0.8,
    },
}

def _get_model_meta(checkpoint):
    """모델 메타데이터 반환 (미등록 모델은 기본값)"""
    if checkpoint in IMAGE_MODELS:
        return IMAGE_MODELS[checkpoint]
    return {
        "id": checkpoint, "name": checkpoint, "family": "unknown",
        "default_steps": 20, "max_steps": 50, "default_size": [1024, 1024],
        "max_size": 2048, "speed": "normal", "quality": "standard",
        "capabilities": ["text-to-image"], "sampler": "euler", "scheduler": "normal",
    }

def _build_flux_workflow(prompt, width=1024, height=1024, steps=4, seed=None, checkpoint=None):
    import random
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    ckpt = checkpoint or FLUX_CHECKPOINT
    meta = _get_model_meta(ckpt)
    sampler = meta.get("sampler", "euler")
    scheduler = meta.get("scheduler", "simple")
    return {
        "6":  {"inputs": {"text": prompt, "clip": ["30", 1]}, "class_type": "CLIPTextEncode"},
        "8":  {"inputs": {"samples": ["13", 0], "vae": ["30", 2]}, "class_type": "VAEDecode"},
        "9":  {"inputs": {"filename_prefix": "ollama-admin", "images": ["8", 0]}, "class_type": "SaveImage"},
        "13": {"inputs": {"noise": ["25", 0], "guider": ["22", 0], "sampler": ["16", 0],
                          "sigmas": ["17", 0], "latent_image": ["27", 0]}, "class_type": "SamplerCustomAdvanced"},
        "16": {"inputs": {"sampler_name": sampler}, "class_type": "KSamplerSelect"},
        "17": {"inputs": {"scheduler": scheduler, "steps": steps, "denoise": 1.0,
                          "model": ["30", 0]}, "class_type": "BasicScheduler"},
        "22": {"inputs": {"model": ["30", 0], "conditioning": ["6", 0]}, "class_type": "BasicGuider"},
        "25": {"inputs": {"noise_seed": seed}, "class_type": "RandomNoise"},
        "27": {"inputs": {"width": width, "height": height, "batch_size": 1},
               "class_type": "EmptySD3LatentImage"},
        "30": {"inputs": {"ckpt_name": ckpt}, "class_type": "CheckpointLoaderSimple"},
    }

def _build_flux_lora_workflow(prompt, width=1024, height=1024, steps=20, seed=None,
                               checkpoint=None, lora_name=None, lora_strength=0.8):
    """FLUX + LoRA workflow — LoraLoader node inserted"""
    import random
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    ckpt = checkpoint or FLUX_CHECKPOINT
    meta = _get_model_meta(ckpt)
    sampler = meta.get("sampler", "euler")
    scheduler = meta.get("scheduler", "simple")
    return {
        "30": {"inputs": {"ckpt_name": ckpt}, "class_type": "CheckpointLoaderSimple"},
        "40": {"inputs": {
            "lora_name": lora_name,
            "strength_model": lora_strength,
            "strength_clip": lora_strength,
            "model": ["30", 0], "clip": ["30", 1],
        }, "class_type": "LoraLoader"},
        "6":  {"inputs": {"text": prompt, "clip": ["40", 1]}, "class_type": "CLIPTextEncode"},
        "8":  {"inputs": {"samples": ["13", 0], "vae": ["30", 2]}, "class_type": "VAEDecode"},
        "9":  {"inputs": {"filename_prefix": "ollama-admin", "images": ["8", 0]}, "class_type": "SaveImage"},
        "13": {"inputs": {"noise": ["25", 0], "guider": ["22", 0], "sampler": ["16", 0],
                          "sigmas": ["17", 0], "latent_image": ["27", 0]}, "class_type": "SamplerCustomAdvanced"},
        "16": {"inputs": {"sampler_name": sampler}, "class_type": "KSamplerSelect"},
        "17": {"inputs": {"scheduler": scheduler, "steps": steps, "denoise": 1.0,
                          "model": ["40", 0]}, "class_type": "BasicScheduler"},
        "22": {"inputs": {"model": ["40", 0], "conditioning": ["6", 0]}, "class_type": "BasicGuider"},
        "25": {"inputs": {"noise_seed": seed}, "class_type": "RandomNoise"},
        "27": {"inputs": {"width": width, "height": height, "batch_size": 1},
               "class_type": "EmptySD3LatentImage"},
    }

def _build_sdxl_workflow(prompt, negative="", width=1024, height=1024, steps=25, seed=None, checkpoint=None, cfg=7.0):
    """SDXL 계열 모델용 KSampler 워크플로우"""
    import random
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    ckpt = checkpoint or "sd_xl_base_1.0.safetensors"
    meta = _get_model_meta(ckpt)
    sampler = meta.get("sampler", "euler_ancestral")
    scheduler = meta.get("scheduler", "normal")
    if not negative:
        negative = "(worst quality:1.4), (low quality:1.4), blurry, deformed, ugly, bad anatomy"
    return {
        "1": {"inputs": {"ckpt_name": ckpt}, "class_type": "CheckpointLoaderSimple"},
        "2": {"inputs": {"text": prompt, "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
        "3": {"inputs": {"text": negative, "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
        "4": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "5": {"inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler, "scheduler": scheduler,
            "denoise": 1.0,
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        }, "class_type": "KSampler"},
        "6": {"inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
        "7": {"inputs": {"filename_prefix": "ollama-admin", "images": ["6", 0]}, "class_type": "SaveImage"},
    }



# Initialize Image Blueprint dependencies
init_image(
    get_db=get_db, load_config=load_config, save_config=save_config,
    worker_event=worker_event, image_worker_event=image_worker_event,
    validate_webhook_url=_validate_webhook_url, image_models_cache=_image_models_cache,
    get_model_meta=_get_model_meta, get_image_dir=_get_image_dir,
    get_image_stats=_get_image_stats, cleanup_images=_cleanup_images,
    is_admin_request=is_admin_request, comfyui_base=COMFYUI_BASE,
    flux_checkpoint=FLUX_CHECKPOINT, image_models=IMAGE_MODELS,
    image_style_presets=IMAGE_STYLE_PRESETS, base_dir=BASE_DIR,
)

# S3 Storage 초기화 (v2.70.0 — Wasabi 이미지 저장)
from core.s3_storage import init_s3 as _init_s3
_s3_cfg = load_config()
_init_s3(_s3_cfg)
def _build_sdxl_lora_workflow(prompt, negative="", width=1024, height=1024, steps=25, seed=None,
                               checkpoint=None, cfg=7.0, lora_name=None, lora_strength=0.8,
                               sampler_override=None, scheduler_override=None):
    """SDXL + LoRA 워크플로우 — LoraLoader 노드 삽입 (LCM 모드 지원)"""
    import random
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    ckpt = checkpoint or "sd_xl_base_1.0.safetensors"
    meta = _get_model_meta(ckpt)
    sampler = sampler_override or meta.get("sampler", "euler_ancestral")
    scheduler = scheduler_override or meta.get("scheduler", "normal")
    if not negative:
        negative = "(worst quality:1.4), (low quality:1.4), blurry, deformed, ugly, bad anatomy"
    return {
        "1": {"inputs": {"ckpt_name": ckpt}, "class_type": "CheckpointLoaderSimple"},
        "10": {"inputs": {
            "lora_name": lora_name,
            "strength_model": lora_strength,
            "strength_clip": lora_strength,
            "model": ["1", 0], "clip": ["1", 1],
        }, "class_type": "LoraLoader"},
        "2": {"inputs": {"text": prompt, "clip": ["10", 1]}, "class_type": "CLIPTextEncode"},
        "3": {"inputs": {"text": negative, "clip": ["10", 1]}, "class_type": "CLIPTextEncode"},
        "4": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "5": {"inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler, "scheduler": scheduler,
            "denoise": 1.0,
            "model": ["10", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        }, "class_type": "KSampler"},
        "6": {"inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
        "7": {"inputs": {"filename_prefix": "ollama-admin", "images": ["6", 0]}, "class_type": "SaveImage"},
    }


def _build_sdxl_ipadapter_workflow(prompt, negative="", width=1024, height=1024, steps=25, seed=None,
                                    checkpoint=None, cfg=7.0, reference_image=None,
                                    ip_adapter_model="ip-adapter-plus_sdxl_vit-h.safetensors",
                                    clip_vision_model="CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
                                    ip_adapter_weight=0.7, ip_adapter_mode="full",
                                    lora_name=None, lora_strength=0.8):
    """SDXL + IP-Adapter 워크플로우 — 캐릭터 일관성 유지
    IPAdapterUnifiedLoader를 사용하여 모델/클립비전을 자동 로드"""
    import random
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    ckpt = checkpoint or "sd_xl_base_1.0.safetensors"
    meta = _get_model_meta(ckpt)
    sampler = meta.get("sampler", "euler_ancestral")
    scheduler = meta.get("scheduler", "normal")
    if not negative:
        negative = "(worst quality:1.4), (low quality:1.4), blurry, deformed, ugly, bad anatomy"

    # 모델 소스: LoRA 사용 시 LoRA 출력, 아니면 체크포인트 직접
    model_source = ["1", 0]
    clip_source = ["1", 1]

    workflow = {
        "1": {"inputs": {"ckpt_name": ckpt}, "class_type": "CheckpointLoaderSimple"},
    }

    # LoRA 삽입 (옵션)
    if lora_name:
        workflow["20"] = {"inputs": {
            "lora_name": lora_name,
            "strength_model": lora_strength,
            "strength_clip": lora_strength,
            "model": ["1", 0], "clip": ["1", 1],
        }, "class_type": "LoraLoader"}
        model_source = ["20", 0]
        clip_source = ["20", 1]

    # IPAdapterUnifiedLoader — 모델/클립비전을 자동으로 로드
    ipa_preset = "PLUS FACE (portraits)" if ip_adapter_mode == "face" else "PLUS (high strength)"
    workflow["11"] = {"inputs": {
        "model": model_source,
        "preset": ipa_preset,
    }, "class_type": "IPAdapterUnifiedLoader"}

    # 참조 이미지 로드
    workflow["13"] = {"inputs": {"image": reference_image, "upload": "image"}, "class_type": "LoadImage"}

    # IP-Adapter 적용 — 참조 이미지를 모델에 주입
    workflow["15"] = {"inputs": {
        "model": ["11", 0],
        "ipadapter": ["11", 1],
        "image": ["13", 0],
        "weight": ip_adapter_weight,
        "start_at": 0.0,
        "end_at": 1.0,
        "weight_type": "standard",
    }, "class_type": "IPAdapter"}

    # 텍스트 인코딩
    workflow["2"] = {"inputs": {"text": prompt, "clip": clip_source}, "class_type": "CLIPTextEncode"}
    workflow["3"] = {"inputs": {"text": negative, "clip": clip_source}, "class_type": "CLIPTextEncode"}

    # 빈 잠재 이미지
    workflow["4"] = {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage"}

    # KSampler — IP-Adapter 적용된 모델 사용
    workflow["5"] = {"inputs": {
        "seed": seed, "steps": steps, "cfg": cfg,
        "sampler_name": sampler, "scheduler": scheduler,
        "denoise": 1.0,
        "model": ["15", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
    }, "class_type": "KSampler"}

    workflow["6"] = {"inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"}
    workflow["7"] = {"inputs": {"filename_prefix": "ollama-admin", "images": ["6", 0]}, "class_type": "SaveImage"}

    return workflow


def _sana_generate_job(job, opts):
    """SANA 무스왑 fast-lane — 사이드카(127.0.0.1:8189) co-resident 생성.

    RAW PNG를 sana_out/에 저장하고 결과를 반환. 마킹(§31)/S3/서빙은 route 계층
    (_auto_s3_upload/api_image_view)이 ComfyUI 경로와 동일하게 처리 → 바이트 소스만 디스크.
    LLM 언로드 없이 상주 qwen과 co-resident(무스왑). 실패 시 예외→워커 retry.
    """
    import requests as _rq
    prompt = job["prompt"]
    style = opts.get("style", "none")
    preset = IMAGE_STYLE_PRESETS.get(style, {}) if style and style != "none" else {}
    if preset:
        prompt = preset.get("prefix", "") + prompt + preset.get("suffix", "")
    negative = opts.get("negative_prompt", "") or (preset.get("negative", "") if preset else "")
    payload = {
        "prompt": prompt, "negative": negative,
        "width": int(opts.get("width", 1024)), "height": int(opts.get("height", 1024)),
        "steps": int(opts.get("steps", 20)),
    }
    if opts.get("seed") is not None:
        try:
            payload["seed"] = int(opts["seed"])
        except (TypeError, ValueError):
            pass
    base = load_config().get("sana_service_url", "http://127.0.0.1:8189")
    r = _rq.post(f"{base}/generate", json=payload, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"sana sidecar {r.status_code}: {r.text[:200]}")
    sana_dir = os.path.join(BASE_DIR, "sana_out")
    os.makedirs(sana_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"sana_{job['id'][:8]}_{ts}.png"
    with open(os.path.join(sana_dir, filename), "wb") as f:
        f.write(r.content)
    return {"filename": filename, "subfolder": "sana", "engine": "sana",
            "prompt_id": job["id"], "sana_sec": r.headers.get("X-Sana-Sec")}


def _unload_sana_sidecar():
    """ComfyUI 대형잡(SDXL/Flux) 전 SANA 사이드카 VRAM 반납 — 상호배제(best-effort)."""
    try:
        import requests as _rq
        base = load_config().get("sana_service_url", "http://127.0.0.1:8189")
        _rq.post(f"{base}/unload", timeout=10)
    except Exception as e:
        logger.debug("sana sidecar unload skipped: %s", e)


def _process_image_job(job):
    """ComfyUI 이미지 생성 — 완료까지 폴링 (최대 900초)"""
    opts = {}
    if job.get("options"):
        try:
            opts = json.loads(job["options"])
        except (json.JSONDecodeError, TypeError):
            pass

    # SANA 무스왑 fast-lane: engine=sana면 사이드카로 즉시 생성(co-resident, LLM 스왑 없음)
    if opts.get("engine") == "sana":
        return _sana_generate_job(job, opts)

    checkpoint = job.get("model") or FLUX_CHECKPOINT
    meta = _get_model_meta(checkpoint)

    width     = int(opts.get("width", meta["default_size"][0]))
    height    = int(opts.get("height", meta["default_size"][1]))
    steps     = min(int(opts.get("steps", meta["default_steps"])), meta["max_steps"])
    seed      = opts.get("seed")

    # 스타일 프리셋 적용
    style = opts.get("style", "none")
    prompt = job["prompt"]
    preset = IMAGE_STYLE_PRESETS.get(style, {}) if style != "none" else {}
    if preset:
        prompt = preset.get("prefix", "") + prompt + preset.get("suffix", "")

    negative = opts.get("negative_prompt", "")
    # 스타일 프리셋에 기본 negative가 있으면 병합
    if preset.get("negative") and not negative:
        negative = preset["negative"]

    # 스타일 프리셋에 기본 해상도가 있으면 적용 (사용자 미지정 시)
    if preset.get("default_size"):
        if "width" not in opts:
            width = preset["default_size"][0]
        if "height" not in opts:
            height = preset["default_size"][1]

    # 모델 family에 따라 워크플로우 분기
    lora_name = preset.get("lora")
    lora_strength = preset.get("lora_strength", 0.8)

    # LCM 모드: sampler/scheduler/cfg 자동 오버라이드
    lcm_mode = preset.get("lcm_mode", False) or opts.get("lcm_mode", False)
    if lcm_mode:
        steps = min(steps, 8)  # LCM은 4-8 step이 최적
        if "steps" not in opts:
            steps = 6

    # 캐릭터 일관성 (IP-Adapter)
    character_id = opts.get("character_id")
    reference_image = None
    if character_id:
        try:
            conn_ch = get_db()
            row = conn_ch.execute("SELECT reference_image FROM characters WHERE id=?", (character_id,)).fetchone()
            conn_ch.close()
            if row:
                reference_image = row[0]  # e.g. "characters/uuid.png"
        except Exception as e:
            logger.debug("character reference image lookup failed: %s", e)

    ip_adapter_strength = float(opts.get("ip_adapter_strength", 0.7))
    ip_adapter_mode = opts.get("ip_adapter_mode", "full")

    if reference_image and meta.get("family") == "sdxl":
        # IP-Adapter 워크플로우
        ip_model = "ip-adapter-plus-face_sdxl_vit-h.safetensors" if ip_adapter_mode == "face" else "ip-adapter-plus_sdxl_vit-h.safetensors"
        cfg = float(opts.get("cfg", 7.0))
        workflow = _build_sdxl_ipadapter_workflow(
            prompt, negative=negative, width=width, height=height,
            steps=steps, seed=seed, checkpoint=checkpoint, cfg=cfg,
            reference_image=reference_image,
            ip_adapter_model=ip_model,
            ip_adapter_weight=ip_adapter_strength,
            lora_name=lora_name, lora_strength=lora_strength,
        )
    elif meta.get("family") == "sdxl":
        cfg = float(opts.get("cfg", 7.0))
        if lcm_mode:
            cfg = float(opts.get("cfg", 1.8))  # LCM 최적 CFG
        if lora_name:
            sampler_override = "lcm" if lcm_mode else None
            scheduler_override = "sgm_uniform" if lcm_mode else None
            workflow = _build_sdxl_lora_workflow(prompt, negative=negative, width=width, height=height,
                                                 steps=steps, seed=seed, checkpoint=checkpoint, cfg=cfg,
                                                 lora_name=lora_name, lora_strength=lora_strength,
                                                 sampler_override=sampler_override if lcm_mode else None,
                                                 scheduler_override=scheduler_override if lcm_mode else None)
        else:
            workflow = _build_sdxl_workflow(prompt, negative=negative, width=width, height=height,
                                            steps=steps, seed=seed, checkpoint=checkpoint, cfg=cfg)
    elif lora_name:
        # FLUX + LoRA
        workflow = _build_flux_lora_workflow(prompt, width=width, height=height,
                                             steps=steps, seed=seed, checkpoint=checkpoint,
                                             lora_name=lora_name, lora_strength=lora_strength)
    else:
        workflow = _build_flux_workflow(prompt, width=width, height=height,
                                        steps=steps, seed=seed, checkpoint=checkpoint)

    r = requests.post(f"{COMFYUI_BASE}/prompt", json={"prompt": workflow}, timeout=10)
    r.raise_for_status()
    prompt_id = r.json()["prompt_id"]

    deadline = time.time() + 120  # 2분 (대규모 배치 시 블록 최소화)
    while time.time() < deadline:
        time.sleep(3)
        try:
            h = requests.get(f"{COMFYUI_BASE}/history/{prompt_id}", timeout=5).json()
            if prompt_id in h:
                outputs = h[prompt_id].get("outputs", {})
                for node_out in outputs.values():
                    images = node_out.get("images", [])
                    if images:
                        img = images[0]
                        return {
                            "filename": img["filename"],
                            "subfolder": img.get("subfolder", ""),
                            "prompt_id": prompt_id,
                        }
        except Exception as e:
            logger.debug("ComfyUI history poll failed: %s", e)

    raise Exception("ComfyUI timeout after 120s")








@app.route("/drug-pipeline")
def drug_pipeline_page():
    """Drug Data Pipeline — 전용 페이지"""
    from flask import render_template
    return render_template("drug_pipeline.html")


@app.route("/model-manager")
@require_web_login
def model_manager_page():
    """Model Manager — 모델 관리 전용 페이지"""
    from flask import render_template
    return render_template("model_manager.html")


@app.route("/orchestration-editor")
@require_web_login
def orchestration_editor_page():
    """Orchestration Editor — 워크플로우 편집기 전용 페이지"""
    from flask import render_template
    return render_template("orchestration_editor.html")


@app.route("/admin-console")
@app.route("/admin-console/")
@app.route("/admin-console/<path:subpath>")
@limiter.exempt
def admin_console(subpath=""):
    """Next.js Admin Console (static export)"""
    import os as _os
    base = _os.path.join(BASE_DIR, "static", "admin-console")
    # 🔴 경로 순회 봉합(v2.125.17) — 이 라우트는 **인증이 없고** `@limiter.exempt`다.
    #    2026-08-25 라이브 재현: `/admin-console/../../config.json` → 200 + config.json 전문
    #    (S3 시크릿·워커 토큰 포함). `%2e%2e` 변종도 통과했다(nginx 정규화 우회).
    #    `<path:subpath>`는 `../`를 정규화하지 않으므로 **조립 후 base 안인지 확인**한다
    #    (`basename`은 정적 자산의 하위 디렉터리 구조를 깨므로 여기선 realpath 봉쇄를 쓴다).
    _base_real = _os.path.realpath(base)
    def _inside(p):
        rp = _os.path.realpath(p)
        return rp == _base_real or rp.startswith(_base_real + _os.sep)
    # Try exact path, then path/index.html, then fallback
    for candidate in [
        _os.path.join(base, subpath),
        _os.path.join(base, subpath, "index.html"),
        _os.path.join(base, subpath + ".html"),
    ]:
        if not _inside(candidate):
            continue
        if _os.path.isfile(candidate):
            mime = "text/html; charset=utf-8"
            if candidate.endswith(".js"): mime = "application/javascript; charset=utf-8"
            elif candidate.endswith(".css"): mime = "text/css; charset=utf-8"
            elif candidate.endswith(".json"): mime = "application/json; charset=utf-8"
            elif candidate.endswith(".png"): mime = "image/png"
            elif candidate.endswith(".svg"): mime = "image/svg+xml"
            elif candidate.endswith(".ico"): mime = "image/x-icon"
            elif candidate.endswith(".txt"): mime = "text/plain; charset=utf-8"
            mode = "rb" if mime.startswith("image") else "r"
            with open(candidate, mode) as f:
                return Response(f.read(), mimetype=mime)
    with open(_os.path.join(base, "index.html"), "r") as f:
        return Response(f.read(), mimetype="text/html; charset=utf-8")

@app.route("/hr-law-admin")
def hr_law_admin_page():
    """HR Law Admin Console (별도 페이지)"""
    gpath = os.path.join(BASE_DIR, 'static', 'hr-law-admin', 'index.html')
    with open(gpath, 'r') as f:
        return Response(f.read(), mimetype='text/html')

@app.route("/game-admin")
def game_admin_page():
    """Game NPC Admin Console (별도 페이지)"""
    gpath = os.path.join(BASE_DIR, 'static', 'game-admin', 'index.html')
    with open(gpath, 'r') as f:
        return Response(f.read(), mimetype='text/html')

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute")
def login():
    error = None
    next_url = request.args.get("next", "/")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _verify_web_login(username, password):
            session.clear()
            session["web_logged_in"] = True
            session["web_user"] = username
            session["web_last_active"] = time.time()
            # 사용자 권한 세션 저장
            try:
                _uconn = _get_users_db()
                _urow = _uconn.execute("SELECT role, permissions FROM users WHERE username=?", (username,)).fetchone()
                _uconn.close()
                if _urow:
                    session["web_role"] = _urow["role"]
                    session["web_permissions"] = _urow["permissions"]
                else:
                    session["web_role"] = "admin"
                    session["web_permissions"] = json.dumps(ROLE_DEFAULTS["admin"])
            except Exception:
                session["web_role"] = "admin"
                session["web_permissions"] = json.dumps(ROLE_DEFAULTS["admin"])
            session.permanent = False
            logger.info(f"Web login: {username} from {request.remote_addr}")
            if next_url == "/" or not next_url:
                return redirect(url_for("index"))
            else:
                prefix = request.script_root or ""
                return redirect(prefix + next_url)
        else:
            error = "아이디 또는 비밀번호가 올바르지 않습니다."
            logger.warning(f"Web login failed: {username} from {request.remote_addr}")
    # 서버 환경에 따른 동적 타이틀
    env = _detect_server_env()
    if env.get('has_gpu') and env.get('gpu_name'):
        _gpu_short = env['gpu_name'].replace('NVIDIA GeForce ', '').replace('NVIDIA ', '')
        server_label = f'GPU ({_gpu_short})'
    elif env.get('platform') == 'darwin':
        server_label = 'iMac'
    else:
        server_label = 'CPU'
    server_title = f'WeRU.B AI Server Admin - {server_label}'
    try:
        return render_template('login.html', error=error, next_url=next_url, server_title=server_title)
    except Exception:
        return render_template_string(LOGIN_TEMPLATE, error=error, next=next_url, server_title=server_title)

@app.route("/logout")
def logout():
    user = session.get("web_user", "unknown")
    session.clear()
    logger.info(f"Web logout: {user}")
    return redirect(url_for("login"))

@app.route("/api/admin/change-password", methods=["POST"])
@require_web_login
def change_password():
    """어드민 비밀번호 변경
    ---
    tags: [Admin]
    summary: Change admin password
    description: 웹 로그인 세션이 필요합니다. 현재 비밀번호 확인 후 새 비밀번호로 변경합니다.
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [current_password, new_password]
          properties:
            current_password:
              type: string
              description: 현재 비밀번호
            new_password:
              type: string
              description: 새 비밀번호 (최소 8자)
              minLength: 8
    responses:
      200:
        description: 비밀번호 변경 성공
        schema:
          type: object
          properties:
            ok: {type: boolean, example: true}
      400:
        description: 새 비밀번호가 8자 미만
        schema:
          type: object
          properties:
            error: {type: string}
      403:
        description: 현재 비밀번호 불일치 또는 세션 없음
        schema:
          type: object
          properties:
            error: {type: string}
    """
    data = request.json or {}
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    cfg = load_config()
    if not _verify_web_login(cfg.get("admin_user", "admin"), current):
        return jsonify({"error": "현재 비밀번호가 올바르지 않습니다."}), 403
    if len(new_pw) < 8:
        return jsonify({"error": "비밀번호는 최소 8자 이상이어야 합니다."}), 400
    new_hash = _hash_password(new_pw)
    # 인증은 users.db만 사용 → config admin_password_hash(write-only 잔재) 기록 제거
    try:
        uconn = _get_users_db()
        uconn.execute("UPDATE users SET password_hash=?, updated_at=datetime('now') WHERE username=?",
            (new_hash, session.get("web_user", "admin")))
        uconn.commit()
        uconn.close()
    except Exception: pass
    logger.info(f"Admin password changed by {session.get('web_user')}")
    session.clear()
    return jsonify({"ok": True, "message": "비밀번호가 변경되었습니다. 다시 로그인하세요."})

@app.route("/")
@require_web_login
def index():
    # 사용자 정보를 서버 사이드에서 주입
    username = session.get("web_user", "admin")
    role = "admin"
    perms_str = "{}"
    display_name = username
    try:
        conn = _get_users_db()
        user = conn.execute("SELECT role, permissions, display_name FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user:
            role = user["role"]
            perms_str = user["permissions"] or "{}"
            display_name = user["display_name"] or username
    except Exception: pass
    # HTML에 주입할 JS 변수 — json.dumps로 만들고 `</`를 이스케이프한다.
    # 🔴 v2.125.66까지는 문자열 이어 붙이기였다: 이름에서 `"`만 빼고, permissions는 **DB 원문을 그대로** 넣었다.
    #    ①permissions가 JSON 객체가 아닌 문자열로 저장되면(PUT /api/admin/users가 문자열을 그대로 저장했다)
    #      인라인 스크립트가 문법 오류 → 그 사용자의 관리 화면 전체가 동작 불능
    #    ②display_name·permissions에 `</script>`가 들어가면 스크립트 탈출(XSS). 설정 권한은 admin뿐이라 위험은 낮다.
    try:
        _perms_obj = json.loads(perms_str)
        if not isinstance(_perms_obj, dict):
            _perms_obj = {}
    except Exception:  # noqa: BLE001 — 깨진 권한은 '권한 없음'으로(모르면 보이지 않게)
        _perms_obj = {}
    _user_json = json.dumps({"username": username, "role": role, "displayName": display_name,
                             "permissions": _perms_obj}, ensure_ascii=False).replace("</", "<\\/")
    user_inject = '<script>window.__USER__=' + _user_json + ';</script>'
    # 화면에 보일 탭 — 템플릿 렌더에 넘긴다. 예전엔 인라인 HTML(삭제됨·v2.125.66)만 고치고
    # render_template 결과로 덮어써서 **실제 화면에선 숨김이 한 번도 적용되지 않았다**(D6).
    allowed_tabs = None  # None = 전부(admin)
    if role != "admin":
        _p = _perms_obj  # 위에서 파싱·검증한 값(객체가 아니면 {})
        # `ok is True`만 허용 — 권한 JSON에 빠진 탭·"true" 문자열은 숨긴다(모르면 보이지 않게).
        # users는 권한 JSON과 무관하게 admin 전용(사용자 관리 API도 역할 admin을 따로 검사한다).
        # ⚠️ 이 숨김은 **화면 편의**다. 서버 강제는 is_admin_request(관리자급)·_tab_permission_check(탭별).
        allowed_tabs = [t for t, ok in _p.items() if ok is True and t != "users"]
    # 서버 환경에 따른 동적 타이틀
    env = _detect_server_env()
    if env.get('has_gpu') and env.get('gpu_name'):
        _gpu_short = env['gpu_name'].replace('NVIDIA GeForce ', '').replace('NVIDIA ', '')
        server_label = f'GPU ({_gpu_short})'
    elif env.get('platform') == 'darwin':
        server_label = 'iMac'
    else:
        server_label = 'CPU'
    server_title = f'WeRU.B AI Server Admin - {server_label}'
    is_imac = env.get('platform') == 'darwin'
    try:
        from flask import render_template
        # app_version: static 캐시버스터 — 템플릿에 하드코딩하면 낡는다(실측 2026-08-10:
        # `main.js?v=2.87.2-medlaw`가 v2.124.94까지 그대로여서 그 사이 프론트 변경이
        # 캐시된 브라우저에 붙지 않았다). VERSION에 묶어 배포마다 자동 갱신한다.
        html = render_template('admin.html', user_script=user_inject, server_title=server_title,
                               is_imac=is_imac, app_version=VERSION, allowed_tabs=allowed_tabs)
        # 🔴 escape — v2.125.66까지는 이름을 HTML에 **그대로** 넣었다(헤더 표시). `</script><script>…`·`<img onerror>`가
        #    든 display_name이면 그 사용자의 관리 화면에서 실행됐다(v2.125.67 회귀가 발견·설정 권한은 admin뿐).
        from markupsafe import escape as _esc
        html = html.replace("<!--USER_DISPLAY-->", str(_esc(display_name + " (" + role + ")")))
    except Exception:
        # 🔴 v2.125.65까지는 여기서 `pass` → **로그 없이 인라인 HTML(3,744줄 낡은 사본)**을 내보냈다.
        #    그 사본엔 v2.125.54~64에서 고친 결함(저장 실패를 '저장됨'으로·역할 숨김 무효 등)이 그대로 있어
        #    템플릿이 한 번 깨지면 운영자는 **오류 대신 옛 화면**을 봤을 것이다(클래스 #1). 사본은 삭제했다.
        #    이제 실패는 로그 + 500으로 드러낸다.
        logger.exception("[admin-ui] templates/admin.html 렌더 실패 — 500 응답")
        return Response("<!DOCTYPE html><meta charset='utf-8'><h1>관리 화면을 표시할 수 없습니다</h1>"
                        "<p>관리 화면 템플릿 렌더에 실패했습니다. 서버 로그의 <code>[admin-ui]</code>를 확인하세요.</p>",
                        status=500, content_type="text/html; charset=utf-8")
    from flask import make_response
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp

LOGIN_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ server_title }} — Login</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0f1117;
  color: #e4e4e7;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.card {
  background: #1a1d27;
  border: 1px solid #2a2d3a;
  border-radius: 12px;
  padding: 40px 36px;
  width: 360px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
}
.logo {
  text-align: center;
  margin-bottom: 28px;
}
.logo h1 {
  font-size: 20px;
  font-weight: 700;
  color: #e4e4e7;
  letter-spacing: -0.3px;
}
.logo p {
  font-size: 12px;
  color: #71717a;
  margin-top: 4px;
}
.form-group {
  margin-bottom: 16px;
}
label {
  display: block;
  font-size: 13px;
  color: #a1a1aa;
  margin-bottom: 6px;
  font-weight: 500;
}
input {
  width: 100%;
  padding: 10px 13px;
  background: #0f1117;
  border: 1px solid #2a2d3a;
  border-radius: 7px;
  color: #e4e4e7;
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s;
}
input:focus { border-color: #6366f1; }
.btn {
  width: 100%;
  padding: 11px;
  background: #6366f1;
  color: #fff;
  border: none;
  border-radius: 7px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 8px;
  transition: background 0.15s;
}
.btn:hover { background: #4f52d1; }
.error {
  background: rgba(239,68,68,0.12);
  border: 1px solid rgba(239,68,68,0.3);
  border-radius: 7px;
  padding: 10px 13px;
  font-size: 13px;
  color: #f87171;
  margin-bottom: 16px;
}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <h1>{{ server_title }}</h1>
    <p>관리자 로그인</p>
  </div>
  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}
  <form method="POST" action="{{ url_for('login', next=next) }}">
    <div class="form-group">
      <label>아이디</label>
      <input type="text" name="username" autocomplete="username" autofocus required>
    </div>
    <div class="form-group">
      <label>비밀번호</label>
      <input type="password" name="password" autocomplete="current-password" required>
    </div>
    <button class="btn" type="submit">로그인</button>
  </form>
</div>

<!-- 학습 위저드 사이드바 -->
<style>
#wizardSidebar {
  position: fixed; right: -380px; top: 0; width: 380px; height: 100vh;
  background: var(--card); border-left: 1px solid var(--border);
  z-index: 9999; transition: right 0.3s ease;
  display: flex; flex-direction: column; box-shadow: -4px 0 20px rgba(0,0,0,0.3);
}
#wizardSidebar.open { right: 0; }
#wizardSidebar .ws-header {
  padding: 16px 20px; border-bottom: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
}
#wizardSidebar .ws-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
#wizardSidebar .ws-step {
  margin-bottom: 20px; padding: 16px; background: var(--surface);
  border-radius: 10px; border-left: 3px solid var(--border); cursor: pointer;
  transition: all 0.2s;
}
#wizardSidebar .ws-step.active { border-left-color: var(--accent); background: rgba(99,102,241,0.05); }
#wizardSidebar .ws-step.done { border-left-color: #22c55e; }
#wizardSidebar .ws-step .ws-num {
  display: inline-flex; width: 24px; height: 24px; border-radius: 50%;
  background: var(--border); color: var(--muted); font-size: 12px; font-weight: 700;
  align-items: center; justify-content: center; margin-right: 8px;
}
#wizardSidebar .ws-step.active .ws-num { background: var(--accent); color: #fff; }
#wizardSidebar .ws-step.done .ws-num { background: #22c55e; color: #fff; }
#wizardSidebar .ws-step .ws-title { font-size: 14px; font-weight: 700; color: var(--text); }
#wizardSidebar .ws-step .ws-desc { font-size: 12px; color: var(--muted); margin-top: 6px; line-height: 1.6; }
#wizardSidebar .ws-step .ws-tip {
  font-size: 11px; color: var(--accent); margin-top: 8px; padding: 8px 10px;
  background: rgba(99,102,241,0.08); border-radius: 6px; line-height: 1.5;
}
#wizardSidebar .ws-step .ws-action {
  margin-top: 10px; display: flex; gap: 6px;
}
#wizardSidebar .ws-step .ws-action button {
  padding: 6px 14px; border-radius: 6px; font-size: 11px; border: none; cursor: pointer;
}
.ws-btn-primary { background: var(--accent); color: #fff; }
.ws-btn-secondary { background: var(--surface); color: var(--muted); border: 1px solid var(--border) !important; }
#wizardToggle {
  position: fixed; right: 20px; bottom: 80px; z-index: 9998;
  width: 56px; height: 56px; border-radius: 50%; background: var(--accent);
  color: #fff; border: none; cursor: pointer; font-size: 24px;
  box-shadow: 0 4px 15px rgba(99,102,241,0.4); display: flex; align-items: center; justify-content: center;
  transition: transform 0.2s;
}
#wizardToggle:hover { transform: scale(1.1); }
</style>

<button id="wizardToggle" onclick="toggleWizardSidebar()" title="학습 가이드" style="display:none;">🧙</button>

<div id="wizardSidebar">
  <div class="ws-header">
    <div>
      <div style="font-size:16px;font-weight:700;color:var(--text);">🧙 학습 가이드</div>
      <div style="font-size:11px;color:var(--muted);margin-top:2px;">단계별로 따라하면 AI가 똑똑해져요</div>
    </div>
    <button onclick="toggleWizardSidebar()" style="background:none;border:none;color:var(--muted);font-size:24px;cursor:pointer;padding:4px 8px;border-radius:4px;hover:background:var(--surface);" title="닫기 (ESC)">✕</button>
  </div>
  <div class="ws-body">

    <div class="ws-step active" id="wsStep1" onclick="wizardSidebarStep(1)">
      <div><span class="ws-num">1</span><span class="ws-title">학습 재료 준비하기</span></div>
      <div class="ws-desc">
        AI에게 "이건 나쁜 글이야", "이건 괜찮은 글이야" 라고 알려주는 예시를 모아야 해요.
        <br><br>
        마치 아이에게 "이건 뜨거워, 만지면 안 돼" 라고 가르치는 것과 같아요.
        예시가 많을수록 AI가 더 정확하게 판단합니다.
      </div>
      <div class="ws-tip">
        💡 <b>최소 100건</b> 이상 필요해요.<br>
        "차단할 글"과 "정상 글"을 골고루 넣어주세요.<br>
        관리자가 판정을 수정하면 자동으로 모입니다.
      </div>
      <div class="ws-action">
        <button class="ws-btn-primary" onclick="showWizardStep(1);toggleWizardSidebar();">재료 준비하러 가기 →</button>
      </div>
    </div>

    <div class="ws-step" id="wsStep2" onclick="wizardSidebarStep(2)">
      <div><span class="ws-num">2</span><span class="ws-title">AI 학습시키기</span></div>
      <div class="ws-desc">
        모아둔 재료로 AI를 학습시켜요.
        <br><br>
        AI가 재료를 반복해서 읽으면서 "이런 글은 차단해야 하는구나" 하고 배우게 됩니다.
        사람이 공부할 때 교과서를 여러 번 읽는 것과 비슷해요.
      </div>
      <div class="ws-tip">
        ⚡ <b>빠른 학습</b> (~10분): 처음 시도할 때<br>
        ⚖️ <b>균형 학습</b> (~30분): 일반적으로 추천<br>
        🎯 <b>정밀 학습</b> (~60분): 최고 정확도가 필요할 때<br>
        <br>
        💡 학습 중에도 기존 차단 기능은 계속 작동해요!
      </div>
      <div class="ws-action">
        <button class="ws-btn-primary" onclick="showWizardStep(2);toggleWizardSidebar();">학습 시작하러 가기 →</button>
      </div>
    </div>

    <div class="ws-step" id="wsStep3" onclick="wizardSidebarStep(3)">
      <div><span class="ws-num">3</span><span class="ws-title">성적표 확인하기</span></div>
      <div class="ws-desc">
        학습이 끝나면 AI가 얼마나 잘 배웠는지 시험을 봐요.
        <br><br>
        100점 만점으로 점수가 나오고, <b>85점 이상</b>이면 합격이에요.
        어떤 종류의 나쁜 글을 잘 찾는지, 어디서 실수하는지도 알 수 있어요.
      </div>
      <div class="ws-tip">
        📊 <b>점수가 높을수록</b> 나쁜 글을 잘 찾아요<br>
        📊 <b>정확도가 높을수록</b> 실수로 차단하는 일이 적어요<br>
        <br>
        ❌ 틀린 것들을 재료에 추가하면 다음에 더 잘 배워요!
      </div>
      <div class="ws-action">
        <button class="ws-btn-primary" onclick="showWizardStep(3);toggleWizardSidebar();">성적표 보러 가기 →</button>
      </div>
    </div>

    <div class="ws-step" id="wsStep4" onclick="wizardSidebarStep(4)">
      <div><span class="ws-num">4</span><span class="ws-title">새 AI 적용하기</span></div>
      <div class="ws-desc">
        시험에 합격한 AI를 실제 서비스에 적용해요.
        <br><br>
        버튼 하나로 적용되고, 그 순간부터 게시글/댓글/블로그의
        유해 콘텐츠 검사가 새로운 AI로 바뀌어요.
      </div>
      <div class="ws-tip">
        🚀 적용 후에도 <b>언제든 이전 AI로 되돌릴 수 있어요</b><br>
        <br>
        💡 이 과정을 반복할수록 AI가 점점 똑똑해져요!<br>
        재료 추가 → 학습 → 확인 → 적용 → 재료 추가 → ...
      </div>
      <div class="ws-action">
        <button class="ws-btn-primary" onclick="showWizardStep(4);toggleWizardSidebar();">적용하러 가기 →</button>
      </div>
    </div>

    <div style="padding:16px;background:var(--surface);border-radius:10px;margin-top:8px;">
      <div style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:8px;">🔄 학습 사이클</div>
      <div style="font-size:12px;color:var(--muted);line-height:1.8;">
        ① 재료 모으기 (자동 + 수동)<br>
        ② AI 학습시키기 (원클릭)<br>
        ③ 성적표 확인 (85점 이상?)<br>
        ④ 서비스에 적용<br>
        ⑤ 사용하면서 실수 발견 → ①로<br>
        <br>
        <span style="color:var(--accent);">이 과정이 반복될수록<br>AI가 점점 정확해집니다! 🎯</span>
      </div>
    </div>

  </div>
</div>

<script>
function toggleWizardSidebar() {
  const sb = document.getElementById('wizardSidebar');
  const isOpen = sb.classList.toggle('open');
  // 오버레이
  let ov = document.getElementById('wizardOverlay');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'wizardOverlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:9998;display:none;';
    ov.onclick = toggleWizardSidebar;
    document.body.appendChild(ov);
  }
  ov.style.display = isOpen ? 'block' : 'none';
}
// ESC 닫기
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const sb = document.getElementById('wizardSidebar');
    if (sb && sb.classList.contains('open')) toggleWizardSidebar();
  }
});

function wizardSidebarStep(n) {
  document.querySelectorAll('#wizardSidebar .ws-step').forEach((el,i) => {
    el.className = 'ws-step' + (i+1===n ? ' active' : (i+1<n ? ' done' : ''));
  });
}

// Training 탭 진입 시 사이드바 토글 버튼 표시 제어
const _origSwitch = switchTab;
switchTab = function(name) {
  _origSwitch(name);
  const btn = document.getElementById('wizardToggle');
  if (btn) btn.style.display = name === 'training' ? 'flex' : 'none';
};
// 초기 숨김 (Training 탭이 아닐 때)
function applyPermissions() {
  const me = window.__USER__;
  if (!me) return;
  // 헤더에 사용자 표시
  const hUser = document.getElementById('headerUser');
  if(hUser) hUser.textContent = me.displayName + ' (' + me.role + ')';
  if(me.role==='admin') return;
  let perms = me.permissions || {};
  perms.users = false; // Users 탭은 admin만
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach(tab=>{
    const oc=tab.getAttribute('onclick')||'';
    const m=oc.match(/switchTab\('(\w+)'\)/);
    if(m){
      const key=m[1];
      const allowed = perms[key]===true;
      tab.style.display = allowed ? '' : 'none';
      const panel = document.getElementById('panel-'+key);
      if(panel && !allowed) panel.style.display='none';
    }
  });
}
// 즉시 + 지연 실행 (DOM 준비 보장)
document.addEventListener('DOMContentLoaded', () => {
  applyPermissions();
  setTimeout(applyPermissions, 500);
  const btn = document.getElementById('wizardToggle');
  if (btn) btn.style.display = 'none';
});
</script>

</body>
</html>"""

# (HTML_TEMPLATE — 관리 화면 인라인 사본 3,744줄은 v2.125.66에서 삭제. 실제 화면은 templates/admin.html + static/js/main.js)


# ═══ 자동 정제 + 에이전트 스케줄러 ═══════════════════════════════════════════
import threading as _sched_threading

def _auto_refine_job():
    """매일 자동 정제 (pending > 100건일 때).

    ⚠️ `_get_mod_db`는 app.py에 없고 routes/training.py에 정의돼 있다. 과거엔 이름을
    import 없이 호출해 **매 실행 NameError로 조용히 실패**(HR Law admin 가드와 동일
    클래스, 2026-07-21 로그에서 발견). moderation 데이터가 pending 100건 이상 쌓여도
    정제가 되지 않던 잠재 버그 → 실제 함수를 import해 사용."""
    from routes.training import _get_mod_db
    while True:
        try:
            import time; time.sleep(3600)  # 1시간마다 체크
            conn = _get_mod_db()
            pending = conn.execute("SELECT COUNT(*) FROM raw_collections WHERE refine_status='pending'").fetchone()[0]
            conn.close()
            if pending >= 100:
                # 정제 실행
                from flask import Flask
                with app.app_context():
                    conn = _get_mod_db()
                    rows = conn.execute("SELECT * FROM raw_collections WHERE refine_status='pending'").fetchall()
                    seen = set(r["text_hash"] for r in conn.execute("SELECT text_hash FROM refined_samples"))
                    count = 0
                    for row in rows:
                        if len(row["text"]) < 5 or row["text_hash"] in seen:
                            conn.execute("UPDATE raw_collections SET refine_status='excluded' WHERE id=?", (row["id"],))
                            continue
                        label = row["admin_action"] or row["ai_action"] or "allow"
                        conn.execute("INSERT OR IGNORE INTO refined_samples (text, text_hash, label, categories, source) VALUES (?, ?, ?, ?, ?)",
                            (row["text"], row["text_hash"], label, row["ai_categories"] or "[]", row["source"]))
                        conn.execute("UPDATE raw_collections SET refine_status='included' WHERE id=?", (row["id"],))
                        seen.add(row["text_hash"])
                        count += 1
                    conn.commit()
                    conn.close()
                    if count > 0:
                        print(f"[auto-refine] {count}건 정제 완료")
        except Exception as e:
            print(f"[auto-refine] error: {e}")

_sched_threading.Thread(target=_auto_refine_job, daemon=True).start()


# ---------------------------------------------------------------------------
# Startup — worker threads + graceful shutdown (Flask dev / Gunicorn 공용)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# GPUResourceManager — ComfyUI on-demand + 양방향 GPU 스케줄링 (v2.25.0)
# ---------------------------------------------------------------------------
import threading as _gpu_threading

class GPUResourceManager:
    """ComfyUI on-demand 시작/종료 + LLM↔Image VRAM 양방향 관리

    모드 (config.json → comfyui_mode):
      - "on-demand" (기본): ComfyUI OFF → 이미지 요청 시 자동 시작 → idle 후 종료
      - "preload": ComfyUI 항시 실행 (콜드스타트 0초, VRAM 상시 점유)
      - "manual": 자동 시작/종료 없음 (수동 systemctl 관리)

    기본: ComfyUI OFF → LLM이 VRAM 전부 사용
    이미지 요청: LLM 언로드 → ComfyUI 시작 → 생성 → /free → LLM 복원 → idle 후 종료
    LLM 요청: ComfyUI 실행 중이면 /free → VRAM 확보
    """

    COMFYUI_START_TIMEOUT = 30  # ComfyUI 시작 대기 최대 30초

    def __init__(self):
        self._lock = _gpu_threading.Lock()
        self._comfyui_running = self._check_comfyui_alive()
        self._idle_timer = None
        self._last_image_time = 0
        self._load_mode_config()
        logger.info(f"GPUResourceManager initialized (mode={self._mode}, ComfyUI: {'running' if self._comfyui_running else 'stopped'})")

        # Preload: ensure ComfyUI is running at startup
        if self._mode == "preload" and not self._comfyui_running:
            logger.info("GPUResourceManager: preload mode — starting ComfyUI at boot")
            self._unload_llm()
            time.sleep(2)
            self._start_comfyui()

    def _load_mode_config(self):
        """Load ComfyUI mode from config.json"""
        try:
            cfg = load_config()
            self._mode = cfg.get("comfyui_mode", "on-demand")  # on-demand | preload | manual
            self._idle_timeout = cfg.get("comfyui_idle_timeout", 300)  # configurable timeout
        except Exception:
            self._mode = "on-demand"
            self._idle_timeout = 300

    def _check_comfyui_alive(self):
        """ComfyUI 프로세스 실행 여부 확인"""
        try:
            r = requests.get(f"{COMFYUI_BASE}/", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _start_comfyui(self):
        """ComfyUI 시작 + ready 대기"""
        logger.info("GPUResourceManager: starting ComfyUI...")
        subprocess.run(["sudo", "systemctl", "start", "comfyui"],
                       capture_output=True, timeout=10)
        # ready 대기
        for i in range(self.COMFYUI_START_TIMEOUT):
            try:
                r = requests.get(f"{COMFYUI_BASE}/", timeout=2)
                if r.status_code == 200:
                    logger.info(f"GPUResourceManager: ComfyUI ready in {i+1}s")
                    self._comfyui_running = True
                    return True
            except Exception as e:
                logger.debug("ComfyUI readiness check failed: %s", e)
            time.sleep(1)
        logger.error("GPUResourceManager: ComfyUI start timeout")
        return False

    def _stop_comfyui(self):
        """ComfyUI 완전 종료 (프로세스 + VRAM + RAM 회수)"""
        logger.info("GPUResourceManager: stopping ComfyUI...")
        subprocess.run(["sudo", "systemctl", "stop", "comfyui"],
                       capture_output=True, timeout=10)
        self._comfyui_running = False
        logger.info("GPUResourceManager: ComfyUI stopped")

    def _free_comfyui_vram(self):
        """ComfyUI 모델 캐시 해제 (프로세스 유지, VRAM만 해제)"""
        try:
            requests.post(f"{COMFYUI_BASE}/free",
                          json={"unload_models": True}, timeout=5)
            logger.info("GPUResourceManager: freed ComfyUI VRAM")
        except Exception as e:
            logger.debug("ComfyUI VRAM free request failed: %s", e)

    def _unload_llm(self):
        """LLM 모델 VRAM 해제"""
        try:
            r = requests.get(f"{OLLAMA_BASE}/api/ps", timeout=5)
            running = r.json().get("models", [])
            for m in running:
                model_name = m.get("name", "")
                if model_name:
                    requests.post(f"{OLLAMA_BASE}/api/generate", json={
                        "model": model_name, "prompt": "", "keep_alive": 0
                    }, timeout=60)
                    logger.info(f"GPUResourceManager: unloaded LLM {model_name}")
        except Exception as e:
            logger.warning(f"GPUResourceManager: LLM unload failed: {e}")

    def _reload_llm(self):
        """기본 LLM 모델 복원 — 비상주 모드면 `_ensure_default_model`이 no-op."""
        try:
            _ensure_default_model()
            logger.info("GPUResourceManager: LLM reloaded")
        except Exception as e:
            logger.warning(f"GPUResourceManager: LLM reload failed: {e}")

    def _schedule_idle_shutdown(self):
        """idle 타이머 리셋 — 설정된 시간 후 ComfyUI 자동 종료"""
        if self._mode == "preload":
            return  # preload 모드에서는 자동 종료 안 함
        if self._idle_timer:
            self._idle_timer.cancel()
        self._idle_timer = _gpu_threading.Timer(
            self._idle_timeout, self._idle_shutdown)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _idle_shutdown(self):
        """idle 타임아웃 콜백 — ComfyUI 종료 + LLM 복원"""
        if self._mode == "preload":
            return  # preload 모드에서는 종료 안 함
        with self._lock:
            if not self._comfyui_running:
                return
            elapsed = time.time() - self._last_image_time
            if elapsed < self._idle_timeout - 5:
                return  # 타이머 중 새 요청이 들어왔으면 무시
            logger.info(f"GPUResourceManager: ComfyUI idle {int(elapsed)}s → auto shutdown")
            self._stop_comfyui()
            self._reload_llm()

    # --- Public API ---

    def ensure_comfyui_for_image(self):
        """이미지 생성 전 호출 — ComfyUI 보장 + LLM 언로드"""
        if self._mode == "manual":
            if not self._check_comfyui_alive():
                raise RuntimeError("ComfyUI not running (manual mode — start with systemctl)")
            return
        with self._lock:
            self._last_image_time = time.time()
            if not self._comfyui_running:
                self._unload_llm()
                time.sleep(2)  # VRAM 해제 대기
                if not self._start_comfyui():
                    raise RuntimeError("ComfyUI start failed")
            else:
                # ComfyUI 실행 중이지만 LLM도 로드되어 있을 수 있음 (on-demand)
                if self._mode == "on-demand":
                    self._unload_llm()

    def after_image_done(self):
        """이미지 생성 완료 후 호출 — VRAM 해제 + LLM 복원 + idle 타이머"""
        with self._lock:
            self._last_image_time = time.time()
            self._free_comfyui_vram()
            time.sleep(1)
            self._reload_llm()
            self._schedule_idle_shutdown()

    def ensure_llm_vram(self):
        """LLM 호출 전 — ComfyUI VRAM 해제 (필요 시)"""
        # lock 밖에서 빠르게 체크 (성능)
        if not self._comfyui_running:
            return
        with self._lock:
            if self._comfyui_running:
                self._free_comfyui_vram()

    def status(self):
        """현재 GPU 리소스 상태"""
        return {
            "comfyui_running": self._comfyui_running,
            "comfyui_mode": self._mode,
            "comfyui_idle_timeout": self._idle_timeout,
            "last_image_time": self._last_image_time,
            "idle_timer_active": self._idle_timer is not None and self._idle_timer.is_alive(),
        }


_gpu_manager = GPUResourceManager()

# Initialize Ollama Blueprint
init_ollama(
    get_db=get_db, load_config=load_config, save_config=save_config,
    ollama_session=_ollama_session, is_admin_request=is_admin_request,
    is_local_request=is_local_request,
    generate_api_key=generate_api_key, chat_queue=_chat_queue,
    gpu_manager=_gpu_manager, ollama_base=OLLAMA_BASE, ollama_get=ollama_get, ollama_post=ollama_post, ollama_delete=ollama_delete, format_size=format_size, get_ollama_status=get_ollama_status, version=VERSION, changelog=CHANGELOG, build_system_prompt=build_init_system_prompt, llm_response_cache=_llm_response_cache, rate_limit_key=_rate_limit_key, clear_key_cache=_key_cache.clear,
)

# Heavy tier 초기화 (하이브리드 대형 모델 — 상주 복원에 _gpu_manager 사용)
init_heavy(load_config=load_config, save_config=save_config, ollama_base=OLLAMA_BASE,
           gpu_manager=_gpu_manager, is_admin_request=is_admin_request)

# OpenAI 호환 엔드포인트 초기화 (/v1/chat/completions)
init_openai_compat(ollama_base=OLLAMA_BASE, load_config=load_config)


# Initialize Dashboard API (통합 대시보드)
init_dashboard_api(
    get_db=get_db, ollama_session=_ollama_session, ollama_base=OLLAMA_BASE,
    get_ollama_status=get_ollama_status, get_worker_alive=get_worker_alive,
    get_queue_paused=get_queue_paused, gpu_manager=_gpu_manager,
    chat_queue=_chat_queue, base_dir=BASE_DIR, version=VERSION,
    monitor_subscribers=_monitor_subscribers, monitor_lock=_monitor_lock,
    monitor_subscribe=_monitor_subscribe, monitor_unsubscribe=_monitor_unsubscribe,
    ollama_get=ollama_get,
)

# 하위 호환용 래퍼 (기존 코드에서 호출하는 함수명 유지)
_gpu_lock = _gpu_threading.Lock()

def _unload_llm_for_image():
    _gpu_manager.ensure_comfyui_for_image()

def _reload_llm_after_image():
    _gpu_manager.after_image_done()

# ---------------------------------------------------------------------------
# Image Worker — ComfyUI 전용 (LLM 워커와 병렬 실행)
# ---------------------------------------------------------------------------
def image_worker_loop():
    """이미지 잡 전용 워커 — ComfyUI만 사용, LLM과 독립 실행"""
    # worker_alive managed by queue blueprint
    logger.info("Image worker started")

    while get_worker_alive():
        if get_queue_paused():
            image_worker_event.wait(timeout=2)
            image_worker_event.clear()
            continue

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT id, model, system_prompt, prompt, retry_count, options, webhook_url, job_type "
                "FROM jobs WHERE status='pending' AND job_type='image' "
                "ORDER BY priority ASC, created_at ASC LIMIT 1"
            ).fetchone()

            if row is None:
                conn.close()
                image_worker_event.wait(timeout=2)
                image_worker_event.clear()
                continue

            job = dict(row)
            conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat() + "Z", job["id"]))
            conn.commit()
        finally:
            conn.close()

        # SANA 무스왑 fast-lane 감지 — co-resident라 LLM 언로드/복원 skip
        _sana_engine = False
        try:
            if job.get("options"):
                _sana_engine = (json.loads(job["options"]) or {}).get("engine") == "sana"
        except (json.JSONDecodeError, TypeError):
            _sana_engine = False

        logger.info(f"Image job {job['id'][:8]} started — model={job['model']}"
                    + (" [sana]" if _sana_engine else ""))
        start_ts = time.time()
        try:
            # GPU 스케줄러: 이미지 생성 전 LLM VRAM 해제 (SANA는 co-resident=무스왑이라 skip)
            if not _sana_engine:
                with _gpu_lock:
                    _unload_llm_for_image()
                # ComfyUI 대형잡 전 SANA 사이드카 VRAM 반납(상호배제 — SANA+SDXL+qwen 동시 불가).
                # SANA 기능 활성 시에만 — 비활성(기본)이면 기존 ComfyUI 경로 완전 무변경.
                if load_config().get("sana_enabled", False):
                    _unload_sana_sidecar()
            else:
                # 반대 방향 상호배제: SANA co-resident 전 ComfyUI가 상주 중이면 VRAM 반납
                # (ComfyUI 미실행 시 no-op). SANA(4.4GB)+ComfyUI+qwen 동시상주 OOM 방지.
                try:
                    _gpu_manager.ensure_llm_vram()
                except Exception:
                    pass
            img_result = _process_image_job(job)
            response_text = json.dumps(img_result)
            duration_ms = int((time.time() - start_ts) * 1000)

            conn = get_db()
            try:
                conn.execute(
                    "UPDATE jobs SET status='completed', response=?, tokens=0, duration_ms=?, completed_at=? WHERE id=?",
                    (response_text, duration_ms, datetime.now(timezone.utc).isoformat() + "Z", job["id"]),
                )
                conn.commit()
            finally:
                conn.close()

            logger.info(f"Image job {job['id'][:8]} completed — {duration_ms}ms")
            _fire_webhook(job, "completed", response_text, 0, duration_ms)
            _broadcast_job_event(job, "completed", response_text=response_text, duration_ms=duration_ms)
            # GPU 스케줄러: 이미지 완료 후 LLM 기본 모델 복원 (SANA는 언로드 안 했으므로 skip)
            if not _sana_engine:
                _reload_llm_after_image()

        except Exception as e:
            logger.error(f"Image job {job['id'][:8]} failed: {e}")
            retry = (job.get("retry_count", 0) or 0) + 1
            conn = get_db()
            try:
                if retry >= 3:
                    conn.execute("UPDATE jobs SET status='failed', response=?, completed_at=? WHERE id=?",
                                 (str(e), datetime.now(timezone.utc).isoformat() + "Z", job["id"]))
                    _fire_webhook(job, "failed", str(e), 0, 0)
                    _broadcast_job_event(job, "failed", error=str(e))
                else:
                    conn.execute("UPDATE jobs SET status='pending', retry_count=?, started_at=NULL WHERE id=?",
                                 (retry, job["id"]))
                conn.commit()
            finally:
                conn.close()

_worker_started = False

def _start_background_threads():
    """워커/활동기록 스레드 시작 (중복 호출 방지)"""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    init_db()
    # 성능 모니터링 (P2): 워커 부팅마다 recycle 카운터 증가 + 부팅시각 기록.
    # max_requests recycle(~37-40min)·HUP 배포로 빈번 → churn 추세는 DB delta로 산출
    # (인메모리 카운터는 recycle마다 리셋되므로 영속 필요).
    try:
        _mc = get_db()
        _mc.execute("INSERT INTO monitoring_meta(key,value) VALUES('worker_recycle_total','1') "
                    "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
        _mc.execute("INSERT INTO monitoring_meta(key,value) VALUES('worker_boot_epoch',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
        _mc.commit()
        _mc.close()
    except Exception as e:
        logger.debug("worker recycle counter update failed: %s", e)
    # 비정상 종료로 running 상태로 남은 잡을 pending으로 복구 (staleness 게이트 —
    # 워커 재생성 중첩창에 살아있는 구 워커가 처리 중인 잡은 건드리지 않음)
    _conn = get_db()
    _stuck = _conn.execute(
        "UPDATE jobs SET status='pending', started_at=NULL "
        "WHERE status='running' AND (started_at IS NULL OR started_at < ?)",
        (_stale_running_cutoff(),)).rowcount
    _conn.commit()
    if _stuck:
        logger.warning(f"Recovered {_stuck} stuck running job(s) to pending on startup")

    t = threading.Thread(target=worker_loop, daemon=True, name="llm-worker")
    t.start()
    t_img = threading.Thread(target=image_worker_loop, daemon=True, name="image-worker")
    t_img.start()
    t_act = threading.Thread(target=_activity_writer, daemon=True, name="activity-writer")
    t_act.start()
    logger.info("Background threads started (llm-worker + image-worker + activity-writer)")

    def graceful_shutdown(signum, frame):
        # worker_alive managed by queue blueprint
        logger.info(f"Shutdown signal received (sig={signum}), stopping worker...")
        set_worker_alive(False)
        worker_event.set()
        image_worker_event.set()
        _act_queue.put(None)
        t.join(timeout=10)
        t_act.join(timeout=3)
        logger.info("Worker stopped, exiting")
        sys.exit(0)

    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)


# Gunicorn: post_fork에서 호출 (gunicorn.conf.py에서 import)
# Flask dev: __main__에서 호출
_start_background_threads()


if __name__ == "__main__":
    cfg = load_config()
    host = cfg.get("bind_host", "127.0.0.1")
    port = cfg.get("bind_port", 8585)
    logger.info(f"Flask dev server on {host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
