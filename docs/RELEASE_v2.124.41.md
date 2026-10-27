# Release v2.124.41 — 실시간 STT 어휘 바이어싱(hotwords) + VoiceEMR 계약 실측 확정

날짜: 2026-07-28 | 종류: feat(stt) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

HIS VoiceEMR 연동 요청서(P0 선결 Q1~Q3). "`/api/stt/chunk`는 단발 성공만 확인됐고
스트리밍 프로토콜·동시성·바이어싱이 계약으로 미확정"이 착수 게이트라는 요청.
회신만 하지 않고 **Q3(어휘 바이어싱)는 서버에 구현**, Q1/Q2는 **라이브 실측으로 확정**.

## 신규 — 어휘 바이어싱

- `POST /api/stt/chunk/vocab` `{session_id, terms[], initial_prompt}` — 진료(세션) 단위
  등록·무활동 TTL 1시간. 이후 같은 `X-Session-Id` 청크에 자동 적용.
- `X-Hotwords` 헤더 = 무상태 오버라이드. ⚠️**HTTP 헤더는 latin-1이라 한글 직접 전송 불가**
  → 퍼센트 인코딩 수용(권장 경로는 JSON 등록).
- 상한 **200개 / 600자**(whisper 프롬프트 창 224토큰). HIS 약품마스터 24,022건 전량 주입은
  원리적으로 불가 → shortlist 전제. 초과분은 **조용히 버리지 않고** `truncated`·
  `terms_applied`로 통지(실측 24,022건 → 66건 적용).
- `transcribe_pcm(..., hotwords=)` (faster-whisper 1.2.1 `hotwords` 사용).
- 응답 `hotwords_applied`, `/status`에 `hotwords_supported`·`vocab_sessions`·
  `serialized_inference`(동시 요청 큐잉 계약 명시) 노출.

## 실측으로 확정한 계약 (회신 근거)

- **지연은 오디오 길이와 거의 무관**(2s 140ms / 30s 127ms) — whisper가 30초 창으로 패딩하므로
  비용이 *호출 횟수*에 붙는다. → 청크를 잘게 쪼갤수록 총 부하 증가(1.5s 미만 비권장).
  ⚠️합성음 기준 하한이며 실발화는 PoC 실측 320~659ms.
- **추론 완전 직렬**(`_infer_lock`): 동시 2/3/5 = 268/405/661ms(마지막 응답 257/378/630ms).
  실발화 환산 부하율 ≈ 0.2N → **권장 운영 상한 동시 4~5 진료실**.
- **실시간(medium) ↔ 배치(large-v3) 경로는 락이 달라 상호 블로킹 없음**, 단 **VRAM 공유**
  (상주 LLM 10.3/16.3GB) → 대량 배치와 외래 피크 겹침 비권장(07-08 CUDA OOM 이력).
- **warm은 유지되지 않는다**: 워커 ~40분 재생성 시 언로드(콜드 18s·워밍 후 2ms) →
  진료 시작 직전 `/warm` 1회 규칙 필요.
- ⚠️**바이어싱 부작용 실측**: 무음/잡음에서 hotword가 출력으로 유도되는 환각 관측 →
  클라이언트 VAD 게이팅 + `low_confidence` 보류 가드 유지 권고.
- 부수 확인: `application/octet-stream` POST는 **Bearer 없으면 CSRF 가드가 415**로 거부
  (HIS는 Bearer라 무영향·로컬 무키 테스트만 해당).

## 검증

- 회귀 `tests/test_stt_realtime.py` +4(계열 48 passed).
- 라이브 E2E: shortlist 등록→적용(`hotwords_applied` true)·24k 절단 통지·퍼센트 인코딩
  헤더 경로·세션 해제까지 확인. 임시 벤치 키는 발급 후 삭제 완료.

## 회신

`docs/01-plan/references/voice-emr-stt-reply-to-his.md` (Q1~Q3 확정 + Q4~Q8 원문 요청 +
실제 진료 발화 샘플/정답 전사 요청 = Q3 정량 캘리브레이션 전제).
