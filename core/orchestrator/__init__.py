"""Node Orchestrator — GPU↔iMac 통합 동적 작업 오케스트레이션 (Phase 1).

설계: docs/02-design/features/node-orchestrator.design.md
공개 진입점: dispatch.run(capability, payload, local_fn=...)

불변식:
  - 노드 0개 = 기존 동작 100% (config.json apple_worker_* 미설정 → 전부 gpu-local)
  - 폴백 예외 전파 금지 (원격/로컬 모두 가드)
  - Darwin 가드 (iMac 자신은 remote 위임 개시 안 함)
  - 무상태 워커 / 오케스트레이터 상태는 GPU 인메모리
"""
