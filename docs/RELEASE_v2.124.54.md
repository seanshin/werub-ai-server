# Release v2.124.54 — Ollama 0.30.11 → 0.32.5 업그레이드

날짜: 2026-07-29 | 종류: ops(엔진) | 배포: 바이너리 교체 + `systemctl restart ollama`

## 배경

0.30.11에서 5개 마이너 뒤처짐. 07-27에는 "실익 제한적이라 관망"으로 판단했으나, libc6
재부팅 창을 거치며 함께 정리하기로 결정. **릴리즈 노트 12종(0.30.11~0.32.5) 전수 검토 결과
우리 사용 경로에 breaking change 없음**을 먼저 확인했다.

## 사전 검토 — 충돌 없음

- v0.32.0 "Warns before launching older agent models: **Qwen2.5**…" → **`ollama launch`(CLI
  통합 메뉴) 한정**. 우리는 `/api/chat`·`/api/generate`·`/api/embed`만 쓰므로 무영향.
  (다만 업스트림이 qwen2.5를 '구형'으로 분류하기 시작한 신호는 모델 전략에 기록.)
- v0.31.2 "Fixed structured output for **thinking models when thinking is disabled**" →
  heavy(`qwen3.6:35b-a3b` think=false)·eval `format` 제약에 **유리한 방향**.
- v0.32.3 "Fixed GLM tool calls being silently dropped" → 우리는 GLM 미사용이나 도구 호출
  경로 재검증 대상으로 반영.
- v0.32.4 "Fixed Qwen3 MoE decoding" → heavy 슬롯에 유리.
- 나머지는 MLX/Apple Silicon·Windows·통합 UI 위주로 무관.

## 검증 매트릭스 확장 (선행 작업)

기존 스크립트는 v2.114.0 시점이라 **그 뒤 만든 기능을 검증하지 않았다**. 5종 추가:
비전 `/api/generate+images` · 비전 `/v1` 멀티모달 · `/v1` tool_calls 계약 ·
**모델 digest 안정성**(규제 트랙이 고정 참조) · 의료 라우팅(M4 fail-closed 영향).

⚠️ 확장 중 발견: 비전 검증을 **Ollama에 직접** 쏘면 qwen3-vl(thinking 모델)은 토큰이
`thinking`으로 가고 `response`가 빈 채 `done_reason=length`가 된다(num_predict를 키워도 동일·
`think:false`도 0.30.11에선 무효). **v2.118.2의 '빈 응답' 관측이 같은 메커니즘**이었다.
우리 계약은 앱 경로(시스템 프롬프트 주입·num_predict 미구속)이므로 검증도 앱 경로로 교정.

## 결과

| 단계 | 결과 |
|------|------|
| 기준선(0.30.11) | **21 PASS / 0 FAIL** |
| 업그레이드 후(0.32.5) | **21 PASS / 0 FAIL** |

- 버전 0.32.5 · **모델 38개 무손실** · **env drop-in 6종 보존** · 생성속도 **86 tok/s**(기준 동일)
- **digest 7cdf5a0187d5 불변** → 규제 트랙(트윈 고정 참조) **통지 불요**
- format 제약·임베딩·비전 2경로·tool_calls·의료 라우팅 전부 정상
- 실사용 스모크: `/api/chat` 정상 · RAG 검색 5건 · 의료법 ask 정답 포함 · health 200 ·
  vram_ratio 1.0 · p95 580ms · 경보 0

## 롤백 자산 (보존)

- `/usr/local/bin/ollama.0.30.11`(38MB) · `/usr/local/lib/ollama.0.30.11`(2.1GB)
- 롤백 = 두 경로를 되돌리고 `systemctl restart ollama`. 디스크 여유 220G라 당분간 보존.
- soak 24h(worker_churn·latency_p95 경보 + **야간 eval 배치**가 format 제약의 실사용 검증)
  통과 후 정리.

## 잔여

- soak 관측: 07-30 03:30 eval 배치(format 제약 디코딩 대량 사용) 결과.
- 백업 정리(4.3GB)는 soak 후.
