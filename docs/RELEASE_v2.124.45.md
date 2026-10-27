# Release v2.124.45 — 실시간 STT 슬라이딩 유실 통지 + 어휘 등록 영속화 (HIS Q9/Q11)

날짜: 2026-07-29 | 종류: feat/fix(stt) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

HIS VoiceEMR 후속 요청 Q9~Q11. Q9(장문 SOAP 구술 스티칭 책임 소재)·Q11(vocab 워커 재생성
지속성) 검토 중 **양쪽 다 서버 결함이 확인**되어 봉합.

## 1. 세션 슬라이딩 유실 통지 (Q9)

`session_append`가 30초 상한 초과분을 **조용히 폐기**했다. 수 분짜리 구술에서는 그대로
전사 누락이며 호출자는 알 방법이 없었다(무성 실패 클래스).

- 버린 길이를 세션에 누적해 응답 **`dropped_s`** + **`session_max_s`** 로 통지.
- 관측용 `session_state(session_id)` 추가(buffered_s·dropped_s·max_s).
- 반환 계약 변경: `session_append() → (pcm, dropped_s)` (프로덕션 호출자 1곳 동시 수정).
- 라이브: 20s+15s 누적 → `dropped_s: 5.0` 확인.

## 2. 어휘 등록 영속화 (Q11)

vocab이 인메모리 dict라 gunicorn 워커 재생성(~40분)마다 소실 → TTL 1시간과 무관하게
진료 중 바이어싱이 조용히 꺼졌다.

- `stt_vocab.db`(sqlite·WAL) 영속화 + 조회 시 자동 복구 → **호출자 재등록 불요**.
- 저장 대상은 **어휘·프롬프트뿐**(발화·전사 미저장 = PHI 비저장 원칙 유지). TTL 만료분 정리.
- 영속화 실패는 경고만(인메모리 등록은 유효·비차단). `/status`에 `vocab_persistent`.
- ⚠️ 모델 상주(warm)는 별개 — 워커 재생성 시 여전히 언로드되므로 `/warm` 규칙은 유지.

## 검증

- 회귀 `tests/test_stt_realtime.py` 11 → **15**(유실 통지 2·영속 복구 2 + 기존 계약 갱신).
- 라이브 E2E: `dropped_s` 통지·`vocab_persistent: true`·`hotwords_applied: true`.
  임시 검증 키는 발급 후 삭제.

## 회신

`docs/01-plan/references/voice-emr-stt-reply-to-his.md` [후속 회신] Q9~Q11
(스티칭 권장안 5항·X-Targets 계약·vocab 재등록 불요).
