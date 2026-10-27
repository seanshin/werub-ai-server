# Release v2.86.0 — STT 오프로드 LIVE + 보안·신뢰성 하드닝

> 2026-06-08 | 이전: v2.85.0

## 하이라이트

### 1. 🟢 STT 자동 오프로드 LIVE
회의 분석 STT가 iMac으로 자동 위임된다. 이제 **GPU만 호출하면 detect/mark/stt 3종 모두** iMac에 배정.

- `meeting_pipeline` STT 단계 → `dispatch.run("stt")` → **engine=imac-worker** (mlx-whisper), 실패 시 faster-whisper 폴백
- 멀티파트 필드 `file`→**`audio`** (워커 계약 일치), capability `timeout_s=1800`(장시간 전사)
- 워커측(operator): MLX Metal이 gunicorn fork 후 접근 불가 → **요청마다 subprocess spawn**으로 해결 + 실패 시 503 graceful (99e5bc4)
- 검증: 워커 `/worker/stt` 200, GPU end-to-end `engine=imac-worker`, 라이브 caps=[detect,mark,stt]

### 2. 🔒 보안 — C1 (인증 누락 차단)
- `imac_link`/orchestrator 엔드포인트에 인증 훅 추가 — `/api/orchestrator/registry/reload`(상태변경)·`/nodes`·`/imac-link/status`가 외부 무인증 노출이던 문제 차단
- 검증: 외부(CF-Connecting-IP) 무인증 → **401**, localhost → 200 (모든 블루프린트와 동일 모델)

### 3. 🛡 신뢰성 (code-analyzer 반영)
- **M5**: 헬스 폴링을 요청 스레드 밖 **비차단 백그라운드**로 — gthread 고갈 트리거 제거(과거 SSE 인시던트 패턴 회피)
- **H1**: client 파일 핸들 누수 차단(STT 장시간/예외에도 finally close)
- **M1**: `dispatch`가 `scheduler.pick` 예외도 폴백(never-raise 완성)
- **M3**: inflight 카운터 누수 가드(예외에도 정산)
- **M4**: config 수치 강제 변환(오타 방어)
- detect/mark **콜드스타트 타임아웃 상향**(관측 9s>5s 폴백 제거), 워커 `detector.available` 게이팅(ML 다운 시 breaker churn 제거)

### 4. 📄 문서 / 조율
- `docs/ORCHESTRATOR_GUIDE.md`(호출법) · `ORCHESTRATOR_GATEWAY_BOUNDARY.md`(gateway↔orchestrator 경계) · `MERGE_OWNERSHIP.md`(동시작업 충돌 예방) · `IMAC_WORKER_TODO.md`(워커 핸드오프)
- 기획: `node-orchestrator-p2-stt` / `-p2-embed` / `-p3-llm`

## 운영 노트
- GPU 코드 반영 = 전체 재기동(`preload_app=True` → SIGHUP 무효). config(delegate 등)는 핫로드.
- **동시작업 충돌 실사례**: STT 활성화 중 `client.py _encode`에서 operator와 머지 충돌 발생·해소 → `MERGE_OWNERSHIP.md`의 핫스팟 조율 필요성 확인. VERSION/CHANGELOG는 GPU 단일 소유.

## 현황 / 다음
| Phase | 상태 |
|---|---|
| 1 detect | 🟢 LIVE |
| 2 STT | 🟢 LIVE |
| 2 mark | 위임가능(미배선, 경량이라 보류) |
| 2-b embed | 📋 기획 (operator 모델/컬렉션 결정 대기) |
| 3 LLM/큐 | 📋 기획 (gateway 루프 가드 선행) |

## 카운트
- VERSION 2.85.0 → **2.86.0** | API 551+ | Blueprint 33 | Core 33
