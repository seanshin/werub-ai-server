# Node Orchestrator (Phase 1) — Gap 분석

> **Feature**: node-orchestrator | **Phase**: 1 (코어 + detect 이전) | **Date**: 2026-06-08
> **Design**: docs/02-design/features/node-orchestrator.design.md
> **Method**: bkit gap-detector (설계 vs 구현 대조)

## 종합

| 항목 | 점수 |
|------|:----:|
| Design Match (Phase 1) | **96%** |
| 아키텍처/불변식 준수 | 100% |
| 테스트 커버리지(불변식) | 90% → (hysteresis 추가 후) ✅ |
| **Overall (Phase 1)** | **96%** ✅ (90% 임계 초과 — [Act] 반복 불요) |

## 검증된 설계 항목 (전부 PASS)
- 불변식 4종: 노드0=무변경 / 폴백 무전파(원격·로컬 가드) / Darwin 가드 / 무상태 워커
- C1 thermal `throttled` 게이트 / C2 `memory.pressure` 중첩·`unknown` 미게이트
- W1 ok=HTTP200∧available / W2 `_LOCK` 전 변이 보호·inflight 무누수(64스레드 테스트)
- W3 payload_kind 인코딩 / W4 `_run_local` 예외 가드 / W6 engine 스탬프(gpu-local/imac-worker)
- 스케줄러 하드게이트(미도달·manifest·breaker·mem critical·thermal throttled·heavy lock) + 소프트가중 + `gpu_only` 킬스위치

## 발견·조치
| 항목 | 심각도 | 조치 |
|------|:------:|------|
| hysteresis 로직 테스트 누락 | Medium | ✅ 유지/전환 테스트 2건 추가(scheduler 14건) |
| apple_worker "흡수" 설계 vs 구현 "유지+config 재사용" | Low | ✅ 의도된 reconcile로 설계 §14 기록(저위험·동작보존). 두 경로 공존은 Phase 2 기술부채 |
| 설계 §11 "thermal serious" 잔재 | Info | ✅ "throttled" 정정 |
| `half_open` enum 사실상 미사용 | Info | 보류(만료 breaker는 breaker_until로 게이트 — 기능상 무해) |

## 결론
Phase 1은 설계대로 구현됨. 라이브 반영 완료(detect engine=gpu-local 확인, 휴면=node0 무변경 보존).
잔여 조치 모두 반영 → **report 단계로 진행**. Phase 2(워커 generic + mark/stt/embed)는 별도 사이클.
