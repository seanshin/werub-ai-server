# RELEASE v2.124.10 — 규제 라우팅 가드(opt-in) + 자체 파인튜닝 모델 카드 2종

2026-07-19 | 이전: v2.124.9(의료법 벌칙 오귀속 근본 봉합) | 배경: 식약처 LLM 가이드라인
(안내서-1511-01) 대응 — **WeRU.B↔트윈 동일 법인 확정**(운영자, 2026-07-19)으로 유형①
(자체 개발 모델=직접 위험통제 대상) 논점 활성화에 따른 대비 2건.

## 1. 규제 라우팅 가드 — `medical_pin_public_models` (opt-in)

- 배경: 트윈 임상 경로(predict·DUR·ADR·reference·explain·translate)에 자체 파인튜닝
  모델이 미관여함을 실측 확인(reply2 Q2)했으나, 이는 **활성 설정
  (`medical_model_active.json` heavy=werubmedv2)의 현재 상태일 뿐 구조적 보장이 아님 —
  설정 드리프트로 관여가 생길 수 있음.
- 구현: `routes/medical.py` `is_custom_model`(werub* 접두)+`_pin_public` —
  config `medical_pin_public_models`(기본 **false**=기존 동작 100% 불변) 활성 시
  `_get_medical_model` 자동 라우팅과 `/api/chat/enhanced` 파이프라인 하드코딩 모델이
  자체 모델을 반환하지 않고 `medical_public_substitute`(기본 qwen2.5:14b)로 대체.
  **명시 `model` 지정·`single_model`은 존중**(추적 가능한 의도적 선택=가드 대상 아님).
  config 로드 실패=기존 라우팅 강등(fail-safe).
- 활성화(운영자 결정 대기): config.json `"medical_pin_public_models": true` 1줄 —
  식약처 유형 판정·트윈 방침 확정 시. 활성 시 변경되는 것=summarize·analyze·draft·
  한국어 폴백·reasoning/enhanced의 werub 모델이 qwen2.5:14b로 대체(A/B 실측상 품질
  동등 이상 근거: v2.114.0 grounded A/B·v2.104.0).

## 2. 모델 카드 2종 — `docs/model-cards/`

- `werubmedv2.md`: 기반 Qwen2.5-7B-Instruct·QLoRA(r32/α32)·**학습 232건**(v1 53+v2 179 —
  내부 약물 코드 DB 템플릿 합성 중심, **실환자 기록 아님·인구통계 해당 없음**)·loss
  2.38→0.70·GGUF Q4_K_M·서빙 위치·한계(소규모=형식 적응 목적·독립 표준 벤치 미측정)·
  변경 이력(2026-05-21 이후 변경 0).
- `werub-medical-7b.md`(v1): 53건·데이터 스냅샷 미보존 명시(파이프라인은 형상관리=재현
  가능)·**정비 권고**=v1 서빙 경로(enhanced·diagnosis·SOAP)를 v2 또는 공개 모델로 통일해
  자체 모델 1종화(변경 관리·카드 유지 부담 축소).
- 출처 아티팩트 전부 형상관리 확인: `training/{medical_config.yaml,prepare_medical_data.py,
  data/medical/train.jsonl(179행 스냅샷)}`·`scripts/generate_medical_training_v2.py`·
  커밋 42bf678/f5074b1.

## 3. 검증

- GPU 회귀: test_medical_model_pin **7**(+medlaw gen_precision 11=18 passed) · py_compile.
- 배포: pull → graceful HUP(NRestarts 0) → health 200. 가드 기본 off라 라이브 동작 무변경.
- 트윈 회신 연계: `llm-regulatory-reply2-to-twin.md` Q1 확정(동일 법인)·대비 2건 이행 완료.
