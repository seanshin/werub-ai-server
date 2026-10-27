# Release v2.124.40 — 회의/강의 STT 백엔드 계약 정합 + 대기열 (edu 신규 요청 2건 봉합)

날짜: 2026-07-28 | 종류: fix(meeting) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

edu가 집합교육 녹화 → 자막·요약 파이프라인을 운영 개시(오디오 185초 → STT 8.5s +
요약 10.8s ≈ 22배속·korean_ratio 0.92~1.0)하며 **신규 요청 3건 + 운영 질의 3건** 제기.
이 중 2건이 **서버측 실결함**으로 확인되어 당일 봉합.

## 수정 3건

### 1. 위임 STT 응답 키 불일치 — "STT failed (imac-worker): unknown"

- **근본원인**: iMac 워커(mlx-whisper)는 전사 결과를 `text`로, 로컬 faster-whisper
  래퍼는 `full_text`로 반환하는데 `meeting_pipeline`이 `full_text`만 인정 →
  **위임 성공 응답이 매번 실패로 판정**. 에러 문자열 `unknown`은 워커가 에러를 보낸
  적이 없기 때문(정상 응답이었음).
- edu가 "산발 실패"로 관측한 것은 재시도가 **GPU 로컬로 배정**되어 성공한 것 —
  실제로는 **위임 경로 100% 실패**(meeting.db 이력의 imac-worker 실패 전건 동일 서명).
- `client.call`은 예외/`available:false`만 실패로 보므로 **dispatch 폴백도 미발동**
  (200 + 이형 페이로드가 폴백을 우회하는 구조적 사각).
- **fix**: `normalize_stt_result()` 순수 함수로 두 백엔드 계약 통일(`text`→`full_text`,
  segments 기본값) + **원격 결과 사용 불가 시 로컬 1회 자동 재시도**(edu의 수동
  재제출 요구 해소). 로컬 실패는 종전대로 실제 에러를 담아 전파.

### 2. 동시 상한 초과분이 실패하던 문제 (queue 단계 오실패)

- `_run`의 세마포어 `acquire(timeout=5)` — 동시 2건 초과 제출이 5초 후
  `error_stage=queue`로 **실패**. 집합교육 일괄 업로드 같은 버스트에서 정상 잡 손실.
- **fix**: 기본 대기 30분(`QUEUE_WAIT_S`)으로 상향해 **대기열로 흡수**. 대기 중
  status는 `pending` 유지(호출자 폴링에 실패로 보이지 않음).
  `MEETING_MAX_CONCURRENT` / `MEETING_QUEUE_WAIT_S` env 무재배포 튜닝.
  상한 2 자체는 16GB VRAM 제약(whisper large-v3 + 상주 LLM)이라 실측 선행 유지.

### 3. 용어 힌트 필드 신규 (의료용어 유사 발음 치환 완화)

- `options.initial_prompt`(문자열) 또는 `options.vocabulary`(문자열·배열) →
  faster-whisper `initial_prompt` 전달. **600자 상한**(whisper 프롬프트 창 224토큰 —
  과도한 용어집이 전사 자체를 밀어내는 것 방지).
- 힌트 지정 시 **결정적으로 로컬 백엔드 처리**: 워커 mlx-whisper는 프롬프트를 받지
  않아 위임되면 힌트가 조용히 사라진다(무성 실패 금지 규율).

## 부수

- `GET /api/meeting/status`에 **`diarize_available`**·`supported_options` 노출
  (edu의 ① 요청=화자분리 미실행은 **옵트인 `options.diarize` 미지정**이 원인·코드 결함
  아님. 호출자가 가용 여부를 셀프 확인하도록 계약 노출). 아울러 하드코딩이던
  `whisper_model`/`llm_model`/`max_concurrent`를 소스값 참조로 교체(실측 정합).

## 검증

- 회귀 `tests/test_meeting_stt_contract.py` **9** 신규 + orchestrator/stt 계열 포함
  **28 passed**(GPU venv).
- 라이브: graceful HUP 3회 전부 NRestarts 0·health 200·`/api/meeting/status` 신규 필드
  확인(`diarize_available: true`).
- 화자분리 라이브 실행 확인(SpeechBrain ECAPA·6초 오디오 2.3초·클러스터링 동작).
  ⚠️정직: 합성 신호로는 **화자 수 정확도 검증 불가** — 실제 2인 강의 파일로 edu 첫
  결과 대조 필요.

## 회신

`docs/01-plan/references/edu-ai-integration-reply.md` §10 (요청 3건 + 운영 질의 3건:
job 보존=무기한·레이트리밋=키당 120/min·동시 2건 상향은 VRAM 실측 선행).

## 부수 2 — `/openapi.json` 500 전면 중단 봉합 (동일 릴리즈)

전체 회귀 중 발견. v2.124.39에서 추가한 RAG `documents` 스키마의
`description: … {id, text, metadata}`가 **따옴표 없이 flow mapping 안에** 들어가
YAML 파싱이 깨졌고, flasgger가 스펙을 만들지 못해 **`/openapi.json`·`/apispec.json`·
`/docs`가 전부 500**(약 하루). 외부 연동 셀프서비스 정본이 중단된 상태였다.

- fix: description 인용부호 처리 → 복구 확인(**555 paths**·rag/meeting 경로 정상).
- 가드: `tests/test_swagger_docstrings.py` — `routes/*.py` 독스트링의 `---` 이후 YAML을
  **서버 없이 순수 파싱**(37 passed). 같은 클래스 CI 차단.
- ⚠️미검출 원인: GPU 레포 루트에 untracked 중복 `test_dur.py`(07-05 잔재)가 있어
  `pytest` 기본 실행이 basename 충돌로 **수집 단계에서 중단** → `tests/api`의 openapi
  검증이 아예 실행되지 않았다. **레포 루트 잔재 정리 필요**(운영자 확인 후).
  우회 실행 = `./venv/bin/python -m pytest --import-mode=importlib`
  (이 모드 전체 회귀 = **1015 passed / 7 skipped**, 잔여 실패 1건은 기존 건
  `test_no_flask_imports` — core/errors.py의 flask import, v2.124.25 도입분).
