# Release v2.124.53 — 배치 STT CUDA OOM 근본 원인 봉합(SANA VRAM 미반환)

날짜: 2026-07-29 | 종류: fix(sana/meeting) | 배포: 앱 HUP 완료 · **사이드카 재시작 대기(운영자)**

## 배경 — 내가 만든 장애

v2.124.52에서 edu 승인을 받아 **배치 추론 + int8을 동시에 켰다**. edu가 181초 파일에서
**CUDA OOM 2회 실패**를 보고(30초 파일은 성공·`active_jobs:0`이라 경합 아님)하며 롤백을 요청.

## 실측 (284초 한국어 음성 — 이번에 확보한 검증 자산)

| 설정 | 결과 |
|------|------|
| **int8 · 배치 off** | ✅ 62.2s · 207 seg · VRAM 1.13GB · **의료용어 7/7** |
| int8 · 배치 8 | ❌ CUDA OOM (edu 보고 재현) |
| **float16 · 배치 off**(원래 기본) | ❌ **CUDA OOM** |

→ **전체 롤백은 오히려 더 막는다**(여유 3.14GB < float16 3.84GB). `int8 + 배치 off`로 적용.

## 근본 원인

**SANA 사이드카가 `loaded:false`인데도 VRAM 2.36GB 점유.** `_do_unload()`가 참조만 끊고
(`_pipe=None`) **`gc.collect()` 없이** `empty_cache()`를 호출 — diffusers 파이프라인은 순환
참조가 많아 gc 없이는 해제되지 않으므로 반납할 캐시 블록이 없다. **모드 스왑 경로에는 gc가
있는데 이 경로에만 없던 비대칭**(v2.116.0 OOM fix 때 한쪽만 고쳤다).

## 수정

- `sana_service._do_unload`: `gc.collect()` + `synchronize()` 추가, `_pipe`가 None이어도
  캐시 반납 시도(명시적 `/unload`는 "지금 비워라"는 뜻). ⚠️**사이드카 재시작 후 효력**(sudo).
- `meeting_pipeline._free_sana_vram()`: 배치 STT 직전 사이드카에 반납 요청
  (실시간 STT 반납과 동일 규율·비차단).
- 회귀 +1(반납 실패가 본 경로를 막지 않음) — 계열 32 passed.

## 교훈

- **품질을 못 재는 상태에서 두 변수를 동시에 켜지 말 것.** 한 번에 하나씩, 긴 오디오로 먼저.
- VRAM 점유자는 Ollama·실시간 STT만이 아니다 — **이미지 사이드카도 상시 후보**.
  `loaded:false` 보고를 믿지 말고 `nvidia-smi --query-compute-apps`로 실측할 것.
- 284초 검증 자산 확보(macOS TTS 합성·의료 용어 포함) → 앞으로 설정 변경 전 자체 검증.

## 잔여

운영자 `sudo systemctl restart sana` → 2.36GB 회수 → float16 복귀 여부 재판단.
