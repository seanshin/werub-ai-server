# Release v2.82.0 — iMac 연계 콘솔 (Phase A) + ERP 공시 수명주기 API

> 2026-06-07 | v2.81.0(Apple Silicon Worker) 당일 후속 — 연계 가시화 + 공시 RAG의 ERP 수신부.
> GPU 서버 추가형 변경만 (신규 BP 2개 + 대시보드 카드 1개) — 기존 API/동작 무변경

## A. iMac 연계 콘솔 Phase A — `routes/imac_link.py` + Admin 카드

GPU↔iMac 연계 설정·상태가 4곳 분산/전부 수동이던 것을 통합 가시화 (`imac-link-console.plan.md`).

- **`GET /api/imac-link/status`**: ①터널 3채널(8586 Ollama/8587 워커/8590 역방향 — 신호등+레이턴시) ②워커 health 프록시(memory_pressure/thermal/locks/capabilities/expose) ③위임 config(**토큰 비노출**, darwin_guard 표시) + **실적 카운터**(delegated/fallback+최근 10건) ④**오프로드 모델 교집합**(`models_ready` — "라우팅 대상인데 iMac에 모델 없음" 사고를 UI에서 즉시 노출)
- **Admin 대시보드 "iMac 연계" 카드** (admin.html + main.js, 30s 폴링) — 6/8 배포 검증의 시각 도구
- 워커 health에 `link.tunnel_8590_ok` (iMac→GPU 역방향 채널 자가 점검, 30s 캐시)
- Phase B(설정 변경 UI — LIGHTWEIGHT_MODELS config화)·C(원클릭 진단)는 배포 후 착수

## B. ERP 공시 수명주기 API — `routes/notice.py` (erp-notice-rag Day 2)

ERP가 공시(의료법/삭감 심사기준/수가 고시 등)를 push하는 수신부 5종. 색인 잡 생성까지 — 잡 소비 워커는 Day 3.

| API | 동작 |
|-----|------|
| `POST /api/notice` | 등록 — **멱등**: 동일 (erp_ref_id, revision_no)+동일 content_hash→200, 상이→409 |
| `POST /api/notice/batch` | ≤100건, **207 부분 성공** (accepted/rejected 인덱스별) |
| `PUT /api/notice/{id}` | 개정 — revision 역행 금지(409)·erp_ref_id 불변(400), supersedes 체인 + remove/index 잡 2건 |
| `POST /api/notice/{id}/revoke` | 폐지 (멱등) |
| `DELETE /api/notice/{id}` | 오등록 정정 |

- 검증 5중: 필수 필드 / **notice_type 코드표 8종**(ERP 합의) / 정규화 후 1MB(413) / **content_hash 대조 422**(정규화 7항 — 테스트 벡터 ERP 전달 완료) / mode
- 인증 `_blueprint_auth`(Bearer) + **600/minute** (기존 `_rate_limit_key`가 키 기반 버킷임을 확인 — ERP 프록시 단일 IP 우려 해소)

## C. 품질/정리

- **fix(컨벤션)**: `core/worker_guard.py` Flask 의존 제거 — `test_no_flask_imports`가 검출, check_light/check_heavy 순수 함수화 (데코레이터는 worker_app으로)
- **fix(tests)**: `test_korean_spellcheck.py`의 폐기 `CheckResult`(구 외부 API, 32dde5d 전환 시 미정리) 제거 — **단위 회귀 301 passed / 0 failed** 달성
- **known limitation**: macOS에서 c2pa 실서명 미지원 확정(passthrough — v2.79 전제와 일치). iMac mark API는 워터마크+iTXt 전담, `c2pa_signed:false` 정직 보고. C2PA 서명은 GPU 유지
- ERP 협의 산출물 전달: AI측 회신 + **해시 테스트 벡터 3종** (약속 1~2주 → 당일)

## 검증

- 단위 테스트 **301 passed / 6 skipped / 0 failed** (신규: notice API 13 + imac_link 3, 수리: spellcheck 11)
- 워커 라이브 재기동 — health `link` 필드 정상 (`tunnel_8590_ok: false` = 터널 미가동 정직 보고)
- JS 문법 검증 (node --check)

## 배포

- GPU: `git pull` + gunicorn 재시작 — **6/8 통합 배포에 포함** (`gpu-imac-link.plan.md` §4 11단계, runbook `DEPLOY_IMAC_DELEGATION.md`)
- notice API는 ERP 키 발급 전까지 호출 주체 없음(휴면) / imac-link 카드는 터널 복구 전까지 적색 신호등 표시(정상)

## 잔여

- erp-notice-rag Day 3(색인 잡 워커 — immediate 5분 SLA·nightly·상태/changes/sync API) / Day 4(query+인용+필터 강제)
- imac-link-console Phase B/C / worker Phase 2(STT mlx-whisper)·3(임베딩) / 6/8 배포 + 24h 관찰
