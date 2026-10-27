# Release v2.124.47 — 당일 변경분 자체 점검(위생·배선 고정)

날짜: 2026-07-29 | 종류: fix/refactor | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

하루에 릴리즈 7건(v2.124.40~46)을 빠르게 낸 뒤 **그 변경분 자체를 점검**했다.
두 건이 걸렸고, 둘 다 오늘 내가 만든 것이다.

## 1. 신규 저장소 파일 위생 (v2.124.45 자체 결함)

`stt_vocab.db`가 ①**644(world-readable)** 로 생성돼 2026-07-25 하드닝의 "전 DB 600" 규율
(제2 로컬 계정 sakpung 존재)을 어겼고 ②**.gitignore에 없어 커밋될 수 있었다**.
저장 내용이 진료 단위 어휘(환자 활성 처방에서 파생 가능)라 준민감으로 다뤄야 한다.

- `_vocab_db()`: 생성 직후 본체·`-wal`·`-shm` 권한 **600**(best-effort).
- `.gitignore`에 `stt_vocab.db` 추가. 라이브 기존 파일도 600으로 정정.
- 검증: 새 쓰기 후에도 600 유지 · 영속 복구 정상.

## 2. edu ③ 배선을 테스트로 고정 (검증 공백)

용어집을 `initial_prompt`→**`hotwords`**로 돌린 v2.124.42의 **핵심 배선이 인라인**이라
시그니처 테스트만 있고 실제 라우팅은 검증되지 않았다. → `stt_options()` 순수 함수 분리 +
회귀 4건(용어집→hotwords 확인·CSV/배열/별칭·프롬프트 분리 유지·빈값 None).

부수: `/api/meeting/status`의 `supported_options`가 코드와 어긋나 있었다 —
실제로 읽는 `keyframe_interval`을 넣고, 읽지 않는 `summary`를 뺐다.

## 3. 사전 확인(문제 없음)

- **배치 추론 × hotwords 조합**: `BatchedInferencePipeline.transcribe`가
  `hotwords`·`batch_size`·`initial_prompt`·`vad_*`·`beam_size`를 **전부 지원**함을 확인
  (미지원이었다면 edu가 배치를 켜는 순간 TypeError로 잡이 죽었을 자리).
- `session_append` 반환 계약 변경의 프로덕션 호출자는 1곳뿐(수정 완료).

## 검증

`tests/test_meeting_stt_contract.py` 20 → **24** · GPU HUP NRestarts 0 · `/status` 신규 계약 확인.
