# Release v2.124.43 — 실시간 VoiceEMR ↔ 배치 STT VRAM 경합 완화

날짜: 2026-07-28 | 종류: fix(meeting/stt) | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 배경

v2.124.42에서 배치 경로 접수를 대기열 기반(50건)으로 완화하면서 **집합교육 일괄 업로드가
GPU를 오래 점유하는 구간**이 생겼다. HIS VoiceEMR(실시간)과 같은 GPU를 쓰므로 경합을
실측으로 확인했다.

## 실측 — 경합 재현

| 상태 | 여유 VRAM | 배치 large-v3(3.84GB) |
|------|-----------|------------------------|
| 상주 LLM만 | 5.61 GB | 적재 OK |
| 상주 LLM + 실시간 medium(1.96GB) | **3.18 GB** | **CUDA OOM** |

07-08 배치 실패 이력과 동일 패턴. "락 분리"(서로 기다리지 않음)는 유효하지만 **VRAM은 공유**.

## 완화 — 진료 우선 정책을 코드로 고정

- `core/stt_realtime`: `unload()`(VRAM 반납)·`idle_seconds()`·`_last_used` 추적,
  `/status`에 `idle_seconds` 노출.
- `core/meeting_pipeline._free_realtime_stt_vram()`: 배치 STT 직전 **유휴 5분** 실시간 모델
  반납, OOM 발생 시 **유휴 1분** 조건으로 반납 후 **1회 재시도**.
- **활성 세션(`active_sessions>0`) 또는 최근 발화면 반납하지 않는다** — 라이브 진료를 끊는
  대신 배치를 실패시키고 대기열이 다시 태운다. env `MEETING_RT_IDLE_S`/`MEETING_RT_IDLE_FORCE_S`.

## 검증

- 회귀 `tests/test_meeting_stt_contract.py` +4 (진료 중 미반납·최근 사용 미반납·유휴 반납·
  미로드 no-op) — 계열 31 passed.
- ⚠️정직: 반납 정책은 **단위 테스트로 검증**했고, 실제 배치 잡과 진료가 겹치는 상황의
  라이브 재현은 하지 않았다(실 녹화 파일·동시 진료 필요). 경합 자체(OOM)는 라이브 재현함.

## 통지

`docs/01-plan/references/voice-emr-stt-reply-to-his.md` [추가 통지] — HIS에 경합 실측·완화
정책·`/warm` 규칙 재확인·외래 피크 시간대 요청.
