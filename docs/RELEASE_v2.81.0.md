# Release v2.81.0 — Apple Silicon Worker (iMac M1 특화 워커, Phase 0+1)

> 2026-06-07 | iMac M1을 "CPU 오프로드 노드"에서 **Apple Silicon 특화 워커**로 격상.
> GPU 서버 변경은 위임+폴백 분기 한정 — config 미설정 시 기존 동작 100% (v2.80.0 Phase 0 범위 포함)
> PDCA: Plan→Design→Do→Check(96%)→Report 완주. 보고서 `docs/03-analysis/apple-silicon-worker.report.md`

## Phase 0 — M1 기반 정비 (9/9)

- **Metal 실측 확정**: 전 모델 `ollama ps` 100% GPU — 기존 문서의 "iMac=CPU 노드" 표기는 오류였음 (정정 완료). 3b 22.4 / 7b 10.9 tok/s, bge-m3 5.0 docs/s(500자 청크). `docs/IMAC_M1_BENCHMARK.md`
- **Ollama 튜닝** (`setup/com.werub.ollama.plist`, brew services 대체): `FLASH_ATTENTION=1, NUM_PARALLEL=2, MAX_LOADED_MODELS=2, KEEP_ALIVE=30m` — **동시 요청 직렬 큐잉 해소** (head-of-line 블로킹 제거, 위임+독립 이중 활용의 전제)
- **절전 차단** (`com.werub.caffeinate.plist`, `-ims`): pmset `sleep=1`(유휴 1분 잠자기)이 터널 끊김 근본 원인이었음을 발견·차단
- **watchdog macOS판** (`scripts/watchdog_mac.sh` + plist, 5분): ollama/워커/터널(조건부)/caffeinate 감시 → `launchctl kickstart` 복구 + 메모리/디스크/thermal 경고
- **모니터링**: `scripts/server-stats.py` darwin 분기 (Linux `/proc` 경로 불변) + `apple_silicon` 블록(chip/pressure/thermal/swap)
- **의존성/배포**: `requirements-mac.txt`(GPU 핀 동일) + `venv-mac` + `scripts/deploy_imac.sh`(--setup: 토큰 생성·plist 4종 일괄) + `pytest.ini` macos 마커

## Phase 1 — AI 판별/마킹 워커 + 위임

### iMac 워커 (`worker_app.py` :8587, gunicorn_worker.conf.py)
- `GET /worker/health` — memory/thermal/locks/capabilities (watchdog·모니터링용, 무인증)
- `POST /worker/detect/image` — sdxl-detector **MPS 추론 웜 58~92ms** (GPU 서버 CPU ~100ms 동등 이상), cpu↔mps 편차 <1%p
- `POST /worker/mark/image` — 워터마크+iTXt(15ms) + C2PA opt-in (`content_marking`/`c2pa_sign` 재사용)
- **이중 인증** `@worker_auth`: 위임=X-Worker-Token(hmac), 독립=Bearer(`WORKER_EXPOSE` opt-in, 기본 OFF — queue.db api_keys 직접 검증)
- **가드** (`core/worker_guard.py`): memory_pressure warn/critical 503 + heavy(stt/embed) 상호배제 락 — 16GB 통합 메모리 보호

### GPU 서버 위임 (휴면 배포)
- `core/apple_worker.py`: **4중 활성 조건**(Linux + enabled + url/token + delegate) 전부 충족 시에만 동작. 헬스 30s 캐시. **모든 실패 → None → 기존 로컬 경로** (예외 미전파)
- **Darwin 가드**: iMac에서는 config가 있어도 위임 불가 (자기 위임 차단)
- `routes/image.py` detect ML 게이트 분기 ~10줄 — config 미설정 시 기존과 바이트 동일 동작 (테스트 보증)
- 활성화는 배포 runbook 절차: `docs/DEPLOY_IMAC_DELEGATION.md` (2026-06-08 예정, 롤백=config 키 제거)

## Phase 2 PoC — STT 백엔드 선정 (구현은 차기)

- **mlx-whisper 선정**: large-v3 동일 조건에서 whisper.cpp와 **전사 텍스트 완전 동일**(CER 2.8%=구어 숫자 정규화), RTF 0.34, Python in-process 상주가 워커 가드/락 구조와 정합 (`IMAC_M1_BENCHMARK.md` §6-3)

## erp-notice-rag 기반 (v2.84.0 선행 작업)

- `core/notice_indexer.py`: **정규화 7항**(ERP 합의 스펙) + content_hash/verify_hash + **조문 청크**(제N조·별표·붙임·부칙, 비정형 512자 슬라이딩)
- `core/notice_db.py`: notices(UNIQUE(erp_ref_id,revision_no)) + notice_index_jobs DDL
- `tests/data/notice_hash_vectors.json`: **ERP 교환용 해시 벡터 3종** (일반/표서식/CRLF+BOM+NFD)
- ERP 협의 완료: 폴링 단독 모드, 코드표 8종, SLA(immediate 5분/batch 24h) — 회신 `docs/01-plan/references/notice-integration-reply-to-erp.md`

## 검증

- 테스트 **41건 통과**: worker 13(인증/입력/멱등/락/MPS 동일성/Bearer 3케이스) + apple_worker 7(폴백 계약) + notice 14(7항/벡터/청크/스키마) + 기존 회귀
- E2E 실워커 위임 89ms(MPS) + 토큰 오류→None 폴백 실검증, `deploy_imac.sh --setup` 실배포 검증
- **갭 분석 96%** — 결함 1건(모드 B Bearer 부재 함수 호출, 휴면 상태) 당일 수정
- iMac 현재 가동: launchd 4종 (ollama/caffeinate/watchdog/worker)

## 배포

- GPU 서버: `git pull`만으로 동작 변화 0 (apple_worker는 config 휴면, macos 테스트는 자동 스킵). gunicorn 재시작 시 SSE drain 순단만
- **위임 활성화는 별도 운영 작업** (2026-06-08 예정): `docs/DEPLOY_IMAC_DELEGATION.md` runbook — config 4키 + iMac 터널. 롤백은 config 키 제거(재배포 불필요)
- iMac: `bash scripts/deploy_imac.sh --setup` (이미 수행됨)

## 잔여

- Phase 2 구현 (stt_backend + /worker/stt + meeting_pipeline 위임 — mlx-whisper, ffmpeg 요건) / Phase 3 (embed + 컬렉션-모델 맵)
- erp-notice-rag Day 2~4 (API 12종 + 색인 잡 + query)
- 운영 검증: detect 동일성 100장, 24h 안정성, worker-tunnel plist 영구화 (배포일)
- 독립 모드(B) 노출 경로 결정 (LAN/공인)
