# v2.124.70 — 히스토리 요약기 모델 재사용 (7b 추가 적재 제거)

> 2026-08-04 · 1파일 · 회귀 3건

## 문제

`core/chat_memory.py`의 히스토리 압축(요약) 함수에 **`qwen2.5:7b`가 하드코딩**돼 있었다.
세션이 길어질 때마다 요약기가 **7b(5.2GB)를 추가로 적재**한다.

상주 정책이 `llm_resident=false`(요청 시 탑재·v2.124.61)라 기본 모델 14b(10.5GB)와 겹치면
`MAX_LOADED_MODELS=2`에서 **15.7GB로 VRAM이 거의 차고**, heavy tier·배치 STT가 들어올 때
축출 → 다음 채팅에 **콜드 로드 2.5~3.9초**가 붙는다.

**실측**: 세션 기준 `qwen2.5:14b` 7,901건 vs `qwen2.5:7b` **27건**(0.34%) — 주 사용은 14b인데
소수의 보조 작업이 별도 모델을 띄우고 있었다.

## fix

`_summary_model()` — `core.llm_residency.resident_model(load_config())`로 **이미 떠 있는
상주/기본 모델을 재사용**한다. 요약은 품질 요구가 낮아 어떤 모델이든 되므로 **적재 0**인 쪽이 낫다.
설정 조회 실패 시 종전 기본값(`qwen2.5:7b`)으로 폴백해 동작을 보존한다.

`llm_resident_model`(채팅 기본과 상주 분리, v2.124.62)이 지정돼 있으면 그쪽을 따른다.

## 회귀 3건

`tests/test_chat_summary_model.py` — 상주 모델 사용 · 명시 override 추종 · 설정 실패 폴백.

## 남은 하드코딩 (변경 안 함)

`core/faithfulness.py`(야간 평가 verifier·`FAITH_VERIFIER_MODEL` env로 조절 가능) ·
`core/hr_law.py`(휴면) · `core/websocket_handler.py` · `core/gpu_scheduler.py`.
실제로 자주 도는 건 요약기 하나였다.
