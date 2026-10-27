# Release v2.124.52 — 배치 추론·int8 양자화 적용 (edu 승인) + config 무재배포 토글

날짜: 2026-07-29 | 종류: feat/ops(meeting) | 배포: GPU graceful HUP(NRestarts 0) + config 적용

## 1. 토글을 config로 이관

배치 추론·양자화는 env(systemd 드롭인)로만 바꿀 수 있어 **sudo 없이는 운영 중 전환이 불가**했다.
이 프로젝트 관례(무재배포 config 토글)로 옮겼다.

- `meeting_stt_batched` · `meeting_stt_batch_size` · `whisper_compute` (env는 폴백, 미설정 시
  기존 동작 불변).
- **compute 변경 시 모델 자동 재적재**(배치 파이프라인도 함께 무효화) → 토글 즉시 반영.
- `/api/meeting/status`에 현재 값 노출 — A/B 결과와 설정을 함께 식별.

## 2. edu 승인으로 A/B **적용**

| 설정 | 값 | 실측 |
|------|-----|------|
| 배치 추론 | on(batch_size 8) | `BatchedInferencePipeline` 활성 |
| 양자화 | int8_float16 | 모델 VRAM **3.84 GB → 1.91 GB** |

→ **동시 2건 STT가 하드웨어상 가능해졌다**(종전 2개 적재 = CUDA OOM 실증). 단
`max_concurrent`는 2 유지 — A/B 결과 확인 후 상향 판단.

## ⚠️ 미측정 리스크(정직 기록)

우리 쪽에 **실제 한국어 강의 음성이 없어 품질 영향을 측정하지 못했다.** 확인한 것은
①경로 정상 동작 ②VRAM 감소뿐이다. int8은 이론상 정확도 손실이 있을 수 있고, 하필 그게
edu가 중요하게 보는 의료 전문용어 인식이다.

- edu에 **185초 기준선 파일 재제출**을 요청했다(속도 8.5초 대비 / 용어 정확도 유지 여부).
- **되돌리기는 config 한 줄**(`whisper_compute` 제거 → float16 복귀·재배포 불필요).

## 검증

회귀 `tests/test_meeting_stt_contract.py` 31 passed(config 토글 2건 포함) ·
실 DB import dry-run 통과 후 HUP · health 200.
