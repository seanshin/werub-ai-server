# Release v2.124.57 — 실시간 STT 무음 환각 처리 (HIS 요청 3건: no_speech_prob·에코 억제·VAD 게이트)

날짜: 2026-07-29 | 종류: feat/fix(stt) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

HIS 무음/환각 처리 요청서(`his-werub-stt-silence-request-2026-07-29.md`). 실측 제보:
무음/초단발화 PCM에서 STT가 프롬프트·어휘 문구를 전사문으로 방출.

- 0.5초 무음 → "진료, 복용, 약국, 예약 … 의료 용어가 사용됩니다." (**프롬프트 에코**)
- 저에너지 → "이 영상은 유료 광고를 포함하고 있습니다." (**whisper 학습데이터 정형 환각**)
- 응답에 무음 판별 신호 부재 → HIS가 걸러낼 방법 없음.

⚠️두 사례는 원인이 다르다 — 에코는 서버가 주입한 initial_prompt가 디코더를 바이어스한
결과라 **confidence가 높게 나와 기존 `low_confidence` 게이트를 통과**한다(v2.124.41에서
"무음 hotword 유도 환각"으로 관측했던 클래스의 프롬프트 판).

## 1. 무음 판별 신호 노출 (요청 1 — 최우선)

`/api/stt/chunk` 응답에 세그먼트 집계 3종 + 편의 필드 추가:

- **`no_speech_prob`**(평균·세그먼트 0개면 1.0) — 에코 환각의 **유일한 판별 신호**.
  HIS 드롭 임계 권장 0.6(whisper 관례값)부터 캘리브레이션.
- `avg_logprob`(평균·없으면 null)·`compression_ratio`(최댓값=반복 환각 지표·없으면 null).
- `no_speech`(불리언=최종 전사 빈 문자열)·`vad`(on|off).

## 2. 프롬프트 에코·정형 환각구 서버 억제 (요청 2)

프롬프트는 서버 주입 값 → 에코 차단도 서버 책임. `core/stt_realtime.py`:

- **`_detect_suppression` 2축**: ①**순서 보존 부분열 매칭**(`_subseq_ratio` ≥0.85) —
  에코는 어휘를 **건너뛰며 재조합**돼 나와서(제보 사례: 혈압·당뇨 생략) 연속 substring
  검사로는 원리적으로 못 잡는다 ②**무순서 어절 커버리지**(`_word_coverage` — 어절 ≥4개
  AND 프롬프트 어휘 구성 비율 ≥0.8). 🐞**라이브 검증이 축 ② 필요를 실증**: 배포 직후
  무음 0.5s 재현에서 에코가 어휘를 **재배열·반복**까지 해("…약국, 예약, **당뇨**…" —
  원문 순서와 다름) 축 ①이 0.61로 통과 → 즉시 보강. 실발화는 명사에 조사가 붙어 어절
  자체가 달라지므로("혈압이"≠"혈압") 무순서 대조가 정확한 분리축.
- **실발화 보호 2중**: 정규화 6자 미만("처방전"·"네 진료요") 판정 제외 + 실발화는
  조사·동사가 프롬프트에 없어 비율 미달("혈압이 높아서 약을 처방받으러 왔어요" 미차단).
- **정형 환각구 6종**(유료 광고·시청 감사·구독 등 유튜브 기원): **문구-단독일 때만** 차단
  (실문장에 섞이면 통과). env `STT_HALLU_PATTERNS`로 교체 가능.
- 차단 시 `text: ""` + **`suppressed: prompt_echo|hallucination_pattern`**. 원문은
  응답·로그 미포함(길이만 로깅=PHI 원칙). env `STT_ECHO_SUPPRESS=0`으로 노출-only 전환.
- **`condition_on_previous_text=false` 기본 적용**(신규 env `STT_CONDITION_PREV`) —
  청크 오디오 ≤30초=whisper 창 1개라 정확도 손실 없이 환각 연쇄 통로만 차단.

## 3. 서버측 VAD 게이트 (요청 3 — 옵트인)

- `X-VAD: on` + `X-Min-Speech-Ms`(기본 250) 헤더 → faster-whisper **Silero VAD** 배선
  (`vad_filter=True`+`min_speech_duration_ms`). 무음 청크는 인코더 전에 걸러져
  세그먼트 0개 → `text: ""`·`no_speech_prob: 1.0`.
- 기본 **off = 기존 계약 불변**. 클라이언트 에너지 게이트(HIS Q4 이행)와 중복 무해
  (서버 VAD는 저에너지 잡음의 2차 방어선).

## 검증

- 회귀 `tests/test_stt_realtime.py` 15 → **26**(+11): 부분열 에코 감지(substring 불가 형태
  고정)·**재배열 에코 라이브 실측 케이스**·어절 커버리지 실발화 분리·hotwords 에코·
  실발화/짧은 용어 보호·환각구 단독/혼합·빈 PCM=no_speech 1.0·모의 모델 계약(지표 집계·
  suppressed·**VAD 파라미터 실전달** — 배선 무검증 함정 방지)·세그먼트 0개 집계.
  Swagger 가드 37 passed.
- **라이브(임시 키·검증 후 삭제)**: ①무음 0.5s VAD off = HIS 제보 그대로 에코 재현 +
  **confidence 0.646으로 low_confidence 통과 실증**(no_speech_prob 0.732=드롭 신호 유효)
  → 축 ② 보강 후 suppressed 확인 ②무음 0.5s VAD on = `text:""`·no_speech_prob 1.0·44ms
  ③저에너지 잡음 1s VAD off = "도쿄에서 MBC 뉴스 …" 정형 환각(no_speech_prob 0.769로
  HIS 드롭 가능)·VAD on = 완전 차단 2ms.

## 회신

`docs/01-plan/references/voice-emr-stt-reply-to-his.md` [회신] 무음/환각 처리 —
응답 예시·HIS 드롭 로직 권장·임계 캘리브레이션은 실발화 샘플 대기(Q3-5와 동일 항목).
