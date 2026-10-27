# Release v2.124.42 — 화자 병합·용어집 hotwords 전환·버스트 접수 봉합 (edu 2차 요청)

날짜: 2026-07-28 | 종류: fix/feat(meeting) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

edu가 v2.124.40 반영 후 2차 요청 3건(화자 정보 위치·용어 힌트 미적용·처리 용량 상향).
대조 결과도 함께 제출(합성 2화자 185초: speakers_detected 2·오배정 0 — 단 TTS 피치 변조
합성이라 실강의 대조는 여전히 필요).

## 수정·추가 4건

### 1. 화자 정보를 transcript.segments에 병합 (edu ②)

화자가 `speaker_segments`에만 실려 호출자가 두 배열을 조인해야 했다(의도가 아닌 미완성).
`merge_speakers_into_segments()` 순수 함수로 분리해 `transcript.segments[].speaker`를 채운다.
`speaker_segments`는 유지 → **edu 현재 배포 무수정 동작**. 길이 불일치 시 **틀린 화자를
붙이느니 병합 skip**(speaker=null + warning).

### 2. 용어집 전달 경로 initial_prompt → hotwords (edu ③)

v2.124.40은 `options.vocabulary`를 whisper `initial_prompt`로 넣었는데, 이는 문맥 프롬프트라
**개별 용어 표기 교정력이 약하다**(edu 실사용: 요로감염·재원일수 미교정, 세그먼트 분할만
41→31로 변동=프롬프트가 문맥에 영향 준 흔적). faster-whisper `hotwords`로 전환.
`options.initial_prompt`는 문체·주제용으로 분리 유지.

### 3. 🔴 버스트 접수 503 오거절 봉합 (edu ④ 과정에서 발견)

**v2.124.40의 대기열 흡수가 절반만 동작했다**: `can_accept()`가 `active >= MAX_CONCURRENT(2)`
면 제출을 503으로 거절해 세 번째 업로드부터 접수 단계에서 튕겼다(대기열까지 도달하지 못함).
→ **접수 판정을 backlog(대기+진행) < `MEETING_MAX_QUEUE`(기본 50)** 로 변경.
대기 상한도 30분 → **4시간**(30건 꼬리 흡수: 20건×4분/동시2 = 40분).
`/api/meeting/status`에 `queued_jobs`·`max_queue`·`queue_wait_s` 노출.

### 4. 배치 추론 opt-in (용량 레버)

`BatchedInferencePipeline` 사용 가능 확인 → `MEETING_STT_BATCHED=1`·`MEETING_STT_BATCH_SIZE`(8).
기본 off = 기존 동작 불변, 미지원 버전이면 자동 기본 경로. **같은 VRAM에서 건당 처리 시간
단축** 레버(실 파일 A/B 미실시 — edu 185초 기준선으로 검증 예정).

## 용량 실측 (edu ④ 답변 근거)

| 항목 | 실측 |
|------|------|
| 여유 VRAM | **5.61 GB** / 16.61 GB (상주 LLM이 나머지) |
| large-v3 float16 | **3.84 GB/인스턴스** |
| large-v3 2개 동시 적재 | ❌ **CUDA OOM**(실시도 확인) |
| large-v3 int8_float16 | **1.93 GB** → 여유 내 2개 가능 |
| medium float16 | 1.96 GB |

→ **현 하드웨어에서 large-v3 병렬 STT는 1개가 한계**. 동시 4~8은 (A)배치 추론 (B)int8 양자화
(C)GPU 증설(동시 4 ≈ +8GB·24GB면 3~4) (D)일정 사전 통지 중 선택 필요. A·B는 env 토글로
준비 완료(재배포 불필요), C는 운영자 판단 사안.

## 검증

- 회귀 `tests/test_meeting_stt_contract.py` 9 → **15**(화자 병합 2·hotwords 계약·대기열 2 등).
- 라이브: HUP 3회 NRestarts 0·`/api/meeting/status` 신규 필드 확인(max_queue 50·
  queue_wait_s 14400·supported_options에 hotwords).

## 회신

`docs/01-plan/references/edu-ai-integration-reply.md` §11 (용어 재시도 요청 + 배치/int8
토글 제안 + 증설 조건 수치).
